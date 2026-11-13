from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
import os
import re
import sqlite3
import time
from typing import Any


ID_TABLES = {
    "agent_creation_requests",
    "agent_creation_responses",
    "agent_resources",
    "apis",
    "company",
    "company_registration_requests",
    "company_feature_subscriptions",
    "company_feature_usage_events",
    "company_feature_usage_periods",
    "contact_messages",
    "custom_agent_chat_messages",
    "custom_agent_chat_sessions",
    "custom_agents",
    "database_tool_audit_logs",
    "kb_details",
    "kb_ingestion_jobs",
    "kb_sources",
    "login_requests",
    "one_time_tokens",
    "payments",
    "subscriptions",
    "subscription_packages_config",
    "chat_token_usage_events",
    "user_invitations",
    "users",
    "whatsapp_message_dispatches",
    "whatsapp_outbound_messages",
}


class DbRow(Mapping[str, Any]):
    def __init__(self, columns: Sequence[str], values: Sequence[Any]):
        self._columns = list(columns)
        self._values = list(values)
        self._index = {name: index for index, name in enumerate(self._columns)}

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._index[key]]

    def __iter__(self) -> Iterator[str]:
        return iter(self._columns)

    def __len__(self) -> int:
        return len(self._columns)

    def keys(self):
        return self._index.keys()


class StaticCursor:
    def __init__(self, columns: Sequence[str], rows: Sequence[Sequence[Any]]):
        self._columns = list(columns)
        self._rows = [DbRow(columns, row) for row in rows]
        self.rowcount = len(self._rows)
        self.lastrowid = None

    def fetchone(self):
        if not self._rows:
            return None
        return self._rows.pop(0)

    def fetchall(self):
        rows = list(self._rows)
        self._rows.clear()
        return rows

    def close(self) -> None:
        self._rows.clear()


class PostgresCursor:
    def __init__(self, cursor, *, lastrowid: int | None = None):
        self._cursor = cursor
        self._columns = [item.name for item in (cursor.description or [])]
        self.lastrowid = lastrowid

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return DbRow(self._columns, row)

    def fetchall(self):
        return [DbRow(self._columns, row) for row in self._cursor.fetchall()]

    def close(self) -> None:
        self._cursor.close()


class PostgresCompatConnection:
    def __init__(self, database_url: str):
        import psycopg
        from psycopg.rows import tuple_row

        self._psycopg = psycopg
        attempts = int(os.getenv("DATABASE_CONNECT_RETRIES", "8"))
        delay = float(os.getenv("DATABASE_CONNECT_RETRY_DELAY", "1.5"))
        last_error = None
        for attempt in range(max(1, attempts)):
            try:
                self._conn = psycopg.connect(database_url, row_factory=tuple_row)
                break
            except psycopg.OperationalError as exc:
                last_error = exc
                if attempt == attempts - 1:
                    raise
                time.sleep(delay)
        else:
            raise last_error
        self._last_insert_id: int | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type:
            self.rollback()
        else:
            self.commit()
        self.close()

    def execute(self, sql: str, params: Sequence[Any] = ()):
        handled = self._handle_builtin(sql)
        if handled is not None:
            return handled

        translated = _translate_sql(sql)
        translated, wants_id = _add_returning_id(translated)
        cursor = self._conn.cursor()
        try:
            cursor.execute(translated, tuple(params or ()))
            lastrowid = None
            if wants_id:
                row = cursor.fetchone()
                if row:
                    lastrowid = int(row[0])
                    self._last_insert_id = lastrowid
            return PostgresCursor(cursor, lastrowid=lastrowid)
        except self._psycopg.IntegrityError as exc:
            cursor.close()
            raise sqlite3.IntegrityError(str(exc)) from exc
        except Exception:
            cursor.close()
            raise

    def executescript(self, script: str) -> None:
        for statement in _split_sql_script(script):
            if statement.strip():
                cursor = self.execute(statement)
                cursor.close()

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def close(self) -> None:
        self._conn.close()

    def _handle_builtin(self, sql: str):
        normalized = " ".join(sql.strip().split()).lower()
        if normalized.startswith("pragma "):
            return self._handle_pragma(normalized)
        if normalized == "select last_insert_rowid()":
            return StaticCursor(["last_insert_rowid"], [[self._last_insert_id]])
        if "from sqlite_master" in normalized:
            return self._handle_sqlite_master(sql)
        return None

    def _handle_pragma(self, normalized: str):
        match = re.match(r"pragma\s+table_info\(([^)]+)\)", normalized)
        if not match:
            return StaticCursor([], [])
        table = match.group(1).strip().strip('"')
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        rows = [
            (index, name, data_type, 0 if nullable == "YES" else 1, default, 0)
            for index, (name, data_type, nullable, default) in enumerate(cursor.fetchall())
        ]
        cursor.close()
        return StaticCursor(["cid", "name", "type", "notnull", "dflt_value", "pk"], rows)

    def _handle_sqlite_master(self, sql: str):
        match = re.search(r"name\s*=\s*'([^']+)'", sql, flags=re.I)
        table = match.group(1) if match else ""
        if not table:
            return StaticCursor(["sql"], [])
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = %s
            )
            """,
            (table,),
        )
        exists = bool(cursor.fetchone()[0])
        cursor.close()
        if not exists:
            return StaticCursor(["sql"], [])
        return StaticCursor(["sql"], [[f"CREATE TABLE {table} (... sharepoint ...)"]])


def get_database_url() -> str:
    return (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or "").strip()


def connect_postgres() -> PostgresCompatConnection:
    database_url = get_database_url()
    if not database_url:
        raise RuntimeError("DATABASE_URL or POSTGRES_URL is required. SQLite fallback has been removed.")
    return PostgresCompatConnection(database_url)


def _translate_sql(sql: str) -> str:
    translated = sql.strip().rstrip(";")
    if re.fullmatch(r"BEGIN\s+(DEFERRED|IMMEDIATE|EXCLUSIVE)", translated, flags=re.I):
        return "BEGIN"
    translated = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "BIGSERIAL PRIMARY KEY",
        translated,
        flags=re.I,
    )
    translated = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", translated, flags=re.I)
    translated = re.sub(r"\bdatetime\('now'\)", "CURRENT_TIMESTAMP", translated, flags=re.I)
    translated = re.sub(r"\s+COLLATE\s+NOCASE\b", "", translated, flags=re.I)
    translated = _replace_placeholders(translated)
    if _was_insert_or_ignore(sql) and " on conflict " not in translated.lower():
        translated = f"{translated} ON CONFLICT DO NOTHING"
    return translated


def _was_insert_or_ignore(sql: str) -> bool:
    return bool(re.match(r"\s*INSERT\s+OR\s+IGNORE\s+INTO\b", sql, flags=re.I))


def _add_returning_id(sql: str) -> tuple[str, bool]:
    if re.search(r"\bRETURNING\b", sql, flags=re.I):
        return sql, False
    match = re.match(r"\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)\b", sql, flags=re.I)
    if not match:
        return sql, False
    table = match.group(1).lower()
    if table not in ID_TABLES or " values " not in f" {sql.lower()} ":
        return sql, False
    return f"{sql} RETURNING id", True


def _replace_placeholders(sql: str) -> str:
    output: list[str] = []
    in_single = False
    in_double = False
    index = 0
    while index < len(sql):
        char = sql[index]
        if char == "'" and not in_double:
            in_single = not in_single
            output.append(char)
        elif char == '"' and not in_single:
            in_double = not in_double
            output.append(char)
        elif char == "?" and not in_single and not in_double:
            output.append("%s")
        else:
            output.append(char)
        index += 1
    return "".join(output)


def _split_sql_script(script: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    index = 0
    while index < len(script):
        char = script[index]
        next_char = script[index + 1] if index + 1 < len(script) else ""
        if char == "-" and next_char == "-" and not in_single and not in_double:
            while index < len(script) and script[index] != "\n":
                current.append(script[index])
                index += 1
            continue
        if char == "'" and not in_double:
            in_single = not in_single
        elif char == '"' and not in_single:
            in_double = not in_double
        if char == ";" and not in_single and not in_double:
            statements.append("".join(current))
            current = []
        else:
            current.append(char)
        index += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements
