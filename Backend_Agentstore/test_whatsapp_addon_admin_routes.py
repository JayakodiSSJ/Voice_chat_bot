from functools import wraps
import sqlite3
import unittest
from unittest.mock import patch

from flask import Flask, request

import auth_routes


def _test_auth(_app):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            request.auth = {
                "user_id": 9,
                "company_id": 1,
                "email": "admin@intranet.slt.com.lk",
                "role": "admin",
                "is_super_admin": True,
            }
            return fn(*args, **kwargs)

        return wrapper

    return decorator


class WhatsAppAddonAdminRouteTests(unittest.TestCase):
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
                billing_source TEXT NOT NULL DEFAULT 'stripe',
                stripe_subscription_item_id TEXT,
                stripe_price_id TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )

        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.patches = [
            patch.object(auth_routes, "init_db"),
            patch.object(auth_routes, "_mark_stale_running_ingestions"),
            patch.object(auth_routes, "get_conn", return_value=self.conn),
            patch.object(auth_routes, "require_auth", new=_test_auth),
            patch.object(auth_routes, "_is_super_admin", return_value=True),
            patch.object(
                auth_routes,
                "migrate_legacy_whatsapp_entitlements",
                return_value={"subscriptions_created": 0, "usage_periods_created": 0},
            ),
        ]
        for active_patch in self.patches:
            active_patch.start()

        auth_routes.register_auth_routes(self.app, mail=None)
        self.client = self.app.test_client()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.conn.close()

    def test_get_returns_seeded_global_config(self):
        response = self.client.get("/api/auth/admin/features/whatsapp/addon")

        self.assertEqual(response.status_code, 200)
        config = response.get_json()["config"]
        self.assertEqual(config["feature"], "whatsapp")
        self.assertEqual(config["monthly_price_cents"], 0)
        self.assertNotIn("monthly_quota", config)
        self.assertFalse(config["available"])

    def test_put_updates_price_and_availability(self):
        response = self.client.put(
            "/api/auth/admin/features/whatsapp/addon",
            json={
                "monthly_price_cents": 1999,
                "available": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        config = response.get_json()["config"]
        self.assertEqual(config["monthly_price_cents"], 1999)
        self.assertEqual(config["monthly_price_usd"], 19.99)
        self.assertNotIn("monthly_quota", config)
        self.assertTrue(config["available"])
        self.assertEqual(config["updated_by_user_id"], 9)

    def test_first_price_handles_active_subscription_without_catalog_price(self):
        self.conn.execute(
            """
            INSERT INTO company_feature_subscriptions (
                company_id, feature_key, status, billing_source,
                stripe_subscription_item_id, stripe_price_id, updated_at
            ) VALUES (41, 'whatsapp', 'active', 'stripe', 'si_41', NULL, 'old')
            """
        )
        self.conn.commit()

        with patch.dict("os.environ", {"STRIPE_SECRET_KEY": ""}):
            response = self.client.put(
                "/api/auth/admin/features/whatsapp/addon",
                json={
                    "monthly_price_cents": 1000,
                    "available": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["config"]["monthly_price_cents"], 1000)
        self.assertNotIn("monthly_quota", payload["config"])
        self.assertEqual(payload["price_sync"]["status"], "pending")
        self.assertEqual(payload["price_sync"]["failed_companies"], 1)

    def test_put_rejects_invalid_available_configuration(self):
        response = self.client.put(
            "/api/auth/admin/features/whatsapp/addon",
            json={
                "monthly_price_cents": 0,
                "available": True,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_payload")

    def test_manual_company_entitlement_updates_are_retired(self):
        response = self.client.put(
            "/api/auth/admin/companies/41/features/whatsapp",
            json={"enabled": True},
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.get_json()["error"],
            "whatsapp_entitlement_billing_managed",
        )

    def test_routes_require_super_admin(self):
        auth_routes._is_super_admin.return_value = False

        get_response = self.client.get("/api/auth/admin/features/whatsapp/addon")
        put_response = self.client.put(
            "/api/auth/admin/features/whatsapp/addon",
            json={
                "monthly_price_cents": 1999,
                "available": True,
            },
        )

        self.assertEqual(get_response.status_code, 403)
        self.assertEqual(put_response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
