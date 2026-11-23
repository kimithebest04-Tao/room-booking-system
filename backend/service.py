"""Object-oriented service layer.

``BookingService`` is the only class that combines the pure domain rule with
persistence.  It owns the unit of work (commit / rollback), knows about the
maintenance flag and converts ORM rows into the plain values the pure functions
consume.  The API layer contains no business rules of its own.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from . import processing
from .config import Settings, get_settings
from .errors import BookingConflictError, InvalidBookingError, WritesBlockedError
from .models import MAINTENANCE_MODE_KEY, Booking, SystemState, utcnow
from .schemas import BookingCreate

logger = logging.getLogger(__name__)


class BookingService:
    """Application behaviour for reservations, utilization and maintenance mode."""

    def __init__(self, session: Session, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------ flags
    def is_maintenance_mode(self) -> bool:
        """Return whether writes are currently blocked by the controller."""
        row = self._session.get(SystemState, MAINTENANCE_MODE_KEY)
        return row is not None and row.value == "1"

    def set_maintenance_mode(self, enabled: bool, *, updated_by: str = "maintenance-controller") -> datetime:
        """Persist the maintenance flag and return the update timestamp."""
        stamp = utcnow()
        row = self._session.get(SystemState, MAINTENANCE_MODE_KEY)
        if row is None:
            row = SystemState(key=MAINTENANCE_MODE_KEY, value="1" if enabled else "0", updated_at=stamp)
            self._session.add(row)
        else:
            row.value = "1" if enabled else "0"
            row.updated_at = stamp
        self._session.commit()
        logger.warning("Maintenance mode set to %s by %s", enabled, updated_by)
        return stamp

    def maintenance_state(self) -> tuple[bool, datetime | None]:
        """Return ``(enabled, updated_at)`` for the admin endpoints."""
        row = self._session.get(SystemState, MAINTENANCE_MODE_KEY)
        if row is None:
            return False, None
        return row.value == "1", row.updated_at

    def probe_write(self, marker: str) -> str:
        """Write, read back and delete a probe row (controlled write test).

        This deliberately bypasses the maintenance flag: it is only reachable
        through the authenticated admin API and proves that the *new* database
        accepts writes while normal application writes stay blocked.
        """
        key = f"probe:{marker}"[:64]
        stamp = utcnow()
        row = SystemState(key=key, value="written", updated_at=stamp)
        self._session.add(row)
        self._session.commit()
        stored = self._session.get(SystemState, key)
        roundtrip = "missing" if stored is None else stored.value
        if stored is not None:
            self._session.delete(stored)
            self._session.commit()
        return roundtrip

    # --------------------------------------------------------------- bookings
    def create(self, payload: BookingCreate) -> Booking:
        """Create a reservation, refusing overlapping slots for the same room.

        Raises :class:`InvalidBookingError` for an unusable interval,
        :class:`WritesBlockedError` during maintenance and
        :class:`BookingConflictError` when the rule reports an overlap.
        """
        if self.is_maintenance_mode():
            raise WritesBlockedError("New reservations are refused while maintenance is running.")
        if not processing.is_valid_interval(payload.start_at, payload.end_at):
            raise InvalidBookingError()

        room = processing.normalize_text(payload.room)
        candidate = processing.Interval(payload.start_at, payload.end_at)
        existing = self._overlapping_rows(room, candidate)
        conflicts = processing.find_overlapping(candidate, existing)
        if conflicts:
            raise BookingConflictError(
                room=room,
                requested_start=payload.start_at.isoformat(),
                requested_end=payload.end_at.isoformat(),
                conflicting_booking_ids=[item.identifier for item in conflicts],
            )

        booking = Booking(
            room=room,
            booker=processing.normalize_text(payload.booker),
            purpose=payload.purpose,
            start_at=payload.start_at,
            end_at=payload.end_at,
        )
        self._session.add(booking)
        try:
            self._session.commit()
        except SQLAlchemyError:
            self._session.rollback()
            logger.exception("Failed to store a booking for room %s", room)
            raise
        self._session.refresh(booking)
        logger.info("Booking %s created: room=%s %s..%s", booking.id, room, payload.start_at, payload.end_at)
        return booking

    def _overlapping_rows(self, room: str, candidate: processing.Interval) -> list[processing.Interval]:
        """Rows of the same room that the SQL pre-filter considers candidates."""
        rows = self._session.scalars(
            select(Booking).where(
                Booking.room == room,
                Booking.end_at > candidate.start,
                Booking.start_at < candidate.end,
            )
        ).all()
        return [processing.Interval(row.start_at, row.end_at, row.id) for row in rows]

    def list_bookings(
        self,
        *,
        room: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 200,
    ) -> tuple[int, list[Booking]]:
        """Return ``(total, rows)`` for the given filters, earliest first."""
        filters = []
        if room:
            filters.append(Booking.room == processing.normalize_text(room))
        if date_from is not None:
            filters.append(Booking.end_at > datetime.combine(date_from, time(0)))
        if date_to is not None:
            filters.append(Booking.start_at < datetime.combine(date_to + timedelta(days=1), time(0)))

        total = self._session.scalar(select(func.count()).select_from(Booking).where(*filters)) or 0
        stmt = select(Booking).where(*filters).order_by(Booking.start_at, Booking.id).limit(limit)
        rows = list(self._session.scalars(stmt).all())
        return int(total), rows

    def count_bookings(self) -> int:
        """Number of stored reservations (used by the replacement verification)."""
        return int(self._session.scalar(select(func.count()).select_from(Booking)) or 0)

    def list_rooms(self) -> list[str]:
        """All distinct room names, sorted, for the frontend suggestions."""
        rows = self._session.scalars(select(Booking.room).distinct().order_by(Booking.room)).all()
        return list(rows)

    # ------------------------------------------------------------------ stats
    def utilization(
        self, date_from: date, date_to: date
    ) -> tuple[tuple[processing.RoomUsage, ...], tuple[tuple[datetime, datetime], ...]]:
        """Return utilization rows and the opening windows used as denominator."""
        windows = processing.open_windows(
            date_from, date_to, self._settings.open_hour, self._settings.close_hour
        )
        if not windows:
            return (), ()
        range_start, range_end = windows[0][0], windows[-1][1]
        rows = self._session.scalars(
            select(Booking).where(Booking.end_at > range_start, Booking.start_at < range_end)
        ).all()
        grouped = processing.group_intervals(
            [(row.room, processing.Interval(row.start_at, row.end_at, row.id)) for row in rows]
        )
        return processing.utilization_report(grouped, windows), windows
