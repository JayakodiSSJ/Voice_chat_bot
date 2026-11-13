from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import threading
from typing import Any, Callable

from auth_sqlite import fetch_all, fetch_one, get_conn, utcnow_iso
from custom_agent_chat_service import custom_agent_chat_service
from whatsapp_addon_service import get_company_whatsapp_subscription
from whatsapp_conversation_service import build_agent_menu, list_whatsapp_agents
from whatsapp_cloud_api_service import (
    WhatsAppCloudAPIError,
    split_whatsapp_text,
    whatsapp_cloud_api_service,
)
from whatsapp_waha_api_service import (
    WhatsAppWahaAPIError,
    whatsapp_waha_api_service,
)


MAX_DISPATCH_ATTEMPTS = 3
WORKSPACE_QUOTA_REACHED_MESSAGE = (
    "This workspace has reached its monthly token limit. "
    "No agent was invoked. Send /exit to leave this agent, or try again after "
    "the workspace quota resets."
)
WHATSAPP_SUBSCRIPTION_UNAVAILABLE_MESSAGE = (
    "WhatsApp agent access is currently unavailable for this workspace. "
    "Send /exit to leave this agent."
)


class WhatsAppDispatchService:
    def __init__(
        self,
        *,
        connection_factory: Callable = get_conn,
        chat_service=custom_agent_chat_service,
        cloud_api_service=whatsapp_cloud_api_service,
        waha_api_service=whatsapp_waha_api_service,
        workspace_quota_checker: Callable | None = None,
        workspace_metered_runner: Callable | None = None,
    ):
        self.connection_factory = connection_factory
        self.chat_service = chat_service
        self.cloud_api_service = cloud_api_service
        self.waha_api_service = waha_api_service
        self.workspace_quota_checker = workspace_quota_checker
        self.workspace_metered_runner = workspace_metered_runner

    def process_dispatch(
        self,
        dispatch_id: int,
        *,
        decrypt_secret: Callable[[str | None], str | None],
        graph_api_version: str = "v26.0",
        waha_base_url: str = "",
        timeout_seconds: float = 20,
    ) -> dict[str, Any]:
        dispatch = self._claim_dispatch(dispatch_id)
        if not dispatch:
            return {"status": "skipped", "retryable": False}

        try:
            provider = str(dispatch.get("provider") or "meta").strip().lower()
            access_token = None
            waha_api_key = None
            if provider == "waha":
                waha_api_key = decrypt_secret(dispatch.get("waha_api_key_secret"))
                if not waha_base_url or not dispatch.get("waha_session") or not waha_api_key:
                    self._mark_dispatch(
                        dispatch_id,
                        "blocked_configuration",
                        "WAHA_BASE_URL, WAHA session, and API key are required for outbound messaging.",
                    )
                    return {"status": "blocked_configuration", "retryable": False}
            else:
                access_token = decrypt_secret(dispatch.get("meta_access_token_secret"))
                if not dispatch.get("meta_phone_number_id") or not access_token:
                    self._mark_dispatch(
                        dispatch_id,
                        "blocked_configuration",
                        "Meta Phone Number ID and access token are required for outbound messaging.",
                    )
                    return {"status": "blocked_configuration", "retryable": False}
            response_text = dispatch.get("response_text")
            if dispatch["action"] == "route_to_agent":
                response_text = dispatch.get("agent_response_text")
                if not response_text:
                    response_text = self._run_selected_agent(dispatch)
                    self._save_agent_response(dispatch_id, response_text)
            if not response_text:
                raise RuntimeError("The WhatsApp dispatch did not produce a response.")

            self._ensure_outbox(
                dispatch_id=dispatch_id,
                company_id=int(dispatch["company_id"]),
                recipient_phone=dispatch["sender_phone"],
                response_text=response_text,
            )
            self._send_outbox(
                dispatch_id=dispatch_id,
                provider=provider,
                phone_number_id=dispatch["meta_phone_number_id"],
                access_token=access_token,
                waha_base_url=waha_base_url,
                waha_session=dispatch.get("waha_session"),
                waha_api_key=waha_api_key,
                graph_api_version=graph_api_version,
                timeout_seconds=timeout_seconds,
            )
            self._mark_dispatch(dispatch_id, "completed", None, completed=True)
            return {"status": "completed", "retryable": False}
        except (WhatsAppCloudAPIError, WhatsAppWahaAPIError) as error:
            self._mark_dispatch(dispatch_id, "failed", str(error))
            return {"status": "failed", "retryable": error.retryable}
        except Exception as error:
            self._mark_dispatch(dispatch_id, "failed", str(error))
            return {"status": "failed", "retryable": True}

    def _claim_dispatch(self, dispatch_id: int) -> dict[str, Any] | None:
        with self.connection_factory() as conn:
            now = utcnow_iso()
            cursor = conn.execute(
                """
                UPDATE whatsapp_message_dispatches
                SET processing_status = 'processing',
                    attempt_count = attempt_count + 1,
                    processing_started_at = ?,
                    last_error = NULL
                WHERE id = ?
                  AND processing_status IN ('pending', 'failed')
                  AND attempt_count < ?
                """,
                (now, dispatch_id, MAX_DISPATCH_ATTEMPTS),
            )
            if cursor.rowcount <= 0:
                return None
            row = fetch_one(
                conn,
                """
                SELECT
                    d.id,
                    d.company_id,
                    d.meta_message_id,
                    d.sender_phone,
                    d.action,
                    d.selected_agent_id,
                    d.response_text,
                    d.agent_response_text,
                    d.attempt_count,
                    i.message_text,
                    c.meta_phone_number_id,
                    c.meta_access_token_secret,
                    c.provider,
                    c.waha_session,
                    c.waha_api_key_secret
                FROM whatsapp_message_dispatches d
                JOIN whatsapp_inbound_messages i
                  ON i.company_id = d.company_id
                 AND i.meta_message_id = d.meta_message_id
                JOIN company_whatsapp_connections c
                  ON c.company_id = d.company_id
                WHERE d.id = ?
                LIMIT 1
                """,
                (dispatch_id,),
            )
            conn.commit()
        if not row:
            self._mark_dispatch(dispatch_id, "failed", "Dispatch dependencies were not found.")
            return None
        return dict(row)

    def _run_selected_agent(self, dispatch: dict[str, Any]) -> str:
        agent_id = dispatch.get("selected_agent_id")
        with self.connection_factory() as conn:
            agent_row = None
            if agent_id is not None:
                agent_row = fetch_one(
                    conn,
                    """
                    SELECT id, name, configuration, provisioning_status
                    FROM custom_agents
                    WHERE id = ? AND company_id = ?
                    LIMIT 1
                    """,
                    (agent_id, dispatch["company_id"]),
                )
            if not agent_row:
                return self._recover_unavailable_agent(conn, dispatch)
            agent = dict(agent_row)
            try:
                configuration = json.loads(agent.get("configuration") or "{}")
            except (TypeError, ValueError):
                configuration = {}
            if (
                agent.get("provisioning_status") != "deployed"
                or not isinstance(configuration, dict)
                or configuration.get("whatsapp_enabled") is not True
            ):
                return self._recover_unavailable_agent(conn, dispatch)
            subscription = get_company_whatsapp_subscription(
                conn, int(dispatch["company_id"])
            )
            if subscription["status"] not in {"active", "cancel_at_period_end"}:
                response_text = WHATSAPP_SUBSCRIPTION_UNAVAILABLE_MESSAGE
                conn.execute(
                    """
                    UPDATE whatsapp_message_dispatches
                    SET action = ?, response_text = ?
                    WHERE id = ?
                    """,
                    ("subscription_unavailable", response_text, int(dispatch["id"])),
                )
                conn.commit()
                return response_text
            if self.workspace_quota_checker:
                quota = self.workspace_quota_checker(
                    conn, int(dispatch["company_id"]), 0
                )
                if not quota.get("allowed"):
                    response_text = WORKSPACE_QUOTA_REACHED_MESSAGE
                    conn.execute(
                        """
                        UPDATE whatsapp_message_dispatches
                        SET action = 'workspace_quota_exceeded', response_text = ?
                        WHERE id = ?
                        """,
                        (response_text, int(dispatch["id"])),
                    )
                    conn.commit()
                    return response_text
            conn.commit()
            history = self._load_history(
                conn,
                company_id=int(dispatch["company_id"]),
                sender_phone=dispatch["sender_phone"],
                agent_id=int(agent_id),
                before_dispatch_id=int(dispatch["id"]),
            )

        operation = lambda: self.chat_service.chat_custom_agent(
            company_id=int(dispatch["company_id"]),
            agent_id=int(agent_id),
            agent_name=agent["name"],
            user_id=None,
            chat_session_id=None,
            agent_config=configuration,
            question=str(dispatch.get("message_text") or "").strip(),
            history=history,
            top_k=5,
        )
        if self.workspace_metered_runner:
            payload, _ = self.workspace_metered_runner(
                int(dispatch["company_id"]), 0, operation
            )
        else:
            payload = operation()
        return _whatsapp_answer(payload)

    @staticmethod
    def _recover_unavailable_agent(conn, dispatch: dict[str, Any]) -> str:
        company_id = int(dispatch["company_id"])
        conn.execute(
            """
            UPDATE whatsapp_conversations
            SET selected_agent_id = NULL,
                state = 'awaiting_selection',
                updated_at = ?
            WHERE company_id = ? AND sender_phone = ?
            """,
            (utcnow_iso(), company_id, dispatch["sender_phone"]),
        )
        agents = list_whatsapp_agents(conn, company_id)
        conn.commit()
        if not agents:
            return "The selected agent is no longer available, and no other WhatsApp agents are currently available."
        return (
            "The selected agent is no longer available.\n\n"
            f"{build_agent_menu(agents)}"
        )

    @staticmethod
    def _load_history(
        conn,
        *,
        company_id: int,
        sender_phone: str,
        agent_id: int,
        before_dispatch_id: int,
    ) -> list[dict[str, str]]:
        rows = fetch_all(
            conn,
            """
            SELECT i.message_text, d.agent_response_text
            FROM whatsapp_message_dispatches d
            JOIN whatsapp_inbound_messages i
              ON i.company_id = d.company_id
             AND i.meta_message_id = d.meta_message_id
            WHERE d.company_id = ?
              AND d.sender_phone = ?
              AND d.selected_agent_id = ?
              AND d.action = 'route_to_agent'
              AND d.processing_status = 'completed'
              AND d.id < ?
            ORDER BY d.id DESC
            LIMIT 4
            """,
            (company_id, sender_phone, agent_id, before_dispatch_id),
        )
        history: list[dict[str, str]] = []
        for row in reversed(rows):
            if row["message_text"]:
                history.append({"role": "user", "content": str(row["message_text"])})
            if row["agent_response_text"]:
                history.append({"role": "assistant", "content": str(row["agent_response_text"])})
        return history

    def _save_agent_response(self, dispatch_id: int, response_text: str) -> None:
        with self.connection_factory() as conn:
            conn.execute(
                """
                UPDATE whatsapp_message_dispatches
                SET agent_response_text = ?
                WHERE id = ?
                """,
                (response_text, dispatch_id),
            )
            conn.commit()

    def _ensure_outbox(
        self,
        *,
        dispatch_id: int,
        company_id: int,
        recipient_phone: str,
        response_text: str,
    ) -> None:
        chunks = split_whatsapp_text(response_text)
        if not chunks:
            raise RuntimeError("The WhatsApp response was empty.")
        now = utcnow_iso()
        with self.connection_factory() as conn:
            for sequence, chunk in enumerate(chunks, start=1):
                conn.execute(
                    """
                    INSERT INTO whatsapp_outbound_messages (
                        company_id,
                        dispatch_id,
                        recipient_phone,
                        sequence_number,
                        message_text,
                        send_status,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'pending', ?)
                    ON CONFLICT(dispatch_id, sequence_number) DO NOTHING
                    """,
                    (company_id, dispatch_id, recipient_phone, sequence, chunk, now),
                )
            conn.commit()

    def _send_outbox(
        self,
        *,
        dispatch_id: int,
        provider: str,
        phone_number_id: str | None,
        access_token: str | None,
        waha_base_url: str,
        waha_session: str | None,
        waha_api_key: str | None,
        graph_api_version: str,
        timeout_seconds: float,
    ) -> None:
        with self.connection_factory() as conn:
            rows = fetch_all(
                conn,
                """
                SELECT id, recipient_phone, message_text
                FROM whatsapp_outbound_messages
                WHERE dispatch_id = ? AND send_status IN ('pending', 'failed')
                ORDER BY sequence_number
                """,
                (dispatch_id,),
            )

        for row in rows:
            outbound_id = int(row["id"])
            with self.connection_factory() as conn:
                conn.execute(
                    """
                    UPDATE whatsapp_outbound_messages
                    SET send_status = 'sending', attempt_count = attempt_count + 1, last_error = NULL
                    WHERE id = ?
                    """,
                    (outbound_id,),
                )
                conn.commit()
            try:
                if provider == "waha":
                    meta_message_id = self.waha_api_service.send_text(
                        base_url=waha_base_url,
                        session=waha_session,
                        api_key=waha_api_key,
                        recipient_phone=row["recipient_phone"],
                        message_text=row["message_text"],
                        timeout_seconds=timeout_seconds,
                    )
                else:
                    meta_message_id = self.cloud_api_service.send_text(
                        phone_number_id=phone_number_id,
                        access_token=access_token,
                        recipient_phone=row["recipient_phone"],
                        message_text=row["message_text"],
                        graph_api_version=graph_api_version,
                        timeout_seconds=timeout_seconds,
                    )
            except Exception as error:
                with self.connection_factory() as conn:
                    conn.execute(
                        """
                        UPDATE whatsapp_outbound_messages
                        SET send_status = 'failed', last_error = ?, status_updated_at = ?
                        WHERE id = ?
                        """,
                        (_error_text(error), utcnow_iso(), outbound_id),
                    )
                    conn.commit()
                raise
            with self.connection_factory() as conn:
                now = utcnow_iso()
                conn.execute(
                    """
                    UPDATE whatsapp_outbound_messages
                    SET send_status = 'accepted',
                        meta_status = 'accepted',
                        meta_message_id = ?,
                        sent_at = ?,
                        status_updated_at = ?
                    WHERE id = ?
                    """,
                    (meta_message_id, now, now, outbound_id),
                )
                conn.commit()

    def _mark_dispatch(
        self,
        dispatch_id: int,
        status: str,
        error: str | None,
        *,
        completed: bool = False,
    ) -> None:
        with self.connection_factory() as conn:
            conn.execute(
                """
                UPDATE whatsapp_message_dispatches
                SET processing_status = ?,
                    last_error = ?,
                    completed_at = ?,
                    processing_started_at = NULL
                WHERE id = ?
                """,
                (
                    status,
                    _error_text(error) if error else None,
                    utcnow_iso() if completed else None,
                    dispatch_id,
                ),
            )
            conn.commit()


class WhatsAppBackgroundDispatcher:
    def __init__(self):
        self._lock = threading.Lock()
        self._started = False
        self._executor: ThreadPoolExecutor | None = None
        self._service: WhatsAppDispatchService | None = None
        self._decrypt_secret = None
        self._graph_api_version = "v26.0"
        self._waha_base_url = ""
        self._timeout_seconds = 20.0

    def start(
        self,
        app,
        *,
        decrypt_secret,
        workspace_quota_checker=None,
        workspace_metered_runner=None,
    ) -> None:
        with self._lock:
            if self._started:
                return
            workers = max(1, int(app.config.get("WHATSAPP_WORKER_THREADS", 2)))
            self._executor = ThreadPoolExecutor(
                max_workers=workers,
                thread_name_prefix="whatsapp-dispatch",
            )
            self._service = WhatsAppDispatchService(
                workspace_quota_checker=workspace_quota_checker,
                workspace_metered_runner=workspace_metered_runner,
            )
            self._decrypt_secret = decrypt_secret
            self._graph_api_version = str(
                app.config.get("WHATSAPP_GRAPH_API_VERSION") or "v26.0"
            )
            self._waha_base_url = str(app.config.get("WAHA_BASE_URL") or "").strip()
            self._timeout_seconds = float(
                app.config.get("WHATSAPP_API_TIMEOUT_SECONDS", 20)
            )
            self._started = True
        self._recover_and_submit()

    def submit(self, dispatch_ids: list[int]) -> None:
        if not self._started or not self._executor:
            return
        for dispatch_id in sorted({int(item) for item in dispatch_ids if item is not None}):
            self._executor.submit(self._run, dispatch_id)

    def requeue_company(self, company_id: int) -> None:
        if not self._started:
            return
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE whatsapp_message_dispatches
                SET processing_status = 'pending', attempt_count = 0, last_error = NULL
                WHERE company_id = ?
                  AND processing_status IN ('blocked_configuration', 'failed')
                """,
                (company_id,),
            )
            rows = fetch_all(
                conn,
                """
                SELECT id FROM whatsapp_message_dispatches
                WHERE company_id = ? AND processing_status = 'pending'
                ORDER BY id LIMIT 1000
                """,
                (company_id,),
            )
            conn.commit()
        self.submit([int(row["id"]) for row in rows])

    def _recover_and_submit(self) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE whatsapp_message_dispatches
                SET processing_status = 'pending', processing_started_at = NULL
                WHERE processing_status = 'processing'
                  AND (processing_started_at IS NULL OR processing_started_at < ?)
                """,
                (cutoff,),
            )
            conn.execute(
                """
                UPDATE whatsapp_message_dispatches
                SET processing_status = 'pending'
                WHERE processing_status = 'failed' AND attempt_count < ?
                """,
                (MAX_DISPATCH_ATTEMPTS,),
            )
            rows = fetch_all(
                conn,
                """
                SELECT id FROM whatsapp_message_dispatches
                WHERE processing_status = 'pending' AND attempt_count < ?
                ORDER BY id LIMIT 1000
                """,
                (MAX_DISPATCH_ATTEMPTS,),
            )
            conn.commit()
        self.submit([int(row["id"]) for row in rows])

    def _run(self, dispatch_id: int) -> None:
        if not self._service or not self._decrypt_secret:
            return
        result = self._service.process_dispatch(
            dispatch_id,
            decrypt_secret=self._decrypt_secret,
            graph_api_version=self._graph_api_version,
            waha_base_url=self._waha_base_url,
            timeout_seconds=self._timeout_seconds,
        )
        if result.get("retryable"):
            timer = threading.Timer(2.0, lambda: self.submit([dispatch_id]))
            timer.daemon = True
            timer.start()


def _whatsapp_answer(payload: dict[str, Any] | None) -> str:
    payload = payload if isinstance(payload, dict) else {}
    answer = str(payload.get("answer") or payload.get("message") or "").strip()
    if not answer:
        answer = "I could not prepare a response. Please try again."
    if payload.get("type") == "render_form":
        answer = (
            f"{answer}\n\nThis form is available in the Agent Store web application."
        )
    quick_replies = payload.get("quick_replies")
    if isinstance(quick_replies, list):
        labels = []
        for item in quick_replies:
            if isinstance(item, dict):
                label = item.get("label") or item.get("title") or item.get("text")
            else:
                label = item
            normalized = str(label or "").strip()
            if normalized:
                labels.append(normalized)
        if labels:
            answer = f"{answer}\n\n" + "\n".join(
                f"{index}. {label}" for index, label in enumerate(labels, start=1)
            )
    return answer


def _error_text(error: Any) -> str:
    return str(error or "Unknown WhatsApp processing error.").strip()[:1000]


whatsapp_background_dispatcher = WhatsAppBackgroundDispatcher()
