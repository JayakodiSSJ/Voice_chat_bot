from __future__ import annotations

import re
from typing import Any

import requests


GRAPH_API_VERSION = re.compile(r"^v\d+\.\d+$")
MAX_TEXT_LENGTH = 4096


class WhatsAppCloudAPIError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


class WhatsAppCloudAPIService:
    def send_text(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        recipient_phone: str,
        message_text: str,
        graph_api_version: str = "v26.0",
        timeout_seconds: float = 20,
    ) -> str:
        version = str(graph_api_version or "").strip()
        if not GRAPH_API_VERSION.fullmatch(version):
            raise ValueError("WhatsApp Graph API version must look like v26.0.")
        phone_id = str(phone_number_id or "").strip()
        token = str(access_token or "").strip()
        recipient = re.sub(r"\D", "", str(recipient_phone or ""))
        body = str(message_text or "").strip()
        if not phone_id or not token or not recipient or not body:
            raise ValueError("Phone Number ID, access token, recipient, and message text are required.")
        if len(body) > MAX_TEXT_LENGTH:
            raise ValueError(f"WhatsApp text messages cannot exceed {MAX_TEXT_LENGTH} characters.")

        url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
        try:
            response = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={
                    "messaging_product": "whatsapp",
                    "recipient_type": "individual",
                    "to": recipient,
                    "type": "text",
                    "text": {"preview_url": False, "body": body},
                },
                timeout=timeout_seconds,
            )
        except requests.RequestException as error:
            raise WhatsAppCloudAPIError(
                f"Could not reach the WhatsApp Cloud API: {error}",
                retryable=True,
            ) from error

        payload = _response_payload(response)
        if not 200 <= response.status_code < 300:
            detail = _meta_error_message(payload) or f"HTTP {response.status_code}"
            raise WhatsAppCloudAPIError(
                f"WhatsApp Cloud API rejected the message: {detail}",
                retryable=response.status_code == 429 or response.status_code >= 500,
            )

        messages = payload.get("messages") if isinstance(payload, dict) else None
        meta_message_id = (
            str(messages[0].get("id") or "").strip()
            if isinstance(messages, list) and messages and isinstance(messages[0], dict)
            else ""
        )
        if not meta_message_id:
            raise WhatsAppCloudAPIError(
                "WhatsApp Cloud API accepted the request without returning a message id.",
                retryable=True,
            )
        return meta_message_id


def split_whatsapp_text(message_text: str, limit: int = MAX_TEXT_LENGTH) -> list[str]:
    text = str(message_text or "").strip()
    if not text:
        return []
    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit + 1)
        if split_at < limit // 2:
            split_at = remaining.rfind(" ", 0, limit + 1)
        if split_at < limit // 2:
            split_at = limit
        chunk = remaining[:split_at].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _response_payload(response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _meta_error_message(payload: dict[str, Any]) -> str:
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return ""
    message = str(error.get("message") or "").strip()
    code = error.get("code")
    return f"{message} (code {code})" if message and code is not None else message


whatsapp_cloud_api_service = WhatsAppCloudAPIService()
