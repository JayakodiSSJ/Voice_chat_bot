import json
import sqlite3
import unittest

from whatsapp_conversation_service import (
    list_whatsapp_agents,
    process_whatsapp_messages,
)


class WhatsAppConversationServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE custom_agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                configuration TEXT NOT NULL,
                provisioning_status TEXT NOT NULL
            );
            CREATE TABLE whatsapp_inbound_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                meta_message_id TEXT NOT NULL,
                sender_phone TEXT,
                recipient_phone_number_id TEXT,
                message_type TEXT NOT NULL,
                message_text TEXT,
                payload_json TEXT NOT NULL,
                received_at TEXT NOT NULL,
                UNIQUE(company_id, meta_message_id)
            );
            CREATE TABLE whatsapp_conversations (
                company_id INTEGER NOT NULL,
                sender_phone TEXT NOT NULL,
                selected_agent_id INTEGER,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (company_id, sender_phone)
            );
            CREATE TABLE whatsapp_message_dispatches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                meta_message_id TEXT NOT NULL,
                sender_phone TEXT NOT NULL,
                action TEXT NOT NULL,
                selected_agent_id INTEGER,
                response_text TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(company_id, meta_message_id)
            );
            """
        )
        self._add_agent(7, "Support", enabled=True)
        self._add_agent(7, "Billing", enabled=True)
        self._add_agent(7, "Internal", enabled=False)
        self._add_agent(8, "Other tenant", enabled=True)

    def tearDown(self):
        self.conn.close()

    def _add_agent(
        self,
        company_id,
        name,
        *,
        enabled,
        status="deployed",
    ):
        cursor = self.conn.execute(
            """
            INSERT INTO custom_agents (
                company_id, name, configuration, provisioning_status
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                company_id,
                name,
                json.dumps({"whatsapp_enabled": enabled}),
                status,
            ),
        )
        return cursor.lastrowid

    def _message(self, message_id, text, *, message_type="text"):
        message = {
            "meta_message_id": message_id,
            "sender_phone": "94770000000",
            "recipient_phone_number_id": "12345",
            "message_type": message_type,
            "message_text": text,
            "payload": {"id": message_id},
        }
        self.conn.execute(
            """
            INSERT INTO whatsapp_inbound_messages (
                company_id, meta_message_id, sender_phone,
                recipient_phone_number_id, message_type, message_text,
                payload_json, received_at
            )
            VALUES (7, ?, ?, ?, ?, ?, '{}', '2026-08-19T00:00:00+00:00')
            """,
            (
                message_id,
                message["sender_phone"],
                message["recipient_phone_number_id"],
                message_type,
                text,
            ),
        )
        return message

    def _dispatch(self, message_id):
        return self.conn.execute(
            """
            SELECT action, selected_agent_id, response_text
            FROM whatsapp_message_dispatches
            WHERE company_id = 7 AND meta_message_id = ?
            """,
            (message_id,),
        ).fetchone()

    def test_lists_only_deployed_enabled_agents_in_stable_order(self):
        self._add_agent(7, "Draft agent", enabled=True, status="draft")

        agents = list_whatsapp_agents(self.conn, 7)

        self.assertEqual([agent["name"] for agent in agents], ["Billing", "Support"])

    def test_shows_menu_then_selects_by_number_and_routes_stickily(self):
        menu = self._message("wamid.menu", "Hello")
        process_whatsapp_messages(self.conn, 7, [menu])
        self.assertEqual(self._dispatch("wamid.menu")["action"], "show_agent_menu")
        self.assertIn("1. Billing", self._dispatch("wamid.menu")["response_text"])

        selection = self._message("wamid.select", "2")
        process_whatsapp_messages(self.conn, 7, [selection])
        selected = self._dispatch("wamid.select")
        self.assertEqual(selected["action"], "agent_selected")

        question = self._message("wamid.question", "How do I reset my password?")
        process_whatsapp_messages(self.conn, 7, [question])
        routed = self._dispatch("wamid.question")
        self.assertEqual(routed["action"], "route_to_agent")
        self.assertEqual(routed["selected_agent_id"], selected["selected_agent_id"])
        self.assertIsNone(routed["response_text"])

    def test_requires_exit_before_switching_then_accepts_agent_mention(self):
        process_whatsapp_messages(
            self.conn,
            7,
            [self._message("wamid.select", "@Support")],
        )
        process_whatsapp_messages(
            self.conn,
            7,
            [self._message("wamid.blocked", "@Billing")],
        )
        self.assertEqual(
            self._dispatch("wamid.blocked")["action"],
            "switch_requires_exit",
        )

        process_whatsapp_messages(
            self.conn,
            7,
            [self._message("wamid.exit", "/exit")],
        )
        self.assertEqual(self._dispatch("wamid.exit")["action"], "agent_exited")

        process_whatsapp_messages(
            self.conn,
            7,
            [self._message("wamid.billing", "@Billing")],
        )
        self.assertEqual(
            self._dispatch("wamid.billing")["action"],
            "agent_selected",
        )

    def test_does_not_process_the_same_meta_message_twice(self):
        message = self._message("wamid.retry", "1")

        first = process_whatsapp_messages(self.conn, 7, [message])
        second = process_whatsapp_messages(self.conn, 7, [message])

        self.assertEqual(first["processed"], 1)
        self.assertEqual(second["processed"], 0)
        self.assertEqual(second["already_processed"], 1)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM whatsapp_message_dispatches"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_recovers_when_the_selected_agent_is_no_longer_available(self):
        process_whatsapp_messages(
            self.conn,
            7,
            [self._message("wamid.select", "@Support")],
        )
        self.conn.execute(
            """
            UPDATE custom_agents
            SET configuration = '{"whatsapp_enabled": false}'
            WHERE company_id = 7 AND name = 'Support'
            """
        )

        process_whatsapp_messages(
            self.conn,
            7,
            [self._message("wamid.unavailable", "Hello?")],
        )

        dispatch = self._dispatch("wamid.unavailable")
        self.assertEqual(dispatch["action"], "selected_agent_unavailable")
        conversation = self.conn.execute(
            """
            SELECT state, selected_agent_id
            FROM whatsapp_conversations
            WHERE company_id = 7 AND sender_phone = '94770000000'
            """
        ).fetchone()
        self.assertEqual(conversation["state"], "awaiting_selection")
        self.assertIsNone(conversation["selected_agent_id"])


if __name__ == "__main__":
    unittest.main()
