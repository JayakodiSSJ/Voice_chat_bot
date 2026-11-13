from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from tool_routing_profile import normalize_routing_profile


@dataclass(frozen=True)
class ToolSpec:
    """Normalized, planner-safe description of an executable agent tool."""

    id: str
    kind: str
    name: str
    description: str
    input_schema: dict[str, Any]
    target: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    side_effect: str = "none"
    approval_policy: str = "never"
    configuration_fingerprint: str = field(default="", repr=False)
    planner_context: dict[str, Any] = field(default_factory=dict)
    routing_profile: dict[str, Any] = field(default_factory=dict)

    def planner_payload(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "side_effect": self.side_effect,
            "approval_policy": self.approval_policy,
        }
        if self.planner_context:
            payload["context"] = self.planner_context
        routing_profile = normalize_routing_profile(self.routing_profile)
        if routing_profile:
            payload["routing_profile"] = routing_profile
        return payload
