"""Mock dental clinic: an appointment diary in SQLite, exposed as two HTTP tools.

    POST /clinic/check_slots  {"date": "2026-11-27"}
        -> {"date": "2026-11-27", "available": ["09:00", "10:00", ...]}

    POST /clinic/book_slot    {"date": "2026-11-27", "time": "10:00", "patientName": "Priya"}
        -> 201 {"bookingId": 7, ...}
        -> 409 if the slot is already booked, 404 if the clinic has no such slot

    GET  /clinic/bookings     every upcoming booking (local machine only; it lists patient names)

Double booking is prevented by the database, not by application code:
`bookings.slot_id` is UNIQUE, so if two calls race for the same slot the
second INSERT fails, even when both checked the slot was free a moment before.
"""

import asyncio
import random
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from voice_agent.config import Settings, get_settings

OPENING_HOURS = range(9, 17)  # one-hour slots starting 09:00 ... 16:00
DAYS_AHEAD = 14
CLOSED_WEEKDAYS = {6}  # Sunday

# Demo data: roughly this share of slots is pre-booked by made-up patients.
SEED_BOOKED_SHARE = 0.3
SEED_PATIENT_NAMES = ("Arjun Mehta", "Kavya Iyer", "Rahul Nair", "Divya Rao", "Suresh Pillai", "Meera Krishnan")

SCHEMA = """
CREATE TABLE IF NOT EXISTS slots (
    id        INTEGER PRIMARY KEY,
    starts_at TEXT NOT NULL UNIQUE          -- 'YYYY-MM-DD HH:MM', clinic local time
);
CREATE TABLE IF NOT EXISTS bookings (
    id           INTEGER PRIMARY KEY,
    slot_id      INTEGER NOT NULL UNIQUE REFERENCES slots(id),  -- one booking per slot
    patient_name TEXT NOT NULL,
    phone        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


@contextmanager
def _connect(db_path: str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:  # commits on success, rolls back on error
            yield conn
    finally:
        conn.close()


def init_db(db_path: str, *, today: date | None = None, seed_bookings: bool = False) -> None:
    """Create the tables, make sure the next DAYS_AHEAD days have slots, and optionally pre-book some."""
    today = today or date.today()
    days = (today + timedelta(days=n) for n in range(DAYS_AHEAD))
    starts = [
        f"{day.isoformat()} {hour:02d}:00"
        for day in days
        if day.weekday() not in CLOSED_WEEKDAYS
        for hour in OPENING_HOURS
    ]
    with _connect(db_path) as conn:
        conn.executescript(SCHEMA)
        conn.executemany("INSERT OR IGNORE INTO slots (starts_at) VALUES (?)", [(s,) for s in starts])
        if seed_bookings:
            _seed_bookings(conn, starts)


def _seed_bookings(conn: sqlite3.Connection, starts: list[str]) -> None:
    """Pre-book the same slots on every start, so demos are repeatable.

    Each slot's fate is decided by a random generator seeded with the slot's own
    time, so the choice does not depend on run order or on the current date.
    INSERT OR IGNORE leaves slots that real callers booked untouched.
    """
    seeded = []
    for starts_at in starts:
        rng = random.Random(starts_at)
        if rng.random() < SEED_BOOKED_SHARE:
            seeded.append((rng.choice(SEED_PATIENT_NAMES), starts_at))
    conn.executemany(
        "INSERT OR IGNORE INTO bookings (slot_id, patient_name) SELECT id, ? FROM slots WHERE starts_at = ?",
        seeded,
    )


# ---- API ---------------------------------------------------------------------------


class CheckSlotsRequest(BaseModel):
    date: date


class CheckSlotsResponse(BaseModel):
    date: date
    available: list[str] = Field(description="Free start times, 'HH:MM'")


class BookSlotRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    date: date
    time: time
    patient_name: str = Field(alias="patientName", min_length=1)
    phone: str | None = None


class BookSlotResponse(BaseModel):
    booking_id: int = Field(serialization_alias="bookingId")
    date: date
    time: str
    patient_name: str = Field(serialization_alias="patientName")


class BookingView(BaseModel):
    starts_at: str = Field(serialization_alias="startsAt")
    patient_name: str = Field(serialization_alias="patientName")
    phone: str | None
    booked_at: str = Field(serialization_alias="bookedAt", description="UTC")


router = APIRouter(prefix="/clinic", tags=["mock clinic"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


async def _simulate_failures(settings: Settings) -> None:
    """Optionally misbehave, to demo how the agent handles a broken backend."""
    if settings.clinic_simulate == "slow":
        await asyncio.sleep(30)
    elif settings.clinic_simulate == "error":
        raise HTTPException(status_code=500, detail="Booking system is down")


@router.post("/check_slots")
async def check_slots(body: CheckSlotsRequest, settings: SettingsDep) -> CheckSlotsResponse:
    await _simulate_failures(settings)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    with _connect(settings.clinic_db_path) as conn:
        rows = conn.execute(
            """
            SELECT substr(s.starts_at, 12, 5)
            FROM slots s LEFT JOIN bookings b ON b.slot_id = s.id
            WHERE b.id IS NULL AND s.starts_at LIKE ? || '%' AND s.starts_at > ?
            ORDER BY s.starts_at
            """,
            (body.date.isoformat(), now),
        ).fetchall()
    return CheckSlotsResponse(date=body.date, available=[r[0] for r in rows])


@router.post("/book_slot", status_code=201)
async def book_slot(body: BookSlotRequest, settings: SettingsDep) -> BookSlotResponse:
    await _simulate_failures(settings)
    starts_at = f"{body.date.isoformat()} {body.time.strftime('%H:%M')}"
    with _connect(settings.clinic_db_path) as conn:
        slot = conn.execute("SELECT id FROM slots WHERE starts_at = ?", (starts_at,)).fetchone()
        if slot is None:
            raise HTTPException(status_code=404, detail=f"The clinic has no slot at {starts_at}")
        try:
            cursor = conn.execute(
                "INSERT INTO bookings (slot_id, patient_name, phone) VALUES (?, ?, ?)",
                (slot[0], body.patient_name, body.phone),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail=f"The slot at {starts_at} is already booked") from None
    assert cursor.lastrowid is not None
    return BookSlotResponse(
        booking_id=cursor.lastrowid,
        date=body.date,
        time=body.time.strftime("%H:%M"),
        patient_name=body.patient_name,
    )


@router.get("/bookings")
async def list_bookings(request: Request, settings: SettingsDep) -> list[BookingView]:
    # Requests through the ngrok tunnel carry X-Forwarded-For; only direct local ones don't.
    if "x-forwarded-for" in request.headers:
        raise HTTPException(status_code=403, detail="Bookings can only be viewed on the server machine")
    today = date.today().isoformat()
    with _connect(settings.clinic_db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.starts_at, b.patient_name, b.phone, b.created_at
            FROM bookings b JOIN slots s ON s.id = b.slot_id
            WHERE s.starts_at >= ?
            ORDER BY s.starts_at
            """,
            (today,),
        ).fetchall()
    return [BookingView(starts_at=r[0], patient_name=r[1], phone=r[2], booked_at=r[3]) for r in rows]
