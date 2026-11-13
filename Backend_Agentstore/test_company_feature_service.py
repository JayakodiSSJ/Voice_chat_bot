import sqlite3
import unittest

from company_feature_service import (
    WHATSAPP_FEATURE,
    company_has_feature,
    get_company_feature_entitlement,
    set_company_feature_entitlement,
)


class CompanyFeatureServiceTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE company_feature_entitlements (
                company_id INTEGER NOT NULL,
                feature_key TEXT NOT NULL,
                is_enabled INTEGER NOT NULL DEFAULT 0 CHECK (is_enabled IN (0,1)),
                enabled_at TEXT,
                disabled_at TEXT,
                updated_at TEXT NOT NULL,
                updated_by_user_id INTEGER,
                PRIMARY KEY (company_id, feature_key)
            );
            """
        )

    def tearDown(self):
        self.conn.close()

    def test_missing_entitlement_is_disabled_by_default(self):
        entitlement = get_company_feature_entitlement(
            self.conn,
            7,
            WHATSAPP_FEATURE,
        )

        self.assertFalse(entitlement["enabled"])
        self.assertIsNone(entitlement["updated_at"])
        self.assertFalse(company_has_feature(self.conn, 7, WHATSAPP_FEATURE))

    def test_can_enable_and_disable_whatsapp_entitlement(self):
        enabled = set_company_feature_entitlement(
            self.conn,
            7,
            WHATSAPP_FEATURE,
            enabled=True,
            updated_by_user_id=3,
        )

        self.assertTrue(enabled["enabled"])
        self.assertIsNotNone(enabled["enabled_at"])
        self.assertIsNone(enabled["disabled_at"])
        self.assertEqual(enabled["updated_by_user_id"], 3)

        disabled = set_company_feature_entitlement(
            self.conn,
            7,
            WHATSAPP_FEATURE,
            enabled=False,
            updated_by_user_id=4,
        )

        self.assertFalse(disabled["enabled"])
        self.assertEqual(disabled["enabled_at"], enabled["enabled_at"])
        self.assertIsNotNone(disabled["disabled_at"])
        self.assertEqual(disabled["updated_by_user_id"], 4)

        reenabled = set_company_feature_entitlement(
            self.conn,
            7,
            WHATSAPP_FEATURE,
            enabled=True,
            updated_by_user_id=5,
        )

        self.assertTrue(reenabled["enabled"])
        self.assertIsNone(reenabled["disabled_at"])
        self.assertEqual(reenabled["updated_by_user_id"], 5)

    def test_rejects_unknown_features_and_non_boolean_values(self):
        with self.assertRaises(ValueError):
            get_company_feature_entitlement(self.conn, 7, "messenger")

        with self.assertRaises(TypeError):
            set_company_feature_entitlement(
                self.conn,
                7,
                WHATSAPP_FEATURE,
                enabled=1,
            )


if __name__ == "__main__":
    unittest.main()
