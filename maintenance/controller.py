"""Modular maintenance controller.

One process, started outside the backend, that:

1. checks the database instance status on a schedule using an ``async`` API
   call (APScheduler + ``httpx.AsyncClient``),
2. runs the authorised replacement workflow: block writes, take a final backup,
   create a new instance through the provider API, restore and verify the data,
   switch the backend connection setting and redeploy, verify the restored
   content through the API, then release maintenance mode.

The workflow is written as a sequence of plain functions on top of a small state
object (procedural programming), while the class itself owns the orchestration
and the durable run state.  Every step is idempotent: a run that was interrupted
can be resumed without creating a second instance or a second backup.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from backend import __version__
from backend.config import Settings, get_settings
from backend.db import build_engine
from backend.models import Booking

from .backup import (
    BackupManifest,
    InvalidBackupError,
    create_backup,
    latest_backup,
    load_backup,
    restore_backup,
    rule_snapshot,
    verify_restore,
)
from .backend_client import BackendClient, BackendError
from .provider import (
    DatabaseProvider,
    InstanceInfo,
    ProviderError,
    ProviderQuotaError,
    ProviderUnavailableError,
    with_retries,
)

logger = logging.getLogger(__name__)

STATE_FILENAME = "run_state.json"
MAINTENANCE_HEADER_NOTE = "Writes are blocked in the database, not hidden in the UI."


class ReplacementError(RuntimeError):
    """A replacement run stopped; the message explains what must be done next."""


@dataclass
class ReplacementState:
    """Durable record of one replacement run (persisted between restarts)."""

    run_id: str
    reason: str
    started_at: str
    status: str = "in_progress"
    steps: list[str] = field(default_factory=list)
    source_instance_id: str = ""
    new_instance_id: str = ""
    backup_id: str = ""
    backup_directory: str = ""
    deploy_id: str = ""
    source_rule_snapshot: dict[str, Any] = field(default_factory=dict)
    message: str = ""

    def mark(self, step: str) -> None:
        """Record a completed step (idempotent)."""
        if step not in self.steps:
            self.steps.append(step)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReplacementState":
        known = {key: value for key, value in data.items() if key in cls.__dataclass_fields__}
        return cls(**known)


def check_identifier_generation(engine: Engine) -> int:
    """Insert and delete a probe booking to prove ids cannot collide after a restore."""
    max_id = 0
    with Session(engine) as session:
        max_id = int(session.scalar(select(func.max(Booking.id)).select_from(Booking)) or 0)
        probe = Booking(
            room="maintenance-probe",
            booker="maintenance-controller",
            purpose="identifier generation check",
            start_at=datetime(2000, 1, 1, 0, 0),
            end_at=datetime(2000, 1, 1, 1, 0),
        )
        session.add(probe)
        session.commit()
        generated = int(probe.id)
        session.delete(probe)
        session.commit()
    if generated <= max_id:
        raise ReplacementError(f"Identifier generation is unsafe: new id {generated} <= restored max {max_id}")
    logger.info("Identifier generation verified: new id %s > restored max %s", generated, max_id)
    return generated


class MaintenanceController:
    """Scheduled status checks and the authorised database replacement workflow."""

    def __init__(
        self,
        *,
        provider: DatabaseProvider,
        deployer: Any | None = None,
        backend: BackendClient | None = None,
        settings: Settings | None = None,
        engine_factory: Callable[[str], Engine] = build_engine,
        state_dir: str | Path | None = None,
        backup_dir: str | Path | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._provider = provider
        self._deployer = deployer
        self._backend = backend
        self._engine_factory = engine_factory
        self._state_dir = Path(state_dir or self._settings.maintenance_state_dir)
        self._backup_dir = Path(backup_dir or self._settings.maintenance_backup_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._scheduler: Any | None = None

    # ------------------------------------------------------------------ plumbing
    @property
    def state_path(self) -> Path:
        return self._state_dir / STATE_FILENAME

    def _load_state(self) -> ReplacementState | None:
        path = self.state_path
        if not path.is_file():
            return None
        try:
            return ReplacementState.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            logger.error("Could not read the run state at %s: %s", path, exc)
            return None

    def _save_state(self, state: ReplacementState) -> None:
        """Write the state atomically so a crash cannot corrupt it."""
        path = self.state_path
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(state.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def _require_backend(self) -> BackendClient:
        if self._backend is None:
            raise ReplacementError("A backend client is required for this operation")
        return self._backend

    def _require_deployer(self) -> Any:
        if self._deployer is None:
            raise ReplacementError("A deployment client is required for the switch step")
        return self._deployer

    # ------------------------------------------------------------- status checks
    async def check_status(self, *, trigger_if_needed: bool | None = None) -> dict[str, Any]:
        """Read provider status, expiry information and backend health."""
        instance = await with_retries(
            lambda: self._provider.get_instance(),
            description="status check: provider instance",
            attempts=self._settings.http_max_retries,
        )
        expires_in_hours: float | None = None
        if instance.expires_at is not None:
            expires_in_hours = round(
                (instance.expires_at - datetime.now(timezone.utc)).total_seconds() / 3600, 1
            )

        maintenance = False
        backend_ok = True
        bookings = 0
        try:
            backend = self._require_backend()
            health = await backend.health()
            backend_ok = health.get("status") == "ok"
            maintenance = bool(health.get("maintenance_mode"))
            bookings = await backend.count_bookings()
        except (BackendError, ReplacementError) as exc:
            backend_ok = False
            logger.warning("Backend health could not be read: %s", exc)

        report = {
            "instance_id": instance.identifier,
            "instance_name": instance.name,
            "ready": instance.ready,
            "expires_at": instance.expires_at.isoformat() if instance.expires_at else None,
            "expires_in_hours": expires_in_hours,
            "backend_ok": backend_ok,
            "maintenance_mode": maintenance,
            "bookings": bookings,
            "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
        logger.info("Status check: %s", report)

        if trigger_if_needed is None:
            trigger_if_needed = self._settings.maintenance_auto_replace
        if trigger_if_needed and self._replacement_justified(report):
            logger.warning("Replacement conditions met (%s); starting an authorised run", report["expires_at"])
            await self.replace_instance(reason="scheduled expiry warning", resume=True)
        return report

    def _replacement_justified(self, report: dict[str, Any]) -> bool:
        """A connection failure or an empty table never justifies a replacement."""
        if not report.get("backend_ok"):
            logger.warning("Replacement suppressed: the backend is not reachable")
            return False
        if int(report.get("bookings") or 0) == 0:
            logger.warning("Replacement suppressed: the database holds no application data")
            return False
        hours = report.get("expires_in_hours")
        return hours is not None and hours <= self._settings.maintenance_expiry_warning_hours

    # --------------------------------------------------------- replacement flow
    async def replace_instance(
        self,
        reason: str,
        *,
        resume: bool = True,
        delete_source: bool = False,
    ) -> ReplacementState:
        """Run (or resume) the authorised replacement workflow."""
        state = self._load_state()
        if state is not None and state.status == "in_progress":
            if not resume:
                raise ReplacementError(f"Run {state.run_id} is still in progress; use resume instead")
            logger.warning("Resuming interrupted run %s (steps: %s)", state.run_id, state.steps)
        elif state is not None and state.status == "completed":
            logger.info("Previous run %s already completed; starting a new authorised run", state.run_id)
            state = None

        if state is None:
            state = ReplacementState(
                run_id=uuid.uuid4().hex[:12],
                reason=reason,
                started_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            )
            self._save_state(state)
            logger.warning("Starting replacement run %s (%s)", state.run_id, reason)

        try:
            await self._step_block_writes(state)
            manifest = await self._step_backup(state)
            target = await self._step_create_instance(state)
            await self._step_restore(state, target, manifest)
            await self._step_switch(state, target)
            await self._step_verify(state, manifest, target)
            await self._step_release(state)
            if delete_source:
                await self._step_delete_source(state, target)
            state.status = "completed"
            state.message = "replacement completed"
            self._save_state(state)
            logger.warning("Replacement run %s completed", state.run_id)
            return state
        except Exception as exc:  # noqa: BLE001 - the controller must not die mid-workflow
            state.status = "failed"
            state.message = str(exc) or exc.__class__.__name__
            self._save_state(state)
            logger.exception("Replacement run %s failed: %s", state.run_id, state.message)
            await self._ensure_writes_blocked()
            raise ReplacementError(state.message) from exc

    # ------------------------------------------------------------------ steps
    async def _step_block_writes(self, state: ReplacementState) -> None:
        """Stop new application writes and wait for in-flight work to drain."""
        backend = self._require_backend()
        blocked = await backend.set_maintenance(True)
        confirmed = await backend.get_maintenance()
        if not blocked or not confirmed:
            raise ReplacementError("The backend did not confirm the write block; aborting before the backup")
        if "writes_blocked" not in state.steps:
            await asyncio.sleep(2.0)  # allow requests already in progress to finish
            state.mark("writes_blocked")
            self._save_state(state)
        logger.warning("Writes are blocked (%s)", MAINTENANCE_HEADER_NOTE)

    async def _step_backup(self, state: ReplacementState) -> BackupManifest:
        """Take the final backup of the source database (reused on resume)."""
        if state.backup_directory:
            candidate = Path(state.backup_directory)
            if (candidate / "manifest.json").is_file():
                try:
                    manifest, _ = load_backup(candidate)
                    logger.warning("Reusing the verified backup %s from the previous attempt", manifest.backup_id)
                    state.mark("backup_created")
                    return manifest
                except InvalidBackupError as exc:
                    logger.error("Existing backup is unusable (%s); taking a new one", exc)

        instance = await with_retries(
            lambda: self._provider.get_instance(state.source_instance_id or None),
            description="backup: read source instance",
            attempts=self._settings.http_max_retries,
        )
        if not instance.connection_string:
            raise ReplacementError("The source instance has no connection string; cannot back it up")
        if not state.source_instance_id:
            state.source_instance_id = instance.identifier
        state.mark("source_instance_known")

        source_engine = self._engine_factory(instance.connection_string)
        if not state.source_rule_snapshot:
            state.source_rule_snapshot = rule_snapshot(source_engine)
        manifest = create_backup(
            source_engine,
            self._backup_dir,
            source_instance_id=instance.identifier,
            app_version=__version__,
        )
        load_backup(manifest.directory)  # immediate integrity check
        state.backup_id = manifest.backup_id
        state.backup_directory = str(manifest.directory)
        state.mark("backup_created")
        self._save_state(state)
        return manifest

    async def _step_create_instance(self, state: ReplacementState) -> InstanceInfo:
        """Create the replacement instance, reusing anything a previous attempt made."""
        target_name = f"{self._settings.neon_project_name}-{state.run_id[:8]}"

        if state.new_instance_id:
            try:
                existing = await self._provider.get_instance(state.new_instance_id)
                logger.warning("Reusing the instance %s recorded in the run state", state.new_instance_id)
                state.mark("instance_created")
                return existing
            except ProviderError as exc:
                logger.warning("Recorded instance %s is gone (%s); looking it up by name", state.new_instance_id, exc)

        instances = await self._provider.list_instances()
        matches = [item for item in instances if item.name == target_name]
        if matches:
            logger.warning("Found instance %s from an interrupted attempt; reusing it", matches[0].identifier)
            state.new_instance_id = matches[0].identifier
            state.mark("instance_created")
            self._save_state(state)
            return matches[0]

        try:
            created = await with_retries(
                lambda: self._provider.create_instance(target_name),
                description=f"create instance {target_name}",
                attempts=self._settings.http_max_retries,
                retry_on=(ProviderUnavailableError,),
            )
        except ProviderQuotaError:
            raise ReplacementError("The provider refused the new instance because of a plan or quota limit")
        except ProviderUnavailableError as exc:
            instances = await self._provider.list_instances()
            matches = [item for item in instances if item.name == target_name]
            if not matches:
                raise ReplacementError(
                    "Instance creation ended in an unknown state and no matching instance exists; "
                    "stopping without guessing"
                ) from exc
            logger.warning("Creation timed out but instance %s exists; adopting it", matches[0].identifier)
            created = matches[0]

        state.new_instance_id = created.identifier
        state.mark("instance_created")
        self._save_state(state)
        return created

    async def _step_restore(
        self, state: ReplacementState, target: InstanceInfo, manifest: BackupManifest
    ) -> None:
        """Restore the backup into the new instance and verify it."""
        if "restored" in state.steps:
            logger.warning("Restore already completed for run %s; skipping", state.run_id)
            return
        connection_string = target.connection_string or (await self._provider.get_instance(target.identifier)).connection_string
        if not connection_string:
            raise ReplacementError("The new instance has no connection string yet")

        target_engine = self._engine_factory(connection_string)
        restore_backup(target_engine, manifest.directory)
        report = verify_restore(
            target_engine, manifest, source_rule_snapshot=state.source_rule_snapshot or None
        )
        if not report.ok:
            raise ReplacementError(
                "Restored data does not match the backup "
                f"(content={report.content_matches}, counts={report.row_counts_match}, rule={report.rule_matches})"
            )
        check_identifier_generation(target_engine)
        state.mark("restored")
        state.mark("restore_verified")
        self._save_state(state)
        logger.warning("Restore verified: %s", report.rule_snapshot)

    async def _step_switch(self, state: ReplacementState, target: InstanceInfo) -> None:
        """Update ``DATABASE_URL`` through the deployment API and wait for the deploy."""
        if "backend_switched" in state.steps:
            logger.warning("Backend already switched for run %s; skipping", state.run_id)
            return
        if not target.connection_string:
            raise ReplacementError("The new instance has no connection string to switch to")
        deployer = self._require_deployer()
        await with_retries(
            lambda: deployer.update_env_var("DATABASE_URL", target.connection_string or ""),
            description="switch: update DATABASE_URL",
            attempts=self._settings.http_max_retries,
            retry_on=(ProviderUnavailableError,),
        )
        deploy_id = await with_retries(
            lambda: deployer.trigger_deploy(),
            description="switch: trigger deployment",
            attempts=self._settings.http_max_retries,
            retry_on=(ProviderUnavailableError,),
        )
        status = await deployer.wait_for_deploy(deploy_id)
        logger.warning("Deployment %s finished with status %s", deploy_id, status)
        state.deploy_id = deploy_id
        state.mark("backend_switched")
        self._save_state(state)

    async def _step_verify(
        self, state: ReplacementState, manifest: BackupManifest, target: InstanceInfo
    ) -> None:
        """Read the restored content through the API and perform the controlled write."""
        backend = self._require_backend()
        await backend.wait_until_healthy(timeout_seconds=300, poll_seconds=10)
        expected = manifest.row_counts.get("bookings", 0)
        actual = await backend.count_bookings(limit=200)
        if actual != expected:
            raise ReplacementError(f"API reports {actual} bookings but the backup holds {expected}")
        if not await backend.get_maintenance():
            raise ReplacementError("Maintenance mode was lost during the switch; writes may have leaked")
        if not await backend.probe_write():
            raise ReplacementError("The controlled write to the new database failed")
        state.mark("content_verified")
        state.mark("probe_write_verified")
        self._save_state(state)
        logger.warning("Post-switch verification passed for instance %s", target.identifier)

    async def _step_release(self, state: ReplacementState) -> None:
        """Leave maintenance mode only after a successful verification."""
        backend = self._require_backend()
        released = await backend.set_maintenance(False)
        if released or not await backend.get_maintenance():
            state.mark("maintenance_released")
            self._save_state(state)
            logger.warning("Maintenance mode released; application writes are open again")

    async def _step_delete_source(self, state: ReplacementState, target: InstanceInfo) -> None:
        """Delete the old instance; only reached with explicit authorisation."""
        if not state.source_instance_id or state.source_instance_id == target.identifier:
            return
        await self._provider.delete_instance(state.source_instance_id)
        state.mark("source_deleted")
        self._save_state(state)

    async def _ensure_writes_blocked(self) -> None:
        """Keep writes blocked when a run fails; used by the failure path."""
        if self._backend is None:
            return
        try:
            await self._backend.set_maintenance(True)
            logger.warning("Maintenance mode kept active after the failure")
        except Exception:  # noqa: BLE001 - best effort only
            logger.exception("Could not re-assert maintenance mode after the failure")

    # --------------------------------------------------------------- scheduling
    def start_scheduler(self) -> Any:
        """Start the async interval scheduler (one instance per process)."""
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        if self._scheduler is not None and getattr(self._scheduler, "running", False):
            logger.warning("A scheduler is already running; not starting a second one")
            return self._scheduler
        scheduler = AsyncIOScheduler(timezone=self._settings.timezone)
        scheduler.add_job(
            self._safe_status_check,
            trigger="interval",
            minutes=self._settings.maintenance_interval_minutes,
            id="database-status-check",
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        self._scheduler = scheduler
        logger.warning(
            "Status checks scheduled every %s minutes", self._settings.maintenance_interval_minutes
        )
        return scheduler

    async def _safe_status_check(self) -> None:
        """Scheduler entry point: never let a failed check kill the scheduler."""
        try:
            await self.check_status()
        except Exception:  # noqa: BLE001 - logged for the operator
            logger.exception("Scheduled status check failed")

    def shutdown_scheduler(self) -> None:
        """Stop the scheduler if this process started one."""
        if self._scheduler is not None and getattr(self._scheduler, "running", False):
            self._scheduler.shutdown(wait=False)
            logger.warning("Status-check scheduler stopped")
        self._scheduler = None

    # ----------------------------------------------------------------- helpers
    def current_state(self) -> ReplacementState | None:
        """Return the persisted run state (for the CLI and the README evidence)."""
        return self._load_state()

    def newest_backup(self) -> BackupManifest | None:
        """Return the newest usable backup on disk."""
        return latest_backup(self._backup_dir)
