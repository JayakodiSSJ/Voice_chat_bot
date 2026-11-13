import sqlite3
import unittest

from auth_sqlite import migrate_legacy_whatsapp_entitlements


class WhatsAppLegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE feature_addon_configs (
                feature_key TEXT PRIMARY KEY,
                monthly_quota INTEGER NOT NULL
            );
            CREATE TABLE company_feature_entitlements (
                company_id INTEGER NOT NULL,
                feature_key TEXT NOT NULL,
                is_enabled INTEGER NOT NULL,
                PRIMARY KEY (company_id, feature_key)
            );
            CREATE TABLE company_feature_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                feature_key TEXT NOT NULL,
                status TEXT NOT NULL,
                billing_source TEXT NOT NULL DEFAULT 'stripe',
                monthly_price_cents INTEGER NOT NULL,
                currency TEXT NOT NULL,
                monthly_quota INTEGER NOT NULL,
                current_period_start TEXT,
                current_period_end TEXT,
                cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
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
            INSERT INTO feature_addon_configs (feature_key, monthly_quota)
            VALUES ('whatsapp', 750);
            INSERT INTO company_feature_entitlements
                (company_id, feature_key, is_enabled)
            VALUES (41, 'whatsapp', 1);
            INSERT INTO company_feature_entitlements
                (company_id, feature_key, is_enabled)
            VALUES (42, 'whatsapp', 0);
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_migrates_enabled_entitlements_once_without_creating_payments(self):
        first = migrate_legacy_whatsapp_entitlements(
            self.conn,
            at_time="2026-08-19T12:00:00+00:00",
        )
        second = migrate_legacy_whatsapp_entitlements(
            self.conn,
            at_time="2026-08-19T12:00:00+00:00",
        )

        self.assertEqual(first["subscriptions_created"], 1)
        self.assertEqual(first["usage_periods_created"], 1)
        self.assertEqual(second["subscriptions_created"], 0)
        self.assertEqual(second["usage_periods_created"], 0)
        subscription = self.conn.execute(
            "SELECT * FROM company_feature_subscriptions"
        ).fetchone()
        self.assertEqual(subscription["company_id"], 41)
        self.assertEqual(subscription["billing_source"], "legacy")
        self.assertEqual(subscription["monthly_price_cents"], 0)
        self.assertEqual(subscription["monthly_quota"], 750)
        self.assertEqual(
            subscription["current_period_start"],
            "2026-08-01T00:00:00+00:00",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM company_feature_usage_periods"
            ).fetchone()[0],
            1,
        )


if __name__ == "__main__":
    unittest.main()
