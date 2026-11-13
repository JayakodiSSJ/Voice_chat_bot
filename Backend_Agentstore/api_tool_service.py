from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib.parse import quote, urlparse

import requests

from agent_runtime_utils import clean_text, extract_json_object
from model_router import model_router
from openai_service import openai_service


class ApiToolService:
    def configured_endpoints(self, agent_config: dict[str, Any]) -> list[dict[str, Any]]:
        api_config = agent_config.get("api") or agent_config.get("api_config") or {}
        if not isinstance(api_config, dict):
            return []

        configured_tools = agent_config.get("tools") or []
        normalized_tools = {
            str(item.get("type") if isinstance(item, dict) else item or "").strip().lower()
            for item in configured_tools
        }
        if configured_tools and "api" not in normalized_tools:
            return []

        raw_endpoints = api_config.get("api_endpoints") or api_config.get("apiEndpoints") or []
        if isinstance(raw_endpoints, (str, dict)):
            raw_endpoints = [raw_endpoints]

        fallback_method = str(api_config.get("req_type") or api_config.get("reqType") or "GET").upper()
        endpoints: list[dict[str, Any]] = []
        for configured_index, endpoint in enumerate(raw_endpoints or []):
            if isinstance(endpoint, str):
                endpoint = {"url": endpoint}
            if not isinstance(endpoint, dict):
                continue

            url = str(endpoint.get("url") or endpoint.get("endpoint") or "").strip()
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue

            method = str(endpoint.get("method") or fallback_method or "GET").upper()
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue

            parameters = endpoint.get("parameters") or []
            if not isinstance(parameters, list):
                parameters = []

            endpoints.append(
                {
                    "index": len(endpoints),
                    "configured_index": configured_index,
                    "name": str(endpoint.get("name") or endpoint.get("label") or "").strip(),
                    "description": str(endpoint.get("description") or endpoint.get("purpose") or "").strip(),
                    "routing_profile": endpoint.get("routing_profile") or endpoint.get("routingProfile") or {},
                    "url": url,
                    "method": method,
                    "format": endpoint.get("format") or "JSON",
                    "parameters": [
                        param
                        for param in parameters
                        if isinstance(param, dict) and str(param.get("key") or "").strip()
                    ],
                }
            )

        return endpoints

    def plan_call(
        self,
        *,
        api_key: str,
        agent_name: str,
        question: str,
        history: list[dict[str, str]],
        api_endpoints: list[dict[str, Any]],
    ) -> dict[str, Any]:
        endpoint_summaries = []
        for endpoint in api_endpoints:
            endpoint_summaries.append(
                {
                    "index": endpoint["index"],
                    "method": endpoint["method"],
                    "url": endpoint["url"],
                    "parameters": [
                        {
                            "key": param.get("key"),
                            "location": param.get("location") or "query",
                            "default": param.get("value") or "",
                        }
                        for param in endpoint.get("parameters", [])
                    ],
                }
            )

        history_text = "\n".join(
            f"{item.get('role')}: {clean_text(item.get('content') or '')[:500]}"
            for item in history[-6:]
            if item.get("role") in {"user", "assistant"} and item.get("content")
        )
        planning_prompt = (
            f"You are planning API tool use for {agent_name}. "
            "Choose only from the configured endpoints. Return JSON only with this shape: "
            "{\"should_call\": boolean, \"endpoint_index\": number|null, "
            "\"parameters\": object, \"reason\": string, \"missing_parameters\": array}. "
            "Set should_call=true only when the user clearly needs one configured API. "
            "Path parameters replace matching {parameter_key} placeholders in the endpoint URL. "
            "Do not invent endpoints or parameter keys. Use configured default parameter values when useful. "
            "If a required value is unknown and has no default, set should_call=false."
        )
        response_text = openai_service.call_text(
            api_key=api_key,
            model=model_router.choose("api_plan"),
            instructions=planning_prompt,
            input_messages=[
                {
                    "role": "user",
                    "content": (
                        f"Configured API endpoints:\n{json.dumps(endpoint_summaries, indent=2)}\n\n"
                        f"Recent conversation:\n{history_text or 'None'}\n\n"
                        f"User message:\n{question}"
                    ),
                }
            ],
            max_output_tokens=350,
        )
        try:
            plan = json.loads(extract_json_object(response_text))
        except Exception:
            return {"should_call": False, "endpoint_index": None, "parameters": {}, "reason": "invalid_api_plan"}

        if not isinstance(plan, dict):
            return {"should_call": False, "endpoint_index": None, "parameters": {}, "reason": "invalid_api_plan"}
        if not isinstance(plan.get("parameters"), dict):
            plan["parameters"] = {}
        return plan

    def execute_configured_call(
        self,
        api_endpoints: list[dict[str, Any]],
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            endpoint_index = int(plan.get("endpoint_index"))
        except (TypeError, ValueError):
            raise RuntimeError("API tool selected an invalid endpoint.")

        endpoint = next((item for item in api_endpoints if item["index"] == endpoint_index), None)
        if not endpoint:
            raise RuntimeError("API tool selected an endpoint that is not assigned to this agent.")

        planned_parameters = plan.get("parameters") if isinstance(plan.get("parameters"), dict) else {}
        request_url = endpoint["url"]
        path_params: dict[str, Any] = {}
        query_params: dict[str, Any] = {}
        body_params: dict[str, Any] = {}
        headers = {"User-Agent": "AgentStoreAPITool/1.0"}
        missing_path_params: list[str] = []

        for param in endpoint.get("parameters", []):
            key = str(param.get("key") or "").strip()
            if not key:
                continue
            location = str(param.get("location") or "query").strip().lower()
            value = planned_parameters.get(key)
            if value in (None, ""):
                value = param.get("value")
            if value in (None, ""):
                continue

            if location == "path":
                placeholder = "{" + key + "}"
                if placeholder not in request_url:
                    continue
                request_url = request_url.replace(placeholder, quote(str(value), safe=""))
                path_params[key] = value
            elif location == "header":
                headers[key] = str(value)
            elif location == "body":
                body_params[key] = value
            else:
                query_params[key] = value

        for key in re.findall(r"\{([^{}]+)\}", request_url):
            missing_path_params.append(key)
        if missing_path_params:
            raise RuntimeError(f"Missing path parameter value(s): {', '.join(missing_path_params)}.")

        method = endpoint["method"]
        request_kwargs: dict[str, Any] = {
            "method": method,
            "url": request_url,
            "headers": headers,
            "params": query_params or None,
            "timeout": float(os.getenv("API_TOOL_TIMEOUT_SECONDS", "20")),
        }
        if method in {"POST", "PUT", "PATCH", "DELETE"} and body_params:
            request_kwargs["json"] = body_params

        response = requests.request(**request_kwargs)
        content_type = response.headers.get("content-type", "")
        if "json" in content_type.lower():
            try:
                parsed_body: Any = response.json()
                body_text = json.dumps(parsed_body, default=str)
                body = parsed_body if len(body_text) <= 8000 else {"truncated": True, "preview": body_text[:8000]}
            except Exception:
                body = response.text[:8000]
        else:
            body = response.text[:8000]

        return {
            "endpoint_index": endpoint["index"],
            "method": method,
            "url": request_url,
            "status_code": response.status_code,
            "ok": response.ok,
            "request": {
                "path": path_params,
                "query": query_params,
                "body": body_params,
                "headers": sorted(headers.keys()),
            },
            "response": body,
        }


api_tool_service = ApiToolService()
