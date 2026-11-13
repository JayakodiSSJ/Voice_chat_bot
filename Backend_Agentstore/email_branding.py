from __future__ import annotations

from html import escape
from typing import Any
from datetime import datetime


# Keep palette close to the current web app brand.
PRIMARY_COLOR = "#9200dc"
SECONDARY_COLOR = "#7c3aed"
TEXT_COLOR = "#141821"
MUTED_TEXT_COLOR = "#5e6472"
PAGE_BG_COLOR = "#f3f4f8"
CARD_BG_COLOR = "#ffffff"
BORDER_COLOR = "#e5e7ee"
TABLE_HEADER_BG = "#f5f0ff"
TABLE_ROW_ALT_BG = "#fafbfe"


def _frontend_base_url(app) -> str:
    if app.debug:
        return "http://localhost:3000"

    try:
        from flask import has_request_context, request
        from urllib.parse import urlparse
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


def get_logo_url(app) -> str:
    configured = (app.config.get("MAIL_LOGO_URL") or "").strip()
    if configured:
        return configured
    return "cid:logo_image"


def get_sender_identity(app):
    sender = app.config.get("MAIL_DEFAULT_SENDER") or app.config.get("MAIL_USERNAME")
    if isinstance(sender, (tuple, list)) and len(sender) >= 2:
        return (str(sender[0]).strip(), str(sender[1]).strip())
    if isinstance(sender, str):
        text = sender.strip()
        if "<" in text and ">" in text:
            return text
        # Fallback to "Agent Store" display name if only email is specified
        return f"Agent Store <{text}>"
    return None


def _normalize_details(details: Any) -> list[tuple[str, str]]:
    if not details:
        return []

    if isinstance(details, dict):
        items = details.items()
    elif isinstance(details, (list, tuple)):
        items = details
    else:
        return []

    normalized: list[tuple[str, str]] = []
    for item in items:
        if isinstance(item, tuple) and len(item) == 2:
            key, value = item
        else:
            continue
        key_text = str(key or "").strip()
        value_text = str(value or "").strip()
        if key_text and value_text:
            normalized.append((key_text, value_text))
    return normalized


def _to_html_paragraphs(text: str) -> str:
    chunks = [chunk.strip() for chunk in (text or "").splitlines()]
    parts = [f"<p style='margin:0 0 10px 0; color:{TEXT_COLOR}; line-height:1.55;'>{escape(chunk)}</p>" for chunk in chunks if chunk]
    if not parts:
        return ""
    return "".join(parts)


def _to_text_lines(text: str) -> list[str]:
    chunks = [chunk.strip() for chunk in (text or "").splitlines()]
    return [chunk for chunk in chunks if chunk]


def build_email_html(
    app,
    *,
    subject: str,
    intro_text: str,
    details: Any | None = None,
    cta_text: str | None = None,
    cta_url: str | None = None,
) -> str:
    logo_url = get_logo_url(app)
    rows = _normalize_details(details)
    # Intro Section as plain paragraphs
    intro_html = _to_html_paragraphs(intro_text)

    # Details Section in a Table
    details_table = ""
    if rows:
        row_html_parts = []
        for idx, (k, v) in enumerate(rows):
            bg = TABLE_ROW_ALT_BG if idx % 2 else CARD_BG_COLOR
            row_html_parts.append(
                f"""
                <tr style="background:{bg};">
                  <td style="padding:12px 15px; border:1px solid {BORDER_COLOR}; width:30%; color:{MUTED_TEXT_COLOR}; font-weight:600; font-size:13px;">{escape(k)}</td>
                  <td style="padding:12px 15px; border:1px solid {BORDER_COLOR}; color:{TEXT_COLOR}; font-size:13px;">{escape(v).replace(chr(10), '<br/>')}</td>
                </tr>
                """
            )
        details_table = (
            f"""
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse; margin:0 0 25px 0; border-radius:10px; overflow:hidden;">
              <thead>
                <tr>
                  <th align="left" style="padding:12px 15px; border:1px solid {BORDER_COLOR}; background:{TABLE_HEADER_BG}; color:{PRIMARY_COLOR}; font-size:11px; text-transform:uppercase;">Property</th>
                  <th align="left" style="padding:12px 15px; border:1px solid {BORDER_COLOR}; background:{TABLE_HEADER_BG}; color:{PRIMARY_COLOR}; font-size:11px; text-transform:uppercase;">Value</th>
                </tr>
              </thead>
              <tbody>
                {''.join(row_html_parts)}
              </tbody>
            </table>
            """
        )

    cta_html = ""
    if cta_text and cta_url:
        cta_html = (
            f"""
            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="margin-top:10px;">
              <tr>
                <td align="center">
                  <a href="{escape(cta_url)}" style="display:inline-block; padding:14px 30px; border-radius:12px; background:linear-gradient(135deg, {PRIMARY_COLOR}, {SECONDARY_COLOR}); color:#ffffff; text-decoration:none; font-weight:700; font-size:15px; box-shadow:0 10px 20px -10px {PRIMARY_COLOR};">
                    {escape(cta_text)}
                  </a>
                </td>
              </tr>
            </table>
            """
        )

    return f"""\
<!doctype html>
<html>
  <head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
  </head>
  <body style="margin:0; padding:0; background:{PAGE_BG_COLOR}; font-family:'Segoe UI', Roboto, Helvetica, Arial, sans-serif; -webkit-font-smoothing:antialiased;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:{PAGE_BG_COLOR};">
      <tr>
        <td align="center" style="padding:40px 15px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px; background:{CARD_BG_COLOR}; border:1px solid {BORDER_COLOR}; border-radius:24px; overflow:hidden; box-shadow:0 20px 40px -15px rgba(0,0,0,0.05);">
            <tr>
              <td style="padding:35px 40px; background:linear-gradient(135deg, {PRIMARY_COLOR}, {SECONDARY_COLOR});">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                  <tr>
                    <td>
                      <img src="{escape(logo_url)}" alt="Agent Store" style="height:35px; width:auto; display:block; margin-bottom:15px;" />
                      <div style="color:#ffffff; font-size:26px; font-weight:800; line-height:1.2; letter-spacing:-0.5px;">{escape(subject)}</div>
                      <div style="color:rgba(255,255,255,0.8); font-size:13px; margin-top:8px; font-weight:500;">SLT Digital Lab • Multi-Tenant AI Platform</div>
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:40px;">
                {intro_html if not details_table else ""}
                {details_table}
                {cta_html}
              </td>
            </tr>
            <tr>
              <td style="padding:25px 40px; background:#fafbff; border-top:1px solid {BORDER_COLOR}; color:{MUTED_TEXT_COLOR}; font-size:12px; text-align:center;">
                <div style="margin-bottom:8px;">© {datetime.now().year} SLT Digital Lab. All rights reserved.</div>
                <div>This is an automated system notification. Please do not reply to this email.</div>
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""


def build_email_text(
    *,
    subject: str,
    intro_text: str,
    details: Any | None = None,
    cta_text: str | None = None,
    cta_url: str | None = None,
) -> str:
    lines: list[str] = [subject, ""]
    lines.extend(_to_text_lines(intro_text))

    rows = _normalize_details(details)
    if rows:
        lines.append("")
        lines.append("Details:")
        for k, v in rows:
            lines.append(f"- {k}: {v}")

    if cta_text and cta_url:
        lines.append("")
        lines.append(f"{cta_text}: {cta_url}")

    lines.append("")
    lines.append("Sent by Agent Store")
    return "\n".join(lines)


def attach_email_logo(app, msg) -> None:
    import os
    if msg.html and "cid:logo_image" in msg.html:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        logo_path = os.path.join(base_dir, "templates", "aisleaiagents.png")
        if os.path.exists(logo_path):
            try:
                with open(logo_path, "rb") as f:
                    img_data = f.read()
                msg.attach(
                    filename="aisleaiagents.png",
                    content_type="image/png",
                    data=img_data,
                    disposition="inline",
                    headers={"Content-ID": "<logo_image>"}
                )
            except Exception as e:
                print(f"ERROR: Failed to attach logo image to email: {e}")
        else:
            print(f"WARN: Logo file not found at {logo_path}")

