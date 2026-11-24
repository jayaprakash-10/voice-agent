# Voice Agent: phone receptionist for a dental clinic

An AI agent that answers real phone calls, holds a natural conversation and books
appointments in a clinic database.

**Try it:** call **+1 346 480 7893** and ask to book an appointment.

```
Caller:  Can I book something tomorrow?
Agent:   One moment, let me check. ... Tomorrow I have nine, ten, twelve noon or two. Which would you like?
Caller:  Twelve.
Agent:   May I have your name?
Caller:  Jaya.
Agent:   So that's tomorrow at twelve noon for Jaya. Shall I book it?
Caller:  Yes.
Agent:   You're booked for tomorrow at twelve noon.
```

## How it works

```
Phone ──► Plivo ──webhook──► /telephony/answer   (signature checked, replies with <Stream> XML)
                └─WebSocket─► /telephony/media    (8 kHz audio both ways)
                                   │
              Pipecat pipeline:    ▼
   Deepgram STT ─► Silero VAD + Smart Turn ─► Groq LLM ─► Cartesia TTS ─► back to caller
                                                 │
                                        tool calls (HTTP)
                                                 ▼
                              Mock clinic API ─► SQLite (clinic.db)
```

| Stage | Service | Why |
|---|---|---|
| Telephony | Plivo (Twilio also supported) | Twilio's free trial blocks media streams |
| Speech-to-text | Deepgram `nova-3`, streaming | Low latency, supports `en-IN` |
| End of turn | Silero VAD + Pipecat Smart Turn | Doesn't cut callers off at mid-sentence pauses |
| LLM | Groq `qwen/qwen3.8-27b`, reasoning off | Fastest first token; see the note below |
| Text-to-speech | Cartesia `sonic`, Indian English voice | Streaming, natural voice |

**Note on the LLM choice:** in a replay of a real call, `openai/gpt-oss-120b` invented
the caller's replies ("I'll take twelve… John Doe") and booked by itself in 5 of 5 runs.
Qwen did this in 0 of 7 runs and was faster (0.50 s compared with 0.86 s).

## Booking tools

The agent has two HTTP tools, `check_slots` and `book_slot`, served by a mock clinic
([clinic.py](src/voice_agent/clinic.py)):

| Endpoint | Body | Result |
|---|---|---|
| `POST /clinic/check_slots` | `{"date": "2026-11-24"}` | `{"available": ["09:00", "10:00", ...]}` |
| `POST /clinic/book_slot` | `{"date", "time", "patientName"}` | `201`, `409` if already booked, `404` if no such slot |
| `GET /clinic/bookings` | | All upcoming bookings (only from the server machine) |

- **No double booking:** `bookings.slot_id` is `UNIQUE`, so the database rejects a second
  booking even when two calls race. This is tested with 8 concurrent bookings: exactly 1 wins.
- **Filler speech:** a short "One moment, let me check" plays as soon as a tool starts.
- **Errors and timeouts are spoken:** the tool runner ([tools.py](src/voice_agent/tools.py))
  never raises. A timeout (4 s), HTTP error or unreachable server becomes
  `{"ok": false, "error": "..."}`, and the agent explains the problem to the caller.
  Set `CLINIC_SIMULATE=slow` or `error` to demo this.
- **Guards against acting alone:** `book_slot` is refused unless it directly follows
  something the caller said, and unless the caller actually said the patient name.
  The LLM is then told to ask the caller.
- **Demo data:** about 30% of slots are pre-booked with made-up patients. The same slots are
  booked on every start, so demos are repeatable. `clinic.db` is committed, so it works out of the box.

## Setup

Requirements: Python 3.12, [uv](https://docs.astral.sh/uv/), [ngrok](https://ngrok.com/),
and accounts with Plivo, Deepgram, Groq and Cartesia.

```bash
uv sync                                   # install dependencies
cp .env.example .env                      # then fill in your keys
uv run python scripts/check_keys.py       # verify every key works (never prints them)

ngrok http 8000                           # copy the https URL into PUBLIC_URL in .env
uv run python scripts/configure_number.py # point your Plivo number at this server
uv run voice-agent                        # start the server on port 8000
```

Then call your Plivo number. Rerun `configure_number.py` whenever the ngrok URL changes.

## Tests

```bash
uv run pytest
```

There are 18 tests covering slot listing, booking, double booking (including concurrent
bookings), demo-data seeding, the tool runner's handling of success, HTTP errors, timeouts and
unreachable servers, and the booking guards.

## Project layout

| File | Purpose |
|---|---|
| [server.py](src/voice_agent/server.py) | FastAPI app: telephony webhook, media WebSocket, clinic routes |
| [telephony.py](src/voice_agent/telephony.py) | Plivo and Twilio adapters: answer XML, signature check, audio serializer |
| [bot.py](src/voice_agent/bot.py) | The Pipecat pipeline for one call, the system prompt and filler speech |
| [tools.py](src/voice_agent/tools.py) | Generic HTTP tool runner, guards and tool-call log |
| [clinic.py](src/voice_agent/clinic.py) | Mock clinic: SQLite schema, seeding and endpoints |
| [call_config.py](src/voice_agent/call_config.py) | Per-call configuration (the `POST /calls` body) |
| [defaults.py](src/voice_agent/defaults.py) | Default inbound config: the clinic receptionist and its tools |
| [config.py](src/voice_agent/config.py) | Settings read from `.env` |
| [voices.py](src/voice_agent/voices.py) | Voice names mapped to Cartesia voice IDs |

## Known gaps (work in progress)

- **Not built yet:** the `POST /calls`, `GET /calls/:id` and `POST /calls/:id/end` API and outbound calls.
- **Not built yet:** ignoring backchannels ("mm-hmm", coughs), inactivity prompts, the maximum call
  duration, and the agent hanging up by itself.
- **Not built yet:** the post-call transcript, recording, cost breakdown, webhook and knowledge-base lookup.
- The caller's phone number is not stored with a booking.
- If the caller interrupts while a booking is in progress, the agent stops waiting for the
  result. The booking may still succeed.
- Qwen sometimes says "let me check" before the automatic filler, so the caller hears two short lines.
- The Groq free tier rate-limits quickly. Heavy testing needs a paid tier.
