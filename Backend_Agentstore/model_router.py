from __future__ import annotations

import os


SMALL_MODEL_DEFAULT = "gpt-4o-mini"
LARGE_MODEL_DEFAULT = "gpt-4o-mini"
DEPRECATED_MODEL_ALIASES = {
    "gpt-4.1-nano": "gpt-4o-mini",
}

SMALL_TASKS = {
    "chat_title",
    "form_plan",
    "kb_answer",
}

LARGE_TASKS = {
    "tool_plan",
    "api_plan",
    "api_response_extract",
    "mcp_plan",
}


def _configured_model(env_name: str, default: str) -> str:
    value = os.getenv(env_name, "").strip()
    value = DEPRECATED_MODEL_ALIASES.get(value, value)
    return value or default


class ModelRouter:
    def small_model(self) -> str:
        return _configured_model("OPENAI_SMALL_MODEL", SMALL_MODEL_DEFAULT)

    def large_model(self) -> str:
        legacy_model = os.getenv("OPENAI_MODEL", "").strip() or LARGE_MODEL_DEFAULT
        return _configured_model("OPENAI_LARGE_MODEL", legacy_model)

    def default_model(self) -> str:
        return _configured_model("OPENAI_DEFAULT_MODEL", self.small_model())

    def choose(self, task_type: str, *, has_tool_results: bool = False) -> str:
        task = (task_type or "").strip().lower()
        if task == "agent_answer":
            return self.large_model() if has_tool_results else self.small_model()
        if task in LARGE_TASKS:
            return self.large_model()
        if task in SMALL_TASKS:
            return self.small_model()
        return self.default_model()


model_router = ModelRouter()
