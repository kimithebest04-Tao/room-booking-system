"""FastAPI application factory and the operational endpoints.

Run with::

    uvicorn backend.main:app --reload

The module level ``app`` object is what the server imports; ``create_app`` is
exposed so the test suite can build an isolated application instance.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from . import __version__
from .api import admin, bookings, stats
from .config import Settings, get_settings
from .db import get_engine, get_session
from .errors import DomainError
from .models import Base
from .schemas import HealthResponse
from .service import BookingService

logger = logging.getLogger(__name__)


def configure_logging(settings: Settings | None = None) -> None:
    """Send application logs to the console and to ``LOG_FILE`` (idempotent)."""
    settings = settings or get_settings()
    root = logging.getLogger()
    if getattr(root, "_room_booking_configured", False):
        return
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")

    log_path = Path(settings.log_file)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:  # pragma: no cover - read-only filesystem in some clouds
        root.warning("Could not open log file %s", log_path)
    if not any(isinstance(handler, logging.StreamHandler) for handler in root.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root.addHandler(stream_handler)

    root.setLevel(level)
    # Keep third-party loggers quiet but still visible in the file.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.INFO)
    setattr(root, "_room_booking_configured", True)
    logger.info("Logging configured (level=%s, file=%s)", settings.log_level, log_path)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create the schema on startup when ``AUTO_CREATE_SCHEMA`` is enabled."""
    settings = get_settings()
    if settings.auto_create_schema:
        Base.metadata.create_all(get_engine())
        logger.info("Database schema ensured on %s", settings.database_url.split("://")[0])
    yield
    logger.info("Application shutdown")


def _register_error_handlers(app: FastAPI) -> None:
    """Map domain errors to documented HTTP responses."""

    @app.exception_handler(DomainError)
    async def _handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        logger.warning(
            "Domain error on %s %s: %s (%s)", request.method, request.url.path, exc.code, exc.message
        )
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.as_payload()})


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application (used by uvicorn and by the tests)."""
    settings = settings or get_settings()
    configure_logging(settings)
    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Room booking REST API. The domain rule refuses overlapping reservations of the "
            "same room; back-to-back reservations are allowed."
        ),
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(bookings.router)
    app.include_router(stats.router)
    app.include_router(admin.router)
    _register_error_handlers(app)

    @app.get("/health", response_model=HealthResponse, tags=["operations"], summary="Liveness and DB check")
    def health(session: Session = Depends(get_session)) -> HealthResponse:
        """Report process health, database reachability and the maintenance flag."""
        try:
            session.execute(text("SELECT 1"))
            database = "ok"
            try:
                maintenance = BookingService(session).is_maintenance_mode()
            except Exception:  # pragma: no cover - table missing before schema creation
                maintenance = False
            status_value = "ok"
        except Exception:
            logger.exception("Health check failed")
            database = "unavailable"
            maintenance = False
            status_value = "degraded"
        return HealthResponse(
            status=status_value, database=database, version=__version__, maintenance_mode=maintenance
        )

    return app


app = create_app()
