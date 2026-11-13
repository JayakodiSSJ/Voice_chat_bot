import sqlite3
import json
import unittest

from tool_audit_service import EXECUTED, ToolAuditService
from tool_types import ToolSpec


class ToolAuditServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = ToolAuditService()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, full_name TEXT);
            CREATE TABLE custom_agents (id INTEGER PRIMARY KEY, company_id INTEGER, name TEXT, configuration TEXT);
            CREATE TABLE tool_execution_audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                agent_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                chat_session_id INTEGER,
                tool_id TEXT NOT NULL,
                tool_kind TEXT NOT NULL,
                tool_name TEXT,
                side_effect TEXT NOT NULL,
                approval_policy TEXT NOT NULL,
                configuration_fingerprint TEXT NOT NULL,
                target_json TEXT NOT NULL,
                arguments_json TEXT NOT NULL,
                execution_arguments_json TEXT,
                request_message TEXT,
                status TEXT NOT NULL,
                result_json TEXT,
                error_message TEXT,
                approved_by_user_id INTEGER,
                approved_at TEXT,
                rejected_by_user_id INTEGER,
                rejected_at TEXT,
                rejection_reason TEXT,
                created_at TEXT NOT NULL,
                executed_at TEXT
            );
            """
        )
        self.conn.execute("INSERT INTO users VALUES (1, 'ada@example.com', 'Ada Lovelace')")
        self.conn.execute("INSERT INTO custom_agents VALUES (10, 7, 'API Agent', '{}')")
        self.tool = ToolSpec(
            id="api:0",
            kind="api",
            name="Create customer",
            description="Create a customer",
            input_schema={"type": "object"},
            target={"endpoint_index": 0},
            side_effect="conditional",
            approval_policy="always",
            configuration_fingerprint="fingerprint",
        )

    def tearDown(self):
        self.conn.close()

    def test_pending_action_is_tenant_scoped_and_claimed_once(self):
        pending = self.service.create_pending_confirmation(
            self.conn,
            company_id=7,
            agent_id=10,
            user_id=1,
            chat_session_id=12,
            tool=self.tool,
            arguments={"name": "Ada"},
            question="Create Ada",
        )

        activity = self.service.get_for_user(
            self.conn,
            company_id=7,
            agent_id=10,
            user_id=1,
            activity_id=pending["id"],
        )
        self.assertEqual(activity["arguments"], {"name": "Ada"})
        self.assertEqual(activity["configuration_fingerprint"], "fingerprint")
        self.assertIsNone(
            self.service.get_for_user(
                self.conn,
                company_id=8,
                agent_id=10,
                user_id=1,
                activity_id=pending["id"],
            )
        )
        self.assertTrue(
            self.service.mark_user_executing(
                self.conn,
                activity_id=pending["id"],
                company_id=7,
                user_id=1,
            )
        )
        self.assertFalse(
            self.service.mark_user_executing(
                self.conn,
                activity_id=pending["id"],
                company_id=7,
                user_id=1,
            )
        )

    def test_records_execution_result_and_lists_display_name(self):
        pending = self.service.create_pending_confirmation(
            self.conn,
            company_id=7,
            agent_id=10,
            user_id=1,
            chat_session_id=None,
            tool=self.tool,
            arguments={"name": "Ada"},
            question="Create Ada",
        )
        self.service.mark_execution_result(
            self.conn,
            activity_id=pending["id"],
            company_id=7,
            status=EXECUTED,
            result={"ok": True, "response": {"id": 42}},
        )

        rows = self.service.list_for_company(self.conn, company_id=7, user_query="ada")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], EXECUTED)
        self.assertEqual(rows[0]["requester_display_name"], "Ada Lovelace")
        self.assertTrue(rows[0]["result"]["response"]["content_omitted_from_audit"])

    def test_audit_view_redacts_sensitive_arguments_while_execution_copy_is_preserved(self):
        pending = self.service.create_pending_confirmation(
            self.conn,
            company_id=7,
            agent_id=10,
            user_id=1,
            chat_session_id=None,
            tool=self.tool,
            arguments={"name": "Ada", "accessToken": "run-secret"},
            question="Create Ada with Bearer question-secret",
        )

        activity = self.service.get_for_user(
            self.conn,
            company_id=7,
            agent_id=10,
            user_id=1,
            activity_id=pending["id"],
        )
        self.service.mark_execution_result(
            self.conn,
            activity_id=pending["id"],
            company_id=7,
            status=EXECUTED,
            result={"ok": True, "response": {"token": "response-secret"}},
            error_message="Bearer error-secret",
        )
        rows = self.service.list_for_company(self.conn, company_id=7)

        self.assertEqual(activity["arguments"]["accessToken"], "[REDACTED]")
        self.assertEqual(activity["execution_arguments"]["accessToken"], "run-secret")
        self.assertNotIn("run-secret", json.dumps(rows))
        self.assertNotIn("response-secret", json.dumps(rows))

    def test_legacy_arguments_are_redacted_in_views_but_remain_available_to_execute(self):
        self.conn.execute(
            """
            INSERT INTO tool_execution_audit_logs (
                company_id, agent_id, user_id, tool_id, tool_kind, tool_name,
                side_effect, approval_policy, configuration_fingerprint,
                target_json, arguments_json, status, created_at
            )
            VALUES (7, 10, 1, 'api:0', 'api', 'Create customer', 'conditional', 'always',
                    'fingerprint', '{}', '{"accessToken":"legacy-secret","name":"Ada"}',
                    'pending_user_confirmation', '2026-01-01T00:00:00+00:00')
            """
        )

        activity = self.service.get_for_user(
            self.conn,
            company_id=7,
            agent_id=10,
            user_id=1,
            activity_id=1,
        )
        rows = self.service.list_for_company(self.conn, company_id=7)

        self.assertEqual(activity["arguments"]["accessToken"], "[REDACTED]")
        self.assertEqual(activity["execution_arguments"]["accessToken"], "legacy-secret")
        self.assertNotIn("legacy-secret", json.dumps(rows))

    def test_pending_action_can_be_cancelled_only_once(self):
        pending = self.service.create_pending_confirmation(
            self.conn,
            company_id=7,
            agent_id=10,
            user_id=1,
            chat_session_id=None,
            tool=self.tool,
            arguments={},
            question="Create customer",
        )

        self.assertTrue(
            self.service.mark_user_cancelled(
                self.conn,
                activity_id=pending["id"],
                company_id=7,
                user_id=1,
                reason="Cancelled",
            )
        )
        self.assertFalse(
            self.service.mark_user_cancelled(
                self.conn,
                activity_id=pending["id"],
                company_id=7,
                user_id=1,
            )
        )


if __name__ == "__main__":
    unittest.main()
