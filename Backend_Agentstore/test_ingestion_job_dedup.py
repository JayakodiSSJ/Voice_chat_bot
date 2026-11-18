import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, request

import auth_routes


def _skip_auth(_app):
    return lambda fn: fn


class FakeDB:
    """Just enough of the DB layer for the ingestion-job routes."""

    def __init__(self, active_job_id=None):
        self.active_job_id = active_job_id
        self.inserted_jobs = 0
        self.active_job_params = None

    def fetch_one(self, _conn, sql, params=()):
        if "FROM custom_agents" in sql:
            return {"id": 1, "name": "Test agent", "configuration": "{}", "created_at": "now"}
        if "FROM kb_sources" in sql:
            return {"id": 2}
        if "FROM kb_ingestion_jobs" in sql:
            self.active_job_params = params
            return {"id": self.active_job_id} if self.active_job_id else None
        raise AssertionError(f"Unexpected query: {sql}")

    @contextmanager
    def get_conn(self):
        db = self

        class Conn:
            def execute(self, sql, _params=()):
                if "INSERT INTO kb_ingestion_jobs" in sql:
                    db.inserted_jobs += 1
                    return SimpleNamespace(lastrowid=100 + db.inserted_jobs)
                return SimpleNamespace(lastrowid=None)

            def commit(self):
                pass

        yield Conn()


class IngestionJobDedupTests(unittest.TestCase):
    def setUp(self):
        for active_patch in (
            patch.object(auth_routes, "require_auth", side_effect=_skip_auth),
            patch.object(auth_routes, "init_db"),
            patch.object(auth_routes, "_mark_stale_running_ingestions"),
        ):
            active_patch.start()
            self.addCleanup(active_patch.stop)

        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        auth_routes.register_auth_routes(self.app, mail=None)

        @self.app.before_request
        def _fake_auth():
            request.auth = {"role": "admin", "company_id": 2}

        self.client = self.app.test_client()

    def _ingest(self, db):
        with (
            patch.object(auth_routes, "get_conn", db.get_conn),
            patch.object(auth_routes, "fetch_one", db.fetch_one),
            patch("threading.Thread") as thread_cls,
        ):
            response = self.client.post(
                "/api/auth/ingest-url",
                json={"agent_id": 1, "url": "https://example.com", "crawl": {"maxPages": 5}},
            )
        return response, thread_cls

    def test_new_source_starts_a_background_crawl(self):
        db = FakeDB(active_job_id=None)
        response, thread_cls = self._ingest(db)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "started")
        self.assertEqual(response.get_json()["job_id"], 101)
        self.assertEqual(db.inserted_jobs, 1)
        thread_cls.return_value.start.assert_called_once()
        args = thread_cls.call_args.kwargs["args"]
        self.assertEqual(args[5:9], ("website", "https://example.com", None, {"max_pages": 5}))

    def test_running_crawl_is_reused_instead_of_started_again(self):
        db = FakeDB(active_job_id=42)
        response, thread_cls = self._ingest(db)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "already_running")
        self.assertEqual(response.get_json()["job_id"], 42)
        self.assertEqual(db.inserted_jobs, 0)
        thread_cls.assert_not_called()

    def test_only_recent_jobs_count_as_running(self):
        db = FakeDB(active_job_id=None)
        self._ingest(db)

        company_id, source_id, active_since = db.active_job_params
        self.assertEqual((company_id, source_id), (2, 2))
        age = datetime.now(timezone.utc) - datetime.fromisoformat(active_since)
        self.assertAlmostEqual(age.total_seconds(), timedelta(minutes=30).total_seconds(), delta=60)


if __name__ == "__main__":
    unittest.main()
