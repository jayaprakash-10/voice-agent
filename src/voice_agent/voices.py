"""Maps the public voice names used in the API to provider-specific voice IDs.

Callers of the API say `"voice": "female-en-in-1"`; only this module knows that
means a particular Cartesia voice. Adding a voice is a one-line change here.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Voice:
    cartesia_voice_id: str
    description: str


VOICES: dict[str, Voice] = {
    "female-en-in-1": Voice("e6b71342-48f1-4c70-a13a-d197b176ff24", "Sana - friendly concierge, Indian accent"),
    "female-en-in-2": Voice("4459a9a5-69d6-4680-b970-e13dc51845b6", "Siya - bright conversationalist, Indian accent"),
}

DEFAULT_VOICE = "female-en-in-1"


def resolve_voice(name: str) -> Voice:
    """Return the voice for `name`, raising a clear error for unknown names."""
    try:
        return VOICES[name]
    except KeyError:
        raise ValueError(f"Unknown voice {name!r}. Available: {', '.join(sorted(VOICES))}") from None
