import json
import unittest
from unittest.mock import MagicMock, patch

from custom_agent_chat_service import CustomAgentChatService


class CustomAgentChatServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = CustomAgentChatService()

    def test_formats_database_select_rows_with_all_columns(self):
        answer = self.service.format_database_select_answer(
            "list all customers",
            [
                {
                    "type": "database",
                    "ok": True,
                    "operation": "select",
                    "response": {
                        "columns": ["name", "email", "status", "created_at"],
                        "rows": [
                            {
                                "name": "Keith Ramsay",
                                "email": "keith@gmail.com",
                                "status": "inactive",
                                "created_at": "2026-08-04",
                            }
                        ],
                        "row_count": 1,
                    },
                }
            ],
        )

        self.assertIn("Here are customers:", answer)
        self.assertIn("| Name | Email | Status | Created At |", answer)
        self.assertIn("| --- | --- | --- | --- |", answer)
        self.assertIn("| Keith Ramsay | keith@gmail.com | inactive | 2026-08-04 |", answer)

    def test_recognizes_shared_tool_confirmation_commands(self):
        self.assertTrue(self.service.is_tool_confirmation_command("Run Tool #42"))
        self.assertTrue(self.service.is_tool_confirmation_command("Cancel tool action 42"))
        self.assertFalse(self.service.is_tool_confirmation_command("Run Query #42"))

    @patch("custom_agent_chat_service.openai_service.call_text", return_value="Thanks for asking.")
    def test_voice_answer_uses_conversational_speaking_instructions(self, call_text):
        answer = self.service.generate_openai_answer(
            api_key="test-key",
            agent_name="Support Agent",
            question="What are your hours?",
            history=[],
            context_blocks=["Support is open from nine to five."],
            response_mode="voice",
        )

        instructions = call_text.call_args.kwargs["instructions"]
        self.assertEqual(answer, "Thanks for asking.")
        self.assertIn("naturally conversational", instructions)
        self.assertIn("Avoid markdown", instructions)
        self.assertIn("answer only from the provided document context", instructions)
        self.assertIn("Treat conversation history as context for understanding follow-ups, not as evidence", instructions)
        self.assertIn('"under" means strictly less than the stated amount', instructions)
        self.assertIn('"up to" means less than or equal to it', instructions)
        self.assertIn("Do not include higher-priced or unrelated plans", instructions)
        self.assertNotIn("Use bullet points", instructions)

    @patch("custom_agent_chat_service.openai_service.call_text", return_value="හරි, මම උදව් කරන්නම්.")
    def test_sinhala_unicode_and_singlish_use_sinhala_unicode_response_guidance(self, call_text):
        questions = (
            "මට අඩු ගානට data package එකක් ඕනේ",
            "mata package ekak one, data keeyada?",
        )
        for question in questions:
            with self.subTest(question=question):
                self.service.generate_openai_answer(
                    api_key="test-key",
                    agent_name="SLT-MOBITEL Support",
                    question=question,
                    history=[],
                    context_blocks=[],
                )
                call = call_text.call_args
                self.assertIn("respond in natural, friendly Sinhala Unicode", call.kwargs["instructions"])
                self.assertIn("respond in Sinhala Unicode by default", call.kwargs["instructions"])
                self.assertIn(question, call.kwargs["input_messages"][-1]["content"])

    @patch("custom_agent_chat_service.openai_service.call_text", return_value="I can help with that.")
    def test_english_and_mixed_language_response_guidance(self, call_text):
        for question in ("What internet packages are available?", "මට Fiber package එකක් ඕනේ"):
            with self.subTest(question=question):
                self.service.generate_openai_answer(
                    api_key="test-key",
                    agent_name="SLT-MOBITEL Support",
                    question=question,
                    history=[],
                    context_blocks=["Available information is limited to this document."],
                )
                instructions = call_text.call_args.kwargs["instructions"]
                self.assertIn("For English input, respond in English", instructions)
                self.assertIn("For Sinhala-English mixed input", instructions)
                self.assertIn(question, call_text.call_args.kwargs["input_messages"][-1]["content"])

    @patch("custom_agent_chat_service.openai_service.call_text", return_value="I can help you explore that.")
    def test_latest_user_language_can_switch_from_sinhala_to_english(self, call_text):
        history = [
            {"role": "user", "content": "මට Fiber connection එකක් ගැන දැනගන්න ඕනේ"},
            {"role": "assistant", "content": "ඔයාගේ ප්‍රදේශය කියන්න පුළුවන්ද?"},
        ]
        self.service.generate_openai_answer(
            api_key="test-key",
            agent_name="SLT-MOBITEL Support",
            question="Please continue in English. What areas are covered?",
            history=history,
            context_blocks=[],
        )

        instructions = call_text.call_args.kwargs["instructions"]
        messages = call_text.call_args.kwargs["input_messages"]
        self.assertIn("the latest message takes priority", instructions)
        self.assertEqual(messages[0]["content"], history[0]["content"])
        self.assertEqual(messages[1]["content"], history[1]["content"])
        self.assertIn("Please continue in English. What areas are covered?", messages[-1]["content"])

    @patch("custom_agent_chat_service.openai_service.call_text", return_value="මේ තොරතුර මට තහවුරු කරන්න බැහැ.")
    def test_package_answers_without_knowledge_must_not_invent_details(self, call_text):
        self.service.generate_openai_answer(
            api_key="test-key",
            agent_name="SLT-MOBITEL Support",
            question="මට data package එකක price එක සහ activation code එක කියන්න",
            history=[],
            context_blocks=[],
        )

        instructions = call_text.call_args.kwargs["instructions"]
        self.assertIn("only when supported by the assigned-resource results", instructions)
        self.assertIn("using the user's preferred language", instructions)
        self.assertIn("Do not guess missing package prices, data allowances, validity periods, or activation codes", instructions)

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("custom_agent_chat_service.mcp_tool_service.plan_call")
    @patch("custom_agent_chat_service.database_tool_service.plan_call")
    @patch("custom_agent_chat_service.api_tool_service.plan_call")
    @patch("custom_agent_chat_service.tool_orchestrator.plan")
    @patch("custom_agent_chat_service.api_tool_service.execute_configured_call")
    def test_chat_replans_after_selected_executor_returns(
        self,
        execute_api,
        orchestrator_plan,
        legacy_api_plan,
        legacy_database_plan,
        legacy_mcp_plan,
    ):
        endpoint = {
            "index": 0,
            "configured_index": 0,
            "name": "Customer lookup",
            "description": "Fetch a customer",
            "url": "https://example.test/customers/{id}",
            "method": "GET",
            "parameters": [{"key": "id", "location": "path"}],
        }
        orchestrator_plan.side_effect = [
            {
                "action": "call_tool",
                "tool_id": "api:0",
                "arguments": {"id": "42"},
                "reason": "customer lookup",
                "missing_parameters": [],
            },
            {
                "action": "no_tool",
                "tool_id": None,
                "arguments": {},
                "reason": "lookup complete",
                "missing_parameters": [],
            },
        ]
        execute_api.return_value = {
            "ok": True,
            "endpoint_index": 0,
            "response": {"id": "42", "name": "Ada"},
        }

        with (
            patch("custom_agent_chat_service.form_tool_service.configured_forms", return_value=[]),
            patch("custom_agent_chat_service.api_tool_service.configured_endpoints", return_value=[endpoint]),
            patch("custom_agent_chat_service.database_tool_service.configured_profiles", return_value=[]),
            patch("custom_agent_chat_service.mcp_tool_service.configured_servers", return_value=[]),
            patch.object(self.service, "generate_openai_answer", return_value="Ada"),
        ):
            response = self.service.chat_custom_agent(
                company_id=1,
                agent_id=2,
                agent_name="Support Agent",
                agent_config={"tools": ["api"]},
                question="Find customer 42",
            )

        self.assertEqual(response["answer"], "Ada")
        self.assertEqual(orchestrator_plan.call_count, 2)
        self.assertEqual(orchestrator_plan.call_args_list[0].kwargs["execution_history"], [])
        completed_steps = orchestrator_plan.call_args_list[1].kwargs["execution_history"]
        self.assertEqual(completed_steps[0]["tool_id"], "api:0")
        self.assertEqual(completed_steps[0]["result"]["response"]["name"], "Ada")
        execute_api.assert_called_once()
        self.assertEqual(execute_api.call_args.args[1]["parameters"], {"id": "42"})
        legacy_api_plan.assert_not_called()
        legacy_database_plan.assert_not_called()
        legacy_mcp_plan.assert_not_called()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("custom_agent_chat_service.tool_orchestrator.plan")
    @patch("custom_agent_chat_service.api_tool_service.execute_configured_call")
    def test_chat_can_chain_multiple_tool_calls(self, execute_api, orchestrator_plan):
        endpoints = [
            {
                "index": 0,
                "configured_index": 0,
                "name": "Customer lookup",
                "url": "https://example.test/customers/{id}",
                "method": "GET",
                "parameters": [{"key": "id", "location": "path", "required": True}],
            },
            {
                "index": 1,
                "configured_index": 1,
                "name": "Order lookup",
                "url": "https://example.test/orders",
                "method": "GET",
                "parameters": [{"key": "customer_id", "location": "query", "required": True}],
            },
        ]
        orchestrator_plan.side_effect = [
            {"action": "call_tool", "tool_id": "api:0", "arguments": {"id": "42"}},
            {"action": "call_tool", "tool_id": "api:1", "arguments": {"customer_id": "42"}},
            {"action": "no_tool", "tool_id": None, "arguments": {}},
        ]
        execute_api.side_effect = [
            {"ok": True, "endpoint_index": 0, "response": {"id": "42", "name": "Ada"}},
            {"ok": True, "endpoint_index": 1, "response": {"orders": [{"id": "A-1"}]}},
        ]

        with (
            patch("custom_agent_chat_service.form_tool_service.configured_forms", return_value=[]),
            patch("custom_agent_chat_service.api_tool_service.configured_endpoints", return_value=endpoints),
            patch("custom_agent_chat_service.database_tool_service.configured_profiles", return_value=[]),
            patch("custom_agent_chat_service.mcp_tool_service.configured_servers", return_value=[]),
            patch.object(self.service, "generate_openai_answer", return_value="Ada has order A-1."),
        ):
            response = self.service.chat_custom_agent(
                company_id=1,
                agent_id=2,
                agent_name="Support Agent",
                agent_config={"tools": ["api"]},
                question="Find customer 42 and their orders",
            )

        self.assertEqual(response["answer"], "Ada has order A-1.")
        self.assertEqual(len(response["api_results"]), 2)
        self.assertEqual(execute_api.call_count, 2)
        self.assertEqual(orchestrator_plan.call_count, 3)
        second_plan_history = orchestrator_plan.call_args_list[1].kwargs["execution_history"]
        self.assertEqual(second_plan_history[0]["result"]["response"]["name"], "Ada")
        final_plan_history = orchestrator_plan.call_args_list[2].kwargs["execution_history"]
        self.assertEqual([step["tool_id"] for step in final_plan_history], ["api:0", "api:1"])

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("custom_agent_chat_service.tool_orchestrator.plan")
    @patch("custom_agent_chat_service.api_tool_service.execute_configured_call")
    def test_chat_stops_before_repeating_identical_tool_call(self, execute_api, orchestrator_plan):
        endpoint = {
            "index": 0,
            "configured_index": 0,
            "name": "Customer lookup",
            "url": "https://example.test/customers/{id}",
            "method": "GET",
            "parameters": [{"key": "id", "location": "path", "required": True}],
        }
        orchestrator_plan.return_value = {
            "action": "call_tool",
            "tool_id": "api:0",
            "arguments": {"id": "42"},
        }
        execute_api.return_value = {"ok": True, "response": {"id": "42", "name": "Ada"}}

        with (
            patch("custom_agent_chat_service.form_tool_service.configured_forms", return_value=[]),
            patch("custom_agent_chat_service.api_tool_service.configured_endpoints", return_value=[endpoint]),
            patch("custom_agent_chat_service.database_tool_service.configured_profiles", return_value=[]),
            patch("custom_agent_chat_service.mcp_tool_service.configured_servers", return_value=[]),
            patch.object(self.service, "generate_openai_answer", return_value="Ada"),
        ):
            response = self.service.chat_custom_agent(
                company_id=1,
                agent_id=2,
                agent_name="Support Agent",
                agent_config={"tools": ["api"]},
                question="Find customer 42",
            )

        self.assertEqual(response["answer"], "Ada")
        self.assertEqual(orchestrator_plan.call_count, 2)
        execute_api.assert_called_once()
        self.assertEqual(len(response["api_results"]), 1)
        self.assertEqual(response["tool_trace"][-1]["reason"], "duplicate_tool_call")

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("custom_agent_chat_service.tool_orchestrator.plan")
    @patch("custom_agent_chat_service.api_tool_service.execute_configured_call")
    def test_tool_results_and_trace_are_redacted_before_returning_or_replanning(self, execute_api, orchestrator_plan):
        endpoint = {
            "index": 0,
            "configured_index": 0,
            "name": "Customer lookup",
            "url": "https://example.test/customers/{id}",
            "method": "GET",
            "parameters": [{"key": "id", "location": "path", "required": True}],
        }
        orchestrator_plan.side_effect = [
            {"action": "call_tool", "tool_id": "api:0", "arguments": {"id": "42"}},
            {"action": "no_tool", "tool_id": None, "arguments": {}},
        ]
        execute_api.return_value = {
            "ok": True,
            "response": {"access_token": "response-secret", "customer_id": "42"},
            "request": {"Authorization": "Bearer request-secret"},
        }

        with (
            patch("custom_agent_chat_service.form_tool_service.configured_forms", return_value=[]),
            patch("custom_agent_chat_service.api_tool_service.configured_endpoints", return_value=[endpoint]),
            patch("custom_agent_chat_service.database_tool_service.configured_profiles", return_value=[]),
            patch("custom_agent_chat_service.mcp_tool_service.configured_servers", return_value=[]),
            patch.object(self.service, "generate_openai_answer", return_value="Ada" ) as generate_answer,
        ):
            response = self.service.chat_custom_agent(
                company_id=1,
                agent_id=2,
                agent_name="Support Agent",
                agent_config={"tools": ["api"]},
                question="Find customer 42",
            )

        rendered = json.dumps(response)
        self.assertNotIn("response-secret", rendered)
        self.assertNotIn("request-secret", rendered)
        self.assertEqual(response["api_results"][0]["response"]["access_token"], "[REDACTED]")
        self.assertEqual(response["tool_trace"][0]["event"], "plan")
        self.assertEqual(response["tool_trace"][-1]["event"], "stop")
        model_results = generate_answer.call_args.kwargs["api_results"]
        self.assertEqual(model_results[0]["response"]["access_token"], "[REDACTED]")

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("custom_agent_chat_service.tool_orchestrator.plan")
    @patch("custom_agent_chat_service.api_tool_service.execute_configured_call")
    def test_chat_caps_tool_calls_per_turn(self, execute_api, orchestrator_plan):
        endpoint = {
            "index": 0,
            "configured_index": 0,
            "name": "Page lookup",
            "url": "https://example.test/pages/{page}",
            "method": "GET",
            "parameters": [{"key": "page", "location": "path", "required": True}],
        }
        orchestrator_plan.side_effect = [
            {"action": "call_tool", "tool_id": "api:0", "arguments": {"page": str(page)}}
            for page in range(1, 5)
        ]
        execute_api.side_effect = [
            {"ok": True, "response": {"page": page}}
            for page in range(1, 5)
        ]

        with (
            patch("custom_agent_chat_service.form_tool_service.configured_forms", return_value=[]),
            patch("custom_agent_chat_service.api_tool_service.configured_endpoints", return_value=[endpoint]),
            patch("custom_agent_chat_service.database_tool_service.configured_profiles", return_value=[]),
            patch("custom_agent_chat_service.mcp_tool_service.configured_servers", return_value=[]),
            patch.object(self.service, "generate_openai_answer", return_value="Done"),
        ):
            response = self.service.chat_custom_agent(
                company_id=1,
                agent_id=2,
                agent_name="Support Agent",
                agent_config={"tools": ["api"]},
                question="Read all pages",
            )

        self.assertEqual(orchestrator_plan.call_count, 3)
        self.assertEqual(execute_api.call_count, 3)
        self.assertEqual(len(response["api_results"]), 3)
        self.assertEqual(response["tool_trace"][-1]["reason"], "tool_call_limit_reached")

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("custom_agent_chat_service.tool_orchestrator.plan")
    @patch("custom_agent_chat_service.api_tool_service.execute_configured_call")
    def test_needs_input_after_a_safe_call_keeps_the_completed_result(self, execute_api, orchestrator_plan):
        endpoint = {
            "index": 0,
            "configured_index": 0,
            "name": "Customer lookup",
            "url": "https://example.test/customers/{id}",
            "method": "GET",
            "parameters": [{"key": "id", "location": "path", "required": True}],
        }
        orchestrator_plan.side_effect = [
            {"action": "call_tool", "tool_id": "api:0", "arguments": {"id": "42"}},
            {
                "action": "needs_input",
                "tool_id": "api:0",
                "arguments": {},
                "message": "I need an additional filter.",
                "missing_parameters": ["filter"],
            },
        ]
        execute_api.return_value = {"ok": True, "response": {"id": "42", "name": "Ada"}}

        with (
            patch("custom_agent_chat_service.form_tool_service.configured_forms", return_value=[]),
            patch("custom_agent_chat_service.api_tool_service.configured_endpoints", return_value=[endpoint]),
            patch("custom_agent_chat_service.database_tool_service.configured_profiles", return_value=[]),
            patch("custom_agent_chat_service.mcp_tool_service.configured_servers", return_value=[]),
        ):
            response = self.service.chat_custom_agent(
                company_id=1,
                agent_id=2,
                agent_name="Support Agent",
                agent_config={"tools": ["api"]},
                question="Find customer 42 and filter their cases",
            )

        self.assertEqual(response["type"], "needs_input")
        self.assertEqual(response["api_results"][0]["response"]["name"], "Ada")
        self.assertEqual(response["tool_trace"][-1]["reason"], "needs_input")
        execute_api.assert_called_once()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("custom_agent_chat_service.tool_orchestrator.plan")
    @patch("custom_agent_chat_service.api_tool_service.execute_configured_call")
    def test_chat_returns_needs_input_without_executing_tool(self, execute_api, orchestrator_plan):
        endpoint = {
            "index": 0,
            "configured_index": 0,
            "name": "Customer lookup",
            "description": "Fetch a customer",
            "url": "https://example.test/customers/{id}",
            "method": "GET",
            "parameters": [{"key": "id", "location": "path", "required": True}],
        }
        orchestrator_plan.return_value = {
            "action": "needs_input",
            "tool_id": "api:0",
            "arguments": {},
            "reason": "missing_tool_input",
            "missing_parameters": ["id"],
            "invalid_parameters": [],
            "validation_errors": ["Required value id is missing."],
            "message": "Before I can use Customer lookup, I need: Id.",
        }

        with (
            patch("custom_agent_chat_service.form_tool_service.configured_forms", return_value=[]),
            patch("custom_agent_chat_service.api_tool_service.configured_endpoints", return_value=[endpoint]),
            patch("custom_agent_chat_service.database_tool_service.configured_profiles", return_value=[]),
            patch("custom_agent_chat_service.mcp_tool_service.configured_servers", return_value=[]),
        ):
            response = self.service.chat_custom_agent(
                company_id=1,
                agent_id=2,
                agent_name="Support Agent",
                agent_config={"tools": ["api"]},
                question="Find the customer",
            )

        self.assertEqual(response["type"], "needs_input")
        self.assertEqual(response["missing_parameters"], ["id"])
        execute_api.assert_not_called()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("custom_agent_chat_service.tool_audit_service.create_pending_confirmation")
    @patch("custom_agent_chat_service.get_conn")
    @patch("custom_agent_chat_service.tool_orchestrator.plan")
    @patch("custom_agent_chat_service.api_tool_service.execute_configured_call")
    def test_side_effecting_api_waits_for_confirmation(
        self,
        execute_api,
        orchestrator_plan,
        get_conn,
        create_pending,
    ):
        endpoint = {
            "index": 0,
            "configured_index": 0,
            "name": "Create customer",
            "description": "Create a customer",
            "url": "https://example.test/customers",
            "method": "POST",
            "parameters": [{"key": "name", "location": "body", "required": True}],
        }
        orchestrator_plan.return_value = {
            "action": "call_tool",
            "tool_id": "api:0",
            "arguments": {"name": "Ada"},
            "reason": "create customer",
            "missing_parameters": [],
        }
        create_pending.return_value = {"id": 81, "status": "pending_user_confirmation"}
        get_conn.return_value.__enter__.return_value = MagicMock()

        with (
            patch("custom_agent_chat_service.form_tool_service.configured_forms", return_value=[]),
            patch("custom_agent_chat_service.api_tool_service.configured_endpoints", return_value=[endpoint]),
            patch("custom_agent_chat_service.database_tool_service.configured_profiles", return_value=[]),
            patch("custom_agent_chat_service.mcp_tool_service.configured_servers", return_value=[]),
        ):
            response = self.service.chat_custom_agent(
                company_id=1,
                agent_id=2,
                user_id=3,
                agent_name="Support Agent",
                agent_config={"tools": ["api"]},
                question="Create customer Ada",
            )

        self.assertEqual(response["api_results"][0]["status"], "pending_user_confirmation")
        self.assertEqual(response["quick_replies"], ["Run Tool #81", "Cancel Tool #81"])
        orchestrator_plan.assert_called_once()
        create_pending.assert_called_once()
        execute_api.assert_not_called()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("custom_agent_chat_service.tool_audit_service.create_pending_confirmation")
    @patch("custom_agent_chat_service.api_tool_service.execute_configured_call")
    @patch("custom_agent_chat_service.tool_orchestrator.plan")
    def test_side_effecting_api_is_blocked_without_an_authenticated_user(
        self,
        orchestrator_plan,
        execute_api,
        create_pending,
    ):
        endpoint = {
            "index": 0,
            "configured_index": 0,
            "name": "Create customer",
            "url": "https://example.test/customers",
            "method": "POST",
            "parameters": [{"key": "name", "location": "body", "required": True}],
        }
        orchestrator_plan.return_value = {
            "action": "call_tool",
            "tool_id": "api:0",
            "arguments": {"name": "Ada"},
        }

        with (
            patch("custom_agent_chat_service.form_tool_service.configured_forms", return_value=[]),
            patch("custom_agent_chat_service.api_tool_service.configured_endpoints", return_value=[endpoint]),
            patch("custom_agent_chat_service.database_tool_service.configured_profiles", return_value=[]),
            patch("custom_agent_chat_service.mcp_tool_service.configured_servers", return_value=[]),
        ):
            response = self.service.chat_custom_agent(
                company_id=1,
                agent_id=2,
                agent_name="Support Agent",
                agent_config={"tools": ["api"]},
                question="Create customer Ada",
            )

        self.assertEqual(response["service_error"], "tool_confirmation_requires_authenticated_user")
        self.assertEqual(response["tool_trace"][-1]["reason"], "terminal_tool_response")
        create_pending.assert_not_called()
        execute_api.assert_not_called()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("custom_agent_chat_service.tool_audit_service.mark_execution_result")
    @patch("custom_agent_chat_service.tool_audit_service.mark_user_executing", return_value=True)
    @patch("custom_agent_chat_service.tool_audit_service.get_for_user")
    @patch("custom_agent_chat_service.get_conn")
    @patch("custom_agent_chat_service.api_tool_service.execute_configured_call")
    def test_confirmed_api_action_executes_once(
        self,
        execute_api,
        get_conn,
        get_activity,
        mark_executing,
        mark_result,
    ):
        endpoint = {
            "index": 0,
            "configured_index": 0,
            "name": "Create customer",
            "description": "Create a customer",
            "url": "https://example.test/customers",
            "method": "POST",
            "parameters": [{"key": "name", "location": "body", "required": True}],
        }
        registry = self._registry_for_api(endpoint)
        get_activity.return_value = {
            "id": 81,
            "status": "pending_user_confirmation",
            "tool_id": "api:0",
            "configuration_fingerprint": registry.get("api:0").configuration_fingerprint,
            "arguments": {"name": "Ada"},
        }
        execute_api.return_value = {"ok": True, "status_code": 201, "response": {"id": 42}}
        get_conn.return_value.__enter__.return_value = MagicMock()

        with (
            patch("custom_agent_chat_service.api_tool_service.configured_endpoints", return_value=[endpoint]),
            patch("custom_agent_chat_service.mcp_tool_service.configured_servers", return_value=[]),
        ):
            response = self.service.chat_custom_agent(
                company_id=1,
                agent_id=2,
                user_id=3,
                agent_name="Support Agent",
                agent_config={"tools": ["api"]},
                question="Run Tool #81",
            )

        self.assertIn("completed successfully", response["answer"])
        execute_api.assert_called_once()
        mark_executing.assert_called_once()
        mark_result.assert_called_once()

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("custom_agent_chat_service.tool_audit_service.mark_execution_result")
    @patch("custom_agent_chat_service.tool_audit_service.mark_user_executing", return_value=True)
    @patch("custom_agent_chat_service.tool_audit_service.get_for_user")
    @patch("custom_agent_chat_service.get_conn")
    @patch("custom_agent_chat_service.api_tool_service.execute_configured_call")
    def test_confirmation_uses_private_arguments_and_redacts_the_completed_response(
        self,
        execute_api,
        get_conn,
        get_activity,
        _mark_executing,
        _mark_result,
    ):
        endpoint = {
            "index": 0,
            "configured_index": 0,
            "name": "Create customer",
            "url": "https://example.test/customers",
            "method": "POST",
            "parameters": [
                {"key": "name", "location": "body", "required": True},
                {"key": "accessToken", "location": "header", "required": True},
            ],
        }
        registry = self._registry_for_api(endpoint)
        get_activity.return_value = {
            "id": 81,
            "status": "pending_user_confirmation",
            "tool_id": "api:0",
            "configuration_fingerprint": registry.get("api:0").configuration_fingerprint,
            "arguments": {"name": "Ada", "accessToken": "[REDACTED]"},
            "execution_arguments": {"name": "Ada", "accessToken": "private-token"},
        }
        execute_api.return_value = {"ok": True, "response": {"accessToken": "response-token"}}
        get_conn.return_value.__enter__.return_value = MagicMock()

        with (
            patch("custom_agent_chat_service.api_tool_service.configured_endpoints", return_value=[endpoint]),
            patch("custom_agent_chat_service.mcp_tool_service.configured_servers", return_value=[]),
        ):
            response = self.service.chat_custom_agent(
                company_id=1,
                agent_id=2,
                user_id=3,
                agent_name="Support Agent",
                agent_config={"tools": ["api"]},
                question="Run Tool #81",
            )

        self.assertEqual(execute_api.call_args.args[1]["parameters"]["accessToken"], "private-token")
        self.assertEqual(response["api_results"][0]["response"]["accessToken"], "[REDACTED]")

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"})
    @patch("custom_agent_chat_service.tool_audit_service.mark_execution_result")
    @patch("custom_agent_chat_service.tool_audit_service.mark_user_executing", return_value=True)
    @patch("custom_agent_chat_service.tool_audit_service.get_for_user")
    @patch("custom_agent_chat_service.get_conn")
    @patch("custom_agent_chat_service.api_tool_service.execute_configured_call")
    def test_changed_api_configuration_invalidates_pending_approval(
        self,
        execute_api,
        get_conn,
        get_activity,
        _mark_executing,
        mark_result,
    ):
        endpoint = {
            "index": 0,
            "configured_index": 0,
            "name": "Create customer",
            "url": "https://new.example.test/customers",
            "method": "POST",
            "parameters": [],
        }
        get_activity.return_value = {
            "id": 81,
            "status": "pending_user_confirmation",
            "tool_id": "api:0",
            "configuration_fingerprint": "old-fingerprint",
            "arguments": {},
        }
        get_conn.return_value.__enter__.return_value = MagicMock()

        with (
            patch("custom_agent_chat_service.api_tool_service.configured_endpoints", return_value=[endpoint]),
            patch("custom_agent_chat_service.mcp_tool_service.configured_servers", return_value=[]),
        ):
            response = self.service.chat_custom_agent(
                company_id=1,
                agent_id=2,
                user_id=3,
                agent_name="Support Agent",
                agent_config={"tools": ["api"]},
                question="Run Tool #81",
            )

        self.assertIn("configuration changed", response["answer"])
        execute_api.assert_not_called()
        self.assertEqual(mark_result.call_args.kwargs["status"], "failed")

    @staticmethod
    def _registry_for_api(endpoint):
        from tool_registry import ToolRegistry

        return ToolRegistry.from_configured_resources(
            has_knowledge_base=False,
            forms=[],
            api_endpoints=[endpoint],
            database_summaries=[],
            mcp_servers=[],
        )


if __name__ == "__main__":
    unittest.main()
