"""LLM provider abstraction — one interface, swappable backends.

Providers are selected only by LLM_PROVIDER in backend/.env
(mock | openai | auto). The OpenAI-compatible client targets CommandCode
(z-ai/glm-5.3-flash by default). MockLLM is pytest-only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from app.ai.cost import Usage, estimate_usage
from app.observability.llm_trace import preview_completion, preview_messages, preview_simple
from app.ai.reasoning import (
    completion_extra_body,
    effort_for_tier,
    enrich_completion_payload,
    usage_from_api_response,
)
from app.core.errors import AIError, AIStructuredOutputError, AITimeoutError, ConfigError
from app.core.settings import Settings, get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass
class ToolCallRequest:
    """One native tool invocation requested by the model."""

    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCompletion:
    """Result of a Chat Completions call that may include tool_calls."""

    content: str | None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    raw_message: dict[str, Any] | None = None


class LLMProvider(ABC):
    """Minimal chat + structured-output surface for orchestration components."""

    @abstractmethod
    async def generate(self, system: str, user: str, *, tier: str = "standard",
                       json_mode: bool = False) -> str:
        """Free-form completion for the given tier (cheap|standard|strong)."""

    @abstractmethod
    async def generate_structured(self, system: str, user: str, schema: type[T],
                                  *, tier: str = "standard",
                                  repairs: int = 1) -> tuple[T, Usage]:
        """Schema-validated output; raises AIStructuredOutputError on failure."""

    @abstractmethod
    def usage_model(self) -> str:
        """Pricing-table key for this provider."""

    def last_usage(self) -> Usage:
        """Usage from the most recent generate / generate_structured call."""
        return getattr(self, "_last_usage", Usage())

    def io_preview(self) -> tuple[str, str]:
        """Truncated input/output from the most recent LLM call."""
        return (
            str(getattr(self, "_last_input_preview", "") or ""),
            str(getattr(self, "_last_output_preview", "") or ""),
        )

    def _remember_io(self, input_preview: str, output_preview: str) -> None:
        self._last_input_preview = input_preview
        self._last_output_preview = output_preview

    async def generate_stream(self, system: str, user: str, *,
                              tier: str = "standard") -> AsyncIterator[str]:
        """Yield reply tokens as they arrive. Default: chunk a full generate()."""
        text = await self.generate(system, user, tier=tier)
        pieces = re.findall(r"\S+\s*", text) or ([text] if text else [])
        for piece in pieces:
            yield piece

    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tier: str = "standard",
    ) -> ToolCompletion:
        """Chat completion with native tool definitions. Default: no tool support."""
        system = ""
        user = ""
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            if role == "system" and isinstance(content, str):
                system = content
            elif role == "user" and isinstance(content, str) and not user:
                user = content
        text = await self.generate(system, user, tier=tier)
        return ToolCompletion(content=text, usage=self.last_usage())


_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """First JSON object in model output — fenced block, raw JSON, or prefix/suffix."""
    text = text.strip()
    if text.startswith("```"):
        text = _JSON_FENCE.sub("", text).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start >= 0:
        try:
            parsed, _ = json.JSONDecoder().raw_decode(text, start)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    raise AIStructuredOutputError("no JSON object found in model output")


class MockLLM(LLMProvider):
    """Minimal deterministic provider for pytest only — not used in production."""

    def __init__(self) -> None:
        self._last_usage = Usage()
        self._last_input_preview = ""
        self._last_output_preview = ""

    def usage_model(self) -> str:
        return "mock"

    def _remember(self, system: str, user: str, output: str) -> Usage:
        self._remember_io(preview_simple(system, user), preview_completion(output))
        self._last_usage = estimate_usage(f"{system}\n{user}", output)
        return self._last_usage

    async def generate(self, system: str, user: str, *, tier: str = "standard",
                       json_mode: bool = False) -> str:
        text = self._script(system, user)
        self._remember(system, user, text)
        return text

    async def generate_structured(self, system: str, user: str, schema: type[T],
                                  *, tier: str = "standard",
                                  repairs: int = 1) -> tuple[T, Usage]:
        text = self._script(system, user)
        usage = self._remember(system, user, text)
        data = extract_json(text)
        try:
            return schema.model_validate(data), usage
        except ValidationError as exc:
            raise AIStructuredOutputError(f"mock output failed schema: {exc}") from exc

    def _script(self, system: str, user: str) -> str:
        system_l = system.lower()
        if '"intent"' in system:
            return self._router_script(user)
        if '"steps"' in system and "planner" in system_l:
            return self._planner_script(user)
        if "worker agent" in system_l or '"prior_hops"' in user or '"assigned_task"' in user:
            return self._worker_react_script(user)
        if "evidence_json" in user.lower():
            return self._synthesis_script(user)
        if (
            "conversational assistant" in system_l
            or "specialized agents" in system_l
            or "available capabilities" in system_l
            or "answer the user" in system_l
        ):
            return self._conversational_script(user)
        if "mcp data services are unavailable" in system_l:
            return (
                "I can't reach the Jira data service right now, so I couldn't run live "
                "tools for your question. Please retry shortly or check that MCP "
                "services are running."
            )
        return "Completed based on the available evidence."

    def _router_script(self, user: str) -> str:
        message = user
        match = re.search(r"user message:\s*(.*)", user, re.IGNORECASE)
        if match:
            message = match.group(1).split("\n")[0].strip()
        from app.orchestration.routing.router import is_smalltalk

        if is_smalltalk(message):
            return json.dumps({
                "intent": "simple", "direct_answer": None, "temporal_sensitivity": False,
            })
        vague = re.search(
            r"\b(how(?:'s| is)|status of|health of)\s+(?:the\s+)?(?:project|sprint)\b",
            message, re.IGNORECASE)
        has_scope = re.search(r"\b(PLAT|NOVA|ANLT|PAY)\b", message, re.IGNORECASE)
        if vague and not has_scope:
            return json.dumps({
                "intent": "clarify",
                "direct_answer": "Which Jira project or sprint should I look at?",
                "temporal_sensitivity": False,
            })
        temporal = bool(re.search(
            r"\b(latest|current|next|upcoming|now|today|this sprint|this week)\b",
            message, re.IGNORECASE))
        return json.dumps({
            "intent": "complex", "direct_answer": None, "temporal_sensitivity": temporal,
        })

    def _planner_script(self, user: str) -> str:
        goal = user
        if "GOAL:" in user:
            goal = user.split("GOAL:", 1)[1].split("\n", 1)[0].strip()
        thread_id = "mock-thread"
        tid = re.search(r'"thread_id"\s*:\s*"([^"]+)"', user)
        if tid:
            thread_id = tid.group(1)
        lowered = goal.lower()
        if any(w in lowered for w in ("create", "add", "new", "track")) and "issue" in lowered:
            steps = [{
                "id": "s1", "agent": "jira-writer", "task": goal, "depends_on": [],
            }]
        elif any(w in lowered for w in ("velocity", "forecast", "predict")):
            steps = [
                {"id": "s1", "agent": "jira-analyst", "task": goal, "depends_on": []},
                {"id": "s2", "agent": "data-scientist", "task": "Analyze fetched data",
                 "depends_on": ["s1"]},
            ]
        elif any(w in lowered for w in ("compare", "side by side", "parallel")) and (
            "plat" in lowered and "nova" in lowered
        ):
            steps = [
                {"id": "s1", "agent": "jira-analyst",
                 "task": "Fetch PLAT project health metrics and risks",
                 "depends_on": []},
                {"id": "s2", "agent": "jira-analyst",
                 "task": "Fetch NOVA project health metrics and risks",
                 "depends_on": []},
            ]
        elif any(w in lowered for w in ("bug count", "bug counts", "trend")) and (
            "sprint" in lowered
        ):
            steps = [
                {"id": "s1", "agent": "jira-analyst",
                 "task": "Fetch bug counts across the last 3 closed sprints",
                 "depends_on": []},
                {"id": "s2", "agent": "data-scientist",
                 "task": "Analyze sprint bug-count trend from upstream data",
                 "depends_on": ["s1"]},
            ]
        elif any(w in lowered for w in ("ticket", "issue", "sprint")):
            steps = [{
                "id": "s1", "agent": "jira-analyst", "task": goal, "depends_on": [],
            }]
        else:
            steps = [{
                "id": "s1", "agent": "jira-analyst", "task": goal, "depends_on": [],
            }]
        return json.dumps({
            "thread_id": thread_id,
            "goal": goal[:200],
            "reasoning": ["Parse goal", "Select agent", "Order steps"],
            "steps": steps,
            "synthesis": "Answer the user's question using step evidence.",
            "uses_episodic_hint": False,
            "clarify_question": None,
        })

    def _worker_finish_summary(self, payload: dict[str, Any]) -> str:
        """Build a user-facing finish summary from compact hop previews."""
        prior = payload.get("prior_hops") or []
        lines: list[str] = []
        for hop in prior:
            if hop.get("type") != "tool_result" or hop.get("status") != "ok":
                continue
            data = hop.get("data") if isinstance(hop.get("data"), dict) else {}
            for issue in data.get("issues") or []:
                if not isinstance(issue, dict):
                    continue
                key = str(issue.get("id") or issue.get("key") or "").strip()
                summary = str(issue.get("summary") or "").strip()
                if key:
                    lines.append(f"- **{key}**: {summary}" if summary else f"- **{key}**")
        if lines:
            return "### Open tickets\n\n" + "\n".join(lines)
        return "Completed the assigned task using retrieved tool data."

    def _worker_react_script(self, user: str) -> str:
        raw = user
        if "REQUEST:" in user:
            raw = user.split("REQUEST:", 1)[-1].strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        goal = str(payload.get("user_goal") or payload.get("goal") or "")
        task = str(payload.get("assigned_task") or payload.get("task") or "")
        bound = payload.get("bound_tools") or []
        names = [str(t.get("name")) for t in bound if isinstance(t, dict) and t.get("name")]
        prior = payload.get("prior_hops") or []
        if prior:
            return json.dumps({
                "action": "finish",
                "summary": self._worker_finish_summary(payload),
            })
        lowered = f"{goal} {task}".lower()
        if "run_python" in names and any(
            w in lowered
            for w in ("analyze", "trend", "forecast", "regression", "upstream", "statistics")
        ):
            return json.dumps({
                "action": "call_tool",
                "tool": "run_python",
                "args": {
                    "code": "result = {'trend': 'stable', 'ok': True}\n",
                    "timeout_s": 15,
                },
            })
        tool = "jira_get_projects"
        args: dict[str, Any] = {}
        write_tools = (
            ("create", "jira_create_issue"),
            ("add comment", "jira_add_comment"),
            ("transition", "jira_transition_issue"),
            ("update issue", "jira_update_issue"),
        )
        picked_write = False
        for keyword, write_tool in write_tools:
            if keyword in lowered and write_tool in names:
                tool = write_tool
                picked_write = True
                break
        if not picked_write and any(w in lowered for w in ("ticket", "issue", "sprint")):
            if "jira_get_sprint_issues" in names:
                tool = "jira_get_sprint_issues"
                args = {"sprint_id": "SPR-6"}
            elif "jira_get_sprints" in names:
                tool = "jira_get_sprints"
                args = {"board_id": "BRD-1", "state": "closed"}
        elif "health" in lowered and "jira_get_project_health" in names:
            tool = "jira_get_project_health"
            project = re.search(r"\b(PLAT|NOVA|ANLT|PAY)\b", f"{goal} {task}", re.IGNORECASE)
            if project:
                args = {"project": project.group(1).upper()}
        elif "jira_get_projects" in names or not names:
            tool = "jira_get_projects"
        elif names:
            tool = names[0]
        if tool == "run_python":
            args = {"code": "result = {'ok': True}\n", "timeout_s": 15}
        if tool == "jira_create_issue":
            args = {
                "project": "PLAT",
                "issue_type": "Task",
                "summary": goal[:120] or "Follow-up task",
            }
        return json.dumps({"action": "call_tool", "tool": tool, "args": args})

    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tier: str = "standard",
    ) -> ToolCompletion:
        system = ""
        user = ""
        prior_hops = 0
        for msg in messages:
            role = msg.get("role")
            if role == "system" and isinstance(msg.get("content"), str):
                system = msg["content"]
            elif role == "user" and isinstance(msg.get("content"), str):
                user = msg["content"]
                try:
                    payload = json.loads(msg["content"])
                    prior_hops = len(payload.get("prior_hops") or [])
                except json.JSONDecodeError:
                    pass
            elif role == "tool":
                prior_hops = max(prior_hops, 1)
        names = [
            str(t.get("function", {}).get("name"))
            for t in tools
            if isinstance(t, dict)
        ]
        names = [n for n in names if n]
        if prior_hops > 0 or any(m.get("role") == "tool" for m in messages):
            parsed: dict[str, Any] = {}
            if user.startswith("{"):
                try:
                    parsed = json.loads(user)
                except json.JSONDecodeError:
                    parsed = {}
            if not parsed.get("prior_hops") and any(m.get("role") == "tool" for m in messages):
                parsed.setdefault("prior_hops", [{"type": "tool_result"}])
            text = self._worker_react_script(json.dumps({
                "user_goal": parsed.get("user_goal") or parsed.get("goal", ""),
                "assigned_task": parsed.get("assigned_task") or parsed.get("task", ""),
                "prior_hops": parsed.get("prior_hops") or [{"type": "tool_result"}],
                "bound_tools": [{"name": n} for n in names],
            }))
            data = extract_json(text)
            self._remember(system, user, text)
            return ToolCompletion(
                content=data.get("summary"),
                usage=self._last_usage,
            )
        parsed_user: dict[str, Any] = {}
        if user.startswith("{"):
            try:
                parsed_user = json.loads(user)
            except json.JSONDecodeError:
                parsed_user = {}
        script_user = json.dumps({
            "user_goal": parsed_user.get("user_goal") or parsed_user.get("goal", ""),
            "assigned_task": parsed_user.get("assigned_task") or parsed_user.get("task", ""),
            "bound_tools": [{"name": n} for n in names],
            "prior_hops": [],
        })
        text = self._worker_react_script(script_user)
        data = extract_json(text)
        self._remember(system, user, text)
        if data.get("action") == "call_tool" and data.get("tool"):
            return ToolCompletion(
                content=None,
                tool_calls=[ToolCallRequest(
                    id="mock-call-1",
                    name=str(data["tool"]),
                    args=dict(data.get("args") or {}),
                )],
                usage=self._last_usage,
            )
        return ToolCompletion(content=data.get("summary"), usage=self._last_usage)

    def _worker_args_script(self, user: str) -> str:
        try:
            payload = json.loads(user)
        except json.JSONDecodeError:
            payload = {}
        tool = str(payload.get("tool") or "")
        goal = str(payload.get("goal") or "")
        args: dict[str, Any] = {}
        issue = re.search(r"\b([A-Z]{2,10}-\d+)\b", goal)
        project = re.search(r"\b(PLAT|NOVA|ANLT|PAY)\b", goal, re.IGNORECASE)
        if issue and "issue" in tool:
            args["issue_id"] = issue.group(1)
        if project:
            args["project"] = project.group(1).upper()
        if tool == "jira_get_sprints":
            args.setdefault("board_id", "BRD-1")
            args.setdefault("state", "closed")
        if tool == "jira_get_sprint_issues":
            args.setdefault("sprint_id", "SPR-6")
        if tool == "jira_create_issue":
            args.setdefault("project", args.get("project", "PLAT"))
            args.setdefault("issue_type", "Task")
            args.setdefault("summary", "Follow-up task")
        if tool == "run_python":
            args.setdefault("code", "result = {'ok': True}\n")
            args.setdefault("timeout_s", 15)
        if not tool:
            return json.dumps({"action": "finish", "summary": "No tool specified."})
        return json.dumps({"action": "call_tool", "tool": tool, "args": args})

    def _synthesis_script(self, user: str) -> str:
        if re.search(r'"id":\s*"PLAT-9"', user):
            return (
                "### Open tickets\n\n"
                "- **PLAT-9** — Stabilize checkout retries · In Progress · 3 points · bob\n"
                "- **PLAT-10** — Add sprint health dashboard · In Progress · 5 points · alice\n"
            )
        if re.search(r"not found|error", user, re.IGNORECASE):
            return "I could not find a match for that item in the retrieved data."
        return "Here is a summary based on the tool results collected in this run."

    def _conversational_script(self, user: str) -> str:
        lowered = user.lower()
        if re.match(r"^(hi|hello|hey)\b", lowered):
            return (
                "Hello! I coordinate specialist agents to answer questions using "
                "connected tools. What would you like to know?"
            )
        if re.search(r"\b(thanks|thank you)\b", lowered):
            return "You're welcome — ask anytime."
        return "I can help with questions that need live data from connected tools."


class OpenAICompatibleProvider(LLMProvider):
    """CommandCode inference (OpenAI-compatible interface).

    Implements request timeouts, controlled retries, JSON extraction and one
    repair pass for structured output; never exposes raw provider errors.
    """

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise ConfigError("OPENAI_API_KEY is required for live inference")
        self._model = settings.openai_model
        self._planner_model = settings.llm_planner_model or settings.openai_model
        self._temperature = settings.llm_temperature
        self._max_tokens = settings.llm_max_tokens
        self._api_key = settings.openai_api_key
        self._base_url = settings.openai_base_url
        self._timeout_s = settings.llm_timeout_s
        self._max_retries = settings.llm_max_retries
        self._client: Any | None = None
        self._last_usage = Usage()
        self._last_input_preview = ""
        self._last_output_preview = ""

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=self._timeout_s,
                max_retries=self._max_retries,
            )
        return self._client

    def usage_model(self) -> str:
        return self._model

    def _model_for_tier(self, tier: str) -> str:
        return self._planner_model if tier in {"strong", "planner"} else self._model

    async def _complete(self, client: Any, system: str, user: str, *,
                        tier: str, json_mode: bool) -> Any:
        settings = get_settings()
        payload: dict[str, Any] = {
            "model": self._model_for_tier(tier),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        model = self._model_for_tier(tier)
        effort = effort_for_tier(tier, settings, model=model)
        enrich_completion_payload(payload, effort)
        extra = completion_extra_body(effort, model=model)
        create_kwargs: dict[str, Any] = {"extra_body": extra} if extra else {}
        if json_mode:
            try:
                return await client.chat.completions.create(
                    **payload, response_format={"type": "json_object"}, **create_kwargs)
            except TimeoutError:
                raise
            except Exception as exc:  # noqa: BLE001 - fallback to plain text
                logger.warning("json_object mode rejected (%s); retrying as text",
                               type(exc).__name__)
        return await client.chat.completions.create(**payload, **create_kwargs)

    async def generate(self, system: str, user: str, *, tier: str = "standard",
                       json_mode: bool = False) -> str:
        client = self._get_client()
        try:
            response = await self._complete(
                client, system, user, tier=tier, json_mode=json_mode)
        except TimeoutError as exc:
            raise AITimeoutError(f"provider timeout after {self._timeout_s}s") from exc
        except Exception as exc:  # noqa: BLE001 - converted to typed AIError below
            raise AIError(f"provider call failed: {type(exc).__name__}",
                          safe_message="The AI service is currently unavailable.") from exc
        content = response.choices[0].message.content or ""
        reported = usage_from_api_response(response, fallback_completion=content)
        self._last_usage = reported if reported.total_tokens else estimate_usage(
            f"{system}\n{user}", content)
        self._remember_io(preview_simple(system, user), preview_completion(content))
        return content

    async def generate_stream(self, system: str, user: str, *,
                              tier: str = "standard") -> AsyncIterator[str]:
        client = self._get_client()
        settings = get_settings()
        payload: dict[str, Any] = {
            "model": self._model_for_tier(tier),
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
            "stream": True,
        }
        model = self._model_for_tier(tier)
        effort = effort_for_tier(tier, settings, model=model)
        enrich_completion_payload(payload, effort)
        extra = completion_extra_body(effort, model=model)
        stream_kwargs: dict[str, Any] = {"extra_body": extra} if extra else {}
        collected: list[str] = []
        try:
            async with asyncio.timeout(self._timeout_s):
                stream = await client.chat.completions.create(**payload, **stream_kwargs)
                async for event in stream:
                    choice = (event.choices or [None])[0]
                    delta = getattr(getattr(choice, "delta", None), "content", None)
                    if delta:
                        collected.append(delta)
                        yield delta
                    usage = getattr(event, "usage", None)
                    if usage is not None:
                        self._last_usage = usage_from_api_response(event)
        except TimeoutError as exc:
            if collected:
                logger.warning(
                    "provider stream timed out after %ss; using %d partial tokens",
                    self._timeout_s, len(collected))
            else:
                raise AITimeoutError(
                    f"provider timeout after {self._timeout_s}s") from exc
        except Exception as exc:  # noqa: BLE001
            raise AIError(f"provider stream failed: {type(exc).__name__}",
                          safe_message="The AI service is currently unavailable.") from exc
        if collected and self._last_usage.total_tokens == 0:
            self._last_usage = estimate_usage(f"{system}\n{user}", "".join(collected))
        self._remember_io(
            preview_simple(system, user),
            preview_completion("".join(collected) if collected else None),
        )

    async def generate_structured(self, system: str, user: str, schema: type[T],
                                  *, tier: str = "standard",
                                  repairs: int = 1) -> tuple[T, Usage]:
        last_error: Exception | None = None
        repair_note = ""
        attempts = 1 + max(0, repairs)
        for _ in range(attempts):
            text = await self.generate(
                system, user + repair_note, tier=tier, json_mode=True)
            try:
                data = extract_json(text)
                self._remember_io(
                    preview_simple(system, user + repair_note),
                    preview_completion(text),
                )
                return schema.model_validate(data), self._last_usage
            except (AIStructuredOutputError, ValidationError) as exc:
                last_error = exc
                repair_note = (f"\n\nYour previous reply was invalid ({type(exc).__name__}). "
                               "Reply with ONLY the corrected JSON object.")
        raise AIStructuredOutputError(f"structured output failed validation: {last_error}")

    async def generate_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        tier: str = "standard",
    ) -> ToolCompletion:
        client = self._get_client()
        settings = get_settings()
        payload: dict[str, Any] = {
            "model": self._model_for_tier(tier),
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": self._max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        model = self._model_for_tier(tier)
        effort = effort_for_tier(tier, settings, model=model)
        enrich_completion_payload(payload, effort)
        extra = completion_extra_body(effort, model=model)
        create_kwargs: dict[str, Any] = {"extra_body": extra} if extra else {}
        try:
            response = await client.chat.completions.create(**payload, **create_kwargs)
        except TimeoutError as exc:
            raise AITimeoutError(f"provider timeout after {self._timeout_s}s") from exc
        except Exception as exc:  # noqa: BLE001
            raise AIError(
                f"provider tool call failed: {type(exc).__name__}",
                safe_message="The AI service is currently unavailable.",
            ) from exc

        choice = response.choices[0].message
        content = getattr(choice, "content", None) or None
        tool_calls: list[ToolCallRequest] = []
        for tc in getattr(choice, "tool_calls", None) or []:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            raw_args = getattr(fn, "arguments", None) or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}
            tool_calls.append(ToolCallRequest(
                id=str(getattr(tc, "id", "") or f"call-{len(tool_calls)}"),
                name=str(getattr(fn, "name", "") or ""),
                args=args,
            ))
        reported = usage_from_api_response(response, fallback_completion=content or "")
        self._last_usage = reported if reported.total_tokens else estimate_usage(
            json.dumps(messages, default=str)[:4000],
            content or json.dumps([tc.name for tc in tool_calls]),
        )
        self._remember_io(
            preview_messages(messages),
            preview_completion(content, tool_calls=tool_calls),
        )
        raw_message: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            raw_message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.args, default=str),
                    },
                }
                for tc in tool_calls
            ]
        return ToolCompletion(
            content=content,
            tool_calls=tool_calls,
            usage=self._last_usage,
            raw_message=raw_message,
        )


_provider: LLMProvider | None = None


def resolve_llm_provider(settings: Settings | None = None) -> str:
    """Honor ``LLM_PROVIDER`` from settings (backend/.env at runtime).

    ``mock`` and ``openai`` are explicit. ``auto`` picks live only when a key
    exists. Pytest pins ``LLM_PROVIDER=mock`` in conftest — it does not
    second-guess a developer who set ``mock`` in .env while a key is present.
    """
    settings = settings or get_settings()
    requested = (settings.llm_provider or "auto").strip().lower()
    if requested not in {"auto", "mock", "openai"}:
        raise ConfigError(
            f"unknown LLM_PROVIDER '{settings.llm_provider}' (expected auto|mock|openai)")
    if requested == "openai":
        if not settings.openai_api_key:
            raise ConfigError("LLM_PROVIDER=openai requires OPENAI_API_KEY in backend/.env")
        return "openai"
    if requested == "mock":
        return "mock"
    if not settings.openai_api_key:
        raise ConfigError(
            "LLM_PROVIDER=auto requires OPENAI_API_KEY — set LLM_PROVIDER=mock for tests only")
    return "openai"


def get_provider() -> LLMProvider:
    """Factory honoring LLM_PROVIDER; identical interface behind the flip."""
    global _provider
    if _provider is None:
        name = resolve_llm_provider()
        if name == "mock":
            _provider = MockLLM()
        elif name == "openai":
            _provider = OpenAICompatibleProvider()
        else:
            raise ConfigError(f"unknown LLM_PROVIDER '{name}'")
        logger.info("LLM provider selected: %s model=%s",
                    name, _provider.usage_model())
    return _provider


def reset_provider() -> None:
    """Reset the cached provider (used by tests to swap providers cleanly)."""
    global _provider
    _provider = None
