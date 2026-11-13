import json
import unittest

from tool_observability import REDACTED_VALUE, ToolTrace, tool_data_redactor


class ToolObservabilityTests(unittest.TestCase):
    def test_redacts_sensitive_keys_and_common_token_text_without_hiding_safe_fields(self):
        value = {
            "author": "Ada",
            "Authorization": "Bearer top-secret",
            "authToken": "mcp-secret",
            "nested": {"apiKey": "api-secret", "customer_id": "42"},
            "url": "https://example.test/items?access_token=query-secret&customer_id=42",
            "error": "upstream token=inline-secret was rejected",
        }

        redacted = tool_data_redactor.redact(value)

        self.assertEqual(redacted["author"], "Ada")
        self.assertEqual(redacted["Authorization"], REDACTED_VALUE)
        self.assertEqual(redacted["authToken"], REDACTED_VALUE)
        self.assertEqual(redacted["nested"]["apiKey"], REDACTED_VALUE)
        self.assertEqual(redacted["nested"]["customer_id"], "42")
        self.assertNotIn("query-secret", redacted["url"])
        self.assertNotIn("inline-secret", redacted["error"])

    def test_trace_is_structured_and_never_contains_full_tool_response(self):
        trace = ToolTrace()
        trace.add("tool_call", tool_id="api:0", arguments={"access_token": "secret"})
        trace.add(
            "tool_result",
            tool_id="api:0",
            result=tool_data_redactor.summarize_result(
                {"ok": True, "response": {"access_token": "secret", "customer_id": "42"}}
            ),
        )

        events = trace.as_list()
        rendered = json.dumps(events)

        self.assertEqual([event["sequence"] for event in events], [1, 2])
        self.assertIn("response_fields", rendered)
        self.assertNotIn("secret", rendered)
        self.assertNotIn("customer_id\": \"42", rendered)


if __name__ == "__main__":
    unittest.main()
