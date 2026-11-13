import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from database_tool_service import DatabaseToolService
from database_tool_audit_service import DatabaseToolAuditService


class DatabaseToolServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = DatabaseToolService()
        self.audit_service = DatabaseToolAuditService()

    def test_allows_single_select_statement(self):
        sql = self.service._validate_readonly_sql("SELECT * FROM public.users LIMIT 1;")

        self.assertEqual(sql, "SELECT * FROM public.users LIMIT 1")

    def test_blocks_write_statement(self):
        with self.assertRaisesRegex(RuntimeError, "does not allow DELETE"):
            self.service._validate_readonly_sql("DELETE FROM public.users")

    def test_requires_selected_schema_scope(self):
        profile = {"allowed_schemas": [], "allowed_tables": [], "schema_metadata": []}

        with self.assertRaisesRegex(RuntimeError, "Select at least one schema"):
            self.service._validate_scope(profile, "SELECT 1")

    def test_blocks_schema_outside_scope(self):
        profile = {
            "allowed_schemas": ["analytics"],
            "allowed_tables": [],
            "schema_metadata": [],
        }

        with self.assertRaisesRegex(RuntimeError, "outside this agent's database scope"):
            self.service._validate_scope(profile, "SELECT * FROM public.users")

    def test_select_list_commas_do_not_look_like_tables(self):
        profile = {
            "allowed_schemas": ["analytics"],
            "allowed_tables": [],
            "schema_metadata": [
                {"name": "analytics", "tables": [{"name": "orders", "columns": []}]}
            ],
        }

        self.service._validate_scope(profile, "SELECT id, total FROM analytics.orders")

    def test_blocks_comma_join_outside_scope(self):
        profile = {
            "allowed_schemas": ["analytics"],
            "allowed_tables": [],
            "schema_metadata": [],
        }

        with self.assertRaisesRegex(RuntimeError, "outside this agent's database scope"):
            self.service._validate_scope(profile, "SELECT * FROM analytics.orders, public.users")

    def test_normalizes_supported_engine_aliases(self):
        self.assertEqual(self.service._normalize_profile({"engine": "mssql", "database": "app"})["engine"], "sqlserver")
        self.assertEqual(self.service._normalize_profile({"engine": "mariadb", "database": "app"})["engine"], "mysql")
        self.assertEqual(self.service._normalize_profile({"engine": "sqlite3", "database": "app.db"})["engine"], "sqlite")

    def test_extracts_bracket_and_backtick_qualified_tables(self):
        refs = self.service._extract_table_references("SELECT * FROM [dbo].[Users] JOIN `analytics`.`orders` ON 1 = 1")

        self.assertIn(("dbo", "Users"), refs)
        self.assertIn(("analytics", "orders"), refs)

    def test_readonly_session_recovers_after_failed_hint(self):
        class FailingCursor:
            description = []

            def execute(self, statement, params=()):
                if "statement_timeout" in statement:
                    raise RuntimeError("syntax error")

            def close(self):
                pass

        class FakeConnection:
            def __init__(self):
                self.rollback_count = 0

            def cursor(self):
                return FailingCursor()

            def rollback(self):
                self.rollback_count += 1

        conn = FakeConnection()

        self.service._configure_readonly_session(
            conn,
            {"engine": "postgres", "limits": {"timeout_seconds": 15}},
        )

        self.assertEqual(conn.rollback_count, 1)

    def test_append_only_allows_insert_with_explicit_columns(self):
        profile = self.service._normalize_profile({
            "engine": "postgres",
            "permissionMode": "append_only",
            "selectedSchemas": ["public"],
            "selectedTables": ["public.audit_log"],
            "selectedColumns": {"public.audit_log": ["event_type", "message"]},
        })

        sql, operation = self.service._validate_sql_for_profile(
            profile,
            "INSERT INTO public.audit_log (event_type, message) VALUES ('test', 'ok')",
        )
        self.service._validate_scope(profile, sql, operation=operation)
        self.service._validate_column_scope(profile, sql, operation)

        self.assertEqual(operation, "insert")

    def test_blocked_keyword_scanner_ignores_quoted_values(self):
        profile = self.service._normalize_profile({
            "engine": "mysql",
            "permissionMode": "append_only",
            "selectedSchemas": ["agent_tool_test"],
            "selectedTables": ["agent_tool_test.customers"],
        })

        sql, operation = self.service._validate_sql_for_profile(
            profile,
            (
                "INSERT INTO agent_tool_test.customers (name, email, status) "
                "VALUES ('Grant Brody', 'grant@gmail.com', 'active')"
            ),
        )

        self.assertEqual(operation, "insert")
        self.assertIn("grant@gmail.com", sql)

    def test_append_only_blocks_update(self):
        profile = self.service._normalize_profile({"engine": "postgres", "permissionMode": "append_only"})

        with self.assertRaisesRegex(RuntimeError, "does not allow UPDATE"):
            self.service._validate_sql_for_profile(profile, "UPDATE public.users SET status = 'active' WHERE id = 1")

    def test_access_mode_reduces_read_write_db_role_to_read_only(self):
        profile = self.service._normalize_profile({
            "engine": "postgres",
            "permissionMode": "read_only",
            "rolePermissionsDetected": True,
            "roleAllowedOperations": ["select", "insert", "update", "delete"],
        })

        self.assertEqual(profile["allowed_operations"], ["select"])
        with self.assertRaisesRegex(RuntimeError, "does not allow INSERT"):
            self.service._validate_sql_for_profile(profile, "INSERT INTO public.users (name) VALUES ('A')")

    def test_access_mode_does_not_add_missing_db_role_write_grants(self):
        profile = self.service._normalize_profile({
            "engine": "postgres",
            "permissionMode": "read_write",
            "rolePermissionsDetected": True,
            "roleAllowedOperations": ["select"],
        })

        self.assertEqual(profile["allowed_operations"], ["select"])
        with self.assertRaisesRegex(RuntimeError, "does not allow UPDATE"):
            self.service._validate_sql_for_profile(profile, "UPDATE public.users SET name = 'A' WHERE id = 1")

    def test_table_role_permissions_block_writes_on_specific_table(self):
        profile = self.service._normalize_profile({
            "engine": "postgres",
            "permissionMode": "read_write",
            "rolePermissionsDetected": True,
            "roleAllowedOperations": ["select", "insert"],
            "selectedSchemas": ["public"],
            "selectedTables": ["public.users"],
            "tablePermissions": {
                "public.users": ["select"],
            },
        })
        profile["index"] = 0

        with self.assertRaisesRegex(RuntimeError, "does not allow INSERT on 'public.users'"):
            self.service.prepare_configured_query(
                [profile],
                {"profile_index": 0, "sql": "INSERT INTO public.users (name) VALUES ('A')"},
            )

    def test_detects_requested_insert_intent(self):
        operation = self.service.detect_requested_operation(
            "add new customer. name: Daisey Lee, email: daisey@gmail.com, status: active"
        )

        self.assertEqual(operation, "insert")

    def test_json_safe_value_converts_database_driver_types(self):
        value = self.service._json_safe_value({
            "price": Decimal("19.99"),
            "quantity": Decimal("2"),
            "created_at": datetime(2026, 8, 5, 9, 30, 15),
        })

        self.assertEqual(value["price"], "19.99")
        self.assertEqual(value["quantity"], 2)
        self.assertEqual(value["created_at"], "2026-08-05T09:30:15")

    def test_configured_profiles_caps_profiles_and_keeps_purpose(self):
        config = {
            "tools": ["database"],
            "database": {
                "profiles": [
                    {
                        "engine": "postgres",
                        "name": f"DB {index}",
                        "purpose": f"Purpose {index}",
                        "selectedSchemas": ["public"],
                    }
                    for index in range(5)
                ]
            },
        }

        profiles = self.service.configured_profiles(config)

        self.assertEqual(len(profiles), 3)
        self.assertEqual(profiles[0]["purpose"], "Purpose 0")
        self.assertEqual(profiles[2]["configured_index"], 2)

    def test_plan_call_manual_profile_override_limits_planner_scope(self):
        profiles = []
        for index, name in enumerate(["Billing DB", "Support DB"]):
            profile = self.service._normalize_profile({
                "engine": "postgres",
                "name": name,
                "purpose": "Support tickets" if index == 1 else "Invoices",
                "permissionMode": "read_only",
                "selectedSchemas": ["public"],
                "schemaMetadata": [
                    {
                        "name": "public",
                        "tables": [
                            {
                                "name": "tickets" if index == 1 else "invoices",
                                "type": "table",
                                "columns": ["id", "name"],
                            }
                        ],
                    }
                ],
            })
            profile["index"] = index
            profiles.append(profile)

        with patch("database_tool_service.openai_service.call_text", return_value='{"should_call": true, "profile_index": 1, "sql": "SELECT * FROM public.tickets", "reason": "manual", "missing_parameters": []}') as call_text:
            plan = self.service.plan_call(
                api_key="test-key",
                agent_name="DB Agent",
                question="show tickets",
                history=[],
                profiles=profiles,
                selected_profile_index=1,
            )

        prompt_payload = call_text.call_args.kwargs["input_messages"][0]["content"]
        self.assertIn("Support DB", prompt_payload)
        self.assertIn("Support tickets", prompt_payload)
        self.assertNotIn("Billing DB", prompt_payload)
        self.assertTrue(plan["should_call"])
        self.assertEqual(plan["profile_index"], 1)

    def test_plan_call_reports_write_operation_not_allowed_before_model_call(self):
        profile = self.service._normalize_profile({
            "engine": "mysql",
            "permissionMode": "read_only",
            "selectedSchemas": ["agent_tool_test"],
            "schemaMetadata": [
                {
                    "name": "agent_tool_test",
                    "tables": [
                        {
                            "name": "customers",
                            "type": "table",
                            "columns": ["id", "name", "email", "status"],
                        }
                    ],
                }
            ],
        })
        profile["index"] = 0

        with patch("database_tool_service.openai_service.call_text") as call_text:
            plan = self.service.plan_call(
                api_key="test-key",
                agent_name="DB Agent",
                question="add new customer. name: Daisey Lee, email: daisey@gmail.com, status: active",
                history=[],
                profiles=[profile],
            )

        call_text.assert_not_called()
        self.assertFalse(plan["should_call"])
        self.assertEqual(plan["reason"], "database_operation_not_allowed")
        self.assertEqual(plan["requested_operation"], "insert")

    def test_manual_read_only_profile_blocks_delete_even_when_write_profile_exists(self):
        read_profile = self.service._normalize_profile({
            "engine": "mysql",
            "name": "Read Only Profile",
            "permissionMode": "read_only",
            "selectedSchemas": ["agent_tool_test"],
            "schemaMetadata": [
                {
                    "name": "agent_tool_test",
                    "tables": [
                        {
                            "name": "customers",
                            "type": "table",
                            "columns": [{"name": "id"}, {"name": "name"}],
                        }
                    ],
                }
            ],
        })
        write_profile = self.service._normalize_profile({
            "engine": "mysql",
            "name": "Write Profile",
            "permissionMode": "read_write",
            "selectedSchemas": ["agent_tool_test"],
            "schemaMetadata": [
                {
                    "name": "agent_tool_test",
                    "tables": [
                        {
                            "name": "customers",
                            "type": "table",
                            "columns": [{"name": "id"}, {"name": "name"}],
                        }
                    ],
                }
            ],
        })
        read_profile["index"] = 0
        write_profile["index"] = 1

        with patch("database_tool_service.openai_service.call_text") as call_text:
            plan = self.service.plan_call(
                api_key="test-key",
                agent_name="DB Agent",
                question="delete customer Keith Ramsay",
                history=[],
                profiles=[read_profile, write_profile],
                selected_profile_index=0,
            )

        call_text.assert_not_called()
        self.assertFalse(plan["should_call"])
        self.assertEqual(plan["reason"], "database_operation_not_allowed")
        self.assertEqual(plan["requested_operation"], "delete")

    def test_manual_profile_name_wins_over_stale_profile_index(self):
        read_profile = self.service._normalize_profile({
            "engine": "mysql",
            "name": "Read Only Profile",
            "permissionMode": "read_only",
            "selectedSchemas": ["agent_tool_test"],
            "schemaMetadata": [
                {
                    "name": "agent_tool_test",
                    "tables": [
                        {
                            "name": "customers",
                            "type": "table",
                            "columns": [{"name": "id"}, {"name": "name"}],
                        }
                    ],
                }
            ],
        })
        write_profile = self.service._normalize_profile({
            "engine": "mysql",
            "name": "Write Profile",
            "permissionMode": "read_write",
            "selectedSchemas": ["agent_tool_test"],
            "schemaMetadata": [
                {
                    "name": "agent_tool_test",
                    "tables": [
                        {
                            "name": "customers",
                            "type": "table",
                            "columns": [{"name": "id"}, {"name": "name"}],
                        }
                    ],
                }
            ],
        })
        read_profile["index"] = 0
        write_profile["index"] = 1

        with patch("database_tool_service.openai_service.call_text") as call_text:
            plan = self.service.plan_call(
                api_key="test-key",
                agent_name="DB Agent",
                question="add customer Keith Ramsay",
                history=[],
                profiles=[read_profile, write_profile],
                selected_profile_index=1,
                selected_profile_name="Read Only Profile",
            )

        call_text.assert_not_called()
        self.assertFalse(plan["should_call"])
        self.assertEqual(plan["reason"], "database_operation_not_allowed")
        self.assertEqual(plan["requested_operation"], "insert")

    def test_auto_write_request_filters_to_profiles_that_allow_operation(self):
        read_profile = self.service._normalize_profile({
            "engine": "mysql",
            "name": "Read Only Profile",
            "permissionMode": "read_only",
            "selectedSchemas": ["agent_tool_test"],
            "schemaMetadata": [
                {
                    "name": "agent_tool_test",
                    "tables": [
                        {
                            "name": "customers",
                            "type": "table",
                            "columns": [{"name": "id"}, {"name": "name"}],
                        }
                    ],
                }
            ],
        })
        write_profile = self.service._normalize_profile({
            "engine": "mysql",
            "name": "Write Profile",
            "permissionMode": "read_write",
            "selectedSchemas": ["agent_tool_test"],
            "schemaMetadata": [
                {
                    "name": "agent_tool_test",
                    "tables": [
                        {
                            "name": "customers",
                            "type": "table",
                            "columns": [{"name": "id"}, {"name": "name"}],
                        }
                    ],
                }
            ],
        })
        read_profile["index"] = 0
        write_profile["index"] = 1

        with patch("database_tool_service.openai_service.call_text", return_value='{"should_call": true, "profile_index": 1, "sql": "DELETE FROM agent_tool_test.customers WHERE name = ''Keith Ramsay''", "reason": "delete customer", "missing_parameters": []}') as call_text:
            plan = self.service.plan_call(
                api_key="test-key",
                agent_name="DB Agent",
                question="delete customer Keith Ramsay",
                history=[
                    {
                        "role": "assistant",
                        "content": "The selected database profile is configured to allow SELECT. Your request needs DELETE.",
                    }
                ],
                profiles=[read_profile, write_profile],
            )

        prompt_payload = call_text.call_args.kwargs["input_messages"][0]["content"]
        self.assertIn("Write Profile", prompt_payload)
        self.assertNotIn("Read Only Profile", prompt_payload)
        self.assertTrue(plan["should_call"])
        self.assertEqual(plan["profile_index"], 1)

    def test_update_requires_where_clause(self):
        profile = self.service._normalize_profile({"engine": "postgres", "permissionMode": "read_update"})

        with self.assertRaisesRegex(RuntimeError, "must include a WHERE clause"):
            self.service._validate_sql_for_profile(profile, "UPDATE public.users SET status = 'active'")

    def test_update_blocks_columns_outside_scope(self):
        profile = self.service._normalize_profile({
            "engine": "postgres",
            "permissionMode": "read_update",
            "selectedSchemas": ["public"],
            "selectedTables": ["public.users"],
            "selectedColumns": {"public.users": ["status"]},
        })
        sql, operation = self.service._validate_sql_for_profile(
            profile,
            "UPDATE public.users SET email = 'x@example.com' WHERE id = 1",
        )

        with self.assertRaisesRegex(RuntimeError, "outside this agent's write scope"):
            self.service._validate_column_scope(profile, sql, operation)

    def test_delete_allowed_only_in_read_write_mode(self):
        profile = self.service._normalize_profile({
            "engine": "postgres",
            "permissionMode": "read_write",
            "selectedSchemas": ["public"],
            "selectedTables": ["public.sessions"],
        })
        sql, operation = self.service._validate_sql_for_profile(
            profile,
            "DELETE FROM public.sessions WHERE expires_at < now()",
        )
        self.service._validate_scope(profile, sql, operation=operation)

        self.assertEqual(operation, "delete")

    def test_prepare_configured_query_returns_validated_write_metadata(self):
        profile = self.service._normalize_profile({
            "engine": "postgres",
            "permissionMode": "read_update",
            "selectedSchemas": ["public"],
            "selectedTables": ["public.users"],
            "selectedColumns": {"public.users": ["status"]},
        })
        profile["index"] = 0

        prepared = self.service.prepare_configured_query(
            [profile],
            {"profile_index": 0, "sql": "UPDATE public.users SET status = 'active' WHERE id = 1;"},
        )

        self.assertEqual(prepared["operation"], "update")
        self.assertEqual(prepared["sql"], "UPDATE public.users SET status = 'active' WHERE id = 1")
        self.assertEqual(prepared["profile"]["name"], profile["name"])

    def test_write_impact_helpers_build_safe_previews_for_simple_writes(self):
        self.assertEqual(
            self.service._count_sql_for_write(
                "UPDATE public.users SET status = 'active' WHERE id = 1 RETURNING id",
                "update",
            ),
            "SELECT COUNT(*) FROM public.users WHERE id = 1",
        )
        self.assertEqual(
            self.service._count_sql_for_write(
                "DELETE FROM public.sessions WHERE expires_at < now() LIMIT 5",
                "delete",
            ),
            "SELECT COUNT(*) FROM public.sessions WHERE expires_at < now()",
        )
        self.assertEqual(
            self.service._estimate_insert_rows(
                "INSERT INTO public.audit_log (event_type, message) VALUES ('a', 'b'), ('c', 'd')"
            ),
            2,
        )

    def test_audit_result_compaction_omits_read_rows(self):
        compact = self.audit_service._compact_result({
            "response": {
                "columns": ["id", "email"],
                "rows": [{"id": 1, "email": "a@example.com"}],
                "row_count": 1,
                "truncated": False,
            }
        })

        self.assertEqual(compact["response"]["rows"], [])
        self.assertTrue(compact["response"]["rows_omitted_from_audit"])
        self.assertEqual(compact["response"]["row_count"], 1)


if __name__ == "__main__":
    unittest.main()
