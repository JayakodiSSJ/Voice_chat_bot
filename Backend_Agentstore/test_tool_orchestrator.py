import unittest
from unittest.mock import patch

from tool_orchestrator import ToolOrchestrator
from tool_registry import ToolRegistry
from tool_types import ToolSpec


class ToolOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.orchestrator = ToolOrchestrator()
        self.registry = ToolRegistry(
            [
                ToolSpec(
                    id="api:0",
                    kind="api",
                    name="Customer lookup",
                    description="Fetch a customer by id",
                    input_schema={
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                        "required": ["id"],
                        "additionalProperties": False,
                    },
                    target={"endpoint_index": 0},
                    routing_profile={
                        "use_when": ["The user needs a current customer record"],
                        "do_not_use_when": ["The user wants general documentation"],
                    },
                )
            ]
        )

    @patch("tool_orchestrator.openai_service.call_text")
    def test_selects_one_registered_tool(self, call_text):
        call_text.return_value = (
            '{"action":"call_tool","tool_id":"api:0","arguments":{"id":"42"},'
            '"reason":"customer requested","missing_parameters":[]}'
        )

        plan = self.orchestrator.plan(
            api_key="test-key",
            agent_name="Support Agent",
            question="Find customer 42",
            history=[],
            registry=self.registry,
        )

        self.assertEqual(plan["tool_id"], "api:0")
        self.assertEqual(plan["arguments"], {"id": "42"})
        call_text.assert_called_once()
        prompt = call_text.call_args.kwargs["input_messages"][0]["content"]
        self.assertIn('"routing_profile"', prompt)
        self.assertIn("current customer record", prompt)
        instructions = call_text.call_args.kwargs["instructions"]
        self.assertIn("organization-specific selection guidance", instructions)
        self.assertIn("never grant permissions", instructions)
        self.assertIn("use it for substantive questions about assigned", instructions)
        self.assertIn("do not treat earlier assistant answers as evidence", instructions)
        self.assertIn("knowledge-base result from this turn already supports", instructions)
        self.assertIn("Preserve the user's language and wording in knowledge-base queries", instructions)
        self.assertIn("Do not translate Sinhala or", instructions)

    @patch("tool_orchestrator.openai_service.call_text")
    def test_rejects_a_tool_outside_the_registry(self, call_text):
        call_text.return_value = (
            '{"action":"call_tool","tool_id":"api:99","arguments":{},'
            '"reason":"invented","missing_parameters":[]}'
        )

        plan = self.orchestrator.plan(
            api_key="test-key",
            agent_name="Support Agent",
            question="Do something",
            history=[],
            registry=self.registry,
        )

        self.assertEqual(plan["action"], "no_tool")
        self.assertEqual(plan["reason"], "unassigned_tool_selected")

    @patch("tool_orchestrator.openai_service.call_text")
    def test_missing_required_argument_becomes_needs_input(self, call_text):
        call_text.return_value = (
            '{"action":"call_tool","tool_id":"api:0","arguments":{},'
            '"reason":"lookup requested","missing_parameters":[]}'
        )

        plan = self.orchestrator.plan(
            api_key="test-key",
            agent_name="Support Agent",
            question="Find the customer",
            history=[],
            registry=self.registry,
        )

        self.assertEqual(plan["action"], "needs_input")
        self.assertEqual(plan["tool_id"], "api:0")
        self.assertEqual(plan["missing_parameters"], ["id"])
        self.assertIn("Id", plan["message"])

    @patch("tool_orchestrator.openai_service.call_text")
    def test_wrong_type_and_unknown_argument_are_not_executed(self, call_text):
        call_text.return_value = (
            '{"action":"call_tool","tool_id":"api:0",'
            '"arguments":{"id":42,"invented":true},"reason":"lookup requested","missing_parameters":[]}'
        )

        plan = self.orchestrator.plan(
            api_key="test-key",
            agent_name="Support Agent",
            question="Find customer 42",
            history=[],
            registry=self.registry,
        )

        self.assertEqual(plan["action"], "needs_input")
        self.assertEqual(plan["reason"], "invalid_tool_arguments")
        self.assertEqual(plan["invalid_parameters"], ["id", "invented"])

    @patch("tool_orchestrator.openai_service.call_text")
    def test_planner_can_request_missing_input_explicitly(self, call_text):
        call_text.return_value = (
            '{"action":"needs_input","tool_id":"api:0","arguments":{},'
            '"reason":"customer id required","missing_parameters":["id"]}'
        )

        plan = self.orchestrator.plan(
            api_key="test-key",
            agent_name="Support Agent",
            question="Find a customer",
            history=[],
            registry=self.registry,
        )

        self.assertEqual(plan["action"], "needs_input")
        self.assertEqual(plan["missing_parameters"], ["id"])

    @patch("tool_orchestrator.openai_service.call_text")
    def test_prior_tool_results_are_available_for_next_step_planning(self, call_text):
        call_text.return_value = (
            '{"action":"no_tool","tool_id":null,"arguments":{},'
            '"reason":"lookup complete","missing_parameters":[]}'
        )
        execution_history = [
            {
                "tool_id": "api:0",
                "arguments": {"id": "42"},
                "result": {"ok": True, "response": {"name": "Ada", "access_token": "planner-secret"}},
            }
        ]

        plan = self.orchestrator.plan(
            api_key="test-key",
            agent_name="Support Agent",
            question="Find customer 42",
            history=[],
            registry=self.registry,
            execution_history=execution_history,
        )

        self.assertEqual(plan["action"], "no_tool")
        prompt = call_text.call_args.kwargs["input_messages"][0]["content"]
        self.assertIn("Tools already called for this request", prompt)
        self.assertIn('"name": "Ada"', prompt)
        self.assertNotIn("planner-secret", prompt)
        self.assertIn("[REDACTED]", prompt)
        instructions = call_text.call_args.kwargs["instructions"]
        self.assertIn("Never repeat an identical tool call", instructions)


if __name__ == "__main__":
    unittest.main()
