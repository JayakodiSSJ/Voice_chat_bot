from __future__ import annotations

import json
from typing import Any

from company_feature_service import WHATSAPP_FEATURE, company_has_feature
from whatsapp_connection_service import get_whatsapp_connection


WHATSAPP_ACCESS_KEYS = ("whatsapp_enabled", "whatsappEnabled")


def resolve_agent_whatsapp_enabled(
    payload: dict[str, Any] | None,
    *,
    default: bool = False,
) -> bool:
    source = payload if isinstance(payload, dict) else {}
    candidates = [source]
    configuration = source.get("configuration")
    if isinstance(configuration, str):
        try:
            configuration = json.loads(configuration)
        except json.JSONDecodeError:
            configuration = None
    if isinstance(configuration, dict):
        candidates.append(configuration)

    for candidate in candidates:
        for key in WHATSAPP_ACCESS_KEYS:
            if key not in candidate:
                continue
            value = candidate[key]
            if type(value) is not bool:
                raise ValueError("whatsapp_enabled must be a boolean.")
            return value
    return bool(default)


def validate_agent_whatsapp_access(
    conn,
    company_id: int,
    enabled: bool,
) -> tuple[bool, str | None, str | None]:
    if not enabled:
        return True, None, None
    if not company_has_feature(conn, company_id, WHATSAPP_FEATURE):
        return (
            False,
            "whatsapp_not_enabled",
            "WhatsApp is not enabled for this workspace.",
        )
    if not get_whatsapp_connection(conn, company_id)["configured"]:
        return (
            False,
            "whatsapp_not_configured",
            "Configure the workspace WhatsApp number before enabling an agent.",
        )
    return True, None, None
