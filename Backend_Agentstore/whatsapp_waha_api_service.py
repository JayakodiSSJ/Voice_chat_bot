from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import requests


class WhatsAppWahaAPIError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class WhatsAppWahaAPIService:
    def send_text(
        self,
        *,
        base_url: str,
        session: str,
        api_key: str,
        recipient_phone: str,
        message_text: str,
        timeout_seconds: float = 20,
    ) -> str:
        endpoint = _normalized_base_url(base_url)
        session_name = str(session or "").strip()
        token = str(api_key or "").strip()
        chat_id = _recipient_chat_id(recipient_phone)
        body = str(message_text or "").strip()
        if not session_name or not token or not chat_id or not body:
            raise ValueError("WAHA URL, session, API key, recipient, and message text are required.")

        try:
            response = requests.post(
                f"{endpoint}/api/sendText",
                headers={
                    "X-Api-Key": token,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json={
                    "session": session_name,
                    "chatId": chat_id,
                    "text": body,
                },
                timeout=timeout_seconds,
            )
        except requests.RequestException as error:
            raise WhatsAppWahaAPIError(
                f"Could not reach WAHA: {error}",
                retryable=True,
            ) from error

        payload = _response_payload(response)
        if not 200 <= response.status_code < 300:
            detail = _error_message(payload) or f"HTTP {response.status_code}"
            raise WhatsAppWahaAPIError(
                f"WAHA rejected the message: {detail}",
                retryable=response.status_code == 429 or response.status_code >= 500,
            )

        message_id = _message_id(payload)
        if not message_id:
            raise WhatsAppWahaAPIError(
                "WAHA accepted the request without returning a message id.",
                retryable=True,
            )
        return message_id


def _normalized_base_url(value: str) -> str:
    base_url = str(value or "").strip().rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("WAHA_BASE_URL must be an absolute HTTP or HTTPS URL.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("WAHA_BASE_URL cannot contain credentials, a query, or a fragment.")
    return base_url


def _recipient_chat_id(value: str) -> str:
    raw = str(value or "").strip()
    if raw.endswith("@s.whatsapp.net"):
        raw = f"{raw.removesuffix('@s.whatsapp.net')}@c.us"
    if raw.endswith(("@c.us", "@lid")):
        local_id = raw.split("@", 1)[0]
        return raw if local_id.isdigit() else ""
    digits = "".join(character for character in raw if character.isdigit())
    return f"{digits}@c.us" if digits else ""


def _response_payload(response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _message_id(payload: dict[str, Any]) -> str:
    value = payload.get("id")
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("_serialized") or value.get("id") or "").strip()
    key = payload.get("key")
    if isinstance(key, dict):
        return str(key.get("id") or "").strip()
    return ""


def _error_message(payload: dict[str, Any]) -> str:
    exception = payload.get("exception")
    if isinstance(exception, dict):
        message = str(exception.get("message") or "").strip()
        if message:
            return message
    return str(payload.get("message") or payload.get("error") or "").strip()


whatsapp_waha_api_service = WhatsAppWahaAPIService()
