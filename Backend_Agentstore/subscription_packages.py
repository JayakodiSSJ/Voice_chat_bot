from __future__ import annotations

import copy
import json
from typing import Any

from auth_sqlite import fetch_one, utcnow_iso

PACKAGE_IDS = ("free", "starter", "business", "enterprise")
EDITABLE_PACKAGE_IDS = PACKAGE_IDS
LEGACY_MESSAGE_TOKEN_MULTIPLIER = 1000

DEFAULT_PACKAGES: dict[str, dict[str, Any]] = {
    "free": {
        "id": "free",
        "name": "Free Trial",
        "description": "7-day free trial to get started with custom AI agents.",
        "price_monthly_usd": 0.0,
        "popular": False,
        "display_order": 0,
        "agent_limit": 1,
        "monthly_tokens": 100_000,
        "knowledge_bases_limit": 1,
        "websites_limit": 1,
        "allow_website_kb": True,
        "allow_documents_kb": False,
        "allow_onedrive_kb": False,
        "allow_sharepoint_kb": False,
        "allow_form_builder": False,
        "allow_endpoint_tools": False,
        "allow_mcp": False,
        "agent_expiry_days": 7,
    },
    "starter": {
        "id": "starter",
        "name": "Starter",
        "description": "For individuals or teams getting started with custom AI agents.",
        "price_monthly_usd": 19.0,
        "popular": False,
        "display_order": 1,
        "agent_limit": 1,
        "monthly_tokens": 1_000_000,
        "knowledge_bases_limit": 1,
        "websites_limit": 1,
        "allow_website_kb": True,
        "allow_documents_kb": False,
        "allow_onedrive_kb": True,
        "allow_sharepoint_kb": True,
        "allow_form_builder": False,
        "allow_endpoint_tools": False,
        "allow_mcp": False,
        "agent_expiry_days": 30,
    },
    "business": {
        "id": "business",
        "name": "Business",
        "description": "For growing organizations connecting knowledge bases and forms.",
        "price_monthly_usd": 49.0,
        "popular": True,
        "display_order": 2,
        "agent_limit": 5,
        "monthly_tokens": 5_000_000,
        "knowledge_bases_limit": 5,
        "websites_limit": 10,
        "allow_website_kb": True,
        "allow_documents_kb": True,
        "allow_onedrive_kb": True,
        "allow_sharepoint_kb": True,
        "allow_form_builder": True,
        "allow_endpoint_tools": False,
        "allow_mcp": False,
        "agent_expiry_days": 30,
    },
    "enterprise": {
        "id": "enterprise",
        "name": "Enterprise",
        "description": "For large enterprises demanding unlimited scale and API integrations.",
        "price_monthly_usd": 149.0,
        "popular": False,
        "display_order": 3,
        "agent_limit": None,
        "monthly_tokens": 50_000_000,
        "knowledge_bases_limit": None,
        "websites_limit": None,
        "allow_website_kb": True,
        "allow_documents_kb": True,
        "allow_onedrive_kb": True,
        "allow_sharepoint_kb": True,
        "allow_form_builder": True,
        "allow_endpoint_tools": True,
        "allow_mcp": True,
        "agent_expiry_days": 30,
    },
}

PACKAGE_NUMERIC_FIELDS = (
    "price_monthly_usd",
    "agent_limit",
    "monthly_tokens",
    "knowledge_bases_limit",
    "websites_limit",
    "display_order",
    "agent_expiry_days",
)

PACKAGE_BOOL_FIELDS = (
    "popular",
    "allow_website_kb",
    "allow_documents_kb",
    "allow_onedrive_kb",
    "allow_sharepoint_kb",
    "allow_form_builder",
    "allow_endpoint_tools",
    "allow_mcp",
)

PACKAGE_TEXT_FIELDS = ("name", "description")


def get_default_packages() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(DEFAULT_PACKAGES)


def _is_unlimited(value: Any) -> bool:
    return value is None or value == "" or value == -1 or str(value).lower() == "unlimited"


def format_limit(value: Any, *, unit: str = "", unlimited_label: str = "Unlimited") -> str:
    if _is_unlimited(value):
        return unlimited_label
    if unit:
        return f"{value:,} {unit}".replace(", ", " ")
    return f"{value:,}"


def package_allowed_tools(package: dict[str, Any]) -> list[str]:
    tools = ["kb"]
    if package.get("allow_form_builder"):
        tools.append("form_handler")
    if package.get("allow_endpoint_tools"):
        tools.append("api")
        tools.append("database")
    if package.get("allow_mcp"):
        tools.append("mcp")
    return tools


def package_allowed_kb(package: dict[str, Any]) -> list[str]:
    allowed: list[str] = []
    if package.get("allow_website_kb"):
        allowed.append("website")
    if package.get("allow_documents_kb"):
        allowed.append("documents")
    if package.get("allow_onedrive_kb"):
        allowed.append("onedrive")
    if package.get("allow_sharepoint_kb"):
        allowed.append("sharepoint")
    return allowed


def package_to_legacy_tier_info(package: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": package.get("name") or package.get("id", "").title(),
        "price": float(package.get("price_monthly_usd") or 0.0),
        "agent_limit": package.get("agent_limit"),
        "monthly_tokens": package.get("monthly_tokens"),
        "knowledge_bases_limit": package.get("knowledge_bases_limit"),
        "websites_limit": package.get("websites_limit"),
        "allowed_tools": package_allowed_tools(package),
        "allowed_kb": package_allowed_kb(package),
        "allow_form_builder": bool(package.get("allow_form_builder")),
        "allow_endpoint_tools": bool(package.get("allow_endpoint_tools")),
        "allow_sharepoint_kb": bool(package.get("allow_sharepoint_kb")),
        "allow_mcp": bool(package.get("allow_mcp")),
    }


def package_feature_lines(package: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    agent_limit = package.get("agent_limit")
    if _is_unlimited(agent_limit):
        lines.append("Unlimited Agents")
    else:
        lines.append(f"{agent_limit} Agent{'s' if int(agent_limit or 0) != 1 else ''}")

    if package.get("allow_website_kb"):
        lines.append("Web URL Knowledge Base")
    if package.get("allow_onedrive_kb"):
        lines.append("OneDrive Knowledge Base")
    if package.get("allow_sharepoint_kb"):
        lines.append("SharePoint Knowledge Base")

    if package.get("allow_form_builder"):
        lines.append("Form Builder")
    if package.get("allow_endpoint_tools"):
        lines.append("APIs")
    if package.get("allow_mcp"):
        lines.append("MCP")

    lines.append(f"{format_limit(package.get('monthly_tokens'))} monthly tokens")

    kb_limit = package.get("knowledge_bases_limit")
    if _is_unlimited(kb_limit):
        lines.append("Unlimited Knowledge Bases")
    elif kb_limit not in (None, ""):
        lines.append(f"Up to {kb_limit} Knowledge Base{'s' if int(kb_limit or 0) != 1 else ''}")

    website_limit = package.get("websites_limit")
    if _is_unlimited(website_limit):
        lines.append("Unlimited Websites")

    return lines


def package_limitation_lines(package: dict[str, Any]) -> list[str]:
    limitations: list[str] = []
    if not package.get("allow_form_builder"):
        limitations.append("No Form Builder")
    if not package.get("allow_endpoint_tools"):
        limitations.append("No APIs")
    if not package.get("allow_mcp"):
        limitations.append("No MCP")
    if not package.get("allow_onedrive_kb"):
        limitations.append("No OneDrive Knowledge Base")
    if not package.get("allow_sharepoint_kb"):
        limitations.append("No SharePoint Knowledge Base")
    return limitations


def normalize_package(package_id: str, raw: dict[str, Any] | None) -> dict[str, Any]:
    defaults = DEFAULT_PACKAGES.get(package_id, {})
    source = raw or {}
    merged = {**defaults, **source}
    if "monthly_tokens" not in source and "monthly_messages" in source:
        legacy_limit = source.get("monthly_messages")
        merged["monthly_tokens"] = (
            None
            if _is_unlimited(legacy_limit)
            else int(legacy_limit) * LEGACY_MESSAGE_TOKEN_MULTIPLIER
        )
    merged["id"] = package_id
    merged.pop("storage_gb", None)
    merged.pop("monthly_messages", None)

    for field in PACKAGE_NUMERIC_FIELDS:
        if field not in merged:
            continue
        value = merged[field]
        if value in ("", "null", "None"):
            merged[field] = None
            continue
        if value is None:
            continue
        if field == "price_monthly_usd":
            merged[field] = float(value)
        else:
            merged[field] = int(value)

    for field in PACKAGE_BOOL_FIELDS:
        merged[field] = bool(merged.get(field))

    for field in PACKAGE_TEXT_FIELDS:
        merged[field] = str(merged.get(field) or defaults.get(field) or package_id.title())

    return merged


def normalize_packages_config(raw: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    source = raw or {}
    normalized: dict[str, dict[str, Any]] = {}
    for package_id in DEFAULT_PACKAGES:
        normalized[package_id] = normalize_package(package_id, source.get(package_id, {}))
    return normalized


def packages_for_public(config: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    normalized = normalize_packages_config(config)
    public_packages: list[dict[str, Any]] = []
    for package_id in PACKAGE_IDS:
        package = normalized[package_id]
        public_packages.append(
            {
                **package,
                "features": package_feature_lines(package),
                "limitations": package_limitation_lines(package),
            }
        )
    public_packages.sort(key=lambda item: item.get("display_order", 0))
    return public_packages


def get_tier_info(config: dict[str, dict[str, Any]], tier: str) -> dict[str, Any]:
    normalized = normalize_packages_config(config)
    tier_key = (tier or "free").lower()
    package = normalized.get(tier_key) or normalized["free"]
    return package_to_legacy_tier_info(package)


def load_packages_config(conn) -> dict[str, dict[str, Any]]:
    row = fetch_one(conn, "SELECT config_json FROM subscription_packages_config WHERE id = 1")
    if not row or not row["config_json"]:
        return get_default_packages()
    try:
        stored = json.loads(row["config_json"])
    except (TypeError, json.JSONDecodeError):
        return get_default_packages()
    if not isinstance(stored, dict):
        return get_default_packages()
    return normalize_packages_config(stored)


def save_packages_config(conn, updates: dict[str, Any], *, updated_by_user_id: int | None = None) -> dict[str, dict[str, Any]]:
    current = load_packages_config(conn)
    merged = copy.deepcopy(current)

    for package_id in EDITABLE_PACKAGE_IDS:
        if package_id not in updates:
            continue
        patch = updates.get(package_id)
        if not isinstance(patch, dict):
            continue
        merged[package_id] = normalize_package(package_id, {**merged.get(package_id, {}), **patch})

    config_json = json.dumps(merged, separators=(",", ":"), sort_keys=True)
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO subscription_packages_config (id, config_json, updated_at, updated_by_user_id)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            config_json = excluded.config_json,
            updated_at = excluded.updated_at,
            updated_by_user_id = excluded.updated_by_user_id
        """,
        (config_json, now, updated_by_user_id),
    )
    conn.commit()
    return merged


def seed_packages_config(conn) -> None:
    existing = fetch_one(conn, "SELECT id FROM subscription_packages_config WHERE id = 1")
    if existing:
        return
    config_json = json.dumps(get_default_packages(), separators=(",", ":"), sort_keys=True)
    conn.execute(
        """
        INSERT INTO subscription_packages_config (id, config_json, updated_at, updated_by_user_id)
        VALUES (1, ?, ?, NULL)
        """,
        (config_json, utcnow_iso()),
    )
