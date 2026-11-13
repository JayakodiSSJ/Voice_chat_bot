import hashlib
import hmac
import json
import sqlite3
import unittest
from unittest.mock import patch

from flask import Flask

import auth_routes


class WhatsAppWebhookRouteTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE company_feature_entitlements (
                company_id INTEGER NOT NULL,
                feature_key TEXT NOT NULL,
                is_enabled INTEGER NOT NULL,
                enabled_at TEXT,
                disabled_at TEXT,
                updated_at TEXT NOT NULL,
                updated_by_user_id INTEGER,
                PRIMARY KEY (company_id, feature_key)
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
            CREATE TABLE whatsapp_inbound_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                meta_message_id TEXT NOT NULL,
                sender_phone TEXT,
                recipient_phone_number_id TEXT,
                message_type TEXT NOT NULL,
                message_text TEXT,
                payload_json TEXT NOT NULL,
                received_at TEXT NOT NULL,
                UNIQUE(company_id, meta_message_id)
            );
            CREATE TABLE custom_agents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                configuration TEXT NOT NULL,
                provisioning_status TEXT NOT NULL
            );
            CREATE TABLE whatsapp_conversations (
                company_id INTEGER NOT NULL,
                sender_phone TEXT NOT NULL,
                selected_agent_id INTEGER,
                state TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (company_id, sender_phone)
            );
            CREATE TABLE whatsapp_message_dispatches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                meta_message_id TEXT NOT NULL,
                sender_phone TEXT NOT NULL,
                action TEXT NOT NULL,
                selected_agent_id INTEGER,
                response_text TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(company_id, meta_message_id)
            );
            CREATE TABLE whatsapp_outbound_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                dispatch_id INTEGER NOT NULL,
                recipient_phone TEXT NOT NULL,
                sequence_number INTEGER NOT NULL,
                message_text TEXT NOT NULL,
                send_status TEXT NOT NULL DEFAULT 'pending',
                meta_status TEXT,
                meta_message_id TEXT UNIQUE,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                created_at TEXT NOT NULL,
                sent_at TEXT,
                status_updated_at TEXT,
                UNIQUE(dispatch_id, sequence_number)
            );
            INSERT INTO company_feature_entitlements
                (company_id, feature_key, is_enabled, updated_at)
            VALUES (7, 'whatsapp', 1, '2026-08-19T00:00:00+00:00');
            """
        )

        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            AUTH_SECRET_KEY="test-auth-secret",
            SECRET_KEY="test-auth-secret",
            PUBLIC_BACKEND_URL="https://api.example.com",
        )
        verify_token_secret = auth_routes._encrypt_val(self.app, "verify-token")
        meta_app_secret = auth_routes._encrypt_val(self.app, "meta-app-secret")
        self.conn.execute(
            """
            INSERT INTO company_whatsapp_connections (
                company_id,
                phone_number,
                webhook_key,
                verification_token_secret,
                meta_app_secret,
                created_at,
                updated_at
            )
            VALUES (7, '+94771234567', 'tenant-key', ?, ?, ?, ?)
            """,
            (
                verify_token_secret,
                meta_app_secret,
                "2026-08-19T00:00:00+00:00",
                "2026-08-19T00:00:00+00:00",
            ),
        )
        self.conn.commit()

        self.init_patch = patch.object(auth_routes, "init_db")
        self.cleanup_patch = patch.object(auth_routes, "_mark_stale_running_ingestions")
        self.connection_patch = patch.object(auth_routes, "get_conn", return_value=self.conn)
        self.init_patch.start()
        self.cleanup_patch.start()
        self.connection_patch.start()
        auth_routes.register_auth_routes(self.app, mail=None)
        self.client = self.app.test_client()

    def tearDown(self):
        self.connection_patch.stop()
        self.cleanup_patch.stop()
        self.init_patch.stop()
        self.conn.close()

    def test_echoes_challenge_for_matching_verification_token(self):
        response = self.client.get(
            "/api/whatsapp/webhook/tenant-key",
            query_string={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-token",
                "hub.challenge": "challenge-value",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "challenge-value")
        verified_at = self.conn.execute(
            "SELECT webhook_verified_at FROM company_whatsapp_connections WHERE company_id = 7"
        ).fetchone()[0]
        self.assertIsNotNone(verified_at)

    def test_rejects_incorrect_verification_token(self):
        response = self.client.get(
            "/api/whatsapp/webhook/tenant-key",
            query_string={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "challenge-value",
            },
        )

        self.assertEqual(response.status_code, 403)

    def test_accepts_signed_message_and_deduplicates_retry(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "12345"},
                                "messages": [
                                    {
                                        "id": "wamid.route-test",
                                        "from": "94770000000",
                                        "type": "text",
                                        "text": {"body": "Hello"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }
        raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        digest = hmac.new(
            b"meta-app-secret",
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        headers = {"X-Hub-Signature-256": f"sha256={digest}"}

        first = self.client.post(
            "/api/whatsapp/webhook/tenant-key",
            data=raw_body,
            content_type="application/json",
            headers=headers,
        )
        second = self.client.post(
            "/api/whatsapp/webhook/tenant-key",
            data=raw_body,
            content_type="application/json",
            headers=headers,
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json()["stored"], 1)
        self.assertEqual(first.get_json()["processed"], 1)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.get_json()["duplicates"], 1)
        self.assertEqual(second.get_json()["processed"], 0)
        dispatch_count = self.conn.execute(
            "SELECT COUNT(*) FROM whatsapp_message_dispatches"
        ).fetchone()[0]
        self.assertEqual(dispatch_count, 1)

    def test_rejects_invalid_post_signature(self):
        response = self.client.post(
            "/api/whatsapp/webhook/tenant-key",
            data=b"{}",
            content_type="application/json",
            headers={"X-Hub-Signature-256": "sha256=" + ("0" * 64)},
        )

        self.assertEqual(response.status_code, 403)
        count = self.conn.execute(
            "SELECT COUNT(*) FROM whatsapp_inbound_messages"
        ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_ignores_messages_for_a_different_configured_phone_number_id(self):
        self.conn.execute(
            """
            UPDATE company_whatsapp_connections
            SET meta_phone_number_id = '99999'
            WHERE company_id = 7
            """
        )
        self.conn.commit()
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "metadata": {"phone_number_id": "12345"},
                                "messages": [
                                    {
                                        "id": "wamid.wrong-recipient",
                                        "from": "94770000000",
                                        "type": "text",
                                        "text": {"body": "Hello"},
                                    }
                                ],
                            },
                        }
                    ]
                }
            ],
        }
        raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        digest = hmac.new(b"meta-app-secret", raw_body, hashlib.sha256).hexdigest()

        response = self.client.post(
            "/api/whatsapp/webhook/tenant-key",
            data=raw_body,
            content_type="application/json",
            headers={"X-Hub-Signature-256": f"sha256={digest}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["ignored_recipient"], 1)
        self.assertEqual(response.get_json()["stored"], 0)

    def test_applies_signed_meta_delivery_status_updates(self):
        self.conn.execute(
            """
            INSERT INTO whatsapp_outbound_messages (
                company_id, dispatch_id, recipient_phone, sequence_number,
                message_text, meta_message_id, created_at
            )
            VALUES (7, 1, '94770000000', 1, 'Hello', 'wamid.outbound',
                    '2026-08-19T00:00:00+00:00')
            """
        )
        self.conn.commit()
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "statuses": [
                                    {"id": "wamid.outbound", "status": "delivered"}
                                ]
                            },
                        }
                    ]
                }
            ],
        }
        raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        digest = hmac.new(b"meta-app-secret", raw_body, hashlib.sha256).hexdigest()

        response = self.client.post(
            "/api/whatsapp/webhook/tenant-key",
            data=raw_body,
            content_type="application/json",
            headers={"X-Hub-Signature-256": f"sha256={digest}"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status_updates"], 1)
        meta_status = self.conn.execute(
            "SELECT meta_status FROM whatsapp_outbound_messages WHERE meta_message_id = 'wamid.outbound'"
        ).fetchone()[0]
        self.assertEqual(meta_status, "delivered")

    def test_accepts_signed_waha_message_and_activates_webhook(self):
        self.conn.execute(
            """
            UPDATE company_whatsapp_connections
            SET provider = 'waha', waha_session = 'workspace-7'
            WHERE company_id = 7
            """
        )
        self.conn.commit()
        payload = {
            "event": "message",
            "session": "workspace-7",
            "payload": {
                "id": "false_94770000000@c.us_IN",
                "from": "94770000000@c.us",
                "fromMe": False,
                "body": "Hello",
                "hasMedia": False,
            },
        }
        raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        digest = hmac.new(b"verify-token", raw_body, hashlib.sha512).hexdigest()

        response = self.client.post(
            "/api/whatsapp/webhook/tenant-key",
            data=raw_body,
            content_type="application/json",
            headers={
                "X-Webhook-Hmac": digest,
                "X-Webhook-Hmac-Algorithm": "sha512",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["stored"], 1)
        self.assertEqual(response.get_json()["processed"], 1)
        verified_at = self.conn.execute(
            "SELECT webhook_verified_at FROM company_whatsapp_connections WHERE company_id = 7"
        ).fetchone()[0]
        self.assertIsNotNone(verified_at)

    def test_rejects_waha_event_from_another_session(self):
        self.conn.execute(
            """
            UPDATE company_whatsapp_connections
            SET provider = 'waha', waha_session = 'workspace-7'
            WHERE company_id = 7
            """
        )
        self.conn.commit()
        payload = {
            "event": "message",
            "session": "workspace-8",
            "payload": {
                "id": "wrong-session",
                "from": "94770000000@c.us",
                "fromMe": False,
                "body": "Hello",
            },
        }
        raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        digest = hmac.new(b"verify-token", raw_body, hashlib.sha512).hexdigest()

        response = self.client.post(
            "/api/whatsapp/webhook/tenant-key",
            data=raw_body,
            content_type="application/json",
            headers={"X-Webhook-Hmac": digest, "X-Webhook-Hmac-Algorithm": "sha512"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["error"], "invalid_waha_session")


if __name__ == "__main__":
    unittest.main()
