import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from auth_routes import _check_chat_quota, _record_chat_token_usage
from token_usage import TokenUsageCollector


class ChatTokenQuotaTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE company (
                id INTEGER PRIMARY KEY,
                created_at TEXT,
                subscription_tier TEXT,
                subscription_status TEXT,
                subscription_expires_at TEXT
            );
            CREATE TABLE subscriptions (
                id INTEGER PRIMARY KEY,
                workspace_id INTEGER,
                status TEXT,
                started_at TEXT,
                expires_at TEXT
            );
            CREATE TABLE company_chat_usage (
                company_id INTEGER PRIMARY KEY,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                messages_used INTEGER NOT NULL DEFAULT 0,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE user_chat_usage (
                company_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                messages_used INTEGER NOT NULL DEFAULT 0,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (company_id, user_id)
            );
            CREATE TABLE user_chat_quota_overrides (
                company_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                monthly_token_limit INTEGER,
                is_suspended INTEGER NOT NULL DEFAULT 0,
                suspended_until TEXT,
                reason TEXT,
                PRIMARY KEY (company_id, user_id)
            );
            CREATE TABLE chat_token_usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider_response_id TEXT NOT NULL UNIQUE,
                company_id INTEGER NOT NULL,
                user_id INTEGER,
                model TEXT NOT NULL,
                input_tokens INTEGER NOT NULL,
                output_tokens INTEGER NOT NULL,
                total_tokens INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            INSERT INTO company VALUES (1, '2026-01-01T00:00:00+00:00', 'starter', 'active', NULL);
            """
        )
        now = datetime.now(timezone.utc)
        self.period = (now - timedelta(days=1), now + timedelta(days=1))
        self.limit_patch = patch("auth_routes._chat_limit_for_company", return_value=("starter", 20))
        self.period_patch = patch("auth_routes._monthly_chat_period_for_company", return_value=self.period)
        self.limit_patch.start()
        self.period_patch.start()

    def tearDown(self):
        self.limit_patch.stop()
        self.period_patch.stop()
        self.conn.close()

    @staticmethod
    def _collector() -> TokenUsageCollector:
        collector = TokenUsageCollector()
        collector.add(
            response_id="resp_once",
            model="gpt-test",
            usage={"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
        )
        return collector

    def test_actual_tokens_are_charged_once_and_then_block_the_workspace(self):
        first = _record_chat_token_usage(self.conn, 1, 7, self._collector())
        duplicate = _record_chat_token_usage(self.conn, 1, 7, self._collector())
        checked = _check_chat_quota(self.conn, 1, 7)

        self.assertEqual(first["tokens_used"], 20)
        self.assertEqual(first["tokens_remaining"], 0)
        self.assertEqual(duplicate["request_token_usage"]["total_tokens"], 0)
        self.assertEqual(duplicate["tokens_used"], 20)
        self.assertFalse(checked["allowed"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM chat_token_usage_events").fetchone()[0],
            1,
        )
        self.assertEqual(
            self.conn.execute("SELECT tokens_used FROM user_chat_usage").fetchone()[0],
            20,
        )


if __name__ == "__main__":
    unittest.main()
