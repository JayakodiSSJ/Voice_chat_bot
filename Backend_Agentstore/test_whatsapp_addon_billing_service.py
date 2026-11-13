from types import SimpleNamespace
import sqlite3
import unittest
from unittest.mock import Mock

from whatsapp_addon_billing_service import (
    create_whatsapp_checkout_session,
    ensure_whatsapp_stripe_catalog,
    reconcile_whatsapp_subscription_prices,
    set_whatsapp_subscription_cancel_at_period_end,
)


class WhatsAppAddonBillingServiceTests(unittest.TestCase):
    def setUp(self):
        self.stripe = SimpleNamespace(
            Product=SimpleNamespace(create=Mock(), retrieve=Mock()),
            Price=SimpleNamespace(create=Mock(), retrieve=Mock()),
            Subscription=SimpleNamespace(modify=Mock()),
            SubscriptionItem=SimpleNamespace(modify=Mock()),
            checkout=SimpleNamespace(Session=SimpleNamespace(create=Mock())),
        )
        self.config = {
            "display_name": "WhatsApp Add-on",
            "monthly_price_cents": 1999,
            "currency": "usd",
            "stripe_product_id": None,
            "stripe_price_id": None,
        }

    def test_creates_reusable_monthly_product_and_price(self):
        self.stripe.Product.create.return_value = SimpleNamespace(id="prod_whatsapp")
        self.stripe.Price.create.return_value = SimpleNamespace(id="price_1999")

        product_id, price_id = ensure_whatsapp_stripe_catalog(
            self.stripe,
            self.config,
        )

        self.assertEqual(product_id, "prod_whatsapp")
        self.assertEqual(price_id, "price_1999")
        self.stripe.Product.create.assert_called_once_with(
            name="WhatsApp Add-on",
            metadata={"feature_key": "whatsapp"},
            idempotency_key="agent-store-whatsapp-addon-product-v1",
        )
        self.stripe.Price.create.assert_called_once_with(
            product="prod_whatsapp",
            currency="usd",
            unit_amount=1999,
            recurring={"interval": "month"},
            metadata={"feature_key": "whatsapp"},
            idempotency_key="agent-store-whatsapp-addon-price-usd-1999-v1",
        )

    def test_reuses_a_matching_configured_price(self):
        self.config.update(
            stripe_product_id="prod_whatsapp",
            stripe_price_id="price_1999",
        )
        self.stripe.Price.retrieve.return_value = SimpleNamespace(
            id="price_1999",
            product="prod_whatsapp",
            unit_amount=1999,
            currency="usd",
            recurring={"interval": "month"},
            active=True,
        )

        product_id, price_id = ensure_whatsapp_stripe_catalog(
            self.stripe,
            self.config,
        )

        self.assertEqual((product_id, price_id), ("prod_whatsapp", "price_1999"))
        self.stripe.Product.create.assert_not_called()
        self.stripe.Price.create.assert_not_called()

    def test_checkout_contains_tenant_metadata_and_recurring_price(self):
        self.stripe.Product.create.return_value = SimpleNamespace(id="prod_whatsapp")
        self.stripe.Price.create.return_value = SimpleNamespace(id="price_1999")
        self.stripe.checkout.Session.create.return_value = SimpleNamespace(
            id="cs_whatsapp",
            url="https://checkout.stripe.test/session",
        )

        session, product_id, price_id = create_whatsapp_checkout_session(
            self.stripe,
            config=self.config,
            company_id=41,
            customer_email="admin@example.com",
            customer_id=None,
            frontend_url="https://app.example.com/",
        )

        self.assertEqual(session.id, "cs_whatsapp")
        self.assertEqual((product_id, price_id), ("prod_whatsapp", "price_1999"))
        call = self.stripe.checkout.Session.create.call_args.kwargs
        self.assertEqual(call["mode"], "subscription")
        self.assertEqual(call["line_items"], [{"price": "price_1999", "quantity": 1}])
        self.assertEqual(call["client_reference_id"], "41")
        self.assertEqual(call["customer_email"], "admin@example.com")
        self.assertEqual(
            call["metadata"],
            {"company_id": "41", "feature_key": "whatsapp"},
        )
        self.assertIn("whatsapp_addon=success", call["success_url"])
        self.assertIn("session_id={CHECKOUT_SESSION_ID}", call["success_url"])
        self.assertIn("whatsapp_addon=cancelled", call["cancel_url"])

    def test_reconciles_active_subscription_price_without_proration(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE company_feature_subscriptions (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL,
                feature_key TEXT NOT NULL,
                status TEXT NOT NULL,
                billing_source TEXT NOT NULL DEFAULT 'stripe',
                stripe_subscription_item_id TEXT,
                stripe_price_id TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO company_feature_subscriptions
                (id, company_id, feature_key, status,
                 stripe_subscription_item_id, stripe_price_id, updated_at)
            VALUES (1, 41, 'whatsapp', 'active', 'si_41', 'price_old', 'old')
            """
        )
        self.stripe.Product.create.return_value = SimpleNamespace(id="prod_whatsapp")
        self.stripe.Price.create.return_value = SimpleNamespace(id="price_1999")

        result = reconcile_whatsapp_subscription_prices(
            conn,
            self.stripe,
            self.config,
        )

        self.stripe.SubscriptionItem.modify.assert_called_once_with(
            "si_41",
            price="price_1999",
            proration_behavior="none",
        )
        self.assertEqual(result["updated_company_ids"], [41])
        self.assertEqual(result["failures"], [])
        row = conn.execute(
            "SELECT stripe_price_id FROM company_feature_subscriptions WHERE id = 1"
        ).fetchone()
        self.assertEqual(row["stripe_price_id"], "price_1999")
        conn.close()

    def test_repricing_excludes_legacy_and_inactive_subscriptions(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE company_feature_subscriptions (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL,
                feature_key TEXT NOT NULL,
                status TEXT NOT NULL,
                billing_source TEXT NOT NULL DEFAULT 'stripe',
                stripe_subscription_item_id TEXT,
                stripe_price_id TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO company_feature_subscriptions
                (id, company_id, feature_key, status, billing_source,
                 stripe_subscription_item_id, stripe_price_id, updated_at)
            VALUES (?, ?, 'whatsapp', ?, ?, ?, 'price_old', 'old')
            """,
            [
                (1, 41, "active", "legacy", None),
                (2, 42, "canceled", "stripe", "si_42"),
            ],
        )
        self.stripe.Product.create.return_value = SimpleNamespace(id="prod_whatsapp")
        self.stripe.Price.create.return_value = SimpleNamespace(id="price_1999")

        result = reconcile_whatsapp_subscription_prices(
            conn,
            self.stripe,
            self.config,
        )

        self.stripe.SubscriptionItem.modify.assert_not_called()
        self.assertEqual(result["updated_company_ids"], [])
        self.assertEqual(result["failures"], [])
        prices = conn.execute(
            "SELECT stripe_price_id FROM company_feature_subscriptions ORDER BY id"
        ).fetchall()
        self.assertEqual([row["stripe_price_id"] for row in prices], ["price_old", "price_old"])
        conn.close()

    def test_updates_stripe_subscription_renewal_state(self):
        self.stripe.Subscription.modify.return_value = SimpleNamespace(
            id="sub_41",
            cancel_at_period_end=True,
        )

        subscription = set_whatsapp_subscription_cancel_at_period_end(
            self.stripe,
            subscription_id="sub_41",
            cancel_at_period_end=True,
        )

        self.assertEqual(subscription.id, "sub_41")
        self.stripe.Subscription.modify.assert_called_once_with(
            "sub_41",
            cancel_at_period_end=True,
        )


if __name__ == "__main__":
    unittest.main()
