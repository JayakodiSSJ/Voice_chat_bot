from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import date, datetime, time
from decimal import Decimal
import hashlib
import json
import os
import re
import sqlite3
from typing import Any, Iterator

from agent_runtime_utils import clean_text, extract_json_object
from model_router import model_router
from openai_service import openai_service
from tool_routing_profile import routing_profile_from


ENGINE_ALIASES = {
    "postgres": "postgres",
    "postgresql": "postgres",
    "pg": "postgres",
    "mysql": "mysql",
    "mariadb": "mysql",
    "sqlserver": "sqlserver",
    "mssql": "sqlserver",
    "azure_sql": "sqlserver",
    "azure-sql": "sqlserver",
    "sqlite": "sqlite",
    "sqlite3": "sqlite",
    "oracle": "oracle",
    "oracledb": "oracle",
    "odbc": "odbc",
    "generic_odbc": "odbc",
}

ENGINE_LABELS = {
    "postgres": "PostgreSQL",
    "mysql": "MySQL/MariaDB",
    "sqlserver": "SQL Server/Azure SQL",
    "sqlite": "SQLite",
    "oracle": "Oracle",
    "odbc": "Generic ODBC",
}

PERMISSION_MODES = {
    "read_only": {"select"},
    "append_only": {"select", "insert"},
    "read_update": {"select", "update"},
    "read_insert_update": {"select", "insert", "update"},
    "read_write": {"select", "insert", "update", "delete"},
}

PERMISSION_MODE_LABELS = {
    "read_only": "Read-only",
    "append_only": "Append-only",
    "read_update": "Read + Update",
    "read_insert_update": "Read + Insert + Update",
    "read_write": "Read/Write DML",
}

MAX_DATABASE_PROFILES_PER_AGENT = 3
OPERATION_ORDER = ("select", "insert", "update", "delete")

BLOCKED_SQL_KEYWORDS = {
    "alter",
    "analyze",
    "call",
    "comment",
    "copy",
    "create",
    "do",
    "drop",
    "execute",
    "grant",
    "lock",
    "merge",
    "reindex",
    "reset",
    "revoke",
    "set",
    "truncate",
    "vacuum",
}

REQUESTED_OPERATION_PATTERNS = [
    (
        "delete",
        re.compile(r"\b(delete|remove|erase)\b", flags=re.I),
    ),
    (
        "update",
        re.compile(r"\b(update|edit|change|modify|set|mark)\b", flags=re.I),
    ),
    (
        "insert",
        re.compile(r"\b(add|create|insert|new|register)\b", flags=re.I),
    ),
    (
        "select",
        re.compile(r"\b(show|list|find|get|fetch|select|count|search|summarize|report)\b", flags=re.I),
    ),
]


class DatabaseToolService:
    def configured_profiles(self, agent_config: dict[str, Any]) -> list[dict[str, Any]]:
        db_config = (
            agent_config.get("database")
            or agent_config.get("database_config")
            or agent_config.get("databaseConfig")
            or agent_config.get("structured_database")
            or agent_config.get("structuredDatabase")
            or {}
        )

        configured_tools = agent_config.get("tools") or []
        normalized_tools = {
            str(item.get("type") if isinstance(item, dict) else item or "").strip().lower()
            for item in configured_tools
        }
        if configured_tools and not normalized_tools.intersection({"database", "db", "structured_database"}):
            return []

        raw_profiles: Any = []
        if isinstance(db_config, dict):
            raw_profiles = (
                db_config.get("profiles")
                or db_config.get("connections")
                or db_config.get("databases")
                or db_config.get("profile")
                or []
            )
        elif isinstance(db_config, list):
            raw_profiles = db_config

        if isinstance(raw_profiles, dict):
            raw_profiles = [raw_profiles]
        if not isinstance(raw_profiles, list):
            return []

        profiles: list[dict[str, Any]] = []
        for configured_index, profile in enumerate(raw_profiles[:MAX_DATABASE_PROFILES_PER_AGENT]):
            if not isinstance(profile, dict):
                continue
            normalized = self._normalize_profile(profile)
            if not normalized:
                continue
            normalized["index"] = len(profiles)
            normalized["configured_index"] = configured_index
            profiles.append(normalized)
        return profiles

    def verify_profile(self, profile_config: dict[str, Any]) -> dict[str, Any]:
        profile = self._normalize_profile(profile_config)
        if not profile:
            raise RuntimeError("Configure a valid structured database connection profile.")

        with self._connect(profile) as conn:
            self._configure_session(conn, profile)
            database_name, role_name = self._connection_identity(conn, profile)
            schemas = self._introspect_schemas(conn, profile)
            role_allowed_operations, table_permissions = self._detect_role_permissions(conn, profile, schemas)

        if role_allowed_operations is not None:
            profile["role_allowed_operations"] = role_allowed_operations
            profile["table_permissions"] = table_permissions
            profile["allowed_operations"] = self._effective_allowed_operations(
                profile["permission_mode"],
                role_allowed_operations,
            )
            schemas = self._schemas_with_table_permissions(schemas, table_permissions, profile["permission_mode"])

        message = f"Connected. Detected {len(schemas)} schema(s) visible to this DB role."
        access_mode_operations = self._operations_for_mode(profile["permission_mode"])
        if role_allowed_operations is not None and set(profile["allowed_operations"]) != set(access_mode_operations):
            message += (
                " Effective access is limited to "
                f"{self._format_operations(profile['allowed_operations'])} after applying DB role permissions."
            )

        return {
            "ok": True,
            "engine": profile["engine"],
            "engine_label": ENGINE_LABELS.get(profile["engine"], profile["engine"]),
            "name": profile["name"],
            "permission_mode": profile["permission_mode"],
            "access_mode_operations": access_mode_operations,
            "role_permissions_detected": role_allowed_operations is not None,
            "role_allowed_operations": role_allowed_operations,
            "allowed_operations": profile["allowed_operations"],
            "database": database_name,
            "role": role_name,
            "message": message,
            "schemas": schemas,
            "limits": profile["limits"],
        }

    def plan_call(
        self,
        *,
        api_key: str,
        agent_name: str,
        question: str,
        history: list[dict[str, str]],
        profiles: list[dict[str, Any]],
        selected_profile_index: int | None = None,
        selected_profile_name: str | None = None,
    ) -> dict[str, Any]:
        summaries, preliminary_plan = self.planning_profile_summaries(
            profiles=profiles,
            question=question,
            selected_profile_index=selected_profile_index,
            selected_profile_name=selected_profile_name,
        )
        if preliminary_plan:
            return preliminary_plan
        selected_profile_name = clean_text(selected_profile_name or "")
        selected_profile_index = summaries[0]["index"] if (selected_profile_name or selected_profile_index is not None) else None

        history_text = "\n".join(
            f"{item.get('role')}: {clean_text(item.get('content') or '')[:500]}"
            for item in history[-6:]
            if item.get("role") in {"user", "assistant"} and item.get("content")
        )
        requested_operation = self.detect_requested_operation(question)
        planning_prompt = (
            f"You are planning governed database tool use for {agent_name}. "
            "Choose only from the configured database profiles and visible schema/table scope. "
            "Return JSON only with this shape: "
            "{\"should_call\": boolean, \"profile_index\": number|null, \"sql\": string, "
            "\"reason\": string, \"missing_parameters\": array, \"profile_confidence\": string, \"profile_reason\": string}. "
            "Set should_call=true when the user's request clearly needs assigned database data or asks to create, add, insert, update, modify, or delete assigned database records. "
            "In Auto mode, choose the best profile using the user's intent, each profile's purpose, visible schemas, tables, columns, and allowed operations. "
            "If the current request is a write operation and configured profiles include that operation, ignore earlier conversation refusals caused by a different selected profile. "
            "When a profile was manually selected, use only that selected profile. "
            "For create/add/register requests, generate INSERT when INSERT is listed for that profile. "
            "For edit/change/modify requests, generate UPDATE when UPDATE is listed for that profile. "
            "For remove/delete requests, generate DELETE when DELETE is listed for that profile. "
            "Generate one SQL statement in the configured profile's SQL dialect and only use operations listed for that profile. "
            "For INSERT, always include an explicit target table and column list. Do not include auto-generated/default columns unless the user supplied those values. "
            "Do not use DDL, SET, CALL, stored procedures, privileged commands, or multiple statements. "
            "For UPDATE or DELETE, always include a narrow WHERE clause. "
            "Use schema-qualified table names when the database engine supports them. "
            "If required values are missing or the request cannot be handled within assigned scope, set should_call=false and list missing_parameters."
        )
        response_text = openai_service.call_text(
            api_key=api_key,
            model=model_router.choose("database_plan"),
            instructions=planning_prompt,
            input_messages=[
                {
                    "role": "user",
                    "content": (
                        f"Configured database profiles:\n{json.dumps(summaries, indent=2)}\n\n"
                        f"Recent conversation:\n{history_text or 'None'}\n\n"
                        f"User message:\n{question}"
                    ),
                }
            ],
            max_output_tokens=650,
        )
        try:
            plan = json.loads(extract_json_object(response_text))
        except Exception:
            return {"should_call": False, "profile_index": None, "sql": "", "reason": "invalid_database_plan"}
        if not isinstance(plan, dict):
            return {"should_call": False, "profile_index": None, "sql": "", "reason": "invalid_database_plan"}
        plan["sql"] = str(plan.get("sql") or "").strip()
        if selected_profile_index is not None and plan.get("should_call"):
            plan["profile_index"] = selected_profile_index
        plan.setdefault("requested_operation", requested_operation)
        return plan

    def planning_profile_summaries(
        self,
        *,
        profiles: list[dict[str, Any]],
        question: str,
        selected_profile_index: int | None = None,
        selected_profile_name: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Return the database tools visible to a planner and any deterministic refusal."""
        summaries = [self._profile_summary(profile) for profile in profiles]
        summaries = [summary for summary in summaries if summary["allowed_schemas"]]
        selected_summary = None
        selected_profile_name = clean_text(selected_profile_name or "")
        if selected_profile_name:
            selected_summary = next(
                (
                    summary
                    for summary in summaries
                    if str(summary.get("name") or "").casefold() == selected_profile_name.casefold()
                ),
                None,
            )
        if selected_summary is None and selected_profile_index is not None:
            selected_summary = next(
                (summary for summary in summaries if summary["index"] == selected_profile_index),
                None,
            )
        if selected_profile_name or selected_profile_index is not None:
            if selected_summary is None:
                return [], {
                    "should_call": False,
                    "profile_index": selected_profile_index,
                    "sql": "",
                    "reason": "selected_database_profile_unavailable",
                }
            summaries = [selected_summary]
            selected_profile_index = selected_summary["index"]
        if not summaries:
            return [], {"should_call": False, "profile_index": None, "sql": "", "reason": "no_allowed_database_scope"}

        requested_operation = self.detect_requested_operation(question)
        if requested_operation in {"insert", "update", "delete"}:
            if selected_profile_index is None and not selected_profile_name:
                operation_summaries = [
                    summary
                    for summary in summaries
                    if requested_operation in (summary.get("allowed_operations") or [])
                ]
                if operation_summaries:
                    summaries = operation_summaries
            allowed_operations = {
                operation
                for summary in summaries
                for operation in (summary.get("allowed_operations") or [])
            }
            if requested_operation not in allowed_operations:
                return [], {
                    "should_call": False,
                    "profile_index": selected_profile_index,
                    "sql": "",
                    "reason": "database_operation_not_allowed",
                    "requested_operation": requested_operation,
                    "allowed_operations": sorted(allowed_operations),
                }
        return summaries, None

    def detect_requested_operation(self, question: str) -> str | None:
        normalized = clean_text(question).lower()
        if not normalized:
            return None
        for operation, pattern in REQUESTED_OPERATION_PATTERNS:
            if pattern.search(normalized):
                return operation
        return None

    def execute_configured_query(
        self,
        profiles: list[dict[str, Any]],
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        prepared = self.prepare_configured_query(profiles, plan)
        profile = prepared["profile"]
        safe_sql = prepared["sql"]
        operation = prepared["operation"]

        limits = profile["limits"]
        max_rows = int(limits.get("max_rows") or 100)
        max_result_bytes = int(limits.get("max_result_bytes") or 12000)

        with self._connect(profile) as conn:
            self._configure_session(conn, profile)
            self._apply_schema_scope(conn, profile)
            with self._cursor(conn) as cur:
                cur.execute(safe_sql)
                if operation == "select":
                    columns = self._column_names(cur)
                    rows = cur.fetchmany(max_rows + 1)
                else:
                    affected_rows = max(0, int(getattr(cur, "rowcount", 0) or 0))
                    max_affected_rows = int(limits.get("max_affected_rows") or 10)
                    if affected_rows > max_affected_rows:
                        self._rollback(conn)
                        raise RuntimeError(
                            f"Database write affected {affected_rows} rows, exceeding the configured limit of {max_affected_rows}."
                        )
                    self._commit(conn)
                    return {
                        "type": "database",
                        "ok": True,
                        "profile_index": profile["index"],
                        "profile_name": profile["name"],
                        "engine": profile["engine"],
                        "operation": operation,
                        "request": {"sql": safe_sql},
                        "response": {
                            "affected_rows": affected_rows,
                            "max_affected_rows": max_affected_rows,
                        },
                    }

        truncated_rows = len(rows) > max_rows
        rows = rows[:max_rows]
        row_dicts = [
            {column: self._json_safe_value(value) for column, value in zip(columns, row)}
            for row in rows
        ]
        payload: Any = {
            "columns": columns,
            "rows": row_dicts,
            "row_count": len(row_dicts),
            "truncated": truncated_rows,
        }
        encoded = json.dumps(payload, default=str)
        if len(encoded) > max_result_bytes:
            payload = {
                "columns": columns,
                "rows": row_dicts[: max(1, min(len(row_dicts), 10))],
                "row_count": len(row_dicts),
                "truncated": True,
                "result_size_limited": True,
                "preview": encoded[:max_result_bytes],
            }

        return {
            "type": "database",
            "ok": True,
            "profile_index": profile["index"],
            "profile_name": profile["name"],
            "engine": profile["engine"],
            "operation": operation,
            "request": {"sql": safe_sql},
            "response": payload,
        }

    def prepare_configured_query(
        self,
        profiles: list[dict[str, Any]],
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            profile_index = int(plan.get("profile_index"))
        except (TypeError, ValueError):
            raise RuntimeError("Database tool selected an invalid profile.")

        profile = next((item for item in profiles if item["index"] == profile_index), None)
        if not profile:
            raise RuntimeError("Database tool selected a profile that is not assigned to this agent.")

        sql = str(plan.get("sql") or "").strip()
        safe_sql, operation = self._validate_sql_for_profile(profile, sql)
        self._validate_scope(profile, safe_sql, operation=operation)
        self._validate_role_table_permissions(profile, safe_sql, operation)
        self._validate_column_scope(profile, safe_sql, operation)
        return {
            "profile": profile,
            "sql": safe_sql,
            "operation": operation,
        }

    def write_requires_approval(self, operation: str) -> bool:
        return operation in {"insert", "update", "delete"}

    def estimate_write_impact(self, profile: dict[str, Any], sql: str, operation: str) -> dict[str, Any]:
        if operation == "insert":
            return {"estimated_affected_rows": self._estimate_insert_rows(sql), "preview_error": None}
        if operation not in {"update", "delete"}:
            return {"estimated_affected_rows": None, "preview_error": None}

        preview_sql = self._count_sql_for_write(sql, operation)
        if not preview_sql:
            return {"estimated_affected_rows": None, "preview_error": "Could not build a row-count preview for this SQL dialect."}
        try:
            with self._connect(profile) as conn:
                self._configure_session(conn, {**profile, "permission_mode": "read_only"})
                self._apply_schema_scope(conn, profile)
                with self._cursor(conn) as cur:
                    cur.execute(preview_sql)
                    row = cur.fetchone()
                self._rollback(conn)
            if row is None:
                return {"estimated_affected_rows": None, "preview_error": "Row-count preview returned no result."}
            return {"estimated_affected_rows": int(row[0]), "preview_error": None}
        except Exception as exc:
            return {"estimated_affected_rows": None, "preview_error": str(exc)[:1000]}

    def supported_engines(self) -> list[dict[str, str]]:
        return [{"id": engine, "label": label} for engine, label in ENGINE_LABELS.items()]

    def _json_safe_value(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, Decimal):
            return int(value) if value == value.to_integral_value() else str(value)
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        if isinstance(value, bytes):
            return base64.b64encode(value).decode("ascii")
        if isinstance(value, dict):
            return {str(key): self._json_safe_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._json_safe_value(item) for item in value]
        return str(value)

    def _normalize_profile(self, profile: dict[str, Any]) -> dict[str, Any] | None:
        raw_engine = str(profile.get("engine") or profile.get("type") or "postgres").strip().lower()
        engine = ENGINE_ALIASES.get(raw_engine)
        if not engine:
            return None

        limits = profile.get("limits") if isinstance(profile.get("limits"), dict) else {}
        allowed_schemas = self._list_from_value(
            profile.get("allowed_schemas")
            or profile.get("allowedSchemas")
            or profile.get("schemas")
            or profile.get("selectedSchemas")
        )
        allowed_tables = self._normalize_allowed_tables(
            profile.get("allowed_tables")
            or profile.get("allowedTables")
            or profile.get("tables")
            or profile.get("selectedTables")
        )
        allowed_columns = self._normalize_allowed_columns(
            profile.get("allowed_columns")
            or profile.get("allowedColumns")
            or profile.get("selectedColumns")
        )
        schema_metadata = self._normalize_schema_metadata(
            profile.get("schema_metadata")
            or profile.get("schemaMetadata")
            or profile.get("detectedSchemas")
            or profile.get("schemas_metadata")
        )
        permission_mode = self._normalize_permission_mode(
            profile.get("permission_mode")
            or profile.get("permissionMode")
            or profile.get("mode")
        )
        role_allowed_operations_is_configured = any(
            key in profile
            for key in ("role_allowed_operations", "roleAllowedOperations", "dbRoleOperations", "db_role_operations")
        ) or bool(profile.get("rolePermissionsDetected") or profile.get("role_permissions_detected"))
        role_allowed_operations = (
            self._normalize_operation_list(
                profile.get("role_allowed_operations")
                or profile.get("roleAllowedOperations")
                or profile.get("dbRoleOperations")
                or profile.get("db_role_operations")
            )
            if role_allowed_operations_is_configured
            else None
        )
        table_permissions = self._normalize_table_permissions(
            profile.get("table_permissions")
            or profile.get("tablePermissions")
            or profile.get("roleTablePermissions")
            or profile.get("role_table_permissions")
        )
        if not table_permissions:
            table_permissions = self._table_permissions_from_schema_metadata(schema_metadata)
        if role_allowed_operations is None and table_permissions:
            role_allowed_operations = sorted(
                {
                    operation
                    for operations in table_permissions.values()
                    for operation in operations
                },
                key=self._operation_sort_key,
            )

        connection_uri = str(
            profile.get("connection_uri")
            or profile.get("connectionUri")
            or profile.get("connection_uri_secret")
            or profile.get("connectionUriSecret")
            or profile.get("database_url")
            or profile.get("databaseUrl")
            or profile.get("odbcConnectionString")
            or profile.get("odbc_connection_string")
            or ""
        ).strip()
        database = str(profile.get("database") or profile.get("dbname") or profile.get("service_name") or "").strip()
        file_path = str(profile.get("file_path") or profile.get("filePath") or "").strip()
        name = str(profile.get("name") or profile.get("label") or database or file_path or "Database").strip()
        purpose = str(
            profile.get("purpose")
            or profile.get("description")
            or profile.get("profilePurpose")
            or profile.get("profile_purpose")
            or ""
        ).strip()[:500]
        return {
            "engine": engine,
            "name": name,
            "purpose": purpose,
            "routing_profile": routing_profile_from(profile),
            "connection_uri": connection_uri,
            "host": str(profile.get("host") or profile.get("server") or "").strip(),
            "port": profile.get("port") or self._default_port(engine),
            "database": database,
            "file_path": file_path,
            "username": str(profile.get("username") or profile.get("user") or "").strip(),
            "password": str(profile.get("password") or profile.get("password_secret") or profile.get("passwordSecret") or "").strip(),
            "sslmode": str(profile.get("sslmode") or profile.get("sslMode") or "prefer").strip(),
            "driver": str(profile.get("driver") or "").strip(),
            "dsn": str(profile.get("dsn") or "").strip(),
            "service_name": str(profile.get("service_name") or profile.get("serviceName") or database).strip(),
            "allowed_schemas": allowed_schemas,
            "allowed_tables": allowed_tables,
            "allowed_columns": allowed_columns,
            "permission_mode": permission_mode,
            "role_permissions_detected": role_allowed_operations is not None,
            "role_allowed_operations": role_allowed_operations,
            "allowed_operations": self._effective_allowed_operations(permission_mode, role_allowed_operations),
            "table_permissions": table_permissions,
            "schema_metadata": schema_metadata,
            "limits": {
                "max_rows": self._int_limit(limits.get("max_rows") or limits.get("maxRows"), 100, 1, 1000),
                "timeout_seconds": self._int_limit(limits.get("timeout_seconds") or limits.get("timeoutSeconds"), 15, 1, 120),
                "max_result_bytes": self._int_limit(limits.get("max_result_bytes") or limits.get("maxResultBytes"), 12000, 1000, 200000),
                "max_affected_rows": self._int_limit(limits.get("max_affected_rows") or limits.get("maxAffectedRows"), 10, 1, 1000),
            },
        }

    def _connect(self, profile: dict[str, Any]):
        engine = profile["engine"]
        if engine == "postgres":
            return self._connect_postgres(profile)
        if engine == "mysql":
            return self._connect_mysql(profile)
        if engine == "sqlserver":
            return self._connect_sqlserver(profile)
        if engine == "sqlite":
            return self._connect_sqlite(profile)
        if engine == "oracle":
            return self._connect_oracle(profile)
        if engine == "odbc":
            return self._connect_odbc(profile)
        raise RuntimeError(f"Unsupported database engine: {engine}.")

    def _connect_postgres(self, profile: dict[str, Any]):
        try:
            import psycopg
            from psycopg.rows import tuple_row
        except ImportError as exc:
            raise RuntimeError("PostgreSQL support requires psycopg. Install psycopg[binary].") from exc

        connect_timeout = min(int(profile["limits"].get("timeout_seconds") or 15), 30)
        if profile.get("connection_uri"):
            return psycopg.connect(self._decrypt_secret(profile["connection_uri"]), row_factory=tuple_row, connect_timeout=connect_timeout)
        self._require_direct_fields(profile, require_username=True)
        return psycopg.connect(
            host=profile["host"],
            port=int(profile.get("port") or 5432),
            dbname=profile["database"],
            user=profile["username"],
            password=self._decrypt_secret(profile.get("password")) or None,
            sslmode=profile.get("sslmode") or "prefer",
            row_factory=tuple_row,
            connect_timeout=connect_timeout,
        )

    def _connect_mysql(self, profile: dict[str, Any]):
        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError("MySQL/MariaDB support requires PyMySQL. Install PyMySQL.") from exc

        timeout = min(int(profile["limits"].get("timeout_seconds") or 15), 30)
        if profile.get("connection_uri"):
            return pymysql.connect(
                defer_connect=False,
                connect_timeout=timeout,
                read_timeout=timeout,
                write_timeout=timeout,
                charset="utf8mb4",
                **self._mysql_kwargs_from_uri(self._decrypt_secret(profile["connection_uri"]) or ""),
            )
        self._require_direct_fields(profile, require_username=True)
        return pymysql.connect(
            host=profile["host"],
            port=int(profile.get("port") or 3306),
            database=profile["database"],
            user=profile["username"],
            password=self._decrypt_secret(profile.get("password")) or None,
            connect_timeout=timeout,
            read_timeout=timeout,
            write_timeout=timeout,
            charset="utf8mb4",
        )

    def _connect_sqlserver(self, profile: dict[str, Any]):
        try:
            import pyodbc
        except ImportError as exc:
            raise RuntimeError("SQL Server/Azure SQL support requires pyodbc and an installed ODBC driver.") from exc

        timeout = min(int(profile["limits"].get("timeout_seconds") or 15), 30)
        connection_string = self._decrypt_secret(profile.get("connection_uri"))
        if not connection_string:
            self._require_direct_fields(profile, require_username=True)
            driver = profile.get("driver") or "ODBC Driver 18 for SQL Server"
            connection_string = (
                f"DRIVER={{{driver}}};SERVER={profile['host']},{int(profile.get('port') or 1433)};"
                f"DATABASE={profile['database']};UID={profile['username']};"
                f"PWD={self._decrypt_secret(profile.get('password')) or ''};Encrypt=yes;TrustServerCertificate=no;"
            )
        return pyodbc.connect(connection_string, timeout=timeout)

    def _connect_sqlite(self, profile: dict[str, Any]):
        database_path = self._decrypt_secret(profile.get("connection_uri")) or profile.get("file_path") or profile.get("database")
        if not database_path:
            raise RuntimeError("SQLite requires a database file path or URI.")
        uri = str(database_path).startswith("file:")
        conn = sqlite3.connect(database_path, timeout=float(profile["limits"].get("timeout_seconds") or 15), uri=uri)
        conn.row_factory = None
        return conn

    def _connect_oracle(self, profile: dict[str, Any]):
        try:
            import oracledb
        except ImportError as exc:
            raise RuntimeError("Oracle support requires oracledb. Install oracledb.") from exc

        timeout = int(profile["limits"].get("timeout_seconds") or 15)
        if profile.get("connection_uri"):
            dsn = self._decrypt_secret(profile["connection_uri"])
        else:
            self._require_direct_fields(profile, require_username=True)
            dsn = oracledb.makedsn(profile["host"], int(profile.get("port") or 1521), service_name=profile.get("service_name") or profile["database"])
        return oracledb.connect(
            user=profile["username"] or None,
            password=self._decrypt_secret(profile.get("password")) or None,
            dsn=dsn,
            tcp_connect_timeout=timeout,
        )

    def _connect_odbc(self, profile: dict[str, Any]):
        try:
            import pyodbc
        except ImportError as exc:
            raise RuntimeError("Generic ODBC support requires pyodbc and a configured ODBC driver or DSN.") from exc

        timeout = min(int(profile["limits"].get("timeout_seconds") or 15), 30)
        connection_string = self._decrypt_secret(profile.get("connection_uri"))
        if not connection_string:
            if profile.get("dsn"):
                connection_string = f"DSN={profile['dsn']};"
                if profile.get("username"):
                    connection_string += f"UID={profile['username']};"
                if profile.get("password"):
                    connection_string += f"PWD={self._decrypt_secret(profile.get('password')) or ''};"
            else:
                raise RuntimeError("Generic ODBC requires a DSN or ODBC connection string.")
        return pyodbc.connect(connection_string, timeout=timeout)

    def _configure_readonly_session(self, conn, profile: dict[str, Any]) -> None:
        self._configure_session(conn, profile)

    def _configure_session(self, conn, profile: dict[str, Any]) -> None:
        engine = profile["engine"]
        permission_mode = profile.get("permission_mode") or "read_only"
        timeout_ms = int(profile["limits"].get("timeout_seconds") or 15) * 1000
        statements: list[tuple[str, tuple[Any, ...]]] = []
        if engine == "postgres" and permission_mode == "read_only":
            statements = [("SET TRANSACTION READ ONLY", ()), (f"SET LOCAL statement_timeout = {timeout_ms}", ())]
        elif engine == "mysql":
            statements = [(f"SET SESSION max_execution_time = {timeout_ms}", ())]
            if permission_mode == "read_only":
                statements.insert(0, ("SET SESSION TRANSACTION READ ONLY", ()))
        elif engine == "sqlite" and permission_mode == "read_only":
            statements = [("PRAGMA query_only = ON", ())]
        elif engine == "sqlserver":
            statements = [(f"SET LOCK_TIMEOUT {timeout_ms}", ())]

        for statement, params in statements:
            try:
                with self._cursor(conn) as cur:
                    cur.execute(statement, params)
            except Exception:
                # SQL validation is the hard guardrail; session-level hints vary by engine and permissions.
                self._recover_connection(conn)
                continue

    def _apply_schema_scope(self, conn, profile: dict[str, Any]) -> None:
        if profile["engine"] != "postgres":
            return
        schemas = profile.get("allowed_schemas") or []
        if not schemas:
            return
        quoted = ", ".join(self._quote_identifier(schema, profile["engine"]) for schema in schemas)
        try:
            with self._cursor(conn) as cur:
                cur.execute(f"SET LOCAL search_path = {quoted}, pg_catalog")
        except Exception:
            self._recover_connection(conn)
            return

    def _connection_identity(self, conn, profile: dict[str, Any]) -> tuple[str, str]:
        engine = profile["engine"]
        queries = {
            "postgres": "SELECT current_database(), current_user",
            "mysql": "SELECT DATABASE(), CURRENT_USER()",
            "sqlserver": "SELECT DB_NAME(), SUSER_SNAME()",
            "oracle": "SELECT SYS_CONTEXT('USERENV', 'DB_NAME'), USER FROM dual",
            "odbc": None,
            "sqlite": None,
        }
        query = queries.get(engine)
        if query:
            try:
                with self._cursor(conn) as cur:
                    cur.execute(query)
                    row = cur.fetchone()
                    if row:
                        return str(row[0] or profile.get("database") or ""), str(row[1] or profile.get("username") or "")
            except Exception:
                self._recover_connection(conn)
                pass
        return str(profile.get("database") or profile.get("file_path") or profile.get("dsn") or ""), str(profile.get("username") or "readonly")

    def _introspect_schemas(self, conn, profile: dict[str, Any]) -> list[dict[str, Any]]:
        if profile["engine"] == "sqlite":
            return self._introspect_sqlite(conn)
        if profile["engine"] == "oracle":
            return self._introspect_oracle(conn)
        return self._introspect_information_schema(conn, profile)

    def _introspect_information_schema(self, conn, profile: dict[str, Any]) -> list[dict[str, Any]]:
        engine = profile["engine"]
        table_sql = """
            SELECT table_schema, table_name, table_type
            FROM information_schema.tables
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog', 'mysql', 'performance_schema', 'sys')
              AND table_schema NOT LIKE 'pg_%'
              AND table_type IN ('BASE TABLE', 'VIEW')
            ORDER BY table_schema, table_name
        """
        column_sql = """
            SELECT table_schema, table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema NOT IN ('information_schema', 'pg_catalog', 'mysql', 'performance_schema', 'sys')
              AND table_schema NOT LIKE 'pg_%'
            ORDER BY table_schema, table_name, ordinal_position
        """
        if engine == "sqlserver":
            table_sql = """
                SELECT table_schema, table_name, table_type
                FROM information_schema.tables
                WHERE table_type IN ('BASE TABLE', 'VIEW')
                ORDER BY table_schema, table_name
            """
            column_sql = """
                SELECT table_schema, table_name, column_name, data_type, is_nullable
                FROM information_schema.columns
                ORDER BY table_schema, table_name, ordinal_position
            """

        with self._cursor(conn) as cur:
            cur.execute(table_sql)
            table_rows = cur.fetchall()
        with self._cursor(conn) as cur:
            cur.execute(column_sql)
            column_rows = cur.fetchall()
        return self._schemas_from_rows(table_rows, column_rows)

    def _introspect_sqlite(self, conn) -> list[dict[str, Any]]:
        schemas: dict[str, dict[str, Any]] = {}
        with self._cursor(conn) as cur:
            cur.execute("PRAGMA database_list")
            databases = cur.fetchall()
        for db in databases:
            schema_name = str(db[1])
            schema = schemas.setdefault(schema_name, {"name": schema_name, "tables": []})
            with self._cursor(conn) as cur:
                cur.execute(f"SELECT name, type FROM {self._quote_identifier(schema_name, 'sqlite')}.sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name")
                table_rows = cur.fetchall()
            for table_name, table_type in table_rows:
                table = {"name": table_name, "type": table_type, "columns": []}
                with self._cursor(conn) as cur:
                    cur.execute(f"PRAGMA {self._quote_identifier(schema_name, 'sqlite')}.table_info({self._quote_identifier(table_name, 'sqlite')})")
                    for column in cur.fetchall():
                        table["columns"].append({"name": column[1], "type": column[2], "nullable": not bool(column[3])})
                schema["tables"].append(table)
        return list(schemas.values())

    def _introspect_oracle(self, conn) -> list[dict[str, Any]]:
        table_sql = """
            SELECT owner, table_name, 'BASE TABLE'
            FROM all_tables
            WHERE owner NOT IN ('SYS', 'SYSTEM')
            UNION ALL
            SELECT owner, view_name, 'VIEW'
            FROM all_views
            WHERE owner NOT IN ('SYS', 'SYSTEM')
            ORDER BY 1, 2
        """
        column_sql = """
            SELECT owner, table_name, column_name, data_type, nullable
            FROM all_tab_columns
            WHERE owner NOT IN ('SYS', 'SYSTEM')
            ORDER BY owner, table_name, column_id
        """
        with self._cursor(conn) as cur:
            cur.execute(table_sql)
            table_rows = cur.fetchall()
        with self._cursor(conn) as cur:
            cur.execute(column_sql)
            column_rows = cur.fetchall()
        return self._schemas_from_rows(table_rows, column_rows, nullable_yes_value="Y")

    def _schemas_from_rows(
        self,
        table_rows: list[Any],
        column_rows: list[Any],
        *,
        nullable_yes_value: str = "YES",
    ) -> list[dict[str, Any]]:
        schemas: dict[str, dict[str, Any]] = {}
        for schema_name, table_name, table_type in table_rows:
            schema = schemas.setdefault(str(schema_name), {"name": str(schema_name), "tables": []})
            schema["tables"].append({"name": str(table_name), "type": str(table_type).lower().replace(" ", "_"), "columns": []})

        table_lookup = {
            (schema["name"], table["name"]): table
            for schema in schemas.values()
            for table in schema["tables"]
        }
        for schema_name, table_name, column_name, data_type, is_nullable in column_rows:
            table = table_lookup.get((str(schema_name), str(table_name)))
            if table is None:
                continue
            table["columns"].append(
                {
                    "name": str(column_name),
                    "type": str(data_type),
                    "nullable": str(is_nullable).upper() == nullable_yes_value,
                }
            )
        return list(schemas.values())

    def _detect_role_permissions(
        self,
        conn,
        profile: dict[str, Any],
        schemas: list[dict[str, Any]],
    ) -> tuple[list[str] | None, dict[str, list[str]]]:
        engine = profile["engine"]
        try:
            if engine == "postgres":
                table_permissions = self._detect_postgres_table_permissions(conn, schemas)
            elif engine == "mysql":
                table_permissions = self._detect_mysql_table_permissions(conn, schemas)
            elif engine == "sqlserver":
                table_permissions = self._detect_sqlserver_table_permissions(conn, schemas)
            elif engine == "oracle":
                table_permissions = self._detect_oracle_table_permissions(conn, schemas)
            else:
                return None, {}
        except Exception:
            self._recover_connection(conn)
            return None, {}

        role_operations = sorted(
            {
                operation
                for operations in table_permissions.values()
                for operation in operations
            },
            key=self._operation_sort_key,
        )
        return role_operations, table_permissions

    def _detect_postgres_table_permissions(self, conn, schemas: list[dict[str, Any]]) -> dict[str, list[str]]:
        permissions: dict[str, list[str]] = {}
        with self._cursor(conn) as cur:
            for schema in schemas:
                schema_name = str(schema.get("name") or "")
                for table in schema.get("tables") or []:
                    table_name = str(table.get("name") or "")
                    if not schema_name or not table_name:
                        continue
                    table_ref = f"{schema_name}.{table_name}"
                    qualified = f"{self._quote_identifier(schema_name)}.{self._quote_identifier(table_name)}"
                    operations = []
                    for operation, privilege in (
                        ("select", "SELECT"),
                        ("insert", "INSERT"),
                        ("update", "UPDATE"),
                        ("delete", "DELETE"),
                    ):
                        cur.execute("SELECT has_table_privilege(%s, %s)", (qualified, privilege))
                        row = cur.fetchone()
                        if row and bool(row[0]):
                            operations.append(operation)
                    permissions[table_ref] = operations
        return permissions

    def _detect_mysql_table_permissions(self, conn, schemas: list[dict[str, Any]]) -> dict[str, list[str]]:
        permissions: dict[str, list[str]] = {}
        grantee_sql = "CONCAT(\"'\", SUBSTRING_INDEX(CURRENT_USER(), '@', 1), \"'@'\", SUBSTRING_INDEX(CURRENT_USER(), '@', -1), \"'\")"
        privilege_sql = f"""
            SELECT COUNT(*)
            FROM (
                SELECT privilege_type
                FROM information_schema.USER_PRIVILEGES
                WHERE grantee = {grantee_sql}
                UNION ALL
                SELECT privilege_type
                FROM information_schema.SCHEMA_PRIVILEGES
                WHERE grantee = {grantee_sql} AND table_schema = %s
                UNION ALL
                SELECT privilege_type
                FROM information_schema.TABLE_PRIVILEGES
                WHERE grantee = {grantee_sql} AND table_schema = %s AND table_name = %s
            ) privileges
            WHERE privilege_type IN (%s, 'ALL')
        """
        with self._cursor(conn) as cur:
            for schema in schemas:
                schema_name = str(schema.get("name") or "")
                for table in schema.get("tables") or []:
                    table_name = str(table.get("name") or "")
                    if not schema_name or not table_name:
                        continue
                    table_ref = f"{schema_name}.{table_name}"
                    operations = []
                    for operation, privilege in (
                        ("select", "SELECT"),
                        ("insert", "INSERT"),
                        ("update", "UPDATE"),
                        ("delete", "DELETE"),
                    ):
                        cur.execute(privilege_sql, (schema_name, schema_name, table_name, privilege))
                        row = cur.fetchone()
                        if row and int(row[0] or 0) > 0:
                            operations.append(operation)
                    permissions[table_ref] = operations
        return permissions

    def _detect_sqlserver_table_permissions(self, conn, schemas: list[dict[str, Any]]) -> dict[str, list[str]]:
        permissions: dict[str, list[str]] = {}
        with self._cursor(conn) as cur:
            for schema in schemas:
                schema_name = str(schema.get("name") or "")
                for table in schema.get("tables") or []:
                    table_name = str(table.get("name") or "")
                    if not schema_name or not table_name:
                        continue
                    table_ref = f"{schema_name}.{table_name}"
                    qualified = f"{self._quote_identifier(schema_name, 'sqlserver')}.{self._quote_identifier(table_name, 'sqlserver')}"
                    operations = []
                    for operation, privilege in (
                        ("select", "SELECT"),
                        ("insert", "INSERT"),
                        ("update", "UPDATE"),
                        ("delete", "DELETE"),
                    ):
                        cur.execute("SELECT HAS_PERMS_BY_NAME(?, 'OBJECT', ?)", (qualified, privilege))
                        row = cur.fetchone()
                        if row and int(row[0] or 0) == 1:
                            operations.append(operation)
                    permissions[table_ref] = operations
        return permissions

    def _detect_oracle_table_permissions(self, conn, schemas: list[dict[str, Any]]) -> dict[str, list[str]]:
        permissions: dict[str, list[str]] = {}
        with self._cursor(conn) as cur:
            cur.execute("SELECT USER FROM dual")
            row = cur.fetchone()
            current_user = str(row[0] or "").upper() if row else ""
            for schema in schemas:
                schema_name = str(schema.get("name") or "").upper()
                for table in schema.get("tables") or []:
                    table_name = str(table.get("name") or "").upper()
                    if not schema_name or not table_name:
                        continue
                    table_ref = f"{schema_name}.{table_name}"
                    if schema_name == current_user:
                        permissions[table_ref] = list(OPERATION_ORDER)
                        continue
                    operations = []
                    for operation, privilege in (
                        ("select", "SELECT"),
                        ("insert", "INSERT"),
                        ("update", "UPDATE"),
                        ("delete", "DELETE"),
                    ):
                        cur.execute(
                            """
                            SELECT COUNT(*)
                            FROM all_tab_privs
                            WHERE owner = :1
                              AND table_name = :2
                              AND privilege = :3
                              AND grantee IN (USER, 'PUBLIC')
                            """,
                            (schema_name, table_name, privilege),
                        )
                        row = cur.fetchone()
                        if row and int(row[0] or 0) > 0:
                            operations.append(operation)
                    permissions[table_ref] = operations
        return permissions

    def _schemas_with_table_permissions(
        self,
        schemas: list[dict[str, Any]],
        table_permissions: dict[str, list[str]],
        permission_mode: str,
    ) -> list[dict[str, Any]]:
        access_mode_operations = set(self._operations_for_mode(permission_mode))
        next_schemas = []
        for schema in schemas:
            schema_name = str(schema.get("name") or "")
            next_tables = []
            for table in schema.get("tables") or []:
                table_name = str(table.get("name") or "")
                table_ref = f"{schema_name}.{table_name}"
                table_ops = table_permissions.get(table_ref)
                next_table = dict(table)
                if table_ops is not None:
                    next_table["roleOperations"] = table_ops
                    next_table["effectiveOperations"] = sorted(
                        set(table_ops).intersection(access_mode_operations),
                        key=self._operation_sort_key,
                    )
                next_tables.append(next_table)
            next_schema = dict(schema)
            next_schema["tables"] = next_tables
            next_schemas.append(next_schema)
        return next_schemas

    def _effective_allowed_operations(
        self,
        permission_mode: str,
        role_allowed_operations: list[str] | None = None,
    ) -> list[str]:
        operations = set(self._operations_for_mode(permission_mode))
        if role_allowed_operations is not None:
            operations = operations.intersection(set(role_allowed_operations))
        return sorted(operations, key=self._operation_sort_key)

    def _operations_for_mode(self, permission_mode: str) -> list[str]:
        return sorted(PERMISSION_MODES.get(permission_mode or "read_only", {"select"}), key=self._operation_sort_key)

    def _operation_sort_key(self, operation: str) -> int:
        try:
            return OPERATION_ORDER.index(operation)
        except ValueError:
            return len(OPERATION_ORDER)

    def _format_operations(self, operations: list[str] | set[str]) -> str:
        ordered = sorted(set(operations), key=self._operation_sort_key)
        return ", ".join(operation.upper() for operation in ordered) or "no database operations"

    def _validate_readonly_sql(self, sql: str) -> str:
        return self._validate_sql_for_profile({"permission_mode": "read_only"}, sql)[0]

    def _validate_sql_for_profile(self, profile: dict[str, Any], sql: str) -> tuple[str, str]:
        cleaned = self._strip_sql_comments(sql).strip().rstrip(";")
        if not cleaned:
            raise RuntimeError("Database tool generated an empty SQL query.")
        statements = self._split_statements(cleaned)
        if len(statements) != 1:
            raise RuntimeError("Database tool may execute only one SQL statement at a time.")

        operation = self._sql_operation(cleaned)
        configured_operations = (
            profile.get("allowed_operations")
            if profile.get("allowed_operations") is not None
            else PERMISSION_MODES.get(profile.get("permission_mode") or "read_only", {"select"})
        )
        allowed_operations = set(configured_operations)
        if operation not in allowed_operations:
            mode_label = PERMISSION_MODE_LABELS.get(profile.get("permission_mode") or "read_only", profile.get("permission_mode") or "read_only")
            raise RuntimeError(f"Database profile is in {mode_label} mode and does not allow {operation.upper()} statements.")

        keyword_scan_sql = self._sql_without_quoted_content(cleaned)
        tokens = {token.lower() for token in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", keyword_scan_sql)}
        blocked = sorted(tokens.intersection(BLOCKED_SQL_KEYWORDS))
        if operation == "update" and "set" in blocked:
            blocked.remove("set")
        if blocked:
            raise RuntimeError(f"Database query contains blocked read/write keyword(s): {', '.join(blocked)}.")

        if operation in {"update", "delete"} and not re.search(r"\bwhere\b", cleaned, flags=re.I):
            raise RuntimeError(f"Database {operation.upper()} statements must include a WHERE clause.")
        return cleaned, operation

    def _sql_operation(self, sql: str) -> str:
        first_word = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)", sql)
        if not first_word:
            raise RuntimeError("Database tool generated invalid SQL.")
        operation = first_word.group(1).lower()
        if operation == "with":
            lowered = sql.lower()
            if re.search(r"\b(insert|update|delete|merge)\b", lowered):
                raise RuntimeError("Database CTE statements may only be used for SELECT queries.")
            return "select"
        if operation not in {"select", "insert", "update", "delete"}:
            raise RuntimeError("Database tool only allows SELECT, INSERT, UPDATE, or DELETE statements.")
        return operation

    def _validate_scope(self, profile: dict[str, Any], sql: str, *, operation: str = "select") -> None:
        allowed_schemas = set(profile.get("allowed_schemas") or [])
        if not allowed_schemas:
            raise RuntimeError("Select at least one schema before assigning this database profile to the agent.")

        allowed_tables = set(profile.get("allowed_tables") or [])
        table_refs = self._extract_table_references(sql, operation=operation)
        known_tables = self._known_allowed_table_names(profile)
        for schema_name, table_name in table_refs:
            if schema_name and schema_name not in allowed_schemas:
                raise RuntimeError(f"Database query references schema '{schema_name}', which is outside this agent's database scope.")
            if not schema_name and known_tables and table_name not in known_tables:
                raise RuntimeError(f"Database query references table '{table_name}', which is outside this agent's database scope.")
            if allowed_tables:
                candidate_refs = {f"{schema_name}.{table_name}"} if schema_name else {f"{schema}.{table_name}" for schema in allowed_schemas}
                if not candidate_refs.intersection(allowed_tables):
                    raise RuntimeError(f"Database query references table '{table_name}', which is outside this agent's table scope.")

    def _validate_role_table_permissions(self, profile: dict[str, Any], sql: str, operation: str) -> None:
        table_permissions = profile.get("table_permissions") or {}
        if not table_permissions:
            return
        table_refs = self._extract_table_references(sql, operation=operation)
        if not table_refs:
            return

        for schema_name, table_name in table_refs:
            candidate_refs = self._candidate_table_refs(profile, schema_name, table_name)
            known_refs = [ref for ref in candidate_refs if ref in table_permissions]
            if not known_refs:
                continue
            if any(operation in set(table_permissions.get(ref) or []) for ref in known_refs):
                continue
            label = known_refs[0] if len(known_refs) == 1 else table_name
            raise RuntimeError(
                f"The configured DB role does not allow {operation.upper()} on '{label}'. "
                "Choose a different access mode or update the DB role grants."
            )

    def _candidate_table_refs(
        self,
        profile: dict[str, Any],
        schema_name: str | None,
        table_name: str,
    ) -> list[str]:
        if schema_name:
            return [f"{schema_name}.{table_name}"]
        refs = []
        for schema in profile.get("allowed_schemas") or []:
            refs.append(f"{schema}.{table_name}")
        if not refs:
            refs = [
                ref
                for ref in (profile.get("table_permissions") or {}).keys()
                if ref.split(".", 1)[-1] == table_name
            ]
        return refs

    def _profile_summary(self, profile: dict[str, Any]) -> dict[str, Any]:
        allowed_schemas = profile.get("allowed_schemas") or []
        allowed_tables = set(profile.get("allowed_tables") or [])
        allowed_columns = profile.get("allowed_columns") or {}
        schemas = []
        for schema in profile.get("schema_metadata") or []:
            schema_name = schema.get("name")
            if schema_name not in allowed_schemas:
                continue
            tables = []
            for table in schema.get("tables") or []:
                full_name = f"{schema_name}.{table.get('name')}"
                if allowed_tables and full_name not in allowed_tables:
                    continue
                table_operations = (
                    table.get("effectiveOperations")
                    or table.get("effective_operations")
                    or table.get("permissions")
                    or table.get("roleOperations")
                    or []
                )
                profile_allowed_operations = (
                    profile.get("allowed_operations")
                    if profile.get("allowed_operations") is not None
                    else ["select"]
                )
                table_effective_operations = sorted(
                    set(table_operations).intersection(set(profile_allowed_operations)),
                    key=self._operation_sort_key,
                )
                tables.append(
                    {
                        "name": table.get("name"),
                        "type": table.get("type"),
                        "columns": (table.get("columns") or [])[:40],
                        "effective_operations": table_effective_operations,
                    }
                )
            schemas.append({"name": schema_name, "tables": tables[:80]})
        return {
            "index": profile["index"],
            "name": profile["name"],
            "purpose": profile.get("purpose") or "",
            "routing_profile": profile.get("routing_profile") or {},
            "engine": profile["engine"],
            "dialect": ENGINE_LABELS.get(profile["engine"], profile["engine"]),
            "permission_mode": profile.get("permission_mode") or "read_only",
            "allowed_operations": profile.get("allowed_operations") if profile.get("allowed_operations") is not None else ["select"],
            "role_permissions_detected": profile.get("role_permissions_detected"),
            "role_allowed_operations": profile.get("role_allowed_operations"),
            "allowed_schemas": allowed_schemas,
            "allowed_tables": sorted(allowed_tables),
            "allowed_columns": allowed_columns,
            "schema_metadata": schemas[:20],
            "limits": profile["limits"],
        }

    def _extract_table_references(self, sql: str, *, operation: str = "select") -> set[tuple[str | None, str]]:
        refs: set[tuple[str | None, str]] = set()
        if operation in {"insert", "update", "delete"}:
            target = self._extract_write_target(sql, operation)
            if target:
                refs.add(target)
                return refs

        tokens = re.findall(r'`[^`]+`|"[^"]+"|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*|[(),.]', sql)
        expect_relation = False
        in_from_clause = False
        index = 0
        while index < len(tokens):
            token = tokens[index]
            lower = token.lower()
            if lower in {"from", "join"}:
                expect_relation = True
                in_from_clause = True
                index += 1
                continue
            if lower in {"where", "group", "order", "having", "limit", "offset", "union", "except", "intersect", "returning"}:
                in_from_clause = False
                expect_relation = False
            if expect_relation:
                if token == "(":
                    expect_relation = False
                    index += 1
                    continue
                if self._is_identifier_token(token):
                    first = self._unquote_identifier(token)
                    if index + 2 < len(tokens) and tokens[index + 1] == ".":
                        second = self._unquote_identifier(tokens[index + 2])
                        refs.add((first, second))
                        index += 3
                    else:
                        refs.add((None, first))
                        index += 1
                    expect_relation = False
                    continue
            if in_from_clause and token == ",":
                expect_relation = True
            index += 1
        return refs

    def _extract_write_target(self, sql: str, operation: str) -> tuple[str | None, str] | None:
        if operation == "insert":
            pattern = r"\binsert\s+into\s+((?:`[^`]+`|\"[^\"]+\"|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)(?:\s*\.\s*(?:`[^`]+`|\"[^\"]+\"|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*))?)"
        elif operation == "update":
            pattern = r"\bupdate\s+((?:`[^`]+`|\"[^\"]+\"|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)(?:\s*\.\s*(?:`[^`]+`|\"[^\"]+\"|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*))?)"
        elif operation == "delete":
            pattern = r"\bdelete\s+from\s+((?:`[^`]+`|\"[^\"]+\"|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)(?:\s*\.\s*(?:`[^`]+`|\"[^\"]+\"|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*))?)"
        else:
            return None
        match = re.search(pattern, sql, flags=re.I)
        if not match:
            return None
        return self._relation_ref_from_text(match.group(1))

    def _relation_ref_from_text(self, text: str) -> tuple[str | None, str]:
        parts = [self._unquote_identifier(part.strip()) for part in re.split(r"\s*\.\s*", text.strip()) if part.strip()]
        if len(parts) >= 2:
            return parts[-2], parts[-1]
        return None, parts[0]

    def _known_allowed_table_names(self, profile: dict[str, Any]) -> set[str]:
        names: set[str] = set()
        allowed_schemas = set(profile.get("allowed_schemas") or [])
        allowed_tables = set(profile.get("allowed_tables") or [])
        for schema in profile.get("schema_metadata") or []:
            schema_name = schema.get("name")
            if schema_name not in allowed_schemas:
                continue
            for table in schema.get("tables") or []:
                table_name = table.get("name")
                if not table_name:
                    continue
                if allowed_tables and f"{schema_name}.{table_name}" not in allowed_tables:
                    continue
                names.add(str(table_name))
        return names

    def _validate_column_scope(self, profile: dict[str, Any], sql: str, operation: str) -> None:
        if operation not in {"insert", "update"}:
            return
        allowed_columns = profile.get("allowed_columns") or {}
        if not allowed_columns:
            return
        target = self._extract_write_target(sql, operation)
        if not target:
            raise RuntimeError(f"Could not determine target table for {operation.upper()} statement.")
        schema_name, table_name = target
        candidate_refs = [f"{schema_name}.{table_name}"] if schema_name else [
            f"{schema}.{table_name}" for schema in profile.get("allowed_schemas") or []
        ]
        configured_columns: set[str] = set()
        for ref in candidate_refs:
            configured_columns.update(str(column) for column in allowed_columns.get(ref, []))
        if not configured_columns:
            return

        write_columns = self._extract_write_columns(sql, operation)
        disallowed = sorted(column for column in write_columns if column not in configured_columns)
        if disallowed:
            raise RuntimeError(
                f"Database {operation.upper()} references column(s) outside this agent's write scope: {', '.join(disallowed)}."
            )

    def _extract_write_columns(self, sql: str, operation: str) -> set[str]:
        if operation == "insert":
            match = re.search(
                r"\binsert\s+into\s+(?:`[^`]+`|\"[^\"]+\"|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)(?:\s*\.\s*(?:`[^`]+`|\"[^\"]+\"|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*))?\s*\(([^)]+)\)",
                sql,
                flags=re.I | re.S,
            )
            if not match:
                raise RuntimeError("Database INSERT statements must include an explicit column list.")
            return {
                self._unquote_identifier(column.strip())
                for column in match.group(1).split(",")
                if column.strip()
            }
        if operation == "update":
            match = re.search(r"\bset\s+(.+?)\bwhere\b", sql, flags=re.I | re.S)
            if not match:
                raise RuntimeError("Database UPDATE statements must include SET assignments and a WHERE clause.")
            columns: set[str] = set()
            for assignment in self._split_assignments(match.group(1)):
                left = assignment.split("=", 1)[0].strip()
                if "." in left:
                    left = left.split(".")[-1]
                columns.add(self._unquote_identifier(left))
            return columns
        return set()

    def _estimate_insert_rows(self, sql: str) -> int | None:
        match = re.search(r"\bvalues\b(.+)$", sql, flags=re.I | re.S)
        if not match:
            return None
        values_text = re.split(r"\breturning\b", match.group(1), maxsplit=1, flags=re.I)[0]
        count = 0
        depth = 0
        in_single = False
        in_double = False
        for char in values_text:
            if char == "'" and not in_double:
                in_single = not in_single
            elif char == '"' and not in_single:
                in_double = not in_double
            elif char == "(" and not in_single and not in_double:
                if depth == 0:
                    count += 1
                depth += 1
            elif char == ")" and not in_single and not in_double and depth:
                depth -= 1
        return count or None

    def _count_sql_for_write(self, sql: str, operation: str) -> str | None:
        if operation == "update":
            match = re.search(
                r"\bupdate\s+(.+?)\s+set\s+.+?\s+where\s+(.+)$",
                sql,
                flags=re.I | re.S,
            )
        elif operation == "delete":
            match = re.search(
                r"\bdelete\s+from\s+(.+?)\s+where\s+(.+)$",
                sql,
                flags=re.I | re.S,
            )
        else:
            return None
        if not match:
            return None
        relation = match.group(1).strip()
        where_clause = self._trim_write_where_clause(match.group(2).strip())
        if not relation or not where_clause:
            return None
        return f"SELECT COUNT(*) FROM {relation} WHERE {where_clause}"

    def _trim_write_where_clause(self, where_clause: str) -> str:
        # Keep preview counting conservative by removing common post-filter clauses.
        return re.split(
            r"\b(returning|order\s+by|limit|offset)\b",
            where_clause,
            maxsplit=1,
            flags=re.I | re.S,
        )[0].strip()

    def _split_assignments(self, text: str) -> list[str]:
        assignments: list[str] = []
        current: list[str] = []
        depth = 0
        in_single = False
        in_double = False
        for char in text:
            if char == "'" and not in_double:
                in_single = not in_single
            elif char == '"' and not in_single:
                in_double = not in_double
            elif char == "(" and not in_single and not in_double:
                depth += 1
            elif char == ")" and not in_single and not in_double and depth:
                depth -= 1
            if char == "," and not in_single and not in_double and depth == 0:
                assignment = "".join(current).strip()
                if assignment:
                    assignments.append(assignment)
                current = []
            else:
                current.append(char)
        tail = "".join(current).strip()
        if tail:
            assignments.append(tail)
        return assignments

    def _normalize_schema_metadata(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        schemas = []
        for schema in value:
            if not isinstance(schema, dict):
                continue
            name = str(schema.get("name") or "").strip()
            if not name:
                continue
            tables = []
            for table in schema.get("tables") or []:
                if not isinstance(table, dict):
                    continue
                table_name = str(table.get("name") or "").strip()
                if not table_name:
                    continue
                tables.append(
                    {
                        "name": table_name,
                        "type": str(table.get("type") or "table"),
                        "roleOperations": self._normalize_operation_list(
                            table.get("roleOperations")
                            or table.get("role_operations")
                            or table.get("permissions")
                        ),
                        "effectiveOperations": self._normalize_operation_list(
                            table.get("effectiveOperations")
                            or table.get("effective_operations")
                        ),
                        "columns": [
                            column
                            for column in table.get("columns") or []
                            if isinstance(column, dict) and str(column.get("name") or "").strip()
                        ],
                    }
                )
            schemas.append({"name": name, "tables": tables})
        return schemas

    def _normalize_allowed_tables(self, value: Any) -> list[str]:
        refs: list[str] = []
        if isinstance(value, dict):
            for schema_name, table_names in value.items():
                for table_name in self._list_from_value(table_names):
                    refs.append(f"{schema_name}.{table_name}")
        else:
            refs = self._list_from_value(value)
        return sorted({ref for ref in refs if "." in ref})

    def _normalize_allowed_columns(self, value: Any) -> dict[str, list[str]]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, list[str]] = {}
        for table_ref, columns in value.items():
            ref = str(table_ref or "").strip()
            if "." not in ref:
                continue
            normalized[ref] = self._list_from_value(columns)
        return normalized

    def _normalize_operation_list(self, value: Any) -> list[str]:
        operations = []
        for item in self._list_from_value(value):
            operation = str(item or "").strip().lower()
            if operation in PERMISSION_MODES["read_write"]:
                operations.append(operation)
        return sorted(set(operations), key=self._operation_sort_key)

    def _normalize_table_permissions(self, value: Any) -> dict[str, list[str]]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, list[str]] = {}
        for table_ref, operations in value.items():
            ref = str(table_ref or "").strip()
            if "." not in ref:
                continue
            normalized[ref] = self._normalize_operation_list(operations)
        return normalized

    def _table_permissions_from_schema_metadata(self, schemas: list[dict[str, Any]]) -> dict[str, list[str]]:
        permissions: dict[str, list[str]] = {}
        for schema in schemas:
            schema_name = str(schema.get("name") or "")
            if not schema_name:
                continue
            for table in schema.get("tables") or []:
                table_name = str(table.get("name") or "")
                if not table_name:
                    continue
                operations = self._normalize_operation_list(
                    table.get("roleOperations")
                    or table.get("role_operations")
                    or table.get("permissions")
                )
                if operations:
                    permissions[f"{schema_name}.{table_name}"] = operations
        return permissions

    def _normalize_permission_mode(self, value: Any) -> str:
        normalized = re.sub(r"[^a-z0-9]+", "_", str(value or "read_only").strip().lower()).strip("_")
        aliases = {
            "readonly": "read_only",
            "read": "read_only",
            "select": "read_only",
            "append": "append_only",
            "insert": "append_only",
            "insert_only": "append_only",
            "read_insert": "append_only",
            "update": "read_update",
            "read_and_update": "read_update",
            "insert_update": "read_insert_update",
            "read_insert_update": "read_insert_update",
            "read_update_insert": "read_insert_update",
            "write": "read_write",
            "read_write_dml": "read_write",
            "full_dml": "read_write",
        }
        return aliases.get(normalized, normalized if normalized in PERMISSION_MODES else "read_only")

    def _list_from_value(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item or "").strip()]
        if isinstance(value, (set, tuple)):
            return [str(item).strip() for item in value if str(item or "").strip()]
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return []

    def _int_limit(self, value: Any, fallback: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = fallback
        return max(minimum, min(maximum, parsed))

    def _default_port(self, engine: str) -> int | None:
        return {
            "postgres": 5432,
            "mysql": 3306,
            "sqlserver": 1433,
            "oracle": 1521,
        }.get(engine)

    def _require_direct_fields(self, profile: dict[str, Any], *, require_username: bool) -> None:
        if not profile.get("host") or not profile.get("database") or (require_username and not profile.get("username")):
            raise RuntimeError("Database host, database name, and DB role username are required.")

    def _mysql_kwargs_from_uri(self, uri: str) -> dict[str, Any]:
        from urllib.parse import unquote, urlparse

        parsed = urlparse(uri)
        if parsed.scheme not in {"mysql", "mariadb", "mysql+pymysql"}:
            raise RuntimeError("MySQL connection URI must start with mysql:// or mariadb://.")
        return {
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 3306,
            "user": unquote(parsed.username or ""),
            "password": unquote(parsed.password or ""),
            "database": parsed.path.lstrip("/"),
        }

    @contextmanager
    def _cursor(self, conn) -> Iterator[Any]:
        cur = conn.cursor()
        try:
            yield cur
        finally:
            try:
                cur.close()
            except Exception:
                pass

    def _column_names(self, cur) -> list[str]:
        columns = []
        for item in cur.description or []:
            columns.append(str(getattr(item, "name", None) or item[0]))
        return columns

    def _recover_connection(self, conn) -> None:
        try:
            conn.rollback()
        except Exception:
            pass

    def _commit(self, conn) -> None:
        try:
            conn.commit()
        except Exception:
            pass

    def _rollback(self, conn) -> None:
        try:
            conn.rollback()
        except Exception:
            pass

    def _quote_identifier(self, value: str, engine: str = "postgres") -> str:
        if engine == "mysql":
            return "`" + value.replace("`", "``") + "`"
        if engine == "sqlserver":
            return "[" + value.replace("]", "]]") + "]"
        return '"' + value.replace('"', '""') + '"'

    def _is_identifier_token(self, value: str) -> bool:
        return bool(re.match(r'^`[^`]+`$|^"[^"]+"$|^\[[^\]]+\]$|^[A-Za-z_][A-Za-z0-9_]*$', value))

    def _unquote_identifier(self, value: str) -> str:
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            return value[1:-1].replace('""', '"')
        if value.startswith("`") and value.endswith("`"):
            return value[1:-1].replace("``", "`")
        if value.startswith("[") and value.endswith("]"):
            return value[1:-1].replace("]]", "]")
        return value

    def _strip_sql_comments(self, sql: str) -> str:
        sql = re.sub(r"--[^\n\r]*", "", sql)
        sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.S)
        return sql

    def _sql_without_quoted_content(self, sql: str) -> str:
        output: list[str] = []
        in_single = False
        in_double = False
        in_backtick = False
        in_bracket = False
        index = 0
        while index < len(sql):
            char = sql[index]
            next_char = sql[index + 1] if index + 1 < len(sql) else ""

            if in_single:
                output.append(" ")
                if char == "\\" and next_char:
                    output.append(" ")
                    index += 2
                    continue
                if char == "'" and next_char == "'":
                    output.append(" ")
                    index += 2
                    continue
                if char == "'":
                    in_single = False
                index += 1
                continue

            if in_double:
                output.append(" ")
                if char == '"' and next_char == '"':
                    output.append(" ")
                    index += 2
                    continue
                if char == '"':
                    in_double = False
                index += 1
                continue

            if in_backtick:
                output.append(" ")
                if char == "`" and next_char == "`":
                    output.append(" ")
                    index += 2
                    continue
                if char == "`":
                    in_backtick = False
                index += 1
                continue

            if in_bracket:
                output.append(" ")
                if char == "]" and next_char == "]":
                    output.append(" ")
                    index += 2
                    continue
                if char == "]":
                    in_bracket = False
                index += 1
                continue

            if char == "'":
                in_single = True
                output.append(" ")
            elif char == '"':
                in_double = True
                output.append(" ")
            elif char == "`":
                in_backtick = True
                output.append(" ")
            elif char == "[":
                in_bracket = True
                output.append(" ")
            else:
                output.append(char)
            index += 1
        return "".join(output)

    def _split_statements(self, sql: str) -> list[str]:
        statements: list[str] = []
        current: list[str] = []
        in_single = False
        in_double = False
        index = 0
        while index < len(sql):
            char = sql[index]
            if char == "'" and not in_double:
                in_single = not in_single
            elif char == '"' and not in_single:
                in_double = not in_double
            if char == ";" and not in_single and not in_double:
                statement = "".join(current).strip()
                if statement:
                    statements.append(statement)
                current = []
            else:
                current.append(char)
            index += 1
        tail = "".join(current).strip()
        if tail:
            statements.append(tail)
        return statements

    def _decrypt_secret(self, value: str | None) -> str | None:
        if not value:
            return None
        text = str(value)
        if not text.startswith("fernet:"):
            return text
        try:
            from cryptography.fernet import Fernet

            secret = os.getenv("AUTH_SECRET_KEY") or os.getenv("SECRET_KEY") or "dev-secret-change-me"
            key = hashlib.sha256(secret.encode()).digest()
            return Fernet(base64.urlsafe_b64encode(key)).decrypt(text.removeprefix("fernet:").encode()).decode()
        except Exception:
            return text


database_tool_service = DatabaseToolService()
