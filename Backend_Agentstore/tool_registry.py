from __future__ import annotations

import hashlib
import json
from typing import Any

from tool_types import ToolSpec
from tool_routing_profile import merge_routing_profiles, routing_profile_from


class ToolRegistry:
    def __init__(self, tools: list[ToolSpec] | None = None):
        self._tools: dict[str, ToolSpec] = {}
        for tool in tools or []:
            if tool.id in self._tools:
                raise ValueError(f"Duplicate tool id: {tool.id}")
            self._tools[tool.id] = tool

    def get(self, tool_id: Any) -> ToolSpec | None:
        return self._tools.get(str(tool_id or "").strip())

    def planner_payload(self) -> list[dict[str, Any]]:
        return [tool.planner_payload() for tool in self._tools.values()]

    def __bool__(self) -> bool:
        return bool(self._tools)

    @classmethod
    def from_configured_resources(
        cls,
        *,
        has_knowledge_base: bool,
        forms: list[dict[str, Any]],
        api_endpoints: list[dict[str, Any]],
        database_summaries: list[dict[str, Any]],
        mcp_servers: list[dict[str, Any]],
        knowledge_base_routing_profile: dict[str, Any] | None = None,
        shown_form_ids: list[str] | None = None,
        dismissed_form_ids: list[str] | None = None,
    ) -> "ToolRegistry":
        tools: list[ToolSpec] = []

        if has_knowledge_base:
            tools.append(
                ToolSpec(
                    id="knowledge_base:search",
                    kind="knowledge_base",
                    name="Search assigned knowledge base",
                    description=(
                        "Search the documents assigned to this agent. Use for substantive questions "
                        "that require facts from those documents."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                    target={"operation": "search"},
                    routing_profile=merge_routing_profiles(knowledge_base_routing_profile),
                )
            )

        shown = {str(item) for item in shown_form_ids or []}
        dismissed = {str(item) for item in dismissed_form_ids or []}
        for form in forms:
            form_id = str(form.get("id") or "").strip()
            if not form_id:
                continue
            tools.append(
                ToolSpec(
                    id=f"form:{form_id}",
                    kind="form",
                    name=str(form.get("title") or "Render form"),
                    description=str(
                        form.get("triggerDescription")
                        or form.get("description")
                        or "Render this form only when the user's intent clearly matches it."
                    ),
                    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
                    target={"form_id": form_id},
                    planner_context={
                        "trigger_keywords": form.get("triggerKeywords") or [],
                        "already_shown": form_id in shown,
                        "dismissed": form_id in dismissed,
                    },
                    routing_profile=routing_profile_from(form),
                )
            )

        for endpoint in api_endpoints:
            endpoint_index = endpoint.get("index")
            properties: dict[str, Any] = {}
            required: list[str] = []
            for parameter in endpoint.get("parameters") or []:
                key = str(parameter.get("key") or "").strip()
                if not key:
                    continue
                value_type = str(parameter.get("dataType") or parameter.get("type") or "string").lower()
                if value_type not in {"string", "number", "integer", "boolean", "array", "object"}:
                    value_type = "string"
                properties[key] = {
                    "type": value_type,
                    "description": str(parameter.get("description") or "").strip(),
                }
                required_value = str(parameter.get("required") or "").strip().lower()
                has_default = parameter.get("value") not in (None, "")
                if required_value in {"true", "yes", "1", "required"} and not has_default:
                    required.append(key)
            method = str(endpoint.get("method") or "GET").upper()
            api_fingerprint = _configuration_fingerprint(
                {
                    "kind": "api",
                    "method": method,
                    "url": endpoint.get("url"),
                    "parameters": [
                        {
                            "key": parameter.get("key"),
                            "location": parameter.get("location") or "query",
                            "type": parameter.get("dataType") or parameter.get("type") or "string",
                            "required": parameter.get("required"),
                        }
                        for parameter in endpoint.get("parameters") or []
                    ],
                }
            )
            tools.append(
                ToolSpec(
                    id=f"api:{endpoint_index}",
                    kind="api",
                    name=str(endpoint.get("name") or f"{method} API endpoint"),
                    description=str(
                        endpoint.get("description")
                        or f"Call the configured {method} endpoint {endpoint.get('url')}."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": properties,
                        "required": required,
                        "additionalProperties": False,
                    },
                    target={"endpoint_index": endpoint_index},
                    side_effect="none" if method == "GET" else "conditional",
                    approval_policy="never" if method == "GET" else "always",
                    configuration_fingerprint=api_fingerprint,
                    planner_context={
                        "method": method,
                        "url": endpoint.get("url"),
                        "parameter_locations": {
                            str(parameter.get("key")): str(parameter.get("location") or "query")
                            for parameter in endpoint.get("parameters") or []
                            if str(parameter.get("key") or "").strip()
                        },
                    },
                    routing_profile=routing_profile_from(endpoint),
                )
            )

        for summary in database_summaries:
            profile_index = summary.get("index")
            allowed_operations = [str(item).lower() for item in summary.get("allowed_operations") or []]
            side_effect = "none" if set(allowed_operations).issubset({"select"}) else "conditional"
            tools.append(
                ToolSpec(
                    id=f"database:{profile_index}",
                    kind="database",
                    name=str(summary.get("name") or "Database"),
                    description=str(
                        summary.get("purpose")
                        or "Query this configured database profile within its visible schema and permissions."
                    ),
                    input_schema={
                        "type": "object",
                        "properties": {
                            "sql": {
                                "type": "string",
                                "description": "One SQL statement in the configured database dialect.",
                            }
                        },
                        "required": ["sql"],
                        "additionalProperties": False,
                    },
                    target={"profile_index": profile_index},
                    side_effect=side_effect,
                    planner_context=summary,
                    routing_profile=routing_profile_from(summary),
                )
            )

        for server in mcp_servers:
            allowed = set(server.get("allowed_tools") or [])
            configured_read_only = set(server.get("read_only_tools") or [])
            for tool in server.get("tools") or []:
                tool_name = str(tool.get("name") or "").strip()
                if not tool_name or (allowed and tool_name not in allowed):
                    continue
                read_only = tool_name in configured_read_only
                mcp_fingerprint = _configuration_fingerprint(
                    {
                        "kind": "mcp",
                        "server_url": server.get("url"),
                        "tool_name": tool_name,
                        "input_schema": tool.get("inputSchema") or {"type": "object"},
                    }
                )
                tools.append(
                    ToolSpec(
                        id=f"mcp:{server.get('index')}:{tool_name}",
                        kind="mcp",
                        name=str(tool.get("title") or tool_name),
                        description=str(tool.get("description") or f"Call {tool_name} on {server.get('name')}."),
                        input_schema=tool.get("inputSchema") or {"type": "object"},
                        target={"server_index": server.get("index"), "tool_name": tool_name},
                        side_effect="none" if read_only else "conditional",
                        approval_policy="never" if read_only else "always",
                        configuration_fingerprint=mcp_fingerprint,
                        planner_context={"server_name": server.get("name")},
                        routing_profile=merge_routing_profiles(
                            routing_profile_from(server),
                            routing_profile_from(tool),
                        ),
                    )
                )

        return cls(tools)


def _configuration_fingerprint(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
