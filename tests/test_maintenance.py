"""Unit tests for maintenance decisions and the backup/restore operations.

Everything here runs against simulated provider, deployment and backend
responses plus temporary SQLite databases: no live service, no real cloud
resource and no secret is used (section 4.2 of the brief).
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.models import Base, Booking
from backend.schemas import BookingCreate
from backend.service import BookingService
from maintenance.backup import (
    InvalidBackupError,
    create_backup,
    load_backup,
    restore_backup,
    rule_snapshot,
    verify_restore,
)
from maintenance.controller import MaintenanceController, ReplacementError, check_identifier_generation
from maintenance.provider import (
    DatabaseProvider,
    InstanceInfo,
    ProviderUnavailableError,
)

DAY = date(2026, 12, 1)


# --------------------------------------------------------------------------- fakes
class FakeProvider(DatabaseProvider):
    """In-memory stand-in for the Neon API with recorded behaviour."""

    def __init__(
        self,
        *,
        source_url: str,
        target_url: str,
        expires_in_hours: float | None = 48.0,
        fail_create_times: int = 0,
    ) -> None:
        expires_at = (
            datetime.now(timezone.utc) + timedelta(hours=expires_in_hours)
            if expires_in_hours is not None
            else None
        )
        self._source = InstanceInfo(
            identifier="src-1",
            name="room-booking-prod",
            ready=True,
            connection_string=source_url,
            expires_at=expires_at,
        )
        self._instances: dict[str, InstanceInfo] = {"src-1": self._source}
        self._target_url = target_url
        self._fail_create_times = fail_create_times
        self.create_calls = 0
        self.created_names: list[str] = []
        self.deleted: list[str] = []

    async def get_instance(self, identifier: str | None = None) -> InstanceInfo:
        key = identifier or "src-1"
        if key not in self._instances:
            raise ProviderUnavailableError(f"instance {key} does not exist")
        return self._instances[key]

    async def list_instances(self) -> list[InstanceInfo]:
        return list(self._instances.values())

    async def create_instance(self, name: str) -> InstanceInfo:
        self.create_calls += 1
        if self._fail_create_times > 0:
            self._fail_create_times -= 1
            raise ProviderUnavailableError("simulated provider timeout during creation")
        self.created_names.append(name)
        info = InstanceInfo(
            identifier=f"new-{self.create_calls}",
            name=name,
            ready=True,
            connection_string=self._target_url,
        )
        self._instances[info.identifier] = info
        return info

    async def delete_instance(self, identifier: str) -> None:
        self.deleted.append(identifier)
        self._instances.pop(identifier, None)


class FakeBackend:
    """Records write blocks and answers the read endpoints the controller uses."""

    def __init__(self, *, bookings: int = 3, probe_ok: bool = True) -> None:
        self.maintenance = False
        self.bookings = bookings
        self.probe_ok = probe_ok
        self.calls: list[tuple[str, Any]] = []

    async def health(self) -> dict[str, Any]:
        return {"status": "ok", "database": "ok", "maintenance_mode": self.maintenance}

    async def get_maintenance(self) -> bool:
        return self.maintenance

    async def set_maintenance(self, enabled: bool) -> bool:
        self.maintenance = enabled
        self.calls.append(("set_maintenance", enabled))
        return enabled

    async def count_bookings(self, limit: int = 1) -> int:
        return self.bookings

    async def probe_write(self) -> bool:
        return self.probe_ok

    async def wait_until_healthy(self, **kwargs: Any) -> dict[str, Any]:
        return await self.health()


class FakeDeployer:
    """Simulated Render API: records the new DATABASE_URL and the deployments."""

    def __init__(self, *, fail_deploys: int = 0) -> None:
        self.env: dict[str, str] = {}
        self.deploys = 0
        self.fail_deploys = fail_deploys

    async def update_env_var(self, key: str, value: str) -> None:
        self.env[key] = value

    async def trigger_deploy(self) -> str:
        if self.fail_deploys > 0:
            self.fail_deploys -= 1
            raise ProviderUnavailableError("simulated deployment failure")
        self.deploys += 1
        return f"deploy-{self.deploys}"

    async def wait_for_deploy(self, deploy_id: str) -> str:
        return "live"


# ------------------------------------------------------------------------ helpers
def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "database_url": f"sqlite:///{tmp_path / 'app.db'}",
        "admin_token": "test-admin-token",
        "maintenance_state_dir": str(tmp_path / "maintenance_state"),
        "maintenance_backup_dir": str(tmp_path / "backups"),
        "http_max_retries": 2,
        "http_timeout_seconds": 5.0,
        "maintenance_auto_replace": False,
        "neon_project_name": "test-project",
    }
    defaults.update(overrides)
    return Settings(**defaults)


def make_source_database(tmp_path: Path) -> tuple[Engine, int]:
    """A source database with two adjacent bookings and one in another room."""
    source = create_engine(
        f"sqlite:///{tmp_path / 'source.db'}", connect_args={"check_same_thread": False}, future=True
    )
    Base.metadata.create_all(source)
    with Session(source) as session:
        service = BookingService(session)
        for room, start_hour in (("Room A", 9), ("Room A", 10), ("Room B", 9)):
            service.create(
                BookingCreate(
                    room=room,
                    booker="Alice Example",
                    purpose="synthetic",
                    start_at=datetime.combine(DAY, time(start_hour)),
                    end_at=datetime.combine(DAY, time(start_hour + 1)),
                )
            )
        count = len(session.scalars(select(Booking)).all())
    return source, count


# --------------------------------------------------------------------------- tests
def test_backup_restore_preserves_content_types_and_identifier_generation(tmp_path: Path) -> None:
    """A backup restored into an empty database is identical and keeps ids safe."""
    source, expected_rows = make_source_database(tmp_path)
    source_snapshot = rule_snapshot(source)
    manifest = create_backup(source, tmp_path / "backups", source_instance_id="neon-src-1")
    assert manifest.row_counts["bookings"] == expected_rows
    assert manifest.payload_sha256 == load_backup(manifest.directory)[0].payload_sha256

    target = create_engine(f"sqlite:///{tmp_path / 'target.db'}", future=True)
    restore_backup(target, manifest.directory)
    report = verify_restore(target, manifest, source_rule_snapshot=source_snapshot)
    assert report.ok, report
    assert report.rule_snapshot["overlap_pairs"] == source_snapshot["overlap_pairs"]

    new_id = check_identifier_generation(target)
    assert new_id > expected_rows


def test_restore_refuses_a_database_that_is_not_empty(tmp_path: Path) -> None:
    """A fresh instance must start blank; a populated target is rejected."""
    source, _ = make_source_database(tmp_path)
    manifest = create_backup(source, tmp_path / "backups", source_instance_id="neon-src-1")

    target = create_engine(f"sqlite:///{tmp_path / 'populated.db'}", future=True)
    Base.metadata.create_all(target)
    with Session(target) as session:
        session.add(
            Booking(
                room="Room Z",
                booker="Placeholder",
                purpose=None,
                start_at=datetime.combine(DAY, time(9)),
                end_at=datetime.combine(DAY, time(10)),
            )
        )
        session.commit()

    with pytest.raises(InvalidBackupError):
        restore_backup(target, manifest.directory)


def test_missing_or_corrupted_backup_is_rejected(tmp_path: Path) -> None:
    """A missing backup and a tampered payload are both refused."""
    with pytest.raises(InvalidBackupError):
        load_backup(tmp_path / "does-not-exist")

    source, _ = make_source_database(tmp_path)
    manifest = create_backup(source, tmp_path / "backups", source_instance_id="neon-src-1")
    payload_path = manifest.directory / "payload.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["tables"]["bookings"]["rows"] = payload["tables"]["bookings"]["rows"][:-1]
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(InvalidBackupError):
        load_backup(manifest.directory)


async def test_status_check_never_replaces_an_empty_database(tmp_path: Path) -> None:
    """An approaching expiry does not justify a replacement when data is empty."""
    source, _ = make_source_database(tmp_path)
    provider = FakeProvider(
        source_url=str(source.url), target_url=f"sqlite:///{tmp_path / 'target.db'}", expires_in_hours=10
    )
    controller = MaintenanceController(
        provider=provider,
        deployer=FakeDeployer(),
        backend=FakeBackend(bookings=0),
        settings=make_settings(tmp_path, maintenance_auto_replace=True, maintenance_expiry_warning_hours=72),
    )
    report = await controller.check_status()
    assert report["backend_ok"] is True
    assert provider.create_calls == 0
    assert controller.current_state() is None


async def test_temporary_provider_failure_stops_without_creating_an_instance(tmp_path: Path) -> None:
    """Bounded retries, then a clear stop - never a guessed second instance."""
    source, _ = make_source_database(tmp_path)
    provider = FakeProvider(
        source_url=str(source.url),
        target_url=f"sqlite:///{tmp_path / 'target.db'}",
        fail_create_times=2,
    )
    backend = FakeBackend()
    controller = MaintenanceController(
        provider=provider,
        deployer=FakeDeployer(),
        backend=backend,
        settings=make_settings(tmp_path),
    )

    with pytest.raises(ReplacementError):
        await controller.replace_instance("provider failure test", resume=False)

    state = controller.current_state()
    assert state is not None and state.status == "failed"
    assert provider.create_calls == 2
    assert provider.created_names == []
    assert backend.maintenance is True, "writes must stay blocked after a failed run"


async def test_interrupted_run_resumes_without_a_second_instance_or_backup(tmp_path: Path) -> None:
    """The workflow is resumable: a rerun adopts the recorded instance and backup."""
    source, expected_rows = make_source_database(tmp_path)
    provider = FakeProvider(
        source_url=str(source.url), target_url=f"sqlite:///{tmp_path / 'target.db'}"
    )
    deployer = FakeDeployer(fail_deploys=2)
    backend = FakeBackend(bookings=expected_rows)
    controller = MaintenanceController(
        provider=provider, deployer=deployer, backend=backend, settings=make_settings(tmp_path)
    )

    with pytest.raises(ReplacementError):
        await controller.replace_instance("interrupted run", resume=False)

    state = controller.current_state()
    assert state is not None and state.status == "failed"
    assert state.new_instance_id, "the created instance must be recorded before the switch"
    assert "restore_verified" in state.steps
    assert provider.create_calls == 1
    assert backend.maintenance is True

    deployer.fail_deploys = 0
    completed = await controller.replace_instance("interrupted run", resume=True)
    assert completed.status == "completed"
    assert provider.create_calls == 1, "resuming must not create a second instance"
    assert backend.maintenance is False
    assert completed.deploy_id


async def test_failed_restore_keeps_maintenance_mode_active(tmp_path: Path) -> None:
    """A safe stop: the write block stays on and the backup is preserved."""
    from backend.db import build_engine

    source, _ = make_source_database(tmp_path)
    provider = FakeProvider(
        source_url=str(source.url), target_url=f"sqlite:///{tmp_path / 'target.db'}"
    )

    def engine_factory(url: str) -> Engine:
        if "target" in url:
            raise RuntimeError("simulated failure while connecting to the new instance")
        return build_engine(url)

    backend = FakeBackend()
    controller = MaintenanceController(
        provider=provider,
        deployer=FakeDeployer(),
        backend=backend,
        settings=make_settings(tmp_path),
        engine_factory=engine_factory,
    )
    with pytest.raises(ReplacementError):
        await controller.replace_instance("restore failure test", resume=False)
    assert backend.maintenance is True
    assert controller.newest_backup() is not None
