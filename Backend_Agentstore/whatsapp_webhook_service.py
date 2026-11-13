from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from auth_sqlite import fetch_one, utcnow_iso


def verify_meta_signature(raw_body: bytes, signature_header: str, app_secret: str) -> bool:
    signature = str(signature_header or "").strip()
    secret = str(app_secret or "")
    if not signature.startswith("sha256=") or not secret:
        return False
    supplied_digest = signature.split("=", 1)[1].strip().lower()
    if len(supplied_digest) != 64:
        return False
    expected_digest = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(supplied_digest, expected_digest)


def verify_waha_signature(
    raw_body: bytes,
    signature_header: str,
    algorithm_header: str,
    webhook_secret: str,
) -> bool:
    algorithm = str(algorithm_header or "sha512").strip().lower()
    signature = str(signature_header or "").strip().lower()
    secret = str(webhook_secret or "")
    if algorithm != "sha512" or not signature or not secret:
        return False
    if signature.startswith("sha512="):
        signature = signature.split("=", 1)[1].strip()
    if len(signature) != 128:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha512).hexdigest()
    return hmac.compare_digest(signature, expected)


def extract_inbound_messages(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return []

    extracted: list[dict[str, Any]] = []
    entries = payload.get("entry") if isinstance(payload.get("entry"), list) else []
    for entry in entries:
        changes = entry.get("changes") if isinstance(entry, dict) else []
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            metadata = value.get("metadata") if isinstance(value.get("metadata"), dict) else {}
            phone_number_id = str(metadata.get("phone_number_id") or "").strip() or None
            messages = value.get("messages") if isinstance(value.get("messages"), list) else []
            for message in messages:
                if not isinstance(message, dict):
                    continue
                message_id = str(message.get("id") or "").strip()
                if not message_id:
                    continue
                message_type = str(message.get("type") or "unknown").strip().lower()
                text_data = message.get("text") if isinstance(message.get("text"), dict) else {}
                extracted.append(
                    {
                        "meta_message_id": message_id,
                        "sender_phone": str(message.get("from") or "").strip() or None,
                        "recipient_phone_number_id": phone_number_id,
                        "message_type": message_type,
                        "message_text": (
                            str(text_data.get("body") or "")
                            if message_type == "text"
                            else None
                        ),
                        "payload": message,
                    }
                )
    return extracted


def extract_status_updates(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return []

    updates: list[dict[str, Any]] = []
    entries = payload.get("entry") if isinstance(payload.get("entry"), list) else []
    for entry in entries:
        changes = entry.get("changes") if isinstance(entry, dict) else []
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            statuses = value.get("statuses") if isinstance(value.get("statuses"), list) else []
            for status in statuses:
                if not isinstance(status, dict):
                    continue
                meta_message_id = str(status.get("id") or "").strip()
                meta_status = str(status.get("status") or "").strip().lower()
                if not meta_message_id or not meta_status:
                    continue
                errors = status.get("errors") if isinstance(status.get("errors"), list) else []
                updates.append(
                    {
                        "meta_message_id": meta_message_id,
                        "status": meta_status,
                        "error": _status_error(errors),
                    }
                )
    return updates


def extract_waha_inbound_messages(
    payload: dict[str, Any] | None,
    *,
    expected_session: str,
) -> list[dict[str, Any]]:
    if (
        not isinstance(payload, dict)
        or payload.get("event") != "message"
        or str(payload.get("session") or "").strip() != str(expected_session or "").strip()
    ):
        return []
    message = payload.get("payload")
    if not isinstance(message, dict) or message.get("fromMe") is True:
        return []

    message_id = str(message.get("id") or "").strip()
    sender = _waha_sender_id(message.get("from") or message.get("chatId"))
    if not message_id or not sender:
        return []
    has_media = message.get("hasMedia") is True
    body = message.get("body")
    is_text = isinstance(body, str) and bool(body.strip()) and not has_media
    return [
        {
            "meta_message_id": message_id,
            "sender_phone": sender,
            "recipient_phone_number_id": str(expected_session or "").strip(),
            "message_type": "text" if is_text else str(message.get("type") or "unknown").lower(),
            "message_text": body if is_text else None,
            "payload": message,
        }
    ]


def extract_waha_status_updates(
    payload: dict[str, Any] | None,
    *,
    expected_session: str,
) -> list[dict[str, Any]]:
    if (
        not isinstance(payload, dict)
        or payload.get("event") != "message.ack"
        or str(payload.get("session") or "").strip() != str(expected_session or "").strip()
    ):
        return []
    message = payload.get("payload")
    if not isinstance(message, dict):
        return []
    message_id = str(message.get("id") or "").strip()
    if not message_id:
        return []
    ack_name = str(message.get("ackName") or "").strip().upper()
    if not ack_name:
        ack_name = {
            -1: "ERROR",
            0: "PENDING",
            1: "SERVER",
            2: "DEVICE",
            3: "READ",
            4: "PLAYED",
        }.get(message.get("ack"), "UNKNOWN")
    status = {
        "ERROR": "failed",
        "PENDING": "pending",
        "SERVER": "sent",
        "DEVICE": "delivered",
        "READ": "read",
        "PLAYED": "played",
    }.get(ack_name, ack_name.lower())
    return [
        {
            "meta_message_id": message_id,
            "status": status,
            "error": "WAHA reported a delivery error." if ack_name == "ERROR" else None,
        }
    ]


def apply_outbound_status_updates(
    conn,
    company_id: int,
    updates: list[dict[str, Any]],
) -> dict[str, int]:
    updated = 0
    unmatched = 0
    for item in updates:
        now = utcnow_iso()
        cursor = conn.execute(
            """
            UPDATE whatsapp_outbound_messages
            SET meta_status = ?, last_error = ?, status_updated_at = ?
            WHERE company_id = ? AND meta_message_id = ?
            """,
            (
                item.get("status") or "unknown",
                item.get("error"),
                now,
                company_id,
                item.get("meta_message_id"),
            ),
        )
        if cursor.rowcount > 0:
            updated += 1
        else:
            unmatched += 1
    return {"updated": updated, "unmatched": unmatched}


def store_inbound_messages(
    conn,
    company_id: int,
    messages: list[dict[str, Any]],
) -> dict[str, int]:
    stored = 0
    duplicates = 0
    for message in messages:
        cursor = conn.execute(
            """
            INSERT INTO whatsapp_inbound_messages (
                company_id,
                meta_message_id,
                sender_phone,
                recipient_phone_number_id,
                message_type,
                message_text,
                payload_json,
                received_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(company_id, meta_message_id) DO NOTHING
            """,
            (
                company_id,
                message["meta_message_id"],
                message.get("sender_phone"),
                message.get("recipient_phone_number_id"),
                message.get("message_type") or "unknown",
                message.get("message_text"),
                json.dumps(message.get("payload") or {}, separators=(",", ":")),
                utcnow_iso(),
            ),
        )
        if cursor.rowcount > 0:
            stored += 1
        else:
            duplicates += 1
    return {"stored": stored, "duplicates": duplicates}


def get_webhook_connection(conn, webhook_key: str):
    return fetch_one(
        conn,
        """
        SELECT
            company_id,
            phone_number,
            provider,
            webhook_key,
            verification_token_secret,
            meta_app_secret,
            meta_phone_number_id,
            waha_session,
            webhook_verified_at
        FROM company_whatsapp_connections
        WHERE webhook_key = ?
        LIMIT 1
        """,
        (webhook_key,),
    )


def _waha_sender_id(value: Any) -> str | None:
    sender = str(value or "").strip()
    if sender.endswith("@s.whatsapp.net"):
        sender = f"{sender.removesuffix('@s.whatsapp.net')}@c.us"
    if sender.endswith(("@c.us", "@lid")) and sender.split("@", 1)[0].isdigit():
        return sender
    if sender.isdigit():
        return sender
    return None


def mark_webhook_verified(conn, company_id: int) -> str:
    verified_at = utcnow_iso()
    conn.execute(
        """
        UPDATE company_whatsapp_connections
        SET webhook_verified_at = ?, updated_at = ?
        WHERE company_id = ?
        """,
        (verified_at, verified_at, company_id),
    )
    return verified_at


def _status_error(errors: list[dict[str, Any]]) -> str | None:
    details: list[str] = []
    for error in errors:
        if not isinstance(error, dict):
            continue
        title = str(error.get("title") or error.get("message") or "").strip()
        code = error.get("code")
        if title and code is not None:
            details.append(f"{title} (code {code})")
        elif title:
            details.append(title)
    return "; ".join(details)[:1000] or None
