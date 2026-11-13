from __future__ import annotations

import hashlib
import json
from typing import Any

from auth_sqlite import fetch_one, utcnow_iso
from tool_observability import tool_data_redactor


PENDING_USER_CONFIRMATION = "pending_user_confirmation"
EXECUTING = "executing"
EXECUTED = "executed"
CANCELLED = "cancelled"
FAILED = "failed"


class DatabaseToolAuditService:
    def create_activity(
        self,
        conn,
        *,
        company_id: int,
        agent_id: int,
        user_id: int | None,
        chat_session_id: int | None,
        profile: dict[str, Any],
        operation: str,
        sql: str,
        question: str,
        status: str,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any]:
        now = utcnow_iso()
        max_affected_rows = int((profile.get("limits") or {}).get("max_affected_rows") or 10)
        affected_rows = self._affected_rows_for_result(result)
        cur = conn.execute(
            """
            INSERT INTO database_tool_audit_logs (
                company_id, agent_id, user_id, chat_session_id, profile_index,
                profile_name, engine, operation, status, sql_text, sql_hash,
                request_message, affected_rows, max_affected_rows,
                result_json, error_message, created_at, executed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                agent_id,
                user_id,
                chat_session_id,
                int(profile["index"]),
                profile.get("name"),
                profile.get("engine"),
                operation,
                status,
                sql,
                self._hash_sql(sql),
                question[:4000],
                affected_rows,
                max_affected_rows,
                json.dumps(tool_data_redactor.redact(self._compact_result(result)), default=str)[:20000] if result else None,
                tool_data_redactor.redact_text(error_message) or None,
                now,
                now if status in {EXECUTED, FAILED} else None,
            ),
        )
        return {"id": int(cur.lastrowid), "status": status, "created_at": now}

    def create_pending_confirmation(
        self,
        conn,
        *,
        company_id: int,
        agent_id: int,
        user_id: int | None,
        chat_session_id: int | None,
        profile: dict[str, Any],
        operation: str,
        sql: str,
        question: str,
        estimated_affected_rows: int | None = None,
        preview_error: str | None = None,
    ) -> dict[str, Any]:
        now = utcnow_iso()
        max_affected_rows = int((profile.get("limits") or {}).get("max_affected_rows") or 10)
        cur = conn.execute(
            """
            INSERT INTO database_tool_audit_logs (
                company_id, agent_id, user_id, chat_session_id, profile_index,
                profile_name, engine, operation, status, sql_text, sql_hash,
                request_message, estimated_affected_rows, preview_error,
                max_affected_rows, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                company_id,
                agent_id,
                user_id,
                chat_session_id,
                int(profile["index"]),
                profile.get("name"),
                profile.get("engine"),
                operation,
                PENDING_USER_CONFIRMATION,
                sql,
                self._hash_sql(sql),
                question[:4000],
                estimated_affected_rows,
                preview_error,
                max_affected_rows,
                now,
            ),
        )
        approval_id = int(cur.lastrowid)
        return {
            "id": approval_id,
            "status": PENDING_USER_CONFIRMATION,
            "created_at": now,
            "max_affected_rows": max_affected_rows,
        }

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
        normalized_user = (user_query or "").strip().lower()
        if normalized_user:
            where_parts.append(
                "(lower(COALESCE(requester.full_name, '')) LIKE ? OR lower(COALESCE(requester.email, '')) LIKE ?)"
            )
            params.extend([f"%{normalized_user}%", f"%{normalized_user}%"])
        normalized_date = (activity_date or "").strip()
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
            FROM database_tool_audit_logs l
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

    def get_for_company(self, conn, *, company_id: int, approval_id: int) -> dict[str, Any] | None:
        row = fetch_one(
            conn,
            """
            SELECT l.*, ca.configuration AS agent_configuration, ca.name AS agent_name
            FROM database_tool_audit_logs l
            JOIN custom_agents ca ON ca.id = l.agent_id AND ca.company_id = l.company_id
            WHERE l.id = ? AND l.company_id = ?
            LIMIT 1
            """,
            (approval_id, company_id),
        )
        return dict(row) if row else None

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
            FROM database_tool_audit_logs l
            JOIN custom_agents ca ON ca.id = l.agent_id AND ca.company_id = l.company_id
            WHERE l.id = ? AND l.company_id = ? AND l.agent_id = ? AND l.user_id = ?
            LIMIT 1
            """,
            (activity_id, company_id, agent_id, user_id),
        )
        return dict(row) if row else None

    def mark_user_executing(self, conn, *, activity_id: int, company_id: int, user_id: int) -> bool:
        cur = conn.execute(
            """
            UPDATE database_tool_audit_logs
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
            UPDATE database_tool_audit_logs
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
        approval_id: int,
        company_id: int,
        status: str,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> None:
        affected_rows = self._affected_rows_for_result(result)
        conn.execute(
            """
            UPDATE database_tool_audit_logs
            SET status = ?, affected_rows = ?, result_json = ?, error_message = ?, executed_at = ?
            WHERE id = ? AND company_id = ?
            """,
            (
                status,
                affected_rows,
                json.dumps(tool_data_redactor.redact(self._compact_result(result)), default=str)[:20000],
                tool_data_redactor.redact_text(error_message) or None,
                utcnow_iso(),
                approval_id,
                company_id,
            ),
        )

    def _serialize_row(self, row) -> dict[str, Any]:
        data = dict(row)
        data["requester_display_name"] = self._display_name(data.get("requester_name"), data.get("requester_email"))
        data["approver_display_name"] = self._display_name(data.get("approver_name"), data.get("approver_email"))
        data["rejecter_display_name"] = self._display_name(data.get("rejecter_name"), data.get("rejecter_email"))
        result_json = data.pop("result_json", None)
        if result_json:
            try:
                data["result"] = json.loads(result_json)
            except Exception:
                data["result"] = None
        else:
            data["result"] = None
        return data

    def _display_name(self, full_name: Any, email: Any) -> str | None:
        name = str(full_name or "").strip()
        if name:
            return name
        email_text = str(email or "").strip()
        if not email_text:
            return None
        local_part = email_text.split("@", 1)[0].replace(".", " ").replace("_", " ").strip()
        return local_part.title() if local_part else None

    def _hash_sql(self, sql: str) -> str:
        return hashlib.sha256(sql.encode("utf-8")).hexdigest()

    def _compact_result(self, result: dict[str, Any] | None) -> dict[str, Any] | None:
        if not isinstance(result, dict):
            return result
        compact = dict(result)
        response = compact.get("response")
        if isinstance(response, dict) and isinstance(response.get("rows"), list):
            compact["response"] = {
                "columns": response.get("columns") or [],
                "row_count": response.get("row_count"),
                "truncated": response.get("truncated"),
                "result_size_limited": response.get("result_size_limited"),
                "rows": [],
                "rows_omitted_from_audit": True,
            }
        return compact

    def _affected_rows_for_result(self, result: dict[str, Any] | None) -> int | None:
        response = (result or {}).get("response") if isinstance(result, dict) else {}
        if not isinstance(response, dict):
            return None
        if response.get("affected_rows") is not None:
            return response.get("affected_rows")
        if response.get("row_count") is not None:
            return response.get("row_count")
        return None


database_tool_audit_service = DatabaseToolAuditService()
