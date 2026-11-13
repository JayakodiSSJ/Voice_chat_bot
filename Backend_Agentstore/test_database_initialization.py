import unittest
from unittest.mock import patch

from auth_sqlite import (
    DATABASE_INIT_ADVISORY_LOCK,
    _acquire_database_init_lock,
    init_db,
)


class RecordingCursor:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class RecordingConnection:
    def __init__(self):
        self.calls = []
        self.cursor = RecordingCursor()

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return self.cursor


class InitOrderProbeConnection(RecordingConnection):
    def __init__(self):
        super().__init__()
        self.events = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=()):
        self.events.append("execute")
        return super().execute(sql, params)

    def executescript(self, script):
        self.events.append("executescript")
        raise RuntimeError("stop after observing initialization order")


class DatabaseInitializationTests(unittest.TestCase):
    def test_acquires_transaction_scoped_postgres_advisory_lock(self):
        conn = RecordingConnection()

        _acquire_database_init_lock(conn)

        self.assertEqual(
            conn.calls,
            [
                (
                    "SELECT pg_advisory_xact_lock(?, ?)",
                    DATABASE_INIT_ADVISORY_LOCK,
                )
            ],
        )
        self.assertTrue(conn.cursor.closed)

    def test_init_acquires_lock_before_running_schema_statements(self):
        conn = InitOrderProbeConnection()

        with patch("auth_sqlite.get_conn", return_value=conn):
            with self.assertRaisesRegex(RuntimeError, "initialization order"):
                init_db()

        self.assertEqual(conn.events, ["execute", "executescript"])
        self.assertEqual(
            conn.calls[0],
            (
                "SELECT pg_advisory_xact_lock(?, ?)",
                DATABASE_INIT_ADVISORY_LOCK,
            ),
        )


if __name__ == "__main__":
    unittest.main()
