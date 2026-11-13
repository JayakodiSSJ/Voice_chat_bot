import unittest

from api_tool_service import ApiToolService
from database_tool_service import DatabaseToolService
from form_tool_service import FormToolService
from mcp_tool_service import McpToolService


class ToolRoutingIntegrationTests(unittest.TestCase):
    def test_configuration_services_preserve_routing_profiles(self):
        api_profile = {"use_when": ["Look up a live order"]}
        api_endpoints = ApiToolService().configured_endpoints(
            {
                "tools": ["api"],
                "api": {
                    "api_endpoints": [
                        {"url": "https://example.test/orders", "routing_profile": api_profile}
                    ]
                },
            }
        )
        self.assertEqual(api_endpoints[0]["routing_profile"], api_profile)

        mcp_profile = {"data_scope": "Engineering projects"}
        mcp_servers = McpToolService().configured_servers(
            {
                "tools": ["mcp"],
                "mcp": {
                    "servers": [
                        {
                            "url": "https://example.test/mcp",
                            "tools": [{"name": "find_project"}],
                            "routing_profile": mcp_profile,
                        }
                    ]
                },
            }
        )
        self.assertEqual(mcp_servers[0]["routing_profile"], mcp_profile)

        database_profile = {"purpose": "Query governed finance records"}
        database_profiles = DatabaseToolService().configured_profiles(
            {
                "tools": ["database"],
                "database": {
                    "profiles": [
                        {
                            "engine": "postgres",
                            "host": "db.example.test",
                            "database": "finance",
                            "allowedSchemas": ["public"],
                            "routing_profile": database_profile,
                        }
                    ]
                },
            }
        )
        self.assertEqual(database_profiles[0]["routing_profile"], database_profile)

        form_profile = {"example_requests": ["Register me for the event"]}
        forms = FormToolService().configured_forms(
            {
                "tools": ["form_handler"],
                "form_handlers": [
                    {
                        "triggerDescription": "Event registration",
                        "fields": [{"label": "Email"}],
                        "routing_profile": form_profile,
                    }
                ],
            }
        )
        self.assertEqual(forms[0]["routing_profile"], form_profile)


if __name__ == "__main__":
    unittest.main()
