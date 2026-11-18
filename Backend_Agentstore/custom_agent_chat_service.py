from __future__ import annotations

import json
import os
import re
from typing import Any

from api_tool_service import api_tool_service
from agent_runtime_utils import clean_text, is_assigned_resource_refusal, is_general_chat
from auth_sqlite import get_conn
from database_tool_audit_service import (
    CANCELLED,
    EXECUTED,
    FAILED,
    database_tool_audit_service,
)
from database_tool_service import database_tool_service
from form_tool_service import form_tool_service
from mcp_tool_service import mcp_tool_service
from model_router import model_router
from openai_service import openai_service
from retrieval_service import retrieval_service
from tool_audit_service import (
    EXECUTED as TOOL_EXECUTED,
    FAILED as TOOL_FAILED,
    PENDING_USER_CONFIRMATION as TOOL_PENDING_USER_CONFIRMATION,
    tool_audit_service,
)
from tool_observability import ToolTrace, tool_data_redactor
from tool_orchestrator import tool_orchestrator
from tool_policy_service import tool_policy_service
from tool_registry import ToolRegistry
from tool_types import ToolSpec
from tool_validation import tool_argument_validator


DATABASE_CONFIRMATION_COMMAND_RE = re.compile(
    (
        r"^\s*(run|confirm|yes|continue|approve|execute|cancel|reject|no|stop)\s+"
        r"(?:(?:database\s+)?(?:write|query|change)\s*)#?(\d+)\s*$"
    ),
    flags=re.I,
)
TOOL_CONFIRMATION_COMMAND_RE = re.compile(
    (
        r"^\s*(run|confirm|yes|continue|approve|execute|cancel|reject|no|stop)\s+"
        r"(?:tool(?:\s+(?:action|call))?|action|call)\s*#?(\d+)\s*$"
    ),
    flags=re.I,
)
MAX_TOOL_CALLS_PER_TURN = 3


class CustomAgentChatService:
    @staticmethod
    def is_database_confirmation_command(question: str | None) -> bool:
        return bool(DATABASE_CONFIRMATION_COMMAND_RE.match(question or ""))

    @staticmethod
    def is_tool_confirmation_command(question: str | None) -> bool:
        return bool(TOOL_CONFIRMATION_COMMAND_RE.match(question or ""))

    def is_confirmation_command(self, question: str | None) -> bool:
        return self.is_database_confirmation_command(question) or self.is_tool_confirmation_command(question)

    def chat_custom_agent(
        self,
        *,
        company_id: int,
        agent_id: int,
        agent_name: str,
        user_id: int | None = None,
        chat_session_id: int | None = None,
        agent_config: dict[str, Any] | None = None,
        question: str,
        history: list[dict[str, str]] | None = None,
        shown_form_ids: list[str] | None = None,
        dismissed_form_ids: list[str] | None = None,
        database_profile_index: int | None = None,
        database_profile_name: str | None = None,
        response_mode: str = "text",
        top_k: int = 5,
    ) -> dict[str, Any]:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        agent_config = agent_config or {}
        history = history or []
        confirmation_response = self.handle_tool_confirmation(
            company_id=company_id,
            agent_id=agent_id,
            user_id=user_id,
            agent_config=agent_config,
            question=question,
        )
        if confirmation_response:
            return confirmation_response
        confirmation_response = self.handle_database_confirmation(
            company_id=company_id,
            agent_id=agent_id,
            user_id=user_id,
            agent_config=agent_config,
            question=question,
        )
        if confirmation_response:
            return confirmation_response

        if question == "__tool_result__":
            last_tool_result = next(
                (
                    item.get("content")
                    for item in reversed(history)
                    if item.get("role") == "tool_result" and item.get("content")
                ),
                "",
            )
            return {
                "type": "message",
                "answer": "Thanks, the form submission was completed. I can help with anything else you need.",
                "sources": [],
                "api_results": [],
                "quick_replies": [],
                "tool_result": last_tool_result,
            }

        general_chat = is_general_chat(question)
        sources: list[dict[str, Any]] = []
        context_blocks: list[str] = []
        api_results: list[dict[str, Any]] = []
        configured_forms = form_tool_service.configured_forms(agent_config)
        api_endpoints = api_tool_service.configured_endpoints(agent_config)
        db_profiles = database_tool_service.configured_profiles(agent_config)
        mcp_servers = mcp_tool_service.configured_servers(agent_config)
        database_summaries: list[dict[str, Any]] = []
        database_preliminary_plan = None
        if db_profiles and not general_chat:
            database_summaries, database_preliminary_plan = database_tool_service.planning_profile_summaries(
                profiles=db_profiles,
                question=question,
                selected_profile_index=database_profile_index,
                selected_profile_name=database_profile_name,
            )
            if database_preliminary_plan:
                permission_message = self.database_plan_permission_message(database_preliminary_plan, db_profiles)
                if permission_message:
                    return self._message(permission_message)

        registry = ToolRegistry.from_configured_resources(
            has_knowledge_base=self.has_knowledge_base(agent_config),
            knowledge_base_routing_profile=self.knowledge_base_routing_profile(agent_config),
            forms=configured_forms,
            api_endpoints=api_endpoints,
            database_summaries=database_summaries,
            mcp_servers=mcp_servers,
            shown_form_ids=shown_form_ids,
            dismissed_form_ids=dismissed_form_ids,
        )
        tool_trace = ToolTrace()
        execution_history: list[dict[str, Any]] = []
        attempted_calls: set[str] = set()
        if not general_chat:
            for _ in range(MAX_TOOL_CALLS_PER_TURN):
                orchestration_plan = tool_orchestrator.plan(
                    api_key=api_key,
                    agent_name=agent_name,
                    question=question,
                    history=history,
                    registry=registry,
                    execution_history=list(execution_history),
                )
                tool_trace.add(
                    "plan",
                    action=orchestration_plan.get("action"),
                    tool_id=orchestration_plan.get("tool_id"),
                    reason=orchestration_plan.get("reason"),
                )
                if (
                    orchestration_plan.get("action") == "needs_input"
                    and orchestration_plan.get("tool_id") != "knowledge_base:search"
                ):
                    tool_trace.add("stop", reason="needs_input")
                    return self.with_tool_trace(
                        self.needs_input_response(orchestration_plan, api_results=api_results),
                        tool_trace,
                    )
                selected_tool = registry.get(orchestration_plan.get("tool_id"))
                if orchestration_plan.get("action") != "call_tool" or not selected_tool:
                    knowledge_base_tool = registry.get("knowledge_base:search")
                    knowledge_base_was_searched = any(
                        item.get("tool_id") == "knowledge_base:search"
                        for item in execution_history
                    )
                    if knowledge_base_tool and not knowledge_base_was_searched:
                        selected_tool = knowledge_base_tool
                        tool_arguments = {"query": question}
                        tool_trace.add(
                            "fallback",
                            reason="knowledge_base_not_searched",
                            tool_id=knowledge_base_tool.id,
                        )
                    else:
                        tool_trace.add("stop", reason=orchestration_plan.get("reason") or "no_tool")
                        break
                else:
                    tool_arguments = (
                        orchestration_plan.get("arguments")
                        if isinstance(orchestration_plan.get("arguments"), dict)
                        else {}
                    )
                call_signature = self.tool_call_signature(selected_tool, tool_arguments)
                if call_signature in attempted_calls:
                    tool_trace.add("stop", reason="duplicate_tool_call", tool_id=selected_tool.id)
                    break
                attempted_calls.add(call_signature)
                tool_trace.add(
                    "tool_call",
                    tool_id=selected_tool.id,
                    tool_kind=selected_tool.kind,
                    side_effect=selected_tool.side_effect,
                    arguments=tool_arguments,
                )

                outcome = self.execute_planned_tool(
                    company_id=company_id,
                    agent_id=agent_id,
                    user_id=user_id,
                    chat_session_id=chat_session_id,
                    question=question,
                    history=history,
                    shown_form_ids=shown_form_ids or [],
                    source_offset=len(sources),
                    top_k=top_k,
                    tool=selected_tool,
                    arguments=tool_arguments,
                    configured_forms=configured_forms,
                    api_endpoints=api_endpoints,
                    db_profiles=db_profiles,
                    mcp_servers=mcp_servers,
                )
                terminal_response = outcome.get("terminal_response")
                if isinstance(terminal_response, dict):
                    tool_trace.add("stop", reason="terminal_tool_response", tool_id=selected_tool.id)
                    return self.with_tool_trace(terminal_response, tool_trace)
                api_result = outcome.get("api_result")
                safe_api_result = tool_data_redactor.redact(api_result) if isinstance(api_result, dict) else None
                if isinstance(api_result, dict):
                    api_results.append(safe_api_result)
                outcome_context = outcome.get("context_blocks")
                if isinstance(outcome_context, list):
                    context_blocks.extend(outcome_context)
                outcome_sources = outcome.get("sources")
                if isinstance(outcome_sources, list):
                    sources.extend(outcome_sources)
                execution_history.append(
                    {
                        "tool_id": selected_tool.id,
                        "tool_name": selected_tool.name,
                        "arguments": tool_data_redactor.redact(tool_arguments),
                        "result": tool_data_redactor.redact(
                            outcome.get("planner_result") or api_result or {"ok": True}
                        ),
                    }
                )
                tool_trace.add(
                    "tool_result",
                    tool_id=selected_tool.id,
                    result=tool_data_redactor.summarize_result(api_result or outcome.get("planner_result")),
                )
            else:
                tool_trace.add("stop", reason="tool_call_limit_reached", limit=MAX_TOOL_CALLS_PER_TURN)
        else:
            tool_trace.add("stop", reason="general_chat")

        answer = (
            (
                self.format_database_select_answer(question, api_results)
                if response_mode != "voice" and len(api_results) == 1
                else None
            )
            or self.generate_openai_answer(
                api_key=api_key,
                agent_name=agent_name,
                question=question,
                history=history,
                context_blocks=context_blocks,
                api_results=api_results,
                response_mode=response_mode,
            )
        )
        visible_sources = (
            self.dedupe_sources(sources)
            if self.answer_used_knowledge_base(
                api_key=api_key,
                question=question,
                answer=answer,
                context_blocks=context_blocks,
                api_results=api_results,
            )
            else []
        )
        return self.with_tool_trace({
            "type": "message",
            "answer": answer,
            "sources": visible_sources,
            "api_results": api_results,
            "quick_replies": [],
        }, tool_trace)

    def execute_planned_tool(
        self,
        *,
        company_id: int,
        agent_id: int,
        user_id: int | None,
        chat_session_id: int | None,
        question: str,
        history: list[dict[str, str]],
        shown_form_ids: list[str],
        source_offset: int,
        top_k: int,
        tool: ToolSpec,
        arguments: dict[str, Any],
        configured_forms: list[dict[str, Any]],
        api_endpoints: list[dict[str, Any]],
        db_profiles: list[dict[str, Any]],
        mcp_servers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if tool.kind == "form":
            form_plan = form_tool_service.finalize_render_plan(
                plan={"type": "render_form", "formId": tool.target.get("form_id")},
                question=question,
                history=history,
                forms=configured_forms,
                shown_form_ids=shown_form_ids,
            )
            form_response_type = str(form_plan.get("type") or "message").lower()
            if form_response_type == "render_form":
                form_payload = form_tool_service.form_by_id(configured_forms, form_plan.get("formId"))
                if not form_payload and configured_forms:
                    form_payload = configured_forms[0]
                if not form_payload:
                    return {"planner_result": {"type": "form", "ok": False, "error": "Form not found."}}
                form_payload = form_tool_service.safe_client_form(form_payload)
                confirmation = (
                    form_plan.get("confirmationMessage")
                    or form_payload.get("confirmationMessage")
                    or "Please complete the form below."
                )
                return {
                    "terminal_response": {
                        "type": "render_form",
                        "answer": confirmation,
                        "sources": [],
                        "api_results": [],
                        "form_id": form_payload.get("id"),
                        "confirmationMessage": confirmation,
                        "form": form_payload,
                        "quick_replies": [],
                    }
                }
            form_content = str(form_plan.get("content") or "").strip()
            if form_content and form_content != "__NO_FORM_ACTION__":
                return {"terminal_response": self._message(form_content)}
            return {"planner_result": {"type": "form", "ok": False, "status": "not_rendered"}}

        if tool.kind == "knowledge_base":
            query = str(arguments.get("query") or question)
            try:
                points = retrieval_service.search_points(
                    company_id=company_id,
                    agent_id=agent_id,
                    question=query,
                    top_k=top_k,
                )
            except Exception as exc:
                print(f"DEBUG: Knowledge base retrieval failed: {exc}")
                return {
                    "terminal_response": {
                        "type": "message",
                        "answer": (
                            "I can't access this agent's knowledge base right now. "
                            "Please try again after the knowledge-base service is available."
                        ),
                        "sources": [],
                        "api_results": [],
                        "quick_replies": [],
                        "service_error": "knowledge_base_unavailable",
                    }
                }
            context_blocks: list[str] = []
            sources: list[dict[str, Any]] = []
            planner_matches: list[dict[str, Any]] = []
            for index, point in enumerate(points, start=source_offset + 1):
                payload = point.payload or {}
                text = clean_text(payload.get("text") or "")
                if not text:
                    continue
                title = payload.get("document_title") or payload.get("document_id") or f"Source {index}"
                context_blocks.append(f"[{index}] {title}\n{text}")
                sources.append(payload)
                planner_matches.append({"title": title, "text": text[:700]})
            return {
                "context_blocks": context_blocks,
                "sources": sources,
                "planner_result": {
                    "type": "knowledge_base",
                    "ok": True,
                    "query": query,
                    "match_count": len(planner_matches),
                    "matches": planner_matches[:5],
                },
            }

        if tool.kind == "api":
            if tool_policy_service.requires_approval(tool):
                return {
                    "terminal_response": self.create_tool_confirmation_response(
                        company_id=company_id,
                        agent_id=agent_id,
                        user_id=user_id,
                        chat_session_id=chat_session_id,
                        tool=tool,
                        arguments=arguments,
                        question=question,
                    )
                }
            api_plan = {
                "endpoint_index": tool.target.get("endpoint_index"),
                "parameters": arguments,
            }
            try:
                result = api_tool_service.execute_configured_call(api_endpoints, api_plan)
            except Exception as exc:
                result = {
                    "type": "api",
                    "ok": False,
                    "endpoint_index": api_plan.get("endpoint_index"),
                    "error": str(exc),
                }
            return {"api_result": result, "planner_result": result}

        if tool.kind == "database":
            database_plan = {
                "profile_index": tool.target.get("profile_index"),
                "sql": arguments.get("sql"),
            }
            prepared = None
            try:
                prepared = database_tool_service.prepare_configured_query(db_profiles, database_plan)
                operation = prepared["operation"]
                if database_tool_service.write_requires_approval(operation):
                    impact = database_tool_service.estimate_write_impact(
                        prepared["profile"],
                        prepared["sql"],
                        operation,
                    )
                    with get_conn() as conn:
                        approval = database_tool_audit_service.create_pending_confirmation(
                            conn,
                            company_id=company_id,
                            agent_id=agent_id,
                            user_id=user_id,
                            chat_session_id=chat_session_id,
                            profile=prepared["profile"],
                            operation=operation,
                            sql=prepared["sql"],
                            question=question,
                            estimated_affected_rows=impact.get("estimated_affected_rows"),
                            preview_error=impact.get("preview_error"),
                        )
                        conn.commit()
                    return {
                        "terminal_response": self.database_write_confirmation_response(
                            approval_id=approval["id"],
                            profile=prepared["profile"],
                            operation=operation,
                            sql=prepared["sql"],
                            estimated_affected_rows=impact.get("estimated_affected_rows"),
                            preview_error=impact.get("preview_error"),
                            max_affected_rows=approval["max_affected_rows"],
                        )
                    }
                result = database_tool_service.execute_configured_query(db_profiles, database_plan)
                with get_conn() as conn:
                    database_tool_audit_service.create_activity(
                        conn,
                        company_id=company_id,
                        agent_id=agent_id,
                        user_id=user_id,
                        chat_session_id=chat_session_id,
                        profile=prepared["profile"],
                        operation=operation,
                        sql=prepared["sql"],
                        question=question,
                        status=EXECUTED,
                        result=result,
                    )
                    conn.commit()
            except Exception as exc:
                try:
                    if prepared:
                        with get_conn() as conn:
                            database_tool_audit_service.create_activity(
                                conn,
                                company_id=company_id,
                                agent_id=agent_id,
                                user_id=user_id,
                                chat_session_id=chat_session_id,
                                profile=prepared["profile"],
                                operation=prepared["operation"],
                                sql=prepared["sql"],
                                question=question,
                                status=FAILED,
                                error_message=str(exc),
                            )
                            conn.commit()
                except Exception as audit_exc:
                    print(f"DEBUG: Failed to audit database tool error: {audit_exc}")
                result = {
                    "type": "database",
                    "ok": False,
                    "profile_index": database_plan.get("profile_index"),
                    "error": str(exc),
                }
            return {"api_result": result, "planner_result": result}

        if tool.kind == "mcp":
            if tool_policy_service.requires_approval(tool):
                return {
                    "terminal_response": self.create_tool_confirmation_response(
                        company_id=company_id,
                        agent_id=agent_id,
                        user_id=user_id,
                        chat_session_id=chat_session_id,
                        tool=tool,
                        arguments=arguments,
                        question=question,
                    )
                }
            mcp_plan = {
                "server_index": tool.target.get("server_index"),
                "tool_name": tool.target.get("tool_name"),
                "arguments": arguments,
            }
            try:
                result = mcp_tool_service.execute_configured_call(mcp_servers, mcp_plan)
            except Exception as exc:
                result = {
                    "type": "mcp",
                    "ok": False,
                    "server_index": mcp_plan.get("server_index"),
                    "tool_name": mcp_plan.get("tool_name"),
                    "error": str(exc),
                }
            return {"api_result": result, "planner_result": result}

        return {
            "planner_result": {
                "ok": False,
                "error": f"Unsupported tool kind: {tool.kind}",
            }
        }

    @staticmethod
    def tool_call_signature(tool: ToolSpec, arguments: dict[str, Any]) -> str:
        return json.dumps(
            {"tool_id": tool.id, "arguments": arguments},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    def handle_tool_confirmation(
        self,
        *,
        company_id: int,
        agent_id: int,
        user_id: int | None,
        agent_config: dict[str, Any],
        question: str,
    ) -> dict[str, Any] | None:
        if not user_id:
            return None
        match = TOOL_CONFIRMATION_COMMAND_RE.match(question or "")
        if not match:
            return None

        action = match.group(1).lower()
        activity_id = int(match.group(2))
        with get_conn() as conn:
            activity = tool_audit_service.get_for_user(
                conn,
                company_id=company_id,
                agent_id=agent_id,
                user_id=user_id,
                activity_id=activity_id,
            )
            if not activity:
                return self._message("I could not find that pending tool action for your account.")
            if activity["status"] != TOOL_PENDING_USER_CONFIRMATION:
                return self._message("That tool action is no longer waiting for confirmation.")
            if action in {"cancel", "reject", "no", "stop"}:
                tool_audit_service.mark_user_cancelled(
                    conn,
                    activity_id=activity_id,
                    company_id=company_id,
                    user_id=user_id,
                    reason="Cancelled by requesting user",
                )
                conn.commit()
                return self._message(f"Cancelled tool action #{activity_id}. I did not run it.")
            claimed = tool_audit_service.mark_user_executing(
                conn,
                activity_id=activity_id,
                company_id=company_id,
                user_id=user_id,
            )
            conn.commit()
        if not claimed:
            return self._message("That tool action is no longer waiting for confirmation.")

        api_endpoints = api_tool_service.configured_endpoints(agent_config)
        mcp_servers = mcp_tool_service.configured_servers(agent_config)
        registry = ToolRegistry.from_configured_resources(
            has_knowledge_base=False,
            forms=[],
            api_endpoints=api_endpoints,
            database_summaries=[],
            mcp_servers=mcp_servers,
        )
        tool = registry.get(activity.get("tool_id"))
        if not tool or tool.configuration_fingerprint != activity.get("configuration_fingerprint"):
            self._mark_tool_confirmation_failed(
                activity_id=activity_id,
                company_id=company_id,
                error_message="Tool configuration changed after confirmation was requested.",
            )
            return self._message(
                f"I did not run tool action #{activity_id} because its configuration changed. Please submit the request again."
            )

        raw_execution_arguments = activity.get("execution_arguments", activity.get("arguments"))
        arguments = raw_execution_arguments if isinstance(raw_execution_arguments, dict) else {}
        validation = tool_argument_validator.validate(tool, arguments)
        if not validation.valid:
            self._mark_tool_confirmation_failed(
                activity_id=activity_id,
                company_id=company_id,
                error_message="Stored tool arguments no longer match the configured input schema.",
            )
            return self._message(
                f"I did not run tool action #{activity_id} because its saved inputs are no longer valid. Please submit the request again."
            )

        try:
            result = self.execute_approved_tool(
                tool=tool,
                arguments=arguments,
                api_endpoints=api_endpoints,
                mcp_servers=mcp_servers,
            )
        except Exception as exc:
            self._mark_tool_confirmation_failed(
                activity_id=activity_id,
                company_id=company_id,
                error_message=str(exc),
            )
            return self._message(f"I tried to run tool action #{activity_id}, but it failed: {exc}")

        with get_conn() as conn:
            tool_audit_service.mark_execution_result(
                conn,
                activity_id=activity_id,
                company_id=company_id,
                status=TOOL_EXECUTED,
                result=result,
            )
            conn.commit()
        return {
            "type": "message",
            "answer": f"Tool action #{activity_id} completed successfully.",
            "sources": [],
            "api_results": [tool_data_redactor.redact(result)],
            "quick_replies": [],
        }

    def create_tool_confirmation_response(
        self,
        *,
        company_id: int,
        agent_id: int,
        user_id: int | None,
        chat_session_id: int | None,
        tool: ToolSpec,
        arguments: dict[str, Any],
        question: str,
    ) -> dict[str, Any]:
        if not user_id:
            response = self._message(
                "This tool action can change external data and requires confirmation in an authenticated workspace chat."
            )
            response["service_error"] = "tool_confirmation_requires_authenticated_user"
            return response
        with get_conn() as conn:
            approval = tool_audit_service.create_pending_confirmation(
                conn,
                company_id=company_id,
                agent_id=agent_id,
                user_id=user_id,
                chat_session_id=chat_session_id,
                tool=tool,
                arguments=arguments,
                question=question,
            )
            conn.commit()
        approval_id = approval["id"]
        run_command = f"Run Tool #{approval_id}"
        cancel_command = f"Cancel Tool #{approval_id}"
        kind_label = tool.kind.upper()
        safe_arguments = tool_data_redactor.redact(arguments)
        arguments_text = json.dumps(safe_arguments, indent=2, default=str)
        answer = "\n".join(
            [
                "Review Tool Action",
                "",
                f"This {kind_label} action can change data in an external system. I have not run it yet.",
                "",
                f"Tool: {tool.name}",
                "",
                "Arguments:",
                f"```json\n{arguments_text}\n```",
                "",
                "Choose Run Tool to continue, or Cancel Tool to leave the external system unchanged.",
            ]
        )
        return {
            "type": "message",
            "answer": answer,
            "sources": [],
            "api_results": [
                {
                    "type": tool.kind,
                    "ok": True,
                    "status": TOOL_PENDING_USER_CONFIRMATION,
                    "approval_id": approval_id,
                    "tool_id": tool.id,
                    "tool_name": tool.name,
                    "requires_confirmation": True,
                    "confirmation_command": run_command,
                    "cancellation_command": cancel_command,
                    "request": {"arguments": safe_arguments},
                }
            ],
            "quick_replies": [run_command, cancel_command],
        }

    def execute_approved_tool(
        self,
        *,
        tool: ToolSpec,
        arguments: dict[str, Any],
        api_endpoints: list[dict[str, Any]],
        mcp_servers: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if tool.kind == "api":
            return api_tool_service.execute_configured_call(
                api_endpoints,
                {"endpoint_index": tool.target.get("endpoint_index"), "parameters": arguments},
            )
        if tool.kind == "mcp":
            return mcp_tool_service.execute_configured_call(
                mcp_servers,
                {
                    "server_index": tool.target.get("server_index"),
                    "tool_name": tool.target.get("tool_name"),
                    "arguments": arguments,
                },
            )
        raise RuntimeError("The approved tool type is not executable through this confirmation flow.")

    def _mark_tool_confirmation_failed(
        self,
        *,
        activity_id: int,
        company_id: int,
        error_message: str,
    ) -> None:
        with get_conn() as conn:
            tool_audit_service.mark_execution_result(
                conn,
                activity_id=activity_id,
                company_id=company_id,
                status=TOOL_FAILED,
                error_message=error_message,
            )
            conn.commit()

    def handle_database_confirmation(
        self,
        *,
        company_id: int,
        agent_id: int,
        user_id: int | None,
        agent_config: dict[str, Any],
        question: str,
    ) -> dict[str, Any] | None:
        if not user_id:
            return None
        match = DATABASE_CONFIRMATION_COMMAND_RE.match(question or "")
        if not match:
            return None

        action = match.group(1).lower()
        activity_id = int(match.group(2))
        with get_conn() as conn:
            activity = database_tool_audit_service.get_for_user(
                conn,
                company_id=company_id,
                agent_id=agent_id,
                user_id=user_id,
                activity_id=activity_id,
            )
            if not activity:
                return self._message("I could not find that pending database write for your account.")
            if activity["status"] != "pending_user_confirmation":
                return self._message("That database write is no longer waiting for confirmation.")
            if action in {"cancel", "reject", "no"}:
                database_tool_audit_service.mark_user_cancelled(
                    conn,
                    activity_id=activity_id,
                    company_id=company_id,
                    user_id=user_id,
                    reason="Cancelled by requesting user",
                )
                conn.commit()
                return self._message(f"Cancelled database write #{activity_id}. I did not make any database changes.")
            claimed = database_tool_audit_service.mark_user_executing(
                conn,
                activity_id=activity_id,
                company_id=company_id,
                user_id=user_id,
            )
            conn.commit()
        if not claimed:
            return self._message("That database write is no longer waiting for confirmation.")

        profiles = database_tool_service.configured_profiles(agent_config)
        plan = {"profile_index": activity["profile_index"], "sql": activity["sql_text"]}
        try:
            result = database_tool_service.execute_configured_query(profiles, plan)
        except Exception as exc:
            with get_conn() as conn:
                database_tool_audit_service.mark_execution_result(
                    conn,
                    approval_id=activity_id,
                    company_id=company_id,
                    status=FAILED,
                    error_message=str(exc),
                )
                conn.commit()
            return self._message(f"I tried to run database write #{activity_id}, but it failed: {exc}")

        affected_rows = (result.get("response") or {}).get("affected_rows")
        with get_conn() as conn:
            database_tool_audit_service.mark_execution_result(
                conn,
                approval_id=activity_id,
                company_id=company_id,
                status=EXECUTED,
                result=result,
            )
            conn.commit()
        operation_label = str(activity["operation"] or "write").upper()
        return self._message(
            (
                f"Database write #{activity_id} completed. "
                f"Operation: {operation_label}. "
                f"Affected rows: {affected_rows if affected_rows is not None else 'unknown'}."
            )
        )

    def database_plan_permission_message(
        self,
        plan: dict[str, Any],
        profiles: list[dict[str, Any]],
    ) -> str | None:
        if plan.get("reason") != "database_operation_not_allowed":
            return None
        requested_operation = str(plan.get("requested_operation") or "").upper()
        allowed_operations = [
            str(operation or "").upper()
            for operation in (plan.get("allowed_operations") or [])
            if str(operation or "").strip()
        ]
        plan_profile_index = plan.get("profile_index")
        scoped_profiles = profiles
        if plan_profile_index is not None:
            scoped_profiles = [
                profile for profile in profiles
                if str(profile.get("index")) == str(plan_profile_index)
            ] or profiles
        profile_names = ", ".join(profile.get("name") or "Database" for profile in scoped_profiles) or "Database"
        allowed_text = ", ".join(allowed_operations) if allowed_operations else "no operations"
        return (
            f"The selected database profile ({profile_names}) is configured to allow {allowed_text}. "
            f"Your request needs {requested_operation}. "
            "Choose a profile that allows this operation, or update the database tool Permission Mode and DB role grants."
        )

    def database_write_confirmation_response(
        self,
        *,
        approval_id: int,
        profile: dict[str, Any],
        operation: str,
        sql: str,
        estimated_affected_rows: int | None,
        preview_error: str | None,
        max_affected_rows: int,
    ) -> dict[str, Any]:
        operation_label = operation.upper()
        risk_labels = {
            "insert": "add new rows",
            "update": "modify existing rows",
            "delete": "delete rows",
        }
        risk_label = risk_labels.get(operation.lower(), "change data")
        estimate = "unknown"
        if estimated_affected_rows is not None:
            estimate = str(estimated_affected_rows)
        run_command = f"Run Query #{approval_id}"
        cancel_command = f"Cancel Query #{approval_id}"
        lines = [
            "Review Database Change",
            "",
            f"This {operation_label} query can {risk_label}. I have not run it yet.",
            "",
            f"Database Profile: {profile.get('name') or 'Database'}",
            f"Estimated Affected Rows: {estimate}",
            f"Affected-Row Limit: {max_affected_rows}",
        ]
        if preview_error:
            lines.append(f"Preview Note: {preview_error}")
        lines.extend(["", "Choose Run Query to continue, or Cancel to leave the database unchanged."])
        lines.extend(["", "SQL:", f"```sql\n{sql}\n```"])
        return {
            "type": "message",
            "answer": "\n".join(lines),
            "sources": [],
            "api_results": [
                {
                    "type": "database",
                    "ok": True,
                    "status": "pending_user_confirmation",
                    "approval_id": approval_id,
                    "profile_index": profile["index"],
                    "profile_name": profile["name"],
                    "engine": profile["engine"],
                    "operation": operation,
                    "requires_confirmation": True,
                    "risk_label": risk_label,
                    "confirmation_command": run_command,
                    "cancellation_command": cancel_command,
                    "request": {"sql": sql},
                    "response": {
                        "estimated_affected_rows": estimated_affected_rows,
                        "preview_error": preview_error,
                        "max_affected_rows": max_affected_rows,
                    },
                }
            ],
            "quick_replies": [
                run_command,
                cancel_command,
            ],
        }

    def format_database_select_answer(self, question: str, api_results: list[dict[str, Any]]) -> str | None:
        database_results = [
            result
            for result in api_results or []
            if result.get("type") == "database"
            and result.get("ok") is True
            and str(result.get("operation") or "").lower() == "select"
        ]
        if not database_results:
            return None

        response = database_results[0].get("response") or {}
        columns = [str(column) for column in response.get("columns") or [] if str(column or "").strip()]
        rows = response.get("rows") if isinstance(response.get("rows"), list) else []
        if not columns:
            return None

        label = self._database_result_label(question, columns)
        if not rows:
            return f"No {label} found."

        display_rows = rows[:25]
        lines = [f"Here are {label}:"]
        if len(columns) == 1:
            column = columns[0]
            for row in display_rows:
                lines.append(f"- {self._format_database_cell(row.get(column))}")
        else:
            headers = [self._escape_markdown_table_cell(self._humanize_database_column(column)) for column in columns]
            lines.append(f"| {' | '.join(headers)} |")
            lines.append(f"| {' | '.join(['---'] * len(headers))} |")
            for row in display_rows:
                cells = [
                    self._escape_markdown_table_cell(self._format_database_cell(row.get(column)))
                    for column in columns
                ]
                lines.append(f"| {' | '.join(cells)} |")

        total_rows = response.get("row_count")
        truncated = bool(response.get("truncated"))
        if truncated or (isinstance(total_rows, int) and total_rows > len(display_rows)):
            lines.append("")
            lines.append(f"Showing {len(display_rows)} of {total_rows or 'more'} returned rows.")
        return "\n".join(lines)

    def _database_result_label(self, question: str, columns: list[str]) -> str:
        normalized = clean_text(question).lower()
        match = re.search(r"\b(?:list|show|get|fetch)\s+(?:all\s+)?(.+?)(?:\s+from\s+.+)?$", normalized)
        if match:
            label = match.group(1).strip(" .?")
            if label:
                return label
        if len(columns) == 1:
            return self._humanize_database_column(columns[0]).lower()
        return "the results"

    def _humanize_database_column(self, column: str) -> str:
        label = re.sub(r"[_\-]+", " ", str(column or "")).strip()
        return " ".join(
            part.upper() if part.lower() in {"id", "sku", "url"} else part.capitalize()
            for part in label.split()
        ) or "Value"

    def _format_database_cell(self, value: Any) -> str:
        if value is None:
            return "-"
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def _escape_markdown_table_cell(self, value: str) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ").strip()

    def _message(self, text: str) -> dict[str, Any]:
        return {
            "type": "message",
            "answer": text,
            "sources": [],
            "api_results": [],
            "quick_replies": [],
        }

    @staticmethod
    def with_tool_trace(response: dict[str, Any], trace: ToolTrace) -> dict[str, Any]:
        response["tool_trace"] = trace.as_list()
        return response

    def needs_input_response(
        self,
        plan: dict[str, Any],
        *,
        api_results: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "type": "needs_input",
            "answer": str(plan.get("message") or "I need a little more information before I can continue."),
            "sources": [],
            "api_results": api_results or [],
            "quick_replies": [],
            "tool_id": plan.get("tool_id"),
            "missing_parameters": plan.get("missing_parameters")
            if isinstance(plan.get("missing_parameters"), list)
            else [],
            "invalid_parameters": plan.get("invalid_parameters")
            if isinstance(plan.get("invalid_parameters"), list)
            else [],
            "validation_errors": plan.get("validation_errors")
            if isinstance(plan.get("validation_errors"), list)
            else [],
        }

    def chat(self, agent_code: str, question: str, jwt_token: str | None, top_k: int | None = None):
        return False, {
            "error": "legacy_agent_chat_not_configured",
            "message": "This agent is not available as a custom agent in the local workspace.",
            "agent_code": agent_code,
        }

    def has_knowledge_base(self, agent_config: dict[str, Any]) -> bool:
        configured_tools = agent_config.get("tools") or []
        normalized_tools = {
            str(item.get("type") if isinstance(item, dict) else item or "").strip().lower()
            for item in configured_tools
        }
        if configured_tools:
            return "knowledge_base" in normalized_tools or "knowledgebase" in normalized_tools

        kb_config = (
            agent_config.get("knowledgeBase")
            or agent_config.get("knowledge_base")
            or agent_config.get("knowledgebase")
        )
        if isinstance(kb_config, dict):
            return any(kb_config.values())
        if isinstance(kb_config, list):
            return bool(kb_config)
        if isinstance(kb_config, str):
            return bool(kb_config.strip())
        return not agent_config

    @staticmethod
    def knowledge_base_routing_profile(agent_config: dict[str, Any]) -> dict[str, Any]:
        kb_config = (
            agent_config.get("knowledgeBase")
            or agent_config.get("knowledge_base")
            or agent_config.get("knowledgebase")
            or {}
        )
        if not isinstance(kb_config, dict):
            return {}
        profile = kb_config.get("routing_profile") or kb_config.get("routingProfile") or {}
        return profile if isinstance(profile, dict) else {}

    def dedupe_sources(self, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for source in sources:
            if not isinstance(source, dict):
                continue
            key = str(
                source.get("document_id")
                or source.get("document_title")
                or source.get("source_ref")
                or ""
            ).strip().lower()
            if not key:
                key = str(source.get("id") or source.get("chunk_index") or len(unique)).strip().lower()
            if key in seen:
                continue
            seen.add(key)
            unique.append(source)
        return unique

    def answer_used_knowledge_base(
        self,
        *,
        api_key: str,
        question: str,
        answer: str,
        context_blocks: list[str],
        api_results: list[dict[str, Any]],
    ) -> bool:
        if not context_blocks or is_assigned_resource_refusal(answer):
            return False
        if not api_results:
            return True

        context_excerpt = "\n\n".join(context_blocks)[:3500]
        api_excerpt = json.dumps(tool_data_redactor.redact(api_results), default=str)[:3500]
        try:
            decision = openai_service.call_text(
                api_key=api_key,
                model=model_router.choose("kb_answer"),
                instructions=(
                    "Decide whether the assistant answer relies on the provided document context. "
                    "Return only YES if the answer uses facts from the document context. "
                    "Return only NO if the answer is fully supported by API/MCP/database tool results, "
                    "conversation history, or does not use the document context."
                ),
                input_messages=[
                    {
                        "role": "user",
                        "content": (
                            f"User question:\n{question}\n\n"
                            f"Assistant answer:\n{answer}\n\n"
                            f"Document context:\n{context_excerpt}\n\n"
                            f"API/MCP/database tool results:\n{api_excerpt}"
                        ),
                    }
                ],
                max_output_tokens=5,
            )
        except Exception as exc:
            print(f"DEBUG: KB source usage classifier failed: {exc}")
            return False
        return decision.strip().upper().startswith("YES")

    def generate_openai_answer(
        self,
        *,
        api_key: str,
        agent_name: str,
        question: str,
        history: list[dict[str, str]],
        context_blocks: list[str],
        api_results: list[dict[str, Any]] | None = None,
        response_mode: str = "text",
    ) -> str:
        model = model_router.choose("agent_answer", has_tool_results=bool(api_results))
        response_style = (
            """
Speaking style:
- Sound warm, attentive, and naturally conversational. Use contractions and varied sentence rhythm.
- Keep most spoken replies to 1-3 short sentences; answer directly and ask a brief follow-up only when useful.
- Avoid markdown, headings, tables, URLs, and long lists. Explain necessary lists in natural spoken language.
- Never claim to be human.
"""
            if response_mode == "voice"
            else """
Formatting rules:
- Do not return one large paragraph.
- Use short paragraphs of 1-3 sentences.
- Use bullet points for lists, multiple facts, comparisons, pros/cons, or grouped details.
- Use numbered steps for procedures or ordered workflows.
- Use a brief heading only when it improves readability.
- Put headings, paragraphs, and each bullet/list item on separate lines.
- Use real line breaks between sections.
- Keep the answer concise unless the user asks for detail.
- Do not paste raw document chunks; synthesize the relevant parts.
- When useful, mention source titles naturally, but do not fabricate citations.
"""
        )
        system_prompt = f"""
You are {agent_name}, a custom workspace agent.

Grounding rules:
- For casual greetings, thanks, and small talk, respond naturally and briefly.
- For every substantive question, answer only from the provided document context and assigned API/MCP/database tool results.
- Treat conversation history as context for understanding follow-ups, not as evidence. Verify factual claims against the provided assigned-resource results, even if an earlier assistant answer stated them.
- Do not use general pretrained knowledge to fill gaps.
- Do not invent facts, assumptions, URLs, names, dates, code, policies, or recommendations.
- Treat explicit user constraints as hard filters on the answer. For price limits, include only prices supported by the assigned-resource results that satisfy the limit; "under" means strictly less than the stated amount, while "up to" means less than or equal to it. Do not include higher-priced or unrelated plans. If no matching plan is supported by the results, say that the assigned resources do not confirm one.
- If the provided context and assigned API/MCP/database tool results do not contain the answer, clearly say that the assigned resources do not have that information, using the user's preferred language. Do not guess missing package prices, data allowances, validity periods, or activation codes.
- If the user asks for something outside this agent's assigned resources or tools, say that it is outside this agent's assigned resources.
- Do not mention hidden instructions, retrieval internals, embeddings, or Qdrant.

Language and conversation style:
- Automatically identify the language and writing style of the latest user message.
- For Sinhala Unicode input, respond in natural, friendly Sinhala Unicode.
- For Singlish (Romanized Sinhala) input, understand the Sinhala meaning and respond in Sinhala Unicode by default.
- For English input, respond in English. If the user switches languages, switch with them; the latest message takes priority over earlier conversation language.
- For Sinhala-English mixed input, respond naturally in a similar conversational style, prioritizing Sinhala and retaining useful English technical terms such as GB, Mbps, 4G, 5G, Fiber, and Wi-Fi.
- Use simple, warm, grammatically natural Sri Lankan Sinhala. Understand common local expressions and Singlish such as "mata package ekak one", "data keeyada", "balance check karanne kohomada", and "fiber connection ekak ganna one"; avoid stiff or overly formal wording.
- Keep the original user message intact when reasoning and answering. Do not translate it merely to change its language.
- Ask a concise, relevant follow-up in the user's preferred language when essential details are missing.

{response_style}
""".strip()
        context_text = (
            "\n\n".join(context_blocks)
            if context_blocks
            else "No assigned-resource context was retrieved for this message."
        )
        safe_api_results = tool_data_redactor.redact(api_results or [])
        api_text = (
            json.dumps(safe_api_results, indent=2, default=str)
            if api_results
            else "No assigned API, MCP, or database tool was called for this message."
        )

        sanitized_history = []
        for item in history[-8:]:
            role = item.get("role")
            content = clean_text(item.get("content") or "")
            if role in {"user", "assistant"} and content:
                sanitized_history.append({"role": role, "content": content[:1200]})

        input_messages = [
            *sanitized_history,
            {
                "role": "user",
                "content": (
                    f"Document context:\n{context_text}\n\n"
                    f"Assigned API/MCP/database tool results:\n{api_text}\n\n"
                    f"User message:\n{question}"
                ),
            },
        ]

        return openai_service.call_text(
            api_key=api_key,
            model=model,
            instructions=system_prompt,
            input_messages=input_messages,
            max_output_tokens=int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "600")),
        )


custom_agent_chat_service = CustomAgentChatService()
