from __future__ import annotations

from typing import Any


TEXT_FIELDS = ("purpose", "data_scope")
LIST_FIELDS = (
    "use_when",
    "do_not_use_when",
    "capabilities",
    "requirements",
    "example_requests",
    "counterexamples",
)
FIELD_ALIASES = {
    "purpose": ("purpose", "description"),
    "data_scope": ("data_scope", "dataScope"),
    "use_when": ("use_when", "useWhen"),
    "do_not_use_when": ("do_not_use_when", "doNotUseWhen", "avoid_when", "avoidWhen"),
    "capabilities": ("capabilities",),
    "requirements": ("requirements",),
    "example_requests": ("example_requests", "exampleRequests", "examples"),
    "counterexamples": ("counterexamples", "counter_examples", "counterExamples"),
}


def routing_profile_template() -> dict[str, Any]:
    """Schema-like metadata configuration clients can use to render routing guidance."""
    return {
        "title": "Tool routing guidance",
        "description": (
            "Organization-specific guidance used only to select between configured capabilities. "
            "It does not change permissions or approval requirements."
        ),
        "fields": [
            {"key": "purpose", "label": "Purpose", "type": "text", "max_length": 800},
            {"key": "use_when", "label": "Use when", "type": "list", "max_items": 12},
            {"key": "do_not_use_when", "label": "Do not use when", "type": "list", "max_items": 12},
            {"key": "data_scope", "label": "Data scope", "type": "text", "max_length": 800},
            {"key": "capabilities", "label": "Capabilities", "type": "list", "max_items": 12},
            {"key": "requirements", "label": "Requirements", "type": "list", "max_items": 12},
            {"key": "example_requests", "label": "Example user requests", "type": "list", "max_items": 12},
            {"key": "counterexamples", "label": "Requests that should go elsewhere", "type": "list", "max_items": 12},
        ],
    }


def normalize_routing_profile(value: Any) -> dict[str, Any]:
    """Return bounded, planner-safe routing guidance supplied by an administrator."""
    if not isinstance(value, dict):
        return {}

    normalized: dict[str, Any] = {}
    for field in TEXT_FIELDS:
        raw = _first_value(value, FIELD_ALIASES[field])
        text = _clean_text(raw, 800)
        if text:
            normalized[field] = text

    for field in LIST_FIELDS:
        raw = _first_value(value, FIELD_ALIASES[field])
        items = _clean_list(raw)
        if items:
            normalized[field] = items

    freshness = _clean_text(_first_value(value, ("freshness",)), 40).lower()
    if freshness in {"live", "periodically_synced", "static", "unspecified"}:
        normalized["freshness"] = freshness

    cost_latency = _clean_text(_first_value(value, ("cost_latency", "costLatency")), 40).lower()
    if cost_latency in {"low", "medium", "high", "unspecified"}:
        normalized["cost_latency"] = cost_latency

    return normalized


def merge_routing_profiles(*values: Any) -> dict[str, Any]:
    """Merge broad configuration guidance with more specific tool guidance."""
    result: dict[str, Any] = {}
    for value in values:
        profile = normalize_routing_profile(value)
        for field, item in profile.items():
            if field in LIST_FIELDS:
                existing = result.setdefault(field, [])
                for entry in item:
                    if entry.casefold() not in {current.casefold() for current in existing}:
                        existing.append(entry)
            else:
                result[field] = item
    return result


def routing_profile_from(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return normalize_routing_profile(
        value.get("routing_profile")
        or value.get("routingProfile")
        or value.get("routing")
    )


def _first_value(value: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in value and value[key] not in (None, ""):
            return value[key]
    return None


def _clean_text(value: Any, limit: int) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return " ".join(str(value).split())[:limit]


def _clean_list(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.splitlines() if "\n" in value else value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        text = _clean_text(item, 300)
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            result.append(text)
        if len(result) == 12:
            break
    return result
