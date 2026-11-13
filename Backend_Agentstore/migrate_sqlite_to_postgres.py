from __future__ import annotations

import argparse
import os
from pathlib import Path
import sqlite3
from typing import Iterable


TABLE_ORDER = [
    "company",
    "users",
    "login_requests",
    "company_registration_requests",
    "one_time_tokens",
    "user_invitations",
    "contact_messages",
    "agent_resources",
    "agent_creation_requests",
    "agent_creation_responses",
    "custom_agents",
    "companies",
    "agents",
    "kb_details",
    "apis",
    "kb_sources",
    "kb_ingestion_jobs",
    "custom_agent_chat_sessions",
    "custom_agent_chat_messages",
    "pending_payments",
    "subscriptions",
    "company_chat_usage",
    "user_chat_quota_overrides",
    "user_chat_usage",
    "payments",
    "subscription_packages_config",
]


def default_sqlite_path() -> Path:
    return Path(__file__).resolve().parent / "agentstore.sqlite3"


def sqlite_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
        """
    ).fetchall()
    return [row["name"] for row in rows]


def sqlite_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({quote_ident(table)})").fetchall()]


def ordered_tables(tables: Iterable[str]) -> list[str]:
    available = set(tables)
    ordered = [table for table in TABLE_ORDER if table in available]
    ordered.extend(sorted(available - set(ordered)))
    return ordered


def pg_tables(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """
        )
        return {row[0] for row in cur.fetchall()}


def pg_columns(conn, table: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        return [row[0] for row in cur.fetchall()]


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def reset_sequence(conn, table: str, columns: Iterable[str]) -> None:
    target_columns = set(columns)
    if not target_columns:
        return

    with conn.cursor() as cur:
        table_name = quote_ident(table)
        cur.execute(
            """
            SELECT column_name, pg_get_serial_sequence(format('%%I.%%I', table_schema, table_name), column_name)
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = %s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        serial_columns = [
            (column, sequence)
            for column, sequence in cur.fetchall()
            if column in target_columns and sequence
        ]

        for column, sequence in serial_columns:
            column_name = quote_ident(column)
            cur.execute(f"SELECT MAX({column_name}) FROM {table_name}")
            row = cur.fetchone()
            max_value = row[0] if row else None
            if max_value is not None:
                cur.execute("SELECT setval(%s, %s, true)", (sequence, max_value))


def copy_table(sqlite_conn: sqlite3.Connection, pg_conn, table: str) -> int:
    source_columns = sqlite_columns(sqlite_conn, table)
    target_columns = pg_columns(pg_conn, table)
    columns = [column for column in source_columns if column in target_columns]
    if not columns:
        return 0

    rows = sqlite_conn.execute(
        f"SELECT {', '.join(quote_ident(column) for column in columns)} FROM {quote_ident(table)}"
    ).fetchall()
    if not rows:
        return 0

    placeholders = ", ".join(["%s"] * len(columns))
    column_sql = ", ".join(quote_ident(column) for column in columns)
    insert_sql = f"INSERT INTO {quote_ident(table)} ({column_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    with pg_conn.cursor() as cur:
        cur.executemany(insert_sql, [tuple(row[column] for column in columns) for row in rows])
    reset_sequence(pg_conn, table, target_columns)
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Copy AgentStore data from SQLite to PostgreSQL.")
    parser.add_argument("--sqlite", default=str(default_sqlite_path()), help="Path to agentstore.sqlite3")
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL", ""), help="PostgreSQL DATABASE_URL")
    parser.add_argument("--replace", action="store_true", help="Truncate PostgreSQL tables before copying")
    args = parser.parse_args()

    if not args.database_url:
        raise SystemExit("DATABASE_URL or --database-url is required.")

    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite database not found: {sqlite_path}")

    os.environ["DATABASE_URL"] = args.database_url

    import psycopg
    from auth_sqlite import init_db

    init_db()

    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row
    with sqlite_conn, psycopg.connect(args.database_url) as pg_conn:
        source_tables = ordered_tables(sqlite_tables(sqlite_conn))
        target_tables = pg_tables(pg_conn)
        tables = [table for table in source_tables if table in target_tables]

        if args.replace and tables:
            table_sql = ", ".join(quote_ident(table) for table in reversed(tables))
            with pg_conn.cursor() as cur:
                cur.execute(f"TRUNCATE {table_sql} RESTART IDENTITY CASCADE")

        for table in tables:
            copied = copy_table(sqlite_conn, pg_conn, table)
            if copied:
                print(f"{table}: copied {copied} row(s)")

    # Re-run idempotent schema/data backfills after copied rows are committed.
    init_db()


if __name__ == "__main__":
    main()
