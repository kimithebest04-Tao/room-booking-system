"""Authenticated maintenance-control endpoints.

These are administrative controls, not application features: they are excluded
from the three "meaningful" endpoints required by the brief and every one of
them requires the ``X-Admin-Token`` header (section 6.4, resource protection).
"""

from __future__ import annotations

import logging
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..errors import AdminAuthError
from ..schemas import (
    ErrorResponse,
    MaintenanceStateRequest,
    MaintenanceStateResponse,
    ProbeWriteResponse,
)
from ..service import BookingService

logger = logging.getLogger(__name__)


def require_admin(
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
) -> None:
    """Reject any request that does not present the configured admin token."""
    expected = get_settings().admin_token
    if not x_admin_token or not secrets.compare_digest(x_admin_token, expected):
        logger.warning("Admin request rejected: missing or invalid token")
        raise AdminAuthError()


router = APIRouter(
    prefix="/admin",
    tags=["maintenance"],
    dependencies=[Depends(require_admin)],
    responses={401: {"model": ErrorResponse, "description": "Missing or invalid admin token."}},
)


@router.post(
    "/maintenance",
    response_model=MaintenanceStateResponse,
    summary="Enable or disable maintenance mode",
    description="While enabled, POST /bookings answers 503. The flag is stored in the database.",
)
def set_maintenance(
    payload: MaintenanceStateRequest, session: Session = Depends(get_session)
) -> MaintenanceStateResponse:
    """Block or re-enable application writes."""
    service = BookingService(session)
    updated_at = service.set_maintenance_mode(payload.enabled, updated_by="admin-api")
    return MaintenanceStateResponse(
        maintenance_mode=payload.enabled,
        updated_at=updated_at,
        detail="maintenance mode enabled" if payload.enabled else "maintenance mode disabled",
    )


@router.get(
    "/maintenance",
    response_model=MaintenanceStateResponse,
    summary="Read the maintenance flag",
)
def read_maintenance(session: Session = Depends(get_session)) -> MaintenanceStateResponse:
    """Return the current maintenance flag and when it was changed."""
    service = BookingService(session)
    enabled, updated_at = service.maintenance_state()
    return MaintenanceStateResponse(
        maintenance_mode=enabled,
        updated_at=updated_at,
        detail="read from system_state",
    )


@router.post(
    "/probe-write",
    response_model=ProbeWriteResponse,
    summary="Controlled write test for a freshly restored database",
    description="Writes, reads back and deletes one row; bypasses the maintenance flag on purpose.",
)
def probe_write(session: Session = Depends(get_session)) -> ProbeWriteResponse:
    """Prove that the active database accepts writes while application writes stay blocked."""
    marker = secrets.token_hex(6)
    service = BookingService(session)
    roundtrip = service.probe_write(marker)
    logger.info("Probe write %s roundtrip=%s", marker, roundtrip)
    return ProbeWriteResponse(ok=roundtrip == "written", key=f"probe:{marker}", roundtrip=roundtrip)
