"""Database engine and session lifecycle (SQLAlchemy ORM layer).

The engine is created lazily so that importing the application (for example in
unit tests) never touches a real database file.  Sessions are always closed and
rolled back on failure by :func:`get_session`, which is the single session
lifecycle owner used by the API layer.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _ensure_sqlite_directory(url: str) -> None:
    """Create the parent directory of a file based SQLite database."""
    if not url.startswith("sqlite"):
        return
    database = make_url(url).database
    if not database or database == ":memory:":
        return
    path = Path(database)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)


def _normalise_url(url: str) -> str:
    """Route libpq-style PostgreSQL URLs to the installed psycopg 3 driver.

    Neon and Render both hand out ``postgresql://`` / ``postgres://`` strings,
    which SQLAlchemy would send to psycopg2.  Only ``psycopg`` (version 3) is
    pinned in ``requirements.txt``, so the driver is made explicit here.
    """
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def build_engine(url: str) -> Engine:
    """Build a new engine for ``url`` (used by the controller for backup targets)."""
    url = _normalise_url(url)
    _ensure_sqlite_directory(url)
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        # Streamlit and FastAPI run in different threads than the test client.
        connect_args["check_same_thread"] = False
    logger.debug("Creating database engine for %s", make_url(url).render_as_string(hide_password=True))
    return create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args)


def get_engine() -> Engine:
    """Return the process-wide engine for the configured ``DATABASE_URL``."""
    global _engine
    if _engine is None:
        _engine = build_engine(get_settings().database_url)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory bound to :func:`get_engine`."""
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _session_factory


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yield a session and guarantee cleanup."""
    session = get_session_factory()()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_cache() -> None:
    """Drop the cached engine/factory (tests and the start script use this)."""
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
