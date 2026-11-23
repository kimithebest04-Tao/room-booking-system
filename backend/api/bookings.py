"""Reservation endpoints: create a booking and list bookings."""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from ..db import get_session
from ..schemas import BookingCreate, BookingListResponse, BookingRead, ErrorResponse
from ..service import BookingService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/bookings", tags=["bookings"])


@router.post(
    "",
    response_model=BookingRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a reservation",
    responses={
        409: {"model": ErrorResponse, "description": "The slot overlaps an existing reservation."},
        422: {"description": "Invalid input (for example end_at not after start_at)."},
        503: {"model": ErrorResponse, "description": "Writes are blocked during maintenance."},
    },
)
def create_booking(payload: BookingCreate, session: Session = Depends(get_session)) -> BookingRead:
    """Store a reservation unless the room is already taken in that interval."""
    service = BookingService(session)
    booking = service.create(payload)
    return BookingRead.model_validate(booking)


@router.get(
    "",
    response_model=BookingListResponse,
    summary="List reservations",
    description="Reservations are returned earliest first; all filters are optional.",
)
def list_bookings(
    room: str | None = Query(default=None, description="Exact room name after whitespace normalization."),
    date_from: date | None = Query(default=None, description="Keep reservations ending after this date."),
    date_to: date | None = Query(default=None, description="Keep reservations starting before this date."),
    limit: int = Query(default=50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> BookingListResponse:
    """Return the reservations matching the filters plus the unfiltered-by-limit total."""
    service = BookingService(session)
    total, rows = service.list_bookings(room=room, date_from=date_from, date_to=date_to, limit=limit)
    logger.debug("Listing bookings: total=%s returned=%s", total, len(rows))
    return BookingListResponse(total=total, items=[BookingRead.model_validate(row) for row in rows])
