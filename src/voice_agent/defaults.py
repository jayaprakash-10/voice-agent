"""The agent configuration used for inbound calls until `POST /calls` sets another."""

from voice_agent.call_config import CallConfig, HttpToolTarget, ToolConfig
from voice_agent.config import Settings

CLINIC_SYSTEM_PROMPT = """\
You are the front desk receptionist of Bright Smile Dental, a dental clinic.
Help callers book appointments and answer questions about the clinic.
Be warm, brief and professional.

Booking an appointment:
- Ask which day suits the caller, then use check_slots for that date.
- Offer at most three of the free times.
- Ask for the caller's name. Never make up or assume a name.
- Read the day, time and name back and ask the caller to confirm.
- Use book_slot only after the caller clearly says yes.
- Only say the appointment is booked if book_slot succeeded."""


def clinic_tools(settings: Settings) -> list[ToolConfig]:
    """The mock clinic's tools, pointing at this server's /clinic endpoints."""
    base = f"{settings.local_base_url}/clinic"
    return [
        ToolConfig(
            name="check_slots",
            description="List the free appointment start times on a given date.",
            parameters={
                "type": "object",
                "properties": {"date": {"type": "string", "description": "Date as YYYY-MM-DD"}},
                "required": ["date"],
            },
            http=HttpToolTarget(method="POST", url=f"{base}/check_slots", timeout_ms=4000),
        ),
        ToolConfig(
            name="book_slot",
            description="Book a free appointment slot for the caller.",
            parameters={
                "type": "object",
                "properties": {
                    "date": {"type": "string", "description": "Date as YYYY-MM-DD"},
                    "time": {"type": "string", "description": "Start time as HH:MM, 24-hour"},
                    "patientName": {"type": "string", "description": "The caller's full name"},
                },
                "required": ["date", "time", "patientName"],
            },
            http=HttpToolTarget(method="POST", url=f"{base}/book_slot", timeout_ms=4000),
            requires_caller_turn=True,
            caller_provided_arguments=["patientName"],
        ),
    ]


def default_inbound_config(settings: Settings) -> CallConfig:
    return CallConfig(
        system_prompt=CLINIC_SYSTEM_PROMPT,
        voice="female-en-in-1",
        language="en-IN",
        first_speaker="agent",
        greeting="Hi, this is Bright Smile Dental. How can I help?",
        tools=clinic_tools(settings),
        direction="inbound",
    )
