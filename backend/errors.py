"""Domain errors translated into HTTP responses by the API layer.

Keeping these exceptions free of HTTP details lets the service layer stay
usable outside a web request (tests, maintenance checks, scripts).
"""

from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base class for errors caused by the application rules."""

    status_code: int = 400
    code: str = "domain_error"
    default_message: str = "The request violates an application rule."

    def __init__(self, message: str | None = None, **details: Any) -> None:
        self.message = message or self.default_message
        self.details: dict[str, Any] = details
        super().__init__(self.message)

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        payload.update(self.details)
        return payload


class InvalidBookingError(DomainError):
    """The submitted interval or field values are not usable."""

    status_code = 422
    code = "invalid_booking"
    default_message = "The booking interval is invalid."


class BookingConflictError(DomainError):
    """Another reservation of the same room overlaps the requested interval."""

    status_code = 409
    code = "booking_conflict"
    default_message = "The requested slot overlaps an existing reservation."


class WritesBlockedError(DomainError):
    """The application is in maintenance mode; writes are temporarily refused."""

    status_code = 503
    code = "writes_blocked"
    default_message = "Writes are blocked while maintenance is in progress."


class AdminAuthError(DomainError):
    """The maintenance/admin token is missing or wrong."""

    status_code = 401
    code = "admin_unauthorized"
    default_message = "A valid X-Admin-Token header is required."
