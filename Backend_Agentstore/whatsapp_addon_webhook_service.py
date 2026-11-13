from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from auth_sqlite import fetch_one, utcnow_iso
from company_feature_service import (
    WHATSAPP_FEATURE,
    set_company_feature_entitlement,
)
from whatsapp_addon_service import (
    get_company_whatsapp_subscription,
    get_whatsapp_addon_config,
    save_company_whatsapp_subscription,
)


WHATSAPP_STRIPE_EVENT_TYPES = frozenset(
    {
        "checkout.session.completed",
        "checkout.session.expired",
        "customer.subscription.created",
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "invoice.paid",
        "invoice.payment_failed",
    }
)


def process_whatsapp_stripe_event(conn, stripe_client, event: Any) -> bool:
    event_type = str(_value(event, "type") or "")
    if event_type not in WHATSAPP_STRIPE_EVENT_TYPES:
        return False

    event_created = int(_value(event, "created") or 0)
    event_object = _value(_value(event, "data") or {}, "object") or {}
    if event_type == "checkout.session.completed":
        return _process_checkout_completed(
            conn,
            stripe_client,
            event_object,
            event_created,
        )
    if event_type == "checkout.session.expired":
        return _process_checkout_expired(conn, event_object, event_created)
    if event_type.startswith("customer.subscription."):
        subscription = event_object
        if event_type != "customer.subscription.deleted":
            subscription_id = _stripe_id(event_object)
            if not subscription_id:
                raise ValueError("Stripe subscription event is missing its subscription id.")
            subscription = stripe_client.Subscription.retrieve(subscription_id)
        return _sync_subscription(
            conn,
            subscription,
            event_created=event_created,
        ) is not None
    if event_type == "invoice.paid":
        return _process_invoice(
            conn,
            stripe_client,
            event_object,
            event_created=event_created,
            paid=True,
        )
    if event_type == "invoice.payment_failed":
        return _process_invoice(
            conn,
            stripe_client,
            event_object,
            event_created=event_created,
            paid=False,
        )
    return False


def stripe_event_payload(event: Any) -> dict[str, Any]:
    if isinstance(event, dict):
        return event
    converter = getattr(event, "to_dict_recursive", None)
    if callable(converter):
        converted = converter()
        if isinstance(converted, dict):
            return converted
    try:
        return dict(event)
    except (TypeError, ValueError) as exc:
        raise TypeError("Stripe webhook event could not be serialized.") from exc


def _process_checkout_completed(
    conn,
    stripe_client,
    session: Any,
    event_created: int,
) -> bool:
    metadata = _value(session, "metadata") or {}
    if _value(metadata, "feature_key") != WHATSAPP_FEATURE:
        return False
    company_id = _positive_int(_value(metadata, "company_id"))
    subscription_id = _stripe_id(_value(session, "subscription"))
    if not company_id or not subscription_id:
        raise ValueError("WhatsApp checkout is missing company or subscription metadata.")
    subscription = stripe_client.Subscription.retrieve(subscription_id)
    synced = _sync_subscription(
        conn,
        subscription,
        event_created=event_created,
        company_id=company_id,
        checkout_session_id=_stripe_id(session),
    )
    return synced is not None


def _process_checkout_expired(conn, session: Any, event_created: int) -> bool:
    session_id = _stripe_id(session)
    metadata = _value(session, "metadata") or {}
    company_id = _positive_int(_value(metadata, "company_id"))
    feature_key = _value(metadata, "feature_key")
    row = None
    if session_id:
        row = fetch_one(
            conn,
            """
            SELECT company_id
            FROM company_feature_subscriptions
            WHERE feature_key = ? AND stripe_checkout_session_id = ?
            LIMIT 1
            """,
            (WHATSAPP_FEATURE, session_id),
        )
    if row:
        company_id = int(row["company_id"])
    elif feature_key != WHATSAPP_FEATURE:
        return False
    if not company_id:
        return False

    existing = get_company_whatsapp_subscription(conn, company_id)
    if existing["status"] != "checkout_pending":
        return True
    if event_created and event_created < existing["stripe_event_created_at"]:
        return True
    save_company_whatsapp_subscription(
        conn,
        company_id,
        status="expired",
        monthly_price_cents=existing["monthly_price_cents"] or 0,
        monthly_quota=existing["monthly_quota"] or 0,
        stripe_customer_id=existing["stripe_customer_id"],
        stripe_subscription_id=existing["stripe_subscription_id"],
        stripe_subscription_item_id=existing["stripe_subscription_item_id"],
        stripe_checkout_session_id=existing["stripe_checkout_session_id"],
        stripe_price_id=existing["stripe_price_id"],
        stripe_event_created_at=event_created,
    )
    set_company_feature_entitlement(
        conn,
        company_id,
        WHATSAPP_FEATURE,
        enabled=False,
    )
    conn.execute(
        """
        UPDATE payments
        SET payment_status = 'cancelled'
        WHERE stripe_session_id = ? AND payment_status = 'pending'
        """,
        (session_id,),
    )
    return True


def _process_invoice(
    conn,
    stripe_client,
    invoice: Any,
    *,
    event_created: int,
    paid: bool,
) -> bool:
    subscription_id = _invoice_subscription_id(invoice)
    if not subscription_id:
        return False
    row = fetch_one(
        conn,
        """
        SELECT company_id
        FROM company_feature_subscriptions
        WHERE feature_key = ? AND stripe_subscription_id = ?
        LIMIT 1
        """,
        (WHATSAPP_FEATURE, subscription_id),
    )
    if not row:
        return False
    company_id = int(row["company_id"])
    subscription = stripe_client.Subscription.retrieve(subscription_id)
    synced = _sync_subscription(
        conn,
        subscription,
        event_created=event_created,
        company_id=company_id,
        forced_status=None if paid else "past_due",
    )
    if not synced:
        return False
    _record_invoice_payment(
        conn,
        invoice,
        synced,
        paid=paid,
        event_created=event_created,
    )
    if not paid:
        set_company_feature_entitlement(
            conn,
            company_id,
            WHATSAPP_FEATURE,
            enabled=False,
        )
    return True


def _sync_subscription(
    conn,
    subscription: Any,
    *,
    event_created: int,
    company_id: int | None = None,
    checkout_session_id: str | None = None,
    forced_status: str | None = None,
) -> dict[str, Any] | None:
    subscription_id = _stripe_id(subscription)
    metadata = _value(subscription, "metadata") or {}
    if not company_id:
        company_id = _positive_int(_value(metadata, "company_id"))
    if not company_id and subscription_id:
        row = fetch_one(
            conn,
            """
            SELECT company_id
            FROM company_feature_subscriptions
            WHERE feature_key = ? AND stripe_subscription_id = ?
            LIMIT 1
            """,
            (WHATSAPP_FEATURE, subscription_id),
        )
        company_id = int(row["company_id"]) if row else None
    if not company_id:
        return None
    if _value(metadata, "feature_key") not in (None, "", WHATSAPP_FEATURE):
        return None

    existing = get_company_whatsapp_subscription(conn, company_id)
    if event_created and event_created < existing["stripe_event_created_at"]:
        return existing

    config = get_whatsapp_addon_config(conn)
    cancel_at_period_end = bool(_value(subscription, "cancel_at_period_end"))
    local_status = forced_status or _local_subscription_status(
        _value(subscription, "status"),
        cancel_at_period_end=cancel_at_period_end,
    )
    item = _first_subscription_item(subscription)
    period_start, period_end = _subscription_period(subscription, item)
    if local_status in {"active", "cancel_at_period_end", "past_due"} and (
        not period_start or not period_end
    ):
        period_start = existing["current_period_start"]
        period_end = existing["current_period_end"]
    if local_status in {"active", "cancel_at_period_end", "past_due"} and (
        not period_start or not period_end
    ):
        raise ValueError("Stripe subscription is missing its current billing period.")

    is_new_period = bool(
        period_start and period_start != existing["current_period_start"]
    )
    if existing["status"] == "checkout_pending" and not existing["current_period_start"]:
        quota = existing["monthly_quota"] or config["monthly_quota"]
    elif is_new_period:
        quota = config["monthly_quota"]
    else:
        quota = existing["monthly_quota"] or config["monthly_quota"]

    price = _value(item, "price") or {}
    price_cents = int(
        _value(price, "unit_amount")
        or existing["monthly_price_cents"]
        or config["monthly_price_cents"]
    )
    canceled_at = _timestamp_iso(_value(subscription, "canceled_at"))
    synced = save_company_whatsapp_subscription(
        conn,
        company_id,
        status=local_status,
        monthly_price_cents=price_cents,
        monthly_quota=quota,
        stripe_customer_id=(
            _stripe_id(_value(subscription, "customer"))
            or existing["stripe_customer_id"]
        ),
        stripe_subscription_id=subscription_id or existing["stripe_subscription_id"],
        stripe_subscription_item_id=_stripe_id(item)
        or existing["stripe_subscription_item_id"],
        stripe_checkout_session_id=(
            checkout_session_id or existing["stripe_checkout_session_id"]
        ),
        stripe_price_id=_stripe_id(price) or existing["stripe_price_id"],
        current_period_start=period_start,
        current_period_end=period_end,
        cancel_at_period_end=cancel_at_period_end,
        canceled_at=canceled_at or existing["canceled_at"],
        stripe_event_created_at=event_created,
    )

    entitlement_enabled = local_status in {"active", "cancel_at_period_end"}
    set_company_feature_entitlement(
        conn,
        company_id,
        WHATSAPP_FEATURE,
        enabled=entitlement_enabled,
    )
    return synced


def _record_invoice_payment(
    conn,
    invoice: Any,
    subscription: dict[str, Any],
    *,
    paid: bool,
    event_created: int,
) -> None:
    invoice_id = _stripe_id(invoice)
    if not invoice_id:
        raise ValueError("Stripe invoice is missing its id.")
    amount_cents = int(
        _value(invoice, "amount_paid" if paid else "amount_due") or 0
    )
    currency = str(_value(invoice, "currency") or "usd").lower()
    payment_status = "completed" if paid else "failed"
    paid_at = _value(_value(invoice, "status_transitions") or {}, "paid_at")
    payment_date = _timestamp_iso(paid_at or event_created) or utcnow_iso()
    existing_invoice = fetch_one(
        conn,
        "SELECT id FROM payments WHERE stripe_invoice_id = ? LIMIT 1",
        (invoice_id,),
    )
    if existing_invoice:
        conn.execute(
            """
            UPDATE payments
            SET amount = ?,
                currency = ?,
                payment_status = ?,
                payment_date = ?,
                expires_at = ?,
                stripe_subscription_id = ?
            WHERE id = ?
            """,
            (
                amount_cents / 100,
                currency,
                payment_status,
                payment_date,
                subscription["current_period_end"],
                subscription["stripe_subscription_id"],
                int(existing_invoice["id"]),
            ),
        )
        return

    pending = fetch_one(
        conn,
        """
        SELECT id
        FROM payments
        WHERE workspace_id = ?
          AND purchase_type = 'feature_addon'
          AND feature_key = ?
          AND payment_status = 'pending'
        ORDER BY id DESC
        LIMIT 1
        """,
        (subscription["company_id"], WHATSAPP_FEATURE),
    )
    if pending:
        conn.execute(
            """
            UPDATE payments
            SET amount = ?,
                currency = ?,
                payment_status = ?,
                payment_date = ?,
                expires_at = ?,
                stripe_subscription_id = ?,
                stripe_invoice_id = ?
            WHERE id = ?
            """,
            (
                amount_cents / 100,
                currency,
                payment_status,
                payment_date,
                subscription["current_period_end"],
                subscription["stripe_subscription_id"],
                invoice_id,
                int(pending["id"]),
            ),
        )
        return

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
            expires_at,
            plan_name,
            purchase_type,
            feature_key,
            stripe_subscription_id,
            stripe_invoice_id
        )
        VALUES (?, ?, 'stripe', ?, ?, ?, ?, ?, 'WhatsApp Add-on',
                'feature_addon', ?, ?, ?)
        """,
        (
            subscription["company_id"],
            f"WA_INV_{invoice_id}",
            amount_cents / 100,
            currency,
            payment_status,
            payment_date,
            subscription["current_period_end"],
            WHATSAPP_FEATURE,
            subscription["stripe_subscription_id"],
            invoice_id,
        ),
    )


def _local_subscription_status(status: Any, *, cancel_at_period_end: bool) -> str:
    normalized = str(status or "").lower()
    if normalized in {"active", "trialing"}:
        return "cancel_at_period_end" if cancel_at_period_end else "active"
    if normalized in {"past_due", "unpaid", "paused"}:
        return "past_due"
    if normalized == "canceled":
        return "canceled"
    if normalized == "incomplete_expired":
        return "expired"
    return "checkout_pending"


def _invoice_subscription_id(invoice: Any) -> str | None:
    direct = _stripe_id(_value(invoice, "subscription"))
    if direct:
        return direct
    parent = _value(invoice, "parent") or {}
    details = _value(parent, "subscription_details") or {}
    return _stripe_id(_value(details, "subscription"))


def _first_subscription_item(subscription: Any) -> Any:
    items = _value(_value(subscription, "items") or {}, "data") or []
    return items[0] if items else {}


def _subscription_period(subscription: Any, item: Any) -> tuple[str | None, str | None]:
    start = _value(subscription, "current_period_start") or _value(
        item, "current_period_start"
    )
    end = _value(subscription, "current_period_end") or _value(
        item, "current_period_end"
    )
    return _timestamp_iso(start), _timestamp_iso(end)


def _timestamp_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str) and "T" in value:
        return value
    return datetime.fromtimestamp(int(value), tz=timezone.utc).isoformat()


def _positive_int(value: Any) -> int | None:
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized > 0 else None


def _stripe_id(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return value.strip() or None
    normalized = str(_value(value, "id") or "").strip()
    return normalized or None


def _value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)
