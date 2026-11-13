from __future__ import annotations

from tool_types import ToolSpec


class ToolPolicyService:
    def requires_approval(self, tool: ToolSpec) -> bool:
        return (
            tool.kind in {"api", "mcp"}
            and tool.side_effect != "none"
            and tool.approval_policy == "always"
        )


tool_policy_service = ToolPolicyService()
