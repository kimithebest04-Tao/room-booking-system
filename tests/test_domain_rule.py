"""Unit tests of the domain-specific rule (section 1.1 and 4.2 of the brief).

The expected values below are derived from the rule by hand - "two half-open
intervals overlap when each starts before the other ends" - and are not read
back from the implementation.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest

from backend.processing import (
    Interval,
    calculate_utilization,
    find_overlapping,
    intervals_overlap,
    is_valid_interval,
    open_windows,
    overlap_minutes,
)

DAY = date(2026, 12, 1)


def at(clock: str, day: date = DAY) -> datetime:
    """Build a datetime from a ``"HH:MM"`` string on ``day``."""
    hour, minute = (int(part) for part in clock.split(":"))
    return datetime.combine(day, time(hour, minute))


@pytest.mark.parametrize(
    ("start_a", "end_a", "start_b", "end_b", "expected"),
    [
        ("09:00", "10:00", "10:00", "11:00", False),  # back-to-back: the boundary case
        ("09:00", "10:00", "08:00", "09:00", False),  # back-to-back, other direction
        ("09:00", "10:00", "09:30", "10:30", True),   # partial overlap
        ("09:00", "10:00", "08:00", "11:00", True),   # containment
        ("09:00", "10:00", "09:00", "10:00", True),   # identical slots
        ("09:00", "10:00", "10:30", "11:30", False),  # disjoint
        ("09:00", "12:00", "10:00", "11:00", True),   # reversed containment
    ],
)
def test_overlap_rule_matches_the_hand_computed_table(
    start_a: str, end_a: str, start_b: str, end_b: str, expected: bool
) -> None:
    """Normal, boundary and containment cases of the conflict rule."""
    assert intervals_overlap(at(start_a), at(end_a), at(start_b), at(end_b)) is expected


@pytest.mark.parametrize(("start", "end"), [("09:00", "09:00"), ("10:00", "09:00")])
def test_invalid_intervals_are_rejected(start: str, end: str) -> None:
    """An empty or reversed interval is invalid and must not be reported as free."""
    assert is_valid_interval(at(start), at(end)) is False
    with pytest.raises(ValueError):
        intervals_overlap(at(start), at(end), at("08:00"), at("12:00"))


def test_find_overlapping_reports_conflicts_and_leaves_input_unchanged() -> None:
    """The pure helper returns the conflicting intervals and mutates nothing."""
    existing = (
        Interval(at("09:00"), at("10:00"), 1),
        Interval(at("10:00"), at("11:00"), 2),
        Interval(at("12:00"), at("13:00"), 3),
    )
    snapshot = tuple(existing)
    conflicts = find_overlapping(Interval(at("09:30"), at("10:30")), existing)
    assert [conflict.identifier for conflict in conflicts] == [1, 2]
    assert tuple(existing) == snapshot


def test_overlap_minutes_clips_and_never_returns_negative_values() -> None:
    assert overlap_minutes(at("09:00"), at("10:00"), at("09:30"), at("10:30")) == 30
    assert overlap_minutes(at("09:00"), at("10:00"), at("10:00"), at("11:00")) == 0
    assert overlap_minutes(at("09:00"), at("10:00"), at("11:00"), at("12:00")) == 0


def test_open_windows_are_one_per_calendar_day() -> None:
    windows = open_windows(date(2026, 12, 1), date(2026, 12, 3), 8, 20)
    assert len(windows) == 3
    assert windows[1][0] - windows[0][0] == timedelta(days=1)
    assert (windows[0][1] - windows[0][0]).total_seconds() / 60 == 720
    assert open_windows(date(2026, 12, 3), date(2026, 12, 1), 8, 20) == ()


def test_utilization_uses_opening_hours_as_the_denominator() -> None:
    """A 09:00-10:00 booking occupies 60 of the 720 open minutes of the day."""
    windows = open_windows(DAY, DAY, 8, 20)
    usage = calculate_utilization("Room A", (Interval(at("09:00"), at("10:00")),), windows)
    assert usage.booked_minutes == 60
    assert usage.open_minutes == 720
    assert usage.utilization == pytest.approx(60 / 720)
    assert usage.booking_count == 1


def test_utilization_ignores_time_outside_opening_hours() -> None:
    windows = open_windows(DAY, DAY, 8, 20)
    before_opening = calculate_utilization("Room A", (Interval(at("07:00"), at("08:00")),), windows)
    assert before_opening.booked_minutes == 0
    spanning_close = calculate_utilization("Room A", (Interval(at("19:00"), at("21:00")),), windows)
    assert spanning_close.booked_minutes == 60
