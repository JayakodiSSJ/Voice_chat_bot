from __future__ import annotations

import json
from typing import Any

from auth_sqlite import fetch_one, utcnow_iso
from tool_observability import tool_data_redactor
from tool_types import ToolSpec


PENDING_USER_CONFIRMATION = "pending_user_confirmation"
EXECUTING = "executing"
EXECUTED = "executed"
CANCELLED = "cancelled"
FAILED = "failed"


class ToolAuditService:
    def create_pending_confirmation(
        self,
        conn,
        *,
        company_id: int,
        agent_id: int,
        user_id: int,
        chat_session_id: int | None,
        tool: ToolSpec,
        arguments: dict[str, Any],
        question: str,
    ) -> dict[str, Any]:
        if tool.kind not in {"api", "mcp"}:
            raise ValueError("Only API and MCP actions use the shared tool approval log.")
        now = utcnow_iso()
        cur = conn.execute(
            """
            INSERT INTO tool_execution_audit_logs (
                company_id, agent_id, user_id, chat_session_id,
                tool_id, tool_kind, tool_name, side_effect, approval_policy,
                configuration_fingerprint, target_json, arguments_json,
                execution_arguments_json, request_message, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                agent_id,
                user_id,
                chat_session_id,
                tool.id,
                tool.kind,
                tool.name,
                tool.side_effect,
                tool.approval_policy,
                tool.configuration_fingerprint,
                self._encode(tool_data_redactor.redact(tool.target), 4000),
                self._encode(tool_data_redactor.redact(arguments), 20000),
                self._encode(arguments, 20000),
                tool_data_redactor.redact_text(question),
                PENDING_USER_CONFIRMATION,
                now,
            ),
        )
        return {"id": int(cur.lastrowid), "status": PENDING_USER_CONFIRMATION, "created_at": now}

    def get_for_user(
        self,
        conn,
        *,
        company_id: int,
        agent_id: int,
        user_id: int,
        activity_id: int,
    ) -> dict[str, Any] | None:
        row = fetch_one(
            conn,
            """
            SELECT l.*, ca.configuration AS agent_configuration, ca.name AS agent_name
            FROM tool_execution_audit_logs l
            JOIN custom_agents ca ON ca.id = l.agent_id AND ca.company_id = l.company_id
            WHERE l.id = ? AND l.company_id = ? AND l.agent_id = ? AND l.user_id = ?
            LIMIT 1
            """,
            (activity_id, company_id, agent_id, user_id),
        )
        return self._deserialize_row(row) if row else None

    def list_for_company(
        self,
        conn,
        *,
        company_id: int,
        limit: int = 100,
        user_query: str | None = None,
        activity_date: str | None = None,
    ) -> list[dict[str, Any]]:
        where_parts = ["l.company_id = ?"]
        params: list[Any] = [company_id]
        normalized_user = str(user_query or "").strip().lower()
        if normalized_user:
            where_parts.append(
                "(lower(COALESCE(requester.full_name, '')) LIKE ? OR lower(COALESCE(requester.email, '')) LIKE ?)"
            )
            params.extend([f"%{normalized_user}%", f"%{normalized_user}%"])
        normalized_date = str(activity_date or "").strip()
        if normalized_date:
            where_parts.append("substr(l.created_at, 1, 10) = ?")
            params.append(normalized_date[:10])
        params.append(max(1, min(int(limit or 100), 250)))
        rows = conn.execute(
            """
            SELECT
                l.*,
                ca.name AS agent_name,
                requester.full_name AS requester_name,
                requester.email AS requester_email,
                approver.full_name AS approver_name,
                approver.email AS approver_email,
                rejecter.full_name AS rejecter_name,
                rejecter.email AS rejecter_email
            FROM tool_execution_audit_logs l
            LEFT JOIN custom_agents ca ON ca.id = l.agent_id AND ca.company_id = l.company_id
            LEFT JOIN users requester ON requester.id = l.user_id
            LEFT JOIN users approver ON approver.id = l.approved_by_user_id
            LEFT JOIN users rejecter ON rejecter.id = l.rejected_by_user_id
            WHERE """ + " AND ".join(where_parts) + """
            ORDER BY l.created_at DESC, l.id DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        return [self._serialize_row(row) for row in rows]

    def mark_user_executing(self, conn, *, activity_id: int, company_id: int, user_id: int) -> bool:
        cur = conn.execute(
            """
            UPDATE tool_execution_audit_logs
            SET status = ?, approved_by_user_id = ?, approved_at = ?
            WHERE id = ? AND company_id = ? AND user_id = ? AND status = ?
            """,
            (EXECUTING, user_id, utcnow_iso(), activity_id, company_id, user_id, PENDING_USER_CONFIRMATION),
        )
        return int(getattr(cur, "rowcount", 0) or 0) == 1

    def mark_user_cancelled(
        self,
        conn,
        *,
        activity_id: int,
        company_id: int,
        user_id: int,
        reason: str | None = None,
    ) -> bool:
        cur = conn.execute(
            """
            UPDATE tool_execution_audit_logs
            SET status = ?, rejected_by_user_id = ?, rejected_at = ?, rejection_reason = ?
            WHERE id = ? AND company_id = ? AND user_id = ? AND status = ?
            """,
            (CANCELLED, user_id, utcnow_iso(), reason, activity_id, company_id, user_id, PENDING_USER_CONFIRMATION),
        )
        return int(getattr(cur, "rowcount", 0) or 0) == 1

    def mark_execution_result(
        self,
        conn,
        *,
        activity_id: int,
        company_id: int,
        status: str,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        conn.execute(
            """
            UPDATE tool_execution_audit_logs
            SET status = ?, result_json = ?, error_message = ?, executed_at = ?
            WHERE id = ? AND company_id = ?
            """,
            (
                status,
                self._encode(tool_data_redactor.redact(self._compact_result(result)), 20000),
                tool_data_redactor.redact_text(error_message) or None,
                utcnow_iso(),
                activity_id,
                company_id,
            ),
        )

    def _deserialize_row(self, row) -> dict[str, Any]:
        data = dict(row)
        for source, destination in (
            ("arguments_json", "arguments"),
            ("execution_arguments_json", "execution_arguments"),
            ("target_json", "target"),
            ("result_json", "result"),
        ):
            raw = data.get(source)
            try:
                data[destination] = json.loads(raw) if raw else {}
            except Exception:
                data[destination] = {}
        if not data.get("execution_arguments"):
            data["execution_arguments"] = data.get("arguments") or {}
        data["arguments"] = tool_data_redactor.redact(data.get("arguments") or {})
        return data

    def _serialize_row(self, row) -> dict[str, Any]:
        data = self._deserialize_row(row)
        data["requester_display_name"] = self._display_name(data.get("requester_name"), data.get("requester_email"))
        data["approver_display_name"] = self._display_name(data.get("approver_name"), data.get("approver_email"))
        data["rejecter_display_name"] = self._display_name(data.get("rejecter_name"), data.get("rejecter_email"))
        data.pop("arguments_json", None)
        data.pop("execution_arguments_json", None)
        data.pop("execution_arguments", None)
        data.pop("target_json", None)
        data.pop("result_json", None)
        return data

    @staticmethod
    def _display_name(full_name: Any, email: Any) -> str | None:
        name = str(full_name or "").strip()
        if name:
            return name
        email_text = str(email or "").strip()
        if not email_text:
            return None
        local_part = email_text.split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
        return local_part.title() if local_part else None

    @staticmethod
    def _encode(value: Any, limit: int) -> str:
        return json.dumps(value if value is not None else {}, default=str)[:limit]

    @staticmethod
    def _compact_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(result, dict):
            return result
        compact = dict(result)
        response = compact.get("response")
        if isinstance(response, dict):
            compact["response"] = {
                "status": response.get("status"),
                "isError": response.get("isError"),
                "truncated": response.get("truncated"),
                "content_omitted_from_audit": True,
            }
        return compact


tool_audit_service = ToolAuditService()
