import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from whatsapp_addon_service import (
    get_company_whatsapp_subscription,
    save_company_whatsapp_subscription,
    save_whatsapp_addon_config,
)
from whatsapp_addon_webhook_service import process_whatsapp_stripe_event


class WhatsAppAddonWebhookServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE feature_addon_configs (
                feature_key TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                monthly_price_cents INTEGER NOT NULL DEFAULT 0,
                currency TEXT NOT NULL DEFAULT 'usd',
                monthly_quota INTEGER NOT NULL DEFAULT 0,
                is_available INTEGER NOT NULL DEFAULT 0,
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
            CREATE TABLE company_feature_entitlements (
                company_id INTEGER NOT NULL,
                feature_key TEXT NOT NULL,
                is_enabled INTEGER NOT NULL DEFAULT 0,
                enabled_at TEXT,
                disabled_at TEXT,
                updated_at TEXT NOT NULL,
                updated_by_user_id INTEGER,
                PRIMARY KEY (company_id, feature_key)
            );
            CREATE TABLE payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workspace_id INTEGER NOT NULL,
                transaction_id TEXT NOT NULL UNIQUE,
                payment_gateway TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL,
                payment_status TEXT NOT NULL,
                payment_date TEXT NOT NULL,
                stripe_session_id TEXT,
                expires_at TEXT,
                plan_name TEXT,
                purchase_type TEXT NOT NULL DEFAULT 'workspace_subscription',
                feature_key TEXT,
                stripe_subscription_id TEXT,
                stripe_invoice_id TEXT UNIQUE
            );
            """
        )
        self.conn.execute(
            """
            INSERT INTO company_feature_entitlements (
                company_id, feature_key, is_enabled, updated_at
            ) VALUES (41, 'whatsapp', 0, '2026-08-19T00:00:00+00:00')
            """
        )
        save_whatsapp_addon_config(
            self.conn,
            monthly_price_cents=1999,
            monthly_quota=750,
            is_available=True,
        )
        save_company_whatsapp_subscription(
            self.conn,
            41,
            status="checkout_pending",
            monthly_price_cents=1999,
            monthly_quota=750,
            stripe_checkout_session_id="cs_41",
            stripe_price_id="price_1999",
        )
        self.conn.execute(
            """
            INSERT INTO payments (
                workspace_id, transaction_id, payment_gateway, amount,
                currency, payment_status, payment_date, stripe_session_id,
                plan_name, purchase_type, feature_key
            ) VALUES (
                41, 'WA_cs_41', 'stripe', 19.99, 'usd', 'pending',
                '2026-08-19T00:00:00+00:00', 'cs_41', 'WhatsApp Add-on',
                'feature_addon', 'whatsapp'
            )
            """
        )
        self.stripe = SimpleNamespace(
            Subscription=SimpleNamespace(retrieve=Mock()),
        )

    def tearDown(self):
        self.conn.close()

    def test_checkout_activation_and_invoice_renewal_do_not_create_quota_periods(self):
        initial_subscription = self._subscription(
            period_start=1785542400,
            period_end=1788220800,
        )
        self.stripe.Subscription.retrieve.return_value = initial_subscription
        checkout_event = self._event(
            "evt_checkout",
            "checkout.session.completed",
            1000,
            {
                "id": "cs_41",
                "subscription": "sub_41",
                "metadata": {"company_id": "41", "feature_key": "whatsapp"},
            },
        )

        self.assertTrue(
            process_whatsapp_stripe_event(self.conn, self.stripe, checkout_event)
        )
        active = get_company_whatsapp_subscription(self.conn, 41)
        self.assertEqual(active["status"], "active")
        self.assertEqual(active["stripe_subscription_id"], "sub_41")
        self.assertEqual(active["stripe_event_created_at"], 1000)
        self.assertTrue(self._entitlement_enabled())
        self.assertIsNone(
            self.conn.execute("SELECT * FROM company_feature_usage_periods").fetchone()
        )

        save_whatsapp_addon_config(
            self.conn,
            monthly_price_cents=1999,
            monthly_quota=900,
            is_available=True,
        )
        renewed_subscription = self._subscription(
            period_start=1788220800,
            period_end=1790812800,
        )
        self.stripe.Subscription.retrieve.return_value = renewed_subscription
        invoice_event = self._event(
            "evt_invoice",
            "invoice.paid",
            2000,
            {
                "id": "in_renewal",
                "subscription": "sub_41",
                "amount_paid": 1999,
                "currency": "usd",
                "status_transitions": {"paid_at": 1788220800},
            },
        )

        self.assertTrue(
            process_whatsapp_stripe_event(self.conn, self.stripe, invoice_event)
        )
        renewed = get_company_whatsapp_subscription(self.conn, 41)
        self.assertEqual(renewed["monthly_quota"], 900)
        periods = self.conn.execute("SELECT id FROM company_feature_usage_periods").fetchall()
        self.assertEqual(periods, [])
        payment = self.conn.execute(
            "SELECT * FROM payments WHERE stripe_invoice_id = 'in_renewal'"
        ).fetchone()
        self.assertEqual(payment["payment_status"], "completed")
        self.assertEqual(payment["stripe_subscription_id"], "sub_41")

    def test_cancel_at_period_end_stays_enabled_until_subscription_deleted(self):
        self._activate(event_created=1000)
        canceling = self._subscription(
            period_start=1785542400,
            period_end=1788220800,
            cancel_at_period_end=True,
        )
        cancel_event = self._event(
            "evt_cancel",
            "customer.subscription.updated",
            2000,
            canceling,
        )

        self.stripe.Subscription.retrieve.return_value = canceling
        process_whatsapp_stripe_event(self.conn, self.stripe, cancel_event)
        self.assertEqual(
            get_company_whatsapp_subscription(self.conn, 41)["status"],
            "cancel_at_period_end",
        )
        self.assertTrue(self._entitlement_enabled())

        deleted = self._subscription(
            period_start=1785542400,
            period_end=1788220800,
            status="canceled",
            canceled_at=1788220800,
        )
        delete_event = self._event(
            "evt_deleted",
            "customer.subscription.deleted",
            3000,
            deleted,
        )
        process_whatsapp_stripe_event(self.conn, self.stripe, delete_event)
        self.assertEqual(
            get_company_whatsapp_subscription(self.conn, 41)["status"],
            "canceled",
        )
        self.assertFalse(self._entitlement_enabled())

        stale_active = self._event(
            "evt_stale",
            "customer.subscription.updated",
            3000,
            self._subscription(
                period_start=1785542400,
                period_end=1788220800,
            ),
        )
        self.stripe.Subscription.retrieve.return_value = deleted
        process_whatsapp_stripe_event(self.conn, self.stripe, stale_active)
        self.assertEqual(
            get_company_whatsapp_subscription(self.conn, 41)["status"],
            "canceled",
        )
        self.assertFalse(self._entitlement_enabled())

    def test_failed_invoice_marks_subscription_past_due_and_disables_access(self):
        self._activate(event_created=1000)
        self.stripe.Subscription.retrieve.return_value = self._subscription(
            period_start=1785542400,
            period_end=1788220800,
            status="past_due",
        )
        failed_event = self._event(
            "evt_failed",
            "invoice.payment_failed",
            2000,
            {
                "id": "in_failed",
                "subscription": "sub_41",
                "amount_due": 1999,
                "currency": "usd",
            },
        )

        process_whatsapp_stripe_event(self.conn, self.stripe, failed_event)

        self.assertEqual(
            get_company_whatsapp_subscription(self.conn, 41)["status"],
            "past_due",
        )
        self.assertFalse(self._entitlement_enabled())
        payment = self.conn.execute(
            "SELECT * FROM payments WHERE stripe_invoice_id = 'in_failed'"
        ).fetchone()
        self.assertEqual(payment["payment_status"], "failed")

    def test_expired_checkout_cancels_pending_payment_and_allows_a_retry(self):
        expired_event = self._event(
            "evt_expired",
            "checkout.session.expired",
            1000,
            {
                "id": "cs_41",
                "metadata": {"company_id": "41", "feature_key": "whatsapp"},
            },
        )

        self.assertTrue(
            process_whatsapp_stripe_event(self.conn, self.stripe, expired_event)
        )

        subscription = get_company_whatsapp_subscription(self.conn, 41)
        self.assertEqual(subscription["status"], "expired")
        self.assertFalse(self._entitlement_enabled())
        payment = self.conn.execute(
            "SELECT payment_status FROM payments WHERE stripe_session_id = 'cs_41'"
        ).fetchone()
        self.assertEqual(payment["payment_status"], "cancelled")

    def test_older_subscription_event_cannot_overwrite_newer_state(self):
        self._activate(event_created=2000)
        stale_subscription = self._subscription(
            period_start=1785542400,
            period_end=1788220800,
            status="past_due",
        )
        self.stripe.Subscription.retrieve.return_value = stale_subscription

        self.assertTrue(
            process_whatsapp_stripe_event(
                self.conn,
                self.stripe,
                self._event(
                    "evt_stale_update",
                    "customer.subscription.updated",
                    1500,
                    stale_subscription,
                ),
            )
        )

        subscription = get_company_whatsapp_subscription(self.conn, 41)
        self.assertEqual(subscription["status"], "active")
        self.assertEqual(subscription["stripe_event_created_at"], 2000)
        self.assertTrue(self._entitlement_enabled())

    def test_paid_invoice_restores_access_after_payment_failure(self):
        self._activate(event_created=1000)
        failed_subscription = self._subscription(
            period_start=1785542400,
            period_end=1788220800,
            status="past_due",
        )
        self.stripe.Subscription.retrieve.return_value = failed_subscription
        process_whatsapp_stripe_event(
            self.conn,
            self.stripe,
            self._event(
                "evt_failed",
                "invoice.payment_failed",
                2000,
                {
                    "id": "in_recovered",
                    "subscription": "sub_41",
                    "amount_due": 1999,
                    "currency": "usd",
                },
            ),
        )

        recovered_subscription = self._subscription(
            period_start=1785542400,
            period_end=1788220800,
        )
        self.stripe.Subscription.retrieve.return_value = recovered_subscription
        self.assertTrue(
            process_whatsapp_stripe_event(
                self.conn,
                self.stripe,
                self._event(
                    "evt_recovered",
                    "invoice.paid",
                    3000,
                    {
                        "id": "in_recovered",
                        "subscription": "sub_41",
                        "amount_paid": 1999,
                        "currency": "usd",
                        "status_transitions": {"paid_at": 1785542400},
                    },
                ),
            )
        )

        self.assertEqual(
            get_company_whatsapp_subscription(self.conn, 41)["status"],
            "active",
        )
        self.assertTrue(self._entitlement_enabled())
        payments = self.conn.execute(
            "SELECT payment_status FROM payments WHERE stripe_invoice_id = 'in_recovered'"
        ).fetchall()
        self.assertEqual([row["payment_status"] for row in payments], ["completed"])

    def test_unrelated_stripe_event_is_ignored(self):
        handled = process_whatsapp_stripe_event(
            self.conn,
            self.stripe,
            self._event("evt_customer", "customer.updated", 1000, {"id": "cus_41"}),
        )

        self.assertFalse(handled)
        self.stripe.Subscription.retrieve.assert_not_called()
        self.assertEqual(
            get_company_whatsapp_subscription(self.conn, 41)["status"],
            "checkout_pending",
        )

    def _activate(self, *, event_created):
        subscription = self._subscription(
            period_start=1785542400,
            period_end=1788220800,
        )
        self.stripe.Subscription.retrieve.return_value = subscription
        process_whatsapp_stripe_event(
            self.conn,
            self.stripe,
            self._event(
                "evt_activate",
                "checkout.session.completed",
                event_created,
                {
                    "id": "cs_41",
                    "subscription": "sub_41",
                    "metadata": {"company_id": "41", "feature_key": "whatsapp"},
                },
            ),
        )

    def _entitlement_enabled(self):
        row = self.conn.execute(
            """
            SELECT is_enabled FROM company_feature_entitlements
            WHERE company_id = 41 AND feature_key = 'whatsapp'
            """
        ).fetchone()
        return bool(row["is_enabled"])

    @staticmethod
    def _subscription(
        *,
        period_start,
        period_end,
        status="active",
        cancel_at_period_end=False,
        canceled_at=None,
    ):
        return {
            "id": "sub_41",
            "customer": "cus_41",
            "status": status,
            "cancel_at_period_end": cancel_at_period_end,
            "canceled_at": canceled_at,
            "current_period_start": period_start,
            "current_period_end": period_end,
            "metadata": {"company_id": "41", "feature_key": "whatsapp"},
            "items": {
                "data": [
                    {
                        "id": "si_41",
                        "price": {
                            "id": "price_1999",
                            "unit_amount": 1999,
                            "currency": "usd",
                        },
                    }
                ]
            },
        }

    @staticmethod
    def _event(event_id, event_type, created, event_object):
        return {
            "id": event_id,
            "type": event_type,
            "created": created,
            "livemode": False,
            "data": {"object": event_object},
        }


if __name__ == "__main__":
    unittest.main()
