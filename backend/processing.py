"""Pure processing layer: the functional-programming part of the project.

Every function in this module is *pure* in the sense required by the assignment
brief: given the same inputs it returns the same output, it performs no file,
database or network access, it never mutates the arguments it receives and it
has no hidden state.  The domain-specific rule of the application - detecting
overlapping bookings - lives here and nowhere else.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo

SECONDS_PER_MINUTE = 60


@dataclass(frozen=True, slots=True)
class Interval:
    """A half-open interval ``[start, end)`` with an optional record id."""

    start: datetime
    end: datetime
    identifier: int | None = None


@dataclass(frozen=True, slots=True)
class RoomUsage:
    """Utilization of one room inside a reporting window."""

    room: str
    booked_minutes: int
    open_minutes: int
    booking_count: int
    utilization: float


def normalize_text(value: str) -> str:
    """Collapse repeated whitespace and strip the ends of ``value``."""
    return " ".join(value.split())


def to_naive_local(value: datetime, zone: tzinfo) -> datetime:
    """Return ``value`` as naive local time in ``zone``.

    Timestamps are stored naive so that SQLite and PostgreSQL compare them
    identically; aware inputs are therefore converted, never silently cut.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(zone).replace(tzinfo=None)


def is_valid_interval(start: datetime, end: datetime) -> bool:
    """Return ``True`` only for a usable interval (``start`` strictly before ``end``)."""
    return (
        isinstance(start, datetime)
        and isinstance(end, datetime)
        and start.tzinfo is None
        and end.tzinfo is None
        and start < end
    )


def intervals_overlap(
    start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime
) -> bool:
    """The domain rule: do the two half-open intervals share at least one moment?

    Back-to-back intervals (``end_a == start_b``) do **not** overlap, which is
    exactly why the comparison uses ``<`` and not ``<=``.  Raises ``ValueError``
    for an interval whose end is not after its start, so that an unusable
    reservation can never be reported as "free".
    """
    for start, end in ((start_a, end_a), (start_b, end_b)):
        if not is_valid_interval(start, end):
            raise ValueError("interval end must be later than its start")
    return start_a <= end_b and start_b <= end_a


def find_overlapping(
    candidate: Interval, existing: Sequence[Interval]
) -> tuple[Interval, ...]:
    """Return every interval in ``existing`` that overlaps ``candidate``.

    The input sequence is not modified; the result keeps the input order.
    """
    if not is_valid_interval(candidate.start, candidate.end):
        raise ValueError("candidate interval is invalid")
    return tuple(
        item
        for item in existing
        if intervals_overlap(candidate.start, candidate.end, item.start, item.end)
    )


def overlap_minutes(
    start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime
) -> int:
    """Whole minutes shared by two intervals, clipped to zero when disjoint."""
    latest_start = max(start_a, start_b)
    earliest_end = min(end_a, end_b)
    if earliest_end <= latest_start:
        return 0
    return int((earliest_end - latest_start).total_seconds() // SECONDS_PER_MINUTE)


def open_windows(
    date_from: date, date_to: date, open_hour: int, close_hour: int
) -> tuple[tuple[datetime, datetime], ...]:
    """One opening window per calendar day in ``[date_from, date_to]``.

    ``close_hour`` may be 24 to mean midnight at the end of the day.
    """
    if date_to < date_from:
        return ()
    if not 0 <= open_hour < close_hour <= 24:
        raise ValueError("opening hours must satisfy 0 <= open < close <= 24")
    midnight = datetime.combine(date_from, time(0))
    windows: list[tuple[datetime, datetime]] = []
    day_index = 0
    while date_from + timedelta(days=day_index) <= date_to:
        day_start = midnight + timedelta(days=day_index, hours=open_hour)
        day_end = midnight + timedelta(days=day_index, hours=close_hour)
        windows.append((day_start, day_end))
        day_index += 1
    return tuple(windows)


def group_intervals(
    rows: Sequence[tuple[str, Interval]],
) -> dict[str, tuple[Interval, ...]]:
    """Group ``(room, interval)`` pairs by room without touching the input."""
    grouped: dict[str, list[Interval]] = {}
    for room, interval in rows:
        grouped.setdefault(room, []).append(interval)
    return {room: tuple(items) for room, items in grouped.items()}


def calculate_utilization(
    room: str,
    intervals: Sequence[Interval],
    windows: Sequence[tuple[datetime, datetime]],
) -> RoomUsage:
    """Utilization of ``room``: booked minutes inside the opening windows.

    The denominator is the total opening time of the reported window (for
    example 08:00-20:00 on each day), not wall-clock time, so a night-time
    reservation cannot push the ratio above 100 %.  Every interval is clipped
    against every opening window, so a booking that spans midnight only counts
    the part that falls inside opening hours.
    """
    booked_minutes = sum(
        overlap_minutes(interval.start, interval.end, window_start, window_end)
        for interval in intervals
        for window_start, window_end in windows
    )
    open_minutes = sum(
        int((window_end - window_start).total_seconds() // SECONDS_PER_MINUTE)
        for window_start, window_end in windows
    )
    ratio = booked_minutes / open_minutes if open_minutes else 0.0
    return RoomUsage(
        room=room,
        booked_minutes=booked_minutes,
        open_minutes=open_minutes,
        booking_count=len(intervals),
        utilization=ratio,
    )


def utilization_report(
    grouped: Mapping[str, Sequence[Interval]],
    windows: Sequence[tuple[datetime, datetime]],
) -> tuple[RoomUsage, ...]:
    """Build the utilization rows for every room, sorted by room name."""
    return tuple(
        calculate_utilization(room, intervals, windows)
        for room, intervals in sorted(grouped.items())
    )
