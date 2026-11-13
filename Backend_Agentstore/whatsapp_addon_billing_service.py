from __future__ import annotations

from typing import Any

from auth_sqlite import utcnow_iso
from company_feature_service import WHATSAPP_FEATURE


def ensure_whatsapp_stripe_catalog(stripe_client, config: dict[str, Any]) -> tuple[str, str]:
    price_cents = int(config["monthly_price_cents"])
    currency = str(config.get("currency") or "usd").lower()
    if price_cents <= 0:
        raise ValueError("WhatsApp add-on price must be greater than zero.")

    configured_price_id = config.get("stripe_price_id")
    if configured_price_id:
        try:
            price = stripe_client.Price.retrieve(configured_price_id)
            if _price_matches(price, price_cents=price_cents, currency=currency):
                product_id = _stripe_id(_stripe_value(price, "product"))
                if product_id:
                    return product_id, _stripe_id(price)
        except Exception:
            pass

    product_id = _valid_product_id(stripe_client, config.get("stripe_product_id"))
    if not product_id:
        product = stripe_client.Product.create(
            name=config.get("display_name") or "WhatsApp Add-on",
            metadata={"feature_key": WHATSAPP_FEATURE},
            idempotency_key="agent-store-whatsapp-addon-product-v1",
        )
        product_id = _stripe_id(product)

    price = stripe_client.Price.create(
        product=product_id,
        currency=currency,
        unit_amount=price_cents,
        recurring={"interval": "month"},
        metadata={"feature_key": WHATSAPP_FEATURE},
        idempotency_key=(
            f"agent-store-whatsapp-addon-price-{currency}-{price_cents}-v1"
        ),
    )
    return product_id, _stripe_id(price)


def create_whatsapp_checkout_session(
    stripe_client,
    *,
    config: dict[str, Any],
    company_id: int,
    customer_email: str | None,
    customer_id: str | None,
    frontend_url: str,
):
    product_id, price_id = ensure_whatsapp_stripe_catalog(stripe_client, config)
    metadata = {
        "company_id": str(company_id),
        "feature_key": WHATSAPP_FEATURE,
    }
    customer_args = (
        {"customer": customer_id}
        if customer_id
        else {"customer_email": customer_email or None}
    )
    session = stripe_client.checkout.Session.create(
        mode="subscription",
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        client_reference_id=str(company_id),
        success_url=(
            f"{frontend_url.rstrip('/')}/dashboard"
            "?whatsapp_addon=success&session_id={CHECKOUT_SESSION_ID}"
        ),
        cancel_url=(
            f"{frontend_url.rstrip('/')}/dashboard?whatsapp_addon=cancelled"
        ),
        metadata=metadata,
        subscription_data={"metadata": metadata},
        **customer_args,
    )
    return session, product_id, price_id


def set_whatsapp_subscription_cancel_at_period_end(
    stripe_client,
    *,
    subscription_id: str,
    cancel_at_period_end: bool,
):
    normalized_id = str(subscription_id or "").strip()
    if not normalized_id:
        raise ValueError("Stripe subscription id is required.")
    if type(cancel_at_period_end) is not bool:
        raise TypeError("cancel_at_period_end must be a boolean.")
    subscription = stripe_client.Subscription.modify(
        normalized_id,
        cancel_at_period_end=cancel_at_period_end,
    )
    stripe_value = _stripe_value(subscription, "cancel_at_period_end")
    if stripe_value is not None and bool(stripe_value) != cancel_at_period_end:
        raise RuntimeError("Stripe did not apply the requested renewal change.")
    return subscription


def reconcile_whatsapp_subscription_prices(
    conn,
    stripe_client,
    config: dict[str, Any],
) -> dict[str, Any]:
    product_id, price_id = ensure_whatsapp_stripe_catalog(stripe_client, config)
    rows = conn.execute(
        """
        SELECT id, company_id, stripe_subscription_item_id, stripe_price_id
        FROM company_feature_subscriptions
        WHERE feature_key = ?
          AND billing_source = 'stripe'
          AND status IN ('active', 'cancel_at_period_end', 'past_due')
          AND (stripe_price_id IS NULL OR stripe_price_id <> ?)
        ORDER BY id
        """,
        (WHATSAPP_FEATURE, price_id),
    ).fetchall()
    updated_company_ids: list[int] = []
    failures: list[dict[str, Any]] = []
    for row in rows:
        company_id = int(row["company_id"])
        item_id = str(row["stripe_subscription_item_id"] or "").strip()
        if not item_id:
            failures.append(
                {"company_id": company_id, "error": "Stripe subscription item id is missing."}
            )
            continue
        try:
            stripe_client.SubscriptionItem.modify(
                item_id,
                price=price_id,
                proration_behavior="none",
            )
            conn.execute(
                """
                UPDATE company_feature_subscriptions
                SET stripe_price_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (price_id, utcnow_iso(), int(row["id"])),
            )
            updated_company_ids.append(company_id)
        except Exception as exc:
            failures.append({"company_id": company_id, "error": str(exc)[:500]})
    return {
        "product_id": product_id,
        "price_id": price_id,
        "updated_company_ids": updated_company_ids,
        "failures": failures,
    }


def _valid_product_id(stripe_client, product_id: Any) -> str | None:
    normalized = str(product_id or "").strip()
    if not normalized:
        return None
    try:
        product = stripe_client.Product.retrieve(normalized)
    except Exception:
        return None
    if _stripe_value(product, "active") is False:
        return None
    return _stripe_id(product)


def _price_matches(price: Any, *, price_cents: int, currency: str) -> bool:
    recurring = _stripe_value(price, "recurring") or {}
    return (
        _stripe_value(price, "active") is not False
        and int(_stripe_value(price, "unit_amount") or 0) == price_cents
        and str(_stripe_value(price, "currency") or "").lower() == currency
        and _stripe_value(recurring, "interval") == "month"
    )


def _stripe_id(value: Any) -> str:
    normalized = str(_stripe_value(value, "id") or value or "").strip()
    if not normalized:
        raise ValueError("Stripe returned an object without an id.")
    return normalized


def _stripe_value(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)
