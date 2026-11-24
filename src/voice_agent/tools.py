"""Generic HTTP tools: turn the `tools` list of a call config into LLM functions.

Each configured tool becomes a function the LLM may call. When it does, we send
the arguments to the tool's URL (query string for GET, JSON body otherwise) and
hand the outcome back to the LLM as one of:

    {"ok": true,  "data": <response JSON>}
    {"ok": false, "error": "<what went wrong>"}      # HTTP error, timeout, unreachable

The LLM never sees an exception, so it can always tell the caller what happened.

Guards: an LLM sometimes plays both sides of the conversation ("Which time?
...I'll take twelve. Your name? ...John Doe.") and then books on its own. A tool
can opt in to two checks that run before the HTTP call and refuse it if:

  - requiresCallerTurn:      the call is not a direct reply to something the caller said
  - callerProvidedArguments: an argument's value (e.g. the name) was never said by the caller

A refused call returns {"ok": false, "error": ...} telling the LLM what to ask for.
"""

import re
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from loguru import logger
from pipecat.adapters.llm.schemas.function_schema import FunctionSchema
from pipecat.adapters.llm.schemas.tools_schema import ToolsSchema
from pipecat.processors.aggregators.llm_context import LLMContextMessage
from pipecat.services.llm_service import FunctionCallParams, LLMService

from voice_agent.call_config import ToolConfig

ToolResult = dict[str, Any]

# Pipecat cancels a tool that overruns this much past its own HTTP timeout.
# It is only a safety net; httpx normally times out first and we report it.
_PIPECAT_TIMEOUT_MARGIN_SECS = 2.0


@dataclass(frozen=True)
class ToolCallRecord:
    """One tool call, kept for the post-call tool log."""

    name: str
    arguments: dict[str, Any]
    result: ToolResult
    started_at: float  # unix seconds
    duration_ms: int


@dataclass
class ToolLog:
    calls: list[ToolCallRecord] = field(default_factory=list)


def build_tools_schema(tools: list[ToolConfig]) -> ToolsSchema:
    return ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name=tool.name,
                description=tool.description,
                properties=tool.parameters.get("properties", {}),
                required=tool.parameters.get("required", []),
            )
            for tool in tools
        ]
    )


async def call_http_tool(client: httpx.AsyncClient, tool: ToolConfig, arguments: dict[str, Any]) -> ToolResult:
    """Call one tool's HTTP endpoint. Never raises: failures become {"ok": false}."""
    target = tool.http
    timeout_secs = target.timeout_ms / 1000
    request: dict[str, Any] = {"params": arguments} if target.method == "GET" else {"json": arguments}
    try:
        response = await client.request(target.method, target.url, timeout=timeout_secs, **request)
    except httpx.TimeoutException:
        return {"ok": False, "error": f"The {tool.name} system did not respond within {timeout_secs:g} seconds."}
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"Could not reach the {tool.name} system ({type(exc).__name__})."}

    body = _json_or_text(response)
    if response.is_success:
        return {"ok": True, "data": body}
    detail = body.get("detail", body) if isinstance(body, dict) else body
    return {"ok": False, "status": response.status_code, "error": str(detail)}


def check_guards(
    tool: ToolConfig,
    arguments: dict[str, Any],
    messages: Sequence[LLMContextMessage],
    tool_call_id: str,
) -> str | None:
    """Return why the call must not run, or None if it may."""
    # Pipecat may already have added an "in progress" result for this very call; ignore it.
    standard = [
        m for m in messages if isinstance(m, dict) and not (m.get("role") == "tool" and m.get("tool_call_id") == tool_call_id)
    ]

    if tool.requires_caller_turn and _last_non_assistant_role(standard) != "user":
        return (
            f"Not done: {tool.name} may only be used right after the caller confirms. "
            "Read the details back to the caller, ask them to confirm, and wait for their answer."
        )

    caller_words = _words(" ".join(_text(m) for m in standard if m.get("role") == "user"))
    for name in tool.caller_provided_arguments:
        value = str(arguments.get(name, ""))
        if not _words(value) & caller_words:
            return f"Not done: the caller never said {value!r}. Ask the caller for their {name} and wait for the answer."
    return None


def register_http_tools(llm: LLMService, tools: list[ToolConfig], client: httpx.AsyncClient, log: ToolLog) -> None:
    """Make every configured tool callable by the LLM, recording each call in `log`."""
    for tool in tools:

        async def handler(params: FunctionCallParams, tool: ToolConfig = tool) -> None:
            arguments = dict(params.arguments)
            started = time.time()
            refusal = check_guards(tool, arguments, params.context.get_messages(), params.tool_call_id)
            result = {"ok": False, "error": refusal} if refusal else await call_http_tool(client, tool, arguments)
            duration_ms = round((time.time() - started) * 1000)
            log.calls.append(ToolCallRecord(tool.name, arguments, result, started, duration_ms))
            logger.info(f"Tool {tool.name}({arguments}) -> {result} in {duration_ms} ms")
            await params.result_callback(result)

        llm.register_function(
            tool.name,
            handler,
            timeout_secs=tool.http.timeout_ms / 1000 + _PIPECAT_TIMEOUT_MARGIN_SECS,
        )


def _last_non_assistant_role(messages: Sequence[dict[str, Any]]) -> str | None:
    """Role of the latest message not written by the assistant: "user" or "tool"."""
    for message in reversed(messages):
        if message.get("role") != "assistant":
            return message.get("role")
    return None


def _text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # multi-part content: [{"type": "text", "text": ...}, ...]
        return " ".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.lower()))


def _json_or_text(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text
