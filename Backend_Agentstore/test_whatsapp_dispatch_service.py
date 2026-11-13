import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from whatsapp_cloud_api_service import WhatsAppCloudAPIError
from whatsapp_dispatch_service import WhatsAppDispatchService


class FakeChatService:
    def __init__(self):
        self.calls = []

    def chat_custom_agent(self, **kwargs):
        self.calls.append(kwargs)
        return {"type": "message", "answer": "Agent answer"}


class FakeCloudAPIService:
    def __init__(self, *, fail_once=False):
        self.calls = []
        self.fail_once = fail_once

    def send_text(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail_once:
            self.fail_once = False
            raise WhatsAppCloudAPIError("Temporary Meta failure", retryable=True)
        return f"wamid.out.{len(self.calls)}"


class FakeWahaAPIService:
    def __init__(self):
        self.calls = []

    def send_text(self, **kwargs):
        self.calls.append(kwargs)
        return f"waha.out.{len(self.calls)}"


class FakeWorkspaceQuota:
    def __init__(self):
        self.allowed = True
        self.check_calls = []
        self.metered_calls = []

    def check(self, conn, company_id, user_id):
        self.check_calls.append((company_id, user_id))
        return {"allowed": self.allowed}

    def run(self, company_id, user_id, operation):
        self.metered_calls.append((company_id, user_id))
        return operation(), None


class WhatsAppDispatchServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        now = datetime.now(timezone.utc)
        self.period_start = (now - timedelta(days=1)).isoformat()
        self.period_end = (now + timedelta(days=1)).isoformat()
        schema = """
            CREATE TABLE company_whatsapp_connections (
                company_id INTEGER PRIMARY KEY,
                provider TEXT NOT NULL DEFAULT 'meta',
                meta_phone_number_id TEXT,
                meta_access_token_secret TEXT,
                waha_session TEXT,
                waha_api_key_secret TEXT
            );
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
                message_type TEXT NOT NULL,
                message_text TEXT,
                payload_json TEXT NOT NULL,
                received_at TEXT NOT NULL,
                UNIQUE(company_id, meta_message_id)
            );
            CREATE TABLE whatsapp_message_dispatches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                meta_message_id TEXT NOT NULL,
                sender_phone TEXT NOT NULL,
                action TEXT NOT NULL,
                selected_agent_id INTEGER,
                response_text TEXT,
                processing_status TEXT NOT NULL DEFAULT 'pending',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                agent_response_text TEXT,
                last_error TEXT,
                processing_started_at TEXT,
                completed_at TEXT,
                created_at TEXT NOT NULL,
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
            CREATE TABLE company_feature_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                feature_key TEXT NOT NULL,
                status TEXT NOT NULL,
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT UNIQUE,
                stripe_subscription_item_id TEXT,
                stripe_checkout_session_id TEXT UNIQUE,
                stripe_price_id TEXT,
                monthly_price_cents INTEGER NOT NULL,
                currency TEXT NOT NULL DEFAULT 'usd',
                monthly_quota INTEGER NOT NULL,
                current_period_start TEXT,
                current_period_end TEXT,
                cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
                canceled_at TEXT,
                stripe_event_created_at INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (company_id, feature_key)
            );
            CREATE TABLE company_feature_usage_periods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                feature_key TEXT NOT NULL,
                feature_subscription_id INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                quota_limit INTEGER NOT NULL,
                units_used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (company_id, feature_key, period_start)
            );
            CREATE TABLE company_feature_usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usage_period_id INTEGER NOT NULL,
                company_id INTEGER NOT NULL,
                feature_key TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                event_type TEXT NOT NULL,
                units INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE (company_id, feature_key, idempotency_key)
            );
            CREATE TABLE whatsapp_outbound_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                dispatch_id INTEGER NOT NULL,
                recipient_phone TEXT NOT NULL,
                sequence_number INTEGER NOT NULL,
                message_text TEXT NOT NULL,
                send_status TEXT NOT NULL DEFAULT 'pending',
                meta_status TEXT,
                meta_message_id TEXT UNIQUE,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                sent_at TEXT,
                status_updated_at TEXT,
                UNIQUE(dispatch_id, sequence_number)
            );
            INSERT INTO company_whatsapp_connections
                (company_id, meta_phone_number_id, meta_access_token_secret)
            VALUES (7, '123456789012345', 'encrypted:access-token');
            INSERT INTO custom_agents
                (id, company_id, name, configuration, provisioning_status)
            VALUES (11, 7, 'Support', '{"whatsapp_enabled": true}', 'deployed');
            INSERT INTO custom_agents
                (id, company_id, name, configuration, provisioning_status)
            VALUES (12, 7, 'Billing', '{"whatsapp_enabled": true}', 'deployed');
            INSERT INTO company_feature_subscriptions (
                id, company_id, feature_key, status, stripe_subscription_id,
                monthly_price_cents, monthly_quota, current_period_start,
                current_period_end, created_at, updated_at
            ) VALUES (
                1, 7, 'whatsapp', 'active', 'sub_7', 1999, 2,
                'CURRENT_PERIOD_START', 'CURRENT_PERIOD_END',
                '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00'
            );
            INSERT INTO company_feature_usage_periods (
                id, company_id, feature_key, feature_subscription_id,
                period_start, period_end, quota_limit, units_used, created_at, updated_at
            ) VALUES (
                1, 7, 'whatsapp', 1, 'CURRENT_PERIOD_START',
                'CURRENT_PERIOD_END', 2, 0,
                '2026-08-01T00:00:00+00:00', '2026-08-01T00:00:00+00:00'
            );
            """
        schema = schema.replace("CURRENT_PERIOD_START", self.period_start).replace(
            "CURRENT_PERIOD_END", self.period_end
        )
        self.conn.executescript(schema)
        self.chat = FakeChatService()
        self.cloud = FakeCloudAPIService()
        self.workspace_quota = FakeWorkspaceQuota()
        self.service = WhatsAppDispatchService(
            connection_factory=lambda: self.conn,
            chat_service=self.chat,
            cloud_api_service=self.cloud,
            workspace_quota_checker=self.workspace_quota.check,
            workspace_metered_runner=self.workspace_quota.run,
        )

    def tearDown(self):
        self.conn.close()

    def _dispatch(
        self,
        message_id,
        *,
        text,
        action,
        response_text=None,
        status="pending",
        agent_response_text=None,
    ):
        self.conn.execute(
            """
            INSERT INTO whatsapp_inbound_messages (
                company_id, meta_message_id, sender_phone, message_type,
                message_text, payload_json, received_at
            )
            VALUES (7, ?, '94770000000', 'text', ?, '{}', '2026-08-19T00:00:00+00:00')
            """,
            (message_id, text),
        )
        cursor = self.conn.execute(
            """
            INSERT INTO whatsapp_message_dispatches (
                company_id, meta_message_id, sender_phone, action,
                selected_agent_id, response_text, processing_status,
                agent_response_text, created_at
            )
            VALUES (7, ?, '94770000000', ?, 11, ?, ?, ?, '2026-08-19T00:00:00+00:00')
            """,
            (message_id, action, response_text, status, agent_response_text),
        )
        self.conn.commit()
        return cursor.lastrowid

    @staticmethod
    def _decrypt(value):
        return value.split(":", 1)[1] if value else None

    def test_sends_persisted_menu_without_calling_an_agent(self):
        dispatch_id = self._dispatch(
            "wamid.menu",
            text="Hello",
            action="show_agent_menu",
            response_text="Choose an agent:\n1. Support",
        )

        result = self.service.process_dispatch(dispatch_id, decrypt_secret=self._decrypt)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.chat.calls), 0)
        self.assertEqual(self.cloud.calls[0]["message_text"], "Choose an agent:\n1. Support")
        units_used = self.conn.execute(
            "SELECT units_used FROM company_feature_usage_periods WHERE id = 1"
        ).fetchone()[0]
        self.assertEqual(units_used, 0)

    def test_sends_persisted_menu_through_waha(self):
        self.conn.execute(
            """
            UPDATE company_whatsapp_connections
            SET provider = 'waha', waha_session = 'workspace-7',
                waha_api_key_secret = 'encrypted:waha-key'
            WHERE company_id = 7
            """
        )
        self.conn.commit()
        waha = FakeWahaAPIService()
        service = WhatsAppDispatchService(
            connection_factory=lambda: self.conn,
            chat_service=self.chat,
            cloud_api_service=self.cloud,
            waha_api_service=waha,
            workspace_quota_checker=self.workspace_quota.check,
            workspace_metered_runner=self.workspace_quota.run,
        )
        dispatch_id = self._dispatch(
            "waha.menu",
            text="Hello",
            action="show_agent_menu",
            response_text="Choose an agent:\n1. Support",
        )

        result = service.process_dispatch(
            dispatch_id,
            decrypt_secret=self._decrypt,
            waha_base_url="http://waha:3000",
        )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.cloud.calls), 0)
        self.assertEqual(waha.calls[0]["session"], "workspace-7")
        self.assertEqual(waha.calls[0]["api_key"], "waha-key")
        self.assertEqual(waha.calls[0]["base_url"], "http://waha:3000")

    def test_executes_only_the_selected_agent_with_sender_history(self):
        self._dispatch(
            "wamid.previous",
            text="Earlier question",
            action="route_to_agent",
            status="completed",
            agent_response_text="Earlier answer",
        )
        dispatch_id = self._dispatch(
            "wamid.current",
            text="Current question",
            action="route_to_agent",
        )

        result = self.service.process_dispatch(dispatch_id, decrypt_secret=self._decrypt)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.chat.calls), 1)
        call = self.chat.calls[0]
        self.assertEqual(call["agent_id"], 11)
        self.assertEqual(call["question"], "Current question")
        self.assertEqual(
            call["history"],
            [
                {"role": "user", "content": "Earlier question"},
                {"role": "assistant", "content": "Earlier answer"},
            ],
        )
        self.assertEqual(self.cloud.calls[0]["message_text"], "Agent answer")

    def test_retries_delivery_without_regenerating_the_agent_answer(self):
        cloud = FakeCloudAPIService(fail_once=True)
        service = WhatsAppDispatchService(
            connection_factory=lambda: self.conn,
            chat_service=self.chat,
            cloud_api_service=cloud,
            workspace_quota_checker=self.workspace_quota.check,
            workspace_metered_runner=self.workspace_quota.run,
        )
        dispatch_id = self._dispatch(
            "wamid.retry",
            text="Question",
            action="route_to_agent",
        )

        first = service.process_dispatch(dispatch_id, decrypt_secret=self._decrypt)
        second = service.process_dispatch(dispatch_id, decrypt_secret=self._decrypt)

        self.assertEqual(first, {"status": "failed", "retryable": True})
        self.assertEqual(second["status"], "completed")
        self.assertEqual(len(self.chat.calls), 1)
        self.assertEqual(len(cloud.calls), 2)
        units_used = self.conn.execute(
            "SELECT units_used FROM company_feature_usage_periods WHERE id = 1"
        ).fetchone()[0]
        self.assertEqual(units_used, 0)
        self.assertEqual(self.workspace_quota.metered_calls, [(7, 0)])
        outbox_count = self.conn.execute(
            "SELECT COUNT(*) FROM whatsapp_outbound_messages WHERE dispatch_id = ?",
            (dispatch_id,),
        ).fetchone()[0]
        self.assertEqual(outbox_count, 1)

    def test_blocks_delivery_until_outbound_credentials_are_configured(self):
        self.conn.execute(
            "UPDATE company_whatsapp_connections SET meta_access_token_secret = NULL WHERE company_id = 7"
        )
        dispatch_id = self._dispatch(
            "wamid.blocked",
            text="Hello",
            action="show_agent_menu",
            response_text="Choose an agent",
        )

        result = self.service.process_dispatch(dispatch_id, decrypt_secret=self._decrypt)

        self.assertEqual(result["status"], "blocked_configuration")
        self.assertEqual(len(self.cloud.calls), 0)

    def test_recovers_to_the_agent_menu_when_selected_agent_was_disabled(self):
        self.conn.execute(
            """
            INSERT INTO whatsapp_conversations (
                company_id, sender_phone, selected_agent_id, state, created_at, updated_at
            )
            VALUES (7, '94770000000', 11, 'active',
                    '2026-08-19T00:00:00+00:00', '2026-08-19T00:00:00+00:00')
            """
        )
        self.conn.execute(
            "UPDATE custom_agents SET provisioning_status = 'disabled' WHERE id = 11"
        )
        dispatch_id = self._dispatch(
            "wamid.disabled",
            text="Question",
            action="route_to_agent",
        )

        result = self.service.process_dispatch(dispatch_id, decrypt_secret=self._decrypt)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.chat.calls), 0)
        self.assertIn("1. Billing", self.cloud.calls[0]["message_text"])
        conversation = self.conn.execute(
            "SELECT state, selected_agent_id FROM whatsapp_conversations WHERE company_id = 7"
        ).fetchone()
        self.assertEqual(conversation["state"], "awaiting_selection")
        self.assertIsNone(conversation["selected_agent_id"])

    def test_stops_agent_invocation_when_workspace_token_quota_is_exhausted(self):
        self.workspace_quota.allowed = False
        dispatch_id = self._dispatch(
            "wamid.quota",
            text="Question after quota",
            action="route_to_agent",
        )

        result = self.service.process_dispatch(dispatch_id, decrypt_secret=self._decrypt)

        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.chat.calls), 0)
        self.assertIn("monthly token limit", self.cloud.calls[0]["message_text"])
        dispatch = self.conn.execute(
            "SELECT action, agent_response_text FROM whatsapp_message_dispatches WHERE id = ?",
            (dispatch_id,),
        ).fetchone()
        self.assertEqual(dispatch["action"], "workspace_quota_exceeded")
        self.assertIn("No agent was invoked", dispatch["agent_response_text"])
        self.assertEqual(
            self.conn.execute(
                "SELECT units_used FROM company_feature_usage_periods WHERE id = 1"
            ).fetchone()[0],
            0,
        )

    def test_legacy_whatsapp_quota_does_not_limit_agent_invocations(self):
        self.conn.execute(
            "UPDATE company_feature_usage_periods SET quota_limit = 1 WHERE id = 1"
        )
        self.conn.commit()
        allowed_id = self._dispatch(
            "wamid.allowed",
            text="First question",
            action="route_to_agent",
        )
        second_id = self._dispatch(
            "wamid.blocked.quota",
            text="Second question",
            action="route_to_agent",
        )

        first = self.service.process_dispatch(allowed_id, decrypt_secret=self._decrypt)
        second = self.service.process_dispatch(second_id, decrypt_secret=self._decrypt)

        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "completed")
        self.assertEqual(len(self.chat.calls), 2)
        self.assertEqual(len(self.workspace_quota.metered_calls), 2)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM company_feature_usage_events"
            ).fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
