from __future__ import annotations
from flask import g

from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Callable
import os
import sqlite3
import secrets
import hashlib
import re
import time
from urllib.parse import urlencode, urlparse
import json
import os

import jwt
import requests
from flask import Response, jsonify, request
from werkzeug.security import check_password_hash, generate_password_hash
import base64
from cryptography.fernet import Fernet
from azure_sso_service import verify_microsoft_token

from auth_sqlite import (
    fetch_one,
    get_conn,
    get_email_domain,
    init_db,
    is_public_email_domain,
    is_valid_email,
    migrate_legacy_whatsapp_entitlements,
    normalize_email,
    utcnow_iso,
)
from email_branding import build_email_html, build_email_text, get_sender_identity, attach_email_logo
from api_tool_service import api_tool_service
from database_tool_audit_service import database_tool_audit_service
from database_tool_service import database_tool_service
from tool_audit_service import tool_audit_service
from model_router import model_router
from openai_service import openai_service
from gemini_service import (
    GeminiAPIError,
    MAX_AUDIO_BYTES,
    detect_transcript_language,
    gemini_service,
    select_tts_provider,
)
from whisper_stt_service import stt_provider, whisper_stt
from token_usage import TokenUsageCollector, capture_token_usage
from agent_deployment_service import agent_deployment_service
from custom_agent_chat_service import custom_agent_chat_service
from agent_whatsapp_access_service import (
    resolve_agent_whatsapp_enabled,
    validate_agent_whatsapp_access,
)
from company_feature_service import (
    WHATSAPP_FEATURE,
    company_has_feature,
    get_company_feature_entitlement,
    set_company_feature_entitlement,
)
from form_tool_service import form_tool_service
from ingestion_service import ingestion_service
from mcp_tool_service import mcp_tool_service
from whatsapp_connection_service import (
    delete_whatsapp_connection,
    ensure_whatsapp_webhook_identity,
    get_whatsapp_connection,
    normalize_whatsapp_provider,
    save_whatsapp_connection,
    set_whatsapp_cloud_credentials,
    set_whatsapp_meta_app_secret,
    set_whatsapp_waha_credentials,
)
from whatsapp_webhook_service import (
    apply_outbound_status_updates,
    extract_inbound_messages,
    extract_status_updates,
    extract_waha_inbound_messages,
    extract_waha_status_updates,
    get_webhook_connection,
    mark_webhook_verified,
    store_inbound_messages,
    verify_meta_signature,
    verify_waha_signature,
)
from whatsapp_conversation_service import process_whatsapp_messages
from whatsapp_dispatch_service import whatsapp_background_dispatcher
from whatsapp_addon_service import (
    claim_stripe_webhook_event,
    finish_stripe_webhook_event,
    get_company_whatsapp_addon_state,
    get_company_whatsapp_subscription,
    get_whatsapp_addon_config,
    register_stripe_webhook_event,
    save_company_whatsapp_subscription,
    save_whatsapp_addon_config,
    set_company_whatsapp_subscription_cancel_at_period_end,
    set_whatsapp_addon_stripe_catalog,
)
from whatsapp_addon_billing_service import (
    create_whatsapp_checkout_session,
    reconcile_whatsapp_subscription_prices,
    set_whatsapp_subscription_cancel_at_period_end,
)
from whatsapp_addon_webhook_service import (
    process_whatsapp_stripe_event,
    stripe_event_payload,
)
from agent_configuration_service import (
    APIComponent,
    KBComponent,
    available_tool_definitions,
    create_workflow_file,
    selected_tool_names,
    tool_aliases,
    tool_is_selected,
    workflow_file_path,
)
from subscription_packages import (
    EDITABLE_PACKAGE_IDS,
    get_tier_info,
    load_packages_config,
    normalize_packages_config,
    packages_for_public,
    save_packages_config,
)

def generate_temp_password() -> str:
    return secrets.token_urlsafe(16)


def _auth_secret(app) -> str:
    # Prefer explicit env var; fall back to Flask SECRET_KEY; final fallback is a dev-only string.
    return (
        app.config.get("AUTH_SECRET_KEY")
        or app.config.get("SECRET_KEY")
        or "dev-secret-change-me"
    )


def _encode_token(app, payload: dict[str, Any], expires_in_seconds: int = 60 * 60 * 24 * 7) -> str:
    now = datetime.now(timezone.utc)
    exp = now + timedelta(seconds=expires_in_seconds)
    token_payload = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(token_payload, _auth_secret(app), algorithm="HS256")


def _decode_token(app, token: str) -> dict[str, Any]:
    return jwt.decode(token, _auth_secret(app), algorithms=["HS256"])


def _get_fernet(app) -> Fernet:
    """Helper to get a Fernet instance derived from AUTH_SECRET_KEY"""
    secret = _auth_secret(app)
    key = hashlib.sha256(secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _encrypt_val(app, val: str | None) -> str | None:
    if not val:
        return None
    return _get_fernet(app).encrypt(val.encode()).decode()


def _decrypt_val(app, val: str | None) -> str | None:
    if not val:
        return None
    try:
        return _get_fernet(app).decrypt(val.encode()).decode()
    except Exception:
        # Fallback if the value was already plain text or key changed
        return val


_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _table_columns(conn, table_name: str) -> set[str]:
    if not _SQL_IDENTIFIER_RE.match(table_name):
        raise ValueError(f"Unsafe table name: {table_name}")
    cur = conn.execute(f"PRAGMA table_info({table_name})")
    try:
        return {row["name"] for row in cur.fetchall()}
    finally:
        cur.close()


def _delete_if_column_exists(conn, table_name: str, column_name: str, value: Any) -> bool:
    if not _SQL_IDENTIFIER_RE.match(column_name):
        raise ValueError(f"Unsafe column name: {column_name}")
    columns = _table_columns(conn, table_name)
    if column_name not in columns:
        return False
    conn.execute(f"DELETE FROM {table_name} WHERE {column_name} = ?", (value,))
    return True


def _delete_all_if_table_exists(conn, table_name: str) -> bool:
    if not _table_columns(conn, table_name):
        return False
    conn.execute(f"DELETE FROM {table_name}")
    return True


def _delete_except_column_values_if_exists(
    conn,
    table_name: str,
    column_name: str,
    keep_values: set[int],
) -> bool:
    if not _SQL_IDENTIFIER_RE.match(column_name):
        raise ValueError(f"Unsafe column name: {column_name}")
    columns = _table_columns(conn, table_name)
    if column_name not in columns:
        return False
    if not keep_values:
        conn.execute(f"DELETE FROM {table_name}")
        return True
    placeholders = ", ".join("?" for _ in keep_values)
    conn.execute(
        f"DELETE FROM {table_name} WHERE {column_name} NOT IN ({placeholders})",
        tuple(keep_values),
    )
    return True


def require_auth(app):
    def decorator(fn: Callable[..., Any]):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            authz = request.headers.get("Authorization", "")
            if not authz.startswith("Bearer "):
                return jsonify({"error": "missing_token"}), 401
            token = authz.split(" ", 1)[1].strip()
            try:
                claims = _decode_token(app, token)
            except jwt.ExpiredSignatureError:
                return jsonify({"error": "token_expired"}), 401
            except Exception:
                return jsonify({"error": "invalid_token"}), 401

            try:
                user_id = int(claims.get("user_id") or 0)
                company_id = int(claims.get("company_id") or 0)
            except (TypeError, ValueError):
                return jsonify({"error": "invalid_session", "message": "Invalid session. Please login again."}), 401
            if user_id <= 0 or company_id <= 0:
                return jsonify({"error": "invalid_session", "message": "Invalid session. Please login again."}), 401

            try:
                with get_conn() as conn:
                    user = fetch_one(
                        conn,
                        "SELECT id, email, role, company_id, is_active FROM users WHERE id = ? LIMIT 1",
                        (user_id,),
                    )
            except Exception as exc:
                print(f"ERROR: Failed to validate authenticated user: {exc}")
                return jsonify({"error": "auth_validation_failed", "message": "Unable to validate session. Please try again."}), 500
            if not user or int(user["is_active"]) != 1:
                return jsonify({"error": "invalid_session", "message": "Invalid session. Please login again."}), 401
            if int(user["company_id"]) != company_id:
                return jsonify({"error": "tenant_mismatch", "message": "Invalid session. Please login again."}), 401

            claims["user_id"] = user_id
            claims["company_id"] = company_id
            claims["email"] = user["email"]
            claims["role"] = user["role"]
            request.auth = claims  # type: ignore[attr-defined]

            # Process auto-renewals or expirations dynamically on every request
            company_id = claims.get("company_id")
            if company_id:
                try:
                    with get_conn() as conn:
                        check_and_process_subscriptions(conn, int(company_id))
                        conn.commit()
                except Exception as e:
                    print(f"Error checking dynamic subscription status: {e}")

            return fn(*args, **kwargs)

        return wrapper

    return decorator


_mail_client = None
_app_instance = None


def _json_error(status: int, code: str, message: str):
    return jsonify({"error": code, "message": message}), status


def _voice_quota_error(exc: GeminiAPIError, operation: str):
    provider_message = str(exc).lower()
    daily_limit = "per day" in provider_message or "daily" in provider_message
    if daily_limit:
        code = "gemini_daily_quota_exceeded"
        message = (
            f"Gemini's daily {operation} quota has been reached. "
            "Voice chat is paused. Try again after the quota resets, or ask the project owner "
            "to review billing and limits in Google AI Studio."
        )
    else:
        code = "gemini_rate_limited"
        message = f"Gemini is temporarily rate-limiting {operation} requests. Please try again later."

    response, status = _json_error(429, code, message)
    if exc.retry_after:
        response.headers["Retry-After"] = exc.retry_after
    return response, status


SUPER_ADMIN_DOMAIN = os.getenv("SUPER_ADMIN_DOMAIN", "intranet.slt.com.lk").strip().lower()
INITIAL_ADMIN_PASSWORD_RESET_PURPOSE = "INITIAL_COMPANY_ADMIN_PASSWORD_RESET"


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def is_agent_expired(created_at_str: str, company_expires_at_str: str | None = None, expiry_days: int = 30) -> bool:
    if company_expires_at_str:
        try:
            clean_str = company_expires_at_str
            if clean_str.endswith('Z'):
                clean_str = clean_str[:-1] + '+00:00'
            elif "T" in clean_str and "+" not in clean_str and "-" not in clean_str[clean_str.index("T"):]:
                clean_str += "+00:00"
            expires_at = datetime.fromisoformat(clean_str)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return now > expires_at
        except Exception:
            return False

    # Fallback to created_at + expiry_days
    if not created_at_str:
        return False
    try:
        clean_str = created_at_str
        if clean_str.endswith('Z'):
            clean_str = clean_str[:-1] + '+00:00'
        elif "T" in clean_str and "+" not in clean_str and "-" not in clean_str[clean_str.index("T"):]:
            clean_str += "+00:00"
        created_at = datetime.fromisoformat(clean_str)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        expires_at = created_at + timedelta(days=expiry_days)
        now = datetime.now(timezone.utc)
        return now > expires_at
    except Exception:
        return False


def check_and_process_subscriptions(conn, company_id: int):
    company = fetch_one(
        conn,
        "SELECT subscription_tier, subscription_status, subscription_expires_at FROM company WHERE id = ?",
        (company_id,)
    )
    if not company:
        return
    
    expires_at_str = company["subscription_expires_at"]
    if not expires_at_str:
        return
        
    try:
        expires_at_clean = expires_at_str
        if expires_at_clean.endswith("Z"):
            expires_at_clean = expires_at_clean[:-1] + "+00:00"
        elif "T" in expires_at_clean and "+" not in expires_at_clean and "-" not in expires_at_clean[expires_at_clean.index("T"):]:
            expires_at_clean += "+00:00"
            
        expires_at = datetime.fromisoformat(expires_at_clean)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except Exception as e:
        print(f"Error parsing subscription expiry date '{expires_at_str}': {e}")
        return
        
    now = datetime.now(timezone.utc)
    if now > expires_at:
        tier = (company["subscription_tier"] or "free").lower()
        if tier == "free":
            if company["subscription_status"] != "expired":
                conn.execute(
                    "UPDATE company SET subscription_status = 'expired' WHERE id = ?",
                    (company_id,)
                )
                conn.execute(
                    "UPDATE subscriptions SET status = 'expired', updated_at = ? WHERE workspace_id = ? AND status = 'trialing'",
                    (utcnow_iso(), company_id)
                )
        else:
            packages = load_packages_config(conn)
            tier_info = get_tier_info(packages, tier)
            amount_usd = tier_info.get("price", 0.0)
            
            next_expiry = expires_at + timedelta(days=30)
            if next_expiry < now:
                next_expiry = now + timedelta(days=30)
            next_expiry_str = next_expiry.isoformat()
            
            now_str = utcnow_iso()
            
            conn.execute(
                "UPDATE company SET subscription_expires_at = ?, subscription_status = 'active' WHERE id = ?",
                (next_expiry_str, company_id)
            )
            temp_tx = f"TEMP_TX_{int(now.timestamp())}_{company_id}"
            plan_display_name = f"{tier.capitalize()} Plan"
            cur = conn.execute(
                """
                INSERT INTO payments (workspace_id, transaction_id, payment_gateway, amount, currency, payment_status, payment_date, expires_at, plan_name)
                VALUES (?, ?, 'stripe_auto_renew', ?, 'usd', 'completed', ?, ?, ?)
                """,
                (company_id, temp_tx, amount_usd, now_str, next_expiry_str, plan_display_name)
            )
            inserted_id = cur.lastrowid
            tx_id = f"T_ID {inserted_id:02d}"
            conn.execute(
                "UPDATE payments SET transaction_id = ? WHERE id = ?",
                (tx_id, inserted_id)
            )
            conn.execute(
                """
                INSERT INTO subscriptions (
                    workspace_id, plan, status, started_at, expires_at,
                    trial_used, messages_used, storage_used, created_at, updated_at
                )
                VALUES (?, ?, 'active', ?, ?, 0, 0, 0, ?, ?)
                """,
                (company_id, tier, now_str, next_expiry_str, now_str, now_str)
            )

            # Send auto renewal notification email
            try:
                company_row = fetch_one(conn, "SELECT name FROM company WHERE id = ?", (company_id,))
                company_name = company_row["name"] if company_row else "Workspace Admin"
                expiry_date_only = next_expiry_str.split("T")[0]
                notify_billing_event(
                    conn,
                    company_id,
                    "Subscription Automatically Renewed",
                    f"Your subscription package for {company_name} has been automatically renewed for another month.",
                    [
                        ("Workspace Name", company_name),
                        ("Renewed Plan", f"{tier.capitalize()} Plan"),
                        ("Transaction ID", tx_id),
                        ("Renewal Amount", f"${amount_usd:.2f} USD"),
                        ("Next Renewal Date", expiry_date_only)
                    ]
                )
            except Exception as ex:
                print(f"Error sending auto renewal email notification: {ex}")


def _tier_info_for_company(conn, company_id: int) -> tuple[str, dict[str, Any]]:
    company = fetch_one(conn, "SELECT subscription_tier, subscription_status FROM company WHERE id = ?", (company_id,))
    tier = "free"
    if company and company["subscription_tier"]:
        tier = company["subscription_tier"].lower()
    packages = load_packages_config(conn)
    return tier, get_tier_info(packages, tier)


def _parse_utc_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        elif "T" in text and "+" not in text and "-" not in text[text.index("T"):]:
            text += "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _monthly_chat_period_for_company(
    company: dict[str, Any] | sqlite3.Row | None,
    subscription: dict[str, Any] | sqlite3.Row | None = None,
) -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    company = company or {}
    subscription = subscription or {}
    expiry = _parse_utc_datetime(
        subscription["expires_at"] if "expires_at" in subscription.keys() else None
    ) if hasattr(subscription, "keys") else None
    if not expiry:
        expiry = _parse_utc_datetime(company["subscription_expires_at"] if "subscription_expires_at" in company.keys() else None) if hasattr(company, "keys") else None
    started = _parse_utc_datetime(
        subscription["started_at"] if "started_at" in subscription.keys() else None
    ) if hasattr(subscription, "keys") else None
    created = _parse_utc_datetime(company["created_at"] if "created_at" in company.keys() else None) if hasattr(company, "keys") else None

    if started and expiry and started <= now < expiry:
        period_start = started
        period_end = period_start + timedelta(days=30)
        while period_end <= now:
            period_start = period_end
            period_end = period_start + timedelta(days=30)
        if period_end > expiry:
            period_end = expiry
        return period_start, period_end

    if expiry and expiry > now:
        period_end = expiry
        period_start = period_end - timedelta(days=30)
        while now < period_start:
            period_end = period_start
            period_start = period_end - timedelta(days=30)
        return period_start, period_end

    period_start = created or now
    period_end = period_start + timedelta(days=30)
    while period_end <= now:
        period_start = period_end
        period_end = period_start + timedelta(days=30)
    return period_start, period_end


def _chat_limit_for_company(conn, company_id: int) -> tuple[str, int | None]:
    tier, tier_info = _tier_info_for_company(conn, company_id)
    raw_limit = tier_info.get("monthly_tokens")
    if raw_limit in (None, "", -1, "unlimited", "Unlimited"):
        return tier, None
    try:
        return tier, max(0, int(raw_limit))
    except (TypeError, ValueError):
        return tier, 0


def _count_company_chat_tokens_for_period(
    conn, company_id: int, period_start_iso: str, period_end_iso: str
) -> int:
    row = fetch_one(
        conn,
        """
        SELECT COALESCE(SUM(total_tokens), 0) AS tokens_used
        FROM chat_token_usage_events
        WHERE company_id = ? AND created_at >= ? AND created_at < ?
        """,
        (company_id, period_start_iso, period_end_iso),
    )
    return int((row and row["tokens_used"]) or 0)


def _count_user_chat_tokens_for_period(
    conn, company_id: int, user_id: int, period_start_iso: str, period_end_iso: str
) -> int:
    row = fetch_one(
        conn,
        """
        SELECT COALESCE(SUM(total_tokens), 0) AS tokens_used
        FROM chat_token_usage_events
        WHERE company_id = ? AND user_id = ? AND created_at >= ? AND created_at < ?
        """,
        (company_id, user_id, period_start_iso, period_end_iso),
    )
    return int((row and row["tokens_used"]) or 0)


def _ensure_company_chat_usage(conn, company_id: int) -> dict[str, Any]:
    company = fetch_one(
        conn,
        "SELECT id, created_at, subscription_tier, subscription_status, subscription_expires_at FROM company WHERE id = ? LIMIT 1",
        (company_id,),
    )
    subscription = fetch_one(
        conn,
        """
        SELECT started_at, expires_at
        FROM subscriptions
        WHERE workspace_id = ?
          AND status IN ('active', 'trialing')
          AND started_at IS NOT NULL
          AND expires_at IS NOT NULL
        ORDER BY id DESC
        LIMIT 1
        """,
        (company_id,),
    )
    period_start, period_end = _monthly_chat_period_for_company(company, subscription)
    period_start_iso = period_start.isoformat()
    period_end_iso = period_end.isoformat()
    tier, monthly_limit = _chat_limit_for_company(conn, company_id)
    now = utcnow_iso()
    counted_tokens = _count_company_chat_tokens_for_period(
        conn, company_id, period_start_iso, period_end_iso
    )
    usage = fetch_one(conn, "SELECT * FROM company_chat_usage WHERE company_id = ? LIMIT 1", (company_id,))
    if not usage:
        conn.execute(
            """
            INSERT INTO company_chat_usage
                (company_id, period_start, period_end, messages_used, tokens_used, created_at, updated_at)
            VALUES (?, ?, ?, 0, ?, ?, ?)
            """,
            (company_id, period_start_iso, period_end_iso, counted_tokens, now, now),
        )
        tokens_used = counted_tokens
    elif usage["period_start"] != period_start_iso or usage["period_end"] != period_end_iso:
        conn.execute(
            """
            UPDATE company_chat_usage
            SET period_start = ?, period_end = ?, messages_used = 0, tokens_used = ?, updated_at = ?
            WHERE company_id = ?
            """,
            (period_start_iso, period_end_iso, counted_tokens, now, company_id),
        )
        tokens_used = counted_tokens
    else:
        tokens_used = int(usage["tokens_used"] or 0)
        if tokens_used != counted_tokens:
            tokens_used = counted_tokens
            conn.execute(
                "UPDATE company_chat_usage SET tokens_used = ?, updated_at = ? WHERE company_id = ?",
                (tokens_used, now, company_id),
            )

    remaining = None if monthly_limit is None else max(0, monthly_limit - tokens_used)
    return {
        "tier": tier,
        "monthly_token_limit": monthly_limit,
        "tokens_used": tokens_used,
        "tokens_remaining": remaining,
        "period_start": period_start_iso,
        "period_end": period_end_iso,
    }


def _ensure_user_chat_usage(
    conn,
    company_id: int,
    user_id: int,
    period_start_iso: str,
    period_end_iso: str,
) -> dict[str, Any]:
    now = utcnow_iso()
    usage = fetch_one(
        conn,
        "SELECT * FROM user_chat_usage WHERE company_id = ? AND user_id = ? LIMIT 1",
        (company_id, user_id),
    )
    counted_tokens = _count_user_chat_tokens_for_period(
        conn, company_id, user_id, period_start_iso, period_end_iso
    )
    if not usage:
        conn.execute(
            """
            INSERT INTO user_chat_usage
                (company_id, user_id, period_start, period_end, messages_used, tokens_used, created_at, updated_at)
            VALUES (?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (company_id, user_id, period_start_iso, period_end_iso, counted_tokens, now, now),
        )
        tokens_used = counted_tokens
    elif usage["period_start"] != period_start_iso or usage["period_end"] != period_end_iso:
        conn.execute(
            """
            UPDATE user_chat_usage
            SET period_start = ?, period_end = ?, messages_used = 0, tokens_used = ?, updated_at = ?
            WHERE company_id = ? AND user_id = ?
            """,
            (period_start_iso, period_end_iso, counted_tokens, now, company_id, user_id),
        )
        tokens_used = counted_tokens
    else:
        tokens_used = int(usage["tokens_used"] or 0)
        if tokens_used != counted_tokens:
            tokens_used = counted_tokens
            conn.execute(
                """
                UPDATE user_chat_usage SET tokens_used = ?, updated_at = ?
                WHERE company_id = ? AND user_id = ?
                """,
                (tokens_used, now, company_id, user_id),
            )
    return {
        "tokens_used": tokens_used,
        "period_start": period_start_iso,
        "period_end": period_end_iso,
    }


def _check_user_chat_quota(conn, company_id: int, user_id: int, usage: dict[str, Any]) -> dict[str, Any]:
    override = fetch_one(
        conn,
        """
        SELECT monthly_token_limit, is_suspended, suspended_until, reason
        FROM user_chat_quota_overrides
        WHERE company_id = ? AND user_id = ?
        LIMIT 1
        """,
        (company_id, user_id),
    )
    result = {"allowed": True}
    if not override:
        return result

    suspended_until = _parse_utc_datetime(override["suspended_until"])
    if int(override["is_suspended"] or 0) and (suspended_until is None or suspended_until > datetime.now(timezone.utc)):
        return {
            "allowed": False,
            "error": "user_chat_suspended",
            "message": "Your chat access is temporarily limited by your workspace administrator.",
            "reason": override["reason"],
            "suspended_until": override["suspended_until"],
        }

    monthly_limit = override["monthly_token_limit"]
    if monthly_limit is not None:
        user_usage = _ensure_user_chat_usage(
            conn,
            company_id,
            user_id,
            usage["period_start"],
            usage["period_end"],
        )
        used = int(user_usage["tokens_used"] or 0)
        if used >= int(monthly_limit):
            return {
                "allowed": False,
                "error": "user_monthly_token_limit_reached",
                "message": "You have reached your monthly token limit set by your workspace administrator.",
                "monthly_token_limit": int(monthly_limit),
                "tokens_used": used,
                "tokens_remaining": 0,
            }
        result.update({
            "monthly_token_limit": int(monthly_limit),
            "tokens_used": used,
            "tokens_remaining": max(0, int(monthly_limit) - used),
        })
    return result


def _check_chat_quota(conn, company_id: int, user_id: int) -> dict[str, Any]:
    usage = _ensure_company_chat_usage(conn, company_id)
    user_quota = _check_user_chat_quota(conn, company_id, user_id, usage) if user_id else {"allowed": True}
    if not user_quota.get("allowed"):
        usage["allowed"] = False
        usage["user_quota"] = user_quota
        return usage

    monthly_limit = usage["monthly_token_limit"]
    tokens_used = int(usage["tokens_used"] or 0)
    if monthly_limit is not None and tokens_used >= int(monthly_limit):
        usage["allowed"] = False
        return usage

    usage["allowed"] = True
    usage["user_quota"] = user_quota
    return usage


def _record_chat_token_usage(
    conn,
    company_id: int,
    user_id: int,
    collector: TokenUsageCollector,
) -> dict[str, Any]:
    usage = _ensure_company_chat_usage(conn, company_id)
    if user_id:
        _ensure_user_chat_usage(
            conn,
            company_id,
            user_id,
            usage["period_start"],
            usage["period_end"],
        )
    charged_input_tokens = 0
    charged_output_tokens = 0
    charged_total_tokens = 0
    now = utcnow_iso()

    for event in collector.events:
        cursor = conn.execute(
            """
            INSERT INTO chat_token_usage_events
                (provider_response_id, company_id, user_id, model, input_tokens,
                 output_tokens, total_tokens, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_response_id) DO NOTHING
            """,
            (
                event.provider_response_id,
                company_id,
                user_id or None,
                event.model,
                event.input_tokens,
                event.output_tokens,
                event.total_tokens,
                now,
            ),
        )
        if int(cursor.rowcount or 0) > 0:
            charged_input_tokens += event.input_tokens
            charged_output_tokens += event.output_tokens
            charged_total_tokens += event.total_tokens

    if charged_total_tokens:
        conn.execute(
            """
            UPDATE company_chat_usage
            SET tokens_used = tokens_used + ?, updated_at = ?
            WHERE company_id = ?
            """,
            (charged_total_tokens, now, company_id),
        )
        if user_id:
            conn.execute(
                """
                UPDATE user_chat_usage
                SET tokens_used = tokens_used + ?, updated_at = ?
                WHERE company_id = ? AND user_id = ?
                """,
                (charged_total_tokens, now, company_id, user_id),
            )

    tokens_used = int(usage["tokens_used"] or 0) + charged_total_tokens
    monthly_limit = usage["monthly_token_limit"]
    usage.update(
        {
            "tokens_used": tokens_used,
            "tokens_remaining": None
            if monthly_limit is None
            else max(0, int(monthly_limit) - tokens_used),
            "request_token_usage": {
                "input_tokens": charged_input_tokens,
                "output_tokens": charged_output_tokens,
                "total_tokens": charged_total_tokens,
            },
        }
    )
    return usage


def _run_metered_chat(company_id: int, user_id: int, operation: Callable[[], Any]):
    collector: TokenUsageCollector | None = None
    recorded_usage = None
    try:
        with capture_token_usage() as collector:
            result = operation()
    finally:
        if collector is not None and collector.events:
            with get_conn() as conn:
                recorded_usage = _record_chat_token_usage(conn, company_id, user_id, collector)
                conn.commit()
    return result, recorded_usage


def _company_chat_usage_summary(conn, company_id: int) -> dict[str, Any]:
    usage = _ensure_company_chat_usage(conn, company_id)
    start = usage["period_start"]
    end = usage["period_end"]
    breakdown = fetch_one(
        conn,
        """
        SELECT
            COALESCE(SUM(input_tokens), 0) AS input_tokens_used,
            COALESCE(SUM(output_tokens), 0) AS output_tokens_used
        FROM chat_token_usage_events
        WHERE company_id = ? AND created_at >= ? AND created_at < ?
        """,
        (company_id, start, end),
    )

    rows = conn.execute(
        """
        SELECT
            u.id AS user_id,
            u.email,
            u.full_name,
            u.role,
            u.is_active,
            COALESCE(te.tokens_used, uu.tokens_used, 0) AS tokens_used,
            uu.updated_at AS ledger_updated_at,
            q.monthly_token_limit AS user_monthly_token_limit,
            q.is_suspended AS user_is_suspended,
            q.suspended_until AS user_suspended_until,
            q.reason AS user_quota_reason,
            COALESCE(m.agents_used, 0) AS agents_used,
            COALESCE(m.last_activity_at, te.last_activity_at, uu.updated_at) AS last_activity_at
        FROM users u
        LEFT JOIN user_chat_usage uu
            ON uu.user_id = u.id
            AND uu.company_id = u.company_id
            AND uu.period_start = ?
            AND uu.period_end = ?
        LEFT JOIN (
            SELECT user_id, SUM(total_tokens) AS tokens_used, MAX(created_at) AS last_activity_at
            FROM chat_token_usage_events
            WHERE company_id = ? AND created_at >= ? AND created_at < ?
            GROUP BY user_id
        ) te ON te.user_id = u.id
        LEFT JOIN (
            SELECT user_id, COUNT(DISTINCT agent_id) AS agents_used, MAX(created_at) AS last_activity_at
            FROM custom_agent_chat_messages
            WHERE company_id = ?
              AND message_type = 'user'
              AND content != '__tool_result__'
              AND created_at >= ? AND created_at < ?
            GROUP BY user_id
        ) m ON m.user_id = u.id
        LEFT JOIN user_chat_quota_overrides q
            ON q.company_id = u.company_id
            AND q.user_id = u.id
        WHERE u.company_id = ?
          AND (COALESCE(te.tokens_used, uu.tokens_used, 0) > 0 OR COALESCE(m.agents_used, 0) > 0)
        ORDER BY COALESCE(te.tokens_used, uu.tokens_used, 0) DESC,
                 last_activity_at DESC,
                 u.full_name COLLATE NOCASE ASC
        """,
        (start, end, company_id, start, end, company_id, start, end, company_id),
    ).fetchall()
    users = []
    monthly_limit = usage["monthly_token_limit"]
    for row in rows:
        user_tokens = int(row["tokens_used"] or 0)
        users.append(
            {
                "user_id": row["user_id"],
                "email": row["email"],
                "full_name": row["full_name"],
                "role": row["role"],
                "is_active": bool(row["is_active"]),
                "tokens_used": user_tokens,
                "agents_used": int(row["agents_used"] or 0),
                "last_activity_at": row["last_activity_at"],
                "quota_share_percent": None if not monthly_limit else round((user_tokens / int(monthly_limit)) * 100, 2),
                "user_monthly_token_limit": row["user_monthly_token_limit"],
                "user_is_suspended": bool(row["user_is_suspended"]),
                "user_suspended_until": row["user_suspended_until"],
                "user_quota_reason": row["user_quota_reason"],
            }
        )
    usage["input_tokens_used"] = int((breakdown and breakdown["input_tokens_used"]) or 0)
    usage["output_tokens_used"] = int((breakdown and breakdown["output_tokens_used"]) or 0)
    usage["users"] = users
    usage["usage_percent"] = None if not monthly_limit else round((int(usage["tokens_used"] or 0) / int(monthly_limit)) * 100, 2)
    return usage


def _validate_tier_limits(conn, company_id, data, is_edit=False, agent_id=None):
    company = fetch_one(conn, "SELECT subscription_tier, subscription_status FROM company WHERE id = ?", (company_id,))
    if company and company["subscription_status"] == "expired":
        return False, "Your workspace subscription has expired. Please upgrade or renew your plan to perform this action."

    tier, tier_info = _tier_info_for_company(conn, company_id)
    agent_limit = tier_info.get("agent_limit")

    # 1. Check agent count limit for new agents
    if not is_edit and agent_limit is not None:
        agent_count = conn.execute("SELECT COUNT(*) FROM custom_agents WHERE company_id = ?", (company_id,)).fetchone()[0]
        if agent_count >= int(agent_limit):
            return False, f"Agent creation limit reached for your active {tier.capitalize()} plan (Limit: {agent_limit}). Please upgrade your plan."

    # 2. Check allowed tools
    requested_tools = data.get("tools") or []
    config_obj = data.get("configuration") or {}
    if isinstance(config_obj, dict) and "tools" in config_obj:
        requested_tools = config_obj.get("tools") or []

    normalized_tools = []
    for t in requested_tools:
        if isinstance(t, str):
            normalized_tools.append(t.lower())
        elif isinstance(t, dict):
            normalized_tools.append((t.get("type") or t.get("name") or "").lower())

    for t in normalized_tools:
        mapped_t = "kb" if "knowledge" in t else t
        mapped_t = "form_handler" if "form" in mapped_t else mapped_t
        mapped_t = "database" if mapped_t in {"db", "database", "database_tool", "structured_database", "structured_db"} else mapped_t

        if mapped_t == "api" and "api" not in tier_info["allowed_tools"]:
            return False, "API tools are only available on the Enterprise tier. Please upgrade your subscription."
        if mapped_t == "database" and "database" not in tier_info["allowed_tools"] and "api" not in tier_info["allowed_tools"]:
            return False, "Database tools are only available on the Enterprise tier. Please upgrade your subscription."
        if mapped_t == "form_handler" and "form_handler" not in tier_info["allowed_tools"]:
            return False, "Form builder tools are only available on Business and Enterprise tiers. Please upgrade your subscription."

    # 3. Check knowledge base source types and limits
    kb_sources = data.get("knowledgeBase") or data.get("knowledge_base") or config_obj.get("knowledgeBase") or config_obj.get("knowledge_base") or []
    kb_entries: list[dict[str, Any]] = []
    if isinstance(kb_sources, list):
        kb_entries = [kb for kb in kb_sources if isinstance(kb, dict)]
    elif isinstance(kb_sources, dict):
        def _kb_values(value):
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item or "").strip()]
            if str(value or "").strip():
                return [str(value).strip()]
            return []

        web_values = _kb_values(
            kb_sources.get("web_urls")
            or kb_sources.get("webUrls")
            or kb_sources.get("web_url")
            or kb_sources.get("webUrl")
        )
        onedrive_values = _kb_values(
            kb_sources.get("onedrive_folder_ids")
            or kb_sources.get("onedriveFolderIds")
            or kb_sources.get("onedrive_folder_id")
            or kb_sources.get("onedriveFolderId")
            or kb_sources.get("folder_ids")
            or kb_sources.get("folderIds")
            or kb_sources.get("folder_id")
            or kb_sources.get("folderId")
        )
        sharepoint_values = _kb_values(
            kb_sources.get("sharepoint_folder_ids")
            or kb_sources.get("sharepointFolderIds")
            or kb_sources.get("sharepoint_folder_id")
            or kb_sources.get("sharepointFolderId")
            or kb_sources.get("sharepoint_site_url")
            or kb_sources.get("sharepointSiteUrl")
        )
        kb_entries = [
            *({"type": "website", "value": value} for value in web_values),
            *({"type": "onedrive", "value": value} for value in onedrive_values),
            *({"type": "sharepoint", "value": value} for value in sharepoint_values),
        ]
        if not kb_entries and (kb_sources.get("value") or kb_sources.get("url")):
            kb_entries = [kb_sources]

    website_count = 0
    for kb in kb_entries:
        kb_type = str(kb.get("type") or "website").lower()
        kb_value = str(kb.get("value") or kb.get("url") or "").strip()
        if not kb_value:
            continue
        if kb_type == "website":
            website_count += 1
            if "website" not in tier_info["allowed_kb"]:
                return False, "Website knowledge base sources are not available on your current plan."
        elif kb_type in {"document", "documents", "file", "files"}:
            if "documents" not in tier_info["allowed_kb"]:
                return False, "Document uploads are only available on Business and Enterprise tiers. Please upgrade your subscription."
        elif kb_type == "onedrive":
            if "onedrive" not in tier_info["allowed_kb"]:
                return False, "OneDrive synchronization is only available on Business and Enterprise tiers. Please upgrade your subscription."
        elif kb_type == "sharepoint":
            if "sharepoint" not in tier_info["allowed_kb"]:
                return False, "SharePoint synchronization is not available on your current plan. Please upgrade your subscription."

    websites_limit = tier_info.get("websites_limit")
    if websites_limit is not None and website_count > int(websites_limit):
        return False, f"Website limit reached for your active {tier.capitalize()} plan (Limit: {websites_limit}). Please upgrade your plan."

    if not is_edit:
        kb_limit = tier_info.get("knowledge_bases_limit")
        if kb_limit is not None:
            agent_count = conn.execute("SELECT COUNT(*) FROM custom_agents WHERE company_id = ?", (company_id,)).fetchone()[0]
            if agent_count >= int(kb_limit):
                return False, f"Knowledge base limit reached for your active {tier.capitalize()} plan (Limit: {kb_limit}). Please upgrade your plan."

    return True, None



def _frontend_base_url(app) -> str:
    if app.debug:
        return "http://localhost:3000"

    try:
        from flask import has_request_context, request
        if has_request_context():
            origin = request.headers.get("Origin")
            if origin:
                return origin.rstrip("/")
            referrer = request.headers.get("Referer")
            if referrer:
                parsed = urlparse(referrer)
                return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
    except Exception:
        pass

    raw = (app.config.get("FRONTEND_URL") or "https://aisleagents.raccoon-ai.com").strip()
    if not raw.startswith(("http://", "https://")):
        raw = f"https://{raw}"
    return raw.rstrip("/")


def _frontend_link(app, path: str, params: dict[str, Any] | None = None) -> str:
    base = _frontend_base_url(app)
    normalized_path = path if path.startswith("/") else f"/{path}"
    query = f"?{urlencode(params)}" if params else ""
    return f"{base}{normalized_path}{query}"


def _create_one_time_token(
    conn,
    *,
    purpose: str,
    subject_type: str,
    subject_id: int,
    expires_minutes: int | None = None,
) -> str:
    raw = secrets.token_urlsafe(32)
    token_h = _hash_token(raw)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    exp = (
        (datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)).replace(microsecond=0).isoformat()
        if expires_minutes is not None
        else "9999-12-31T23:59:59+00:00"
    )
    conn.execute(
        """
        INSERT INTO one_time_tokens (purpose, subject_type, subject_id, token_hash, expires_at, used_at, created_at)
        VALUES (?, ?, ?, ?, ?, NULL, ?)
        """,
        (purpose, subject_type, subject_id, token_h, exp, now),
    )
    return raw


def _is_expired(expires_at: str) -> bool:
    try:
        expiry = datetime.fromisoformat((expires_at or "").replace("Z", "+00:00"))
    except Exception:
        return True
    return expiry <= datetime.now(timezone.utc)


def _is_super_admin(conn, claims: dict[str, Any]) -> bool:
    if claims.get("is_super_admin") is not None:
        return bool(claims["is_super_admin"])
    if claims.get("role") != "admin":
        return False
    company_id = int(claims.get("company_id", 0) or 0)
    if company_id <= 0:
        return False
    company = fetch_one(conn, "SELECT domain FROM company WHERE id = ? LIMIT 1", (company_id,))
    if not company:
        return False
    domain = normalize_email(company["domain"])
    return domain in {"slt.com.lk", "intranet.slt.com.lk"}


from flask_mail import Message

def _mark_stale_running_ingestions(timeout_minutes: int = 10) -> None:
    stale_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)).isoformat()
    try:
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE kb_ingestion_jobs
                SET status = 'failed',
                    error_message = COALESCE(error_message, 'Ingestion was interrupted before completion. Please retry the sync.'),
                    finished_at = ?
                WHERE status = 'running'
                  AND requested_at < ?
                """,
                (utcnow_iso(), stale_cutoff),
            )
            conn.commit()
    except Exception as exc:
        print(f"WARN stale_ingestion_cleanup_failed: {exc}", flush=True)

def register_auth_routes(app, mail) -> None:
    global _mail_client, _app_instance
    _mail_client = mail
    _app_instance = app
    init_db()
    _mark_stale_running_ingestions()

    # Keep this in sync with the agent definitions in the frontend Dashboard.
    AGENT_LABELS: dict[str, str] = {
        "A": "Test Aisle Web",
        "B": "Innospace Assistant",
        "C": "SLT Digital Lab",
        "D": "SLT Embryo Agent",
        "E": "Internship Placement",
        "G": "SLTMNIT Assistant",
    }

    def send_notification_email(
        subject: str,
        recipients: list[str],
        body: str,
        details: list[tuple[str, str]] | dict[str, str] | None = None,
        cta_text: str = "Open Agent Store",
        cta_path: str = "/login",
    ) -> bool:
        # Reuse one safe notification path for all auth/admin emails.
        cta_url = _frontend_link(app, cta_path)

        deduped_recipients = []
        seen = set()
        for email in recipients:
            normalized = normalize_email(email or "")
            if normalized and normalized not in seen:
                seen.add(normalized)
                deduped_recipients.append(normalized)

        if not deduped_recipients:
            print(f"WARN: No recipients for notification '{subject}'")
            return False

        try:
            # Validate mail configuration
            mail_server = app.config.get("MAIL_SERVER")
            mail_username = app.config.get("MAIL_USERNAME")
            mail_password = app.config.get("MAIL_PASSWORD")
            
            if not mail_server or not mail_username or not mail_password:
                print(f"WARN: Email configuration incomplete. MAIL_SERVER={bool(mail_server)}, MAIL_USERNAME={bool(mail_username)}, MAIL_PASSWORD={bool(mail_password)}")
                print(f"DEBUG: Notification would be sent to: {', '.join(deduped_recipients)}")
                return False

            kwargs = {
                "subject": subject,
                "recipients": deduped_recipients,
            }
            sender = get_sender_identity(app)
            if sender:
                kwargs["sender"] = sender
            else:
                mail_default_sender = app.config.get("MAIL_DEFAULT_SENDER")
                if mail_default_sender:
                    kwargs["sender"] = mail_default_sender
                else:
                    print(f"WARN: No sender email configured for notification '{subject}'")
                    return False

            msg = Message(**kwargs)
            msg.body = build_email_text(
                subject=subject,
                intro_text=body,
                details=details,
                cta_text=cta_text,
                cta_url=cta_url,
            )
            msg.html = build_email_html(
                app,
                subject=subject,
                intro_text=body,
                details=details,
                cta_text=cta_text,
                cta_url=cta_url,
            )
            attach_email_logo(app, msg)
            
            print(f"DEBUG: Attempting to send email '{subject}' to {deduped_recipients} from {kwargs.get('sender', 'unknown')}")
            mail.send(msg)
            print(f"DEBUG: Successfully sent notification '{subject}' to {', '.join(deduped_recipients)}")
            return True
        except Exception as e:
            print(f"ERROR: Failed to send notification '{subject}' to {deduped_recipients}: {e}")
            import traceback
            traceback.print_exc()
            return False

    def notify_billing_event(conn, company_id: int, subject: str, intro_text: str, details: list[tuple[str, str]]) -> bool:
        company_admins = []
        try:
            admin_rows = conn.execute(
                "SELECT email FROM users WHERE company_id = ? AND role = 'admin' AND is_active = 1",
                (company_id,)
            ).fetchall()
            company_admins = [r["email"] for r in admin_rows if r["email"]]
        except Exception as e:
            print(f"Error fetching company admins for email notify: {e}")

        super_admins = []
        try:
            super_admin_domain = app.config.get("SUPER_ADMIN_DOMAIN") or "intranet.slt.com.lk"
            super_admin_domain = super_admin_domain.strip().lower()
            super_admin_rows = conn.execute(
                """
                SELECT u.email FROM users u
                JOIN company c ON c.id = u.company_id
                WHERE u.role = 'admin' AND u.is_active = 1 AND lower(c.domain) = ?
                """,
                (super_admin_domain,)
            ).fetchall()
            super_admins = [r["email"] for r in super_admin_rows if r["email"]]
        except Exception as e:
            print(f"Error fetching super admins for email notify: {e}")

        recipients = list(set(company_admins + super_admins))
        if not recipients:
            sender = app.config.get("MAIL_DEFAULT_SENDER")
            if sender:
                recipients = [sender]

        return send_notification_email(
            subject=subject,
            recipients=recipients,
            body=intro_text,
            details=details,
            cta_text="Go to Dashboard",
            cta_path="/dashboard"
        )

    @app.route("/api/auth/login", methods=["POST", "OPTIONS"])
    def api_auth_login():
        # CORS preflight
        if request.method == "OPTIONS":
            return ("", 200)
        body = request.get_json(silent=True) or {}
        email = normalize_email(body.get("email", ""))
        password = body.get("password", "") or ""
        full_name = (body.get("full_name") or "").strip()

        if not is_valid_email(email):
            return _json_error(400, "invalid_email", "Please enter a valid email address.")
        if not password or len(password) < 8:
            return _json_error(400, "invalid_password", "Password must be at least 8 characters.")

        domain = get_email_domain(email)
        if not domain:
            return _json_error(400, "invalid_email", "Please enter a valid email address.")

        def _company_password_matches_any_admin(conn, company_id: int, raw_password: str) -> bool:
            admin_rows = conn.execute(
                """
                SELECT password_hash
                FROM users
                WHERE company_id = ? AND role = 'admin' AND is_active = 1
                """,
                (company_id,),
            ).fetchall()
            for admin_row in admin_rows:
                if check_password_hash(admin_row["password_hash"], raw_password):
                    return True
            return False

        with get_conn() as conn:
            user = fetch_one(conn, "SELECT * FROM users WHERE email = ? LIMIT 1", (email,))
            if user:
                company = fetch_one(conn, "SELECT * FROM company WHERE id = ? LIMIT 1", (int(user["company_id"]),))
            else:
                if is_public_email_domain(domain):
                    # Public-domain workspaces may be keyed by:
                    # 1) the provider domain (legacy: gmail.com),
                    # 2) full admin email (e.g., admin@gmail.com),
                    # 3) any stored key ending with @gmail.com.
                    raw_candidates = conn.execute(
                        """
                        SELECT *
                        FROM company
                        WHERE lower(domain) = ?
                           OR lower(domain) = ?
                           OR lower(domain) LIKE ?
                        """,
                        (domain, email, f"%@{domain}"),
                    ).fetchall()
                    seen_company_ids = set()
                    candidates = []
                    for candidate in raw_candidates:
                        cid = int(candidate["id"])
                        if cid in seen_company_ids:
                            continue
                        seen_company_ids.add(cid)
                        candidates.append(candidate)

                    if len(candidates) == 1:
                        company = candidates[0]
                    elif len(candidates) > 1:
                        # Disambiguate by submitted company password.
                        matching = [
                            candidate
                            for candidate in candidates
                            if _company_password_matches_any_admin(conn, int(candidate["id"]), password)
                        ]
                        if len(matching) == 1:
                            company = matching[0]
                        elif len(matching) > 1:
                            return _json_error(
                                409,
                                "workspace_ambiguous",
                                "Multiple workspaces match this public email and password. Please ask your admin to add your user first.",
                            )
                        else:
                            return _json_error(
                                401,
                                "invalid_company_password",
                                "Enter valid credentials or register your company.",
                            )
                    else:
                        company = None
                else:
                    company = fetch_one(conn, "SELECT * FROM company WHERE domain = ? LIMIT 1", (domain,))
                if not company:
                    # User+company does not exist -> redirect to signup on frontend.
                    return _json_error(
                        404,
                        "company_not_found",
                        "No company workspace found for this email domain. Please register your company.",
                    )
            # Allow login when either:
            # 1) the supplied password matches the user's own password_hash (per-user password), OR
            # 2) the supplied password matches the workspace/company password (legacy/shared admin password)
            if user:
                # If user exists, first check the user's own password
                user_pw_hash = user["password_hash"] if "password_hash" in user.keys() else ""
                if not check_password_hash(user_pw_hash, password):
                    # Fallback to company-level password if user password did not match
                    if not _company_password_matches_any_admin(conn, int(company["id"]), password):
                        return _json_error(
                            401,
                            "invalid_credentials",
                            "Invalid email or password.",
                        )
            else:
                # No user record — require company password to match an admin
                if not _company_password_matches_any_admin(conn, int(company["id"]), password):
                    return _json_error(
                        401,
                        "invalid_company_password",
                        "Enter a valid company password or register your company.",
                    )

        # ... existing code ...

            if user:
                print(f"DEBUG: Found user {email}, checking password...")
                if int(user["is_active"]) != 1:
                    print("DEBUG: User inactive")
                    return _json_error(403, "inactive_user", "This account is inactive.")
                if int(user["must_reset_password"]) == 1:
                    return _json_error(
                        403,
                        "password_reset_required",
                        "Password reset is required before login. Please use your reset link.",
                    )
                if int(user["company_id"]) != int(company["id"]):
                    print("DEBUG: Tenant mismatch")
                    return _json_error(403, "tenant_mismatch", "This account is not associated with this workspace.")
                
                is_super_admin_flag = _is_super_admin(conn, {
                    "role": user["role"],
                    "company_id": int(user["company_id"]),
                })
                token = _encode_token(
                    app,
                    {
                        "user_id": int(user["id"]),
                        "email": user["email"],
                        "role": user["role"],
                        "company_id": int(user["company_id"]),
                        "is_super_admin": is_super_admin_flag,
                    },
                )
                return jsonify(
                    {
                        "token": token,
                        "user": {
                            "id": int(user["id"]),
                            "email": user["email"],
                            "full_name": user["full_name"],
                            "role": user["role"],
                            "company_id": int(user["company_id"]),
                            "is_super_admin": is_super_admin_flag,
                        },
                        "company": {
                            "id": int(company["id"]),
                            "name": company["name"],
                            "domain": company["domain"],
                            "website": company["website"],
                            "industry": company["industry"],
                            "allowed_agents": company["allowed_agents"],
                            "subscription_tier": company["subscription_tier"],
                            "subscription_status": company["subscription_status"],
                            "subscription_expires_at": company["subscription_expires_at"],
                        },
                    }
                )
            
            # User not found in this company workspace
            return _json_error(
                401,
                "invalid_credentials",
                "Invalid email or password.",
            )

    @app.route("/api/auth/azure-login", methods=["POST", "OPTIONS"])
    def api_auth_azure_login():
        if request.method == "OPTIONS":
            return ("", 200)
        
        body = request.get_json(silent=True) or {}
        access_token = body.get("access_token")
        
        if not access_token:
            return _json_error(400, "missing_token", "Microsoft access token is required.")
        
        # Verify Microsoft token
        profile = verify_microsoft_token(access_token)
        if not profile:
            print("DEBUG: Microsoft token verification failed (verify_microsoft_token returned None)")
            return _json_error(401, "invalid_token", "Invalid Microsoft access token.")
        
        email = profile["email"]
        display_name = profile["display_name"]
        domain = get_email_domain(email)
        
        print(f"DEBUG: Azure AD SSO login attempt. email={email}, domain={domain}, SUPER_ADMIN_DOMAIN={SUPER_ADMIN_DOMAIN}")
        
        allowed_super_admin_domains = {"slt.com.lk", "intranet.slt.com.lk"}
        
        if domain not in allowed_super_admin_domains:
            print(f"DEBUG: Rejecting login because domain '{domain}' is not in allowed domains: {allowed_super_admin_domains}")
            return _json_error(403, "forbidden", "Azure AD login is restricted to Super Admins only.")
            
        with get_conn() as conn:
            user = fetch_one(conn, "SELECT * FROM users WHERE email = ? LIMIT 1", (email,))
            
            if not user:
                # Auto-provision the super admin user
                # 1. Fetch super admin company ID matching the user's domain
                company = fetch_one(conn, "SELECT * FROM company WHERE lower(domain) = ? LIMIT 1", (domain,))
                if not company:
                    # Fallback/safeguard: Create Sri Lanka Telecom company if it doesn't exist
                    cur = conn.execute(
                        """
                        INSERT INTO company (name, domain, subscription_tier, subscription_status, created_at)
                        VALUES (?, ?, 'free', 'inactive', ?)
                        """,
                        ("Sri Lanka Telecom", domain, utcnow_iso())
                    )
                    company_id = int(cur.lastrowid)
                    company = fetch_one(conn, "SELECT * FROM company WHERE id = ? LIMIT 1", (company_id,))
                else:
                    company_id = int(company["id"])
                
                # 2. Insert new admin user
                dummy_pw_hash = generate_password_hash(secrets.token_hex(16))
                cur_user = conn.execute(
                    """
                    INSERT INTO users (email, password_hash, full_name, role, company_id, is_active, created_at)
                    VALUES (?, ?, ?, 'admin', ?, 1, ?)
                    """,
                    (email, dummy_pw_hash, display_name, company_id, utcnow_iso())
                )
                user_id = int(cur_user.lastrowid)
                user = fetch_one(conn, "SELECT * FROM users WHERE id = ? LIMIT 1", (user_id,))
                conn.commit()
            
            # If user exists but is inactive
            if int(user["is_active"]) != 1:
                return _json_error(403, "inactive_user", "This account is inactive.")
            
            # Confirm user belongs to super admin company
            company = fetch_one(conn, "SELECT * FROM company WHERE id = ? LIMIT 1", (int(user["company_id"]),))
            if not company or normalize_email(company["domain"]) not in allowed_super_admin_domains:
                return _json_error(403, "forbidden", "Only users associated with the Super Admin workspace can log in.")
                
            is_super_admin_flag = _is_super_admin(conn, {
                "role": user["role"],
                "company_id": int(user["company_id"]),
            })
            
            token = _encode_token(
                app,
                {
                    "user_id": int(user["id"]),
                    "email": user["email"],
                    "role": user["role"],
                    "company_id": int(user["company_id"]),
                    "is_super_admin": is_super_admin_flag,
                },
            )
            
            return jsonify(
                {
                    "token": token,
                    "user": {
                        "id": int(user["id"]),
                        "email": user["email"],
                        "full_name": user["full_name"],
                        "role": user["role"],
                        "company_id": int(user["company_id"]),
                        "is_super_admin": is_super_admin_flag,
                    },
                    "company": {
                        "id": int(company["id"]),
                        "name": company["name"],
                        "domain": company["domain"],
                        "website": company["website"],
                        "industry": company["industry"],
                        "allowed_agents": company["allowed_agents"],
                        "subscription_tier": company["subscription_tier"],
                        "subscription_status": company["subscription_status"],
                        "subscription_expires_at": company["subscription_expires_at"],
                    },
                }
            )



    @app.route("/api/auth/company-registrations", methods=["GET"])
    @require_auth(app)
    def get_company_registrations():
        """Get company registration requests (super admin only)."""
        scope = (request.args.get("scope") or "processed").strip().lower()
        if scope not in {"processed", "pending", "all"}:
            return _json_error(
                400,
                "invalid_scope",
                "Scope must be one of: processed, pending, all",
            )

        where_clause = "WHERE status IN ('approved', 'rejected')"
        if scope == "pending":
            where_clause = "WHERE status = 'pending'"
        elif scope == "all":
            where_clause = ""

        with get_conn() as conn:
            if not _is_super_admin(conn, request.auth):
                return _json_error(403, "forbidden", "Super admin access required")

            requests = conn.execute(
                f"""
                SELECT
                  crr.id,
                  crr.company_name,
                  crr.domain,
                  crr.website,
                  crr.industry,
                  crr.requested_agents,
                  crr.admin_full_name,
                  crr.admin_email,
                  crr.requested_at,
                  crr.reviewed_at,
                  crr.status,
                  crr.remark
                FROM company_registration_requests crr
                {where_clause}
                ORDER BY crr.requested_at DESC
                """
            ).fetchall()


        return jsonify([dict(r) for r in requests])

    @app.route("/api/auth/company-registrations/<int:request_id>", methods=["PATCH"])
    @require_auth(app)
    def update_company_registration(request_id):
        """Approve or reject a company registration request (super admin only)."""
        data = request.get_json() or {}
        action = data.get("action")  # "approve" or "reject"
        remark = (data.get("remark") or "").strip()
        approved_agents = data.get("approved_agents") # List of agent codes
        if action not in ["approve", "reject"]:
            return _json_error(400, "invalid_action", "Action must be 'approve' or 'reject'")
        if action == "reject" and not remark:
            return _json_error(400, "missing_remark", "Remark is required when rejecting a company registration.")

        with get_conn() as conn:
            if not _is_super_admin(conn, request.auth):
                return _json_error(403, "forbidden", "Super admin access required")

            reg = fetch_one(
                conn,
                "SELECT * FROM company_registration_requests WHERE id = ? LIMIT 1",
                (request_id,),
            )
            if not reg:
                return _json_error(404, "not_found", "Company registration request not found")
            if reg["status"] != "pending":
                return _json_error(400, "invalid_status", "Request has already been processed")

            new_status = "approved" if action == "approve" else "rejected"

            if action == "approve":
                existing_company = fetch_one(
                    conn,
                    "SELECT id FROM company WHERE domain = ? LIMIT 1",
                    (reg["domain"],),
                )
                if existing_company:
                    return _json_error(
                        409,
                        "company_exists",
                        "A company already exists for this email domain.",
                    )

                existing_user = fetch_one(
                    conn,
                    "SELECT id FROM users WHERE email = ? LIMIT 1",
                    (reg["admin_email"],),
                )
                if existing_user:
                    return _json_error(
                        409,
                        "user_exists",
                        "A user with this admin email already exists.",
                    )

                try:
                    cur = conn.execute(
                        """
                        INSERT INTO company (name, domain, website, industry, allowed_agents, created_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            reg["company_name"],
                            reg["domain"],
                            reg["website"],
                            reg["industry"],
                            json.dumps(approved_agents) if isinstance(approved_agents, list) else reg["requested_agents"],
                            utcnow_iso(),
                        ),
                    )
                    company_id = int(cur.lastrowid)

                    conn.execute(
                        """
                        INSERT INTO users (email, password_hash, full_name, role, company_id, is_active, must_reset_password, created_at)
                        VALUES (?, ?, ?, 'admin', ?, 1, 1, ?)
                        """,
                        (
                            reg["admin_email"],
                            reg["admin_password_hash"],
                            reg["admin_full_name"],
                            company_id,
                            utcnow_iso(),
                        ),
                    )
                    created_admin_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
                    reset_token = _create_one_time_token(
                        conn,
                        purpose=INITIAL_ADMIN_PASSWORD_RESET_PURPOSE,
                        subject_type="user",
                        subject_id=created_admin_id,
                        expires_minutes=None,
                    )
                    reset_link = _frontend_link(app, "/reset-password", {"token": reset_token})
                except sqlite3.IntegrityError as e:
                    conn.rollback()
                    print(f"ERROR: IntegrityError during company approval: {e}")
                    return _json_error(
                        409,
                        "conflict",
                        f"Conflict: {str(e)}",
                    )
                except Exception as e:
                    conn.rollback()
                    print(f"ERROR: Unexpected error during company approval: {e}")
                    return _json_error(500, "internal_error", str(e))

            conn.execute(
                """
                UPDATE company_registration_requests
                SET status = ?, remark = ?, reviewed_by_admin_id = ?, reviewed_at = datetime('now')
                WHERE id = ?
                """,
                (new_status, remark if action == "reject" else None, request.auth["user_id"], request_id),
            )
            conn.commit()

        if action == "approve":
            body_lines = [
                f"Congratulations! Your company registration request for {reg['company_name']} has been approved.",
                "",
                "To complete your account setup, please set your company password using the secure link below:",
                f"{reset_link}",
                "",
                "This link will expire after first use. Once your password is set, you can log in to your workspace.",
            ]

            send_notification_email(
                subject="Set your company password",
                recipients=[reg["admin_email"]],
                body="\n".join(body_lines),
            )
        else:
            body_lines = [
                f"Your company registration request for {reg['company_name']} (ID: {reg['id']}) was rejected.",
                "",
                f"Remark: {remark}",
            ]
            send_notification_email(
                subject="Company Registration Rejected",
                recipients=[reg["admin_email"]],
                body="\n".join(body_lines),
            )

        return jsonify(
            {
                "status": "success",
                "message": (
                    "Company registration approved successfully"
                    if action == "approve"
                    else "Company registration rejected"
                ),
            }
        )

    @app.route("/api/auth/companies/<int:company_id>", methods=["DELETE"])
    @require_auth(app)
    def delete_company(company_id):
        """Delete a company and all its associated data (super admin only)."""
        with get_conn() as conn:
            if not _is_super_admin(conn, request.auth):
                return _json_error(403, "forbidden", "Super admin access required")

            # Check if company exists
            company = fetch_one(conn, "SELECT name, domain FROM company WHERE id = ? LIMIT 1", (company_id,))
            if not company:
                return _json_error(404, "not_found", "Company not found")

            company_name = company["name"]

            try:
                # 1. Delete workflow files for custom agents
                custom_agents_rows = conn.execute(
                    "SELECT name FROM custom_agents WHERE company_id = ?", (company_id,)
                ).fetchall()
                for agent in custom_agents_rows:
                    try:
                        wp_path = workflow_file_path(company_id, agent["name"])
                        if wp_path and wp_path.exists():
                            wp_path.unlink()
                    except Exception as e:
                        print(f"WARN: Failed to delete workflow file for custom agent '{agent['name']}': {e}")

                # 2. Delete workflow files for standard agents
                standard_agents_rows = conn.execute(
                    "SELECT agent_name FROM agents WHERE company_id = ?", (company_id,)
                ).fetchall()
                for agent in standard_agents_rows:
                    try:
                        wp_path = workflow_file_path(company_id, agent["agent_name"])
                        if wp_path and wp_path.exists():
                            wp_path.unlink()
                    except Exception as e:
                        print(f"WARN: Failed to delete workflow file for standard agent '{agent['agent_name']}': {e}")

                # 3. Delete all related rows in dependency-safe order

                # Chat messages and sessions
                conn.execute("DELETE FROM custom_agent_chat_messages WHERE company_id = ?", (company_id,))
                conn.execute("DELETE FROM custom_agent_chat_sessions WHERE company_id = ?", (company_id,))

                # Knowledge base
                conn.execute("DELETE FROM kb_ingestion_jobs WHERE company_id = ?", (company_id,))
                conn.execute("DELETE FROM kb_sources WHERE company_id = ?", (company_id,))
                conn.execute("DELETE FROM kb_details WHERE company_id = ?", (company_id,))

                # Agent creation requests + responses
                conn.execute(
                    "DELETE FROM agent_creation_responses WHERE request_id IN "
                    "(SELECT id FROM agent_creation_requests WHERE company_id = ?)",
                    (company_id,),
                )
                conn.execute("DELETE FROM agent_creation_requests WHERE company_id = ?", (company_id,))

                # Agent resources and configs
                conn.execute("DELETE FROM agent_resources WHERE company_id = ?", (company_id,))
                conn.execute("DELETE FROM apis WHERE company_id = ?", (company_id,))
                conn.execute("DELETE FROM custom_agents WHERE company_id = ?", (company_id,))
                conn.execute("DELETE FROM agents WHERE company_id = ?", (company_id,))

                # Company-level data
                _delete_if_column_exists(conn, "company_agent_requests", "company_id", company_id)
                conn.execute("DELETE FROM company_chat_usage WHERE company_id = ?", (company_id,))
                conn.execute("DELETE FROM user_chat_quota_overrides WHERE company_id = ?", (company_id,))
                conn.execute("DELETE FROM user_chat_usage WHERE company_id = ?", (company_id,))
                _delete_if_column_exists(conn, "company_member_allowlist", "company_id", company_id)

                # Payments
                _delete_if_column_exists(conn, "payments_legacy", "company_id", company_id)
                conn.execute("DELETE FROM pending_payments WHERE company_id = ?", (company_id,))

                # Current billing tables use workspace_id; legacy tables may use company_id.
                _delete_if_column_exists(conn, "payments", "workspace_id", company_id)
                _delete_if_column_exists(conn, "payments", "company_id", company_id)
                _delete_if_column_exists(conn, "subscriptions", "workspace_id", company_id)
                _delete_if_column_exists(conn, "subscriptions", "company_id", company_id)

                # Login requests and user invitations
                conn.execute("DELETE FROM login_requests WHERE company_id = ?", (company_id,))
                conn.execute("DELETE FROM user_invitations WHERE company_id = ?", (company_id,))

                # Associated company registration requests (by matching domain)
                if company["domain"]:
                    conn.execute("DELETE FROM company_registration_requests WHERE domain = ?", (company["domain"],))

                # Users
                conn.execute("DELETE FROM users WHERE company_id = ?", (company_id,))

                # Legacy companies table (has company_id column, separate from company)
                _delete_if_column_exists(conn, "companies", "company_id", company_id)

                # Finally delete the company itself
                conn.execute("DELETE FROM company WHERE id = ?", (company_id,))

                conn.commit()
                print(f"INFO: Company '{company_name}' (id={company_id}) deleted successfully by super admin.")
                return jsonify({"status": "success", "message": f"Company '{company_name}' and all associated data deleted successfully"})

            except Exception as e:
                conn.rollback()
                import traceback
                print(f"ERROR: Failed to delete company {company_id}: {e}")
                traceback.print_exc()
                return _json_error(500, "internal_error", f"Failed to delete company: {str(e)}")

    @app.post("/api/auth/reset-database")
    @require_auth(app)
    def api_auth_reset_database():
        claims = request.auth  # type: ignore[attr-defined]
        
        with get_conn() as conn:
            # Strictly verify that caller is a super admin
            if not _is_super_admin(conn, claims):
                return _json_error(403, "forbidden", "Only Super Admins can reset the database.")
            
            try:
                allowed_domains = ("slt.com.lk", "intranet.slt.com.lk")
                
                # Fetch super admin company IDs to ensure we don't delete their workflow files
                admin_companies = conn.execute(
                    "SELECT id FROM company WHERE lower(domain) IN (?, ?)",
                    allowed_domains
                ).fetchall()
                admin_company_ids = {c["id"] for c in admin_companies}
                
                # 1. Clean up non-admin generated workflow files
                from pathlib import Path
                workflows_dir = Path(__file__).resolve().parent / "generated_workflows"
                if workflows_dir.is_dir():
                    for path in workflows_dir.glob("*.py"):
                        try:
                            parts = path.name.split("_", 1)
                            if len(parts) > 1 and parts[0].isdigit():
                                cid = int(parts[0])
                                if cid not in admin_company_ids:
                                    path.unlink(missing_ok=True)
                        except Exception as file_e:
                            print(f"Warning: Failed to delete workflow file {path}: {file_e}")
                
                # 2. Execute table cleans in correct order (child/dependency tables first)
                
                # Chat messages and sessions
                conn.execute("DELETE FROM custom_agent_chat_messages")
                conn.execute("DELETE FROM custom_agent_chat_sessions")
                
                # Knowledge base details, sources, and ingestion jobs
                conn.execute("DELETE FROM kb_ingestion_jobs")
                conn.execute("DELETE FROM kb_sources")
                conn.execute("DELETE FROM kb_details")
                
                # Agent creation requests and responses
                conn.execute("DELETE FROM agent_creation_responses")
                conn.execute("DELETE FROM agent_creation_requests")
                
                # Agent resources, apis, custom agents, and agents
                conn.execute("DELETE FROM agent_resources")
                conn.execute("DELETE FROM apis")
                conn.execute("DELETE FROM custom_agents")
                conn.execute("DELETE FROM agents")
                
                # Company level chat usage, quotas, overrides
                conn.execute("DELETE FROM company_chat_usage")
                conn.execute("DELETE FROM user_chat_quota_overrides")
                conn.execute("DELETE FROM user_chat_usage")
                
                # Payments and pending payments
                _delete_all_if_table_exists(conn, "payments")
                conn.execute("DELETE FROM pending_payments")
                
                # Tokens and invites
                conn.execute("DELETE FROM one_time_tokens")
                conn.execute("DELETE FROM login_requests")
                conn.execute("DELETE FROM user_invitations")
                
                # Company registration requests (clear non-super-admin domains)
                conn.execute(
                    """
                    DELETE FROM company_registration_requests
                    WHERE lower(domain) NOT IN (?, ?)
                    """,
                    allowed_domains
                )
                
                # Support messages
                conn.execute("DELETE FROM contact_messages")
                
                # Optional/legacy tables
                for table in ["company_agent_requests", "company_member_allowlist", "payments_legacy"]:
                    _delete_all_if_table_exists(conn, table)
                
                # Subscriptions (keep only super admin subscriptions).
                _delete_except_column_values_if_exists(conn, "subscriptions", "workspace_id", admin_company_ids)
                _delete_except_column_values_if_exists(conn, "subscriptions", "company_id", admin_company_ids)
                
                # Companies mapping (keep only super admin companies)
                conn.execute(
                    """
                    DELETE FROM companies WHERE company_id NOT IN (
                        SELECT id FROM company WHERE lower(domain) IN (?, ?)
                    )
                    """,
                    allowed_domains
                )
                
                # Delete all users EXCEPT active super admin admins
                conn.execute(
                    """
                    DELETE FROM users WHERE id NOT IN (
                        SELECT u.id FROM users u
                        JOIN company c ON c.id = u.company_id
                        WHERE u.role = 'admin' AND lower(c.domain) IN (?, ?)
                    )
                    """,
                    allowed_domains
                )
                
                # Delete all companies EXCEPT allowed super admin domains
                conn.execute(
                    """
                    DELETE FROM company WHERE lower(domain) NOT IN (?, ?)
                    """,
                    allowed_domains
                )
                
                conn.commit()
                print("INFO: Database successfully reset to clean super admin state.")
                return jsonify({"status": "success", "message": "Database successfully reset to clean super admin state."})
                
            except Exception as reset_e:
                conn.rollback()
                import traceback
                print(f"ERROR: Failed to reset database: {reset_e}")
                traceback.print_exc()
                return _json_error(500, "internal_error", f"Failed to reset database: {str(reset_e)}")

    @app.route("/api/auth/signup", methods=["POST", "OPTIONS"])
    def api_auth_signup():
        # CORS preflight
        if request.method == "OPTIONS":
            return ("", 200)
        body = request.get_json(silent=True) or {}
        company_name = (body.get("company_name") or "").strip()
        admin_full_name = (body.get("admin_full_name") or "").strip()
        admin_email = normalize_email(body.get("admin_email", ""))
        password = generate_temp_password()
        website = (body.get("website") or "").strip() or None
        industry = (body.get("industry") or "").strip() or None
        raw_requested_agents = body.get("requested_agents") or []
        requested_agents: list[str] = []
        if isinstance(raw_requested_agents, list):
            requested_agents = [
                str(item).strip()
                for item in raw_requested_agents
                if str(item).strip()
            ]
        requested_agents_json = json.dumps(requested_agents) if requested_agents else None

        if not company_name:
            return _json_error(400, "missing_company_name", "Company name is required.")
        if not admin_full_name:
            return _json_error(400, "missing_admin_name", "Admin full name is required.")
        if not is_valid_email(admin_email):
            return _json_error(400, "invalid_email", "Please enter a valid admin email address.")

        domain = get_email_domain(admin_email)
        if not domain:
            return _json_error(400, "invalid_email", "Please enter a valid admin email address.")
        company_domain_key = admin_email if is_public_email_domain(domain) else domain

        with get_conn() as conn:
            existing_company = fetch_one(
                conn,
                "SELECT * FROM company WHERE domain = ? LIMIT 1",
                (company_domain_key,),
            )
            if existing_company:
                return _json_error(
                    409,
                    "company_exists",
                    "A company already exists for this email domain. Please login instead.",
                )

            existing_user = fetch_one(
                conn,
                "SELECT * FROM users WHERE email = ? LIMIT 1",
                (admin_email,),
            )
            if existing_user:
                return _json_error(
                    409,
                    "user_exists",
                    "A user with this email already exists. Please login.",
                )

            existing_pending = fetch_one(
                conn,
                """
                SELECT id FROM company_registration_requests
                WHERE status = 'pending' AND (domain = ? OR admin_email = ?)
                LIMIT 1
                """,
                (company_domain_key, admin_email),
            )
            if existing_pending:
                return _json_error(
                    409,
                    "registration_pending",
                    "A company registration request is already pending for this domain/admin email.",
                )

            if domain != SUPER_ADMIN_DOMAIN:
                password_hash = generate_password_hash(password)
                conn.execute(
                    """
                    INSERT INTO company_registration_requests
                    (company_name, domain, website, industry, requested_agents, admin_full_name, admin_email, admin_password_hash, requested_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                    """,
                    (
                        company_name,
                        company_domain_key,
                        website,
                        industry,
                        requested_agents_json,
                        admin_full_name,
                        admin_email,
                        password_hash,
                        utcnow_iso(),
                    ),
                )
                conn.commit()

                # Notify all active super admins about a pending company registration.
                super_admin_users = conn.execute(
                    """
                    SELECT u.email
                    FROM users u
                    JOIN company c ON c.id = u.company_id
                    WHERE u.role = 'admin' AND u.is_active = 1 AND c.domain = ?
                    """,
                    (SUPER_ADMIN_DOMAIN,),
                ).fetchall()
                super_admin_emails = [row["email"] for row in super_admin_users]

                # Include any requested agents in the notification email to super admins.
                requested_agents_line = ""
                if requested_agents:
                    labels = [f"{AGENT_LABELS.get(code, code)} (ID: {code})" for code in requested_agents]
                    requested_agents_line = "Requested agents: " + ", ".join(labels) + "\n"

                body_lines = [
                    "A new company registration request is pending approval.",
                    "",
                    f"Company: {company_name} ",
                    f"Requested Admin: {admin_full_name} ({admin_email})",
                ]
                if requested_agents_line:
                    body_lines.insert(3, requested_agents_line.rstrip())
                body_lines.append("")
                body_lines.append("Please review this request from the admin dashboard.")

                send_notification_email(
                    subject="New Company Registration Request",
                    recipients=super_admin_emails,
                    body="\n".join(body_lines),
                )

                return (
                    jsonify(
                        {
                            "status": "pending_approval",
                            "message": (
                                "Company registration request submitted. "
                                "Please wait for Sri Lanka Telecom super admin approval."
                            ),
                        }
                    ),
                    202,
                )

            now = utcnow_iso()
            try:
                cur = conn.execute(
                    """
                    INSERT INTO company (name, domain, website, industry, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (company_name, company_domain_key, website, industry, now),
                )
                company_id = int(cur.lastrowid)
            except sqlite3.OperationalError as e:
                # Handle transient locking errors gracefully so the API does not crash.
                msg = str(e).lower()
                if "database is locked" in msg:
                    return _json_error(
                        503,
                        "db_locked",
                        "The database is temporarily busy. Please try signing up again in a moment.",
                    )
                return _json_error(
                    500,
                    "db_error",
                    "A database error occurred while creating the company. Please try again later.",
                )
            except sqlite3.IntegrityError as e:
                # Only treat a UNIQUE violation on company.domain as "company_exists".
                msg = str(e).lower()
                if "unique constraint failed" in msg and "company.domain" in msg:
                    return _json_error(
                        409,
                        "company_exists",
                        "A company already exists for this email domain. Please login instead.",
                    )
                # Any other integrity error is a real server problem.
                return _json_error(
                    500,
                    "db_error",
                    "A database error occurred while creating the company. Please try again later.",
                )

            password_hash = generate_password_hash(password)
            cur2 = conn.execute(
                """
                INSERT INTO users (email, password_hash, full_name, role, company_id, is_active, created_at)
                VALUES (?, ?, ?, 'admin', ?, 1, ?)
                """,
                (admin_email, password_hash, admin_full_name, company_id, now),
            )
            user_id = int(cur2.lastrowid)

            token = _encode_token(
                app,
                {
                    "user_id": user_id,
                    "email": admin_email,
                    "role": "admin",
                    "company_id": company_id,
                },
            )
            return jsonify(
                {
                    "token": token,
                    "user": {
                        "id": user_id,
                        "email": admin_email,
                        "full_name": admin_full_name,
                        "role": "admin",
                        "company_id": company_id,
                    },
                    "company": {
                        "id": company_id,
                        "name": company_name,
                        "domain": company_domain_key,
                        "website": website,
                        "industry": industry,
                    },
                }
            ), 201


    @app.route("/api/auth/users", methods=["GET"])
    @require_auth(app)
    def get_users():
        """Get all users (admin only)"""
        if request.auth["role"] != "admin":
            return _json_error(403, "forbidden", "Admin access required")

        with get_conn() as conn:
            users = conn.execute("""
                SELECT id, email, full_name, role, company_id, is_active, created_at, updated_at
                FROM users
                WHERE company_id = ?
                ORDER BY created_at DESC
            """, (request.auth["company_id"],)).fetchall()
        
        return jsonify([dict(u) for u in users])

    @app.route("/api/auth/user-invitations", methods=["GET"])
    @require_auth(app)
    def get_user_invitations():
        """Get pending workspace member invitations (admin only)."""
        if request.auth["role"] != "admin":
            return _json_error(403, "forbidden", "Admin access required")

        with get_conn() as conn:
            invitations = conn.execute(
                """
                SELECT id, email, full_name, role, invited_at, status
                FROM user_invitations
                WHERE company_id = ? AND status = 'pending'
                ORDER BY invited_at DESC
                """,
                (request.auth["company_id"],),
            ).fetchall()

        return jsonify([dict(invite) for invite in invitations])

    @app.route("/api/auth/users", methods=["POST"])
    @require_auth(app)
    def create_user():
        """Create a pending user invitation and send a password set link (admin only)."""
        if request.auth["role"] != "admin":
            return _json_error(403, "forbidden", "Admin access required")

        body = request.get_json() or {}
        email = normalize_email(body.get("email", ""))
        full_name = (body.get("full_name") or "").strip()
        role = body.get("role", "member")

        if not is_valid_email(email):
            return _json_error(400, "invalid_email", "Please enter a valid email address.")
        if not full_name:
            return _json_error(400, "invalid_name", "Full name is required.")
        if role not in {"admin", "member"}:
            return _json_error(400, "invalid_role", "Role must be admin or member.")

        reset_token = None
        invitation_id = None
        company_name = "your workspace"
        
        with get_conn() as conn:
            existing = fetch_one(conn, "SELECT id FROM users WHERE email = ?", (email,))
            if existing:
                return _json_error(409, "user_exists", "User with this email already exists.")

            pending_invite = fetch_one(
                conn,
                """
                SELECT id
                FROM user_invitations
                WHERE company_id = ? AND email = ? AND status = 'pending'
                LIMIT 1
                """,
                (request.auth["company_id"], email),
            )
            if pending_invite:
                return _json_error(409, "invitation_exists", "An invitation for this email is already pending.")

            company_row = fetch_one(conn, "SELECT name FROM company WHERE id = ?", (request.auth["company_id"],))
            if company_row:
                company_name = company_row["name"]
            
            try:
                cur = conn.execute(
                    """
                    INSERT INTO user_invitations (
                        company_id, email, full_name, role, invited_by_user_id, status, invited_at
                    )
                    VALUES (?, ?, ?, ?, ?, 'pending', datetime('now'))
                    """,
                    (request.auth["company_id"], email, full_name, role, request.auth["user_id"])
                )
                invitation_id = cur.lastrowid

                reset_token = _create_one_time_token(
                    conn,
                    purpose=INITIAL_ADMIN_PASSWORD_RESET_PURPOSE,
                    subject_type="member_invitation",
                    subject_id=invitation_id,
                    expires_minutes=None,
                )
                conn.commit()
            except Exception as e:
                conn.rollback()
                print(f"ERROR: Failed to create user invitation in database: {e}")
                return _json_error(500, "db_error", f"Error creating invitation: {str(e)}")

        # Send invite email to set password (outside the database context)
        if reset_token and invitation_id:
            try:
                email_sent = send_notification_email(
                    subject="Set your company password",
                    recipients=[email],
                    body=(
                        f"Hi {full_name},\n\n"
                        f"You have been added to the {company_name} workspace on Agent Store.\n\n"
                        f"To complete your account setup, please set your company password using the secure link below:"
                    ),
                    details=[
                        ("Workspace", company_name),
                        ("Your Email", email),
                    ],
                    cta_text="Set Password",
                    cta_path=f"/reset-password?token={reset_token}",
                )
                if email_sent:
                    print(f"DEBUG: Invitation email sent to {email} for invitation {invitation_id}")
                else:
                    print(f"WARN: Failed to send invitation email to {email}")
            except Exception as e:
                print(f"ERROR: Exception while sending invitation email to {email}: {e}")
                import traceback
                traceback.print_exc()

        return jsonify({
            "id": invitation_id,
            "email": email,
            "full_name": full_name,
            "role": role,
            "company_id": request.auth["company_id"],
            "status": "pending",
            "invited_at": utcnow_iso(),
        }), 201

    @app.route("/api/auth/users/<int:user_id>", methods=["PATCH"])
    @require_auth(app)
    def update_user(user_id):
        """Update a user (admin only)"""
        if request.auth["role"] != "admin":
            return _json_error(403, "forbidden", "Admin access required")

        body = request.get_json() or {}
        
        updates = []
        params = []
        
        if "full_name" in body:
            updates.append("full_name = ?")
            params.append(body["full_name"])
            
        if "role" in body:
            updates.append("role = ?")
            params.append(body["role"])
            
        if "is_active" in body:
            updates.append("is_active = ?")
            params.append(int(body["is_active"]))

        if not updates:
            return jsonify({"message": "No updates provided"})

        updates.append("updated_at = datetime('now')")
        
        # Add user_id and company_id to params for WHERE clause
        params.append(user_id)
        params.append(request.auth["company_id"])

        with get_conn() as conn:
            conn.execute(
                f"""
                UPDATE users 
                SET {', '.join(updates)}
                WHERE id = ? AND company_id = ?
                """,
                tuple(params)
            )
            conn.commit()
            
            updated_user = fetch_one(
                conn, 
                "SELECT id, email, full_name, role, is_active FROM users WHERE id = ?",
                (user_id,)
            )

        return jsonify(dict(updated_user))

    @app.route("/api/auth/users/<int:user_id>", methods=["DELETE"])
    @require_auth(app)
    def delete_user(user_id):
        """Delete a user (admin only)"""
        if request.auth["role"] != "admin":
            return _json_error(403, "forbidden", "Admin access required")

        if user_id == request.auth["user_id"]:
            return _json_error(400, "invalid_action", "Cannot delete your own account.")

        with get_conn() as conn:
            conn.execute(
                "DELETE FROM users WHERE id = ? AND company_id = ?",
                (user_id, request.auth["company_id"])
            )
            conn.commit()

        return jsonify({"status": "success"})


    # ── /api/auth/me ────────────────────────────────────────────────────────

    @app.get("/api/auth/me")
    @require_auth(app)
    def api_auth_me():
        claims = request.auth  # type: ignore[attr-defined]
        user_id = int(claims.get("user_id", 0) or 0)
        company_id = int(claims.get("company_id", 0) or 0)

        with get_conn() as conn:
            user = fetch_one(
                conn,
                "SELECT id, email, full_name, role, company_id, is_active, must_reset_password, created_at FROM users WHERE id = ? LIMIT 1",
                (user_id,),
            )
            if not user or int(user["is_active"]) != 1:
                return _json_error(401, "invalid_user", "Invalid session. Please login again.")
            if int(user["company_id"]) != company_id:
                return _json_error(403, "tenant_mismatch", "Tenant mismatch.")

            company = fetch_one(
                conn,
                "SELECT id, name, domain, website, industry, allowed_agents, subscription_tier, subscription_status, subscription_expires_at, onboarding_completed, onboarding_seen_at, created_at FROM company WHERE id = ? LIMIT 1",
                (company_id,),
            )
            if not company:
                return _json_error(401, "invalid_company", "Invalid session. Please login again.")

            subscription = fetch_one(
                conn,
                "SELECT trial_used, trial_started_at, trial_expires_at FROM subscriptions WHERE workspace_id = ? LIMIT 1",
                (company_id,),
            )
            trial_used = int(subscription["trial_used"]) if subscription else 0
            trial_started_at = subscription["trial_started_at"] if subscription else None
            trial_expires_at = subscription["trial_expires_at"] if subscription else None
            
            is_super_admin_flag = _is_super_admin(conn, {
                "role": user["role"],
                "company_id": int(user["company_id"]),
            })
            whatsapp_entitlement = get_company_feature_entitlement(
                conn,
                company_id,
                WHATSAPP_FEATURE,
            )

            print(f"DEBUG: company keys: {list(company.keys())}")

        return jsonify(
            {
                "user": {
                    "id": int(user["id"]),
                    "email": user["email"],
                    "full_name": user["full_name"],
                    "role": user["role"],
                    "company_id": int(user["company_id"]),
                    "is_super_admin": is_super_admin_flag,
                    "must_reset_password": (
                        int(user["must_reset_password"])
                        if "must_reset_password" in user.keys()
                        else 0
                    ),
                    "created_at": user["created_at"],
                },
                "company": {
                    "id": int(company["id"]),
                    "name": company["name"],
                    "domain": company["domain"],
                    "website": company["website"],
                    "industry": company["industry"],
                    "allowed_agents": company["allowed_agents"] if "allowed_agents" in company.keys() else None,
                    "subscription_tier": company["subscription_tier"],
                    "subscription_status": company["subscription_status"],
                    "subscription_expires_at": company["subscription_expires_at"],
                    "onboarding_completed": int(company["onboarding_completed"] or 0),
                    "onboarding_seen_at": company["onboarding_seen_at"] if "onboarding_seen_at" in company.keys() else None,
                    "trial_used": trial_used,
                    "trial_started_at": trial_started_at,
                    "trial_expires_at": trial_expires_at,
                    "features": {
                        WHATSAPP_FEATURE: whatsapp_entitlement,
                    },
                    "created_at": company["created_at"],
                },
            }
        )

    @app.post("/api/auth/onboarding")
    @require_auth(app)
    def api_auth_onboarding():
        claims = request.auth  # type: ignore[attr-defined]
        user_id = int(claims.get("user_id", 0) or 0)
        company_id = int(claims.get("company_id", 0) or 0)

        payload = request.get_json() or {}
        option = payload.get("option")  # "trial", "free", "upgrade", "dismiss"

        with get_conn() as conn:
            company = fetch_one(conn, "SELECT onboarding_completed FROM company WHERE id = ?", (company_id,))
            if not company:
                return _json_error(404, "company_not_found", "Company not found.")

            # Check if user is an admin
            user = fetch_one(conn, "SELECT role FROM users WHERE id = ?", (user_id,))
            if not user or user["role"] != "admin":
                return _json_error(403, "forbidden", "Only workspace administrators can complete onboarding.")

            now = utcnow_iso()

            if option == "dismiss":
                conn.execute(
                    """
                    UPDATE company
                    SET onboarding_seen_at = COALESCE(onboarding_seen_at, ?)
                    WHERE id = ?
                    """,
                    (now, company_id),
                )
            elif option == "trial":
                subscription = fetch_one(conn, "SELECT trial_used FROM subscriptions WHERE workspace_id = ?", (company_id,))
                if subscription and int(subscription["trial_used"]) == 1:
                    return _json_error(400, "trial_already_used", "Free trial can only be activated once per workspace.")

                packages = load_packages_config(conn)
                free_package = packages.get("free") or {}
                trial_days = int(free_package.get("agent_expiry_days") or 7)
                expires_at = (datetime.now(timezone.utc) + timedelta(days=trial_days)).isoformat()

                # Update company
                conn.execute(
                    """
                    UPDATE company 
                    SET subscription_tier = ?, subscription_status = ?, subscription_expires_at = ?, onboarding_completed = 1, onboarding_seen_at = COALESCE(onboarding_seen_at, ?)
                    WHERE id = ?
                    """,
                    ("free", "trialing", expires_at, now, company_id)
                )

                # Update subscription record
                if subscription:
                    conn.execute(
                        """
                        UPDATE subscriptions
                        SET plan = ?, status = ?, trial_used = 1, trial_started_at = ?, trial_expires_at = ?, expires_at = ?, updated_at = ?
                        WHERE workspace_id = ?
                        """,
                        ("free", "trialing", now, expires_at, expires_at, now, company_id)
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO subscriptions (workspace_id, plan, status, started_at, expires_at, trial_used, trial_started_at, trial_expires_at, created_at, updated_at)
                        VALUES (?, 'free', 'trialing', ?, ?, 1, ?, ?, ?, ?)
                        """,
                        (company_id, now, expires_at, now, expires_at, now, now)
                    )
            elif option in ("free", "upgrade"):
                conn.execute(
                    """
                    UPDATE company
                    SET onboarding_completed = 1, onboarding_seen_at = COALESCE(onboarding_seen_at, ?)
                    WHERE id = ?
                    """,
                    (now, company_id)
                )
            else:
                return _json_error(400, "invalid_option", "Invalid onboarding option.")

            conn.commit()

        return jsonify({"status": "success"})

    @app.route("/api/auth/reset-password/verify", methods=["GET"])
    def verify_reset_password_token():
        raw_token = (request.args.get("token") or "").strip()
        if not raw_token:
            return _json_error(400, "missing_token", "Token is required.")

        with get_conn() as conn:
            token_row = fetch_one(
                conn,
                """
                SELECT id, subject_type, subject_id, expires_at, used_at
                FROM one_time_tokens
                WHERE token_hash = ?
                  AND purpose = ?
                  AND subject_type IN ('user', 'member_invitation')
                LIMIT 1
                """,
                (_hash_token(raw_token), INITIAL_ADMIN_PASSWORD_RESET_PURPOSE),
            )
            if not token_row:
                return _json_error(404, "invalid_token", "Invalid reset link.")
            if token_row["used_at"]:
                return _json_error(400, "token_used", "This reset link has already been used.")
            if _is_expired(token_row["expires_at"]):
                return _json_error(400, "token_expired", "This reset link has expired.")

            if token_row["subject_type"] == "member_invitation":
                user = fetch_one(
                    conn,
                    """
                    SELECT email, full_name
                    FROM user_invitations
                    WHERE id = ? AND status = 'pending'
                    LIMIT 1
                    """,
                    (token_row["subject_id"],),
                )
                if not user:
                    return _json_error(404, "invitation_not_found", "Invitation for reset link was not found.")
            else:
                user = fetch_one(
                    conn,
                    "SELECT email, full_name FROM users WHERE id = ? LIMIT 1",
                    (token_row["subject_id"],),
                )
                if not user:
                    return _json_error(404, "user_not_found", "User for reset link was not found.")

        return jsonify({"email": user["email"], "full_name": user["full_name"]})

    @app.route("/api/auth/reset-password", methods=["POST"])
    def reset_password_with_token():
        body = request.get_json(silent=True) or {}
        raw_token = (body.get("token") or "").strip()
        new_password = body.get("new_password", "") or ""
        if not raw_token:
            return _json_error(400, "missing_token", "Token is required.")
        if len(new_password) < 8:
            return _json_error(400, "invalid_password", "Password must be at least 8 characters.")

        with get_conn() as conn:
            token_row = fetch_one(
                conn,
                """
                SELECT id, subject_type, subject_id, expires_at, used_at
                FROM one_time_tokens
                WHERE token_hash = ?
                  AND purpose = ?
                  AND subject_type IN ('user', 'member_invitation')
                LIMIT 1
                """,
                (_hash_token(raw_token), INITIAL_ADMIN_PASSWORD_RESET_PURPOSE),
            )
            if not token_row:
                return _json_error(404, "invalid_token", "Invalid reset link.")
            if token_row["used_at"]:
                return _json_error(400, "token_used", "This reset link has already been used.")
            if _is_expired(token_row["expires_at"]):
                return _json_error(400, "token_expired", "This reset link has expired.")

            password_hash = generate_password_hash(new_password)

            if token_row["subject_type"] == "member_invitation":
                invitation = fetch_one(
                    conn,
                    """
                    SELECT id, company_id, email, full_name, role
                    FROM user_invitations
                    WHERE id = ? AND status = 'pending'
                    LIMIT 1
                    """,
                    (token_row["subject_id"],),
                )
                if not invitation:
                    return _json_error(404, "invitation_not_found", "Invitation for reset link was not found.")

                existing = fetch_one(conn, "SELECT id FROM users WHERE email = ? LIMIT 1", (invitation["email"],))
                if existing:
                    return _json_error(409, "user_exists", "A user account with this email already exists.")

                cur = conn.execute(
                    """
                    INSERT INTO users (email, password_hash, full_name, role, company_id, is_active, must_reset_password, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, 0, datetime('now'), datetime('now'))
                    """,
                    (
                        invitation["email"],
                        password_hash,
                        invitation["full_name"],
                        invitation["role"],
                        invitation["company_id"],
                    ),
                )
                user = {
                    "id": int(cur.lastrowid),
                    "email": invitation["email"],
                }
                conn.execute(
                    """
                    UPDATE user_invitations
                    SET status = 'accepted', accepted_at = ?
                    WHERE id = ?
                    """,
                    (utcnow_iso(), invitation["id"]),
                )
            else:
                user_row = fetch_one(conn, "SELECT id, email FROM users WHERE id = ? LIMIT 1", (token_row["subject_id"],))
                if not user_row:
                    return _json_error(404, "user_not_found", "User for reset link was not found.")

                conn.execute(
                    """
                    UPDATE users
                    SET password_hash = ?, must_reset_password = 0, updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (password_hash, user_row["id"]),
                )
                user = {"id": user_row["id"], "email": user_row["email"]}

            now = utcnow_iso()
            conn.execute("UPDATE one_time_tokens SET used_at = ? WHERE id = ?", (now, token_row["id"]))
            conn.execute(
                """
                UPDATE one_time_tokens
                SET used_at = COALESCE(used_at, ?)
                WHERE purpose = ?
                  AND subject_type = ?
                  AND subject_id = ?
                """,
                (now, INITIAL_ADMIN_PASSWORD_RESET_PURPOSE, token_row["subject_type"], token_row["subject_id"]),
            )
            conn.commit()

        send_notification_email(
            subject="Password Reset Successful",
            recipients=[user["email"]],
            body=(
                "Your password was reset successfully.\n\n"
                "You can now log in with your new password."
            ),
        )
        return jsonify({"status": "success", "message": "Password reset successful."})

    @app.route("/api/auth/agent-resources", methods=["GET"])
    @require_auth(app)
    def get_agent_resources():
        """Get all agent resources for the company."""
        company_id = request.auth["company_id"]
        with get_conn() as conn:
            resources = conn.execute(
                "SELECT agent_code, onedrive_link, website_link, access_token FROM agent_resources WHERE company_id = ?",
                (company_id,)
            ).fetchall()
        return jsonify([dict(r) for r in resources])

    @app.route("/api/auth/agent-resources", methods=["POST"])
    @require_auth(app)
    def save_agent_resource():
        """Save or update an agent resource ( OneDrive or website link)."""
        if request.auth["role"] != "admin":
            return _json_error(403, "forbidden", "Only admins can upload links.")
        
        data = request.get_json(silent=True) or {}
        agent_code = data.get("agent_code")
        onedrive_link = (data.get("onedrive_link") or "").strip()
        website_link = (data.get("website_link") or "").strip()
        access_token = (data.get("access_token") or "").strip()
        company_id = request.auth["company_id"]

        if not agent_code:
            return _json_error(400, "missing_code", "Agent code is required.")

        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO agent_resources (company_id, agent_code, onedrive_link, website_link, access_token, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(company_id, agent_code) DO UPDATE SET
                    onedrive_link = excluded.onedrive_link,
                    website_link = excluded.website_link,
                    access_token = excluded.access_token,
                    updated_at = excluded.updated_at
                """,
                (company_id, agent_code, onedrive_link, website_link, _encrypt_val(app, access_token))
            )
            conn.commit()
        
        return jsonify({"status": "success"})

    def _resolve_custom_agent(conn, company_id: int, identifier: Any):
        text = str(identifier or "").strip()
        if not text:
            return None
        if text.isdigit():
            return fetch_one(
                conn,
                "SELECT id, name, configuration, created_at FROM custom_agents WHERE id = ? AND company_id = ? LIMIT 1",
                (int(text), company_id),
            )
        return fetch_one(
            conn,
            "SELECT id, name, configuration, created_at FROM custom_agents WHERE company_id = ? AND lower(name) = lower(?) LIMIT 1",
            (company_id, text),
        )

    def _form_display_value(value: Any) -> str:
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, (list, tuple)):
            return ", ".join(str(item) for item in value)
        if value in (None, ""):
            return "-"
        return str(value)

    def _matching_configured_form(forms: list[dict[str, Any]], form_id: Any):
        if not forms:
            return None
        text = str(form_id or "").strip()
        for form in forms:
            if str(form.get("id") or "").strip() == text:
                return form
        return None if text else forms[0]

    def _create_kb_ingestion_job(company_id: int, agent_id: int, source_type: str, source_value: str):
        now = utcnow_iso()
        collection = ingestion_service.collection_name(company_id)
        with get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO kb_sources
                    (company_id, agent_id, source_type, source_value, qdrant_collection, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT(company_id, agent_id, source_type, source_value) DO UPDATE SET
                    qdrant_collection = excluded.qdrant_collection,
                    status = 'active',
                    updated_at = excluded.updated_at
                """,
                (company_id, agent_id, source_type, source_value, collection, now, now),
            )
            source = fetch_one(
                conn,
                """
                SELECT id FROM kb_sources
                WHERE company_id = ? AND agent_id = ? AND source_type = ? AND source_value = ?
                LIMIT 1
                """,
                (company_id, agent_id, source_type, source_value),
            )
            source_id = int(source["id"] if source else cur.lastrowid)
            # Saving an agent can request the same source several times within seconds.
            # Reuse a job that is still pending/running instead of crawling in parallel.
            # Jobs older than the cutoff are ignored so a crashed worker cannot block re-syncs.
            stale_minutes = max(1, int(os.getenv("KB_INGESTION_STALE_MINUTES", "30") or 30))
            active_since = (datetime.now(timezone.utc) - timedelta(minutes=stale_minutes)).isoformat()
            active_job = fetch_one(
                conn,
                """
                SELECT id FROM kb_ingestion_jobs
                WHERE company_id = ? AND source_id = ? AND status IN ('pending', 'running')
                    AND requested_at >= ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (company_id, source_id, active_since),
            )
            if active_job:
                conn.commit()
                return source_id, int(active_job["id"]), collection, False
            job_cur = conn.execute(
                """
                INSERT INTO kb_ingestion_jobs
                    (company_id, agent_id, source_id, qdrant_collection, status, requested_at)
                VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (company_id, agent_id, source_id, collection, now),
            )
            conn.commit()
            return source_id, int(job_cur.lastrowid), collection, True

    def _start_kb_ingestion(company_id: int, agent_id: int, source_type: str, source_value: str, token=None, options=None) -> dict[str, Any]:
        """Create an ingestion job and run it in the background, unless one is already active."""
        source_id, job_id, collection, created = _create_kb_ingestion_job(company_id, agent_id, source_type, source_value)
        if created:
            import threading

            threading.Thread(
                target=run_ingestion_thread,
                args=(app.app_context(), job_id, company_id, agent_id, source_id, source_type, source_value, token, options),
            ).start()
        else:
            print(
                f"DEBUG ingestion_job_reused: job={job_id} company={company_id} agent={agent_id} "
                f"source={source_type} source_id={source_id} (already pending or running)",
                flush=True,
            )
        return {"source_id": source_id, "job_id": job_id, "collection": collection, "already_running": not created}

    def run_ingestion_thread(app_ctx, job_id, company_id, agent_id, source_id, source, value, token=None, options=None):
        with app_ctx:
            started_monotonic = time.monotonic()
            try:
                print(f"DEBUG ingestion_job_start: job={job_id} company={company_id} agent={agent_id} source={source} source_id={source_id}", flush=True)
                with get_conn() as conn:
                    conn.execute(
                        "UPDATE kb_ingestion_jobs SET status = 'running', started_at = ? WHERE id = ? AND company_id = ?",
                        (utcnow_iso(), job_id, company_id),
                    )
                    conn.commit()

                if source == "website":
                    result = ingestion_service.ingest_website(
                        company_id=company_id,
                        agent_id=agent_id,
                        source_id=source_id,
                        url=value,
                        crawl_options=options,
                    )
                elif source == "onedrive":
                    result = ingestion_service.ingest_onedrive_folder(
                        company_id=company_id,
                        agent_id=agent_id,
                        source_id=source_id,
                        folder_ref=value,
                        access_token=token,
                    )
                elif source == "sharepoint":
                    result = ingestion_service.ingest_sharepoint_folder(
                        company_id=company_id,
                        agent_id=agent_id,
                        source_id=source_id,
                        folder_ref=value,
                        access_token=token,
                    )
                else:
                    raise ValueError(f"Unsupported knowledge base source type: {source}")

                elapsed = round(time.monotonic() - started_monotonic, 2)
                print(
                    f"DEBUG ingestion_job_done: job={job_id} company={company_id} agent={agent_id} "
                    f"source={source} documents={result.get('documents_found')} chunks={result.get('chunks_indexed')} elapsed={elapsed}s",
                    flush=True,
                )
                with get_conn() as conn:
                    conn.execute(
                        """
                        UPDATE kb_ingestion_jobs
                        SET status = 'completed',
                            documents_found = ?,
                            chunks_indexed = ?,
                            finished_at = ?
                        WHERE id = ? AND company_id = ?
                        """,
                        (
                            result.get("documents_found"),
                            result.get("chunks_indexed"),
                            utcnow_iso(),
                            job_id,
                            company_id,
                        ),
                    )
                    conn.execute(
                        "UPDATE kb_sources SET last_ingested_at = ?, updated_at = ? WHERE id = ? AND company_id = ?",
                        (utcnow_iso(), utcnow_iso(), source_id, company_id),
                    )
                    conn.commit()
            except Exception as e:
                elapsed = round(time.monotonic() - started_monotonic, 2)
                print(f"DEBUG ingestion_job_error: job={job_id} company={company_id} agent={agent_id} source={source} elapsed={elapsed}s error={e}", flush=True)
                with get_conn() as conn:
                    conn.execute(
                        """
                        UPDATE kb_ingestion_jobs
                        SET status = 'failed', error_message = ?, finished_at = ?
                        WHERE id = ? AND company_id = ?
                        """,
                        (str(e), utcnow_iso(), job_id, company_id),
                    )
                    conn.execute(
                        "UPDATE kb_sources SET status = 'failed', updated_at = ? WHERE id = ? AND company_id = ?",
                        (utcnow_iso(), source_id, company_id),
                    )
                    conn.commit()

    def _list_from_value(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item or "").strip()]
        if str(value or "").strip():
            return [str(value).strip()]
        return []

    def _normalize_web_crawl_options(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}

        def int_option(*keys: str, minimum: int, maximum: int) -> int | None:
            raw = next((value.get(key) for key in keys if value.get(key) is not None), None)
            if raw is None:
                return None
            try:
                parsed = int(raw)
            except (TypeError, ValueError):
                return None
            return min(maximum, max(minimum, parsed))

        def bool_option(*keys: str) -> bool | None:
            raw = next((value.get(key) for key in keys if value.get(key) is not None), None)
            if raw is None:
                return None
            if isinstance(raw, str):
                return raw.strip().lower() not in {"0", "false", "no", "off"}
            return bool(raw)

        options: dict[str, Any] = {}
        max_pages = int_option("max_pages", "maxPages", minimum=1, maximum=100)
        max_depth = int_option("max_depth", "maxDepth", minimum=0, maximum=5)
        restrict_to_start_path = bool_option("restrict_to_start_path", "restrictToPath")
        if max_pages is not None:
            options["max_pages"] = max_pages
        if max_depth is not None:
            options["max_depth"] = max_depth
        if restrict_to_start_path is not None:
            options["restrict_to_start_path"] = restrict_to_start_path
        # "auto" renders JavaScript-built pages in a headless browser, "always"/"never" force it.
        browser_mode = next((value.get(key) for key in ("browser", "browserMode") if value.get(key) is not None), None)
        if str(browser_mode or "").strip().lower() in {"auto", "always", "never"}:
            options["browser"] = str(browser_mode).strip().lower()
        return options

    def _website_sources_from_config(kb_config: dict[str, Any] | None) -> list[dict[str, Any]]:
        if not isinstance(kb_config, dict):
            return []

        sources: list[dict[str, Any]] = []
        seen: set[str] = set()
        raw_sources = kb_config.get("web_sources") or kb_config.get("webSources") or []
        if not isinstance(raw_sources, list):
            raw_sources = [raw_sources]

        for source in raw_sources:
            if isinstance(source, str):
                url = source.strip()
                options = {}
            elif isinstance(source, dict):
                url = str(source.get("url") or source.get("web_url") or source.get("webUrl") or "").strip()
                options = _normalize_web_crawl_options(source.get("crawl") or source)
            else:
                continue
            if url and url not in seen:
                sources.append({"url": url, "options": options})
                seen.add(url)

        web_urls = _list_from_value(
            kb_config.get("web_urls")
            or kb_config.get("webUrls")
            or kb_config.get("web_url")
            or kb_config.get("webUrl")
        )
        for url in web_urls:
            if url not in seen:
                sources.append({"url": url, "options": {}})
                seen.add(url)
        return sources

    def _desired_kb_sources(kb_config: dict[str, Any] | None) -> set[tuple[str, str]]:
        if not isinstance(kb_config, dict):
            return set()

        web_urls = [source["url"] for source in _website_sources_from_config(kb_config)]
        onedrive_folders = _list_from_value(
            kb_config.get("onedrive_folder_ids")
            or kb_config.get("onedriveFolderIds")
            or kb_config.get("folder_ids")
            or kb_config.get("folderIds")
            or kb_config.get("onedrive_folder_id")
            or kb_config.get("onedriveFolderId")
            or kb_config.get("folder_id")
            or kb_config.get("folderId")
        )
        sharepoint_folders = _list_from_value(
            kb_config.get("sharepoint_folder_ids")
            or kb_config.get("sharepointFolderIds")
            or kb_config.get("sharepoint_folder_id")
            or kb_config.get("sharepointFolderId")
            or kb_config.get("sharepoint_site_url")
            or kb_config.get("sharepointSiteUrl")
        )
        return {
            *{("website", url) for url in web_urls},
            *{("onedrive", folder) for folder in onedrive_folders},
            *{("sharepoint", folder) for folder in sharepoint_folders},
        }

    def _reconcile_removed_kb_sources(company_id: int, agent_id: int, kb_config: dict[str, Any] | None):
        desired = _desired_kb_sources(kb_config)
        removed = []
        failed = []
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, source_type, source_value
                FROM kb_sources
                WHERE company_id = ? AND agent_id = ? AND (status IS NULL OR status != 'deleted')
                """,
                (company_id, agent_id),
            ).fetchall()

        for row in rows:
            key = (row["source_type"], row["source_value"])
            if key in desired:
                continue
            try:
                ingestion_service.delete_source_points(
                    company_id=company_id,
                    agent_id=agent_id,
                    source_id=int(row["id"]),
                )
                with get_conn() as conn:
                    conn.execute(
                        """
                        UPDATE kb_sources
                        SET status = 'deleted', updated_at = ?
                        WHERE id = ? AND company_id = ? AND agent_id = ?
                        """,
                        (utcnow_iso(), row["id"], company_id, agent_id),
                    )
                    conn.commit()
                removed.append({"source_type": row["source_type"], "source_value": row["source_value"]})
            except Exception as e:
                failed.append({"source_type": row["source_type"], "source_value": row["source_value"], "error": str(e)})
        return {"removed": removed, "failed": failed}

    def _start_automatic_kb_ingestion(company_id: int, agent_id: int, kb_config: dict[str, Any] | None, onedrive_token: str | None = None):
        if not isinstance(kb_config, dict):
            return {"started": [], "skipped": []}

        web_sources = _website_sources_from_config(kb_config)
        onedrive_folders = _list_from_value(
            kb_config.get("onedrive_folder_ids")
            or kb_config.get("onedriveFolderIds")
            or kb_config.get("folder_ids")
            or kb_config.get("folderIds")
            or kb_config.get("onedrive_folder_id")
            or kb_config.get("onedriveFolderId")
            or kb_config.get("folder_id")
            or kb_config.get("folderId")
        )
        sharepoint_folders = _list_from_value(
            kb_config.get("sharepoint_folder_ids")
            or kb_config.get("sharepointFolderIds")
            or kb_config.get("sharepoint_folder_id")
            or kb_config.get("sharepointFolderId")
            or kb_config.get("sharepoint_site_url")
            or kb_config.get("sharepointSiteUrl")
        )
        token = (
            onedrive_token
            or kb_config.get("microsoft_token")
            or kb_config.get("onedrive_token")
            or kb_config.get("onedriveToken")
            or kb_config.get("sharepoint_token")
            or kb_config.get("sharepointToken")
        )

        started = []
        skipped = []

        for web_source in web_sources:
            url = web_source["url"]
            job = _start_kb_ingestion(company_id, agent_id, "website", url, options=web_source["options"])
            started.append({"source_type": "website", "source_value": url, "job_id": job["job_id"], "collection": job["collection"]})

        for folder in onedrive_folders:
            if not token:
                skipped.append({"source_type": "onedrive", "source_value": folder, "reason": "missing_microsoft_token"})
                continue
            job = _start_kb_ingestion(company_id, agent_id, "onedrive", folder, token=token)
            started.append({"source_type": "onedrive", "source_value": folder, "job_id": job["job_id"], "collection": job["collection"]})

        for folder in sharepoint_folders:
            if not token:
                skipped.append({"source_type": "sharepoint", "source_value": folder, "reason": "missing_microsoft_token"})
                continue
            job = _start_kb_ingestion(company_id, agent_id, "sharepoint", folder, token=token)
            started.append({"source_type": "sharepoint", "source_value": folder, "job_id": job["job_id"], "collection": job["collection"]})

        return {"started": started, "skipped": skipped}

    @app.route("/api/auth/ingest-url", methods=["POST"])
    @require_auth(app)
    def ingest_url():
        """Trigger website ingestion for an agent."""
        if request.auth["role"] != "admin":
            return _json_error(403, "forbidden", "Only admins can trigger ingestion.")
            
        data = request.get_json(silent=True) or {}
        agent_identifier = data.get("agent_id") or data.get("agent_code") or data.get("agent_name")
        url = (data.get("url") or "").strip()
        crawl_options = _normalize_web_crawl_options(data.get("crawl") or data)
        company_id = int(request.auth["company_id"])
        
        if not agent_identifier or not url:
            return _json_error(400, "invalid_request", "Agent and URL are required.")

        with get_conn() as conn:
            agent = _resolve_custom_agent(conn, company_id, agent_identifier)
        if not agent:
            return _json_error(404, "agent_not_found", "Custom agent not found for this company.")

        job = _start_kb_ingestion(company_id, int(agent["id"]), "website", url, options=crawl_options)
        return jsonify({"status": "already_running" if job["already_running"] else "started", "job_id": job["job_id"], "source_id": job["source_id"], "collection": job["collection"]})

    @app.route("/api/auth/ingest-onedrive", methods=["POST"])
    @require_auth(app)
    def ingest_onedrive():
        """Trigger OneDrive ingestion for an agent."""
        if request.auth["role"] != "admin":
            return _json_error(403, "forbidden", "Only admins can trigger ingestion.")
            
        data = request.get_json(silent=True) or {}
        agent_identifier = data.get("agent_id") or data.get("agent_code") or data.get("agent_name")
        folder_link = (data.get("folder_link") or "").strip()
        body_token = (data.get("access_token") or "").strip() or None
        company_id = int(request.auth["company_id"])

        # Prefer fresh token from the request body (acquired via MSAL right
        # before Sync). Fall back to the token stored at resource-save time
        # only if the client didn't send one — those go stale within an hour.
        token = body_token
        if not token:
            with get_conn() as conn:
                resource = conn.execute(
                    "SELECT access_token FROM agent_resources WHERE company_id = ? AND agent_code = ?",
                    (company_id, agent_identifier)
                ).fetchone()
            token = _decrypt_val(app, resource["access_token"]) if resource else None

        # Diagnostic: shows which token source was used and whether it actually
        # looks like a Graph JWT (real Graph access tokens have exactly 2 dots).
        print(
            f"DEBUG ingest_onedrive: agent={agent_identifier} folder={folder_link!r} "
            f"body_token_len={len(body_token) if body_token else 0} "
            f"final_token_len={len(token) if token else 0} "
            f"final_token_dots={(token or '').count('.')} "
            f"used={'body' if body_token else 'db_fallback'}",
            flush=True,
        )

        if not agent_identifier or not token or not folder_link:
            return _json_error(400, "invalid_request", "Agent, OneDrive file/folder link, and Login are required.")

        with get_conn() as conn:
            agent = _resolve_custom_agent(conn, company_id, agent_identifier)
        if not agent:
            return _json_error(404, "agent_not_found", "Custom agent not found for this company.")

        job = _start_kb_ingestion(company_id, int(agent["id"]), "onedrive", folder_link, token=token)
        return jsonify({"status": "already_running" if job["already_running"] else "started", "job_id": job["job_id"], "source_id": job["source_id"], "collection": job["collection"]})

    @app.route("/api/auth/ingest-sharepoint", methods=["POST"])
    @require_auth(app)
    def ingest_sharepoint():
        """Trigger SharePoint document-library, folder, file, or site-page ingestion for an agent."""
        if request.auth["role"] != "admin":
            return _json_error(403, "forbidden", "Only admins can trigger ingestion.")

        data = request.get_json(silent=True) or {}
        agent_identifier = data.get("agent_id") or data.get("agent_code") or data.get("agent_name")
        folder_link = (data.get("folder_link") or data.get("sharepoint_link") or "").strip()
        body_token = (data.get("access_token") or "").strip() or None
        company_id = int(request.auth["company_id"])

        token = body_token
        if not token:
            with get_conn() as conn:
                resource = conn.execute(
                    "SELECT access_token FROM agent_resources WHERE company_id = ? AND agent_code = ?",
                    (company_id, agent_identifier)
                ).fetchone()
            token = _decrypt_val(app, resource["access_token"]) if resource else None

        print(
            f"DEBUG ingest_sharepoint: agent={agent_identifier} folder={folder_link!r} "
            f"body_token_len={len(body_token) if body_token else 0} "
            f"final_token_len={len(token) if token else 0} "
            f"final_token_dots={(token or '').count('.')} "
            f"used={'body' if body_token else 'db_fallback'}",
            flush=True,
        )

        if not agent_identifier or not token or not folder_link:
            return _json_error(400, "invalid_request", "Agent, SharePoint source link, and Microsoft login are required.")

        with get_conn() as conn:
            agent = _resolve_custom_agent(conn, company_id, agent_identifier)
        if not agent:
            return _json_error(404, "agent_not_found", "Custom agent not found for this company.")

        job = _start_kb_ingestion(company_id, int(agent["id"]), "sharepoint", folder_link, token=token)
        return jsonify({"status": "already_running" if job["already_running"] else "started", "job_id": job["job_id"], "source_id": job["source_id"], "collection": job["collection"]})

    @app.route("/api/auth/api-tool/verify", methods=["POST"])
    @require_auth(app)
    def verify_api_tool_endpoint():
        if request.auth["role"] != "admin":
            return _json_error(403, "forbidden", "Only admins can verify API tools.")

        data = request.get_json(silent=True) or {}
        endpoint = data.get("endpoint") if isinstance(data.get("endpoint"), dict) else {}
        endpoint_kind = str(data.get("type") or endpoint.get("kind") or endpoint.get("type") or "rest").strip().lower()
        test_prompt = str(data.get("test_prompt") or data.get("testPrompt") or "").strip()
        endpoint_kind = str(data.get("type") or endpoint.get("kind") or endpoint.get("type") or "rest").strip().lower()

        if endpoint_kind in {"database", "db", "structured_database"}:
            try:
                result = database_tool_service.verify_profile(endpoint)
            except Exception as exc:
                return jsonify({"ok": False, "message": str(exc), "schemas": []}), 200
            return jsonify(result), 200

        url = str(endpoint.get("url") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            message = "Configure a valid HTTP or HTTPS MCP server endpoint." if endpoint_kind == "mcp" else "Configure a valid HTTP or HTTPS API endpoint."
            return _json_error(400, "invalid_api_endpoint", message)

        if endpoint_kind == "mcp":
            try:
                result = mcp_tool_service.verify_server(endpoint)
            except Exception as exc:
                return jsonify({"ok": False, "message": str(exc), "tools": []}), 200
            tools = result.get("tools") if isinstance(result.get("tools"), list) else []
            return jsonify(
                {
                    "ok": True,
                    "message": f"Detected {len(tools)} MCP tool(s).",
                    "name": result.get("name"),
                    "url": result.get("url"),
                    "transport": result.get("transport"),
                    "tools": tools,
                }
            ), 200

        method = str(endpoint.get("method") or "GET").strip().upper()
        if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            return _json_error(400, "invalid_http_method", "Unsupported HTTP method.")
        if not test_prompt:
            return _json_error(400, "missing_test_prompt", "Enter a sample user request to verify this endpoint.")

        parameters = endpoint.get("parameters") or []
        if not isinstance(parameters, list):
            parameters = []

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return _json_error(400, "openai_not_configured", "OPENAI_API_KEY is required to verify using a sample user request.")

        configured_endpoint = {
            "index": 0,
            "configured_index": 0,
            "url": url,
            "method": method,
            "format": endpoint.get("format") or "JSON",
            "parameters": [
                param
                for param in parameters
                if isinstance(param, dict) and str(param.get("key") or "").strip()
            ],
        }

        try:
            plan = api_tool_service.plan_call(
                api_key=api_key,
                agent_name="API endpoint verifier",
                question=test_prompt,
                history=[],
                api_endpoints=[configured_endpoint],
            )
        except Exception as exc:
            return jsonify({"ok": False, "message": f"Could not interpret the sample request: {exc}"}), 200

        if not plan.get("should_call"):
            missing = plan.get("missing_parameters") if isinstance(plan.get("missing_parameters"), list) else []
            missing_text = f" Missing: {', '.join(str(item) for item in missing)}." if missing else ""
            return jsonify(
                {
                    "ok": False,
                    "message": f"The sample request did not clearly map to this API endpoint.{missing_text}",
                    "plan": plan,
                }
            ), 200

        try:
            result = api_tool_service.execute_configured_call([configured_endpoint], plan)
        except Exception as exc:
            return jsonify({"ok": False, "message": str(exc), "plan": plan}), 200

        extracted_value = None
        if result.get("ok"):
            try:
                extracted_value = openai_service.call_text(
                    api_key=api_key,
                    model=model_router.choose("api_response_extract"),
                    instructions=(
                        "You extract the exact value requested by an admin's sample user request "
                        "from an API response. Use only the API response. "
                        "If the requested value is present, return only that value with no label, "
                        "markdown, quotes, or explanation. If the request asks for multiple values, "
                        "return a concise comma-separated or short list answer. "
                        "If the value is not present, say: Requested value not found."
                    ),
                    input_messages=[
                        {
                            "role": "user",
                            "content": (
                                f"Sample user request:\n{test_prompt}\n\n"
                                f"Planned parameters:\n{json.dumps(plan.get('parameters') or {}, indent=2, default=str)}\n\n"
                                f"API response:\n{json.dumps(result.get('response'), indent=2, default=str)[:12000]}"
                            ),
                        }
                    ],
                    max_output_tokens=180,
                )
            except Exception:
                extracted_value = None

        return jsonify(
            {
                "ok": bool(result.get("ok")),
                "message": "Endpoint verified successfully." if result.get("ok") else f"Endpoint returned status {result.get('status_code')}.",
                "status_code": result.get("status_code"),
                "method": method,
                "url": result.get("url") or url,
                "request": result.get("request"),
                "planned_parameters": plan.get("parameters") if isinstance(plan.get("parameters"), dict) else {},
                "extracted_value": extracted_value,
                "response_preview": result.get("response"),
            }
        ), 200

    @app.route("/api/auth/ingestion-status", methods=["GET"])
    @require_auth(app)
    def get_ingestion_status():
        """Return the latest durable ingestion status for this company."""
        company_id = int(request.auth["company_id"])
        agent_identifier = (
            request.args.get("agent_code")
            or request.args.get("agent_name")
            or request.args.get("agent_id")
        )
        params: list[Any] = [company_id]
        agent_filter = ""

        if agent_identifier:
            with get_conn() as conn:
                agent = _resolve_custom_agent(conn, company_id, agent_identifier)
            if not agent:
                return jsonify({"status": "idle"})
            agent_filter = "AND j.agent_id = ?"
            params.append(int(agent["id"]))

        stale_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        with get_conn() as conn:
            conn.execute(
                """
                UPDATE kb_ingestion_jobs
                SET status = 'failed',
                    error_message = COALESCE(error_message, 'Ingestion timed out before completion. Please retry the sync.'),
                    finished_at = ?
                WHERE company_id = ?
                  AND status = 'running'
                  AND requested_at < ?
                """,
                (utcnow_iso(), company_id, stale_cutoff),
            )
            conn.commit()
            row = fetch_one(
                conn,
                f"""
                SELECT
                    j.id,
                    j.agent_id,
                    ca.name AS agent_name,
                    s.source_type,
                    s.source_value,
                    j.qdrant_collection,
                    j.status,
                    j.documents_found,
                    j.chunks_indexed,
                    j.error_message,
                    j.requested_at,
                    j.started_at,
                    j.finished_at
                FROM kb_ingestion_jobs j
                JOIN kb_sources s ON s.id = j.source_id AND s.company_id = j.company_id AND s.agent_id = j.agent_id
                JOIN custom_agents ca ON ca.id = j.agent_id AND ca.company_id = j.company_id
                WHERE j.company_id = ?
                {agent_filter}
                ORDER BY j.requested_at DESC, j.id DESC
                LIMIT 1
                """,
                tuple(params),
            )
        if not row:
            return jsonify({"status": "idle"})
        return jsonify(
            {
                "status": row["status"],
                "job_id": row["id"],
                "agent": row["agent_name"],
                "agent_id": row["agent_id"],
                "source": row["source_type"],
                "source_value": row["source_value"],
                "collection": row["qdrant_collection"],
                "docs_indexed": row["documents_found"],
                "chunks_indexed": row["chunks_indexed"],
                "error": row["error_message"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
            }
        )

    @app.route("/api/auth/agent-configurations", methods=["GET"])
    @app.route("/api/auth/custom-agents", methods=["GET"])
    @require_auth(app)
    def get_custom_agents():
        """Get all custom agents for the company."""
        company_id = request.auth["company_id"]
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM custom_agents WHERE company_id = ? ORDER BY created_at DESC", 
                (company_id,)
            ).fetchall()
            
            # Fetch company's subscription details
            company_row = fetch_one(conn, "SELECT subscription_tier, subscription_expires_at FROM company WHERE id = ? LIMIT 1", (company_id,))
            company_expires_at = company_row["subscription_expires_at"] if company_row else None
            tier = (company_row["subscription_tier"] or "free").lower() if company_row else "free"
            
            packages = load_packages_config(conn)
            package_info = packages.get(tier) or packages.get("free")
            expiry_days = int(package_info.get("agent_expiry_days") or 30)
        
        # Parse configurations back to dicts
        res = []
        for r in rows:
            d = dict(r)
            d["agent_name"] = d.get("name")
            try:
                d["configuration"] = json.loads(d["configuration"])
                d["tools"] = d["configuration"].get("tools") or []
                d["whatsapp_enabled"] = bool(d["configuration"].get("whatsapp_enabled", False))
            except:
                d["configuration"] = {}
                d["tools"] = []
                d["whatsapp_enabled"] = False
                
            # Add expire_date calculation
            if company_expires_at:
                d["expire_date"] = company_expires_at
            else:
                if d.get("created_at"):
                    try:
                        created_str = d["created_at"]
                        if created_str.endswith('Z'):
                            created_str = created_str[:-1] + '+00:00'
                        elif "T" in created_str and "+" not in created_str and "-" not in created_str[created_str.index("T"):]:
                            created_str += "+00:00"
                        created_dt = datetime.fromisoformat(created_str)
                        expire_dt = created_dt + timedelta(days=expiry_days)
                        d["expire_date"] = expire_dt.isoformat()
                    except Exception:
                        d["expire_date"] = None
                else:
                    d["expire_date"] = None
            res.append(d)
        return jsonify(res)

    @app.route("/api/auth/agent-configurations/<int:agent_id>", methods=["GET"])
    @app.route("/api/auth/custom-agents/<int:agent_id>", methods=["GET"])
    @require_auth(app)
    def get_custom_agent(agent_id):
        """Get one custom agent configuration for the company."""
        company_id = request.auth["company_id"]
        with get_conn() as conn:
            row = fetch_one(
                conn,
                "SELECT * FROM custom_agents WHERE id = ? AND company_id = ? LIMIT 1",
                (agent_id, company_id),
            )
            if not row:
                return _json_error(404, "agent_not_found", "Custom agent was not found.")

            company_row = fetch_one(conn, "SELECT subscription_tier, subscription_expires_at FROM company WHERE id = ? LIMIT 1", (company_id,))
            company_expires_at = company_row["subscription_expires_at"] if company_row else None
            tier = (company_row["subscription_tier"] or "free").lower() if company_row else "free"
            packages = load_packages_config(conn)
            package_info = packages.get(tier) or packages.get("free")
            expiry_days = int(package_info.get("agent_expiry_days") or 30)

        agent = dict(row)
        agent["agent_name"] = agent.get("name")
        try:
            agent["configuration"] = json.loads(agent["configuration"])
            agent["tools"] = agent["configuration"].get("tools") or []
        except Exception:
            agent["configuration"] = {}
            agent["tools"] = []

        if company_expires_at:
            agent["expire_date"] = company_expires_at
        elif agent.get("created_at"):
            try:
                created_str = agent["created_at"]
                if created_str.endswith('Z'):
                    created_str = created_str[:-1] + '+00:00'
                elif "T" in created_str and "+" not in created_str and "-" not in created_str[created_str.index("T"):]:
                    created_str += "+00:00"
                created_dt = datetime.fromisoformat(created_str)
                agent["expire_date"] = (created_dt + timedelta(days=expiry_days)).isoformat()
            except Exception:
                agent["expire_date"] = None
        else:
            agent["expire_date"] = None

        return jsonify({"agent": agent}), 200

    @app.route("/api/auth/companies", methods=["GET"])
    @require_auth(app)
    def get_companies_with_agents():
        """Return each company and its custom agents to super admins."""
        with get_conn() as conn:
            if not _is_super_admin(conn, request.auth):
                return _json_error(403, "forbidden", "Super admin access required.")

            rows = conn.execute(
                """
                SELECT
                    c.id AS company_id,
                    c.name AS company_name,
                    c.domain,
                    COALESCE(cfe.is_enabled, 0) AS whatsapp_enabled,
                    ca.id AS agent_id,
                    ca.name AS agent_name
                FROM company c
                LEFT JOIN company_feature_entitlements cfe
                    ON cfe.company_id = c.id AND cfe.feature_key = 'whatsapp'
                LEFT JOIN custom_agents ca ON ca.company_id = c.id
                ORDER BY lower(c.name), lower(ca.name)
                """
            ).fetchall()

        companies: dict[int, dict[str, Any]] = {}
        for row in rows:
            company_id = int(row["company_id"])
            company = companies.setdefault(
                company_id,
                {
                    "company_id": company_id,
                    "company_name": row["company_name"],
                    "domain": row["domain"],
                    "features": {
                        WHATSAPP_FEATURE: {
                            "feature": WHATSAPP_FEATURE,
                            "enabled": bool(row["whatsapp_enabled"]),
                        }
                    },
                    "agents": [],
                },
            )
            if row["agent_id"] is not None:
                company["agents"].append(
                    {
                        "id": int(row["agent_id"]),
                        "name": row["agent_name"],
                    }
                )

        return jsonify(list(companies.values()))

    @app.route("/api/auth/tools", methods=["GET"])
    @require_auth(app)
    def get_tool_registry():
        """Return the registered custom-agent tools."""
        return jsonify(available_tool_definitions())

    def _create_provisioning_request(conn, company_id: int, agent_name: str, payload: dict[str, Any], agent_id: int | None = None) -> int:
        cur = conn.execute(
            """
            INSERT INTO agent_creation_requests
                (company_id, agent_id, agent_name, payload, status, created_at)
            VALUES (?, ?, ?, ?, 'submitted', ?)
            """,
            (company_id, agent_id, agent_name, json.dumps(payload), utcnow_iso()),
        )
        return int(cur.lastrowid)

    def _mark_provisioning_request(conn, request_id: int, status: str, error_message: str | None = None) -> None:
        conn.execute(
            """
            UPDATE agent_creation_requests
            SET status = ?, error_message = ?, completed_at = ?
            WHERE id = ?
            """,
            (status, error_message, utcnow_iso() if status in {"deployed", "failed", "disabled"} else None, request_id),
        )

    def _mark_custom_agent_provisioning(
        conn,
        *,
        agent_id: int,
        company_id: int,
        status: str,
        request_id: int | None = None,
        error_message: str | None = None,
    ) -> None:
        conn.execute(
            """
            UPDATE custom_agents
            SET provisioning_status = ?,
                provisioning_error = ?,
                last_provisioning_request_id = COALESCE(?, last_provisioning_request_id),
                provisioned_at = CASE WHEN ? = 'deployed' THEN ? ELSE provisioned_at END,
                updated_at = ?
            WHERE id = ? AND company_id = ?
            """,
            (
                status,
                error_message,
                request_id,
                status,
                utcnow_iso(),
                utcnow_iso(),
                agent_id,
                company_id,
            ),
        )

    def _canonical_agent_config(
        agent_name,
        tools,
        api_config=None,
        kb_config=None,
        form_config=None,
        mcp_config=None,
        db_config=None,
        whatsapp_enabled=False,
    ):
        config = {
            "agent_name": agent_name,
            "tools": tools,
            "whatsapp_enabled": bool(whatsapp_enabled),
        }
        if api_config:
            config["api"] = api_config
        if mcp_config:
            config["mcp"] = mcp_config
        if db_config:
            config["database"] = db_config
        if kb_config:
            config["knowledge_base"] = kb_config
        if form_config:
            config["form_handlers"] = form_config
        return config

    def _protect_database_config(db_config):
        """Encrypt DB secrets before persisting agent configuration."""
        if not db_config:
            return db_config

        def protect_profile(profile):
            if not isinstance(profile, dict):
                return profile
            protected = dict(profile)
            for key in ("password", "passwordSecret", "password_secret"):
                value = protected.get(key)
                if value and not str(value).startswith("fernet:"):
                    protected[key] = f"fernet:{_encrypt_val(app, str(value))}"
            for key in ("connectionUri", "connection_uri", "databaseUrl", "database_url", "connectionUriSecret", "connection_uri_secret"):
                value = protected.get(key)
                if value and not str(value).startswith("fernet:"):
                    protected[key] = f"fernet:{_encrypt_val(app, str(value))}"
            return protected

        if isinstance(db_config, list):
            return [protect_profile(profile) for profile in db_config]
        if not isinstance(db_config, dict):
            return db_config
        protected_config = dict(db_config)
        for key in ("profiles", "connections", "databases"):
            if isinstance(protected_config.get(key), list):
                protected_config[key] = [protect_profile(profile) for profile in protected_config[key]]
        if isinstance(protected_config.get("profile"), dict):
            protected_config["profile"] = protect_profile(protected_config["profile"])
        if not any(key in protected_config for key in ("profiles", "connections", "databases", "profile")):
            protected_config = protect_profile(protected_config)
        return protected_config

    def _database_profile_count(db_config):
        if isinstance(db_config, list):
            return len(db_config)
        if not isinstance(db_config, dict):
            return 0
        for key in ("profiles", "connections", "databases"):
            value = db_config.get(key)
            if isinstance(value, list):
                return len(value)
            if isinstance(value, dict):
                return 1
        return 1 if isinstance(db_config.get("profile"), dict) or db_config else 0

    def _create_agent_configuration_internal(company_id, data):
        agent_name = (data.get("agent_name") or data.get("name") or "").strip()
        description = (data.get("description") or "").strip()
        if not agent_name:
            return _json_error(400, "missing_agent_name", "Agent name is required.")

        try:
            whatsapp_enabled = resolve_agent_whatsapp_enabled(data)
        except ValueError as error:
            return _json_error(400, "invalid_whatsapp_access", str(error))

        with get_conn() as conn:
            company = fetch_one(conn, "SELECT name FROM company WHERE id = ? LIMIT 1", (company_id,))
            whatsapp_valid, whatsapp_error, whatsapp_message = validate_agent_whatsapp_access(
                conn,
                company_id,
                whatsapp_enabled,
            )
        if not company:
            return _json_error(404, "company_not_found", "Authenticated company was not found.")
        if not whatsapp_valid:
            return _json_error(403, whatsapp_error, whatsapp_message)
        company_name = company["name"]

        api_config = data.get("api") or data.get("api_config") or data.get("apiConfig") or {}
        mcp_config = data.get("mcp") or data.get("mcp_config") or data.get("mcpConfig") or {}
        db_config = (
            data.get("database")
            or data.get("database_config")
            or data.get("databaseConfig")
            or data.get("structured_database")
            or data.get("structuredDatabase")
            or {}
        )
        kb_config = (
            data.get("knowledge_base")
            or data.get("knowledgeBase")
            or data.get("kb")
            or {}
        )
        form_config = (
            data.get("form_handlers")
            or data.get("formHandlers")
            or data.get("forms")
            or data.get("form")
            or {}
        )
        if not isinstance(api_config, dict):
            api_config = {}
        if not isinstance(mcp_config, (dict, list)):
            mcp_config = {}
        if not isinstance(db_config, (dict, list)):
            db_config = {}
        if not isinstance(kb_config, dict):
            kb_config = {}
        if not isinstance(form_config, (dict, list)):
            form_config = {}

        tools = selected_tool_names(data)
        use_api = tool_is_selected(tools, tool_aliases("api"), api_config)
        use_mcp = tool_is_selected(tools, tool_aliases("mcp"), mcp_config)
        use_db = tool_is_selected(tools, tool_aliases("database"), db_config)
        use_kb = tool_is_selected(
            tools,
            tool_aliases("knowledge_base"),
            kb_config,
        )
        use_form = tool_is_selected(
            tools,
            {"form", "forms", "form_handler", "form_handlers", "form_trigger"},
            form_config,
        )
        if not use_api and not use_mcp and not use_db and not use_kb and not use_form:
            return _json_error(400, "missing_tools", "Select at least one tool for the agent.")

        with get_conn() as conn:
            request_id = _create_provisioning_request(conn, company_id, agent_name, data)
            _mark_provisioning_request(conn, request_id, "provisioning")
            conn.commit()

        configured_tools: list[str] = []
        api_result = None
        collection_id = None

        api_endpoints = (
            data.get("api_endpoints")
            or data.get("apiEndpoints")
            or api_config.get("api_endpoints")
            or api_config.get("apiEndpoints")
            or []
        )
        mcp_servers = (
            data.get("mcp_servers")
            or data.get("mcpServers")
            or api_config.get("mcp_servers")
            or api_config.get("mcpServers")
            or []
        )
        if isinstance(api_endpoints, str):
            api_endpoints = [api_endpoints]
        if isinstance(mcp_servers, dict):
            mcp_servers = [mcp_servers]
        if not isinstance(mcp_servers, list):
            mcp_servers = []
        req_type = (
            data.get("req_type")
            or data.get("reqType")
            or api_config.get("req_type")
            or api_config.get("reqType")
            or api_config.get("method")
            or "GET"
        )

        if use_api:
            api_result = APIComponent(company_id, agent_name, api_endpoints, req_type)
            if api_result.get("status") != "success":
                with get_conn() as conn:
                    _mark_provisioning_request(conn, request_id, "failed", api_result.get("message"))
                    conn.commit()
                return jsonify(api_result), 400
            configured_tools.append("api")
        if use_mcp:
            configured_tools.append("mcp")
        if use_db:
            if _database_profile_count(db_config) > 3:
                return _json_error(400, "too_many_database_profiles", "A custom agent can have up to 3 database profiles.")
            db_config = _protect_database_config(db_config)
            configured_tools.append("database")

        web_url = (
            data.get("web_url")
            or data.get("webUrl")
            or data.get("web_urls")
            or data.get("webUrls")
            or kb_config.get("web_url")
            or kb_config.get("webUrl")
            or kb_config.get("web_urls")
            or kb_config.get("webUrls")
        )
        web_sources = _website_sources_from_config(kb_config)
        seen_web_source_urls = {source["url"] for source in web_sources}
        for url_value in _list_from_value(web_url):
            if url_value not in seen_web_source_urls:
                web_sources.append({"url": url_value, "options": {}})
                seen_web_source_urls.add(url_value)
        if not web_url and web_sources:
            web_url = [source["url"] for source in web_sources]
        onedrive_folder_id = (
            data.get("onedrive_folder_id")
            or data.get("onedriveFolderId")
            or kb_config.get("onedrive_folder_id")
            or kb_config.get("onedriveFolderId")
            or kb_config.get("folder_id")
            or kb_config.get("folderId")
        )
        onedrive_folder_ids = (
            data.get("onedrive_folder_ids")
            or data.get("onedriveFolderIds")
            or kb_config.get("onedrive_folder_ids")
            or kb_config.get("onedriveFolderIds")
            or kb_config.get("folder_ids")
            or kb_config.get("folderIds")
        )
        if isinstance(onedrive_folder_id, list) and not onedrive_folder_ids:
            onedrive_folder_ids = onedrive_folder_id
            onedrive_folder_id = onedrive_folder_ids[0] if onedrive_folder_ids else None
        if isinstance(onedrive_folder_ids, str):
            onedrive_folder_ids = [onedrive_folder_ids]
        if not onedrive_folder_id and onedrive_folder_ids:
            onedrive_folder_id = onedrive_folder_ids[0]
        sharepoint_folder_id = (
            data.get("sharepoint_folder_id")
            or data.get("sharepointFolderId")
            or kb_config.get("sharepoint_folder_id")
            or kb_config.get("sharepointFolderId")
            or kb_config.get("sharepoint_site_url")
            or kb_config.get("sharepointSiteUrl")
        )
        sharepoint_folder_ids = (
            data.get("sharepoint_folder_ids")
            or data.get("sharepointFolderIds")
            or kb_config.get("sharepoint_folder_ids")
            or kb_config.get("sharepointFolderIds")
        )
        if isinstance(sharepoint_folder_id, list) and not sharepoint_folder_ids:
            sharepoint_folder_ids = sharepoint_folder_id
            sharepoint_folder_id = sharepoint_folder_ids[0] if sharepoint_folder_ids else None
        if isinstance(sharepoint_folder_ids, str):
            sharepoint_folder_ids = [sharepoint_folder_ids]
        if not sharepoint_folder_id and sharepoint_folder_ids:
            sharepoint_folder_id = sharepoint_folder_ids[0]
        onedrive_token = (
            data.get("onedrive_token")
            or data.get("onedriveToken")
            or data.get("microsoft_token")
            or kb_config.get("onedrive_token")
            or kb_config.get("onedriveToken")
            or kb_config.get("sharepoint_token")
            or kb_config.get("sharepointToken")
            or kb_config.get("microsoft_token")
        )

        if use_kb:
            try:
                collection_id = KBComponent(
                    company_id,
                    agent_name,
                    web_url,
                    onedrive_folder_id,
                    onedrive_token,
                )
            except Exception as e:
                with get_conn() as conn:
                    _mark_provisioning_request(conn, request_id, "failed", str(e))
                    conn.commit()
                return _json_error(502, "kb_collection_failed", str(e))
            configured_tools.append("knowledge_base")
        if use_form:
            configured_tools.append("form_handler")

        api_workflow_config = {
            "api_endpoints": api_endpoints,
            "mcp_servers": mcp_servers,
            "req_type": str(req_type or "GET").upper(),
        }
        kb_workflow_config = {
            "web_url": web_url,
            "web_sources": [{"url": source["url"], **source["options"]} for source in web_sources],
            "onedrive_folder_id": onedrive_folder_id,
            "onedrive_folder_ids": onedrive_folder_ids or ([onedrive_folder_id] if onedrive_folder_id else []),
            "sharepoint_folder_id": sharepoint_folder_id,
            "sharepoint_folder_ids": sharepoint_folder_ids or ([sharepoint_folder_id] if sharepoint_folder_id else []),
            "routing_profile": kb_config.get("routing_profile") or kb_config.get("routingProfile") or {},
        }
        canonical_config = _canonical_agent_config(
            agent_name,
            configured_tools,
            api_workflow_config if use_api else None,
            kb_workflow_config if use_kb else None,
            form_config if use_form else None,
            mcp_config if use_mcp else None,
            db_config if use_db else None,
            whatsapp_enabled,
        )

        try:
            workflow_path = create_workflow_file(
                company_id=company_id,
                agent_name=agent_name,
                tools=configured_tools,
                api_config=api_workflow_config if use_api else None,
                mcp_config=mcp_config if use_mcp else None,
                db_config=db_config if use_db else None,
                kb_config=kb_workflow_config if use_kb else None,
                form_config=form_config if use_form else None,
                collection_id=collection_id,
            )
        except Exception as e:
            with get_conn() as conn:
                _mark_provisioning_request(conn, request_id, "failed", str(e))
                conn.commit()
            return _json_error(500, "workflow_generation_failed", str(e))

        now = utcnow_iso()
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO companies (company_id, name, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(company_id) DO UPDATE SET name = excluded.name
                """,
                (company_id, company_name, now),
            )
            conn.execute(
                """
                INSERT INTO agents (company_id, agent_name, tools, workflow_path, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'deployed', ?, ?)
                ON CONFLICT(company_id, agent_name) DO UPDATE SET
                    tools = excluded.tools,
                    workflow_path = excluded.workflow_path,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (company_id, agent_name, json.dumps(configured_tools), str(workflow_path), now, now),
            )
            if use_api:
                conn.execute(
                    """
                    INSERT INTO apis (company_id, agent_name, api_endpoints, req_type, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(company_id, agent_name) DO UPDATE SET
                        api_endpoints = excluded.api_endpoints,
                        req_type = excluded.req_type,
                        updated_at = excluded.updated_at
                    """,
                    (
                        company_id,
                        agent_name,
                        json.dumps(api_endpoints),
                        api_workflow_config["req_type"],
                        now,
                        now,
                    ),
                )
            else:
                conn.execute(
                    "DELETE FROM apis WHERE company_id = ? AND agent_name = ?",
                    (company_id, agent_name),
                )
            if use_kb:
                conn.execute(
                    """
                    INSERT INTO kb_details
                        (company_id, agent_name, collection_id, web_url, onedrive_folder_id, onedrive_token, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(company_id, agent_name) DO UPDATE SET
                        collection_id = excluded.collection_id,
                        web_url = excluded.web_url,
                        onedrive_folder_id = excluded.onedrive_folder_id,
                        onedrive_token = excluded.onedrive_token,
                        updated_at = excluded.updated_at
                    """,
                    (
                        company_id,
                        agent_name,
                        collection_id,
                        json.dumps(web_url) if isinstance(web_url, list) else web_url,
                        onedrive_folder_id,
                        _encrypt_val(app, onedrive_token),
                        now,
                        now,
                    ),
                )
            else:
                conn.execute(
                    "DELETE FROM kb_details WHERE company_id = ? AND agent_name = ?",
                    (company_id, agent_name),
                )
            existing_custom_agent = fetch_one(
                conn,
                """
                SELECT id FROM custom_agents
                WHERE company_id = ? AND lower(name) = lower(?)
                LIMIT 1
                """,
                (company_id, agent_name),
            )
            if existing_custom_agent:
                custom_agent_id = int(existing_custom_agent["id"])
                conn.execute(
                    """
                    UPDATE custom_agents
                    SET configuration = ?,
                        description = ?,
                        provisioning_status = 'provisioning',
                        provisioning_error = NULL,
                        last_provisioning_request_id = ?,
                        updated_at = ?
                    WHERE id = ? AND company_id = ?
                    """,
                    (json.dumps(canonical_config), description, request_id, now, custom_agent_id, company_id),
                )
            else:
                cur = conn.execute(
                    """
                    INSERT INTO custom_agents
                        (company_id, name, description, configuration, provisioning_status, last_provisioning_request_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'provisioning', ?, ?, ?)
                    """,
                    (company_id, agent_name, description, json.dumps(canonical_config), request_id, now, now),
                )
                custom_agent_id = int(cur.lastrowid)
            conn.execute(
                "UPDATE agent_creation_requests SET agent_id = ? WHERE id = ?",
                (custom_agent_id, request_id),
            )
            _mark_custom_agent_provisioning(
                conn,
                agent_id=custom_agent_id,
                company_id=company_id,
                status="deployed",
                request_id=request_id,
            )
            _mark_provisioning_request(conn, request_id, "deployed")
            conn.commit()

        kb_for_ingestion = kb_workflow_config if use_kb else None
        cleanup = _reconcile_removed_kb_sources(company_id, custom_agent_id, kb_for_ingestion)
        auto_ingestion = _start_automatic_kb_ingestion(
            company_id,
            custom_agent_id,
            kb_for_ingestion,
            onedrive_token,
        )
        auto_ingestion["removed"] = cleanup["removed"]
        auto_ingestion["cleanup_failed"] = cleanup["failed"]

        return jsonify(
            {
                "status": "success",
                "message": "Agent created and deployed successfully",
                "company_id": company_id,
                "agent_name": agent_name,
                "tools": configured_tools,
                "api": api_result,
                "collection_id": collection_id,
                "workflow_file": str(workflow_path),
                "id": custom_agent_id,
                "configuration": canonical_config,
                "whatsapp_enabled": whatsapp_enabled,
                "provisioning_status": "deployed",
                "provisioning_request_id": request_id,
                "automatic_ingestion": auto_ingestion,
            }
        ), 201

    @app.route("/api/auth/agent-configurations", methods=["POST"])
    @require_auth(app)
    def create_agent_configuration():
        """Receive custom agent configuration and generate its workflow file."""
        if request.auth["role"] != "admin":
            return _json_error(403, "forbidden", "Only admins can create and deploy agents.")

        data = request.get_json(silent=True) or {}
        company_id = int(request.auth["company_id"])
        requested_company_id = data.get("company_id") or data.get("companyId")
        if requested_company_id:
            try:
                requested_company_id = int(requested_company_id)
            except (TypeError, ValueError):
                return _json_error(400, "invalid_company_id", "Company ID must be a number.")
            if requested_company_id != company_id:
                return _json_error(403, "tenant_mismatch", "Company ID does not match the authenticated workspace.")

        # Enforce subscription package validations
        with get_conn() as conn:
            ok, err_msg = _validate_tier_limits(conn, company_id, data, is_edit=False)
            if not ok:
                return _json_error(403, "tier_limit_exceeded", err_msg)

        return _create_agent_configuration_internal(company_id, data)

    @app.route("/api/auth/custom-agents", methods=["POST"])
    @require_auth(app)
    def create_custom_agent():
        """Create a new custom agent and deploy to remote service."""
        company_id = request.auth["company_id"]
        data = request.get_json() or {}
        name = (data.get("name") or "").strip()
        config = data.get("configuration")
        microsoft_token = data.get("microsoft_token")
        
        if not name:
            return _json_error(400, "missing_name", "Agent name is required")
        if not config:
            return _json_error(400, "missing_config", "Configuration is required")

        try:
            whatsapp_enabled = resolve_agent_whatsapp_enabled(data)
        except ValueError as error:
            return _json_error(400, "invalid_whatsapp_access", str(error))
        if isinstance(config, dict):
            config = {**config, "whatsapp_enabled": whatsapp_enabled}
            
        # Enforce subscription package validations
        with get_conn() as conn:
            ok, err_msg = _validate_tier_limits(conn, int(company_id), data, is_edit=False)
            if not ok:
                return _json_error(403, "tier_limit_exceeded", err_msg)
            whatsapp_valid, whatsapp_error, whatsapp_message = validate_agent_whatsapp_access(
                conn,
                int(company_id),
                whatsapp_enabled,
            )
            if not whatsapp_valid:
                return _json_error(403, whatsapp_error, whatsapp_message)
        
        auth_header = request.headers.get("Authorization") or ""
        jwt_token = auth_header.split(" ", 1)[1] if auth_header.startswith("Bearer ") else None
        
        deploy_payload = {
            "name": name,
            "configuration": config,
            "microsoft_token": microsoft_token
        }
        
        # 1. Log request and persist a provisioning agent row before remote work.
        with get_conn() as conn:
            request_id = _create_provisioning_request(conn, int(company_id), name, deploy_payload)
            _mark_provisioning_request(conn, request_id, "provisioning")
            cur = conn.execute(
                """
                INSERT INTO custom_agents
                    (company_id, name, description, configuration, provisioning_status, last_provisioning_request_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'provisioning', ?, ?, ?)
                """,
                (company_id, name, (data.get("description") or "").strip(), json.dumps(config), request_id, utcnow_iso(), utcnow_iso()),
            )
            custom_agent_id = int(cur.lastrowid)
            conn.execute(
                "UPDATE agent_creation_requests SET agent_id = ? WHERE id = ?",
                (custom_agent_id, request_id),
            )
            conn.commit()

        # 2. Deploy remotely
        ok, remote_res, status_code = agent_deployment_service.deploy_custom_agent(name, config, microsoft_token, jwt_token)
        
        # Log Response
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO agent_creation_responses (request_id, status_code, response_body, created_at) VALUES (?, ?, ?, ?)",
                (request_id, status_code, json.dumps(remote_res), utcnow_iso())
            )
            if ok:
                remote_agent_id = remote_res.get("agent_id") if isinstance(remote_res, dict) else None
                conn.execute(
                    """
                    UPDATE custom_agents
                    SET remote_id = ?,
                        provisioning_status = 'deployed',
                        provisioning_error = NULL,
                        provisioned_at = ?,
                        updated_at = ?
                    WHERE id = ? AND company_id = ?
                    """,
                    (remote_agent_id, utcnow_iso(), utcnow_iso(), custom_agent_id, company_id),
                )
                _mark_provisioning_request(conn, request_id, "deployed")
            else:
                error_text = f"Remote deployment failed (Status {status_code}): {remote_res}"
                _mark_custom_agent_provisioning(
                    conn,
                    agent_id=custom_agent_id,
                    company_id=int(company_id),
                    status="failed",
                    request_id=request_id,
                    error_message=error_text,
                )
                _mark_provisioning_request(conn, request_id, "failed", error_text)
            conn.commit()
        
        if not ok:
            return jsonify({
                "status": "error", 
                "message": f"Remote deployment failed (Status {status_code}): {remote_res}"
            }), status_code

        remote_agent_id = remote_res.get("agent_id") if isinstance(remote_res, dict) else None

        return jsonify({
            "status": "success", 
            "message": "Agent created and deployed successfully",
            "agent_id": remote_agent_id,
            "id": custom_agent_id,
            "provisioning_status": "deployed",
            "provisioning_request_id": request_id,
        })

    @app.route("/api/auth/agent-configurations/<int:agent_id>/provisioning", methods=["GET"])
    @app.route("/api/auth/custom-agents/<int:agent_id>/provisioning", methods=["GET"])
    @require_auth(app)
    def get_agent_provisioning_status(agent_id):
        """Return durable provisioning state and latest request/response for an agent."""
        company_id = int(request.auth["company_id"])
        with get_conn() as conn:
            agent = fetch_one(
                conn,
                """
                SELECT id, name, provisioning_status, provisioning_error,
                       last_provisioning_request_id, provisioned_at, created_at, updated_at
                FROM custom_agents
                WHERE id = ? AND company_id = ?
                LIMIT 1
                """,
                (agent_id, company_id),
            )
            if not agent:
                return _json_error(404, "not_found", "Agent not found")

            latest_request = fetch_one(
                conn,
                """
                SELECT id, status, error_message, created_at, completed_at
                FROM agent_creation_requests
                WHERE company_id = ? AND agent_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (company_id, agent_id),
            )
            latest_response = None
            if latest_request:
                latest_response = fetch_one(
                    conn,
                    """
                    SELECT status_code, response_body, created_at
                    FROM agent_creation_responses
                    WHERE request_id = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (latest_request["id"],),
                )

        response_body = None
        if latest_response:
            try:
                response_body = json.loads(latest_response["response_body"])
            except Exception:
                response_body = latest_response["response_body"]

        return jsonify(
            {
                "agent_id": agent["id"],
                "agent_name": agent["name"],
                "provisioning_status": agent["provisioning_status"],
                "provisioning_error": agent["provisioning_error"],
                "last_provisioning_request_id": agent["last_provisioning_request_id"],
                "provisioned_at": agent["provisioned_at"],
                "latest_request": dict(latest_request) if latest_request else None,
                "latest_response": (
                    {
                        "status_code": latest_response["status_code"],
                        "response_body": response_body,
                        "created_at": latest_response["created_at"],
                    }
                    if latest_response
                    else None
                ),
            }
        )

    def _update_custom_agent_internal(company_id, agent_id, data):
        name = data.get("agent_name") or data.get("name")
        config = data.get("configuration")
        has_canonical_payload = any(
            key in data
            for key in ("tools", "api", "mcp", "mcp_config", "mcpConfig", "database", "database_config", "databaseConfig", "structured_database", "structuredDatabase", "knowledge_base", "knowledgeBase", "form_handlers", "formHandlers", "forms", "form")
        )
        microsoft_token = data.get("microsoft_token")
        
        with get_conn() as conn:
            existing = fetch_one(conn, "SELECT id, name, configuration FROM custom_agents WHERE id = ? AND company_id = ?", (agent_id, company_id))
            if not existing:
                return _json_error(404, "not_found", "Agent not found")

            final_name = name or existing["name"]
            old_workflow_path = workflow_file_path(company_id, existing["name"])
            try:
                existing_config = (
                    json.loads(existing["configuration"])
                    if isinstance(existing["configuration"], str)
                    else existing["configuration"]
                )
            except (TypeError, json.JSONDecodeError):
                existing_config = {}
            if not isinstance(existing_config, dict):
                existing_config = {}
            existing_whatsapp_enabled = bool(existing_config.get("whatsapp_enabled", False))
            try:
                whatsapp_enabled = resolve_agent_whatsapp_enabled(
                    data,
                    default=existing_whatsapp_enabled,
                )
            except ValueError as error:
                return _json_error(400, "invalid_whatsapp_access", str(error))
            whatsapp_valid, whatsapp_error, whatsapp_message = validate_agent_whatsapp_access(
                conn,
                int(company_id),
                whatsapp_enabled,
            )
            if not whatsapp_valid:
                return _json_error(403, whatsapp_error, whatsapp_message)
            
            if has_canonical_payload:
                api_config = data.get("api") or data.get("api_config") or data.get("apiConfig") or {}
                mcp_config = data.get("mcp") or data.get("mcp_config") or data.get("mcpConfig") or {}
                db_config = (
                    data.get("database")
                    or data.get("database_config")
                    or data.get("databaseConfig")
                    or data.get("structured_database")
                    or data.get("structuredDatabase")
                    or {}
                )
                kb_config = data.get("knowledge_base") or data.get("knowledgeBase") or data.get("kb") or {}
                form_config = data.get("form_handlers") or data.get("formHandlers") or data.get("forms") or data.get("form") or {}
                if not isinstance(api_config, dict):
                    api_config = {}
                if not isinstance(mcp_config, (dict, list)):
                    mcp_config = {}
                if not isinstance(db_config, (dict, list)):
                    db_config = {}
                if not isinstance(kb_config, dict):
                    kb_config = {}
                if not isinstance(form_config, (dict, list)):
                    form_config = {}

                tool_names = selected_tool_names(data)
                update_use_api = tool_is_selected(tool_names, tool_aliases("api"), api_config)
                update_use_mcp = tool_is_selected(tool_names, tool_aliases("mcp"), mcp_config)
                update_use_db = tool_is_selected(tool_names, tool_aliases("database"), db_config)
                update_use_kb = tool_is_selected(tool_names, tool_aliases("knowledge_base"), kb_config)
                update_use_form = tool_is_selected(
                    tool_names,
                    {"form", "forms", "form_handler", "form_handlers", "form_trigger"},
                    form_config,
                )

                update_tools = []
                update_api_endpoints = api_config.get("api_endpoints") or api_config.get("apiEndpoints") or []
                update_req_type = api_config.get("req_type") or api_config.get("reqType") or api_config.get("method") or "GET"
                if update_use_api:
                    update_tools.append("api")
                if update_use_mcp:
                    update_tools.append("mcp")
                if update_use_db:
                    if _database_profile_count(db_config) > 3:
                        return _json_error(400, "too_many_database_profiles", "A custom agent can have up to 3 database profiles.")
                    db_config = _protect_database_config(db_config)
                    update_tools.append("database")

                update_web_url = kb_config.get("web_url") or kb_config.get("webUrl") or kb_config.get("web_urls") or kb_config.get("webUrls")
                update_web_sources = _website_sources_from_config(kb_config)
                seen_update_web_urls = {source["url"] for source in update_web_sources}
                for url_value in _list_from_value(update_web_url):
                    if url_value not in seen_update_web_urls:
                        update_web_sources.append({"url": url_value, "options": {}})
                        seen_update_web_urls.add(url_value)
                if not update_web_url and update_web_sources:
                    update_web_url = [source["url"] for source in update_web_sources]
                update_onedrive_folder_id = kb_config.get("onedrive_folder_id") or kb_config.get("onedriveFolderId") or kb_config.get("folder_id") or kb_config.get("folderId")
                update_onedrive_folder_ids = (
                    kb_config.get("onedrive_folder_ids")
                    or kb_config.get("onedriveFolderIds")
                    or kb_config.get("folder_ids")
                    or kb_config.get("folderIds")
                )
                if isinstance(update_onedrive_folder_id, list) and not update_onedrive_folder_ids:
                    update_onedrive_folder_ids = update_onedrive_folder_id
                    update_onedrive_folder_id = update_onedrive_folder_ids[0] if update_onedrive_folder_ids else None
                if isinstance(update_onedrive_folder_ids, str):
                    update_onedrive_folder_ids = [update_onedrive_folder_ids]
                if not update_onedrive_folder_id and update_onedrive_folder_ids:
                    update_onedrive_folder_id = update_onedrive_folder_ids[0]
                update_sharepoint_folder_id = (
                    kb_config.get("sharepoint_folder_id")
                    or kb_config.get("sharepointFolderId")
                    or kb_config.get("sharepoint_site_url")
                    or kb_config.get("sharepointSiteUrl")
                )
                update_sharepoint_folder_ids = (
                    kb_config.get("sharepoint_folder_ids")
                    or kb_config.get("sharepointFolderIds")
                )
                if isinstance(update_sharepoint_folder_id, list) and not update_sharepoint_folder_ids:
                    update_sharepoint_folder_ids = update_sharepoint_folder_id
                    update_sharepoint_folder_id = update_sharepoint_folder_ids[0] if update_sharepoint_folder_ids else None
                if isinstance(update_sharepoint_folder_ids, str):
                    update_sharepoint_folder_ids = [update_sharepoint_folder_ids]
                if not update_sharepoint_folder_id and update_sharepoint_folder_ids:
                    update_sharepoint_folder_id = update_sharepoint_folder_ids[0]

                update_collection_id = None
                if update_use_kb:
                    update_collection_id = KBComponent(company_id, final_name, update_web_url, update_onedrive_folder_id)
                    update_tools.append("knowledge_base")
                if update_use_form:
                    update_tools.append("form_handler")

                update_api_workflow_config = {
                    "api_endpoints": update_api_endpoints,
                    "req_type": str(update_req_type or "GET").upper(),
                }
                update_kb_workflow_config = {
                    "web_url": update_web_url,
                    "web_sources": [{"url": source["url"], **source["options"]} for source in update_web_sources],
                    "onedrive_folder_id": update_onedrive_folder_id,
                    "onedrive_folder_ids": update_onedrive_folder_ids or ([update_onedrive_folder_id] if update_onedrive_folder_id else []),
                    "sharepoint_folder_id": update_sharepoint_folder_id,
                    "sharepoint_folder_ids": update_sharepoint_folder_ids or ([update_sharepoint_folder_id] if update_sharepoint_folder_id else []),
                    "routing_profile": kb_config.get("routing_profile") or kb_config.get("routingProfile") or {},
                }
                workflow_path = create_workflow_file(
                    company_id=company_id,
                    agent_name=final_name,
                    tools=update_tools,
                    api_config=update_api_workflow_config if update_use_api else None,
                    mcp_config=mcp_config if update_use_mcp else None,
                    db_config=db_config if update_use_db else None,
                    kb_config=update_kb_workflow_config if update_use_kb else None,
                    form_config=form_config if update_use_form else None,
                    collection_id=update_collection_id,
                )
                conn.execute(
                    """
                    INSERT INTO agents (company_id, agent_name, tools, workflow_path, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'deployed', ?, ?)
                    ON CONFLICT(company_id, agent_name) DO UPDATE SET
                        tools = excluded.tools,
                        workflow_path = excluded.workflow_path,
                        status = excluded.status,
                        updated_at = excluded.updated_at
                    """,
                    (company_id, final_name, json.dumps(update_tools), str(workflow_path), utcnow_iso(), utcnow_iso()),
                )
                if final_name != existing["name"]:
                    conn.execute(
                        "DELETE FROM agents WHERE company_id = ? AND agent_name = ?",
                        (company_id, existing["name"]),
                    )
                    new_workflow_path = workflow_file_path(company_id, final_name)
                    if old_workflow_path != new_workflow_path and old_workflow_path.exists():
                        old_workflow_path.unlink()
                config = _canonical_agent_config(
                    final_name,
                    update_tools,
                    update_api_workflow_config if update_use_api else None,
                    update_kb_workflow_config if update_use_kb else None,
                    form_config if update_use_form else None,
                    mcp_config if update_use_mcp else None,
                    db_config if update_use_db else None,
                    whatsapp_enabled,
                )
            elif isinstance(config, dict):
                config = {**config, "whatsapp_enabled": whatsapp_enabled}
            elif config is None and whatsapp_enabled != existing_whatsapp_enabled:
                config = {**existing_config, "whatsapp_enabled": whatsapp_enabled}
            
            updates = []
            params = []
            if name:
                updates.append("name = ?")
                params.append(name.strip())
            if config:
                updates.append("configuration = ?")
                params.append(json.dumps(config))
            if "description" in data:
                updates.append("description = ?")
                params.append((data.get("description") or "").strip())
            
            final_config = config or existing_config
            deploy_payload = {
                "name": final_name,
                "configuration": final_config,
                "microsoft_token": microsoft_token
            }
            request_id = _create_provisioning_request(conn, int(company_id), final_name, deploy_payload, agent_id)
            _mark_provisioning_request(conn, request_id, "provisioning")
            if updates:
                updates.append("provisioning_status = ?")
                params.append("provisioning")
                updates.append("provisioning_error = NULL")
                updates.append("last_provisioning_request_id = ?")
                params.append(request_id)
                updates.append("updated_at = ?")
                params.append(utcnow_iso())
                params.append(agent_id)
                params.append(company_id)
                conn.execute(f"UPDATE custom_agents SET {', '.join(updates)} WHERE id = ? AND company_id = ?", params)
            else:
                _mark_custom_agent_provisioning(
                    conn,
                    agent_id=agent_id,
                    company_id=int(company_id),
                    status="provisioning",
                    request_id=request_id,
                )
            _mark_custom_agent_provisioning(
                conn,
                agent_id=agent_id,
                company_id=int(company_id),
                status="deployed",
                request_id=request_id,
            )
            _mark_provisioning_request(conn, request_id, "deployed")
            conn.commit()

            kb_for_ingestion = final_config.get("knowledge_base") if isinstance(final_config, dict) else None
            auto_ingestion = {"started": [], "skipped": [], "removed": [], "cleanup_failed": []}
            if has_canonical_payload or data.get("configuration") is not None:
                cleanup = _reconcile_removed_kb_sources(int(company_id), int(agent_id), kb_for_ingestion)
                auto_ingestion = _start_automatic_kb_ingestion(
                    int(company_id),
                    int(agent_id),
                    kb_for_ingestion,
                    microsoft_token,
                )
                auto_ingestion["removed"] = cleanup["removed"]
                auto_ingestion["cleanup_failed"] = cleanup["failed"]

            return jsonify({
                "status": "success",
                "agent_id": agent_id,
                "request_id": request_id,
                "provisioning_status": "deployed",
                "whatsapp_enabled": whatsapp_enabled,
                "automatic_ingestion": auto_ingestion,
            })

    @app.route("/api/auth/agent-configurations/<int:agent_id>", methods=["PATCH"])
    @app.route("/api/auth/custom-agents/<int:agent_id>", methods=["PATCH"])
    @require_auth(app)
    def update_custom_agent(agent_id):
        """Update an existing custom agent and re-deploy."""
        try:
            if request.auth["role"] != "admin":
                return _json_error(403, "forbidden", "Only admins can update agents.")

            company_id = request.auth["company_id"]
            data = request.get_json() or {}
            
            # Enforce subscription package validations
            with get_conn() as conn:
                ok, err_msg = _validate_tier_limits(conn, int(company_id), data, is_edit=True, agent_id=agent_id)
                if not ok:
                    return _json_error(403, "tier_limit_exceeded", err_msg)
                    
            return _update_custom_agent_internal(company_id, agent_id, data)
        except Exception as e:
            import traceback
            print(traceback.format_exc())
            return _json_error(500, "internal_error", str(e))


    @app.route("/api/auth/agent-configurations/<int:agent_id>", methods=["DELETE"])
    @app.route("/api/auth/custom-agents/<int:agent_id>", methods=["DELETE"])
    @require_auth(app)
    def delete_custom_agent(agent_id):
        """Delete a custom agent."""
        if request.auth["role"] != "admin":
            return _json_error(403, "forbidden", "Only admins can delete agents.")

        company_id = request.auth["company_id"]
        workflow_path_to_delete = None
        with get_conn() as conn:
            existing = fetch_one(
                conn,
                "SELECT id, name, configuration FROM custom_agents WHERE id = ? AND company_id = ?",
                (agent_id, company_id),
            )
            if not existing:
                return _json_error(404, "not_found", "Agent not found")
            try:
                ingestion_service.delete_agent_points(
                    company_id=int(company_id),
                    agent_id=int(agent_id),
                )
            except Exception as exc:
                return _json_error(502, "kb_cleanup_failed", f"Could not remove this agent's knowledge-base vectors: {exc}")
            request_id = _create_provisioning_request(
                conn,
                int(company_id),
                existing["name"],
                {"action": "delete", "agent_id": agent_id, "configuration": json.loads(existing["configuration"])},
                agent_id,
            )
            _mark_custom_agent_provisioning(
                conn,
                agent_id=agent_id,
                company_id=int(company_id),
                status="disabled",
                request_id=request_id,
            )
            _mark_provisioning_request(conn, request_id, "disabled")
            workflow_path_to_delete = workflow_file_path(int(company_id), existing["name"])
            conn.execute(
                "DELETE FROM agents WHERE company_id = ? AND agent_name = ?",
                (company_id, existing["name"]),
            )
            conn.execute(
                "DELETE FROM kb_ingestion_jobs WHERE company_id = ? AND agent_id = ?",
                (company_id, agent_id),
            )
            conn.execute(
                "DELETE FROM kb_sources WHERE company_id = ? AND agent_id = ?",
                (company_id, agent_id),
            )
            res = conn.execute("DELETE FROM custom_agents WHERE id = ? AND company_id = ?", (agent_id, company_id))
            conn.commit()
            if res.rowcount == 0:
                return _json_error(404, "not_found", "Agent not found")
        if workflow_path_to_delete and workflow_path_to_delete.exists():
            workflow_path_to_delete.unlink()
        return jsonify({"status": "success", "message": "Agent deleted successfully", "provisioning_request_id": request_id})

    @app.route("/api/auth/voice/ready", methods=["GET"])
    @require_auth(app)
    def voice_service_readiness():
        if stt_provider() == "whisper":
            if not whisper_stt.is_installed():
                return _json_error(
                    503,
                    "speech_model_unavailable",
                    "Voice service is unavailable because the local Whisper model is not installed.",
                )
            return jsonify({"ready": True, "provider": "whisper"})
        if not gemini_service.is_configured():
            return _json_error(
                503,
                "gemini_not_configured",
                "Voice service is unavailable because the Gemini API is not configured.",
            )
        return jsonify({"ready": True, "provider": "gemini"})

    @app.route("/api/auth/voice/stt", methods=["POST"])
    @require_auth(app)
    def voice_speech_to_text():
        use_whisper = stt_provider() == "whisper"
        if not use_whisper and not gemini_service.is_configured():
            return _json_error(
                503,
                "gemini_not_configured",
                "Voice transcription is unavailable because the Gemini API is not configured.",
            )
        if request.content_length and request.content_length > MAX_AUDIO_BYTES + 65536:
            return _json_error(413, "audio_too_large", "The audio recording exceeds the 5 MB limit.")

        language_choices = {
            "": None,
            "auto": None,
            "en": "en-US",
            "si": "si-LK",
            "ta": "ta-LK",
        }
        selected_language = (request.form.get("language") or "auto").strip().lower()
        if selected_language not in language_choices:
            return _json_error(400, "invalid_language", "Choose Auto, English, Sinhala, or Tamil.")

        audio_file = request.files.get("audio")
        if not audio_file or not audio_file.filename:
            return _json_error(400, "missing_audio", "An audio file is required.")
        mime_type = (audio_file.content_type or "").split(";")[0].strip().lower()
        if mime_type not in {
            "audio/wav",
            "audio/x-wav",
            "audio/wave",
        }:
            return _json_error(415, "unsupported_audio_type", "Upload a PCM WAV audio recording.")

        audio_bytes = audio_file.stream.read(MAX_AUDIO_BYTES + 1)
        if len(audio_bytes) > MAX_AUDIO_BYTES:
            return _json_error(413, "audio_too_large", "The audio recording exceeds the 5 MB limit.")

        if use_whisper:
            try:
                transcript = whisper_stt.transcribe(
                    audio_bytes,
                    language_code=language_choices[selected_language],
                )
            except ValueError as exc:
                return _json_error(422, "speech_not_recognized", str(exc))
            except Exception as exc:  # Model missing, decode failure, or runtime error.
                print(f"ERROR Whisper transcription failed: {type(exc).__name__}: {exc}", flush=True)
                return _json_error(
                    503,
                    "speech_model_unavailable",
                    "Local speech recognition is unavailable. Please try again.",
                )
            detected_language = language_choices[selected_language] or detect_transcript_language(transcript)
            return jsonify(
                {
                    "text": transcript,
                    "language": detected_language,
                    "confidence": None,
                    "low_confidence": True,
                }
            )

        try:
            transcript = gemini_service.transcribe_audio(
                api_key=os.getenv("GEMINI_API_KEY", "").strip(),
                audio_bytes=audio_bytes,
                mime_type=mime_type,
                filename=audio_file.filename,
                language_code=language_choices[selected_language],
            )
        except ValueError as exc:
            return _json_error(422, "speech_not_recognized", str(exc))
        except requests.Timeout:
            return _json_error(504, "transcription_timeout", "Gemini took too long to transcribe the audio. Please try again.")
        except requests.RequestException:
            return _json_error(502, "transcription_failed", "Gemini could not process the audio. Please try again.")
        except GeminiAPIError as exc:
            if exc.status_code == 429:
                return _voice_quota_error(exc, "transcription")
            if exc.status_code in {400, 404}:
                return _json_error(503, "speech_model_unavailable", "The configured Gemini transcription model is unavailable.")
            if exc.status_code in {401, 403}:
                return _json_error(503, "gemini_not_configured", "Gemini voice access is not available for this API key.")
            return _json_error(502, "transcription_failed", "Gemini could not transcribe the audio. Please try again.")
        except RuntimeError:
            return _json_error(502, "transcription_failed", "Gemini could not transcribe the audio. Please try again.")

        detected_language = language_choices[selected_language] or detect_transcript_language(transcript)
        return jsonify(
            {
                "text": transcript,
                "language": detected_language,
                "confidence": None,
                "low_confidence": True,
            }
        )

    @app.route("/api/auth/voice/tts", methods=["POST"])
    @require_auth(app)
    def voice_text_to_speech():
        data = request.get_json(silent=True) or {}
        text_value = data.get("text")
        text = text_value.strip() if isinstance(text_value, str) else ""
        if not text:
            return _json_error(400, "missing_text", "Text is required.")

        provider = select_tts_provider(text)
        if provider == "gemini" and not gemini_service.is_configured():
            return _json_error(
                503,
                "gemini_not_configured",
                "Voice synthesis is unavailable because the Gemini API is not configured.",
            )

        contains_sinhala = any("\u0d80" <= char <= "\u0dff" for char in text)
        contains_tamil = any("\u0b80" <= char <= "\u0bff" for char in text)
        contains_latin = any(char.isascii() and char.isalpha() for char in text)
        if (contains_sinhala and (contains_tamil or contains_latin)) or (contains_tamil and contains_latin):
            language = "mixed"
        elif contains_sinhala:
            language = "si-LK"
        elif contains_tamil:
            language = "ta-LK"
        elif contains_latin:
            language = "en-US"
        else:
            language = "unknown"
        print(f"INFO Voice TTS selected provider={provider} language={language}", flush=True)
        voice_setting = (
            "GEMINI_TTS_SINHALA_VOICE"
            if contains_sinhala
            else "GEMINI_TTS_TAMIL_VOICE"
            if contains_tamil
            else "GEMINI_TTS_ENGLISH_VOICE"
        )
        requested_voice = data.get("voice")
        voice = (
            requested_voice.strip()
            if isinstance(requested_voice, str) and requested_voice.strip()
            else os.getenv(voice_setting, "").strip()
            or os.getenv("GEMINI_TTS_VOICE", "").strip()
            or None
        )
        try:
            audio_bytes, mime_type = gemini_service.synthesize_speech(
                api_key=os.getenv("GEMINI_API_KEY", "").strip(),
                text=text,
                voice=voice,
            )
        except ValueError as exc:
            return _json_error(400, "invalid_speech_text", str(exc))
        except requests.Timeout:
            return _json_error(504, "speech_synthesis_timeout", "Gemini took too long to generate speech. Please try again.")
        except requests.RequestException:
            return _json_error(502, "speech_synthesis_failed", "Gemini could not generate speech. Please try again.")
        except GeminiAPIError as exc:
            if exc.status_code == 429:
                return _voice_quota_error(exc, "speech generation")
            if exc.status_code in {400, 404}:
                return _json_error(503, "speech_model_unavailable", "The configured Gemini speech model is unavailable.")
            if exc.status_code in {401, 403}:
                return _json_error(503, "gemini_not_configured", "Gemini voice access is not available for this API key.")
            return _json_error(502, "speech_synthesis_failed", "Gemini could not generate speech. Please try again.")
        except RuntimeError as exc:
            if provider == "piper":
                print(f"ERROR Piper speech synthesis failed: {exc}", flush=True)
                return _json_error(502, "speech_synthesis_failed", "Local speech generation failed. Please try again.")
            return _json_error(502, "speech_synthesis_failed", "Gemini could not generate speech. Please try again.")

        response = Response(audio_bytes, mimetype=mime_type)
        response.headers["X-Speech-Provider"] = provider
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/api/auth/agent-configurations/<int:agent_id>/form-submissions", methods=["POST"])
    @app.route("/api/auth/custom-agents/<int:agent_id>/form-submissions", methods=["POST"])
    @require_auth(app)
    def submit_custom_agent_form(agent_id):
        data = request.get_json(silent=True) or {}
        values = data.get("values") or {}
        if not isinstance(values, dict):
            return _json_error(400, "invalid_values", "Form values must be an object.")

        company_id = int(request.auth["company_id"])
        user_id = int(request.auth.get("user_id") or 0) or None
        submitted_at = utcnow_iso()

        with get_conn() as conn:
            agent = fetch_one(
                conn,
                "SELECT id, name, configuration FROM custom_agents WHERE id = ? AND company_id = ? LIMIT 1",
                (agent_id, company_id),
            )
            if not agent:
                return _json_error(404, "agent_not_found", "Custom agent was not found.")

            try:
                agent_config = json.loads(agent["configuration"] or "{}")
            except Exception:
                agent_config = {}

            configured_forms = form_tool_service.configured_forms(agent_config)
            requested_form = data.get("form") if isinstance(data.get("form"), dict) else {}
            form_id = data.get("form_id") or requested_form.get("id")
            form = _matching_configured_form(configured_forms, form_id)
            if not form:
                return _json_error(400, "form_not_configured", "This agent does not have a configured form.")

            admin_user = fetch_one(
                conn,
                """
                SELECT email FROM users
                WHERE company_id = ? AND role = 'admin' AND is_active = 1
                ORDER BY id ASC
                LIMIT 1
                """,
                (company_id,),
            )

        missing_required: list[str] = []
        submitted_values: dict[str, Any] = {}
        display_values: list[tuple[str, str]] = []

        for field in form.get("fields") or []:
            field_name = field.get("name")
            field_label = str(field.get("label") or field_name or "").strip()
            raw_value = values.get(field_name) if field_name in values else values.get(field_label)
            if field.get("required") and raw_value in (None, "", False):
                missing_required.append(field_label or str(field_name))
            submitted_values[str(field_name or field_label)] = raw_value
            display_values.append((field_label or str(field_name), _form_display_value(raw_value)))

        if missing_required:
            return _json_error(
                400,
                "missing_required_fields",
                f"Missing required fields: {', '.join(missing_required)}",
            )

        submission_payload = {
            "company_id": company_id,
            "agent_id": agent_id,
            "agent_name": agent["name"],
            "form_id": str(form.get("id") or "form-0"),
            "form_title": form.get("title") or "Request Form",
            "submitted_by_user_id": user_id,
            "submitted_at": submitted_at,
            "values": submitted_values,
        }

        raw_submit_actions = form.get("submitActions") or form.get("submissionActions")
        if isinstance(raw_submit_actions, list):
            submit_actions = [
                str(action or "").strip().lower()
                for action in raw_submit_actions
                if str(action or "").strip()
            ]
        else:
            submit_action = str(form.get("submitAction") or form.get("submissionAction") or "email").strip().lower()
            submit_actions = ["email", "api"] if submit_action == "both" else [submit_action]
        submit_actions = [action for action in dict.fromkeys(submit_actions) if action in {"email", "api"}] or ["email"]

        deliveries: list[dict[str, Any]] = []
        if "api" in submit_actions:
            endpoint = str(form.get("apiEndpoint") or form.get("api_endpoint") or "").strip()
            parsed = urlparse(endpoint)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                return _json_error(400, "invalid_form_api_endpoint", "Configure a valid HTTP or HTTPS form API endpoint.")
            try:
                response = requests.post(
                    endpoint,
                    json=submission_payload,
                    timeout=float(app.config.get("FORM_SUBMISSION_API_TIMEOUT_SECONDS", 20)),
                    headers={"Content-Type": "application/json", "User-Agent": "AgentStoreFormSubmission/1.0"},
                )
            except Exception as exc:
                return _json_error(502, "form_api_failed", str(exc))
            if not response.ok:
                return _json_error(
                    502,
                    "form_api_failed",
                    f"Form API returned status {response.status_code}.",
                )
            deliveries.append({"delivery": "api", "endpoint": endpoint})

        if "email" in submit_actions:
            admin_email = admin_user["email"] if admin_user else None
            if not admin_email:
                return _json_error(400, "admin_email_not_found", "No active admin email was found for this company.")

            sent = send_notification_email(
                subject=f"New form submission: {submission_payload['form_title']}",
                recipients=[admin_email],
                body=f"{agent['name']} received a new form submission.",
                details=[
                    ("Agent", agent["name"]),
                    ("Form", submission_payload["form_title"]),
                    ("Submitted At", submitted_at),
                    *display_values,
                ],
                cta_text="Open Agent Store",
                cta_path="/dashboard",
            )
            if not sent:
                return _json_error(502, "form_email_failed", "Failed to send the form submission email.")
            deliveries.append({"delivery": "email", "recipient": admin_email})

        return jsonify(
            {
                "status": "success",
                "delivery": deliveries[0]["delivery"] if len(deliveries) == 1 else "multiple",
                "deliveries": deliveries,
                "form_id": submission_payload["form_id"],
            }
        ), 201

    def _chat_message_payload(row) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload"] or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return {
            "id": row["id"],
            "type": row["message_type"],
            "actionType": row["action_type"],
            "text": row["content"],
            "sources": payload.get("sources") if isinstance(payload.get("sources"), list) else [],
            "quickReplies": payload.get("quickReplies") if isinstance(payload.get("quickReplies"), list) else [],
            "apiResults": payload.get("apiResults") if isinstance(payload.get("apiResults"), list) else [],
            "form": payload.get("form") if isinstance(payload.get("form"), dict) else None,
            "created_at": row["created_at"],
        }

    def _default_chat_title(text: str | None = None) -> str:
        title = " ".join(str(text or "").strip().split())
        if not title or title == "__tool_result__":
            return "New chat"
        return title[:57] + "..." if len(title) > 60 else title

    def _sanitize_generated_chat_title(title: str, question: str) -> str:
        cleaned = " ".join(str(title or "").strip().strip("\"'`").split())
        cleaned = cleaned.rstrip(".:;-")
        question_text = " ".join(str(question or "").strip().split())
        if not cleaned or cleaned.lower() == question_text.lower():
            return _fallback_contextual_chat_title(question)
        return cleaned[:45].rstrip()

    def _fallback_contextual_chat_title(question: str) -> str:
        text = " ".join(str(question or "").strip().lower().split())
        if not text or text == "__tool_result__":
            return "New chat"
        if text in {"hi", "hello", "hey", "good morning", "good afternoon", "good evening"}:
            return "Casual Greeting"

        phrase_titles = [
            ("problem statement", "Problem Statement"),
            ("research gap", "Research Gap"),
            ("literature review", "Literature Review"),
            ("methodology", "Methodology"),
            ("conclusion", "Conclusion"),
            ("summary", "Summary"),
            ("summarize", "Summary"),
            ("email", "Email Draft"),
            ("proposal", "Proposal"),
            ("presentation", "Presentation"),
            ("report", "Report Review"),
            ("code", "Code Help"),
            ("error", "Error Troubleshooting"),
            ("bug", "Bug Troubleshooting"),
        ]
        for phrase, title in phrase_titles:
            if phrase in text:
                return title

        stop_words = {
            "a", "an", "and", "are", "about", "can", "could", "do", "does", "for", "from",
            "give", "help", "how", "i", "in", "is", "it", "me", "of", "on", "please",
            "tell", "that", "the", "this", "to", "what", "when", "where", "which", "why",
            "with", "write", "explain", "describe", "show", "need", "want",
        }
        words = [
            word.strip(".,?!:;()[]{}\"'")
            for word in text.split()
            if word.strip(".,?!:;()[]{}\"'") and word.strip(".,?!:;()[]{}\"'") not in stop_words
        ]
        title_words = words[:4] or ["New", "Chat"]
        return " ".join(word.capitalize() for word in title_words)

    def _generate_contextual_chat_title(question: str) -> str:
        fallback = _fallback_contextual_chat_title(question)
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            return fallback

        try:
            generated = openai_service.call_text(
                api_key=api_key,
                model=model_router.choose("chat_title"),
                instructions=(
                    "Generate a concise chat title from the user's first message. "
                    "Use 2 to 5 words. Capture the topic or task, not the exact wording. "
                    "Do not use quotation marks, punctuation at the end, or labels like 'Title:'."
                ),
                input_messages=[
                    {
                        "role": "user",
                        "content": f"First user message:\n{question}",
                    }
                ],
                max_output_tokens=20,
            )
            return _sanitize_generated_chat_title(generated, question)
        except Exception as exc:
            print(f"WARN: Failed to generate chat title: {exc}")
            return fallback

    def _chat_session_payload(row, message_count: int | None = None) -> dict[str, Any]:
        payload = {
            "id": row["id"],
            "title": row["title"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
        if message_count is not None:
            payload["message_count"] = message_count
        return payload

    def _get_chat_session(conn, *, company_id: int, agent_id: int, user_id: int, session_id: int):
        return fetch_one(
            conn,
            """
            SELECT id, title, created_at, updated_at
            FROM custom_agent_chat_sessions
            WHERE id = ? AND company_id = ? AND agent_id = ? AND user_id = ?
            LIMIT 1
            """,
            (session_id, company_id, agent_id, user_id),
        )

    def _create_chat_session(
        conn,
        *,
        company_id: int,
        agent_id: int,
        user_id: int,
        title: str | None = None,
    ) -> dict[str, Any]:
        now = utcnow_iso()
        cur = conn.execute(
            """
            INSERT INTO custom_agent_chat_sessions
                (company_id, agent_id, user_id, title, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (company_id, agent_id, user_id, _default_chat_title(title), now, now),
        )
        session_id = int(cur.lastrowid)
        return {
            "id": session_id,
            "title": _default_chat_title(title),
            "created_at": now,
            "updated_at": now,
        }

    def _save_chat_message(
        conn,
        *,
        company_id: int,
        agent_id: int,
        user_id: int,
        chat_session_id: int | None = None,
        message_type: str,
        content: str,
        action_type: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO custom_agent_chat_messages
                (company_id, agent_id, user_id, chat_session_id, message_type, action_type, content, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                agent_id,
                user_id,
                chat_session_id,
                message_type,
                action_type,
                content,
                json.dumps(payload or {}),
                utcnow_iso(),
            ),
        )

    @app.route("/api/auth/agent-configurations/<int:agent_id>/chat-sessions", methods=["GET"])
    @app.route("/api/auth/custom-agents/<int:agent_id>/chat-sessions", methods=["GET"])
    @require_auth(app)
    def list_custom_agent_chat_sessions(agent_id):
        company_id = int(request.auth["company_id"])
        user_id = int(request.auth.get("user_id") or 0)
        with get_conn() as conn:
            agent = fetch_one(
                conn,
                "SELECT id FROM custom_agents WHERE id = ? AND company_id = ? LIMIT 1",
                (agent_id, company_id),
            )
            if not agent:
                return _json_error(404, "agent_not_found", "Custom agent was not found.")
            rows = conn.execute(
                """
                WITH recent_sessions AS (
                    SELECT id, title, created_at, updated_at, company_id, agent_id, user_id
                    FROM custom_agent_chat_sessions
                    WHERE company_id = ? AND agent_id = ? AND user_id = ?
                    ORDER BY updated_at DESC, id DESC
                    LIMIT 100
                )
                SELECT
                    s.id,
                    s.title,
                    s.created_at,
                    s.updated_at,
                    (
                        SELECT COUNT(*)
                        FROM custom_agent_chat_messages m
                        WHERE m.company_id = s.company_id
                          AND m.agent_id = s.agent_id
                          AND m.user_id = s.user_id
                          AND m.chat_session_id = s.id
                    ) AS message_count
                FROM recent_sessions s
                ORDER BY s.updated_at DESC, s.id DESC
                """,
                (company_id, agent_id, user_id),
            ).fetchall()
        return jsonify({"sessions": [_chat_session_payload(row, row["message_count"]) for row in rows]}), 200

    @app.route("/api/auth/agent-configurations/<int:agent_id>/chat-sessions", methods=["POST"])
    @app.route("/api/auth/custom-agents/<int:agent_id>/chat-sessions", methods=["POST"])
    @require_auth(app)
    def create_custom_agent_chat_session(agent_id):
        company_id = int(request.auth["company_id"])
        user_id = int(request.auth.get("user_id") or 0)
        data = request.get_json(silent=True) or {}
        with get_conn() as conn:
            agent = fetch_one(
                conn,
                "SELECT id FROM custom_agents WHERE id = ? AND company_id = ? LIMIT 1",
                (agent_id, company_id),
            )
            if not agent:
                return _json_error(404, "agent_not_found", "Custom agent was not found.")
            session = _create_chat_session(
                conn,
                company_id=company_id,
                agent_id=agent_id,
                user_id=user_id,
                title=data.get("title"),
            )
            conn.commit()
        return jsonify({"session": session}), 201

    @app.route("/api/auth/agent-configurations/<int:agent_id>/chat-sessions/<int:session_id>", methods=["PATCH"])
    @app.route("/api/auth/custom-agents/<int:agent_id>/chat-sessions/<int:session_id>", methods=["PATCH"])
    @require_auth(app)
    def rename_custom_agent_chat_session(agent_id, session_id):
        company_id = int(request.auth["company_id"])
        user_id = int(request.auth.get("user_id") or 0)
        data = request.get_json(silent=True) or {}
        title = " ".join(str(data.get("title") or "").strip().split())
        if not title:
            return _json_error(400, "missing_title", "Chat title is required.")
        if len(title) > 80:
            title = title[:80]
        with get_conn() as conn:
            session = _get_chat_session(
                conn,
                company_id=company_id,
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
            )
            if not session:
                return _json_error(404, "chat_session_not_found", "Chat session was not found.")
            now = utcnow_iso()
            conn.execute(
                """
                UPDATE custom_agent_chat_sessions
                SET title = ?, updated_at = ?
                WHERE id = ? AND company_id = ? AND agent_id = ? AND user_id = ?
                """,
                (title, now, session_id, company_id, agent_id, user_id),
            )
            conn.commit()
            updated = _get_chat_session(
                conn,
                company_id=company_id,
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
            )
        return jsonify({"session": _chat_session_payload(updated)}), 200

    @app.route("/api/auth/agent-configurations/<int:agent_id>/chat-sessions/<int:session_id>", methods=["DELETE"])
    @app.route("/api/auth/custom-agents/<int:agent_id>/chat-sessions/<int:session_id>", methods=["DELETE"])
    @require_auth(app)
    def delete_custom_agent_chat_session(agent_id, session_id):
        company_id = int(request.auth["company_id"])
        user_id = int(request.auth.get("user_id") or 0)
        with get_conn() as conn:
            session = _get_chat_session(
                conn,
                company_id=company_id,
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
            )
            if not session:
                return _json_error(404, "chat_session_not_found", "Chat session was not found.")
            conn.execute(
                """
                DELETE FROM custom_agent_chat_messages
                WHERE company_id = ? AND agent_id = ? AND user_id = ? AND chat_session_id = ?
                """,
                (company_id, agent_id, user_id, session_id),
            )
            conn.execute(
                """
                DELETE FROM custom_agent_chat_sessions
                WHERE id = ? AND company_id = ? AND agent_id = ? AND user_id = ?
                """,
                (session_id, company_id, agent_id, user_id),
            )
            conn.commit()
        return jsonify({"status": "success"}), 200

    @app.route("/api/auth/agent-configurations/<int:agent_id>/chat-history", methods=["GET"])
    @app.route("/api/auth/custom-agents/<int:agent_id>/chat-history", methods=["GET"])
    @require_auth(app)
    def get_custom_agent_chat_history(agent_id):
        company_id = int(request.auth["company_id"])
        user_id = int(request.auth.get("user_id") or 0)
        try:
            limit = max(1, min(300, int(request.args.get("limit") or 150)))
        except (TypeError, ValueError):
            limit = 150
        raw_session_id = request.args.get("session_id")
        session_id = None
        if raw_session_id not in (None, ""):
            try:
                session_id = int(raw_session_id)
            except (TypeError, ValueError):
                return _json_error(400, "invalid_chat_session", "Chat session id must be a number.")

        with get_conn() as conn:
            agent = fetch_one(
                conn,
                "SELECT id FROM custom_agents WHERE id = ? AND company_id = ? LIMIT 1",
                (agent_id, company_id),
            )
            if not agent:
                return _json_error(404, "agent_not_found", "Custom agent was not found.")
            if session_id is not None:
                session = _get_chat_session(
                    conn,
                    company_id=company_id,
                    agent_id=agent_id,
                    user_id=user_id,
                    session_id=session_id,
                )
                if not session:
                    return _json_error(404, "chat_session_not_found", "Chat session was not found.")
                rows = conn.execute(
                    """
                    SELECT id, message_type, action_type, content, payload, created_at
                    FROM custom_agent_chat_messages
                    WHERE company_id = ? AND agent_id = ? AND user_id = ? AND chat_session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (company_id, agent_id, user_id, session_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id, message_type, action_type, content, payload, created_at
                    FROM custom_agent_chat_messages
                    WHERE company_id = ? AND agent_id = ? AND user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (company_id, agent_id, user_id, limit),
                ).fetchall()

        messages = [_chat_message_payload(row) for row in reversed(rows)]
        return jsonify({"messages": messages}), 200

    @app.route("/api/auth/agent-configurations/<int:agent_id>/chat-history", methods=["DELETE"])
    @app.route("/api/auth/custom-agents/<int:agent_id>/chat-history", methods=["DELETE"])
    @require_auth(app)
    def clear_custom_agent_chat_history(agent_id):
        company_id = int(request.auth["company_id"])
        user_id = int(request.auth.get("user_id") or 0)
        raw_session_id = request.args.get("session_id")
        session_id = None
        if raw_session_id not in (None, ""):
            try:
                session_id = int(raw_session_id)
            except (TypeError, ValueError):
                return _json_error(400, "invalid_chat_session", "Chat session id must be a number.")
        with get_conn() as conn:
            agent = fetch_one(
                conn,
                "SELECT id FROM custom_agents WHERE id = ? AND company_id = ? LIMIT 1",
                (agent_id, company_id),
            )
            if not agent:
                return _json_error(404, "agent_not_found", "Custom agent was not found.")
            if session_id is not None:
                session = _get_chat_session(
                    conn,
                    company_id=company_id,
                    agent_id=agent_id,
                    user_id=user_id,
                    session_id=session_id,
                )
                if not session:
                    return _json_error(404, "chat_session_not_found", "Chat session was not found.")
                conn.execute(
                    """
                    DELETE FROM custom_agent_chat_messages
                    WHERE company_id = ? AND agent_id = ? AND user_id = ? AND chat_session_id = ?
                    """,
                    (company_id, agent_id, user_id, session_id),
                )
                conn.execute(
                    """
                    UPDATE custom_agent_chat_sessions
                    SET updated_at = ?
                    WHERE id = ? AND company_id = ? AND agent_id = ? AND user_id = ?
                    """,
                    (utcnow_iso(), session_id, company_id, agent_id, user_id),
                )
            else:
                conn.execute(
                    """
                    DELETE FROM custom_agent_chat_messages
                    WHERE company_id = ? AND agent_id = ? AND user_id = ?
                    """,
                    (company_id, agent_id, user_id),
                )
            conn.commit()
        return jsonify({"status": "success"}), 200

    @app.route("/api/auth/chat-usage", methods=["GET"])
    @require_auth(app)
    def get_company_chat_usage():
        if request.auth["role"] != "admin":
            return _json_error(403, "forbidden", "Only company admins can view chat usage.")
        company_id = int(request.auth["company_id"])
        with get_conn() as conn:
            if _is_super_admin(conn, request.auth):
                return _json_error(403, "forbidden", "Use the company admin dashboard to view workspace chat usage.")
            summary = _company_chat_usage_summary(conn, company_id)
            conn.commit()
        return jsonify(summary), 200

    @app.route("/api/auth/chat-usage/users/<int:user_id>/quota", methods=["PATCH"])
    @require_auth(app)
    def update_user_chat_quota(user_id):
        if request.auth["role"] != "admin":
            return _json_error(403, "forbidden", "Only company admins can update chat quota limits.")
        company_id = int(request.auth["company_id"])
        data = request.get_json(silent=True) or {}
        monthly_limit = data.get("monthly_token_limit", data.get("monthly_limit"))
        if monthly_limit in ("", None):
            monthly_limit = None
        else:
            try:
                monthly_limit = max(0, int(monthly_limit))
            except (TypeError, ValueError):
                return _json_error(400, "invalid_monthly_token_limit", "Monthly token limit must be a number.")
        is_suspended = 1 if data.get("is_suspended") else 0
        suspended_until = str(data.get("suspended_until") or "").strip() or None
        reason = str(data.get("reason") or "").strip() or None
        now = utcnow_iso()
        with get_conn() as conn:
            if _is_super_admin(conn, request.auth):
                return _json_error(403, "forbidden", "Use the company admin dashboard to update workspace chat limits.")
            user = fetch_one(conn, "SELECT id FROM users WHERE id = ? AND company_id = ? LIMIT 1", (user_id, company_id))
            if not user:
                return _json_error(404, "user_not_found", "User was not found in this workspace.")
            conn.execute(
                """
                INSERT INTO user_chat_quota_overrides
                    (company_id, user_id, monthly_token_limit, is_suspended, suspended_until, reason, updated_by_user_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(company_id, user_id) DO UPDATE SET
                    monthly_token_limit = excluded.monthly_token_limit,
                    is_suspended = excluded.is_suspended,
                    suspended_until = excluded.suspended_until,
                    reason = excluded.reason,
                    updated_by_user_id = excluded.updated_by_user_id,
                    updated_at = excluded.updated_at
                """,
                (company_id, user_id, monthly_limit, is_suspended, suspended_until, reason, int(request.auth["user_id"]), now, now),
            )
            conn.commit()
        return jsonify({"status": "success"}), 200

    @app.route("/api/auth/chat-usage/users/<int:user_id>/quota", methods=["DELETE"])
    @require_auth(app)
    def clear_user_chat_quota(user_id):
        if request.auth["role"] != "admin":
            return _json_error(403, "forbidden", "Only company admins can clear chat quota limits.")
        company_id = int(request.auth["company_id"])
        with get_conn() as conn:
            if _is_super_admin(conn, request.auth):
                return _json_error(403, "forbidden", "Use the company admin dashboard to update workspace chat limits.")
            conn.execute(
                "DELETE FROM user_chat_quota_overrides WHERE company_id = ? AND user_id = ?",
                (company_id, user_id),
            )
            conn.commit()
        return jsonify({"status": "success"}), 200

    @app.route("/api/auth/database-tool/audit", methods=["GET"])
    @require_auth(app)
    def list_database_tool_audit():
        if request.auth["role"] != "admin":
            return _json_error(403, "forbidden", "Only company admins can view database tool audit logs.")
        company_id = int(request.auth["company_id"])
        try:
            limit = int(request.args.get("limit") or 100)
        except (TypeError, ValueError):
            limit = 100
        user_query = str(request.args.get("user") or "").strip()
        activity_date = str(request.args.get("date") or "").strip()
        with get_conn() as conn:
            if _is_super_admin(conn, request.auth):
                return _json_error(403, "forbidden", "Use a company admin dashboard to view database tool audit logs.")
            logs = database_tool_audit_service.list_for_company(
                conn,
                company_id=company_id,
                limit=limit,
                user_query=user_query,
                activity_date=activity_date,
            )
        return jsonify({"logs": logs}), 200

    @app.route("/api/auth/tool-execution/audit", methods=["GET"])
    @require_auth(app)
    def list_tool_execution_audit():
        if request.auth["role"] != "admin":
            return _json_error(403, "forbidden", "Only company admins can view tool execution audit logs.")
        company_id = int(request.auth["company_id"])
        try:
            limit = int(request.args.get("limit") or 100)
        except (TypeError, ValueError):
            limit = 100
        user_query = str(request.args.get("user") or "").strip()
        activity_date = str(request.args.get("date") or "").strip()
        with get_conn() as conn:
            if _is_super_admin(conn, request.auth):
                return _json_error(403, "forbidden", "Use a company admin dashboard to view tool execution audit logs.")
            logs = tool_audit_service.list_for_company(
                conn,
                company_id=company_id,
                limit=limit,
                user_query=user_query,
                activity_date=activity_date,
            )
        return jsonify({"logs": logs}), 200

    @app.route("/api/auth/chat", methods=["POST"])
    @require_auth(app)
    def chat_proxy():
        """Proxy a RAG chat question through to the ingestion backend.

        Body: {"agent_code": "E", "question": "..."}.  Returns {"answer", "sources", ...}.
        """
        data = request.get_json(silent=True) or {}
        agent_code = (data.get("agent_id") or data.get("agent_code") or data.get("agent_name") or "")
        agent_code = str(agent_code).strip()
        question = (data.get("question") or data.get("query") or "").strip()
        top_k = data.get("top_k")
        history = data.get("history") or []
        if not isinstance(history, list):
            history = []
        response_mode = "voice" if data.get("response_mode") == "voice" else "text"
        shown_form_ids = data.get("shown_form_ids") or []
        dismissed_form_ids = data.get("dismissed_form_ids") or []
        if not isinstance(shown_form_ids, list):
            shown_form_ids = []
        if not isinstance(dismissed_form_ids, list):
            dismissed_form_ids = []
        suppress_user_message = bool(data.get("suppress_user_message")) and (
            custom_agent_chat_service.is_confirmation_command(question)
        )
        raw_session_id = data.get("chat_session_id")
        chat_session_id = None
        if raw_session_id not in (None, ""):
            try:
                chat_session_id = int(raw_session_id)
            except (TypeError, ValueError):
                return _json_error(400, "invalid_chat_session", "Chat session id must be a number.")
        raw_database_profile_index = data.get("database_profile_index")
        database_profile_index = None
        if raw_database_profile_index not in (None, "", "auto"):
            try:
                database_profile_index = int(raw_database_profile_index)
            except (TypeError, ValueError):
                return _json_error(400, "invalid_database_profile", "Database profile index must be a number.")
            if database_profile_index < 0:
                return _json_error(400, "invalid_database_profile", "Database profile index must be zero or greater.")
        database_profile_name = str(data.get("database_profile_name") or "").strip() or None

        if not agent_code:
            return _json_error(400, "missing_agent", "agent_code is required.")
        if not question:
            return _json_error(400, "missing_question", "question is required.")

        company_id = int(request.auth["company_id"])
        user_id = int(request.auth.get("user_id") or 0)
        with get_conn() as conn:
            company_row = fetch_one(
                conn,
                "SELECT id, created_at, subscription_tier, subscription_status, subscription_expires_at FROM company WHERE id = ?",
                (company_id,),
            )
            company = dict(company_row) if company_row else None
            if not company:
                return _json_error(401, "invalid_company", "Invalid session. Please login again.")
            if company and company["subscription_status"] == "expired":
                return _json_error(402, "subscription_expired", "Your workspace subscription has expired. Please renew your subscription to continue chatting.")
            agent_row = _resolve_custom_agent(conn, company_id, agent_code)
            agent = dict(agent_row) if agent_row else None
        if agent:
            created_at_str = agent.get("created_at")
            with get_conn() as conn:
                tier = (company["subscription_tier"] or "free").lower()
                packages = load_packages_config(conn)
                package_info = packages.get(tier) or packages.get("free")
                expiry_days = int(package_info.get("agent_expiry_days") or 30)
            if is_agent_expired(created_at_str, company.get("subscription_expires_at"), expiry_days):
                return _json_error(402, "agent_expired", "This agent's subscription has expired. Please contact your administrator to repay.")
            quota_usage = None
            if question != "__tool_result__" and not suppress_user_message:
                with get_conn() as conn:
                    try:
                        conn.execute("BEGIN IMMEDIATE")
                        quota_usage = _check_chat_quota(conn, company_id, user_id)
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
                if not quota_usage.get("allowed"):
                    user_quota = quota_usage.get("user_quota") if isinstance(quota_usage.get("user_quota"), dict) else {}
                    return jsonify(
                        {
                            "error": user_quota.get("error") or "monthly_token_limit_reached",
                            "message": user_quota.get("message") or "Your workspace has reached its monthly token limit. Please upgrade your package or wait until the next renewal period.",
                            "quota": {
                                "tier": quota_usage.get("tier"),
                                "monthly_token_limit": quota_usage.get("monthly_token_limit"),
                                "tokens_used": quota_usage.get("tokens_used"),
                                "tokens_remaining": quota_usage.get("tokens_remaining"),
                                "period_start": quota_usage.get("period_start"),
                                "period_end": quota_usage.get("period_end"),
                                "user_quota": user_quota or None,
                            },
                        }
                    ), 429
            try:
                try:
                    agent_config = json.loads(agent["configuration"] or "{}")
                except Exception:
                    agent_config = {}
                payload, recorded_usage = _run_metered_chat(
                    company_id,
                    user_id,
                    lambda: custom_agent_chat_service.chat_custom_agent(
                        company_id=company_id,
                        agent_id=int(agent["id"]),
                        agent_name=agent["name"],
                        user_id=user_id,
                        chat_session_id=chat_session_id,
                        agent_config=agent_config,
                        question=question,
                        history=history,
                        shown_form_ids=[str(item) for item in shown_form_ids],
                        dismissed_form_ids=[str(item) for item in dismissed_form_ids],
                        database_profile_index=database_profile_index,
                        database_profile_name=database_profile_name,
                        response_mode=response_mode,
                        top_k=int(top_k or 5),
                    ),
                )
                if recorded_usage:
                    quota_usage = recorded_usage
                agent_id = int(agent["id"])
                chat_session = None
                if user_id:
                    with get_conn() as conn:
                        if chat_session_id is not None:
                            chat_session = _get_chat_session(
                                conn,
                                company_id=company_id,
                                agent_id=agent_id,
                                user_id=user_id,
                                session_id=chat_session_id,
                            )
                            if not chat_session:
                                return _json_error(404, "chat_session_not_found", "Chat session was not found.")
                            chat_session = dict(chat_session)
                        else:
                            chat_session = _create_chat_session(
                                conn,
                                company_id=company_id,
                                agent_id=agent_id,
                                user_id=user_id,
                                title=None,
                            )
                            chat_session_id = int(chat_session["id"])

                        if question != "__tool_result__" and not suppress_user_message:
                            _save_chat_message(
                                conn,
                                company_id=company_id,
                                agent_id=agent_id,
                                user_id=user_id,
                                chat_session_id=chat_session_id,
                                message_type="user",
                                content=question,
                            )
                            current_session_title = (
                                chat_session.get("title")
                                if isinstance(chat_session, dict)
                                else chat_session["title"] if chat_session else ""
                            )
                            if current_session_title == "New chat":
                                title, title_usage = _run_metered_chat(
                                    company_id,
                                    user_id,
                                    lambda: _generate_contextual_chat_title(question),
                                )
                                if title_usage:
                                    quota_usage = title_usage
                                now = utcnow_iso()
                                conn.execute(
                                    """
                                    UPDATE custom_agent_chat_sessions
                                    SET title = ?, updated_at = ?
                                    WHERE id = ? AND company_id = ? AND agent_id = ? AND user_id = ?
                                    """,
                                    (title, now, chat_session_id, company_id, agent_id, user_id),
                                )
                                chat_session = {**chat_session, "title": title, "updated_at": now}
                        else:
                            submitted_message = next(
                                (
                                    item.get("content")
                                    for item in reversed(history)
                                    if item.get("role") == "user" and str(item.get("content") or "").startswith("Submitted ")
                                ),
                                None,
                            )
                            tool_result_message = next(
                                (
                                    item.get("content")
                                    for item in reversed(history)
                                    if item.get("role") == "tool_result" and item.get("content")
                                ),
                                None,
                            )
                            if submitted_message:
                                _save_chat_message(
                                    conn,
                                    company_id=company_id,
                                    agent_id=agent_id,
                                    user_id=user_id,
                                    chat_session_id=chat_session_id,
                                    message_type="user",
                                    content=submitted_message,
                                )
                            if tool_result_message:
                                _save_chat_message(
                                    conn,
                                    company_id=company_id,
                                    agent_id=agent_id,
                                    user_id=user_id,
                                    chat_session_id=chat_session_id,
                                    message_type="tool_result",
                                    content=tool_result_message,
                                )
                        _save_chat_message(
                            conn,
                            company_id=company_id,
                            agent_id=agent_id,
                            user_id=user_id,
                            chat_session_id=chat_session_id,
                            message_type="bot",
                            action_type=payload.get("type") or "message",
                            content=payload.get("answer") or payload.get("message") or "",
                            payload={
                                "sources": payload.get("sources") if isinstance(payload.get("sources"), list) else [],
                                "quickReplies": payload.get("quick_replies") if isinstance(payload.get("quick_replies"), list) else [],
                                "apiResults": payload.get("api_results") if isinstance(payload.get("api_results"), list) else [],
                                "form": payload.get("form") if isinstance(payload.get("form"), dict) else None,
                            },
                        )
                        now = utcnow_iso()
                        conn.execute(
                            """
                            UPDATE custom_agent_chat_sessions
                            SET updated_at = ?
                            WHERE id = ? AND company_id = ? AND agent_id = ? AND user_id = ?
                            """,
                            (now, chat_session_id, company_id, agent_id, user_id),
                        )
                        conn.commit()
                        payload["chat_session_id"] = chat_session_id
                        payload["chat_session_title"] = (
                            chat_session.get("title") if isinstance(chat_session, dict) else chat_session["title"]
                        )
                if quota_usage:
                    payload["quota"] = {
                        "tier": quota_usage.get("tier"),
                        "monthly_token_limit": quota_usage.get("monthly_token_limit"),
                        "tokens_used": quota_usage.get("tokens_used"),
                        "tokens_remaining": quota_usage.get("tokens_remaining"),
                        "period_start": quota_usage.get("period_start"),
                        "period_end": quota_usage.get("period_end"),
                    }
                return jsonify(payload), 200
            except Exception as e:
                return _json_error(502, "custom_agent_chat_failed", str(e))

        auth_header = request.headers.get("Authorization") or ""
        jwt = auth_header.split(" ", 1)[1] if auth_header.startswith("Bearer ") else None

        quota_usage = None
        if question != "__tool_result__":
            with get_conn() as conn:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                    quota_usage = _check_chat_quota(conn, company_id, user_id)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
            if not quota_usage.get("allowed"):
                user_quota = quota_usage.get("user_quota") if isinstance(quota_usage.get("user_quota"), dict) else {}
                return jsonify(
                    {
                        "error": user_quota.get("error") or "monthly_token_limit_reached",
                        "message": user_quota.get("message") or "Your workspace has reached its monthly token limit. Please upgrade your package or wait until the next renewal period.",
                        "quota": {
                            "tier": quota_usage.get("tier"),
                            "monthly_token_limit": quota_usage.get("monthly_token_limit"),
                            "tokens_used": quota_usage.get("tokens_used"),
                            "tokens_remaining": quota_usage.get("tokens_remaining"),
                            "period_start": quota_usage.get("period_start"),
                            "period_end": quota_usage.get("period_end"),
                            "user_quota": user_quota or None,
                        },
                    }
                ), 429

        (ok, payload), recorded_usage = _run_metered_chat(
            company_id,
            user_id,
            lambda: custom_agent_chat_service.chat(agent_code, question, jwt, top_k=top_k),
        )
        if recorded_usage:
            quota_usage = recorded_usage
        if quota_usage and isinstance(payload, dict):
            payload["quota"] = {
                "tier": quota_usage.get("tier"),
                "monthly_token_limit": quota_usage.get("monthly_token_limit"),
                "tokens_used": quota_usage.get("tokens_used"),
                "tokens_remaining": quota_usage.get("tokens_remaining"),
                "period_start": quota_usage.get("period_start"),
                "period_end": quota_usage.get("period_end"),
            }
        return jsonify(payload), 200 if ok else 502

    @app.route("/api/auth/create-checkout-session", methods=["POST"])
    @require_auth(app)
    def auth_create_checkout_session():
        import stripe
        stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
        if not stripe.api_key:
            return _json_error(500, "stripe_error", "Stripe secret key not configured on backend.")

        data = request.get_json() or {}
        company_id = int(request.auth["company_id"])
        user_email = request.auth.get("email") or ""
        
        tier = data.get("tier")
        
        # Resolve frontend URL dynamically from Origin/Referer or config
        if app.debug:
            frontend_url = "http://localhost:3000"
        else:
            origin = request.headers.get("Origin")
            if origin:
                frontend_url = origin.rstrip("/")
            else:
                referrer = request.headers.get("Referer")
                if referrer:
                    parsed = urlparse(referrer)
                    frontend_url = f"{parsed.scheme}://{parsed.netloc}"
                else:
                    frontend_url = (app.config.get("FRONTEND_URL") or "http://localhost:3000").rstrip("/")

        if tier:
            tier = tier.lower()
            if tier not in EDITABLE_PACKAGE_IDS or tier == "free":
                return _json_error(400, "invalid_tier", "Invalid subscription tier.")

            with get_conn() as conn:
                packages = load_packages_config(conn)
                tier_info = get_tier_info(packages, tier)
            amount_usd = tier_info["price"]
            items = f"Aisle Agents - {tier_info['name']}"
            
            try:
                session = stripe.checkout.Session.create(
                    payment_method_types=["card"],
                    line_items=[{
                        "price_data": {
                            "currency": "usd",
                            "product_data": {"name": items},
                            "unit_amount": int(amount_usd * 100),
                            "recurring": {"interval": "month"},
                        },
                        "quantity": 1,
                    }],
                    mode="subscription",
                    customer_email=user_email or None,
                    success_url=f"{frontend_url}/dashboard?subscription=success&session_id={{CHECKOUT_SESSION_ID}}&tier={tier}",
                    cancel_url=f"{frontend_url}/dashboard?subscription=cancelled",
                    metadata={
                        "company_id": str(company_id),
                        "tier": tier
                    }
                )
                
                with get_conn() as conn:
                    conn.execute(
                        """
                        INSERT INTO pending_payments (session_id, company_id, user_email, agent_payload, is_edit, agent_id, created_at)
                        VALUES (?, ?, ?, ?, 0, NULL, ?)
                        """,
                        (session.id, company_id, user_email, json.dumps({"tier": tier}), utcnow_iso())
                    )
                    plan_display_name = f"{tier.capitalize()} Plan"
                    cur = conn.execute(
                        """
                        INSERT INTO payments (workspace_id, transaction_id, payment_gateway, amount, currency, payment_status, payment_date, stripe_session_id, plan_name)
                        VALUES (?, ?, 'stripe', ?, ?, 'pending', ?, ?, ?)
                        """,
                        (company_id, f"TEMP_{session.id}", amount_usd, "usd", utcnow_iso(), session.id, plan_display_name)
                    )
                    inserted_id = cur.lastrowid
                    tx_id = f"T_ID {inserted_id:02d}"
                    conn.execute(
                        "UPDATE payments SET transaction_id = ? WHERE id = ?",
                        (tx_id, inserted_id)
                    )
                    conn.commit()
                return jsonify({"url": session.url})
            except Exception as e:
                return _json_error(500, "stripe_error", str(e))
        
        return _json_error(400, "missing_tier", "A subscription tier is required to create a checkout session.")

    @app.route("/api/auth/payment-success", methods=["POST"])
    @require_auth(app)
    def auth_payment_success():
        import stripe
        stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
        if not stripe.api_key:
            return _json_error(500, "stripe_error", "Stripe secret key not configured on backend.")

        data = request.get_json() or {}
        session_id = data.get("session_id")
        if not session_id:
            return _json_error(400, "missing_session_id", "Session ID is required.")

        try:
            # Verify the session with Stripe
            session = stripe.checkout.Session.retrieve(session_id)
            if session.payment_status != "paid" and session.status != "complete":
                return _json_error(400, "payment_not_completed", "Payment has not been completed successfully.")

            # Retrieve from pending_payments
            with get_conn() as conn:
                pending = fetch_one(conn, "SELECT * FROM pending_payments WHERE session_id = ?", (session_id,))
                if not pending:
                    completed_payment = fetch_one(
                        conn,
                        """
                        SELECT amount, currency, transaction_id
                        FROM payments
                        WHERE stripe_session_id = ? AND payment_status = 'completed'
                        """,
                        (session_id,),
                    )
                    if completed_payment:
                        return jsonify({
                            "status": "success",
                            "message": "Payment already verified successfully.",
                            "payment": {
                                "amount": completed_payment["amount"],
                                "currency": completed_payment["currency"],
                                "agent_name": "Subscription Package",
                                "order_id": completed_payment["transaction_id"]
                            }
                        }), 200
                    return _json_error(404, "session_not_found", "Pending payment session not found or already processed.")

                company_id = pending["company_id"]
                agent_payload = json.loads(pending["agent_payload"])

                # Fetch payment details for the response
                payment_record = fetch_one(
                    conn,
                    "SELECT amount, currency, transaction_id FROM payments WHERE stripe_session_id = ?",
                    (session_id,),
                )
                amount = payment_record["amount"] if payment_record else 1.0
                currency = payment_record["currency"] if payment_record else "usd"

                # Check if it is a subscription package checkout
                if isinstance(agent_payload, dict) and "tier" in agent_payload:
                    tier = agent_payload["tier"]
                    now = utcnow_iso()
                    
                    # Fetch company to check current expiration date
                    company = fetch_one(conn, "SELECT subscription_expires_at FROM company WHERE id = ?", (company_id,))
                    now_dt = datetime.now(timezone.utc)
                    base_dt = now_dt
                    if company and company["subscription_expires_at"]:
                        try:
                            clean_expiry = company["subscription_expires_at"]
                            if clean_expiry.endswith("Z"):
                                clean_expiry = clean_expiry[:-1] + "+00:00"
                            elif "T" in clean_expiry and "+" not in clean_expiry and "-" not in clean_expiry[clean_expiry.index("T"):]:
                                clean_expiry += "+00:00"
                            parsed_expiry = datetime.fromisoformat(clean_expiry)
                            if parsed_expiry.tzinfo is None:
                                parsed_expiry = parsed_expiry.replace(tzinfo=timezone.utc)
                            if parsed_expiry > now_dt:
                                base_dt = parsed_expiry
                        except Exception as e:
                            print(f"Error parsing current expiry for extension: {e}")
                    
                    expires_at = (base_dt + timedelta(days=30)).isoformat()
                    # Update company subscription fields
                    conn.execute(
                        """
                        UPDATE company 
                        SET subscription_tier = ?, subscription_status = ?, subscription_expires_at = ?
                        WHERE id = ?
                        """,
                        (tier, "active", expires_at, company_id)
                    )
                    conn.execute(
                        """
                        INSERT INTO subscriptions (
                            workspace_id,
                            plan,
                            status,
                            started_at,
                            expires_at,
                            trial_used,
                            messages_used,
                            storage_used,
                            created_at,
                            updated_at
                        )
                        VALUES (?, ?, 'active', ?, ?, 0, 0, 0, ?, ?)
                        """,
                        (company_id, tier, now, expires_at, now, now)
                    )
                    conn.execute(
                        "UPDATE payments SET payment_status = 'completed', payment_date = ?, expires_at = ? WHERE stripe_session_id = ?",
                        (now, expires_at, session_id)
                    )
                    conn.execute("DELETE FROM pending_payments WHERE session_id = ?", (session_id,))
                    conn.commit()

                    # Notify by email!
                    try:
                        company_row = fetch_one(conn, "SELECT name FROM company WHERE id = ?", (company_id,))
                        company_name = company_row["name"] if company_row else "Workspace Admin"
                        expiry_date_only = expires_at.split("T")[0]
                        notify_billing_event(
                            conn,
                            company_id,
                            "Subscription Package Activated",
                            f"A new subscription package has been successfully purchased and activated for {company_name}.",
                            [
                                ("Workspace Name", company_name),
                                ("Activated Plan", f"{tier.capitalize()} Plan"),
                                ("Transaction ID", payment_record["transaction_id"] if payment_record else session_id),
                                ("Amount Paid", f"${amount:.2f} {str(currency).upper()}"),
                                ("Expiration Date", expiry_date_only)
                            ]
                        )
                    except Exception as ex:
                        print(f"Error sending subscription success email notification: {ex}")

                    return jsonify({
                        "status": "success",
                        "message": f"Successfully subscribed to {tier.capitalize()} plan.",
                        "payment": {
                            "amount": amount,
                            "currency": currency,
                            "agent_name": f"{tier.capitalize()} Plan Subscription",
                            "order_id": payment_record["transaction_id"] if payment_record else session_id
                        }
                    }), 200

                return _json_error(400, "unsupported_payment_session", "Legacy agent checkout sessions are no longer supported.")

        except Exception as e:
            return _json_error(500, "internal_error", str(e))

    @app.route("/api/auth/payments", methods=["GET"])
    @require_auth(app)
    def list_payments():
        """List all payments (pending, completed, failed) for the current company."""
        company_id = int(request.auth["company_id"])
        with get_conn() as conn:
            payments = conn.execute(
                """
                SELECT
                    id,
                    workspace_id,
                    workspace_id AS company_id,
                    transaction_id,
                    transaction_id AS session_id,
                    payment_gateway,
                    amount,
                    currency,
                    payment_status,
                    payment_status AS status,
                    payment_date,
                    payment_date AS created_at,
                    payment_date AS updated_at,
                    COALESCE(
                        plan_name,
                        CASE
                            WHEN purchase_type = 'feature_addon' AND feature_key = 'whatsapp'
                                THEN 'WhatsApp Add-on'
                            ELSE 'Subscription Package'
                        END
                    ) AS agent_name,
                    plan_name,
                    purchase_type,
                    feature_key,
                    stripe_subscription_id,
                    stripe_invoice_id,
                    '' AS user_email,
                    transaction_id AS order_id
                FROM payments
                WHERE workspace_id = ?
                ORDER BY payment_date DESC
                """,
                (company_id,)
            ).fetchall()
        return jsonify([dict(p) for p in payments])

    @app.route("/api/auth/admin/payments", methods=["GET"])
    @require_auth(app)
    def admin_list_payments():
        """List all payments for super admins across all companies, including company name."""
        with get_conn() as conn:
            if not _is_super_admin(conn, request.auth):
                return _json_error(403, "forbidden", "Super admin access required.")

            payments_rows = conn.execute(
                """
                SELECT 
                    p.id,
                    p.transaction_id,
                    p.transaction_id AS session_id,
                    p.workspace_id,
                    p.workspace_id AS company_id,
                    c.name AS company_name,
                    '' AS user_email,
                    p.payment_gateway,
                    p.amount,
                    p.currency,
                    COALESCE(
                        p.plan_name,
                        CASE
                            WHEN p.purchase_type = 'feature_addon' AND p.feature_key = 'whatsapp'
                                THEN 'WhatsApp Add-on'
                            ELSE 'Subscription Package'
                        END
                    ) AS agent_name,
                    p.payment_status,
                    p.payment_status AS status,
                    p.stripe_session_id,
                    p.expires_at,
                    p.plan_name,
                    p.purchase_type,
                    p.feature_key,
                    p.stripe_subscription_id,
                    p.stripe_invoice_id,
                    p.transaction_id AS order_id,
                    p.payment_date,
                    p.payment_date AS created_at,
                    p.payment_date AS updated_at
                FROM payments p
                LEFT JOIN company c ON p.workspace_id = c.id
                ORDER BY p.payment_date DESC
                """
            ).fetchall()

        return jsonify([dict(row) for row in payments_rows])

    @app.route("/api/subscription-packages", methods=["GET"])
    def public_subscription_packages():
        with get_conn() as conn:
            packages = load_packages_config(conn)
        return jsonify({"packages": packages_for_public(packages)})

    @app.route("/api/auth/subscription-packages", methods=["GET"])
    @require_auth(app)
    def auth_subscription_packages():
        with get_conn() as conn:
            packages = load_packages_config(conn)
        return jsonify({"packages": packages_for_public(packages)})

    @app.route("/api/auth/admin/subscription-packages", methods=["GET"])
    @require_auth(app)
    def admin_get_subscription_packages():
        with get_conn() as conn:
            if not _is_super_admin(conn, request.auth):
                return _json_error(403, "forbidden", "Super admin access required.")
            packages = load_packages_config(conn)
            row = fetch_one(
                conn,
                """
                SELECT updated_at, updated_by_user_id
                FROM subscription_packages_config
                WHERE id = 1
                """,
            )
        return jsonify(
            {
                "packages": packages_for_public(packages),
                "config": normalize_packages_config(packages),
                "updated_at": row["updated_at"] if row else None,
                "updated_by_user_id": row["updated_by_user_id"] if row else None,
            }
        )

    @app.route("/api/auth/admin/subscription-packages", methods=["PUT"])
    @require_auth(app)
    def admin_update_subscription_packages():
        with get_conn() as conn:
            if not _is_super_admin(conn, request.auth):
                return _json_error(403, "forbidden", "Super admin access required.")

            payload = request.get_json() or {}
            updates = payload.get("packages") or payload.get("config") or payload
            if not isinstance(updates, dict):
                return _json_error(400, "invalid_payload", "Expected a packages object keyed by tier id.")

            filtered_updates = {
                package_id: updates[package_id]
                for package_id in EDITABLE_PACKAGE_IDS
                if package_id in updates and isinstance(updates[package_id], dict)
            }
            if not filtered_updates:
                return _json_error(400, "invalid_payload", "No editable package tiers were provided.")

            saved = save_packages_config(
                conn,
                filtered_updates,
                updated_by_user_id=int(request.auth["user_id"]),
            )
            row = fetch_one(
                conn,
                "SELECT updated_at FROM subscription_packages_config WHERE id = 1",
            )

        return jsonify(
            {
                "status": "success",
                "message": "Subscription packages updated successfully.",
                "packages": packages_for_public(saved),
                "config": normalize_packages_config(saved),
                "updated_at": row["updated_at"] if row else utcnow_iso(),
            }
        )

    @app.route("/api/auth/admin/features/whatsapp/addon", methods=["GET"])
    @require_auth(app)
    def admin_get_whatsapp_addon_config():
        with get_conn() as conn:
            if not _is_super_admin(conn, request.auth):
                return _json_error(403, "forbidden", "Super admin access required.")
            config = get_whatsapp_addon_config(conn)
        return jsonify({"config": {key: value for key, value in config.items() if key != "monthly_quota"}})

    @app.route("/api/auth/admin/features/whatsapp/addon", methods=["PUT"])
    @require_auth(app)
    def admin_update_whatsapp_addon_config():
        with get_conn() as conn:
            if not _is_super_admin(conn, request.auth):
                return _json_error(403, "forbidden", "Super admin access required.")

            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return _json_error(400, "invalid_payload", "Expected a JSON object.")

            required_fields = {"monthly_price_cents", "available"}
            if not required_fields.issubset(payload):
                return _json_error(
                    400,
                    "invalid_payload",
                    "Monthly price and availability are required.",
                )
            try:
                config = save_whatsapp_addon_config(
                    conn,
                    monthly_price_cents=payload["monthly_price_cents"],
                    is_available=payload["available"],
                    updated_by_user_id=int(request.auth["user_id"]),
                )
            except (TypeError, ValueError) as exc:
                return _json_error(400, "invalid_payload", str(exc))

            price_sync = {"status": "not_required", "updated_companies": 0}
            mismatch_sql = """
                SELECT COUNT(*) AS company_count
                FROM company_feature_subscriptions
                WHERE feature_key = ?
                  AND billing_source = 'stripe'
                  AND status IN ('active', 'cancel_at_period_end', 'past_due')
            """
            mismatch_params = [WHATSAPP_FEATURE]
            if config["stripe_price_id"]:
                mismatch_sql += """
                  AND (stripe_price_id IS NULL OR stripe_price_id <> ?)
                """
                mismatch_params.append(config["stripe_price_id"])
            mismatch = fetch_one(conn, mismatch_sql, mismatch_params)
            mismatch_count = int(mismatch["company_count"] or 0) if mismatch else 0
            if mismatch_count:
                if config["monthly_price_cents"] <= 0:
                    conn.rollback()
                    return _json_error(
                        400,
                        "invalid_price",
                        "An add-on with active subscriptions must have a price greater than zero.",
                    )
                stripe_secret_key = str(os.getenv("STRIPE_SECRET_KEY") or "").strip()
                if not stripe_secret_key:
                    price_sync = {
                        "status": "pending",
                        "updated_companies": 0,
                        "failed_companies": mismatch_count,
                        "message": "Stripe is not configured; subscription price synchronization is pending.",
                    }
                else:
                    try:
                        import stripe

                        stripe.api_key = stripe_secret_key
                        result = reconcile_whatsapp_subscription_prices(
                            conn,
                            stripe,
                            config,
                        )
                        config = set_whatsapp_addon_stripe_catalog(
                            conn,
                            stripe_product_id=result["product_id"],
                            stripe_price_id=result["price_id"],
                        )
                        failures = result["failures"]
                        price_sync = {
                            "status": "partial" if failures else "synchronized",
                            "updated_companies": len(result["updated_company_ids"]),
                            "failed_companies": len(failures),
                            "failures": failures,
                        }
                    except Exception as exc:
                        price_sync = {
                            "status": "pending",
                            "updated_companies": 0,
                            "failed_companies": mismatch_count,
                            "message": str(exc),
                        }

            legacy_migration = migrate_legacy_whatsapp_entitlements(conn)

        return jsonify(
            {
                "status": "success",
                "message": (
                    "WhatsApp add-on configuration saved; some subscription price updates are pending."
                    if price_sync["status"] in {"pending", "partial"}
                    else "WhatsApp add-on configuration updated successfully."
                ),
                "config": {key: value for key, value in config.items() if key != "monthly_quota"},
                "price_sync": price_sync,
                "legacy_migration": legacy_migration,
            }
        )

    @app.route("/api/auth/features/whatsapp", methods=["GET"])
    @require_auth(app)
    def get_whatsapp_feature_entitlement():
        company_id = int(request.auth["company_id"])
        with get_conn() as conn:
            entitlement = get_company_feature_entitlement(
                conn,
                company_id,
                WHATSAPP_FEATURE,
            )
        return jsonify(entitlement)

    @app.route(
        "/api/auth/admin/companies/<int:company_id>/features/whatsapp",
        methods=["PUT"],
    )
    @require_auth(app)
    def admin_update_whatsapp_feature_entitlement(company_id: int):
        with get_conn() as conn:
            if not _is_super_admin(conn, request.auth):
                return _json_error(403, "forbidden", "Super admin access required.")
        return _json_error(
            409,
            "whatsapp_entitlement_billing_managed",
            "WhatsApp access is managed by add-on billing and cannot be changed manually.",
        )

    def _whatsapp_webhook_details(conn, company_id: int):
        connection = get_whatsapp_connection(conn, company_id)
        if not connection["configured"]:
            return connection, None
        credentials = ensure_whatsapp_webhook_identity(
            conn,
            company_id,
            encrypt_secret=lambda value: _encrypt_val(app, value),
            decrypt_secret=lambda value: _decrypt_val(app, value),
        )
        public_backend_url = str(app.config.get("PUBLIC_BACKEND_URL") or "").strip()
        base_url = public_backend_url or request.url_root
        callback_url = (
            f"{base_url.rstrip('/')}/api/whatsapp/webhook/"
            f"{credentials['webhook_key']}"
        )
        connection = get_whatsapp_connection(conn, company_id)
        return connection, {
            "provider": connection["provider"],
            "callback_url": callback_url,
            "verify_token": (
                credentials["verify_token"] if connection["provider"] == "meta" else None
            ),
            "hmac_secret": (
                credentials["verify_token"] if connection["provider"] == "waha" else None
            ),
            "app_secret_configured": credentials["app_secret_configured"],
            "verified_at": credentials["verified_at"],
        }

    @app.route("/api/auth/whatsapp/configuration", methods=["GET", "PUT", "DELETE"])
    @require_auth(app)
    def whatsapp_configuration():
        company_id = int(request.auth["company_id"])
        if request.auth.get("role") != "admin":
            return _json_error(403, "forbidden", "Only company admins can configure WhatsApp.")

        with get_conn() as conn:
            entitlement = get_company_feature_entitlement(
                conn,
                company_id,
                WHATSAPP_FEATURE,
            )
            if request.method == "GET":
                connection, webhook = _whatsapp_webhook_details(conn, company_id)
                addon = get_company_whatsapp_addon_state(conn, company_id)
                conn.commit()
                return jsonify(
                    {
                        "entitlement": entitlement,
                        "connection": connection,
                        "webhook": webhook,
                        "addon": addon,
                    }
                )

            if not company_has_feature(conn, company_id, WHATSAPP_FEATURE):
                return _json_error(
                    403,
                    "whatsapp_not_enabled",
                    "WhatsApp is not enabled for this workspace.",
                )

            if request.method == "DELETE":
                removed = delete_whatsapp_connection(conn, company_id)
                conn.commit()
                return jsonify(
                    {
                        "status": "success",
                        "removed": removed,
                        "connection": get_whatsapp_connection(conn, company_id),
                    }
                )

            payload = request.get_json(silent=True) or {}
            try:
                existing_connection = get_whatsapp_connection(conn, company_id)
                provider = normalize_whatsapp_provider(
                    payload.get("provider") or existing_connection.get("provider")
                )
                provider_changed = provider != existing_connection.get("provider")
            except ValueError as error:
                return _json_error(400, "invalid_whatsapp_provider", str(error))
            meta_app_secret = payload.get("meta_app_secret")
            meta_phone_number_id = payload.get("meta_phone_number_id")
            meta_access_token = payload.get("meta_access_token")
            waha_session = payload.get("waha_session")
            waha_api_key = payload.get("waha_api_key")
            if meta_app_secret is not None and not str(meta_app_secret).strip():
                return _json_error(
                    400,
                    "invalid_meta_app_secret",
                    "Meta app secret cannot be empty when provided.",
                )
            if meta_phone_number_id is not None and not str(meta_phone_number_id).strip():
                return _json_error(
                    400,
                    "invalid_meta_phone_number_id",
                    "Meta Phone Number ID cannot be empty when provided.",
                )
            if meta_access_token is not None and not str(meta_access_token).strip():
                return _json_error(
                    400,
                    "invalid_meta_access_token",
                    "Meta access token cannot be empty when provided.",
                )
            if waha_session is not None and not str(waha_session).strip():
                return _json_error(
                    400,
                    "invalid_waha_session",
                    "WAHA session cannot be empty when provided.",
                )
            if waha_api_key is not None and not str(waha_api_key).strip():
                return _json_error(
                    400,
                    "invalid_waha_api_key",
                    "WAHA API key cannot be empty when provided.",
                )
            try:
                save_whatsapp_connection(
                    conn,
                    company_id,
                    payload.get("phone_number"),
                    provider=provider,
                    updated_by_user_id=int(request.auth["user_id"]),
                )
                if provider == "meta" and meta_app_secret is not None:
                    set_whatsapp_meta_app_secret(
                        conn,
                        company_id,
                        _encrypt_val(app, str(meta_app_secret).strip()),
                    )
                if provider == "meta" and (
                    meta_phone_number_id is not None or meta_access_token is not None
                ):
                    set_whatsapp_cloud_credentials(
                        conn,
                        company_id,
                        meta_phone_number_id=(
                            str(meta_phone_number_id).strip()
                            if meta_phone_number_id is not None
                            else None
                        ),
                        encrypted_access_token=(
                            _encrypt_val(app, str(meta_access_token).strip())
                            if meta_access_token is not None
                            else None
                        ),
                    )
                if provider == "waha" and (
                    waha_session is not None or waha_api_key is not None
                ):
                    set_whatsapp_waha_credentials(
                        conn,
                        company_id,
                        session=(
                            str(waha_session).strip() if waha_session is not None else None
                        ),
                        encrypted_api_key=(
                            _encrypt_val(app, str(waha_api_key).strip())
                            if waha_api_key is not None
                            else None
                        ),
                    )
                connection, webhook = _whatsapp_webhook_details(conn, company_id)
                conn.commit()
            except ValueError as error:
                conn.rollback()
                return _json_error(400, "invalid_whatsapp_configuration", str(error))
            except sqlite3.IntegrityError:
                conn.rollback()
                return _json_error(
                    409,
                    "phone_number_in_use",
                    "This WhatsApp number, Meta Phone Number ID, or WAHA session is already assigned to another company.",
                )

        if (
            not app.config.get("TESTING")
            and (
                meta_phone_number_id is not None
                or meta_access_token is not None
                or waha_session is not None
                or waha_api_key is not None
                or provider_changed
            )
        ):
            whatsapp_background_dispatcher.requeue_company(company_id)

        return jsonify(
            {
                "status": "success",
                "connection": connection,
                "webhook": webhook,
            }
        )

    @app.route("/api/auth/whatsapp/addon/usage", methods=["GET"])
    @require_auth(app)
    def get_whatsapp_addon_usage():
        if request.auth.get("role") != "admin":
            return _json_error(
                403,
                "forbidden",
                "Only company admins can view WhatsApp usage.",
            )
        company_id = int(request.auth["company_id"])
        with get_conn() as conn:
            if _is_super_admin(conn, request.auth):
                return _json_error(
                    403,
                    "forbidden",
                    "Use the company admin dashboard to view WhatsApp usage.",
                )
        return jsonify(
            {
                "shared_workspace_quota": True,
                "usage_endpoint": "/api/auth/chat-usage",
                "message": "WhatsApp agent calls use the workspace token quota.",
            }
        ), 200

    @app.route("/api/auth/whatsapp/addon/checkout", methods=["POST"])
    @require_auth(app)
    def whatsapp_addon_checkout():
        import stripe

        if request.auth.get("role") != "admin":
            return _json_error(403, "forbidden", "Only company admins can purchase WhatsApp.")

        stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
        if not stripe.api_key:
            return _json_error(500, "stripe_error", "Stripe secret key not configured on backend.")

        company_id = int(request.auth["company_id"])
        user_email = str(request.auth.get("email") or "").strip()
        configured_frontend_url = str(
            app.config.get("FRONTEND_URL") or ""
        ).strip()
        if configured_frontend_url:
            frontend_url = configured_frontend_url.rstrip("/")
        else:
            origin = request.headers.get("Origin")
            if origin:
                frontend_url = origin.rstrip("/")
            else:
                referrer = request.headers.get("Referer")
                if referrer:
                    parsed = urlparse(referrer)
                    frontend_url = f"{parsed.scheme}://{parsed.netloc}"
                else:
                    frontend_url = "http://localhost:3000"

        with get_conn() as conn:
            if company_has_feature(conn, company_id, WHATSAPP_FEATURE):
                return _json_error(
                    409,
                    "whatsapp_already_enabled",
                    "WhatsApp is already enabled for this workspace.",
                )

            config = get_whatsapp_addon_config(conn)
            subscription = get_company_whatsapp_subscription(conn, company_id)
            if not config["available"]:
                return _json_error(
                    409,
                    "whatsapp_addon_unavailable",
                    "WhatsApp is not currently available for purchase.",
                )
            if subscription["status"] not in {
                "not_purchased",
                "checkout_pending",
                "canceled",
                "expired",
            }:
                return _json_error(
                    409,
                    "whatsapp_subscription_exists",
                    "This workspace already has a WhatsApp subscription.",
                )

            existing_session_id = subscription.get("stripe_checkout_session_id")
            if subscription["status"] == "checkout_pending" and existing_session_id:
                try:
                    existing_session = stripe.checkout.Session.retrieve(existing_session_id)
                    existing_success_url = str(
                        getattr(existing_session, "success_url", "") or ""
                    ).strip()
                    expected_success_prefix = f"{frontend_url}/dashboard"
                    existing_payment_status = str(
                        getattr(existing_session, "payment_status", "") or ""
                    ).lower()
                    existing_checkout_status = str(
                        getattr(existing_session, "status", "") or ""
                    ).lower()
                    if (
                        existing_payment_status == "paid"
                        or existing_checkout_status == "complete"
                    ):
                        return jsonify(
                            {
                                "url": (
                                    f"{expected_success_prefix}"
                                    "?whatsapp_addon=success"
                                    f"&session_id={existing_session_id}"
                                ),
                                "checkout_session_id": existing_session_id,
                                "reused": True,
                                "payment_completed": True,
                            }
                        )
                    if (
                        getattr(existing_session, "status", None) == "open"
                        and getattr(existing_session, "url", None)
                        and (
                            not existing_success_url
                            or existing_success_url.startswith(expected_success_prefix)
                        )
                    ):
                        return jsonify(
                            {
                                "url": existing_session.url,
                                "checkout_session_id": existing_session.id,
                                "reused": True,
                            }
                        )
                except Exception:
                    pass

            try:
                session, product_id, price_id = create_whatsapp_checkout_session(
                    stripe,
                    config=config,
                    company_id=company_id,
                    customer_email=user_email or None,
                    customer_id=subscription.get("stripe_customer_id"),
                    frontend_url=frontend_url,
                )
            except Exception as exc:
                return _json_error(502, "stripe_error", str(exc))

            if existing_session_id:
                conn.execute(
                    """
                    UPDATE payments
                    SET payment_status = 'cancelled'
                    WHERE stripe_session_id = ? AND payment_status = 'pending'
                    """,
                    (existing_session_id,),
                )

            set_whatsapp_addon_stripe_catalog(
                conn,
                stripe_product_id=product_id,
                stripe_price_id=price_id,
            )
            customer_id = getattr(session, "customer", None) or subscription.get(
                "stripe_customer_id"
            )
            checkout = save_company_whatsapp_subscription(
                conn,
                company_id,
                status="checkout_pending",
                monthly_price_cents=config["monthly_price_cents"],
                monthly_quota=0,
                stripe_customer_id=customer_id,
                stripe_checkout_session_id=session.id,
                stripe_price_id=price_id,
            )
            conn.execute(
                """
                INSERT INTO payments (
                    workspace_id,
                    transaction_id,
                    payment_gateway,
                    amount,
                    currency,
                    payment_status,
                    payment_date,
                    stripe_session_id,
                    plan_name,
                    purchase_type,
                    feature_key
                )
                VALUES (?, ?, 'stripe', ?, ?, 'pending', ?, ?, ?, 'feature_addon', ?)
                """,
                (
                    company_id,
                    f"WA_{session.id}",
                    config["monthly_price_usd"],
                    config["currency"],
                    utcnow_iso(),
                    session.id,
                    config["display_name"],
                    WHATSAPP_FEATURE,
                ),
            )
            conn.commit()

        return jsonify(
            {
                "url": session.url,
                "checkout_session_id": session.id,
                "reused": False,
                "subscription": {
                    "status": checkout["status"],
                    "monthly_price_cents": checkout["monthly_price_cents"],
                },
            }
        )

    @app.route("/api/auth/whatsapp/addon/payment-success", methods=["POST"])
    @require_auth(app)
    def verify_whatsapp_addon_payment():
        import stripe

        if request.auth.get("role") != "admin":
            return _json_error(
                403,
                "forbidden",
                "Only company admins can verify WhatsApp payments.",
            )

        stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
        if not stripe.api_key:
            return _json_error(
                500,
                "stripe_error",
                "Stripe secret key not configured on backend.",
            )

        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id") or "").strip()
        if not session_id:
            return _json_error(400, "missing_session_id", "Session ID is required.")

        try:
            session = stripe.checkout.Session.retrieve(session_id)
        except Exception as exc:
            return _json_error(502, "stripe_error", str(exc))

        payment_status = str(getattr(session, "payment_status", "") or "").lower()
        checkout_status = str(getattr(session, "status", "") or "").lower()
        if payment_status != "paid" and checkout_status != "complete":
            return _json_error(
                400,
                "payment_not_completed",
                "Payment has not been completed successfully.",
            )

        metadata = getattr(session, "metadata", None) or {}
        if isinstance(metadata, dict):
            metadata_company_id = metadata.get("company_id")
            metadata_feature_key = metadata.get("feature_key")
        else:
            metadata_company_id = getattr(metadata, "company_id", None)
            metadata_feature_key = getattr(metadata, "feature_key", None)
        session_company_id = str(metadata_company_id or "").strip()
        feature_key = str(metadata_feature_key or "").strip()
        company_id = int(request.auth["company_id"])
        if session_company_id != str(company_id) or feature_key != WHATSAPP_FEATURE:
            return _json_error(
                403,
                "checkout_session_mismatch",
                "This Checkout Session does not belong to this workspace's WhatsApp add-on.",
            )

        returned_session_id = str(getattr(session, "id", "") or "").strip()
        if returned_session_id != session_id:
            return _json_error(
                400,
                "checkout_session_mismatch",
                "Stripe returned an unexpected Checkout Session.",
            )
        if not getattr(session, "subscription", None):
            return _json_error(
                400,
                "stripe_subscription_missing",
                "The completed Checkout Session has no Stripe subscription.",
            )

        with get_conn() as conn:
            local_subscription = get_company_whatsapp_subscription(conn, company_id)
            if local_subscription.get("stripe_checkout_session_id") != session_id:
                return _json_error(
                    404,
                    "session_not_found",
                    "Pending WhatsApp payment session was not found.",
                )

            payment = fetch_one(
                conn,
                """
                SELECT id, amount, currency, transaction_id
                FROM payments
                WHERE workspace_id = ?
                  AND stripe_session_id = ?
                  AND purchase_type = 'feature_addon'
                  AND feature_key = ?
                LIMIT 1
                """,
                (company_id, session_id, WHATSAPP_FEATURE),
            )
            if not payment:
                return _json_error(
                    404,
                    "session_not_found",
                    "Pending WhatsApp payment session was not found.",
                )

            event_created = int(getattr(session, "created", 0) or time.time())
            try:
                handled = process_whatsapp_stripe_event(
                    conn,
                    stripe,
                    {
                        "id": f"checkout-return:{session_id}",
                        "type": "checkout.session.completed",
                        "created": event_created,
                        "livemode": bool(getattr(session, "livemode", False)),
                        "data": {"object": session},
                    },
                )
            except Exception as exc:
                conn.rollback()
                return _json_error(502, "stripe_error", str(exc))
            if not handled:
                conn.rollback()
                return _json_error(
                    400,
                    "unsupported_payment_session",
                    "Checkout Session is not a WhatsApp add-on payment.",
                )

            active_subscription = get_company_whatsapp_subscription(conn, company_id)
            if active_subscription["status"] not in {
                "active",
                "cancel_at_period_end",
            }:
                conn.rollback()
                return _json_error(
                    400,
                    "subscription_not_active",
                    "Stripe has not activated this subscription.",
                )

            verified_at = utcnow_iso()
            conn.execute(
                """
                UPDATE payments
                SET payment_status = 'completed',
                    payment_date = ?,
                    expires_at = ?,
                    stripe_subscription_id = ?
                WHERE id = ?
                """,
                (
                    verified_at,
                    active_subscription["current_period_end"],
                    active_subscription["stripe_subscription_id"],
                    int(payment["id"]),
                ),
            )
            addon = get_company_whatsapp_addon_state(conn, company_id)
            entitlement = get_company_feature_entitlement(
                conn,
                company_id,
                WHATSAPP_FEATURE,
            )
            conn.commit()

        return jsonify(
            {
                "status": "success",
                "message": "WhatsApp add-on payment verified successfully.",
                "payment": {
                    "amount": payment["amount"],
                    "currency": payment["currency"],
                    "agent_name": "WhatsApp Add-on",
                    "order_id": payment["transaction_id"],
                },
                "entitlement": entitlement,
                "addon": addon,
            }
        )

    @app.route("/api/auth/whatsapp/addon/subscription", methods=["POST"])
    @require_auth(app)
    def manage_whatsapp_addon_subscription():
        import stripe

        if request.auth.get("role") != "admin":
            return _json_error(
                403,
                "forbidden",
                "Only company admins can manage the WhatsApp subscription.",
            )

        payload = request.get_json(silent=True) or {}
        action = str(payload.get("action") or "").strip().lower()
        if action not in {"cancel", "reactivate"}:
            return _json_error(
                400,
                "invalid_subscription_action",
                "action must be either cancel or reactivate.",
            )

        stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
        if not stripe.api_key:
            return _json_error(
                500,
                "stripe_error",
                "Stripe secret key not configured on backend.",
            )

        company_id = int(request.auth["company_id"])
        should_cancel = action == "cancel"
        with get_conn() as conn:
            subscription = get_company_whatsapp_subscription(conn, company_id)
            required_status = "active" if should_cancel else "cancel_at_period_end"
            if subscription["status"] != required_status:
                message = (
                    "Only an active WhatsApp subscription can be canceled."
                    if should_cancel
                    else "This WhatsApp subscription is not scheduled for cancellation."
                )
                return _json_error(409, "invalid_subscription_state", message)
            stripe_subscription_id = subscription.get("stripe_subscription_id")
            if not stripe_subscription_id:
                return _json_error(
                    409,
                    "stripe_subscription_missing",
                    "This WhatsApp subscription is not linked to Stripe.",
                )

            try:
                set_whatsapp_subscription_cancel_at_period_end(
                    stripe,
                    subscription_id=stripe_subscription_id,
                    cancel_at_period_end=should_cancel,
                )
            except Exception as exc:
                return _json_error(502, "stripe_error", str(exc))

            set_company_whatsapp_subscription_cancel_at_period_end(
                conn,
                company_id,
                cancel_at_period_end=should_cancel,
            )
            addon = get_company_whatsapp_addon_state(conn, company_id)
            entitlement = get_company_feature_entitlement(
                conn,
                company_id,
                WHATSAPP_FEATURE,
            )
            conn.commit()

        return jsonify(
            {
                "status": "success",
                "message": (
                    "WhatsApp will remain active until the current billing period ends."
                    if should_cancel
                    else "WhatsApp monthly renewal has been restored."
                ),
                "entitlement": entitlement,
                "addon": addon,
            }
        )

    @app.route("/api/stripe/webhook", methods=["POST"])
    def stripe_webhook():
        import stripe

        webhook_secret = str(os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
        stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
        if not webhook_secret or not stripe.api_key:
            return _json_error(
                500,
                "stripe_webhook_not_configured",
                "Stripe webhook credentials are not configured.",
            )

        signature = request.headers.get("Stripe-Signature")
        if not signature:
            return _json_error(
                400,
                "missing_stripe_signature",
                "Stripe-Signature header is required.",
            )
        try:
            event = stripe.Webhook.construct_event(
                request.get_data(cache=True),
                signature,
                webhook_secret,
            )
            event_payload = stripe_event_payload(event)
        except ValueError as exc:
            return _json_error(400, "invalid_stripe_payload", str(exc))
        except Exception:
            return _json_error(
                400,
                "invalid_stripe_signature",
                "Stripe webhook signature verification failed.",
            )

        event_id = str(event_payload.get("id") or "").strip()
        event_type = str(event_payload.get("type") or "").strip()
        if not event_id or not event_type:
            return _json_error(
                400,
                "invalid_stripe_event",
                "Stripe event id and type are required.",
            )

        with get_conn() as conn:
            register_stripe_webhook_event(
                conn,
                event_id=event_id,
                event_type=event_type,
                payload=event_payload,
                livemode=bool(event_payload.get("livemode")),
            )
            claimed = claim_stripe_webhook_event(conn, event_id)
            conn.commit()
            if not claimed:
                return jsonify(
                    {
                        "status": "accepted",
                        "event_id": event_id,
                        "duplicate": True,
                    }
                )

            try:
                handled = process_whatsapp_stripe_event(conn, stripe, event_payload)
                finish_stripe_webhook_event(conn, event_id, completed=True)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                finish_stripe_webhook_event(
                    conn,
                    event_id,
                    completed=False,
                    error=str(exc),
                )
                conn.commit()
                return _json_error(
                    500,
                    "stripe_webhook_processing_failed",
                    "Stripe webhook processing failed and will be retried.",
                )

        return jsonify(
            {
                "status": "processed",
                "event_id": event_id,
                "handled": handled,
                "duplicate": False,
            }
        )

    @app.route(
        "/api/whatsapp/webhook/<string:webhook_key>",
        methods=["GET", "POST"],
    )
    def receive_whatsapp_webhook(webhook_key: str):
        with get_conn() as conn:
            connection = get_webhook_connection(conn, webhook_key)
            if not connection or not company_has_feature(
                conn,
                int(connection["company_id"]),
                WHATSAPP_FEATURE,
            ):
                return _json_error(404, "webhook_not_found", "Webhook was not found.")

            company_id = int(connection["company_id"])
            provider = normalize_whatsapp_provider(connection["provider"])
            if request.method == "GET":
                if provider != "meta":
                    return _json_error(
                        405,
                        "webhook_verification_not_supported",
                        "WAHA webhooks are activated by the first valid signed event.",
                    )
                mode = request.args.get("hub.mode", "")
                supplied_token = request.args.get("hub.verify_token", "")
                challenge = request.args.get("hub.challenge", "")
                expected_token = _decrypt_val(app, connection["verification_token_secret"])
                if (
                    mode != "subscribe"
                    or not challenge
                    or not expected_token
                    or not secrets.compare_digest(supplied_token, expected_token)
                ):
                    return _json_error(403, "webhook_verification_failed", "Webhook verification failed.")
                mark_webhook_verified(conn, company_id)
                conn.commit()
                return Response(challenge, status=200, mimetype="text/plain")

            raw_body = request.get_data(cache=True)
            if provider == "waha":
                webhook_secret = _decrypt_val(app, connection["verification_token_secret"])
                if not verify_waha_signature(
                    raw_body,
                    request.headers.get("X-Webhook-Hmac", ""),
                    request.headers.get("X-Webhook-Hmac-Algorithm", "sha512"),
                    webhook_secret or "",
                ):
                    return _json_error(
                        403, "invalid_webhook_signature", "Invalid WAHA webhook signature."
                    )
            else:
                app_secret = _decrypt_val(app, connection["meta_app_secret"])
                signature = request.headers.get("X-Hub-Signature-256", "")
                if not verify_meta_signature(raw_body, signature, app_secret or ""):
                    return _json_error(403, "invalid_webhook_signature", "Invalid webhook signature.")

            payload = request.get_json(silent=True)
            if not isinstance(payload, dict):
                return _json_error(400, "invalid_webhook_payload", "Expected a JSON webhook payload.")
            if provider == "waha":
                expected_session = str(connection["waha_session"] or "").strip()
                supplied_session = str(payload.get("session") or "").strip()
                if not expected_session or supplied_session != expected_session:
                    return _json_error(
                        403,
                        "invalid_waha_session",
                        "WAHA event does not belong to this workspace session.",
                    )
                statuses = extract_waha_status_updates(
                    payload,
                    expected_session=expected_session,
                )
                extracted_messages = extract_waha_inbound_messages(
                    payload,
                    expected_session=expected_session,
                )
                messages = extracted_messages
                mark_webhook_verified(conn, company_id)
            else:
                statuses = extract_status_updates(payload)
                extracted_messages = extract_inbound_messages(payload)
                configured_phone_number_id = str(
                    connection["meta_phone_number_id"] or ""
                ).strip()
                messages = [
                    message
                    for message in extracted_messages
                    if not configured_phone_number_id
                    or message.get("recipient_phone_number_id") == configured_phone_number_id
                ]
            status_result = apply_outbound_status_updates(conn, company_id, statuses)
            result = store_inbound_messages(conn, company_id, messages)
            processing = process_whatsapp_messages(conn, company_id, messages)
            conn.commit()
            if not app.config.get("TESTING"):
                whatsapp_background_dispatcher.submit(processing["dispatch_ids"])
            return jsonify(
                {
                    "status": "accepted",
                    "received": len(extracted_messages),
                    "ignored_recipient": len(extracted_messages) - len(messages),
                    **result,
                    "processed": processing["processed"],
                    "status_updates": status_result["updated"],
                }
            )

    @app.route("/api/auth/cancel-subscription", methods=["POST"])
    @require_auth(app)
    def cancel_subscription():
        user_id = int(request.auth["user_id"])
        with get_conn() as conn:
            user = fetch_one(conn, "SELECT company_id, role FROM users WHERE id = ?", (user_id,))
            if not user:
                return _json_error(404, "not_found", "User not found.")
            
            if user["role"] != "admin":
                return _json_error(403, "forbidden", "Only company admins can cancel the subscription.")
            
            company_id = user["company_id"]
            if not company_id:
                return _json_error(400, "invalid_user", "User is not associated with a company.")
            
            company = fetch_one(conn, "SELECT subscription_tier FROM company WHERE id = ?", (company_id,))
            if not company:
                return _json_error(404, "not_found", "Company not found.")
            
            if company["subscription_tier"] == "free":
                return _json_error(400, "bad_request", "Company is already on the Free tier.")
            
            now = utcnow_iso()
            
            # Check for refund eligibility: 40% of the last completed payment within 30 days
            last_payment = fetch_one(
                conn,
                """
                SELECT id, amount, plan_name, payment_date 
                FROM payments 
                WHERE workspace_id = ? AND payment_status = 'completed' AND amount > 0
                ORDER BY id DESC LIMIT 1
                """,
                (company_id,)
            )
            
            refund_amount = 0.0
            refunded_tx_id = None
            if last_payment:
                try:
                    pay_date_str = last_payment["payment_date"]
                    if pay_date_str.endswith('Z'):
                        pay_date_str = pay_date_str[:-1] + '+00:00'
                    elif "T" in pay_date_str and "+" not in pay_date_str and "-" not in pay_date_str[pay_date_str.index("T"):]:
                        pay_date_str += "+00:00"
                    pay_date = datetime.fromisoformat(pay_date_str)
                    if pay_date.tzinfo is None:
                        pay_date = pay_date.replace(tzinfo=timezone.utc)
                    
                    now_dt = datetime.now(timezone.utc)
                    # Check if within 30 days
                    if (now_dt - pay_date).days <= 30:
                        refund_amount = float(last_payment["amount"]) * 0.40
                        # Record refund payment
                        now_str = utcnow_iso()
                        temp_tx = f"TEMP_REFUND_{int(now_dt.timestamp())}_{company_id}"
                        cur = conn.execute(
                            """
                            INSERT INTO payments (workspace_id, transaction_id, payment_gateway, amount, currency, payment_status, payment_date, plan_name)
                            VALUES (?, ?, 'stripe_refund', ?, 'usd', 'completed', ?, ?)
                            """,
                            (company_id, temp_tx, -refund_amount, now_str, f"40% Refund - {last_payment['plan_name'] or 'Subscription'}")
                        )
                        inserted_id = cur.lastrowid
                        refunded_tx_id = f"T_ID {inserted_id:02d}"
                        conn.execute(
                            "UPDATE payments SET transaction_id = ? WHERE id = ?",
                            (refunded_tx_id, inserted_id)
                        )
                except Exception as e:
                    print(f"Error processing refund calculation: {e}")

            # Update company to Free tier
            conn.execute(
                """
                UPDATE company
                SET subscription_tier = 'free', subscription_status = 'active', subscription_expires_at = NULL
                WHERE id = ?
                """,
                (company_id,)
            )
            # Update active subscriptions in subscriptions table
            conn.execute(
                """
                UPDATE subscriptions
                SET status = 'cancelled', updated_at = ?
                WHERE workspace_id = ? AND status = 'active'
                """,
                (now, company_id)
            )
            conn.commit()

            # Notify by email!
            try:
                company_row = fetch_one(conn, "SELECT name FROM company WHERE id = ?", (company_id,))
                company_name = company_row["name"] if company_row else "Workspace Admin"
                details = [
                    ("Workspace Name", company_name),
                    ("Status", "Cancelled (Downgraded to Free)"),
                    ("Effective Date", now.split("T")[0])
                ]
                if refund_amount > 0:
                    details.append(("Processed Refund", f"${refund_amount:.2f} USD"))
                    details.append(("Refund Transaction ID", refunded_tx_id))
                    subject = "Subscription Cancelled & Refund Processed"
                    intro = f"The subscription for {company_name} has been cancelled, and a 40% refund of ${refund_amount:.2f} has been successfully processed."
                else:
                    subject = "Subscription Cancelled"
                    intro = f"The subscription for {company_name} has been cancelled, and the workspace has been reset to the Free tier."
                
                notify_billing_event(
                    conn,
                    company_id,
                    subject,
                    intro,
                    details
                )
            except Exception as ex:
                print(f"Error sending cancellation email notification: {ex}")
            
            if refund_amount > 0:
                msg = f"Subscription cancelled successfully. Your plan has been reset to Free and a 40% refund of ${refund_amount:.2f} has been processed ({refunded_tx_id})."
            else:
                msg = "Subscription cancelled successfully. Your plan has been reset to Free. (No refund processed as no payment was found within the last 30 days)."
            
        return jsonify({"status": "success", "message": msg})

    @app.route("/api/auth/email-config-test", methods=["GET"])
    def email_config_test():
        """Diagnostic endpoint to check email configuration (no auth required)."""
        config_status = {
            "mail_server": bool(app.config.get("MAIL_SERVER")),
            "mail_port": app.config.get("MAIL_PORT"),
            "mail_username": bool(app.config.get("MAIL_USERNAME")),
            "mail_password": bool(app.config.get("MAIL_PASSWORD")),
            "mail_default_sender": app.config.get("MAIL_DEFAULT_SENDER"),
            "mail_use_tls": app.config.get("MAIL_USE_TLS"),
            "mail_use_ssl": app.config.get("MAIL_USE_SSL"),
        }
        
        all_configured = all([
            config_status["mail_server"],
            config_status["mail_username"],
            config_status["mail_password"],
            config_status["mail_default_sender"]
        ])
        
        return jsonify({
            "status": "OK" if all_configured else "INCOMPLETE",
            "config": config_status,
            "message": "Email configuration is complete and ready." if all_configured else "Email configuration is incomplete. Please check your environment variables."
        })

    @app.route("/api/auth/send-test-email", methods=["POST"])
    def send_test_email():
        """Send a test email (admin only, useful for debugging)."""
        body = request.get_json() or {}
        test_email = body.get("email", "").strip()
        
        if not test_email:
            return _json_error(400, "missing_email", "Test email address required")
        
        if not is_valid_email(test_email):
            return _json_error(400, "invalid_email", "Invalid email address")
        
        try:
            result = send_notification_email(
                subject="Test Email from Agent Store",
                recipients=[test_email],
                body="This is a test email to verify your email configuration is working correctly.",
                details=[
                    ("Test Time", str(datetime.now(timezone.utc))),
                    ("Recipient", test_email),
                ],
                cta_text="Visit Agent Store",
                cta_path="/dashboard"
            )
            
            if result:
                return jsonify({
                    "status": "success",
                    "message": f"Test email sent successfully to {test_email}"
                })
            else:
                return jsonify({
                    "status": "failure",
                    "message": "Failed to send test email. Check server logs for details."
                }), 500
        except Exception as e:
            print(f"Error sending test email: {e}")
            return _json_error(500, "email_error", f"Failed to send test email: {str(e)}")

    if not app.config.get("TESTING"):
        whatsapp_background_dispatcher.start(
            app,
            decrypt_secret=lambda value: _decrypt_val(app, value),
            workspace_quota_checker=_check_chat_quota,
            workspace_metered_runner=_run_metered_chat,
        )
