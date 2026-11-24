"""Mock clinic: slot listing, booking, and double-booking protection."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from voice_agent import clinic
from voice_agent.config import Settings, get_settings

# A Monday a few days out, so every opening hour is still in the future.
DAY = date.today() + timedelta(days=(7 - date.today().weekday()) % 7 or 7)


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    db_path = str(tmp_path / "clinic.db")
    clinic.init_db(db_path)
    app = FastAPI()
    app.include_router(clinic.router)
    # model_construct skips validation, so no API keys are needed for these tests.
    app.dependency_overrides[get_settings] = lambda: Settings.model_construct(clinic_db_path=db_path)
    with TestClient(app) as test_client:
        yield test_client


def book(client: TestClient, time: str = "10:00", name: str = "Priya") -> int:
    body = {"date": DAY.isoformat(), "time": time, "patientName": name}
    return client.post("/clinic/book_slot", json=body).status_code


def test_lists_all_opening_hours_when_nothing_is_booked(client: TestClient) -> None:
    response = client.post("/clinic/check_slots", json={"date": DAY.isoformat()})
    assert response.status_code == 200
    assert response.json()["available"] == [f"{h:02d}:00" for h in clinic.OPENING_HOURS]


def test_booked_slot_disappears_from_availability(client: TestClient) -> None:
    assert book(client, "10:00") == 201
    available = client.post("/clinic/check_slots", json={"date": DAY.isoformat()}).json()["available"]
    assert "10:00" not in available


def test_double_booking_is_rejected(client: TestClient) -> None:
    assert book(client, "11:00", "Priya") == 201
    assert book(client, "11:00", "Ravi") == 409


def test_concurrent_bookings_of_one_slot_have_exactly_one_winner(client: TestClient) -> None:
    with ThreadPoolExecutor(max_workers=8) as pool:
        statuses = list(pool.map(lambda n: book(client, "12:00", f"Caller {n}"), range(8)))
    assert sorted(statuses) == [201] + [409] * 7


def test_slot_outside_opening_hours_is_not_found(client: TestClient) -> None:
    assert book(client, "20:00") == 404


def test_seeding_prebooks_some_slots_and_is_repeatable(tmp_path: Path) -> None:
    db_path = str(tmp_path / "seeded.db")
    clinic.init_db(db_path, seed_bookings=True)
    clinic.init_db(db_path, seed_bookings=True)  # a restart must not change anything

    with clinic._connect(db_path) as conn:
        booked = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0]
    assert 0.15 < booked / total < 0.45


def test_bookings_list_shows_new_booking(client: TestClient) -> None:
    assert book(client, "09:00", "Jaya") == 201
    bookings = client.get("/clinic/bookings").json()
    assert {"startsAt": f"{DAY.isoformat()} 09:00", "patientName": "Jaya"}.items() <= bookings[0].items()


def test_bookings_list_is_hidden_from_the_internet(client: TestClient) -> None:
    assert client.get("/clinic/bookings", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403
