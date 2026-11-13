import unittest

from tool_policy_service import ToolPolicyService
from tool_types import ToolSpec


class ToolPolicyServiceTests(unittest.TestCase):
    def test_only_side_effecting_api_and_mcp_tools_require_approval(self):
        policy = ToolPolicyService()

        self.assertFalse(policy.requires_approval(self._tool("api", "none", "never")))
        self.assertTrue(policy.requires_approval(self._tool("api", "conditional", "always")))
        self.assertTrue(policy.requires_approval(self._tool("mcp", "conditional", "always")))
        self.assertFalse(policy.requires_approval(self._tool("database", "conditional", "always")))

    @staticmethod
    def _tool(kind, side_effect, approval_policy):
        return ToolSpec(
            id=f"{kind}:0",
            kind=kind,
            name="Tool",
            description="Tool",
            input_schema={"type": "object"},
            side_effect=side_effect,
            approval_policy=approval_policy,
        )


if __name__ == "__main__":
    unittest.main()
