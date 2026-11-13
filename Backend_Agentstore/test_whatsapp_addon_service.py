import json
import sqlite3
import unittest

from whatsapp_addon_service import (
    claim_stripe_webhook_event,
    consume_whatsapp_agent_quota,
    ensure_whatsapp_usage_period,
    finish_stripe_webhook_event,
    get_company_whatsapp_subscription,
    get_company_whatsapp_usage_summary,
    get_whatsapp_addon_config,
    get_whatsapp_usage_period,
    register_stripe_webhook_event,
    save_company_whatsapp_subscription,
    save_whatsapp_addon_config,
    set_company_whatsapp_subscription_cancel_at_period_end,
    set_whatsapp_addon_stripe_catalog,
)


class WhatsAppAddonServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE feature_addon_configs (
                feature_key TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                monthly_price_cents INTEGER NOT NULL DEFAULT 0
                    CHECK (monthly_price_cents >= 0),
                currency TEXT NOT NULL DEFAULT 'usd',
                monthly_quota INTEGER NOT NULL DEFAULT 0
                    CHECK (monthly_quota >= 0),
                is_available INTEGER NOT NULL DEFAULT 0
                    CHECK (is_available IN (0,1)),
                stripe_product_id TEXT,
                stripe_price_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by_user_id INTEGER
            );

            CREATE TABLE company_feature_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                feature_key TEXT NOT NULL,
                status TEXT NOT NULL,
                billing_source TEXT NOT NULL DEFAULT 'stripe',
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

            CREATE TABLE stripe_webhook_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                livemode INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL,
                processing_status TEXT NOT NULL DEFAULT 'received',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                processed_at TEXT
            );
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_default_config_is_seeded_once_and_unavailable(self):
        first = get_whatsapp_addon_config(self.conn)
        second = get_whatsapp_addon_config(self.conn)

        self.assertEqual(first["feature"], "whatsapp")
        self.assertEqual(first["monthly_price_cents"], 0)
        self.assertEqual(first["monthly_quota"], 0)
        self.assertFalse(first["available"])
        self.assertEqual(first["created_at"], second["created_at"])
        count = self.conn.execute(
            "SELECT COUNT(*) FROM feature_addon_configs WHERE feature_key = 'whatsapp'"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_config_requires_positive_price_and_quota_when_available(self):
        with self.assertRaises(ValueError):
            save_whatsapp_addon_config(
                self.conn,
                monthly_price_cents=0,
                monthly_quota=100,
                is_available=True,
            )

        with self.assertRaises(ValueError):
            save_whatsapp_addon_config(
                self.conn,
                monthly_price_cents=999,
                monthly_quota=0,
                is_available=True,
            )

        with self.assertRaises(TypeError):
            save_whatsapp_addon_config(
                self.conn,
                monthly_price_cents=True,
                monthly_quota=100,
                is_available=False,
            )

    def test_repricing_invalidates_only_the_cached_stripe_price(self):
        configured = save_whatsapp_addon_config(
            self.conn,
            monthly_price_cents=2500,
            monthly_quota=500,
            is_available=True,
            stripe_product_id="prod_whatsapp",
            stripe_price_id="price_monthly",
            updated_by_user_id=9,
        )
        repriced = save_whatsapp_addon_config(
            self.conn,
            monthly_price_cents=3000,
            monthly_quota=750,
            is_available=True,
            updated_by_user_id=10,
        )

        self.assertEqual(configured["monthly_price_usd"], 25)
        self.assertEqual(repriced["monthly_price_cents"], 3000)
        self.assertEqual(repriced["monthly_quota"], 750)
        self.assertEqual(repriced["stripe_product_id"], "prod_whatsapp")
        self.assertIsNone(repriced["stripe_price_id"])
        self.assertEqual(repriced["updated_by_user_id"], 10)

        catalog = set_whatsapp_addon_stripe_catalog(
            self.conn,
            stripe_product_id="prod_whatsapp",
            stripe_price_id="price_repriced",
        )
        quota_only_update = save_whatsapp_addon_config(
            self.conn,
            monthly_price_cents=3000,
            monthly_quota=900,
            is_available=True,
        )
        self.assertEqual(catalog["stripe_price_id"], "price_repriced")
        self.assertEqual(quota_only_update["stripe_price_id"], "price_repriced")

    def test_subscription_defaults_to_not_purchased_and_upserts_one_row(self):
        missing = get_company_whatsapp_subscription(self.conn, 41)
        self.assertEqual(missing["status"], "not_purchased")
        self.assertIsNone(missing["id"])

        created = self._save_active_subscription(
            company_id=41,
            price=2500,
            quota=500,
        )
        updated = self._save_active_subscription(
            company_id=41,
            price=2500,
            quota=500,
            cancel_at_period_end=True,
            status="cancel_at_period_end",
        )

        self.assertEqual(created["id"], updated["id"])
        self.assertTrue(updated["cancel_at_period_end"])
        count = self.conn.execute(
            "SELECT COUNT(*) FROM company_feature_subscriptions WHERE company_id = 41"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_active_subscription_requires_a_valid_billing_period(self):
        with self.assertRaises(ValueError):
            save_company_whatsapp_subscription(
                self.conn,
                41,
                status="active",
                monthly_price_cents=2500,
                monthly_quota=500,
            )

        with self.assertRaises(ValueError):
            save_company_whatsapp_subscription(
                self.conn,
                41,
                status="active",
                monthly_price_cents=2500,
                monthly_quota=500,
                current_period_start="2026-09-01T00:00:00+00:00",
                current_period_end="2026-08-01T00:00:00+00:00",
            )

        with self.assertRaises(ValueError):
            save_company_whatsapp_subscription(
                self.conn,
                41,
                status="paused",
                monthly_price_cents=2500,
                monthly_quota=500,
            )

    def test_subscription_renewal_state_can_be_canceled_and_restored(self):
        self._save_active_subscription(company_id=41, price=2500, quota=500)

        canceling = set_company_whatsapp_subscription_cancel_at_period_end(
            self.conn,
            41,
            cancel_at_period_end=True,
        )
        self.assertEqual(canceling["status"], "cancel_at_period_end")
        self.assertTrue(canceling["cancel_at_period_end"])

        active = set_company_whatsapp_subscription_cancel_at_period_end(
            self.conn,
            41,
            cancel_at_period_end=False,
        )
        self.assertEqual(active["status"], "active")
        self.assertFalse(active["cancel_at_period_end"])

    def test_subscription_renewal_state_rejects_invalid_transition(self):
        self._save_active_subscription(company_id=41, price=2500, quota=500)

        with self.assertRaises(ValueError):
            set_company_whatsapp_subscription_cancel_at_period_end(
                self.conn,
                41,
                cancel_at_period_end=False,
            )

    def test_usage_period_keeps_its_quota_snapshot(self):
        subscription = self._save_active_subscription(
            company_id=41,
            price=2500,
            quota=500,
        )
        original = ensure_whatsapp_usage_period(
            self.conn,
            company_id=41,
            feature_subscription_id=subscription["id"],
            period_start="2026-08-01T00:00:00+00:00",
            period_end="2026-09-01T00:00:00+00:00",
            quota_limit=500,
        )
        duplicate = ensure_whatsapp_usage_period(
            self.conn,
            company_id=41,
            feature_subscription_id=subscription["id"],
            period_start="2026-08-01T00:00:00+00:00",
            period_end="2026-09-01T00:00:00+00:00",
            quota_limit=900,
        )

        self.assertEqual(original["id"], duplicate["id"])
        self.assertEqual(duplicate["quota_limit"], 500)
        self.assertEqual(duplicate["remaining"], 500)
        current = get_whatsapp_usage_period(
            self.conn,
            41,
            at_time="2026-08-19T12:00:00+00:00",
        )
        self.assertEqual(current["id"], original["id"])
        self.assertIsNone(
            get_whatsapp_usage_period(
                self.conn,
                41,
                at_time="2026-09-01T00:00:00+00:00",
            )
        )

    def test_agent_quota_is_atomic_and_idempotent(self):
        subscription = self._save_active_subscription(
            company_id=41,
            price=2500,
            quota=2,
        )
        ensure_whatsapp_usage_period(
            self.conn,
            company_id=41,
            feature_subscription_id=subscription["id"],
            period_start="2026-08-01T00:00:00+00:00",
            period_end="2026-09-01T00:00:00+00:00",
            quota_limit=2,
        )

        first = consume_whatsapp_agent_quota(
            self.conn,
            41,
            idempotency_key="whatsapp_dispatch:1",
            at_time="2026-08-19T12:00:00+00:00",
        )
        duplicate = consume_whatsapp_agent_quota(
            self.conn,
            41,
            idempotency_key="whatsapp_dispatch:1",
            at_time="2026-08-19T12:00:00+00:00",
        )
        second = consume_whatsapp_agent_quota(
            self.conn,
            41,
            idempotency_key="whatsapp_dispatch:2",
            at_time="2026-08-19T12:00:00+00:00",
        )
        blocked = consume_whatsapp_agent_quota(
            self.conn,
            41,
            idempotency_key="whatsapp_dispatch:3",
            at_time="2026-08-19T12:00:00+00:00",
        )

        self.assertTrue(first["allowed"])
        self.assertTrue(first["counted"])
        self.assertTrue(duplicate["allowed"])
        self.assertFalse(duplicate["counted"])
        self.assertTrue(second["allowed"])
        self.assertFalse(blocked["allowed"])
        self.assertEqual(blocked["reason"], "quota_exceeded")
        self.assertEqual(blocked["usage"]["units_used"], 2)
        event_count = self.conn.execute(
            "SELECT COUNT(*) FROM company_feature_usage_events"
        ).fetchone()[0]
        self.assertEqual(event_count, 2)

    def test_usage_summary_uses_the_billing_period_quota_snapshot(self):
        subscription = self._save_active_subscription(
            company_id=41,
            price=2500,
            quota=500,
        )
        usage = ensure_whatsapp_usage_period(
            self.conn,
            company_id=41,
            feature_subscription_id=subscription["id"],
            period_start="2026-08-01T00:00:00+00:00",
            period_end="2026-09-01T00:00:00+00:00",
            quota_limit=500,
        )
        self.conn.execute(
            "UPDATE company_feature_usage_periods SET units_used = 125 WHERE id = ?",
            (usage["id"],),
        )

        summary = get_company_whatsapp_usage_summary(
            self.conn,
            41,
            at_time="2026-08-19T12:00:00+00:00",
        )

        self.assertTrue(summary["enabled"])
        self.assertEqual(summary["subscription_status"], "active")
        self.assertEqual(summary["quota_limit"], 500)
        self.assertEqual(summary["units_used"], 125)
        self.assertEqual(summary["remaining"], 375)
        self.assertEqual(summary["usage_percent"], 25)
        self.assertFalse(summary["quota_reached"])

    def test_legacy_subscription_rolls_into_a_new_calendar_month(self):
        save_whatsapp_addon_config(
            self.conn,
            monthly_price_cents=1999,
            monthly_quota=900,
            is_available=True,
        )
        subscription = self._save_active_subscription(
            company_id=41,
            price=0,
            quota=500,
        )
        self.conn.execute(
            "UPDATE company_feature_subscriptions SET billing_source = 'legacy' WHERE id = ?",
            (subscription["id"],),
        )
        ensure_whatsapp_usage_period(
            self.conn,
            company_id=41,
            feature_subscription_id=subscription["id"],
            period_start="2026-08-01T00:00:00+00:00",
            period_end="2026-09-01T00:00:00+00:00",
            quota_limit=500,
        )

        summary = get_company_whatsapp_usage_summary(
            self.conn,
            41,
            at_time="2026-09-15T12:00:00+00:00",
        )

        self.assertEqual(summary["billing_source"], "legacy")
        self.assertEqual(summary["quota_limit"], 900)
        self.assertEqual(summary["period_start"], "2026-09-01T00:00:00+00:00")
        self.assertEqual(summary["period_end"], "2026-10-01T00:00:00+00:00")
        periods = self.conn.execute(
            "SELECT period_start, quota_limit FROM company_feature_usage_periods ORDER BY period_start"
        ).fetchall()
        self.assertEqual(len(periods), 2)
        self.assertEqual(periods[0]["quota_limit"], 500)
        self.assertEqual(periods[1]["quota_limit"], 900)

    def test_agent_quota_rejects_processing_outside_the_billing_period(self):
        self._save_active_subscription(company_id=41, price=2500, quota=2)

        result = consume_whatsapp_agent_quota(
            self.conn,
            41,
            idempotency_key="whatsapp_dispatch:1",
            at_time="2026-09-01T00:00:00+00:00",
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "billing_period_unavailable")
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM company_feature_usage_events"
            ).fetchone()[0],
            0,
        )

    def test_agent_quota_rejects_inactive_subscription_without_counting(self):
        subscription = self._save_active_subscription(
            company_id=41,
            price=2500,
            quota=2,
        )
        ensure_whatsapp_usage_period(
            self.conn,
            company_id=41,
            feature_subscription_id=subscription["id"],
            period_start="2026-08-01T00:00:00+00:00",
            period_end="2026-09-01T00:00:00+00:00",
            quota_limit=2,
        )
        self.conn.execute(
            "UPDATE company_feature_subscriptions SET status = 'past_due' WHERE id = ?",
            (subscription["id"],),
        )

        result = consume_whatsapp_agent_quota(
            self.conn,
            41,
            idempotency_key="whatsapp_dispatch:blocked",
            at_time="2026-08-19T12:00:00+00:00",
        )

        self.assertFalse(result["allowed"])
        self.assertEqual(result["reason"], "subscription_inactive")
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM company_feature_usage_events"
            ).fetchone()[0],
            0,
        )

    def test_stripe_event_inbox_is_idempotent_and_retryable(self):
        payload = {"id": "evt_123", "type": "invoice.paid"}
        self.assertTrue(
            register_stripe_webhook_event(
                self.conn,
                event_id="evt_123",
                event_type="invoice.paid",
                payload=payload,
                livemode=False,
            )
        )
        self.assertFalse(
            register_stripe_webhook_event(
                self.conn,
                event_id="evt_123",
                event_type="invoice.paid",
                payload=payload,
                livemode=False,
            )
        )

        self.assertTrue(claim_stripe_webhook_event(self.conn, "evt_123"))
        self.assertFalse(claim_stripe_webhook_event(self.conn, "evt_123"))
        finish_stripe_webhook_event(
            self.conn,
            "evt_123",
            completed=False,
            error="temporary failure",
        )
        self.assertTrue(claim_stripe_webhook_event(self.conn, "evt_123"))
        finish_stripe_webhook_event(self.conn, "evt_123", completed=True)
        self.assertFalse(claim_stripe_webhook_event(self.conn, "evt_123"))

        row = self.conn.execute(
            "SELECT * FROM stripe_webhook_events WHERE event_id = 'evt_123'"
        ).fetchone()
        self.assertEqual(row["processing_status"], "completed")
        self.assertEqual(row["attempt_count"], 2)
        self.assertIsNone(row["last_error"])
        self.assertIsNotNone(row["processed_at"])
        self.assertEqual(json.loads(row["payload_json"]), payload)

    def _save_active_subscription(
        self,
        *,
        company_id,
        price,
        quota,
        cancel_at_period_end=False,
        status="active",
    ):
        return save_company_whatsapp_subscription(
            self.conn,
            company_id,
            status=status,
            monthly_price_cents=price,
            monthly_quota=quota,
            stripe_customer_id="cus_41",
            stripe_subscription_id="sub_41",
            stripe_subscription_item_id="si_41",
            stripe_checkout_session_id="cs_41",
            stripe_price_id="price_monthly",
            current_period_start="2026-08-01T00:00:00+00:00",
            current_period_end="2026-09-01T00:00:00+00:00",
            cancel_at_period_end=cancel_at_period_end,
        )


if __name__ == "__main__":
    unittest.main()
