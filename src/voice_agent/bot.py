"""Builds and runs the real-time voice pipeline for one phone call.

Audio flows through the pipeline like this:

    phone audio in -> speech-to-text -> user turn -> LLM -> text-to-speech -> phone audio out

Pipecat moves "frames" (audio chunks, transcripts, text) between these steps
and handles interruptions: when the caller starts talking, queued agent audio
is dropped and the LLM/TTS are cancelled.
"""

import random
from datetime import datetime

import httpx
from fastapi import WebSocket
from loguru import logger
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.groq.llm import GroqLLMService
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.workers.runner import WorkerRunner

from voice_agent.call_config import CallConfig
from voice_agent.config import Settings
from voice_agent.tools import ToolLog, build_tools_schema, register_http_tools
from voice_agent.voices import resolve_voice

# Phone audio is 8 kHz; running the whole pipeline at that rate avoids resampling.
TELEPHONY_SAMPLE_RATE = 8000

# Appended to every system prompt: the reply is spoken, not read.
VOICE_STYLE_RULES = """

You are speaking on a phone call. Your replies are converted to speech, so:
- You speak only for yourself. Never write the caller's words or answer for them.
- When you ask a question, end your reply right there and wait for the caller.
- Keep replies short: one or two sentences.
- Use plain spoken language. No lists, markdown, emojis or symbols.
- Say times and dates the way a person would, e.g. "ten thirty in the morning".

Tools:
- When you use a tool, do not announce it; the caller automatically hears a short "one moment".
- A tool result with "ok": false means it failed. Tell the caller plainly, e.g. that the
  booking system is not responding or that the slot was just taken. Never guess or
  invent a result, and never confirm something that failed."""

# Spoken while a tool runs, so the caller doesn't hear dead air.
FILLER_PHRASES = (
    "One moment, let me check.",
    "Sure, just a second.",
    "Let me look that up.",
)


def _system_prompt(config: CallConfig) -> str:
    today = datetime.now().strftime("%A, %d %B %Y")
    return f"{config.system_prompt}\n\nToday is {today}.{VOICE_STYLE_RULES}"


async def run_call(
    websocket: WebSocket,
    *,
    call_id: str,
    serializer: FrameSerializer,
    config: CallConfig,
    settings: Settings,
) -> None:
    """Run the agent on an accepted phone media stream until the call ends."""
    voice = resolve_voice(config.voice)

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=serializer,
        ),
    )

    stt = DeepgramSTTService(
        api_key=settings.deepgram_api_key.get_secret_value(),
        settings=DeepgramSTTService.Settings(model="nova-3", language=config.language),
    )
    llm = GroqLLMService(
        api_key=settings.groq_api_key.get_secret_value(),
        settings=GroqLLMService.Settings(
            model=settings.groq_model,
            reasoning_effort=settings.groq_reasoning_effort,
            system_instruction=_system_prompt(config),
        ),
    )
    tts = CartesiaTTSService(
        api_key=settings.cartesia_api_key.get_secret_value(),
        settings=CartesiaTTSService.Settings(voice=voice.cartesia_voice_id, language=config.language.split("-")[0]),
    )

    http_client = httpx.AsyncClient()
    tool_log = ToolLog()
    register_http_tools(llm, config.tools, http_client, tool_log)

    @llm.event_handler("on_function_calls_started")
    async def on_function_calls_started(_llm, _function_calls) -> None:
        await llm.push_frame(TTSSpeakFrame(random.choice(FILLER_PHRASES)))

    context = LLMContext(tools=build_tools_schema(config.tools)) if config.tools else LLMContext()
    aggregators = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            # Silero VAD detects speech; the default turn strategy then uses the
            # Smart Turn model to decide whether the caller has finished speaking.
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2)),
        ),
    )

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            aggregators.user(),
            llm,
            tts,
            transport.output(),
            aggregators.assistant(),
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            audio_in_sample_rate=TELEPHONY_SAMPLE_RATE,
            audio_out_sample_rate=TELEPHONY_SAMPLE_RATE,
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client) -> None:
        logger.info(f"Call {call_id}: connected")
        if config.first_speaker == "agent" and config.greeting:
            await worker.queue_frame(TTSSpeakFrame(config.greeting))

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client) -> None:
        logger.info(f"Call {call_id}: caller hung up")
        await worker.cancel(reason="caller hung up")

    try:
        await WorkerRunner(handle_sigint=False).run(worker)
    finally:
        await http_client.aclose()
    logger.info(f"Call {call_id}: finished, {len(tool_log.calls)} tool call(s)")
