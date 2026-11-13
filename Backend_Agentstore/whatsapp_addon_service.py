from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any

from auth_sqlite import calendar_month_period, fetch_one, utcnow_iso
from company_feature_service import WHATSAPP_FEATURE


WHATSAPP_ADDON_NAME = "WhatsApp Add-on"
WHATSAPP_ADDON_STATUSES = frozenset(
    {
        "checkout_pending",
        "active",
        "cancel_at_period_end",
        "past_due",
        "canceled",
        "expired",
    }
)
_UNSET = object()


def ensure_whatsapp_addon_config(conn) -> None:
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO feature_addon_configs (
            feature_key,
            display_name,
            monthly_price_cents,
            currency,
            monthly_quota,
            is_available,
            stripe_product_id,
            stripe_price_id,
            created_at,
            updated_at,
            updated_by_user_id
        )
        VALUES (?, ?, 0, 'usd', 0, 0, NULL, NULL, ?, ?, NULL)
        ON CONFLICT(feature_key) DO NOTHING
        """,
        (WHATSAPP_FEATURE, WHATSAPP_ADDON_NAME, now, now),
    )


def get_whatsapp_addon_config(conn) -> dict[str, Any]:
    ensure_whatsapp_addon_config(conn)
    row = fetch_one(
        conn,
        """
        SELECT
            feature_key,
            display_name,
            monthly_price_cents,
            currency,
            monthly_quota,
            is_available,
            stripe_product_id,
            stripe_price_id,
            created_at,
            updated_at,
            updated_by_user_id
        FROM feature_addon_configs
        WHERE feature_key = ?
        LIMIT 1
        """,
        (WHATSAPP_FEATURE,),
    )
    return _addon_config_payload(row)


def save_whatsapp_addon_config(
    conn,
    *,
    monthly_price_cents: int,
    is_available: bool,
    monthly_quota: int | None = None,
    stripe_product_id: str | None | object = _UNSET,
    stripe_price_id: str | None | object = _UNSET,
    updated_by_user_id: int | None = None,
) -> dict[str, Any]:
    price_cents = _non_negative_int(monthly_price_cents, "monthly_price_cents")
    # New callers omit this deprecated value and keep the legacy column at
    # zero. Accepting an explicit value preserves old migrations/tests; it is
    # no longer exposed or used to authorize WhatsApp agent calls.
    quota = (
        0
        if monthly_quota is None
        else _non_negative_int(monthly_quota, "monthly_quota")
    )
    if type(is_available) is not bool:
        raise TypeError("is_available must be a boolean.")
    if is_available and price_cents <= 0:
        raise ValueError("An available paid add-on must have a monthly price greater than zero.")
    if monthly_quota is not None and is_available and quota <= 0:
        raise ValueError("A legacy explicit monthly quota must be greater than zero.")

    current = get_whatsapp_addon_config(conn)
    price_changed = price_cents != current["monthly_price_cents"]
    product_id = (
        current["stripe_product_id"]
        if stripe_product_id is _UNSET
        else _optional_text(stripe_product_id)
    )
    price_id = (
        (None if price_changed else current["stripe_price_id"])
        if stripe_price_id is _UNSET
        else _optional_text(stripe_price_id)
    )
    now = utcnow_iso()
    conn.execute(
        """
        UPDATE feature_addon_configs
        SET monthly_price_cents = ?,
            currency = 'usd',
            monthly_quota = ?,
            is_available = ?,
            stripe_product_id = ?,
            stripe_price_id = ?,
            updated_at = ?,
            updated_by_user_id = ?
        WHERE feature_key = ?
        """,
        (
            price_cents,
            quota,
            int(is_available),
            product_id,
            price_id,
            now,
            updated_by_user_id,
            WHATSAPP_FEATURE,
        ),
    )
    return get_whatsapp_addon_config(conn)


def set_whatsapp_addon_stripe_catalog(
    conn,
    *,
    stripe_product_id: str,
    stripe_price_id: str,
) -> dict[str, Any]:
    product_id = _optional_text(stripe_product_id)
    price_id = _optional_text(stripe_price_id)
    if not product_id or not price_id:
        raise ValueError("Stripe product and price ids are required.")
    ensure_whatsapp_addon_config(conn)
    conn.execute(
        """
        UPDATE feature_addon_configs
        SET stripe_product_id = ?,
            stripe_price_id = ?,
            updated_at = ?
        WHERE feature_key = ?
        """,
        (product_id, price_id, utcnow_iso(), WHATSAPP_FEATURE),
    )
    return get_whatsapp_addon_config(conn)


def get_company_whatsapp_subscription(conn, company_id: int) -> dict[str, Any]:
    row = fetch_one(
        conn,
        """
        SELECT *
        FROM company_feature_subscriptions
        WHERE company_id = ? AND feature_key = ?
        LIMIT 1
        """,
        (company_id, WHATSAPP_FEATURE),
    )
    if not row:
        return {
            "id": None,
            "company_id": int(company_id),
            "feature": WHATSAPP_FEATURE,
            "status": "not_purchased",
            "billing_source": None,
            "stripe_customer_id": None,
            "stripe_subscription_id": None,
            "stripe_subscription_item_id": None,
            "stripe_checkout_session_id": None,
            "stripe_price_id": None,
            "monthly_price_cents": None,
            "currency": "usd",
            "monthly_quota": None,
            "current_period_start": None,
            "current_period_end": None,
            "cancel_at_period_end": False,
            "canceled_at": None,
            "stripe_event_created_at": 0,
            "created_at": None,
            "updated_at": None,
        }
    return _subscription_payload(row)


def get_company_whatsapp_addon_state(conn, company_id: int) -> dict[str, Any]:
    config = get_whatsapp_addon_config(conn)
    ensure_legacy_whatsapp_billing_period(conn, company_id)
    subscription = get_company_whatsapp_subscription(conn, company_id)
    status = subscription["status"]
    return {
        "uses_workspace_token_quota": True,
        "offer": {
            "feature": config["feature"],
            "display_name": config["display_name"],
            "monthly_price_cents": config["monthly_price_cents"],
            "monthly_price_usd": config["monthly_price_usd"],
            "currency": config["currency"],
            "available": config["available"],
        },
        "subscription": {
            "status": status,
            "billing_source": subscription["billing_source"],
            "monthly_price_cents": subscription["monthly_price_cents"],
            "currency": subscription["currency"],
            "current_period_start": subscription["current_period_start"],
            "current_period_end": subscription["current_period_end"],
            "cancel_at_period_end": subscription["cancel_at_period_end"],
            "canceled_at": subscription["canceled_at"],
        },
        "can_purchase": bool(config["available"])
        and status in {"not_purchased", "checkout_pending", "canceled", "expired"},
        "can_cancel": status == "active"
        and bool(subscription["stripe_subscription_id"]),
        "can_reactivate": status == "cancel_at_period_end"
        and bool(subscription["stripe_subscription_id"]),
    }


def set_company_whatsapp_subscription_cancel_at_period_end(
    conn,
    company_id: int,
    *,
    cancel_at_period_end: bool,
) -> dict[str, Any]:
    if type(cancel_at_period_end) is not bool:
        raise TypeError("cancel_at_period_end must be a boolean.")
    subscription = get_company_whatsapp_subscription(conn, company_id)
    expected_status = "active" if cancel_at_period_end else "cancel_at_period_end"
    if subscription["status"] != expected_status:
        raise ValueError(
            "Only active subscriptions can be canceled, and only subscriptions "
            "scheduled for cancellation can be reactivated."
        )
    conn.execute(
        """
        UPDATE company_feature_subscriptions
        SET status = ?, cancel_at_period_end = ?, updated_at = ?
        WHERE company_id = ? AND feature_key = ?
        """,
        (
            "cancel_at_period_end" if cancel_at_period_end else "active",
            int(cancel_at_period_end),
            utcnow_iso(),
            int(company_id),
            WHATSAPP_FEATURE,
        ),
    )
    return get_company_whatsapp_subscription(conn, company_id)


def save_company_whatsapp_subscription(
    conn,
    company_id: int,
    *,
    status: str,
    monthly_price_cents: int,
    monthly_quota: int,
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    stripe_subscription_item_id: str | None = None,
    stripe_checkout_session_id: str | None = None,
    stripe_price_id: str | None = None,
    current_period_start: str | None = None,
    current_period_end: str | None = None,
    cancel_at_period_end: bool = False,
    canceled_at: str | None = None,
    stripe_event_created_at: int | None = None,
) -> dict[str, Any]:
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in WHATSAPP_ADDON_STATUSES:
        raise ValueError(f"Unsupported WhatsApp add-on subscription status: {status}")
    if type(cancel_at_period_end) is not bool:
        raise TypeError("cancel_at_period_end must be a boolean.")
    price_cents = _non_negative_int(monthly_price_cents, "monthly_price_cents")
    quota = _non_negative_int(monthly_quota, "monthly_quota")
    event_created = (
        0
        if stripe_event_created_at is None
        else _non_negative_int(stripe_event_created_at, "stripe_event_created_at")
    )
    _validate_period(current_period_start, current_period_end)
    if normalized_status in {"active", "cancel_at_period_end", "past_due"}:
        if not current_period_start or not current_period_end:
            raise ValueError(f"{normalized_status} subscriptions require a billing period.")

    ensure_whatsapp_addon_config(conn)
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO company_feature_subscriptions (
            company_id,
            feature_key,
            status,
            stripe_customer_id,
            stripe_subscription_id,
            stripe_subscription_item_id,
            stripe_checkout_session_id,
            stripe_price_id,
            monthly_price_cents,
            currency,
            monthly_quota,
            current_period_start,
            current_period_end,
            cancel_at_period_end,
            canceled_at,
            stripe_event_created_at,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'usd', ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(company_id, feature_key) DO UPDATE SET
            status = excluded.status,
            stripe_customer_id = excluded.stripe_customer_id,
            stripe_subscription_id = excluded.stripe_subscription_id,
            stripe_subscription_item_id = excluded.stripe_subscription_item_id,
            stripe_checkout_session_id = excluded.stripe_checkout_session_id,
            stripe_price_id = excluded.stripe_price_id,
            monthly_price_cents = excluded.monthly_price_cents,
            currency = excluded.currency,
            monthly_quota = excluded.monthly_quota,
            current_period_start = excluded.current_period_start,
            current_period_end = excluded.current_period_end,
            cancel_at_period_end = excluded.cancel_at_period_end,
            canceled_at = excluded.canceled_at,
            stripe_event_created_at = CASE
                WHEN excluded.stripe_event_created_at > 0
                    THEN excluded.stripe_event_created_at
                ELSE company_feature_subscriptions.stripe_event_created_at
            END,
            updated_at = excluded.updated_at
        """,
        (
            company_id,
            WHATSAPP_FEATURE,
            normalized_status,
            _optional_text(stripe_customer_id),
            _optional_text(stripe_subscription_id),
            _optional_text(stripe_subscription_item_id),
            _optional_text(stripe_checkout_session_id),
            _optional_text(stripe_price_id),
            price_cents,
            quota,
            current_period_start,
            current_period_end,
            int(cancel_at_period_end),
            canceled_at,
            event_created,
            now,
            now,
        ),
    )
    return get_company_whatsapp_subscription(conn, company_id)


def ensure_whatsapp_usage_period(
    conn,
    *,
    company_id: int,
    feature_subscription_id: int,
    period_start: str,
    period_end: str,
    quota_limit: int,
) -> dict[str, Any]:
    quota = _non_negative_int(quota_limit, "quota_limit")
    _validate_period(period_start, period_end, required=True)
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO company_feature_usage_periods (
            company_id,
            feature_key,
            feature_subscription_id,
            period_start,
            period_end,
            quota_limit,
            units_used,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
        ON CONFLICT(company_id, feature_key, period_start) DO NOTHING
        """,
        (
            company_id,
            WHATSAPP_FEATURE,
            feature_subscription_id,
            period_start,
            period_end,
            quota,
            now,
            now,
        ),
    )
    row = fetch_one(
        conn,
        """
        SELECT *
        FROM company_feature_usage_periods
        WHERE company_id = ? AND feature_key = ? AND period_start = ?
        LIMIT 1
        """,
        (company_id, WHATSAPP_FEATURE, period_start),
    )
    return _usage_period_payload(row)


def get_whatsapp_usage_period(
    conn,
    company_id: int,
    *,
    at_time: str,
) -> dict[str, Any] | None:
    row = fetch_one(
        conn,
        """
        SELECT *
        FROM company_feature_usage_periods
        WHERE company_id = ?
          AND feature_key = ?
          AND period_start <= ?
          AND period_end > ?
        ORDER BY period_start DESC
        LIMIT 1
        """,
        (company_id, WHATSAPP_FEATURE, at_time, at_time),
    )
    return _usage_period_payload(row) if row else None


def get_company_whatsapp_usage_summary(
    conn,
    company_id: int,
    *,
    at_time: str | None = None,
) -> dict[str, Any]:
    ensure_legacy_whatsapp_billing_period(conn, company_id, at_time=at_time)
    subscription = get_company_whatsapp_subscription(conn, company_id)
    now = at_time or utcnow_iso()
    usage = None
    period_start = subscription["current_period_start"]
    period_end = subscription["current_period_end"]
    if subscription["id"] and period_start:
        row = fetch_one(
            conn,
            """
            SELECT *
            FROM company_feature_usage_periods
            WHERE company_id = ? AND feature_key = ? AND period_start = ?
            LIMIT 1
            """,
            (company_id, WHATSAPP_FEATURE, period_start),
        )
        usage = _usage_period_payload(row) if row else None
    if (
        not usage
        and subscription["status"] in {"active", "cancel_at_period_end"}
        and period_start
        and period_end
        and _iso_datetime(period_start, "current_period_start")
        <= _iso_datetime(now, "at_time")
        < _iso_datetime(period_end, "current_period_end")
    ):
        usage = ensure_whatsapp_usage_period(
            conn,
            company_id=company_id,
            feature_subscription_id=subscription["id"],
            period_start=period_start,
            period_end=period_end,
            quota_limit=subscription["monthly_quota"],
        )

    quota_limit = usage["quota_limit"] if usage else subscription["monthly_quota"]
    units_used = usage["units_used"] if usage else 0
    remaining = usage["remaining"] if usage else max(0, int(quota_limit or 0))
    usage_percent = (
        min(100, round((units_used / quota_limit) * 100, 2))
        if quota_limit
        else 0
    )
    return {
        "feature": WHATSAPP_FEATURE,
        "subscription_status": subscription["status"],
        "billing_source": subscription["billing_source"],
        "has_subscription": subscription["id"] is not None,
        "enabled": subscription["status"] in {"active", "cancel_at_period_end"},
        "cancel_at_period_end": subscription["cancel_at_period_end"],
        "period_start": usage["period_start"] if usage else period_start,
        "period_end": usage["period_end"] if usage else period_end,
        "quota_limit": quota_limit,
        "units_used": units_used,
        "remaining": remaining,
        "usage_percent": usage_percent,
        "quota_reached": bool(quota_limit is not None and units_used >= quota_limit),
    }


def consume_whatsapp_agent_quota(
    conn,
    company_id: int,
    *,
    idempotency_key: str,
    at_time: str | None = None,
) -> dict[str, Any]:
    usage_key = str(idempotency_key or "").strip()
    if not usage_key:
        raise ValueError("A WhatsApp usage idempotency key is required.")
    now = at_time or utcnow_iso()
    current_time = _iso_datetime(now, "at_time")
    ensure_legacy_whatsapp_billing_period(conn, company_id, at_time=now)
    subscription = get_company_whatsapp_subscription(conn, company_id)
    if subscription["status"] not in {"active", "cancel_at_period_end"}:
        return {
            "allowed": False,
            "counted": False,
            "reason": "subscription_inactive",
            "usage": None,
        }

    period_start = subscription["current_period_start"]
    period_end = subscription["current_period_end"]
    if (
        not period_start
        or not period_end
        or not (
            _iso_datetime(period_start, "current_period_start")
            <= current_time
            < _iso_datetime(period_end, "current_period_end")
        )
    ):
        return {
            "allowed": False,
            "counted": False,
            "reason": "billing_period_unavailable",
            "usage": None,
        }

    existing_event = fetch_one(
        conn,
        """
        SELECT usage_period_id
        FROM company_feature_usage_events
        WHERE company_id = ? AND feature_key = ? AND idempotency_key = ?
        LIMIT 1
        """,
        (company_id, WHATSAPP_FEATURE, usage_key),
    )
    if existing_event:
        row = fetch_one(
            conn,
            "SELECT * FROM company_feature_usage_periods WHERE id = ? LIMIT 1",
            (int(existing_event["usage_period_id"]),),
        )
        return {
            "allowed": True,
            "counted": False,
            "reason": "already_counted",
            "usage": _usage_period_payload(row),
        }

    usage = get_whatsapp_usage_period(conn, company_id, at_time=now)
    if not usage:
        usage = ensure_whatsapp_usage_period(
            conn,
            company_id=company_id,
            feature_subscription_id=subscription["id"],
            period_start=period_start,
            period_end=period_end,
            quota_limit=subscription["monthly_quota"],
        )

    updated_at = utcnow_iso()
    reservation = conn.execute(
        """
        UPDATE company_feature_usage_periods
        SET units_used = units_used + 1, updated_at = ?
        WHERE id = ? AND units_used < quota_limit
        """,
        (updated_at, usage["id"]),
    )
    if reservation.rowcount <= 0:
        existing_event = fetch_one(
            conn,
            """
            SELECT usage_period_id
            FROM company_feature_usage_events
            WHERE company_id = ? AND feature_key = ? AND idempotency_key = ?
            LIMIT 1
            """,
            (company_id, WHATSAPP_FEATURE, usage_key),
        )
        if existing_event:
            row = fetch_one(
                conn,
                "SELECT * FROM company_feature_usage_periods WHERE id = ? LIMIT 1",
                (int(existing_event["usage_period_id"]),),
            )
            return {
                "allowed": True,
                "counted": False,
                "reason": "already_counted",
                "usage": _usage_period_payload(row),
            }
        current = fetch_one(
            conn,
            "SELECT * FROM company_feature_usage_periods WHERE id = ? LIMIT 1",
            (usage["id"],),
        )
        return {
            "allowed": False,
            "counted": False,
            "reason": "quota_exceeded",
            "usage": _usage_period_payload(current),
        }

    event = conn.execute(
        """
        INSERT INTO company_feature_usage_events (
            usage_period_id,
            company_id,
            feature_key,
            idempotency_key,
            event_type,
            units,
            created_at
        )
        VALUES (?, ?, ?, ?, 'agent_invocation', 1, ?)
        ON CONFLICT(company_id, feature_key, idempotency_key) DO NOTHING
        """,
        (usage["id"], company_id, WHATSAPP_FEATURE, usage_key, updated_at),
    )
    counted = event.rowcount > 0
    if not counted:
        conn.execute(
            """
            UPDATE company_feature_usage_periods
            SET units_used = units_used - 1, updated_at = ?
            WHERE id = ? AND units_used > 0
            """,
            (updated_at, usage["id"]),
        )
    current = fetch_one(
        conn,
        "SELECT * FROM company_feature_usage_periods WHERE id = ? LIMIT 1",
        (usage["id"],),
    )
    return {
        "allowed": True,
        "counted": counted,
        "reason": "counted" if counted else "already_counted",
        "usage": _usage_period_payload(current),
    }


def ensure_legacy_whatsapp_billing_period(
    conn,
    company_id: int,
    *,
    at_time: str | None = None,
) -> dict[str, Any]:
    subscription = get_company_whatsapp_subscription(conn, company_id)
    if subscription["billing_source"] != "legacy":
        return subscription

    now = at_time or utcnow_iso()
    current_time = _iso_datetime(now, "at_time")
    period_start = subscription["current_period_start"]
    period_end = subscription["current_period_end"]
    if (
        period_start
        and period_end
        and _iso_datetime(period_start, "current_period_start")
        <= current_time
        < _iso_datetime(period_end, "current_period_end")
    ):
        return subscription

    next_start, next_end = calendar_month_period(now)
    config = get_whatsapp_addon_config(conn)
    quota = int(config["monthly_quota"] or subscription["monthly_quota"] or 0)
    if quota <= 0:
        return subscription
    renewed = save_company_whatsapp_subscription(
        conn,
        company_id,
        status="active",
        monthly_price_cents=0,
        monthly_quota=quota,
        current_period_start=next_start,
        current_period_end=next_end,
    )
    return renewed


def register_stripe_webhook_event(
    conn,
    *,
    event_id: str,
    event_type: str,
    payload: dict[str, Any],
    livemode: bool,
) -> bool:
    normalized_event_id = str(event_id or "").strip()
    normalized_event_type = str(event_type or "").strip()
    if not normalized_event_id or not normalized_event_type:
        raise ValueError("Stripe event id and type are required.")
    if type(livemode) is not bool:
        raise TypeError("livemode must be a boolean.")
    if not isinstance(payload, dict):
        raise TypeError("Stripe event payload must be an object.")
    now = utcnow_iso()
    cursor = conn.execute(
        """
        INSERT INTO stripe_webhook_events (
            event_id,
            event_type,
            livemode,
            payload_json,
            processing_status,
            attempt_count,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, 'received', 0, ?, ?)
        ON CONFLICT(event_id) DO NOTHING
        """,
        (
            normalized_event_id,
            normalized_event_type,
            int(livemode),
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            now,
            now,
        ),
    )
    return cursor.rowcount > 0


def claim_stripe_webhook_event(conn, event_id: str) -> bool:
    stale_before = (
        datetime.now(timezone.utc) - timedelta(minutes=5)
    ).isoformat()
    cursor = conn.execute(
        """
        UPDATE stripe_webhook_events
        SET processing_status = 'processing',
            attempt_count = attempt_count + 1,
            last_error = NULL,
            updated_at = ?
        WHERE event_id = ?
          AND (
            processing_status IN ('received', 'failed')
            OR (processing_status = 'processing' AND updated_at < ?)
          )
        """,
        (utcnow_iso(), str(event_id or "").strip(), stale_before),
    )
    return cursor.rowcount > 0


def finish_stripe_webhook_event(
    conn,
    event_id: str,
    *,
    completed: bool,
    error: str | None = None,
) -> None:
    if type(completed) is not bool:
        raise TypeError("completed must be a boolean.")
    now = utcnow_iso()
    conn.execute(
        """
        UPDATE stripe_webhook_events
        SET processing_status = ?,
            last_error = ?,
            processed_at = ?,
            updated_at = ?
        WHERE event_id = ?
        """,
        (
            "completed" if completed else "failed",
            None if completed else str(error or "Unknown Stripe event error.")[:1000],
            now if completed else None,
            now,
            str(event_id or "").strip(),
        ),
    )


def _addon_config_payload(row) -> dict[str, Any]:
    return {
        "feature": row["feature_key"],
        "display_name": row["display_name"],
        "monthly_price_cents": int(row["monthly_price_cents"]),
        "monthly_price_usd": int(row["monthly_price_cents"]) / 100,
        "currency": row["currency"],
        "monthly_quota": int(row["monthly_quota"]),
        "available": bool(row["is_available"]),
        "stripe_product_id": row["stripe_product_id"],
        "stripe_price_id": row["stripe_price_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "updated_by_user_id": row["updated_by_user_id"],
    }


def _subscription_payload(row) -> dict[str, Any]:
    row_keys = set(row.keys())
    return {
        "id": int(row["id"]),
        "company_id": int(row["company_id"]),
        "feature": row["feature_key"],
        "status": row["status"],
        "billing_source": row["billing_source"] if "billing_source" in row_keys else "stripe",
        "stripe_customer_id": row["stripe_customer_id"],
        "stripe_subscription_id": row["stripe_subscription_id"],
        "stripe_subscription_item_id": row["stripe_subscription_item_id"],
        "stripe_checkout_session_id": row["stripe_checkout_session_id"],
        "stripe_price_id": row["stripe_price_id"],
        "monthly_price_cents": int(row["monthly_price_cents"]),
        "currency": row["currency"],
        "monthly_quota": int(row["monthly_quota"]),
        "current_period_start": row["current_period_start"],
        "current_period_end": row["current_period_end"],
        "cancel_at_period_end": bool(row["cancel_at_period_end"]),
        "canceled_at": row["canceled_at"],
        "stripe_event_created_at": int(row["stripe_event_created_at"] or 0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _usage_period_payload(row) -> dict[str, Any]:
    quota_limit = int(row["quota_limit"])
    units_used = int(row["units_used"])
    return {
        "id": int(row["id"]),
        "company_id": int(row["company_id"]),
        "feature": row["feature_key"],
        "feature_subscription_id": int(row["feature_subscription_id"]),
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "quota_limit": quota_limit,
        "units_used": units_used,
        "remaining": max(0, quota_limit - units_used),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _non_negative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be a whole number.")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise TypeError(f"{field_name} must be a whole number.") from error
    if normalized < 0 or str(value).strip() != str(normalized):
        raise ValueError(f"{field_name} must be a non-negative whole number.")
    return normalized


def _optional_text(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _validate_period(
    period_start: str | None,
    period_end: str | None,
    *,
    required: bool = False,
) -> None:
    if required and (not period_start or not period_end):
        raise ValueError("Billing period start and end are required.")
    if not period_start and not period_end:
        return
    if not period_start or not period_end:
        raise ValueError("Billing period start and end must be provided together.")
    try:
        start = datetime.fromisoformat(str(period_start).replace("Z", "+00:00"))
        end = datetime.fromisoformat(str(period_end).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Billing period values must be ISO-8601 timestamps.") from error
    if end <= start:
        raise ValueError("Billing period end must be after its start.")


def _iso_datetime(value: str, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp.") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
