from functools import wraps
import os
import sqlite3
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flask import Flask, request

import auth_routes
from whatsapp_addon_service import (
    save_company_whatsapp_subscription,
    save_whatsapp_addon_config,
)


def _company_admin_auth(_app):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            request.auth = {
                "user_id": 12,
                "company_id": 41,
                "email": "admin@example.com",
                "role": "admin",
            }
            return fn(*args, **kwargs)

        return wrapper

    return decorator


class WhatsAppAddonCheckoutRouteTests(unittest.TestCase):
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
            CREATE TABLE company_whatsapp_connections (
                company_id INTEGER PRIMARY KEY,
                phone_number TEXT NOT NULL UNIQUE,
                provider TEXT NOT NULL DEFAULT 'meta',
                webhook_key TEXT UNIQUE,
                verification_token_secret TEXT,
                meta_app_secret TEXT,
                meta_phone_number_id TEXT,
                meta_access_token_secret TEXT,
                waha_session TEXT,
                waha_api_key_secret TEXT,
                webhook_verified_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                updated_by_user_id INTEGER
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
                stripe_invoice_id TEXT
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
        self.conn.commit()

        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            SECRET_KEY="test-secret",
            FRONTEND_URL="https://app.example.com",
        )
        self.patches = [
            patch.object(auth_routes, "init_db"),
            patch.object(auth_routes, "_mark_stale_running_ingestions"),
            patch.object(auth_routes, "get_conn", return_value=self.conn),
            patch.object(auth_routes, "require_auth", new=_company_admin_auth),
            patch.object(auth_routes, "_is_super_admin", return_value=False),
        ]
        for active_patch in self.patches:
            active_patch.start()
        auth_routes.register_auth_routes(self.app, mail=None)
        self.client = self.app.test_client()
        self.stripe = SimpleNamespace(
            api_key=None,
            Subscription=SimpleNamespace(modify=Mock(), retrieve=Mock()),
            checkout=SimpleNamespace(
                Session=SimpleNamespace(retrieve=Mock()),
            ),
        )
        self.stripe_module_patch = patch.dict(sys.modules, {"stripe": self.stripe})
        self.stripe_module_patch.start()
        self.stripe_key_patch = patch.dict(
            os.environ,
            {
                "STRIPE_SECRET_KEY": "sk_test",
                "STRIPE_WEBHOOK_SECRET": "",
            },
        )
        self.stripe_key_patch.start()

    def tearDown(self):
        self.stripe_key_patch.stop()
        self.stripe_module_patch.stop()
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.conn.close()

    def test_configuration_get_includes_offer_and_purchase_state(self):
        response = self.client.get("/api/auth/whatsapp/configuration")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["entitlement"]["enabled"])
        self.assertEqual(payload["addon"]["offer"]["monthly_price_cents"], 1999)
        self.assertTrue(payload["addon"]["uses_workspace_token_quota"])
        self.assertNotIn("monthly_quota", payload["addon"]["offer"])
        self.assertEqual(payload["addon"]["subscription"]["status"], "not_purchased")
        self.assertTrue(payload["addon"]["can_purchase"])

    def test_configuration_put_saves_waha_provider_credentials(self):
        self.conn.execute(
            """
            UPDATE company_feature_entitlements
            SET is_enabled = 1
            WHERE company_id = 41 AND feature_key = 'whatsapp'
            """
        )
        self.conn.commit()

        response = self.client.put(
            "/api/auth/whatsapp/configuration",
            json={
                "phone_number": "+94771234567",
                "provider": "waha",
                "waha_session": "workspace-41",
                "waha_api_key": "waha-secret-key",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["connection"]["provider"], "waha")
        self.assertEqual(payload["connection"]["waha_session"], "workspace-41")
        self.assertTrue(payload["connection"]["waha_api_key_configured"])
        self.assertTrue(payload["connection"]["outbound_ready"])
        self.assertNotIn("waha_api_key_secret", payload["connection"])
        self.assertEqual(payload["webhook"]["provider"], "waha")
        self.assertTrue(payload["webhook"]["hmac_secret"])
        self.assertIsNone(payload["webhook"]["verify_token"])

    def test_checkout_snapshots_offer_and_creates_pending_payment(self):
        session = SimpleNamespace(
            id="cs_whatsapp_41",
            url="https://checkout.stripe.test/cs_whatsapp_41",
            customer=None,
        )
        with patch.object(
            auth_routes,
            "create_whatsapp_checkout_session",
            return_value=(session, "prod_whatsapp", "price_1999"),
        ) as create_checkout:
            response = self.client.post(
                "/api/auth/whatsapp/addon/checkout",
                headers={"Origin": "https://untrusted.example"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["url"], session.url)
        self.assertFalse(response.get_json()["reused"])
        create_checkout.assert_called_once()
        call = create_checkout.call_args.kwargs
        self.assertEqual(call["company_id"], 41)
        self.assertEqual(call["customer_email"], "admin@example.com")
        self.assertEqual(call["frontend_url"], "https://app.example.com")

        subscription = self.conn.execute(
            "SELECT * FROM company_feature_subscriptions WHERE company_id = 41"
        ).fetchone()
        self.assertEqual(subscription["status"], "checkout_pending")
        self.assertEqual(subscription["monthly_price_cents"], 1999)
        self.assertEqual(subscription["monthly_quota"], 0)
        self.assertEqual(subscription["stripe_checkout_session_id"], session.id)
        payment = self.conn.execute(
            "SELECT * FROM payments WHERE stripe_session_id = ?",
            (session.id,),
        ).fetchone()
        self.assertEqual(payment["payment_status"], "pending")
        self.assertEqual(payment["purchase_type"], "feature_addon")
        self.assertEqual(payment["feature_key"], "whatsapp")

    def test_checkout_reuses_an_open_pending_session(self):
        session = SimpleNamespace(
            id="cs_existing",
            url="https://checkout.stripe.test/cs_existing",
            customer=None,
        )
        with patch.object(
            auth_routes,
            "create_whatsapp_checkout_session",
            return_value=(session, "prod_whatsapp", "price_1999"),
        ):
            first = self.client.post("/api/auth/whatsapp/addon/checkout")
        self.assertEqual(first.status_code, 200)

        with patch.object(
            self.stripe.checkout.Session,
            "retrieve",
            return_value=SimpleNamespace(
                id="cs_existing",
                url=session.url,
                status="open",
            ),
        ), patch.object(auth_routes, "create_whatsapp_checkout_session") as create_checkout:
            second = self.client.post("/api/auth/whatsapp/addon/checkout")

        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()["reused"])
        create_checkout.assert_not_called()
        payment_count = self.conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        self.assertEqual(payment_count, 1)

    def test_checkout_recovers_an_existing_completed_payment(self):
        self._create_pending_checkout("cs_completed")
        self.stripe.checkout.Session.retrieve.return_value = SimpleNamespace(
            id="cs_completed",
            status="complete",
            payment_status="paid",
            url=None,
        )

        with patch.object(auth_routes, "create_whatsapp_checkout_session") as create_checkout:
            response = self.client.post("/api/auth/whatsapp/addon/checkout")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["reused"])
        self.assertTrue(payload["payment_completed"])
        self.assertEqual(
            payload["url"],
            "https://app.example.com/dashboard"
            "?whatsapp_addon=success&session_id=cs_completed",
        )
        create_checkout.assert_not_called()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
            1,
        )

    def test_checkout_replaces_open_session_with_a_different_redirect_origin(self):
        self._create_pending_checkout("cs_deployed")
        replacement = SimpleNamespace(
            id="cs_local",
            url="https://checkout.stripe.test/cs_local",
            customer=None,
        )
        self.stripe.checkout.Session.retrieve.return_value = SimpleNamespace(
            id="cs_deployed",
            url="https://checkout.stripe.test/cs_deployed",
            status="open",
            success_url=(
                "https://deployed.example.com/dashboard"
                "?whatsapp_addon=success&session_id={CHECKOUT_SESSION_ID}"
            ),
        )

        with patch.object(
            auth_routes,
            "create_whatsapp_checkout_session",
            return_value=(replacement, "prod_whatsapp", "price_1999"),
        ) as create_checkout:
            response = self.client.post("/api/auth/whatsapp/addon/checkout")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["reused"])
        self.assertEqual(response.get_json()["checkout_session_id"], "cs_local")
        self.assertEqual(create_checkout.call_args.kwargs["frontend_url"], "https://app.example.com")
        payments = self.conn.execute(
            "SELECT stripe_session_id, payment_status FROM payments ORDER BY id"
        ).fetchall()
        self.assertEqual(
            [(row["stripe_session_id"], row["payment_status"]) for row in payments],
            [("cs_deployed", "cancelled"), ("cs_local", "pending")],
        )

    def test_checkout_uses_configured_frontend_url_while_debugging(self):
        self.app.config.update(DEBUG=True, FRONTEND_URL="http://localhost:8080")
        session = SimpleNamespace(
            id="cs_docker_frontend",
            url="https://checkout.stripe.test/cs_docker_frontend",
            customer=None,
        )

        with patch.object(
            auth_routes,
            "create_whatsapp_checkout_session",
            return_value=(session, "prod_whatsapp", "price_1999"),
        ) as create_checkout:
            response = self.client.post("/api/auth/whatsapp/addon/checkout")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            create_checkout.call_args.kwargs["frontend_url"],
            "http://localhost:8080",
        )

    def test_checkout_rejects_an_unavailable_offer(self):
        save_whatsapp_addon_config(
            self.conn,
            monthly_price_cents=1999,
            monthly_quota=750,
            is_available=False,
        )
        self.conn.commit()

        with patch.object(auth_routes, "create_whatsapp_checkout_session") as create_checkout:
            response = self.client.post("/api/auth/whatsapp/addon/checkout")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "whatsapp_addon_unavailable")
        create_checkout.assert_not_called()

    def test_checkout_rejects_a_second_subscription_even_if_entitlement_is_stale(self):
        self._save_active_subscription()
        self.conn.execute(
            "UPDATE company_feature_entitlements SET is_enabled = 0 WHERE company_id = 41"
        )
        self.conn.commit()

        with patch.object(auth_routes, "create_whatsapp_checkout_session") as create_checkout:
            response = self.client.post("/api/auth/whatsapp/addon/checkout")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.get_json()["error"], "whatsapp_subscription_exists")
        create_checkout.assert_not_called()
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
            0,
        )

    def test_paid_checkout_return_activates_without_webhook_and_is_idempotent(self):
        self._create_pending_checkout("cs_paid")
        self.stripe.checkout.Session.retrieve.return_value = SimpleNamespace(
            id="cs_paid",
            status="complete",
            payment_status="paid",
            subscription="sub_41",
            customer="cus_41",
            created=1787097600,
            livemode=False,
            metadata=SimpleNamespace(company_id="41", feature_key="whatsapp"),
        )
        self.stripe.Subscription.retrieve.return_value = self._stripe_subscription()

        first = self.client.post(
            "/api/auth/whatsapp/addon/payment-success",
            json={"session_id": "cs_paid"},
        )
        second = self.client.post(
            "/api/auth/whatsapp/addon/payment-success",
            json={"session_id": "cs_paid"},
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(first.get_json()["entitlement"]["enabled"])
        subscription = self.conn.execute(
            "SELECT * FROM company_feature_subscriptions WHERE company_id = 41"
        ).fetchone()
        self.assertEqual(subscription["status"], "active")
        self.assertEqual(subscription["stripe_subscription_id"], "sub_41")
        payment = self.conn.execute(
            "SELECT * FROM payments WHERE stripe_session_id = 'cs_paid'"
        ).fetchone()
        self.assertEqual(payment["payment_status"], "completed")
        self.assertEqual(payment["stripe_subscription_id"], "sub_41")
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM company_feature_usage_periods"
            ).fetchone()[0],
            0,
        )

    def test_checkout_return_rejects_incomplete_payment(self):
        self._create_pending_checkout("cs_open")
        self.stripe.checkout.Session.retrieve.return_value = SimpleNamespace(
            id="cs_open",
            status="open",
            payment_status="unpaid",
        )

        response = self.client.post(
            "/api/auth/whatsapp/addon/payment-success",
            json={"session_id": "cs_open"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "payment_not_completed")
        self.stripe.Subscription.retrieve.assert_not_called()
        self.assertEqual(
            self.conn.execute(
                "SELECT payment_status FROM payments WHERE stripe_session_id = 'cs_open'"
            ).fetchone()["payment_status"],
            "pending",
        )

    def test_checkout_return_rejects_another_workspace_session(self):
        self._create_pending_checkout("cs_foreign")
        self.stripe.checkout.Session.retrieve.return_value = SimpleNamespace(
            id="cs_foreign",
            status="complete",
            payment_status="paid",
            subscription="sub_99",
            metadata={"company_id": "99", "feature_key": "whatsapp"},
        )

        response = self.client.post(
            "/api/auth/whatsapp/addon/payment-success",
            json={"session_id": "cs_foreign"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "checkout_session_mismatch")
        self.stripe.Subscription.retrieve.assert_not_called()

    def test_cancel_and_reactivate_subscription_at_period_end(self):
        self._save_active_subscription()
        self.stripe.Subscription.modify.side_effect = [
            SimpleNamespace(id="sub_41", cancel_at_period_end=True),
            SimpleNamespace(id="sub_41", cancel_at_period_end=False),
        ]

        cancel_response = self.client.post(
            "/api/auth/whatsapp/addon/subscription",
            json={"action": "cancel"},
        )

        self.assertEqual(cancel_response.status_code, 200)
        canceled = cancel_response.get_json()
        self.assertEqual(
            canceled["addon"]["subscription"]["status"],
            "cancel_at_period_end",
        )
        self.assertTrue(canceled["addon"]["can_reactivate"])
        self.assertTrue(canceled["entitlement"]["enabled"])

        reactivate_response = self.client.post(
            "/api/auth/whatsapp/addon/subscription",
            json={"action": "reactivate"},
        )

        self.assertEqual(reactivate_response.status_code, 200)
        reactivated = reactivate_response.get_json()
        self.assertEqual(reactivated["addon"]["subscription"]["status"], "active")
        self.assertTrue(reactivated["addon"]["can_cancel"])
        self.stripe.Subscription.modify.assert_any_call(
            "sub_41",
            cancel_at_period_end=True,
        )
        self.stripe.Subscription.modify.assert_any_call(
            "sub_41",
            cancel_at_period_end=False,
        )

    def test_stripe_failure_does_not_change_local_subscription(self):
        self._save_active_subscription()
        self.stripe.Subscription.modify.side_effect = RuntimeError("Stripe unavailable")

        response = self.client.post(
            "/api/auth/whatsapp/addon/subscription",
            json={"action": "cancel"},
        )

        self.assertEqual(response.status_code, 502)
        row = self.conn.execute(
            "SELECT status, cancel_at_period_end FROM company_feature_subscriptions "
            "WHERE company_id = 41"
        ).fetchone()
        self.assertEqual(row["status"], "active")
        self.assertFalse(row["cancel_at_period_end"])

    def test_whatsapp_usage_endpoint_points_to_workspace_quota(self):
        self._save_active_subscription()
        self.conn.execute(
            """
            INSERT INTO company_feature_usage_periods (
                company_id, feature_key, feature_subscription_id,
                period_start, period_end, quota_limit, units_used,
                created_at, updated_at
            ) VALUES (
                41, 'whatsapp', 1,
                '2026-08-01T00:00:00+00:00', '2026-09-01T00:00:00+00:00',
                750, 325, '2026-08-01T00:00:00+00:00',
                '2026-08-19T00:00:00+00:00'
            )
            """
        )
        self.conn.commit()

        response = self.client.get("/api/auth/whatsapp/addon/usage")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["shared_workspace_quota"])
        self.assertEqual(payload["usage_endpoint"], "/api/auth/chat-usage")

    def test_company_payment_history_labels_whatsapp_addon_charges(self):
        self.conn.execute(
            """
            INSERT INTO payments (
                workspace_id, transaction_id, payment_gateway, amount,
                currency, payment_status, payment_date, plan_name,
                purchase_type, feature_key, stripe_subscription_id,
                stripe_invoice_id
            ) VALUES (
                41, 'WA_INV_in_41', 'stripe', 19.99, 'usd', 'completed',
                '2026-08-19T12:00:00+00:00', 'WhatsApp Add-on',
                'feature_addon', 'whatsapp', 'sub_41', 'in_41'
            )
            """
        )
        self.conn.commit()

        response = self.client.get("/api/auth/payments")

        self.assertEqual(response.status_code, 200)
        payment = response.get_json()[0]
        self.assertEqual(payment["agent_name"], "WhatsApp Add-on")
        self.assertEqual(payment["plan_name"], "WhatsApp Add-on")
        self.assertEqual(payment["purchase_type"], "feature_addon")
        self.assertEqual(payment["feature_key"], "whatsapp")
        self.assertEqual(payment["stripe_invoice_id"], "in_41")

    def _save_active_subscription(self):
        save_company_whatsapp_subscription(
            self.conn,
            41,
            status="active",
            monthly_price_cents=1999,
            monthly_quota=750,
            stripe_customer_id="cus_41",
            stripe_subscription_id="sub_41",
            stripe_subscription_item_id="si_41",
            stripe_price_id="price_1999",
            current_period_start="2026-08-01T00:00:00+00:00",
            current_period_end="2026-09-01T00:00:00+00:00",
        )
        self.conn.execute(
            "UPDATE company_feature_entitlements SET is_enabled = 1 WHERE company_id = 41"
        )
        self.conn.commit()

    def _create_pending_checkout(self, session_id):
        session = SimpleNamespace(
            id=session_id,
            url=f"https://checkout.stripe.test/{session_id}",
            customer=None,
        )
        with patch.object(
            auth_routes,
            "create_whatsapp_checkout_session",
            return_value=(session, "prod_whatsapp", "price_1999"),
        ):
            response = self.client.post("/api/auth/whatsapp/addon/checkout")
        self.assertEqual(response.status_code, 200)

    @staticmethod
    def _stripe_subscription():
        return SimpleNamespace(
            id="sub_41",
            customer="cus_41",
            status="active",
            cancel_at_period_end=False,
            canceled_at=None,
            current_period_start=1785542400,
            current_period_end=1788220800,
            metadata={"company_id": "41", "feature_key": "whatsapp"},
            items=SimpleNamespace(
                data=[
                    SimpleNamespace(
                        id="si_41",
                        price=SimpleNamespace(
                            id="price_1999",
                            unit_amount=1999,
                            currency="usd",
                        ),
                    )
                ]
            ),
        )


if __name__ == "__main__":
    unittest.main()
