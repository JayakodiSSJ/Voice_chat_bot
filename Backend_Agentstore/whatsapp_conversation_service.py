from __future__ import annotations

import json
from typing import Any

from auth_sqlite import fetch_all, fetch_one, utcnow_iso


MENU_COMMANDS = {"/agents", "/menu", "/start"}
EXIT_COMMAND = "/exit"


def list_whatsapp_agents(conn, company_id: int) -> list[dict[str, Any]]:
    rows = fetch_all(
        conn,
        """
        SELECT id, name, configuration
        FROM custom_agents
        WHERE company_id = ? AND provisioning_status = 'deployed'
        ORDER BY lower(name), id
        """,
        (company_id,),
    )
    agents: list[dict[str, Any]] = []
    for row in rows:
        try:
            configuration = json.loads(row["configuration"] or "{}")
        except (TypeError, ValueError):
            configuration = {}
        if not isinstance(configuration, dict) or configuration.get("whatsapp_enabled") is not True:
            continue
        agents.append(
            {
                "id": int(row["id"]),
                "name": str(row["name"] or "").strip(),
            }
        )
    return agents


def process_whatsapp_messages(
    conn,
    company_id: int,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    processed = 0
    already_processed = 0
    ignored = 0
    actions: dict[str, int] = {}
    dispatch_ids: list[int] = []

    for message in messages:
        meta_message_id = str(message.get("meta_message_id") or "").strip()
        sender_phone = str(message.get("sender_phone") or "").strip()
        if not meta_message_id or not sender_phone:
            ignored += 1
            continue

        now = utcnow_iso()
        reservation = conn.execute(
            """
            INSERT INTO whatsapp_message_dispatches (
                company_id,
                meta_message_id,
                sender_phone,
                action,
                selected_agent_id,
                response_text,
                created_at
            )
            VALUES (?, ?, ?, 'processing', NULL, NULL, ?)
            ON CONFLICT(company_id, meta_message_id) DO NOTHING
            """,
            (company_id, meta_message_id, sender_phone, now),
        )
        if reservation.rowcount <= 0:
            already_processed += 1
            continue

        outcome = _process_message(conn, company_id, sender_phone, message)
        conn.execute(
            """
            UPDATE whatsapp_message_dispatches
            SET action = ?, selected_agent_id = ?, response_text = ?
            WHERE company_id = ? AND meta_message_id = ?
            """,
            (
                outcome["action"],
                outcome.get("selected_agent_id"),
                outcome.get("response_text"),
                company_id,
                meta_message_id,
            ),
        )
        processed += 1
        dispatch_id_row = fetch_one(
            conn,
            """
            SELECT id
            FROM whatsapp_message_dispatches
            WHERE company_id = ? AND meta_message_id = ?
            LIMIT 1
            """,
            (company_id, meta_message_id),
        )
        if dispatch_id_row:
            dispatch_ids.append(int(dispatch_id_row["id"]))
        action = outcome["action"]
        actions[action] = actions.get(action, 0) + 1

    return {
        "processed": processed,
        "already_processed": already_processed,
        "ignored": ignored,
        "actions": actions,
        "dispatch_ids": dispatch_ids,
    }


def _process_message(
    conn,
    company_id: int,
    sender_phone: str,
    message: dict[str, Any],
) -> dict[str, Any]:
    agents = list_whatsapp_agents(conn, company_id)
    if not agents:
        _set_conversation(conn, company_id, sender_phone, None)
        return {
            "action": "no_agents",
            "response_text": "No WhatsApp agents are available right now.",
        }

    conversation = fetch_one(
        conn,
        """
        SELECT selected_agent_id, state
        FROM whatsapp_conversations
        WHERE company_id = ? AND sender_phone = ?
        LIMIT 1
        """,
        (company_id, sender_phone),
    )
    selected_agent_id = (
        int(conversation["selected_agent_id"])
        if conversation and conversation["selected_agent_id"] is not None
        else None
    )
    active_agent = next(
        (agent for agent in agents if agent["id"] == selected_agent_id),
        None,
    )
    was_unavailable = bool(selected_agent_id and not active_agent)
    if was_unavailable:
        _set_conversation(conn, company_id, sender_phone, None)

    message_type = str(message.get("message_type") or "unknown").strip().lower()
    text = str(message.get("message_text") or "").strip()
    command = text.casefold()

    if active_agent:
        if command == EXIT_COMMAND:
            _set_conversation(conn, company_id, sender_phone, None)
            return {
                "action": "agent_exited",
                "selected_agent_id": active_agent["id"],
                "response_text": (
                    f"You left {active_agent['name']}.\n\n{build_agent_menu(agents)}"
                ),
            }
        if command in MENU_COMMANDS or text.startswith("@"):
            return {
                "action": "switch_requires_exit",
                "selected_agent_id": active_agent["id"],
                "response_text": (
                    f"You are currently chatting with {active_agent['name']}. "
                    "Send /exit before choosing another agent."
                ),
            }
        if message_type != "text":
            return {
                "action": "unsupported_message",
                "selected_agent_id": active_agent["id"],
                "response_text": "This agent currently accepts text messages only.",
            }
        return {
            "action": "route_to_agent",
            "selected_agent_id": active_agent["id"],
            "response_text": None,
        }

    if message_type != "text":
        return {
            "action": "unsupported_message",
            "response_text": f"Please send a text response.\n\n{build_agent_menu(agents)}",
        }

    selected = _resolve_selection(text, agents)
    if selected:
        _set_conversation(conn, company_id, sender_phone, selected["id"])
        return {
            "action": "agent_selected",
            "selected_agent_id": selected["id"],
            "response_text": (
                f"You are now chatting with {selected['name']}. "
                "Send /exit when you want to choose another agent."
            ),
        }

    if was_unavailable:
        prefix = "Your previous agent is no longer available.\n\n"
        action = "selected_agent_unavailable"
    elif text.startswith("@") or text.isdigit():
        prefix = "That agent was not found.\n\n"
        action = "invalid_selection"
    else:
        prefix = ""
        action = "show_agent_menu"
    _set_conversation(conn, company_id, sender_phone, None)
    return {
        "action": action,
        "response_text": f"{prefix}{build_agent_menu(agents)}",
    }


def _resolve_selection(text: str, agents: list[dict[str, Any]]) -> dict[str, Any] | None:
    value = text.strip()
    if value.isdigit():
        index = int(value) - 1
        return agents[index] if 0 <= index < len(agents) else None
    if not value.startswith("@"):
        return None
    requested_name = _normalized_name(value[1:])
    matches = [agent for agent in agents if _normalized_name(agent["name"]) == requested_name]
    return matches[0] if len(matches) == 1 else None


def build_agent_menu(agents: list[dict[str, Any]]) -> str:
    choices = "\n".join(
        f"{index}. {agent['name']}" for index, agent in enumerate(agents, start=1)
    )
    return f"Choose an agent:\n{choices}\n\nReply with a number or @agent_name."


def _normalized_name(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def _set_conversation(
    conn,
    company_id: int,
    sender_phone: str,
    selected_agent_id: int | None,
) -> None:
    now = utcnow_iso()
    state = "active" if selected_agent_id is not None else "awaiting_selection"
    conn.execute(
        """
        INSERT INTO whatsapp_conversations (
            company_id,
            sender_phone,
            selected_agent_id,
            state,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_id, sender_phone) DO UPDATE SET
            selected_agent_id = excluded.selected_agent_id,
            state = excluded.state,
            updated_at = excluded.updated_at
        """,
        (company_id, sender_phone, selected_agent_id, state, now, now),
    )
