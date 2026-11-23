"""Create deterministic synthetic demo data for the booking application.

    python scripts/seed.py --date 2026-12-01

The file contains no real personal data: every room and booker name is
fictional.  Running the script twice is safe - conflicting slots are skipped
instead of duplicated, and the boundary cases (back-to-back reservations) are
created on purpose so the rule can be demonstrated in the interface.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import get_settings  # noqa: E402
from backend.db import get_engine, get_session_factory  # noqa: E402
from backend.errors import BookingConflictError, WritesBlockedError  # noqa: E402
from backend.models import Base  # noqa: E402
from backend.schemas import BookingCreate  # noqa: E402
from backend.service import BookingService  # noqa: E402

BOOKERS = ("Alice Example", "Bruno Sample", "Chen Demo", "Dora Placeholder")
PURPOSES = ("project sync", "interview practice", "workshop", "demo review")


def build_requests(first_day: date) -> list[BookingCreate]:
    """Return a fixed set of reservations, including adjacent boundary cases."""
    # (day offset, room, start hour, end hour, booker index)
    slots = [
        (0, "Room A", 9, 10, 0),
        (0, "Room A", 10, 11, 1),   # back-to-back with the previous booking: allowed
        (0, "Room B", 14, 15, 2),
        (0, "Room B", 15, 17, 3),   # second boundary pair
        (1, "Room A", 8, 9, 1),
        (1, "Room B", 10, 12, 2),
        (1, "Room C", 13, 18, 0),
        (2, "Room A", 9, 12, 3),
        (2, "Room C", 14, 16, 1),
        (2, "Room C", 16, 18, 2),   # third boundary pair
    ]
    requests: list[BookingCreate] = []
    for index, (day_offset, room, start_hour, end_hour, person) in enumerate(slots):
        day = first_day + timedelta(days=day_offset)
        requests.append(
            BookingCreate(
                room=room,
                booker=BOOKERS[person],
                purpose=PURPOSES[index % len(PURPOSES)],
                start_at=datetime.combine(day, time(start_hour)),
                end_at=datetime.combine(day, time(end_hour)),
            )
        )
    return requests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed synthetic booking data")
    parser.add_argument("--date", default=None, help="First day of the data set (YYYY-MM-DD), default tomorrow")
    args = parser.parse_args(argv)

    first_day = date.fromisoformat(args.date) if args.date else date.today() + timedelta(days=1)
    settings = get_settings()
    engine = get_engine()
    Base.metadata.create_all(engine)
    print(f"[seed] database: {settings.database_url}")
    print(f"[seed] first day: {first_day}")

    created = skipped = blocked = 0
    with get_session_factory()() as session:
        service = BookingService(session, settings)
        for request in build_requests(first_day):
            try:
                booking = service.create(request)
            except BookingConflictError as exc:
                skipped += 1
                print(f"[seed] skipped overlapping slot: {request.room} {request.start_at} ({exc.code})")
            except WritesBlockedError:
                blocked += 1
                print("[seed] writes are blocked by maintenance mode; stopping")
                break
            else:
                created += 1
                print(f"[seed] created #{booking.id} {booking.room} {booking.start_at:%Y-%m-%d %H:%M}-{booking.end_at:%H:%M}")

    print(f"[seed] created={created} skipped={skipped} blocked={blocked}")
    return 0 if blocked == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
