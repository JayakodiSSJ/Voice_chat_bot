import json
import unittest

from tool_registry import ToolRegistry


class ToolRegistryTests(unittest.TestCase):
    def test_normalizes_configured_resources_without_exposing_api_defaults(self):
        registry = ToolRegistry.from_configured_resources(
            has_knowledge_base=True,
            knowledge_base_routing_profile={
                "purpose": "Answer questions from approved company documents",
                "do_not_use_when": ["The request needs live account data"],
            },
            forms=[
                {
                    "id": "form-0",
                    "title": "Contact form",
                    "triggerDescription": "Collect contact details",
                    "triggerKeywords": ["contact"],
                    "routing_profile": {"example_requests": ["Contact sales"]},
                }
            ],
            api_endpoints=[
                {
                    "index": 0,
                    "name": "Customer lookup",
                    "description": "Fetch a customer",
                    "method": "GET",
                    "url": "https://example.test/customers/{id}",
                    "parameters": [
                        {"key": "id", "location": "path", "required": True},
                        {"key": "Authorization", "location": "header", "value": "secret-token"},
                    ],
                    "routing_profile": {"use_when": ["A current customer record is required"]},
                }
            ],
            database_summaries=[
                {
                    "index": 0,
                    "name": "Support DB",
                    "purpose": "Support tickets",
                    "allowed_operations": ["select"],
                    "allowed_schemas": ["public"],
                    "routing_profile": {"data_scope": "Support records"},
                }
            ],
            mcp_servers=[
                {
                    "index": 0,
                    "name": "CRM",
                    "allowed_tools": ["find_contact"],
                    "routing_profile": {"purpose": "Use the company CRM"},
                    "tools": [
                        {
                            "name": "find_contact",
                            "description": "Find a CRM contact",
                            "inputSchema": {"type": "object", "properties": {"email": {"type": "string"}}},
                            "routing_profile": {"counterexamples": ["Search public documentation"]},
                        }
                    ],
                }
            ],
            shown_form_ids=["form-0"],
        )

        payload = registry.planner_payload()
        self.assertEqual(
            {item["id"] for item in payload},
            {
                "knowledge_base:search",
                "form:form-0",
                "api:0",
                "database:0",
                "mcp:0:find_contact",
            },
        )
        self.assertNotIn("secret-token", json.dumps(payload))
        self.assertTrue(registry.get("form:form-0").planner_context["already_shown"])
        self.assertEqual(registry.get("api:0").approval_policy, "never")
        self.assertEqual(registry.get("mcp:0:find_contact").approval_policy, "always")
        self.assertEqual(
            registry.get("knowledge_base:search").routing_profile["purpose"],
            "Answer questions from approved company documents",
        )
        self.assertEqual(
            registry.get("mcp:0:find_contact").routing_profile,
            {
                "purpose": "Use the company CRM",
                "counterexamples": ["Search public documentation"],
            },
        )
        self.assertIn("routing_profile", next(item for item in payload if item["id"] == "api:0"))

    def test_only_workspace_configured_read_only_mcp_tool_avoids_approval(self):
        registry = ToolRegistry.from_configured_resources(
            has_knowledge_base=False,
            forms=[],
            api_endpoints=[],
            database_summaries=[],
            mcp_servers=[
                {
                    "index": 0,
                    "name": "Search",
                    "url": "https://example.test/mcp",
                    "allowed_tools": ["search"],
                    "read_only_tools": ["search"],
                    "tools": [
                        {
                            "name": "search",
                            "inputSchema": {"type": "object"},
                            "annotations": {"readOnlyHint": True},
                        }
                    ],
                }
            ],
        )

        tool = registry.get("mcp:0:search")
        self.assertEqual(tool.side_effect, "none")
        self.assertEqual(tool.approval_policy, "never")

    def test_mcp_server_annotation_cannot_bypass_approval(self):
        registry = ToolRegistry.from_configured_resources(
            has_knowledge_base=False,
            forms=[],
            api_endpoints=[],
            database_summaries=[],
            mcp_servers=[
                {
                    "index": 0,
                    "name": "Search",
                    "url": "https://example.test/mcp",
                    "allowed_tools": ["search"],
                    "tools": [
                        {
                            "name": "search",
                            "inputSchema": {"type": "object"},
                            "annotations": {"readOnlyHint": True},
                        }
                    ],
                }
            ],
        )

        self.assertEqual(registry.get("mcp:0:search").approval_policy, "always")

    def test_api_configuration_fingerprint_changes_with_executable_target_not_secret_default(self):
        def api_tool(url: str, token: str):
            return ToolRegistry.from_configured_resources(
                has_knowledge_base=False,
                forms=[],
                api_endpoints=[
                    {
                        "index": 0,
                        "method": "POST",
                        "url": url,
                        "parameters": [
                            {"key": "Authorization", "location": "header", "value": token},
                            {"key": "name", "location": "body", "required": True},
                        ],
                    }
                ],
                database_summaries=[],
                mcp_servers=[],
            ).get("api:0")

        original = api_tool("https://example.test/customers", "secret-one")
        credential_rotated = api_tool("https://example.test/customers", "secret-two")
        target_changed = api_tool("https://example.test/v2/customers", "secret-one")

        self.assertEqual(original.configuration_fingerprint, credential_rotated.configuration_fingerprint)
        self.assertNotEqual(original.configuration_fingerprint, target_changed.configuration_fingerprint)
        self.assertEqual(original.approval_policy, "always")


if __name__ == "__main__":
    unittest.main()
