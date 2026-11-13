import json
import sqlite3
import unittest

from database_tool_audit_service import DatabaseToolAuditService


class DatabaseToolAuditServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = DatabaseToolAuditService()
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                email TEXT,
                full_name TEXT
            );
            CREATE TABLE custom_agents (
                id INTEGER PRIMARY KEY,
                company_id INTEGER,
                name TEXT
            );
            CREATE TABLE database_tool_audit_logs (
                id INTEGER PRIMARY KEY,
                company_id INTEGER,
                agent_id INTEGER,
                user_id INTEGER,
                chat_session_id INTEGER,
                profile_index INTEGER,
                profile_name TEXT,
                engine TEXT,
                operation TEXT,
                status TEXT,
                sql_text TEXT,
                sql_hash TEXT,
                request_message TEXT,
                estimated_affected_rows INTEGER,
                preview_error TEXT,
                affected_rows INTEGER,
                max_affected_rows INTEGER,
                result_json TEXT,
                error_message TEXT,
                approved_by_user_id INTEGER,
                approved_at TEXT,
                rejected_by_user_id INTEGER,
                rejected_at TEXT,
                rejection_reason TEXT,
                created_at TEXT,
                executed_at TEXT
            );
            """
        )
        self.conn.executemany(
            "INSERT INTO users (id, email, full_name) VALUES (?, ?, ?)",
            [
                (1, "senath.srp@gmail.com", "Senath Perera"),
                (2, "maya@example.com", ""),
            ],
        )
        self.conn.execute("INSERT INTO custom_agents (id, company_id, name) VALUES (10, 7, 'DB Agent')")
        self.conn.executemany(
            """
            INSERT INTO database_tool_audit_logs (
                id, company_id, agent_id, user_id, profile_index, profile_name,
                engine, operation, status, sql_text, sql_hash, request_message,
                affected_rows, max_affected_rows, created_at, executed_at
            )
            VALUES (?, 7, 10, ?, 0, 'Profile', 'mysql', 'select', 'executed', 'SELECT 1', 'h', 'q', 1, 10, ?, ?)
            """,
            [
                (100, 1, "2026-07-31T08:00:00+00:00", "2026-07-31T08:00:00+00:00"),
                (101, 2, "2026-07-30T08:00:00+00:00", "2026-07-30T08:00:00+00:00"),
            ],
        )

    def tearDown(self):
        self.conn.close()

    def test_list_uses_display_names_and_filters_by_user_and_date(self):
        rows = self.service.list_for_company(
            self.conn,
            company_id=7,
            user_query="senath",
            activity_date="2026-07-31",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], 100)
        self.assertEqual(rows[0]["requester_display_name"], "Senath Perera")

    def test_display_name_falls_back_to_email_local_part(self):
        rows = self.service.list_for_company(self.conn, company_id=7, user_query="maya")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["requester_display_name"], "Maya")

    def test_execution_result_and_error_are_redacted_in_the_audit_view(self):
        self.service.create_activity(
            self.conn,
            company_id=7,
            agent_id=10,
            user_id=1,
            chat_session_id=None,
            profile={"index": 0, "name": "Profile", "engine": "mysql", "limits": {}},
            operation="select",
            sql="SELECT 1",
            question="Run report",
            status="failed",
            result={"type": "database", "response": {"access_token": "db-secret"}},
            error_message="token=error-secret",
        )

        rows = self.service.list_for_company(self.conn, company_id=7)

        rendered = json.dumps(rows)
        self.assertNotIn("db-secret", rendered)
        self.assertNotIn("error-secret", rendered)


if __name__ == "__main__":
    unittest.main()
