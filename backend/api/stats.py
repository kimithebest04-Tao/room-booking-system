"""Statistics and room-list endpoints used by the frontend charts."""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..schemas import (
    RoomListResponse,
    RoomUtilizationOut,
    UtilizationResponse,
    UtilizationWindowOut,
)
from ..service import BookingService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["statistics"])


def _default_window() -> tuple[date, date]:
    """Today and the following six days in the configured time zone."""
    today = datetime.now(ZoneInfo(get_settings().timezone)).date()
    return today, today + timedelta(days=6)


@router.get(
    "/stats/utilization",
    response_model=UtilizationResponse,
    summary="Utilization per room",
    description=(
        "Booked minutes divided by opening minutes (OPEN_HOUR..CLOSE_HOUR on each day "
        "of the window). Reservations are clipped to the opening hours."
    ),
)
def utilization(
    date_from: date | None = Query(default=None, description="First day of the window (inclusive)."),
    date_to: date | None = Query(default=None, description="Last day of the window (inclusive)."),
    session: Session = Depends(get_session),
) -> UtilizationResponse:
    """Return the utilization rows that the frontend renders as a bar chart."""
    default_from, default_to = _default_window()
    first = date_from or default_from
    last = date_to or default_to
    if last < first:
        raise HTTPException(
            status_code=422,
            detail="date_to must not be earlier than date_from",
        )

    settings = get_settings()
    service = BookingService(session, settings)
    usages, windows = service.utilization(first, last)
    open_minutes = int(sum((end - start).total_seconds() // 60 for start, end in windows))
    logger.info("Utilization computed for %s..%s over %s rooms", first, last, len(usages))
    return UtilizationResponse(
        window=UtilizationWindowOut(
            date_from=first,
            date_to=last,
            open_hour=settings.open_hour,
            close_hour=settings.close_hour,
            open_minutes=open_minutes,
        ),
        rooms=[RoomUtilizationOut(**asdict(usage)) for usage in usages],
        generated_at=datetime.now(),
    )


@router.get(
    "/rooms",
    response_model=RoomListResponse,
    summary="Rooms that already have reservations",
    description="Used by the frontend to offer existing room names as suggestions.",
)
def list_rooms(session: Session = Depends(get_session)) -> RoomListResponse:
    """Return the distinct room names currently stored."""
    service = BookingService(session)
    return RoomListResponse(items=service.list_rooms())
