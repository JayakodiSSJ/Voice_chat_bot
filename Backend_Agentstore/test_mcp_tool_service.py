import unittest

from mcp_tool_service import McpToolService


class McpToolServiceAuthTests(unittest.TestCase):
    def setUp(self):
        self.service = McpToolService()

    def test_custom_header_auth_from_nested_config(self):
        headers = self.service._auth_headers({
            "auth": {
                "type": "custom_header",
                "token": "secret-token",
                "headerName": "X-API-Key",
            }
        })

        self.assertEqual(headers, {"X-API-Key": "secret-token"})

    def test_custom_header_auth_from_flat_config(self):
        headers = self.service._auth_headers({
            "authType": "custom_header",
            "authToken": "secret-token",
            "authHeaderName": "X-API-Key",
        })

        self.assertEqual(headers, {"X-API-Key": "secret-token"})

    def test_bearer_auth_from_nested_config(self):
        headers = self.service._auth_headers({
            "auth": {
                "type": "bearer",
                "token": "secret-token",
            }
        })

        self.assertEqual(headers, {"Authorization": "Bearer secret-token"})


if __name__ == "__main__":
    unittest.main()
