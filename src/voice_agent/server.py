"""HTTP + WebSocket server.

Inbound call flow:
  1. Someone dials our phone number.
  2. The provider (Twilio/Plivo) requests /telephony/answer; we reply with XML
     telling it to stream the call audio to the /telephony/media WebSocket.
  3. The provider streams the audio over that WebSocket and we run the agent on it.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request, Response, WebSocket
from loguru import logger
from pipecat.runner.utils import parse_telephony_websocket

from voice_agent import clinic
from voice_agent.bot import run_call
from voice_agent.config import get_settings
from voice_agent.defaults import default_inbound_config
from voice_agent.telephony import get_provider


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    clinic.init_db(settings.clinic_db_path, seed_bookings=settings.clinic_seed_bookings)
    yield


app = FastAPI(title="Voice Agent", lifespan=lifespan)
app.include_router(clinic.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.api_route("/telephony/answer", methods=["GET", "POST"])
async def answer_call(request: Request) -> Response:
    """Answer a call by telling the provider to stream its audio to us."""
    settings = get_settings()
    provider = get_provider(settings)
    if settings.validate_webhook_signature:
        await provider.verify_request(request)
    xml = provider.answer_xml(f"{settings.public_ws_url}/telephony/media")
    return Response(content=xml, media_type="application/xml")


@app.websocket("/telephony/media")
async def media_stream(websocket: WebSocket) -> None:
    """Receive the call audio and run the agent on it."""
    settings = get_settings()
    provider = get_provider(settings)

    await websocket.accept()
    detected, call_data = await parse_telephony_websocket(websocket)
    if detected != provider.name or not call_data.stream_id or not call_data.call_id:
        logger.warning(f"Rejected media stream: expected {provider.name}, got {detected}")
        await websocket.close(code=1008)
        return

    logger.info(f"Media stream started for {provider.name} call {call_data.call_id}")
    await run_call(
        websocket,
        call_id=call_data.call_id,
        serializer=provider.create_serializer(call_data),
        config=default_inbound_config(settings),
        settings=settings,
    )


def main() -> None:
    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
