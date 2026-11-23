"""Unit tests of the service layer, the OOP part of the application."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from backend.errors import BookingConflictError, WritesBlockedError
from backend.schemas import BookingCreate
from backend.service import BookingService

DAY = date(2026, 12, 1)


def booking(room: str, start: str, end: str, *, booker: str = "Alice Example") -> BookingCreate:
    """Build a valid request body for ``HH:MM`` slots on the fixed test day."""
    start_hour, start_minute = (int(part) for part in start.split(":"))
    end_hour, end_minute = (int(part) for part in end.split(":"))
    return BookingCreate(
        room=room,
        booker=booker,
        purpose="unit test",
        start_at=datetime.combine(DAY, time(start_hour, start_minute)),
        end_at=datetime.combine(DAY, time(end_hour, end_minute)),
    )


def test_service_rejects_overlap_and_allows_back_to_back(session: Session) -> None:
    """The service turns the rule into a domain error and stores the rest."""
    service = BookingService(session)
    first = service.create(booking("Room A", "09:00", "10:00"))
    assert first.id is not None

    with pytest.raises(BookingConflictError) as conflict:
        service.create(booking("Room A", "09:30", "10:30"))
    assert conflict.value.status_code == 409
    assert conflict.value.details["conflicting_booking_ids"] == [first.id]

    adjacent = service.create(booking("Room A", "10:00", "11:00"))
    assert adjacent.id != first.id

    other_room = service.create(booking("Room B", "09:30", "10:30"))
    assert other_room.room == "Room B"


def test_service_blocks_writes_while_maintenance_mode_is_enabled(session: Session) -> None:
    """The maintenance flag is enforced in the database layer, not in the UI."""
    service = BookingService(session)
    assert service.is_maintenance_mode() is False

    service.set_maintenance_mode(True, updated_by="unit-test")
    assert service.is_maintenance_mode() is True
    with pytest.raises(WritesBlockedError):
        service.create(booking("Room A", "09:00", "10:00"))

    service.set_maintenance_mode(False, updated_by="unit-test")
    assert service.is_maintenance_mode() is False
    assert service.create(booking("Room A", "09:00", "10:00")).id is not None


def test_service_reports_utilization_for_the_requested_window(session: Session) -> None:
    """Two adjacent reservations on one day give two hours of booked time."""
    service = BookingService(session)
    service.create(booking("Room A", "09:00", "10:00"))
    service.create(booking("Room A", "10:00", "11:00"))

    usages, windows = service.utilization(DAY, DAY)
    assert len(windows) == 1
    assert len(usages) == 1
    usage = usages[0]
    assert usage.room == "Room A"
    assert usage.booked_minutes == 120
    assert usage.open_minutes == 720
    assert usage.utilization == pytest.approx(120 / 720)


@pytest.mark.parametrize(("start", "end"), [("10:00", "09:00"), ("09:00", "09:00")])
def test_request_validation_rejects_unusable_intervals(start: str, end: str) -> None:
    """Pydantic rejects the interval before any database work happens."""
    with pytest.raises(ValidationError):
        booking("Room A", start, end)
