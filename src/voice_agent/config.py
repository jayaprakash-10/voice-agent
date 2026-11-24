"""Application settings, loaded from environment variables / the .env file."""

from functools import lru_cache
from typing import Literal, Self

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Which phone provider carries the calls.
    telephony_provider: Literal["twilio", "plivo"] = "plivo"

    # Twilio
    twilio_account_sid: str | None = None
    twilio_auth_token: SecretStr | None = None
    twilio_phone_number: str | None = None

    # Plivo
    plivo_auth_id: str | None = None
    plivo_auth_token: SecretStr | None = None
    plivo_phone_number: str | None = None

    # AI providers
    deepgram_api_key: SecretStr
    groq_api_key: SecretStr
    cartesia_api_key: SecretStr

    # Model choice. Qwen with reasoning off: fastest first token, and in a replay of a
    # real call it never spoke for the caller, unlike gpt-oss-120b (low), which did 5/5.
    # gpt-oss models take "low"/"medium"/"high"; Qwen takes "none"/"default".
    groq_model: str = "qwen/qwen3.8-27b"
    groq_reasoning_effort: Literal["none", "default", "low", "medium", "high"] = "none"

    # Public HTTPS base URL the phone provider uses to reach this server, e.g. https://abc.ngrok.app
    public_url: str

    # Reject webhook requests that are not signed by the phone provider.
    validate_webhook_signature: bool = True

    host: str = "0.0.0.0"
    port: int = 8000

    # Mock clinic (see clinic.py). `clinic_simulate` makes it fail on purpose, for demos:
    # "slow" never answers in time, "error" returns HTTP 500.
    clinic_db_path: str = "clinic.db"
    clinic_simulate: Literal["off", "slow", "error"] = "off"
    clinic_seed_bookings: bool = True  # pre-book ~30% of slots with fake patients

    @property
    def local_base_url(self) -> str:
        """How this server reaches itself, e.g. for the default clinic tools (skips ngrok)."""
        return f"http://127.0.0.1:{self.port}"

    @model_validator(mode="after")
    def _require_provider_credentials(self) -> Self:
        required = {
            "twilio": ("twilio_account_sid", "twilio_auth_token", "twilio_phone_number"),
            "plivo": ("plivo_auth_id", "plivo_auth_token", "plivo_phone_number"),
        }[self.telephony_provider]
        missing = [name.upper() for name in required if not getattr(self, name)]
        if missing:
            raise ValueError(f"TELEPHONY_PROVIDER={self.telephony_provider} needs: {', '.join(missing)}")
        return self

    @property
    def public_base_url(self) -> str:
        return self.public_url.rstrip("/")

    @property
    def public_ws_url(self) -> str:
        """The public URL with the scheme swapped to WebSocket (https -> wss)."""
        return self.public_base_url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
