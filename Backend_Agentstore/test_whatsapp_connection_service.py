import sqlite3
import unittest

from whatsapp_connection_service import (
    delete_whatsapp_connection,
    ensure_whatsapp_webhook_identity,
    get_whatsapp_connection,
    normalize_whatsapp_phone_number,
    save_whatsapp_connection,
    set_whatsapp_cloud_credentials,
    set_whatsapp_waha_credentials,
)


class WhatsAppConnectionServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE company_whatsapp_connections (
                company_id INTEGER PRIMARY KEY,
                phone_number TEXT NOT NULL UNIQUE,
                provider TEXT NOT NULL DEFAULT 'meta',
                webhook_key TEXT UNIQUE,
                verification_token_secret TEXT,
                meta_app_secret TEXT,
                meta_phone_number_id TEXT,
                meta_access_token_secret TEXT,
                waha_session TEXT UNIQUE,
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

    def test_missing_connection_is_not_configured(self):
        connection = get_whatsapp_connection(self.conn, 7)

        self.assertFalse(connection["configured"])
        self.assertIsNone(connection["phone_number"])

    def test_saves_one_normalized_number_per_company(self):
        connection = save_whatsapp_connection(
            self.conn,
            7,
            "+94 77-123-4567",
            updated_by_user_id=3,
        )

        self.assertTrue(connection["configured"])
        self.assertEqual(connection["phone_number"], "+94771234567")
        self.assertEqual(connection["updated_by_user_id"], 3)

        updated = save_whatsapp_connection(
            self.conn,
            7,
            "+94761234567",
            updated_by_user_id=4,
        )
        count = self.conn.execute(
            "SELECT COUNT(*) FROM company_whatsapp_connections WHERE company_id = 7"
        ).fetchone()[0]

        self.assertEqual(count, 1)
        self.assertEqual(updated["phone_number"], "+94761234567")
        self.assertEqual(updated["updated_by_user_id"], 4)

    def test_rejects_a_number_assigned_to_another_company(self):
        save_whatsapp_connection(self.conn, 7, "+94771234567")

        with self.assertRaises(sqlite3.IntegrityError):
            save_whatsapp_connection(self.conn, 8, "+94771234567")

    def test_validates_e164_numbers(self):
        self.assertEqual(
            normalize_whatsapp_phone_number("+1 (415) 555-2671"),
            "+14155552671",
        )
        for invalid in ("", "0771234567", "+012345678", "+123"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    normalize_whatsapp_phone_number(invalid)

    def test_deletes_a_connection(self):
        save_whatsapp_connection(self.conn, 7, "+94771234567")

        self.assertTrue(delete_whatsapp_connection(self.conn, 7))
        self.assertFalse(get_whatsapp_connection(self.conn, 7)["configured"])
        self.assertFalse(delete_whatsapp_connection(self.conn, 7))

    def test_generates_and_reuses_webhook_identity(self):
        save_whatsapp_connection(self.conn, 7, "+94771234567")
        encrypt = lambda value: f"encrypted:{value}"
        decrypt = lambda value: value.split(":", 1)[1] if value else None

        first = ensure_whatsapp_webhook_identity(
            self.conn,
            7,
            encrypt_secret=encrypt,
            decrypt_secret=decrypt,
        )
        second = ensure_whatsapp_webhook_identity(
            self.conn,
            7,
            encrypt_secret=encrypt,
            decrypt_secret=decrypt,
        )

        self.assertEqual(first["webhook_key"], second["webhook_key"])
        self.assertEqual(first["verify_token"], second["verify_token"])
        self.assertGreater(len(first["verify_token"]), 30)
        self.assertFalse(first["app_secret_configured"])

    def test_saves_outbound_credentials_without_exposing_the_token(self):
        save_whatsapp_connection(self.conn, 7, "+94771234567")

        set_whatsapp_cloud_credentials(
            self.conn,
            7,
            meta_phone_number_id="123456789012345",
            encrypted_access_token="encrypted-token",
        )

        connection = get_whatsapp_connection(self.conn, 7)
        self.assertEqual(connection["meta_phone_number_id"], "123456789012345")
        self.assertTrue(connection["access_token_configured"])
        self.assertTrue(connection["outbound_ready"])
        self.assertNotIn("meta_access_token_secret", connection)

    def test_rejects_invalid_meta_phone_number_id(self):
        save_whatsapp_connection(self.conn, 7, "+94771234567")

        with self.assertRaises(ValueError):
            set_whatsapp_cloud_credentials(
                self.conn,
                7,
                meta_phone_number_id="phone-id",
            )

    def test_saves_waha_provider_credentials_without_exposing_the_key(self):
        save_whatsapp_connection(
            self.conn,
            7,
            "+94771234567",
            provider="waha",
        )
        set_whatsapp_waha_credentials(
            self.conn,
            7,
            session="workspace-7",
            encrypted_api_key="encrypted-waha-key",
        )

        connection = get_whatsapp_connection(self.conn, 7)

        self.assertEqual(connection["provider"], "waha")
        self.assertEqual(connection["waha_session"], "workspace-7")
        self.assertTrue(connection["waha_api_key_configured"])
        self.assertTrue(connection["outbound_ready"])
        self.assertNotIn("waha_api_key_secret", connection)

    def test_rejects_a_waha_session_assigned_to_another_company(self):
        save_whatsapp_connection(self.conn, 7, "+94771234567", provider="waha")
        save_whatsapp_connection(self.conn, 8, "+94771234568", provider="waha")
        set_whatsapp_waha_credentials(self.conn, 7, session="shared-session")

        with self.assertRaises(sqlite3.IntegrityError):
            set_whatsapp_waha_credentials(self.conn, 8, session="shared-session")


if __name__ == "__main__":
    unittest.main()
