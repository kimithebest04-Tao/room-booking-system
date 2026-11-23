"""Shared pytest fixtures.

Every test runs against its own temporary SQLite database and its own
application instance, so the suite is repeatable and never touches the
development or the deployed database (section 4.2 of the brief).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.config import reload_settings  # noqa: E402
from backend.db import get_session  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.models import Base  # noqa: E402

TEST_ADMIN_TOKEN = "test-admin-token"


@pytest.fixture(scope="session", autouse=True)
def _test_environment() -> Iterator[None]:
    """Pin the admin token and disable schema auto-creation for the suite."""
    previous = {key: os.environ.get(key) for key in ("ADMIN_TOKEN", "AUTO_CREATE_SCHEMA", "DATABASE_URL")}
    os.environ["ADMIN_TOKEN"] = TEST_ADMIN_TOKEN
    os.environ["AUTO_CREATE_SCHEMA"] = "false"
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    reload_settings()
    yield
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    reload_settings()


@pytest.fixture()
def engine(tmp_path: Path) -> Iterator[Engine]:
    """A file based SQLite database with the application schema."""
    test_engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}, future=True
    )
    Base.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture()
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture()
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    open_session = session_factory()
    try:
        yield open_session
    finally:
        open_session.close()


@pytest.fixture()
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    """A TestClient whose session dependency points at the temporary database."""
    app = create_app()

    def override_session() -> Iterator[Session]:
        open_session = session_factory()
        try:
            yield open_session
        except Exception:
            open_session.rollback()
            raise
        finally:
            open_session.close()

    app.dependency_overrides[get_session] = override_session
    test_client = TestClient(app)
    try:
        yield test_client
    finally:
        app.dependency_overrides.clear()


@pytest.fixture()
def admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": TEST_ADMIN_TOKEN}
