from __future__ import annotations

import json
import re
from typing import Any

from agent_runtime_utils import clean_answer_text, clean_text, extract_json_object
from model_router import model_router
from openai_service import openai_service


def buildSystemPrompt(agent: dict[str, Any], forms: list[dict[str, Any]]) -> str:
    agent_name = agent.get("name") or "this custom agent"
    agent_purpose = (
        agent.get("purpose")
        or agent.get("description")
        or "Help users using only this agent's configured resources and tools."
    )
    form_lines = []
    for form in forms:
        form_lines.append(
            {
                "formId": form.get("id"),
                "title": form.get("title"),
                "triggerDescription": form.get("triggerDescription") or "No trigger description configured.",
                "triggerKeywords": form.get("triggerKeywords") or [],
                "confirmationMessage": form.get("confirmationMessage") or "Please complete the form below.",
            }
        )

    return f"""
You are {agent_name}.

Agent purpose:
{agent_purpose}

Configured forms:
{json.dumps(form_lines, indent=2)}

You must respond with exactly one JSON object in one of these two formats:
1. Plain message: {{"type": "message", "content": "string"}}
2. Form trigger: {{"type": "render_form", "formId": "string", "confirmationMessage": "string"}}

Only emit a render_form action when user intent clearly and confidently matches a form's trigger. Ask clarifying questions if intent is ambiguous. Never force a form.

Confidence gating:
- LOW: vague intent -> respond with type "message" and ask one concise clarifying question.
- MEDIUM: implied intent -> respond with type "message", briefly explain what the matching form does, and ask if the user wants to proceed.
- HIGH: clear explicit intent -> respond with type "render_form" immediately, using the matching formId and its confirmationMessage.

Guardrails:
- If multiple forms exist, use only the one whose triggerDescription best matches the user's intent.
- If trigger descriptions overlap, prefer asking a clarifying question instead of rendering a form.
- If the user dismissed a form, acknowledge that and offer to reopen it later.
- If a form has already been shown in this session, do not render it again unless the user explicitly re-requests it.
- If no form intent is matched, respond exactly: {{"type": "message", "content": "__NO_FORM_ACTION__"}}
- Do not include markdown fences, commentary, or extra keys.
""".strip()


class FormToolService:
    def configured_forms(self, agent_config: dict[str, Any]) -> list[dict[str, Any]]:
        configured_tools = agent_config.get("tools") or []
        normalized_tools = {
            str(item.get("type") if isinstance(item, dict) else item or "").strip().lower()
            for item in configured_tools
        }
        has_form_tool = "form_handler" in normalized_tools or "form" in normalized_tools or "forms" in normalized_tools

        raw_forms = (
            agent_config.get("form_handlers")
            or agent_config.get("formHandlers")
            or agent_config.get("forms")
            or agent_config.get("form")
            or []
        )
        if not raw_forms and configured_tools:
            raw_forms = [
                item.get("config") or {}
                for item in configured_tools
                if isinstance(item, dict)
                and str(item.get("type") or "").strip().lower() in {"form", "forms", "form_handler"}
            ]
        if configured_tools and not has_form_tool:
            return []
        if isinstance(raw_forms, dict):
            raw_forms = [raw_forms]
        if not isinstance(raw_forms, list):
            return []

        forms: list[dict[str, Any]] = []
        for form_index, form in enumerate(raw_forms):
            if not isinstance(form, dict):
                continue
            raw_fields = form.get("fields") or []
            if not isinstance(raw_fields, list):
                continue

            fields = []
            for field_index, field in enumerate(raw_fields):
                if not isinstance(field, dict):
                    continue
                label = str(field.get("label") or field.get("name") or "").strip()
                if not label:
                    continue
                field_type = str(field.get("type") or "Text").strip() or "Text"
                input_type = str(
                    field.get("inputType")
                    or field.get("input_type")
                    or field.get("controlType")
                    or field.get("control_type")
                    or field_type
                ).strip() or "text"
                data_type = str(field.get("dataType") or field.get("data_type") or "string").strip() or "string"
                raw_options = field.get("options") or []
                if isinstance(raw_options, str):
                    raw_options = [option.strip() for option in raw_options.split(",") if option.strip()]
                if not isinstance(raw_options, list):
                    raw_options = []
                required_value = str(field.get("required") or "").strip().lower()
                fields.append(
                    {
                        "name": field.get("name") or self.form_field_name(label, field_index),
                        "label": label,
                        "type": field_type,
                        "inputType": input_type,
                        "dataType": data_type,
                        "options": [str(option).strip() for option in raw_options if str(option).strip()],
                        "required": required_value in {"required", "true", "yes", "1"},
                        "placeholder": field.get("placeholder") or "",
                    }
                )

            if fields:
                trigger_keywords = form.get("triggerKeywords") or form.get("trigger_keywords") or []
                if isinstance(trigger_keywords, str):
                    trigger_keywords = [item.strip() for item in trigger_keywords.split(",") if item.strip()]
                if not isinstance(trigger_keywords, list):
                    trigger_keywords = []
                raw_submit_actions = form.get("submitActions") or form.get("submissionActions")
                if isinstance(raw_submit_actions, list):
                    submit_actions = [
                        str(action or "").strip().lower()
                        for action in raw_submit_actions
                        if str(action or "").strip()
                    ]
                else:
                    submit_action = str(form.get("submitAction") or form.get("submissionAction") or "email").strip().lower()
                    submit_actions = ["email", "api"] if submit_action == "both" else [submit_action]
                submit_actions = [action for action in dict.fromkeys(submit_actions) if action in {"email", "api"}] or ["email"]
                forms.append(
                    {
                        "id": f"form-{form_index}",
                        "title": form.get("title") or form.get("name") or "Request Form",
                        "description": form.get("description") or "",
                        "routing_profile": form.get("routing_profile") or form.get("routingProfile") or {},
                        "triggerDescription": form.get("triggerDescription") or form.get("trigger_description") or "",
                        "triggerKeywords": [str(item).strip() for item in trigger_keywords if str(item).strip()],
                        "confirmationMessage": form.get("confirmationMessage") or form.get("confirmation_message") or "Please complete the form below.",
                        "submitAction": submit_actions[0],
                        "submitActions": submit_actions,
                        "apiEndpoint": form.get("apiEndpoint") or form.get("api_endpoint") or "",
                        "fields": fields,
                    }
                )

        return forms

    def form_field_name(self, label: str, index: int) -> str:
        name = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
        return name or f"field_{index + 1}"

    def form_by_id(self, forms: list[dict[str, Any]], form_id: Any) -> dict[str, Any] | None:
        text = str(form_id or "").strip()
        if not text:
            return None
        return next((form for form in forms if str(form.get("id") or "") == text), None)

    def safe_client_form(self, form: dict[str, Any]) -> dict[str, Any]:
        form_payload = dict(form)
        form_payload.pop("apiEndpoint", None)
        form_payload.pop("api_endpoint", None)
        return form_payload

    def buildSystemPrompt(self, agent: dict[str, Any], forms: list[dict[str, Any]]) -> str:
        return buildSystemPrompt(agent, forms)

    def plan_action(
        self,
        *,
        api_key: str,
        agent_name: str,
        question: str,
        history: list[dict[str, str]],
        forms: list[dict[str, Any]],
        shown_form_ids: list[str],
        dismissed_form_ids: list[str],
        agent_config: dict[str, Any],
    ) -> dict[str, Any]:
        history_text = "\n".join(
            f"{item.get('role')}: {clean_text(item.get('content') or '')[:900]}"
            for item in history[-10:]
            if item.get("role") in {"user", "assistant", "tool_result"} and item.get("content")
        )
        agent = {
            "name": agent_name,
            "purpose": agent_config.get("purpose") or agent_config.get("description"),
        }
        response_text = openai_service.call_text(
            api_key=api_key,
            model=model_router.choose("form_plan"),
            instructions=buildSystemPrompt(agent, forms),
            input_messages=[
                {
                    "role": "user",
                    "content": (
                        f"Forms already shown this session: {json.dumps(shown_form_ids)}\n"
                        f"Forms dismissed this session: {json.dumps(dismissed_form_ids)}\n\n"
                        f"Recent conversation:\n{history_text or 'None'}\n\n"
                        f"Latest user message:\n{question}"
                    ),
                }
            ],
            max_output_tokens=350,
        )
        try:
            plan = json.loads(extract_json_object(response_text))
        except Exception:
            return {"type": "message", "content": clean_answer_text(response_text) or "__NO_FORM_ACTION__"}

        if not isinstance(plan, dict):
            return {"type": "message", "content": clean_answer_text(response_text) or "__NO_FORM_ACTION__"}
        if plan.get("type") == "render_form":
            return self.finalize_render_plan(
                plan=plan,
                question=question,
                history=history,
                forms=forms,
                shown_form_ids=shown_form_ids,
            )
        if plan.get("type") != "message":
            return {"type": "message", "content": "__NO_FORM_ACTION__"}
        if not isinstance(plan.get("content"), str):
            plan["content"] = "__NO_FORM_ACTION__"
        return plan

    def finalize_render_plan(
        self,
        *,
        plan: dict[str, Any],
        question: str,
        history: list[dict[str, str]],
        forms: list[dict[str, Any]],
        shown_form_ids: list[str],
    ) -> dict[str, Any]:
        """Apply deterministic form re-open rules to a selected form tool."""
        form_id = str(plan.get("formId") or "").strip()
        if not self.form_by_id(forms, form_id):
            return {"type": "message", "content": "__NO_FORM_ACTION__"}
        already_shown = form_id in {str(item) for item in shown_form_ids}
        latest_message = clean_text(question).lower()
        recent_assistant_text = " ".join(
            clean_text(item.get("content") or "").lower()
            for item in history[-4:]
            if item.get("role") == "assistant" and item.get("content")
        )
        form_offer_was_recent = any(
            phrase in recent_assistant_text
            for phrase in {
                "would you like me to provide the form",
                "would you like me to show the form",
                "would you like to proceed",
                "do you want to proceed",
                "do you want me to show",
                "provide the form",
                "show the form",
                "complete the form",
                "fill out the form",
            }
        )
        confirmation_reopen = latest_message in {
            "yes",
            "yeah",
            "yep",
            "sure",
            "ok",
            "okay",
            "please",
            "please do",
            "go ahead",
            "proceed",
            "continue",
        } and form_offer_was_recent
        explicit_reopen = any(
            phrase in latest_message
            for phrase in {
                "reopen",
                "show it",
                "show it again",
                "show form",
                "show the form",
                "show the form again",
                "open it",
                "open it again",
                "open form",
                "open the form",
                "open the form again",
                "reopen the form",
                "fill it out",
                "fill the form",
                "provide the form",
            }
        ) or confirmation_reopen
        if already_shown and not explicit_reopen:
            return {
                "type": "message",
                "content": "I already showed that form. I can reopen it if you want to fill it out again.",
            }
        if not plan.get("confirmationMessage"):
            matched_form = self.form_by_id(forms, form_id) or {}
            plan["confirmationMessage"] = matched_form.get("confirmationMessage") or "Please complete the form below."
        return plan


form_tool_service = FormToolService()
