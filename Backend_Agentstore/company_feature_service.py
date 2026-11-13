from __future__ import annotations

from typing import Any

from auth_sqlite import fetch_one, utcnow_iso


WHATSAPP_FEATURE = "whatsapp"
SUPPORTED_COMPANY_FEATURES = frozenset({WHATSAPP_FEATURE})


def _feature_key(feature_key: str) -> str:
    normalized = (feature_key or "").strip().lower()
    if normalized not in SUPPORTED_COMPANY_FEATURES:
        raise ValueError(f"Unsupported company feature: {feature_key}")
    return normalized


def get_company_feature_entitlement(
    conn,
    company_id: int,
    feature_key: str,
) -> dict[str, Any]:
    key = _feature_key(feature_key)
    row = fetch_one(
        conn,
        """
        SELECT is_enabled, enabled_at, disabled_at, updated_at, updated_by_user_id
        FROM company_feature_entitlements
        WHERE company_id = ? AND feature_key = ?
        LIMIT 1
        """,
        (company_id, key),
    )
    if not row:
        return {
            "feature": key,
            "enabled": False,
            "enabled_at": None,
            "disabled_at": None,
            "updated_at": None,
            "updated_by_user_id": None,
        }

    return {
        "feature": key,
        "enabled": bool(row["is_enabled"]),
        "enabled_at": row["enabled_at"],
        "disabled_at": row["disabled_at"],
        "updated_at": row["updated_at"],
        "updated_by_user_id": row["updated_by_user_id"],
    }


def company_has_feature(conn, company_id: int, feature_key: str) -> bool:
    return get_company_feature_entitlement(conn, company_id, feature_key)["enabled"]


def set_company_feature_entitlement(
    conn,
    company_id: int,
    feature_key: str,
    *,
    enabled: bool,
    updated_by_user_id: int | None = None,
) -> dict[str, Any]:
    key = _feature_key(feature_key)
    if type(enabled) is not bool:
        raise TypeError("enabled must be a boolean")

    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO company_feature_entitlements (
            company_id,
            feature_key,
            is_enabled,
            enabled_at,
            disabled_at,
            updated_at,
            updated_by_user_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_id, feature_key) DO UPDATE SET
            is_enabled = excluded.is_enabled,
            enabled_at = CASE
                WHEN excluded.is_enabled = 1 THEN excluded.enabled_at
                ELSE company_feature_entitlements.enabled_at
            END,
            disabled_at = CASE
                WHEN excluded.is_enabled = 0 THEN excluded.disabled_at
                ELSE NULL
            END,
            updated_at = excluded.updated_at,
            updated_by_user_id = excluded.updated_by_user_id
        """,
        (
            company_id,
            key,
            int(enabled),
            now if enabled else None,
            None if enabled else now,
            now,
            updated_by_user_id,
        ),
    )
    return get_company_feature_entitlement(conn, company_id, key)
