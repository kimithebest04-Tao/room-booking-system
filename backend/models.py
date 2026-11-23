"""SQLAlchemy ORM models for the room booking application.

Two tables are used:

* ``bookings``        - one row per reservation (the application data).
* ``system_state``    - key/value operational flags.  The maintenance mode flag
  lives *in the database* on purpose: it is copied by the backup and therefore
  still blocks writes on the freshly restored instance during a replacement.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""


def utcnow() -> datetime:
    """Current UTC time as a naive datetime (portable across SQLite/PostgreSQL)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Booking(Base):
    """A reservation of one room for one half-open time interval."""

    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    room: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    booker: Mapped[str] = mapped_column(String(64), nullable=False)
    purpose: Mapped[str | None] = mapped_column(String(200), nullable=True)
    start_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    __table_args__ = (Index("ix_bookings_room_start_at", "room", "start_at"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging helper
        return (
            f"Booking(id={self.id!r}, room={self.room!r}, "
            f"start_at={self.start_at!r}, end_at={self.end_at!r})"
        )


class SystemState(Base):
    """Small key/value table for operational flags such as maintenance mode."""

    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)


MAINTENANCE_MODE_KEY = "maintenance_mode"
