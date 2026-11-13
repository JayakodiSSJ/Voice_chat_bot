import hashlib
import hmac
import sqlite3
import unittest

from whatsapp_webhook_service import (
    apply_outbound_status_updates,
    extract_inbound_messages,
    extract_status_updates,
    extract_waha_inbound_messages,
    extract_waha_status_updates,
    store_inbound_messages,
    verify_meta_signature,
    verify_waha_signature,
)


class WhatsAppWebhookServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
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
            CREATE TABLE whatsapp_outbound_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                meta_message_id TEXT UNIQUE,
                meta_status TEXT,
                last_error TEXT,
                status_updated_at TEXT
            );
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_verifies_sha256_meta_signature(self):
        body = b'{"object":"whatsapp_business_account"}'
        digest = hmac.new(b"app-secret", body, hashlib.sha256).hexdigest()

        self.assertTrue(
            verify_meta_signature(body, f"sha256={digest}", "app-secret")
        )
        self.assertFalse(
            verify_meta_signature(body, "sha256=" + ("0" * 64), "app-secret")
        )
        self.assertFalse(verify_meta_signature(body, "", "app-secret"))

    def test_extracts_text_messages_and_ignores_status_updates(self):
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
                                        "id": "wamid.abc",
                                        "from": "94770000000",
                                        "type": "text",
                                        "text": {"body": "Hello"},
                                    }
                                ],
                            },
                        },
                        {
                            "field": "messages",
                            "value": {"statuses": [{"id": "wamid.sent"}]},
                        },
                    ]
                }
            ],
        }

        messages = extract_inbound_messages(payload)

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["meta_message_id"], "wamid.abc")
        self.assertEqual(messages[0]["sender_phone"], "94770000000")
        self.assertEqual(messages[0]["recipient_phone_number_id"], "12345")
        self.assertEqual(messages[0]["message_text"], "Hello")

    def test_deduplicates_retried_message_notifications(self):
        message = {
            "meta_message_id": "wamid.abc",
            "sender_phone": "94770000000",
            "recipient_phone_number_id": "12345",
            "message_type": "text",
            "message_text": "Hello",
            "payload": {"id": "wamid.abc"},
        }

        first = store_inbound_messages(self.conn, 7, [message])
        second = store_inbound_messages(self.conn, 7, [message])

        self.assertEqual(first, {"stored": 1, "duplicates": 0})
        self.assertEqual(second, {"stored": 0, "duplicates": 1})
        count = self.conn.execute(
            "SELECT COUNT(*) FROM whatsapp_inbound_messages"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_extracts_and_applies_meta_delivery_statuses_within_the_tenant(self):
        self.conn.execute(
            """
            INSERT INTO whatsapp_outbound_messages
                (company_id, meta_message_id, meta_status)
            VALUES (7, 'wamid.outbound', 'accepted')
            """
        )
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "statuses": [
                                    {
                                        "id": "wamid.outbound",
                                        "status": "delivered",
                                    }
                                ]
                            },
                        }
                    ]
                }
            ],
        }

        statuses = extract_status_updates(payload)
        result = apply_outbound_status_updates(self.conn, 7, statuses)

        self.assertEqual(statuses[0]["status"], "delivered")
        self.assertEqual(result, {"updated": 1, "unmatched": 0})
        status = self.conn.execute(
            "SELECT meta_status FROM whatsapp_outbound_messages WHERE company_id = 7"
        ).fetchone()[0]
        self.assertEqual(status, "delivered")

    def test_verifies_waha_sha512_signature(self):
        body = b'{"event":"message","session":"workspace-7"}'
        digest = hmac.new(b"waha-secret", body, hashlib.sha512).hexdigest()

        self.assertTrue(
            verify_waha_signature(body, digest, "sha512", "waha-secret")
        )
        self.assertTrue(
            verify_waha_signature(body, f"sha512={digest}", "sha512", "waha-secret")
        )
        self.assertFalse(
            verify_waha_signature(body, digest, "sha256", "waha-secret")
        )

    def test_extracts_only_inbound_waha_messages_for_the_expected_session(self):
        payload = {
            "event": "message",
            "session": "workspace-7",
            "payload": {
                "id": "false_94770000000@c.us_ABC",
                "from": "94770000000@c.us",
                "fromMe": False,
                "body": "Hello",
                "hasMedia": False,
            },
        }

        messages = extract_waha_inbound_messages(
            payload,
            expected_session="workspace-7",
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["sender_phone"], "94770000000@c.us")
        self.assertEqual(messages[0]["message_text"], "Hello")
        self.assertEqual(
            extract_waha_inbound_messages(payload, expected_session="another-session"),
            [],
        )
        payload["payload"]["fromMe"] = True
        self.assertEqual(
            extract_waha_inbound_messages(payload, expected_session="workspace-7"),
            [],
        )

    def test_maps_waha_ack_status(self):
        updates = extract_waha_status_updates(
            {
                "event": "message.ack",
                "session": "workspace-7",
                "payload": {"id": "waha.outbound", "ack": 3, "ackName": "READ"},
            },
            expected_session="workspace-7",
        )

        self.assertEqual(
            updates,
            [{"meta_message_id": "waha.outbound", "status": "read", "error": None}],
        )


if __name__ == "__main__":
    unittest.main()
