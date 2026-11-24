"""Generic HTTP tool runner: success, HTTP errors and timeouts all become results the LLM can speak."""

import httpx

from voice_agent.call_config import HttpToolTarget, ToolConfig
from voice_agent.tools import build_tools_schema, call_http_tool, check_guards

TOOL = ToolConfig(
    name="book_slot",
    description="Book a slot",
    parameters={"type": "object", "properties": {"time": {"type": "string"}}, "required": ["time"]},
    http=HttpToolTarget(method="POST", url="http://clinic.test/book_slot", timeout_ms=500),
)


def client_returning(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_success_returns_response_json() -> None:
    async with client_returning(lambda req: httpx.Response(201, json={"bookingId": 7})) as client:
        assert await call_http_tool(client, TOOL, {"time": "10:00"}) == {"ok": True, "data": {"bookingId": 7}}


async def test_post_sends_arguments_as_json_body() -> None:
    seen: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.content)
        return httpx.Response(200, json={})

    async with client_returning(handler) as client:
        await call_http_tool(client, TOOL, {"time": "10:00"})
    assert seen == [b'{"time":"10:00"}']


async def test_http_error_returns_the_servers_reason() -> None:
    response = httpx.Response(409, json={"detail": "The slot is already booked"})
    async with client_returning(lambda req: response) as client:
        result = await call_http_tool(client, TOOL, {"time": "10:00"})
    assert result == {"ok": False, "status": 409, "error": "The slot is already booked"}


async def test_timeout_is_reported_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async with client_returning(handler) as client:
        result = await call_http_tool(client, TOOL, {"time": "10:00"})
    assert result["ok"] is False
    assert "did not respond within 0.5 seconds" in result["error"]


async def test_unreachable_server_is_reported_not_raised() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with client_returning(handler) as client:
        result = await call_http_tool(client, TOOL, {"time": "10:00"})
    assert result["ok"] is False
    assert "Could not reach" in result["error"]


def test_schema_exposes_each_tool_to_the_llm() -> None:
    [schema] = build_tools_schema([TOOL]).standard_tools
    assert schema.name == "book_slot"
    assert schema.required == ["time"]


# ---- guards ---------------------------------------------------------------------------

GUARDED = TOOL.model_copy(update={"requires_caller_turn": True, "caller_provided_arguments": ["patientName"]})
CALLER_GAVE_NAME = [
    {"role": "assistant", "content": "May I have your name?"},
    {"role": "user", "content": "It's Jaya."},
]


def test_guard_allows_a_direct_reply_with_a_name_the_caller_said() -> None:
    assert check_guards(GUARDED, {"patientName": "Jaya"}, CALLER_GAVE_NAME, "c1") is None


def test_guard_rejects_a_name_the_caller_never_said() -> None:
    refusal = check_guards(GUARDED, {"patientName": "John Doe"}, CALLER_GAVE_NAME, "c1")
    assert refusal is not None and "never said 'John Doe'" in refusal


def test_guard_rejects_booking_chained_straight_after_another_tool() -> None:
    """The real bug: check_slots result -> LLM invents the caller's answers -> book_slot."""
    messages = [
        *CALLER_GAVE_NAME,
        {"role": "tool", "tool_call_id": "c0", "content": '{"ok": true}'},
        {"role": "assistant", "content": "I'll take twelve. John Doe."},
    ]
    refusal = check_guards(GUARDED, {"patientName": "Jaya"}, messages, "c1")
    assert refusal is not None and "right after the caller confirms" in refusal


def test_guard_ignores_the_in_progress_placeholder_of_the_current_call() -> None:
    messages = [*CALLER_GAVE_NAME, {"role": "tool", "tool_call_id": "c1", "content": "IN_PROGRESS"}]
    assert check_guards(GUARDED, {"patientName": "Jaya"}, messages, "c1") is None
