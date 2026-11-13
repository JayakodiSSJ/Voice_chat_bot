from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import urlparse

import requests

from agent_runtime_utils import clean_text, extract_json_object
from model_router import model_router
from openai_service import openai_service


MCP_PROTOCOL_VERSION = "2025-06-18"


class McpToolService:
    def configured_servers(self, agent_config: dict[str, Any]) -> list[dict[str, Any]]:
        mcp_config = agent_config.get("mcp") or agent_config.get("mcp_config") or agent_config.get("mcpConfig") or {}
        api_config = agent_config.get("api") or agent_config.get("api_config") or {}

        configured_tools = agent_config.get("tools") or []
        normalized_tools = {
            str(item.get("type") if isinstance(item, dict) else item or "").strip().lower()
            for item in configured_tools
        }
        if configured_tools and "mcp" not in normalized_tools and "api" not in normalized_tools:
            return []

        raw_servers: Any = []
        if isinstance(mcp_config, dict):
            raw_servers = (
                mcp_config.get("servers")
                or mcp_config.get("mcp_servers")
                or mcp_config.get("mcpServers")
                or mcp_config.get("server")
                or []
            )
        elif isinstance(mcp_config, list):
            raw_servers = mcp_config

        if not raw_servers and isinstance(api_config, dict):
            raw_servers = api_config.get("mcp_servers") or api_config.get("mcpServers") or []

        if isinstance(raw_servers, dict):
            raw_servers = [raw_servers]
        if not isinstance(raw_servers, list):
            return []

        servers: list[dict[str, Any]] = []
        for configured_index, server in enumerate(raw_servers):
            if not isinstance(server, dict):
                continue
            url = str(server.get("url") or server.get("endpoint") or "").strip()
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue

            raw_allowed_tools = server.get("allowed_tools") or server.get("allowedTools") or []
            if isinstance(raw_allowed_tools, str):
                raw_allowed_tools = [raw_allowed_tools]
            allowed_tools = [
                str(item).strip()
                for item in raw_allowed_tools
                if str(item or "").strip()
            ] if isinstance(raw_allowed_tools, list) else []

            raw_read_only_tools = server.get("read_only_tools") or server.get("readOnlyTools") or []
            if isinstance(raw_read_only_tools, str):
                raw_read_only_tools = [raw_read_only_tools]
            read_only_tools = [
                str(item).strip()
                for item in raw_read_only_tools
                if str(item or "").strip()
            ] if isinstance(raw_read_only_tools, list) else []

            raw_tools = server.get("tools") or server.get("mcpTools") or server.get("mcp_tools") or []
            tools = [tool for tool in raw_tools if isinstance(tool, dict)] if isinstance(raw_tools, list) else []

            auth = self._normalize_auth_config(server)

            servers.append(
                {
                    "index": len(servers),
                    "configured_index": configured_index,
                    "name": str(server.get("name") or parsed.netloc or f"mcp_server_{configured_index + 1}").strip(),
                    "url": url,
                    "transport": "streamable_http",
                    "auth": auth,
                    "allowed_tools": allowed_tools,
                    "read_only_tools": read_only_tools,
                    "tools": tools,
                    "routing_profile": server.get("routing_profile") or server.get("routingProfile") or {},
                }
            )
        return servers

    def verify_server(self, server_config: dict[str, Any]) -> dict[str, Any]:
        servers = self.configured_servers({"tools": ["mcp"], "mcp": {"servers": [server_config]}})
        if not servers:
            raise RuntimeError("Configure a valid HTTP or HTTPS MCP server endpoint.")
        server = servers[0]
        tools = self.list_tools(server)
        return {
            "name": server["name"],
            "url": server["url"],
            "transport": server["transport"],
            "tools": tools,
        }

    def list_tools(self, server: dict[str, Any]) -> list[dict[str, Any]]:
        session_id = self._initialize(server)
        tools: list[dict[str, Any]] = []
        cursor = None
        for _ in range(25):
            params = {"cursor": cursor} if cursor else {}
            response = self._request(server, "tools/list", params, session_id=session_id)
            result = response.get("result") if isinstance(response.get("result"), dict) else {}
            for tool in result.get("tools") or []:
                if isinstance(tool, dict) and str(tool.get("name") or "").strip():
                    tools.append(self._safe_tool_definition(tool))
            cursor = result.get("nextCursor")
            if not cursor:
                break
        return tools

    def plan_call(
        self,
        *,
        api_key: str,
        agent_name: str,
        question: str,
        history: list[dict[str, str]],
        mcp_servers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        server_summaries = []
        for server in mcp_servers:
            allowed = set(server.get("allowed_tools") or [])
            tool_defs = []
            for tool in server.get("tools") or []:
                name = str(tool.get("name") or "").strip()
                if not name or (allowed and name not in allowed):
                    continue
                tool_defs.append(
                    {
                        "name": name,
                        "title": tool.get("title") or "",
                        "description": tool.get("description") or "",
                        "inputSchema": tool.get("inputSchema") or {"type": "object"},
                    }
                )
            if tool_defs:
                server_summaries.append(
                    {
                        "index": server["index"],
                        "name": server["name"],
                        "tools": tool_defs,
                    }
                )

        if not server_summaries:
            return {"should_call": False, "server_index": None, "tool_name": None, "arguments": {}, "reason": "no_allowed_mcp_tools"}

        history_text = "\n".join(
            f"{item.get('role')}: {clean_text(item.get('content') or '')[:500]}"
            for item in history[-6:]
            if item.get("role") in {"user", "assistant"} and item.get("content")
        )
        planning_prompt = (
            f"You are planning MCP tool use for {agent_name}. "
            "Choose only from the configured MCP servers and allowed tools. Return JSON only with this shape: "
            "{\"should_call\": boolean, \"server_index\": number|null, \"tool_name\": string|null, "
            "\"arguments\": object, \"reason\": string, \"missing_parameters\": array}. "
            "Set should_call=true only when the user clearly needs one allowed MCP tool. "
            "Do not invent tool names or argument keys. If a required value is unknown, set should_call=false."
        )
        response_text = openai_service.call_text(
            api_key=api_key,
            model=model_router.choose("mcp_plan"),
            instructions=planning_prompt,
            input_messages=[
                {
                    "role": "user",
                    "content": (
                        f"Configured MCP servers and tools:\n{json.dumps(server_summaries, indent=2)}\n\n"
                        f"Recent conversation:\n{history_text or 'None'}\n\n"
                        f"User message:\n{question}"
                    ),
                }
            ],
            max_output_tokens=450,
        )
        try:
            plan = json.loads(extract_json_object(response_text))
        except Exception:
            return {"should_call": False, "server_index": None, "tool_name": None, "arguments": {}, "reason": "invalid_mcp_plan"}

        if not isinstance(plan, dict):
            return {"should_call": False, "server_index": None, "tool_name": None, "arguments": {}, "reason": "invalid_mcp_plan"}
        if not isinstance(plan.get("arguments"), dict):
            plan["arguments"] = {}
        return plan

    def execute_configured_call(
        self,
        mcp_servers: list[dict[str, Any]],
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            server_index = int(plan.get("server_index"))
        except (TypeError, ValueError):
            raise RuntimeError("MCP tool selected an invalid server.")

        server = next((item for item in mcp_servers if item["index"] == server_index), None)
        if not server:
            raise RuntimeError("MCP tool selected a server that is not assigned to this agent.")

        tool_name = str(plan.get("tool_name") or "").strip()
        allowed_tools = set(server.get("allowed_tools") or [])
        available_tools = {
            str(tool.get("name") or "").strip()
            for tool in server.get("tools") or []
            if isinstance(tool, dict)
        }
        if not tool_name:
            raise RuntimeError("MCP tool selected an empty tool name.")
        if allowed_tools and tool_name not in allowed_tools:
            raise RuntimeError("MCP tool selected a tool that is not allowed for this agent.")
        if available_tools and tool_name not in available_tools:
            raise RuntimeError("MCP tool selected a tool that is not exposed by this server.")

        session_id = self._initialize(server)
        result = self._request(
            server,
            "tools/call",
            {
                "name": tool_name,
                "arguments": plan.get("arguments") if isinstance(plan.get("arguments"), dict) else {},
            },
            session_id=session_id,
        )
        call_result = result.get("result") if isinstance(result.get("result"), dict) else result
        return {
            "type": "mcp",
            "server_index": server["index"],
            "server_name": server["name"],
            "tool_name": tool_name,
            "ok": not bool(call_result.get("isError")) if isinstance(call_result, dict) else True,
            "request": {
                "arguments": plan.get("arguments") if isinstance(plan.get("arguments"), dict) else {},
            },
            "response": self._truncate(call_result),
        }

    def _initialize(self, server: dict[str, Any]) -> str | None:
        response = self._request(
            server,
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "AgentStore", "version": "1.0"},
            },
        )
        session_id = response.get("_session_id")
        try:
            self._send_notification(server, "notifications/initialized", {}, session_id=session_id)
        except Exception:
            pass
        return session_id

    def _request(
        self,
        server: dict[str, Any],
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        request_id = os.urandom(8).hex()
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        data, response = self._post_json_rpc(server, payload, session_id=session_id)
        if not isinstance(data, dict):
            raise RuntimeError("MCP server returned an invalid JSON-RPC response.")
        if data.get("error"):
            message = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else None
            raise RuntimeError(message or "MCP server returned an error.")
        if "_session_id" not in data:
            data["_session_id"] = response.headers.get("Mcp-Session-Id")
        return data

    def _send_notification(
        self,
        server: dict[str, Any],
        method: str,
        params: dict[str, Any] | None = None,
        *,
        session_id: str | None = None,
    ) -> None:
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        self._post_json_rpc(server, payload, session_id=session_id, allow_empty=True)

    def _post_json_rpc(
        self,
        server: dict[str, Any],
        payload: dict[str, Any],
        *,
        session_id: str | None = None,
        allow_empty: bool = False,
    ) -> tuple[dict[str, Any], requests.Response]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
            "User-Agent": "AgentStoreMCPTool/1.0",
            **self._auth_headers(server),
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id

        timeout_seconds = float(os.getenv("MCP_TOOL_TIMEOUT_SECONDS", "30"))
        try:
            response = requests.post(
                server["url"],
                headers=headers,
                json=payload,
                timeout=timeout_seconds,
            )
        except requests.exceptions.ConnectTimeout as exc:
            raise RuntimeError(
                f"Could not connect to MCP server at {self._display_url(server)} within "
                f"{timeout_seconds:g} seconds. Confirm the host is reachable from the backend, "
                "port 443 is open, and VPN/firewall rules allow outbound HTTPS."
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise RuntimeError(
                f"MCP server at {self._display_url(server)} did not respond within "
                f"{timeout_seconds:g} seconds."
            ) from exc
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(
                f"Could not reach MCP server at {self._display_url(server)}. Confirm the URL, "
                "DNS, TLS certificate, and network/firewall access from the backend."
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise RuntimeError(f"MCP request to {self._display_url(server)} failed: {exc}") from exc
        if allow_empty and response.status_code in {200, 202, 204} and not response.text.strip():
            return {"_session_id": response.headers.get("Mcp-Session-Id")}, response
        if not response.ok:
            raise RuntimeError(f"MCP server returned HTTP {response.status_code}.")

        content_type = response.headers.get("content-type", "").lower()
        if "text/event-stream" in content_type:
            data = self._parse_sse_json_response(response.text, payload.get("id"))
        else:
            try:
                data = response.json()
            except Exception as exc:
                raise RuntimeError("MCP server did not return JSON.") from exc
        if isinstance(data, dict):
            data.setdefault("_session_id", response.headers.get("Mcp-Session-Id"))
        return data, response

    def _auth_headers(self, server: dict[str, Any]) -> dict[str, str]:
        auth = self._normalize_auth_config(server)
        auth_type = str(auth.get("type") or "none").strip().lower()
        if auth_type == "bearer" and auth.get("token"):
            return {"Authorization": f"Bearer {auth.get('token')}"}
        if auth_type in {"api_key", "header", "custom_header"} and auth.get("headerName") and auth.get("token"):
            return {str(auth.get("headerName")).strip(): str(auth.get("token"))}
        headers = auth.get("headers")
        if isinstance(headers, dict):
            return {
                str(key).strip(): str(value)
                for key, value in headers.items()
                if str(key or "").strip() and value is not None
            }
        return {}

    def _normalize_auth_config(self, server: dict[str, Any]) -> dict[str, Any]:
        auth = server.get("auth") if isinstance(server.get("auth"), dict) else {}
        auth_type = (
            server.get("authType")
            or server.get("auth_type")
            or auth.get("type")
            or "none"
        )
        auth_token = (
            server.get("authToken")
            or server.get("auth_token")
            or auth.get("token")
            or auth.get("value")
            or ""
        )
        auth_header_name = (
            server.get("authHeaderName")
            or server.get("auth_header_name")
            or auth.get("headerName")
            or auth.get("header_name")
            or auth.get("name")
            or ""
        )
        return {
            **auth,
            "type": auth_type,
            "token": auth_token,
            "headerName": auth_header_name,
        }

    def _display_url(self, server: dict[str, Any]) -> str:
        parsed = urlparse(str(server.get("url") or ""))
        if not parsed.scheme or not parsed.netloc:
            return str(server.get("url") or "configured endpoint")
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"

    def _parse_sse_json_response(self, text: str, request_id: Any) -> dict[str, Any]:
        events: list[str] = []
        current: list[str] = []
        for line in text.splitlines():
            if not line.strip():
                if current:
                    events.append("\n".join(current))
                    current = []
                continue
            if line.startswith("data:"):
                current.append(line[5:].strip())
        if current:
            events.append("\n".join(current))

        for event in events:
            try:
                data = json.loads(event)
            except Exception:
                continue
            if request_id is None or data.get("id") == request_id:
                return data
        raise RuntimeError("MCP SSE response did not include the requested JSON-RPC response.")

    def _safe_tool_definition(self, tool: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": str(tool.get("name") or "").strip(),
            "title": str(tool.get("title") or "").strip(),
            "description": str(tool.get("description") or "").strip()[:1200],
            "inputSchema": tool.get("inputSchema") if isinstance(tool.get("inputSchema"), dict) else {"type": "object"},
            "outputSchema": tool.get("outputSchema") if isinstance(tool.get("outputSchema"), dict) else None,
            "annotations": tool.get("annotations") if isinstance(tool.get("annotations"), dict) else {},
        }

    def _truncate(self, value: Any) -> Any:
        text = json.dumps(value, default=str)
        if len(text) <= 8000:
            return value
        return {"truncated": True, "preview": text[:8000]}


mcp_tool_service = McpToolService()
