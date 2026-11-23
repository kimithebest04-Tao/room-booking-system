"""API integration test: the real request -> processing -> database -> response path.

The application, the routes and the SQLAlchemy code are the production code; only
the database connection points at an isolated, recreatable SQLite file created by
the fixture (section 4.3 of the brief).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def booking_body(room: str, start: str, end: str, *, booker: str = "Alice Example") -> dict[str, str]:
    """A JSON request body for the fixed test day."""
    return {
        "room": room,
        "booker": booker,
        "purpose": "integration test",
        "start_at": f"2026-12-01T{start}:00",
        "end_at": f"2026-12-01T{end}:00",
    }


def test_booking_api_end_to_end(client: TestClient, admin_headers: dict[str, str]) -> None:
    """Create, list, conflict, boundary, validation, statistics and write block."""
    created = client.post("/bookings", json=booking_body("Room A", "09:00", "10:00"))
    assert created.status_code == 201
    created_body = created.json()
    identifier = created_body["id"]
    assert created_body["room"] == "Room A"
    assert created_body["start_at"].startswith("2026-12-01T09:00")

    listed = client.get("/bookings", params={"room": "Room A"})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == identifier

    conflict = client.post("/bookings", json=booking_body("Room A", "09:30", "10:30"))
    assert conflict.status_code == 409
    detail = conflict.json()["detail"]
    assert detail["code"] == "booking_conflict"
    assert detail["conflicting_booking_ids"] == [identifier]

    adjacent = client.post("/bookings", json=booking_body("Room A", "10:00", "11:00"))
    assert adjacent.status_code == 201, "back-to-back reservations must be allowed"

    other_room = client.post("/bookings", json=booking_body("Room B", "09:30", "10:30"))
    assert other_room.status_code == 201

    invalid = client.post("/bookings", json=booking_body("Room A", "10:00", "09:00"))
    assert invalid.status_code == 422

    total = client.get("/bookings").json()["total"]
    assert total == 3, "the database really holds the accepted reservations"

    stats = client.get(
        "/stats/utilization", params={"date_from": "2026-12-01", "date_to": "2026-12-01"}
    )
    assert stats.status_code == 200
    stats_body = stats.json()
    assert stats_body["window"]["open_minutes"] == 720
    rooms = {row["room"]: row for row in stats_body["rooms"]}
    assert rooms["Room A"]["booked_minutes"] == 120
    assert rooms["Room B"]["booked_minutes"] == 60
    assert rooms["Room A"]["utilization"] == pytest.approx(120 / 720)

    unauthorised = client.post("/admin/maintenance", json={"enabled": True})
    assert unauthorised.status_code == 401

    enabled = client.post("/admin/maintenance", json={"enabled": True}, headers=admin_headers)
    assert enabled.status_code == 200
    assert enabled.json()["maintenance_mode"] is True

    blocked = client.post("/bookings", json=booking_body("Room C", "14:00", "15:00"))
    assert blocked.status_code == 503
    assert blocked.json()["detail"]["code"] == "writes_blocked"

    disabled = client.post("/admin/maintenance", json={"enabled": False}, headers=admin_headers)
    assert disabled.status_code == 200
    assert disabled.json()["maintenance_mode"] is False

    read_after = client.get("/bookings", params={"date_from": "2026-12-01", "date_to": "2026-12-01"})
    assert read_after.status_code == 200
    assert read_after.json()["total"] == 3

    rooms_endpoint = client.get("/rooms")
    assert rooms_endpoint.status_code == 200
    assert rooms_endpoint.json()["items"] == ["Room A", "Room B"]

    empty_stats = client.get(
        "/stats/utilization", params={"date_from": "2027-01-04", "date_to": "2027-01-04"}
    )
    assert empty_stats.status_code == 200
    assert empty_stats.json()["rooms"] == []

    bad_window = client.get(
        "/stats/utilization", params={"date_from": "2026-12-05", "date_to": "2026-12-01"}
    )
    assert bad_window.status_code == 422
