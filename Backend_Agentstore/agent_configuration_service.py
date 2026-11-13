from __future__ import annotations

from dataclasses import dataclass
import os
from pprint import pformat
import re
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models

from tool_routing_profile import routing_profile_template


VALID_REQUEST_TYPES = {"GET", "POST", "PUT", "PATCH", "DELETE"}


@dataclass(frozen=True)
class ToolDefinition:
    id: str
    label: str
    description: str
    aliases: tuple[str, ...]
    config_keys: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "aliases": list(self.aliases),
            "config_keys": list(self.config_keys),
            "routing_profile_template": routing_profile_template(),
        }


TOOL_REGISTRY: dict[str, ToolDefinition] = {
    "api": ToolDefinition(
        id="api",
        label="API Connector",
        description="Call configured external REST API endpoints from a custom agent.",
        aliases=("api", "apis", "api_component"),
        config_keys=("api", "api_config", "apiConfig", "api_endpoints", "apiEndpoints"),
    ),
    "mcp": ToolDefinition(
        id="mcp",
        label="MCP Connector",
        description="Attach Model Context Protocol servers to a custom agent.",
        aliases=("mcp", "mcp_tool", "mcp_connector", "mcp_server", "mcp_servers"),
        config_keys=("mcp", "mcp_config", "mcpConfig", "mcp_servers", "mcpServers"),
    ),
    "database": ToolDefinition(
        id="database",
        label="Database Connector",
        description="Attach a read-only structured database profile to a custom agent.",
        aliases=("db", "database", "database_tool", "structured_database", "structured_db"),
        config_keys=("database", "database_config", "databaseConfig", "structured_database", "structuredDatabase"),
    ),
    "knowledge_base": ToolDefinition(
        id="knowledge_base",
        label="Knowledge Base",
        description="Attach website, OneDrive, or SharePoint sources to an isolated agent knowledge base.",
        aliases=("kb", "knowledgebase", "knowledge_base", "kb_component"),
        config_keys=("knowledge_base", "knowledgeBase", "kb", "web_url", "webUrl", "web_urls", "webUrls", "sharepoint_folder_id", "sharepointFolderId", "sharepoint_folder_ids", "sharepointFolderIds"),
    ),
    "form_handler": ToolDefinition(
        id="form_handler",
        label="Form Builder",
        description="Render an inline form from custom-agent chat when user intent matches configured triggers.",
        aliases=("form", "forms", "form_handler", "form_handlers", "form_trigger"),
        config_keys=("form_handlers", "formHandlers", "forms", "form"),
    ),
}


def available_tool_definitions() -> list[dict[str, Any]]:
    return [tool.to_dict() for tool in TOOL_REGISTRY.values()]


def tool_aliases(tool_id: str) -> set[str]:
    tool = TOOL_REGISTRY.get(tool_id)
    if not tool:
        return set()
    return {tool.id, *tool.aliases}


def _safe_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", (value or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_").lower()
    return cleaned or "agent"


def company_kb_collection_id(company_id: int) -> str:
    return f"company_{company_id}_kb"


def APIComponent(
    company_id: int,
    agent_name: str,
    api_endpoints: list[dict[str, Any]] | list[str] | None = None,
    req_type: str = "GET",
) -> dict[str, str]:
    """Prepare API tool configuration for a custom agent.

    API endpoint details are intentionally optional until the final endpoint
    contract is agreed with the frontend and downstream systems.
    """
    method = str(req_type or "GET").strip().upper()
    if method not in VALID_REQUEST_TYPES:
        return {
            "status": "failure",
            "message": f"Unsupported request type '{req_type}'.",
        }

    endpoint_count = len(api_endpoints or [])
    suffix = f" with {endpoint_count} endpoint(s)" if endpoint_count else ""
    return {
        "status": "success",
        "message": f"API tool configured for {company_id}/{agent_name}{suffix}.",
    }


def KBComponent(
    company_id: int,
    agent_name: str,
    web_url: str | list[str] | None = None,
    onedrive_folder_id: str | None = None,
    onedrive_token: str | None = None,
) -> str:
    """Create or resolve a KB collection and return its collection_id.

    When QDRANT_URL is configured this creates the collection in Qdrant. In
    local/dev environments without Qdrant, the deterministic collection id is
    still returned so the agent can be recorded and wired up later.
    """
    collection_id = company_kb_collection_id(company_id)
    qdrant_url = os.getenv("QDRANT_URL")

    if not qdrant_url:
        return collection_id

    vector_size = int(os.getenv("OPENAI_EMBEDDING_DIMENSIONS") or os.getenv("QDRANT_VECTOR_SIZE", "1536"))
    client = QdrantClient(
        url=qdrant_url,
        api_key=os.getenv("QDRANT_API_KEY") or None,
    )

    try:
        existing = [item.name for item in client.get_collections().collections]
        if collection_id not in existing:
            client.create_collection(
                collection_name=collection_id,
                vectors_config=qdrant_models.VectorParams(
                    size=vector_size,
                    distance=qdrant_models.Distance.COSINE,
                ),
            )
    except Exception as e:
        print(f"Warning: Failed to connect to Qdrant at {qdrant_url}: {e}. Falling back to deterministic collection_id.")

    return collection_id


def selected_tool_names(payload: dict[str, Any]) -> set[str]:
    raw_tools = payload.get("tools")
    if raw_tools is None:
        raw_tools = (payload.get("configuration") or {}).get("tools", [])

    names: set[str] = set()
    for item in raw_tools or []:
        if isinstance(item, dict):
            item = item.get("name") or item.get("type") or item.get("id")
        normalized = re.sub(r"[^a-z0-9]+", "_", str(item or "").strip().lower()).strip("_")
        if normalized:
            names.add(normalized)

    return names


def tool_is_selected(tool_names: set[str], aliases: set[str], config: Any) -> bool:
    return bool(tool_names.intersection(aliases)) or bool(config)


def workflow_file_path(company_id: int, agent_name: str) -> Path:
    base_dir = Path(__file__).resolve().parent / "generated_workflows"
    filename = f"{company_id}_{_safe_identifier(agent_name)}.py"
    return base_dir / filename


def create_workflow_file(
    *,
    company_id: int,
    agent_name: str,
    tools: list[str],
    api_config: dict[str, Any] | None = None,
    mcp_config: dict[str, Any] | list[dict[str, Any]] | None = None,
    db_config: dict[str, Any] | list[dict[str, Any]] | None = None,
    kb_config: dict[str, Any] | None = None,
    form_config: dict[str, Any] | list[dict[str, Any]] | None = None,
    collection_id: str | None = None,
) -> Path:
    """Write a per-agent workflow module that can be imported and executed."""
    target = workflow_file_path(company_id, agent_name)
    target.parent.mkdir(parents=True, exist_ok=True)

    metadata = {
        "company_id": company_id,
        "agent_name": agent_name,
        "tools": tools,
        "api_config": api_config or {},
        "mcp_config": mcp_config or {},
        "db_config": db_config or {},
        "kb_config": kb_config or {},
        "form_config": form_config or [],
        "collection_id": collection_id,
    }

    metadata_literal = pformat(metadata, width=100, sort_dicts=True)

    content = f'''"""Generated workflow for {agent_name}.

This file is generated by the Agent Store backend when a custom agent is
created or deployed.
"""

from __future__ import annotations

from typing import Any


METADATA = {metadata_literal}


class GeneratedAgentWorkflow:
    """Executable descriptor for this custom agent's configured tools."""

    metadata = METADATA

    def run(self, state: dict[str, Any] | None = None) -> dict[str, Any]:
        state = dict(state or {{}})
        state["agent_name"] = self.metadata["agent_name"]
        state["company_id"] = self.metadata["company_id"]
        state["tools"] = list(self.metadata["tools"])

        if "api" in self.metadata["tools"]:
            state["api_tool"] = self.metadata["api_config"]

        if "database" in self.metadata["tools"]:
            state["database_tool"] = self.metadata["db_config"]

        if "knowledge_base" in self.metadata["tools"]:
            state["kb_collection_id"] = self.metadata["collection_id"]
            state["kb_tool"] = self.metadata["kb_config"]

        if "form_handler" in self.metadata["tools"]:
            state["form_tool"] = self.metadata["form_config"]

        return state


def run(state: dict[str, Any] | None = None) -> dict[str, Any]:
    return GeneratedAgentWorkflow().run(state)


def api_node(state):
    state = dict(state or {{}})
    state["api_tool"] = METADATA["api_config"]
    return state


def database_node(state):
    state = dict(state or {{}})
    state["database_tool"] = METADATA["db_config"]
    return state


def kb_node(state):
    state = dict(state or {{}})
    state["kb_collection_id"] = METADATA["collection_id"]
    state["kb_tool"] = METADATA["kb_config"]
    return state


def final_node(state):
    state = dict(state or {{}})
    state["agent_name"] = METADATA["agent_name"]
    state["company_id"] = METADATA["company_id"]
    return state


def form_node(state):
    state = dict(state or {{}})
    state["form_tool"] = METADATA["form_config"]
    return state


def build_workflow():
    try:
        from langgraph.graph import END, StateGraph
    except ImportError as exc:
        raise RuntimeError("Install langgraph to run generated agent workflows.") from exc

    graph = StateGraph(dict)
    nodes = []

    if "api" in METADATA["tools"]:
        graph.add_node("api", api_node)
        nodes.append("api")

    if "database" in METADATA["tools"]:
        graph.add_node("database", database_node)
        nodes.append("database")

    if "knowledge_base" in METADATA["tools"]:
        graph.add_node("knowledge_base", kb_node)
        nodes.append("knowledge_base")

    if "form_handler" in METADATA["tools"]:
        graph.add_node("form_handler", form_node)
        nodes.append("form_handler")

    graph.add_node("final", final_node)
    nodes.append("final")

    graph.set_entry_point(nodes[0])
    for left, right in zip(nodes, nodes[1:]):
        graph.add_edge(left, right)
    graph.add_edge("final", END)
    return graph.compile()
'''

    target.write_text(content, encoding="utf-8")
    return target
