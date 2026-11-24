"""The per-call agent configuration: the body of `POST /calls`."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _ApiModel(BaseModel):
    """Accepts both the API's camelCase names and Python's snake_case names."""

    model_config = ConfigDict(populate_by_name=True)


class InactivityConfig(_ApiModel):
    after_seconds: float = Field(8, alias="afterSeconds", gt=0)
    message: str = "Are you still there?"
    hang_up_after_seconds: float = Field(20, alias="hangUpAfterSeconds", gt=0)


class HttpToolTarget(_ApiModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    url: str
    timeout_ms: int = Field(4000, alias="timeoutMs", gt=0)


class ToolConfig(_ApiModel):
    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}})
    http: HttpToolTarget

    # Guards against the LLM acting on its own (see tools.py). Both are optional extensions.
    # Only run as a direct reply to the caller, never chained after another tool result.
    # Use for actions with side effects, e.g. booking, so the caller confirms first.
    requires_caller_turn: bool = Field(False, alias="requiresCallerTurn")
    # Arguments whose values the caller must actually have said (e.g. their name),
    # so the LLM cannot invent them.
    caller_provided_arguments: list[str] = Field(default_factory=list, alias="callerProvidedArguments")


class CallConfig(_ApiModel):
    """Everything that defines how the agent behaves on one call."""

    system_prompt: str = Field(alias="systemPrompt")
    voice: str = "female-en-in-1"
    language: str = "en-IN"
    first_speaker: Literal["agent", "user"] = Field("agent", alias="firstSpeaker")
    greeting: str | None = None
    max_duration_seconds: int = Field(600, alias="maxDurationSeconds", gt=0)
    inactivity: InactivityConfig = Field(default_factory=InactivityConfig)
    tools: list[ToolConfig] = Field(default_factory=list)
    knowledge_base: str | None = Field(None, alias="knowledgeBase")
    direction: Literal["inbound", "outbound"] = "outbound"
    to: str | None = None
    webhook_url: str | None = Field(None, alias="webhookUrl")
