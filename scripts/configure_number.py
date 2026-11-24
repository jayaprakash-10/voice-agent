"""Points the phone number's incoming calls at this server's /telephony/answer.

Run once, and again whenever PUBLIC_URL changes:
    uv run python scripts/configure_number.py
"""

import plivo
from twilio.rest import Client as TwilioClient

from voice_agent.config import Settings, get_settings

PLIVO_APP_NAME = "voice-agent"


def configure_twilio(settings: Settings, answer_url: str) -> None:
    assert settings.twilio_account_sid and settings.twilio_auth_token
    client = TwilioClient(settings.twilio_account_sid, settings.twilio_auth_token.get_secret_value())
    numbers = client.incoming_phone_numbers.list(phone_number=settings.twilio_phone_number)
    if not numbers:
        raise SystemExit(f"{settings.twilio_phone_number} is not a number on this Twilio account")
    numbers[0].update(voice_url=answer_url, voice_method="POST")


def configure_plivo(settings: Settings, answer_url: str) -> None:
    """Plivo routes a number's calls through an "application" that holds the answer URL."""
    assert settings.plivo_auth_id and settings.plivo_auth_token and settings.plivo_phone_number
    client = plivo.RestClient(settings.plivo_auth_id, settings.plivo_auth_token.get_secret_value())

    existing = [app for app in client.applications.list(limit=20) if app.app_name == PLIVO_APP_NAME]
    if existing:
        app_id = existing[0].app_id
        client.applications.update(app_id, answer_url=answer_url, answer_method="POST")
    else:
        app_id = client.applications.create(app_name=PLIVO_APP_NAME, answer_url=answer_url, answer_method="POST").app_id

    client.numbers.update(settings.plivo_phone_number.lstrip("+"), app_id=app_id)


def main() -> None:
    settings = get_settings()
    answer_url = f"{settings.public_base_url}/telephony/answer"
    {"twilio": configure_twilio, "plivo": configure_plivo}[settings.telephony_provider](settings, answer_url)
    number = settings.twilio_phone_number if settings.telephony_provider == "twilio" else settings.plivo_phone_number
    print(f"{settings.telephony_provider}: {number} now sends incoming calls to {answer_url}")


if __name__ == "__main__":
    main()
