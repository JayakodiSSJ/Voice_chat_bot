from __future__ import annotations

import copy
import re
from typing import Any


REDACTED_VALUE = "[REDACTED]"
_SENSITIVE_KEY_TOKENS = {
    "api_key",
    "apikey",
    "authorization",
    "auth",
    "authentication",
    "token",
    "secret",
    "password",
    "passwd",
    "credential",
    "cookie",
    "private_key",
    "privatekey",
}
_BEARER_TOKEN_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+\-/=]+", flags=re.I)
_JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_QUERY_SECRET_RE = re.compile(
    r"([?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|authorization)=)[^&#\s]+",
    flags=re.I,
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|authorization)\s*([=:])\s*([^\s,;]+)",
    flags=re.I,
)


class ToolDataRedactor:
    """Makes tool telemetry safe to send to models, clients, and audit views."""

    def redact(self, value: Any, *, max_depth: int = 12, max_items: int = 100) -> Any:
        return self._redact(value, depth=0, max_depth=max_depth, max_items=max_items)

    def redact_text(self, value: Any, *, limit: int = 4000) -> str:
        text = str(value or "")
        text = _BEARER_TOKEN_RE.sub(f"Bearer {REDACTED_VALUE}", text)
        text = _JWT_RE.sub(REDACTED_VALUE, text)
        text = _QUERY_SECRET_RE.sub(rf"\1{REDACTED_VALUE}", text)
        text = _SECRET_ASSIGNMENT_RE.sub(rf"\1\2{REDACTED_VALUE}", text)
        return text[:limit]

    def summarize_result(self, result: Any) -> dict[str, Any]:
        safe = self.redact(result, max_depth=5, max_items=20)
        if not isinstance(safe, dict):
            return {"result_type": type(safe).__name__}
        summary = {
            key: safe[key]
            for key in ("type", "ok", "status", "status_code", "operation", "endpoint_index", "tool_name", "error")
            if key in safe
        }
        response = safe.get("response")
        if isinstance(response, dict):
            summary["response_fields"] = list(response.keys())[:20]
        elif response is not None:
            summary["response_type"] = type(response).__name__
        return summary

    def _redact(self, value: Any, *, depth: int, max_depth: int, max_items: int) -> Any:
        if depth >= max_depth:
            return "[TRUNCATED]"
        if isinstance(value, dict):
            safe: dict[str, Any] = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= max_items:
                    safe["_truncated_items"] = True
                    break
                key_text = str(key)
                safe[key_text] = REDACTED_VALUE if self.is_sensitive_key(key_text) else self._redact(
                    item,
                    depth=depth + 1,
                    max_depth=max_depth,
                    max_items=max_items,
                )
            return safe
        if isinstance(value, (list, tuple, set)):
            items = list(value)
            safe_items = [
                self._redact(item, depth=depth + 1, max_depth=max_depth, max_items=max_items)
                for item in items[:max_items]
            ]
            if len(items) > max_items:
                safe_items.append("[TRUNCATED]")
            return safe_items
        if isinstance(value, str):
            return self.redact_text(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return self.redact_text(value)

    @staticmethod
    def is_sensitive_key(key: str) -> bool:
        camel_normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(key or "").strip())
        normalized = re.sub(r"[^a-z0-9]+", "_", camel_normalized.lower()).strip("_")
        return (
            normalized in _SENSITIVE_KEY_TOKENS
            or normalized.endswith(("_api_key", "_private_key"))
            or bool(set(normalized.split("_")) & (_SENSITIVE_KEY_TOKENS - {"api_key", "private_key"}))
        )


class ToolTrace:
    """A response-local, redacted trace of the orchestration decisions."""

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []

    def add(self, event: str, **details: Any) -> None:
        self._events.append(
            {
                "sequence": len(self._events) + 1,
                "event": str(event),
                **tool_data_redactor.redact(details),
            }
        )

    def as_list(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._events)


tool_data_redactor = ToolDataRedactor()
