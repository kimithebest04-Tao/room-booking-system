"""Pydantic schemas: input validation and structured JSON responses.

Validation is split deliberately: shapes and ranges are checked here, while the
domain rule itself (interval validity and overlap) is delegated to the pure
functions in :mod:`backend.processing`.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import get_settings
from .processing import is_valid_interval, normalize_text, to_naive_local


class BookingCreate(BaseModel):
    """Request body of ``POST /bookings``."""

    room: str = Field(
        min_length=1,
        max_length=64,
        description="Room name, for example 'Room A'. Whitespace is normalized.",
    )
    booker: str = Field(min_length=1, max_length=64, description="Name of the person booking.")
    purpose: str | None = Field(default=None, max_length=200, description="Optional short purpose.")
    start_at: datetime = Field(description="Start of the reservation (ISO 8601).")
    end_at: datetime = Field(description="End of the reservation, strictly after start_at.")

    @field_validator("room", "booker", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: object) -> object:
        if isinstance(value, str):
            return normalize_text(value)
        return value

    @field_validator("purpose", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            cleaned = normalize_text(value)
            return cleaned or None
        return value

    @field_validator("start_at", "end_at")
    @classmethod
    def _to_local_naive(cls, value: datetime) -> datetime:
        """Store naive local time so SQLite and PostgreSQL compare identically."""
        return to_naive_local(value, ZoneInfo(get_settings().timezone))

    @model_validator(mode="after")
    def _check_interval(self) -> "BookingCreate":
        if not is_valid_interval(self.start_at, self.end_at):
            raise ValueError("end_at must be later than start_at")
        return self


class BookingRead(BaseModel):
    """A reservation as returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    room: str
    booker: str
    purpose: str | None
    start_at: datetime
    end_at: datetime
    created_at: datetime


class BookingListResponse(BaseModel):
    """Response body of ``GET /bookings``."""

    total: int = Field(description="Number of reservations matching the filters.")
    items: list[BookingRead]


class RoomListResponse(BaseModel):
    """Response body of ``GET /rooms``."""

    items: list[str]


class RoomUtilizationOut(BaseModel):
    """Utilization of a single room inside the reporting window."""

    room: str
    booked_minutes: int
    open_minutes: int
    booking_count: int
    utilization: float = Field(description="booked_minutes / open_minutes, 0.0-1.0")


class UtilizationWindowOut(BaseModel):
    """The reporting window and its denominator."""

    date_from: date
    date_to: date
    open_hour: int
    close_hour: int
    open_minutes: int


class UtilizationResponse(BaseModel):
    """Response body of ``GET /stats/utilization``."""

    window: UtilizationWindowOut
    rooms: list[RoomUtilizationOut]
    generated_at: datetime


class MaintenanceStateRequest(BaseModel):
    """Request body of ``POST /admin/maintenance``."""

    enabled: bool


class MaintenanceStateResponse(BaseModel):
    """Response body of the admin maintenance endpoints."""

    maintenance_mode: bool
    updated_at: datetime | None = None
    detail: str = ""


class ProbeWriteResponse(BaseModel):
    """Result of the controlled write used after a database switch."""

    ok: bool
    key: str
    roundtrip: str


class HealthResponse(BaseModel):
    """Response body of ``GET /health``."""

    status: str
    database: str
    version: str
    maintenance_mode: bool


class ErrorDetail(BaseModel):
    """Machine readable error body used by every documented failure case."""

    model_config = ConfigDict(extra="allow")

    code: str
    message: str


class ErrorResponse(BaseModel):
    """Envelope for the documented error responses (409, 422, 503, 401)."""

    detail: ErrorDetail
