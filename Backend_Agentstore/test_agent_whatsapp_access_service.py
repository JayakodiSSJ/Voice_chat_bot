import sqlite3
import unittest

from agent_whatsapp_access_service import (
    resolve_agent_whatsapp_enabled,
    validate_agent_whatsapp_access,
)


class AgentWhatsAppAccessServiceTests(unittest.TestCase):
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
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_resolves_explicit_and_nested_boolean_values(self):
        self.assertTrue(resolve_agent_whatsapp_enabled({"whatsapp_enabled": True}))
        self.assertFalse(resolve_agent_whatsapp_enabled({"whatsappEnabled": False}, default=True))
        self.assertTrue(
            resolve_agent_whatsapp_enabled(
                {"configuration": {"whatsapp_enabled": True}}
            )
        )
        self.assertTrue(
            resolve_agent_whatsapp_enabled(
                {"configuration": '{"whatsapp_enabled": true}'}
            )
        )
        self.assertTrue(resolve_agent_whatsapp_enabled({}, default=True))

    def test_rejects_non_boolean_values(self):
        for value in (1, "true", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    resolve_agent_whatsapp_enabled({"whatsapp_enabled": value})

    def test_requires_entitlement_and_configured_number(self):
        valid, code, _ = validate_agent_whatsapp_access(self.conn, 7, True)
        self.assertFalse(valid)
        self.assertEqual(code, "whatsapp_not_enabled")

        self.conn.execute(
            """
            INSERT INTO company_feature_entitlements
                (company_id, feature_key, is_enabled, updated_at)
            VALUES (7, 'whatsapp', 1, '2026-08-12T00:00:00+00:00')
            """
        )
        valid, code, _ = validate_agent_whatsapp_access(self.conn, 7, True)
        self.assertFalse(valid)
        self.assertEqual(code, "whatsapp_not_configured")

        self.conn.execute(
            """
            INSERT INTO company_whatsapp_connections
                (company_id, phone_number, created_at, updated_at)
            VALUES (7, '+94771234567', '2026-08-12T00:00:00+00:00', '2026-08-12T00:00:00+00:00')
            """
        )
        self.assertEqual(
            validate_agent_whatsapp_access(self.conn, 7, True),
            (True, None, None),
        )

    def test_disabled_access_needs_no_workspace_configuration(self):
        self.assertEqual(
            validate_agent_whatsapp_access(self.conn, 7, False),
            (True, None, None),
        )


if __name__ == "__main__":
    unittest.main()
