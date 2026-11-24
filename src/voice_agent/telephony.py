"""Phone-provider adapters.

Everything that differs between Twilio and Plivo lives here, behind one small
interface, so the rest of the app never needs to know which provider is used:

  - answer_xml:          what to tell the provider when a call connects
                         ("stream the audio to this WebSocket")
  - verify_request:      check a webhook really came from the provider
  - create_serializer:   convert between the provider's WebSocket messages and Pipecat frames
"""

from typing import Protocol
from xml.sax.saxutils import escape

from fastapi import HTTPException, Request
from pipecat.runner.types import CallData
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.serializers.plivo import PlivoFrameSerializer
from pipecat.serializers.twilio import TwilioFrameSerializer
from plivo.utils.signature_v3 import validate_v3_signature
from twilio.request_validator import RequestValidator
from twilio.twiml.voice_response import Connect, VoiceResponse

from voice_agent.config import Settings


class TelephonyProvider(Protocol):
    name: str

    def answer_xml(self, media_ws_url: str) -> str: ...

    async def verify_request(self, request: Request) -> None: ...

    def create_serializer(self, call_data: CallData) -> FrameSerializer: ...


def _public_url_of(request: Request, settings: Settings) -> str:
    """The URL the provider called. Behind ngrok this differs from request.url."""
    url = settings.public_base_url + request.url.path
    return f"{url}?{request.url.query}" if request.url.query else url


class TwilioProvider:
    name = "twilio"

    def __init__(self, settings: Settings) -> None:
        assert settings.twilio_account_sid and settings.twilio_auth_token
        self._settings = settings
        self._account_sid = settings.twilio_account_sid
        self._auth_token = settings.twilio_auth_token.get_secret_value()

    def answer_xml(self, media_ws_url: str) -> str:
        response = VoiceResponse()
        connect = Connect()
        connect.stream(url=media_ws_url)
        response.append(connect)
        return str(response)

    async def verify_request(self, request: Request) -> None:
        form = dict(await request.form())
        signature = request.headers.get("X-Twilio-Signature", "")
        if not RequestValidator(self._auth_token).validate(_public_url_of(request, self._settings), form, signature):
            raise HTTPException(status_code=403, detail="Invalid Twilio signature")

    def create_serializer(self, call_data: CallData) -> FrameSerializer:
        return TwilioFrameSerializer(
            stream_sid=call_data.stream_id or "",
            call_sid=call_data.call_id,
            account_sid=self._account_sid,
            auth_token=self._auth_token,
        )


class PlivoProvider:
    name = "plivo"

    def __init__(self, settings: Settings) -> None:
        assert settings.plivo_auth_id and settings.plivo_auth_token
        self._settings = settings
        self._auth_id = settings.plivo_auth_id
        self._auth_token = settings.plivo_auth_token.get_secret_value()

    def answer_xml(self, media_ws_url: str) -> str:
        # Bidirectional stream: Plivo sends caller audio and plays audio we send back.
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            '<Stream bidirectional="true" keepCallAlive="true" contentType="audio/x-mulaw;rate=8000">'
            f"{escape(media_ws_url)}"
            "</Stream>"
            "</Response>"
        )

    async def verify_request(self, request: Request) -> None:
        params = dict(await request.form()) if request.method == "POST" else {}
        valid = validate_v3_signature(
            request.method,
            _public_url_of(request, self._settings),
            request.headers.get("X-Plivo-Signature-V3-Nonce", ""),
            self._auth_token,
            request.headers.get("X-Plivo-Signature-V3", ""),
            params,
        )
        if not valid:
            raise HTTPException(status_code=403, detail="Invalid Plivo signature")

    def create_serializer(self, call_data: CallData) -> FrameSerializer:
        return PlivoFrameSerializer(
            stream_id=call_data.stream_id or "",
            call_id=call_data.call_id,
            auth_id=self._auth_id,
            auth_token=self._auth_token,
        )


def get_provider(settings: Settings) -> TelephonyProvider:
    providers: dict[str, type[TwilioProvider] | type[PlivoProvider]] = {
        "twilio": TwilioProvider,
        "plivo": PlivoProvider,
    }
    return providers[settings.telephony_provider](settings)
