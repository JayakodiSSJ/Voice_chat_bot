from __future__ import annotations

import re
import secrets
from typing import Any

from auth_sqlite import fetch_one, utcnow_iso


E164_PHONE_NUMBER = re.compile(r"^\+[1-9]\d{7,14}$")
META_PHONE_NUMBER_ID = re.compile(r"^\d{5,30}$")
WAHA_SESSION_NAME = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
WHATSAPP_PROVIDERS = {"meta", "waha"}


def normalize_whatsapp_provider(provider: str | None) -> str:
    normalized = str(provider or "meta").strip().lower()
    if normalized not in WHATSAPP_PROVIDERS:
        raise ValueError("WhatsApp provider must be either meta or waha.")
    return normalized


def normalize_whatsapp_phone_number(phone_number: str) -> str:
    raw = str(phone_number or "").strip()
    normalized = re.sub(r"[\s().-]", "", raw)
    if not E164_PHONE_NUMBER.fullmatch(normalized):
        raise ValueError("Use an E.164 phone number, for example +94771234567.")
    return normalized


def get_whatsapp_connection(conn, company_id: int) -> dict[str, Any]:
    row = fetch_one(
        conn,
        """
        SELECT
            phone_number,
            provider,
            webhook_key,
            meta_app_secret,
            meta_phone_number_id,
            meta_access_token_secret,
            waha_session,
            waha_api_key_secret,
            webhook_verified_at,
            created_at,
            updated_at,
            updated_by_user_id
        FROM company_whatsapp_connections
        WHERE company_id = ?
        LIMIT 1
        """,
        (company_id,),
    )
    if not row:
        return {
            "configured": False,
            "provider": "meta",
            "phone_number": None,
            "webhook_key": None,
            "app_secret_configured": False,
            "meta_phone_number_id": None,
            "access_token_configured": False,
            "waha_session": None,
            "waha_api_key_configured": False,
            "outbound_ready": False,
            "webhook_verified_at": None,
            "created_at": None,
            "updated_at": None,
            "updated_by_user_id": None,
        }

    provider = normalize_whatsapp_provider(row["provider"])
    outbound_ready = (
        bool(row["waha_session"] and row["waha_api_key_secret"])
        if provider == "waha"
        else bool(row["meta_phone_number_id"] and row["meta_access_token_secret"])
    )
    return {
        "configured": True,
        "provider": provider,
        "phone_number": row["phone_number"],
        "webhook_key": row["webhook_key"],
        "app_secret_configured": bool(row["meta_app_secret"]),
        "meta_phone_number_id": row["meta_phone_number_id"],
        "access_token_configured": bool(row["meta_access_token_secret"]),
        "waha_session": row["waha_session"],
        "waha_api_key_configured": bool(row["waha_api_key_secret"]),
        "outbound_ready": outbound_ready,
        "webhook_verified_at": row["webhook_verified_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "updated_by_user_id": row["updated_by_user_id"],
    }


def save_whatsapp_connection(
    conn,
    company_id: int,
    phone_number: str,
    *,
    provider: str = "meta",
    updated_by_user_id: int | None = None,
) -> dict[str, Any]:
    normalized_phone_number = normalize_whatsapp_phone_number(phone_number)
    normalized_provider = normalize_whatsapp_provider(provider)
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO company_whatsapp_connections (
            company_id,
            phone_number,
            provider,
            created_at,
            updated_at,
            updated_by_user_id
        )
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_id) DO UPDATE SET
            phone_number = excluded.phone_number,
            provider = excluded.provider,
            webhook_verified_at = CASE
                WHEN company_whatsapp_connections.provider = excluded.provider
                    THEN company_whatsapp_connections.webhook_verified_at
                ELSE NULL
            END,
            updated_at = excluded.updated_at,
            updated_by_user_id = excluded.updated_by_user_id
        """,
        (
            company_id,
            normalized_phone_number,
            normalized_provider,
            now,
            now,
            updated_by_user_id,
        ),
    )
    return get_whatsapp_connection(conn, company_id)


def ensure_whatsapp_webhook_identity(
    conn,
    company_id: int,
    *,
    encrypt_secret,
    decrypt_secret,
) -> dict[str, Any]:
    row = fetch_one(
        conn,
        """
        SELECT webhook_key, verification_token_secret, meta_app_secret, webhook_verified_at
        FROM company_whatsapp_connections
        WHERE company_id = ?
        LIMIT 1
        """,
        (company_id,),
    )
    if not row:
        raise ValueError("Configure a WhatsApp number before creating webhook credentials.")

    webhook_key = row["webhook_key"]
    encrypted_verify_token = row["verification_token_secret"]
    verify_token = decrypt_secret(encrypted_verify_token) if encrypted_verify_token else None
    if not webhook_key or not verify_token:
        webhook_key = webhook_key or secrets.token_urlsafe(24)
        verify_token = secrets.token_urlsafe(32)
        now = utcnow_iso()
        conn.execute(
            """
            UPDATE company_whatsapp_connections
            SET webhook_key = ?, verification_token_secret = ?, updated_at = ?
            WHERE company_id = ?
            """,
            (webhook_key, encrypt_secret(verify_token), now, company_id),
        )

    return {
        "webhook_key": webhook_key,
        "verify_token": verify_token,
        "app_secret_configured": bool(row["meta_app_secret"]),
        "verified_at": row["webhook_verified_at"],
    }


def set_whatsapp_meta_app_secret(
    conn,
    company_id: int,
    encrypted_app_secret: str,
) -> None:
    now = utcnow_iso()
    conn.execute(
        """
        UPDATE company_whatsapp_connections
        SET meta_app_secret = ?, webhook_verified_at = NULL, updated_at = ?
        WHERE company_id = ?
        """,
        (encrypted_app_secret, now, company_id),
    )


def set_whatsapp_cloud_credentials(
    conn,
    company_id: int,
    *,
    meta_phone_number_id: str | None = None,
    encrypted_access_token: str | None = None,
) -> None:
    row = fetch_one(
        conn,
        """
        SELECT meta_phone_number_id, meta_access_token_secret
        FROM company_whatsapp_connections
        WHERE company_id = ?
        LIMIT 1
        """,
        (company_id,),
    )
    if not row:
        raise ValueError("Configure a WhatsApp number before saving Meta credentials.")

    phone_number_id = row["meta_phone_number_id"]
    if meta_phone_number_id is not None:
        phone_number_id = str(meta_phone_number_id).strip()
        if not META_PHONE_NUMBER_ID.fullmatch(phone_number_id):
            raise ValueError("Meta Phone Number ID must contain 5 to 30 digits.")

    access_token_secret = encrypted_access_token or row["meta_access_token_secret"]
    now = utcnow_iso()
    conn.execute(
        """
        UPDATE company_whatsapp_connections
        SET meta_phone_number_id = ?, meta_access_token_secret = ?, updated_at = ?
        WHERE company_id = ?
        """,
        (phone_number_id, access_token_secret, now, company_id),
    )


def set_whatsapp_waha_credentials(
    conn,
    company_id: int,
    *,
    session: str | None = None,
    encrypted_api_key: str | None = None,
) -> None:
    row = fetch_one(
        conn,
        """
        SELECT waha_session, waha_api_key_secret
        FROM company_whatsapp_connections
        WHERE company_id = ?
        LIMIT 1
        """,
        (company_id,),
    )
    if not row:
        raise ValueError("Configure a WhatsApp number before saving WAHA credentials.")

    session_name = row["waha_session"]
    if session is not None:
        session_name = str(session).strip()
        if not WAHA_SESSION_NAME.fullmatch(session_name):
            raise ValueError(
                "WAHA session must use 1 to 128 letters, numbers, dots, underscores, or hyphens."
            )

    api_key_secret = encrypted_api_key or row["waha_api_key_secret"]
    now = utcnow_iso()
    conn.execute(
        """
        UPDATE company_whatsapp_connections
        SET waha_session = ?,
            waha_api_key_secret = ?,
            webhook_verified_at = CASE
                WHEN waha_session = ? THEN webhook_verified_at
                ELSE NULL
            END,
            updated_at = ?
        WHERE company_id = ?
        """,
        (session_name, api_key_secret, session_name, now, company_id),
    )


def delete_whatsapp_connection(conn, company_id: int) -> bool:
    cursor = conn.execute(
        "DELETE FROM company_whatsapp_connections WHERE company_id = ?",
        (company_id,),
    )
    return cursor.rowcount > 0
