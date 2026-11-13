import os
import sqlite3
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from flask import Flask

import auth_routes


class StripeWebhookRouteTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
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
        self.event = {
            "id": "evt_whatsapp_1",
            "type": "checkout.session.completed",
            "created": 1785542400,
            "livemode": False,
            "data": {"object": {"id": "cs_41"}},
        }
        self.stripe = SimpleNamespace(
            api_key=None,
            Webhook=SimpleNamespace(construct_event=Mock(return_value=self.event)),
        )
        self.stripe_patch = patch.dict(sys.modules, {"stripe": self.stripe})
        self.env_patch = patch.dict(
            os.environ,
            {
                "STRIPE_SECRET_KEY": "sk_test",
                "STRIPE_WEBHOOK_SECRET": "whsec_test",
            },
        )
        self.init_patch = patch.object(auth_routes, "init_db")
        self.cleanup_patch = patch.object(auth_routes, "_mark_stale_running_ingestions")
        self.connection_patch = patch.object(
            auth_routes,
            "get_conn",
            return_value=self.conn,
        )
        for active_patch in (
            self.stripe_patch,
            self.env_patch,
            self.init_patch,
            self.cleanup_patch,
            self.connection_patch,
        ):
            active_patch.start()

        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        auth_routes.register_auth_routes(self.app, mail=None)
        self.client = self.app.test_client()

    def tearDown(self):
        for active_patch in (
            self.connection_patch,
            self.cleanup_patch,
            self.init_patch,
            self.env_patch,
            self.stripe_patch,
        ):
            active_patch.stop()
        self.conn.close()

    def test_verifies_processes_and_deduplicates_event(self):
        with patch.object(
            auth_routes,
            "process_whatsapp_stripe_event",
            return_value=True,
        ) as processor:
            first = self._post_event()
            second = self._post_event()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json()["status"], "processed")
        self.assertTrue(first.get_json()["handled"])
        self.assertFalse(first.get_json()["duplicate"])
        self.assertEqual(second.status_code, 200)
        self.assertTrue(second.get_json()["duplicate"])
        processor.assert_called_once()
        self.stripe.Webhook.construct_event.assert_called_with(
            b'{"test":true}',
            "test-signature",
            "whsec_test",
        )
        row = self.conn.execute(
            "SELECT * FROM stripe_webhook_events WHERE event_id = ?",
            (self.event["id"],),
        ).fetchone()
        self.assertEqual(row["processing_status"], "completed")
        self.assertEqual(row["attempt_count"], 1)

    def test_failed_processing_is_recorded_and_retried(self):
        with patch.object(
            auth_routes,
            "process_whatsapp_stripe_event",
            side_effect=[RuntimeError("temporary database failure"), True],
        ) as processor:
            first = self._post_event()
            failed = self.conn.execute(
                "SELECT * FROM stripe_webhook_events WHERE event_id = ?",
                (self.event["id"],),
            ).fetchone()
            self.assertEqual(failed["processing_status"], "failed")
            self.assertIn("temporary database failure", failed["last_error"])

            second = self._post_event()

        self.assertEqual(first.status_code, 500)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(processor.call_count, 2)
        completed = self.conn.execute(
            "SELECT * FROM stripe_webhook_events WHERE event_id = ?",
            (self.event["id"],),
        ).fetchone()
        self.assertEqual(completed["processing_status"], "completed")
        self.assertEqual(completed["attempt_count"], 2)
        self.assertIsNone(completed["last_error"])

    def test_rejects_invalid_signature_without_storing_event(self):
        self.stripe.Webhook.construct_event.side_effect = Exception("bad signature")

        response = self._post_event()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "invalid_stripe_signature")
        count = self.conn.execute("SELECT COUNT(*) FROM stripe_webhook_events").fetchone()[0]
        self.assertEqual(count, 0)

    def _post_event(self):
        return self.client.post(
            "/api/stripe/webhook",
            data=b'{"test":true}',
            content_type="application/json",
            headers={"Stripe-Signature": "test-signature"},
        )


if __name__ == "__main__":
    unittest.main()
