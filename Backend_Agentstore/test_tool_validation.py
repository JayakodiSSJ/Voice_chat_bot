import unittest

from tool_types import ToolSpec
from tool_validation import ToolArgumentValidator


class ToolArgumentValidatorTests(unittest.TestCase):
    def setUp(self):
        self.validator = ToolArgumentValidator()

    def test_validates_nested_arguments(self):
        tool = self._tool(
            {
                "type": "object",
                "properties": {
                    "customer": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "integer", "minimum": 1},
                            "status": {"type": "string", "enum": ["active", "inactive"]},
                        },
                        "required": ["id"],
                        "additionalProperties": False,
                    },
                    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                },
                "required": ["customer"],
                "additionalProperties": False,
            }
        )

        result = self.validator.validate(
            tool,
            {"customer": {"id": 42, "status": "active"}, "tags": ["priority"]},
        )

        self.assertTrue(result.valid)

    def test_reports_missing_wrong_type_and_unknown_arguments(self):
        tool = self._tool(
            {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "email": {"type": "string", "pattern": "@"},
                },
                "required": ["id"],
                "additionalProperties": False,
            }
        )

        result = self.validator.validate(tool, {"email": "invalid", "invented": True})

        self.assertFalse(result.valid)
        self.assertEqual(result.missing_parameters, ["id"])
        self.assertEqual(result.invalid_parameters, ["email", "invented"])

    def test_resolves_local_schema_references(self):
        tool = self._tool(
            {
                "type": "object",
                "$defs": {"identifier": {"type": "string", "minLength": 2}},
                "properties": {"id": {"$ref": "#/$defs/identifier"}},
                "required": ["id"],
                "additionalProperties": False,
            }
        )

        self.assertTrue(self.validator.validate(tool, {"id": "42"}).valid)
        self.assertFalse(self.validator.validate(tool, {"id": "x"}).valid)

    def test_rejects_booleans_as_numbers_and_invalid_schema_references(self):
        numeric_tool = self._tool(
            {
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 10}},
                "required": ["limit"],
                "additionalProperties": False,
            }
        )
        invalid_reference_tool = self._tool(
            {
                "type": "object",
                "properties": {"id": {"$ref": "#/missing"}},
                "required": ["id"],
            }
        )

        bool_result = self.validator.validate(numeric_tool, {"limit": True})
        range_result = self.validator.validate(numeric_tool, {"limit": 11})
        reference_result = self.validator.validate(invalid_reference_tool, {"id": "42"})

        self.assertFalse(bool_result.valid)
        self.assertFalse(range_result.valid)
        self.assertFalse(reference_result.valid)
        self.assertIn("limit", bool_result.invalid_parameters)
        self.assertIn("limit", range_result.invalid_parameters)
        self.assertIn("id", reference_result.invalid_parameters)

    @staticmethod
    def _tool(schema):
        return ToolSpec(
            id="test:tool",
            kind="test",
            name="Test tool",
            description="Test",
            input_schema=schema,
        )


if __name__ == "__main__":
    unittest.main()
