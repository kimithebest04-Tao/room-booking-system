"""Reusable backup and restoration operations for the booking database.

The backup is a portable JSON export produced through SQLAlchemy (no ``pg_dump``
binary, no provider specific format), stored in a durable directory that is
independent of the source database and of the backend process.  Every backup
carries its creation time, the identifier of the source instance and a SHA-256
digest of its payload, so a damaged or incomplete file is rejected instead of
being restored silently.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Integer, delete, func, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session

from backend import __version__
from backend.models import Base, Booking
from backend.processing import Interval, find_overlapping

logger = logging.getLogger(__name__)

BACKUP_SCHEMA_VERSION = 1
PAYLOAD_FILENAME = "payload.json"
MANIFEST_FILENAME = "manifest.json"


class BackupError(RuntimeError):
    """Any problem while creating, loading, restoring or verifying a backup."""


class InvalidBackupError(BackupError):
    """The backup is missing, unreadable, incomplete or fails its checksum."""


@dataclass(frozen=True)
class BackupManifest:
    """Metadata stored next to the payload."""

    backup_id: str
    created_at: str
    source_instance_id: str
    app_version: str
    row_counts: dict[str, int]
    payload_sha256: str
    directory: Path

    def as_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "created_at": self.created_at,
            "source_instance_id": self.source_instance_id,
            "app_version": self.app_version,
            "row_counts": self.row_counts,
            "payload_sha256": self.payload_sha256,
            "schema_version": BACKUP_SCHEMA_VERSION,
        }


@dataclass(frozen=True)
class RestoreReport:
    """Outcome of a restoration run."""

    backup_id: str
    row_counts: dict[str, int]
    payload_sha256: str
    restored_at: str


@dataclass(frozen=True)
class VerificationReport:
    """Outcome of the post-restore comparison."""

    backup_id: str
    content_matches: bool
    target_sha256: str
    source_sha256: str
    row_counts_match: bool
    rule_matches: bool
    rule_snapshot: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.content_matches and self.row_counts_match and self.rule_matches


# --------------------------------------------------------------------- encoding
def _encode_value(value: Any) -> Any:
    """Encode one column value so that its type survives a JSON round trip."""
    if isinstance(value, datetime):
        return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__type__": "date", "value": value.isoformat()}
    if isinstance(value, Decimal):
        return {"__type__": "decimal", "value": str(value)}
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise BackupError(f"Unsupported column value of type {type(value)!r}")


def _decode_value(value: Any) -> Any:
    """Inverse of :func:`_encode_value`."""
    if isinstance(value, dict) and "__type__" in value:
        kind = value["__type__"]
        raw = value["value"]
        if kind == "datetime":
            return datetime.fromisoformat(raw)
        if kind == "date":
            return date.fromisoformat(raw)
        if kind == "decimal":
            return Decimal(raw)
        raise InvalidBackupError(f"Unknown encoded type {kind!r}")
    return value


# ---------------------------------------------------------------------- export
def _ordered_tables() -> list[Any]:
    """Tables in a stable order (alphabetical, no cross-table foreign keys)."""
    return [table for _, table in sorted(Base.metadata.tables.items())]


def export_payload(engine: Engine) -> dict[str, Any]:
    """Read the whole application data into a JSON serialisable document."""
    payload: dict[str, Any] = {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "app_version": __version__,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "tables": {},
    }
    with engine.connect() as connection:
        for table in _ordered_tables():
            primary_key = list(table.primary_key.columns)
            stmt = select(table)
            if primary_key:
                stmt = stmt.order_by(*primary_key)
            rows = connection.execute(stmt).mappings().all()
            columns = [column.name for column in table.columns]
            payload["tables"][table.name] = {
                "columns": columns,
                "rows": [[_encode_value(row[column]) for column in columns] for row in rows],
            }
    return payload


def canonical_payload(payload: dict[str, Any]) -> str:
    """Deterministic JSON so two exports of the same data hash identically."""
    body = {
        "schema_version": payload.get("schema_version"),
        "tables": payload.get("tables"),
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_digest(payload: dict[str, Any]) -> str:
    """SHA-256 of the canonical payload."""
    return hashlib.sha256(canonical_payload(payload).encode("utf-8")).hexdigest()


def _counts(payload: dict[str, Any]) -> dict[str, int]:
    return {name: len(table.get("rows", [])) for name, table in payload.get("tables", {}).items()}


# ----------------------------------------------------------------------- create
def create_backup(
    engine: Engine,
    directory: str | Path,
    *,
    source_instance_id: str,
    app_version: str = __version__,
) -> BackupManifest:
    """Write a durable, checksummed backup and return its manifest."""
    payload = export_payload(engine)
    digest = payload_digest(payload)
    created_at = datetime.now(timezone.utc)
    backup_id = f"{created_at:%Y%m%dT%H%M%SZ}-{digest[:8]}"
    target = Path(directory) / backup_id
    target.mkdir(parents=True, exist_ok=True)

    manifest = BackupManifest(
        backup_id=backup_id,
        created_at=created_at.replace(microsecond=0).isoformat(),
        source_instance_id=source_instance_id,
        app_version=app_version,
        row_counts=_counts(payload),
        payload_sha256=digest,
        directory=target,
    )
    (target / PAYLOAD_FILENAME).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (target / MANIFEST_FILENAME).write_text(
        json.dumps(manifest.as_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        "Backup %s written to %s (rows=%s, sha256=%s)",
        backup_id,
        target,
        manifest.row_counts,
        digest[:12],
    )
    return manifest


# ------------------------------------------------------------------------- load
def load_backup(directory: str | Path) -> tuple[BackupManifest, dict[str, Any]]:
    """Load a backup directory and verify its integrity."""
    root = Path(directory)
    manifest_path = root / MANIFEST_FILENAME
    payload_path = root / PAYLOAD_FILENAME
    if not manifest_path.is_file() or not payload_path.is_file():
        raise InvalidBackupError(f"Backup at {root} is incomplete (manifest or payload missing)")
    try:
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InvalidBackupError(f"Backup at {root} is unreadable: {exc}") from exc

    required = {"backup_id", "created_at", "source_instance_id", "row_counts", "payload_sha256"}
    if not required.issubset(manifest_data):
        raise InvalidBackupError(f"Backup manifest at {root} lacks required fields")
    if int(manifest_data.get("schema_version", 0)) > BACKUP_SCHEMA_VERSION:
        raise InvalidBackupError("Backup was produced by an unsupported schema version")
    digest = payload_digest(payload)
    if digest != manifest_data["payload_sha256"]:
        raise InvalidBackupError(
            f"Backup checksum mismatch: manifest {manifest_data['payload_sha256'][:12]} "
            f"vs payload {digest[:12]}"
        )
    manifest = BackupManifest(
        backup_id=str(manifest_data["backup_id"]),
        created_at=str(manifest_data["created_at"]),
        source_instance_id=str(manifest_data["source_instance_id"]),
        app_version=str(manifest_data.get("app_version", "unknown")),
        row_counts={name: int(count) for name, count in manifest_data["row_counts"].items()},
        payload_sha256=str(manifest_data["payload_sha256"]),
        directory=root,
    )
    logger.info("Backup %s loaded and verified (sha256=%s)", manifest.backup_id, digest[:12])
    return manifest, payload


def latest_backup(directory: str | Path) -> BackupManifest | None:
    """Return the newest verified backup in ``directory``, if any."""
    root = Path(directory)
    if not root.is_dir():
        return None
    candidates = sorted(
        (path for path in root.iterdir() if path.is_dir() and (path / MANIFEST_FILENAME).is_file()),
        key=lambda path: path.name,
        reverse=True,
    )
    for candidate in candidates:
        try:
            manifest, _ = load_backup(candidate)
        except InvalidBackupError as exc:
            logger.warning("Skipping unusable backup %s: %s", candidate, exc)
            continue
        return manifest
    return None


# ----------------------------------------------------------------------- restore
def _reset_identifier_generation(connection: Connection, table: Any) -> None:
    """Make sure the next generated id is greater than the restored maximum."""
    primary_key = list(table.primary_key.columns)
    if len(primary_key) != 1 or primary_key[0].name != "id" or not isinstance(primary_key[0].type, Integer):
        return
    max_id = connection.execute(select(func.max(primary_key[0])).select_from(table)).scalar()
    if max_id is None:
        return
    dialect = connection.dialect.name
    if dialect == "postgresql":
        connection.execute(
            text("SELECT setval(pg_get_serial_sequence(:table, :column), :value)"),
            {"table": table.name, "column": primary_key[0].name, "value": int(max_id)},
        )
    elif dialect == "sqlite":
        try:
            connection.execute(
                text("UPDATE sqlite_sequence SET seq = :value WHERE name = :table"),
                {"value": int(max_id), "table": table.name},
            )
            present = connection.execute(
                text("SELECT COUNT(*) FROM sqlite_sequence WHERE name = :table"), {"table": table.name}
            ).scalar()
            if not present:
                connection.execute(
                    text("INSERT INTO sqlite_sequence(name, seq) VALUES (:table, :value)"),
                    {"table": table.name, "value": int(max_id)},
                )
        except Exception:  # pragma: no cover - SQLite without AUTOINCREMENT needs no fix
            logger.debug("SQLite has no sequence row for %s; rowid allocation is already safe", table.name)


def restore_backup(engine: Engine, directory: str | Path, *, allow_non_empty: bool = False) -> RestoreReport:
    """Restore schema, records and identifier generation into a target database.

    The target must be empty of application data unless ``allow_non_empty`` is
    set, mirroring the requirement that a new instance starts blank.
    """
    manifest, payload = load_backup(directory)
    tables = _ordered_tables()
    Base.metadata.create_all(engine)

    with engine.begin() as connection:
        if not allow_non_empty:
            for table in tables:
                existing = connection.execute(select(func.count()).select_from(table)).scalar() or 0
                if existing:
                    raise InvalidBackupError(
                        f"Target table {table.name} is not empty ({existing} rows); refusing to restore"
                    )
        for table in tables:
            connection.execute(delete(table))
        for table in tables:
            table_payload = payload["tables"].get(table.name, {"columns": [], "rows": []})
            columns = table_payload.get("columns", [])
            rows = [
                {column: _decode_value(value) for column, value in zip(columns, row)}
                for row in table_payload.get("rows", [])
            ]
            if rows:
                connection.execute(table.insert(), rows)
        for table in tables:
            _reset_identifier_generation(connection, table)

    counts = _counts(payload)
    report = RestoreReport(
        backup_id=manifest.backup_id,
        row_counts=counts,
        payload_sha256=manifest.payload_sha256,
        restored_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    )
    logger.info(
        "Restored backup %s into %s (rows=%s)",
        manifest.backup_id,
        engine.url.render_as_string(hide_password=True),
        counts,
    )
    return report


# ---------------------------------------------------------------------- verify
def rule_snapshot(engine: Engine) -> dict[str, Any]:
    """Fingerprint of the domain rule applied to the stored data.

    The same data must produce the same conflict verdicts before and after a
    restoration, so the controller compares these snapshots instead of relying
    on row counts alone.
    """
    with Session(engine) as session:
        rows = session.scalars(select(Booking).order_by(Booking.id)).all()
    intervals = [Interval(row.start_at, row.end_at, row.id) for row in rows]
    pairs: list[list[int]] = []
    for index, first in enumerate(intervals):
        for second in intervals[index + 1 :]:
            if find_overlapping(first, (second,)):
                pairs.append([int(first.identifier or 0), int(second.identifier or 0)])
    return {
        "booking_count": len(intervals),
        "overlap_pairs": sorted(pairs),
        "rooms": sorted({row.room for row in rows}),
        "first_booking_id": int(intervals[0].identifier or 0) if intervals else 0,
    }


def verify_restore(
    engine: Engine,
    manifest: BackupManifest,
    *,
    source_rule_snapshot: dict[str, Any] | None = None,
) -> VerificationReport:
    """Compare the restored database with the backup manifest and rule snapshot."""
    payload = export_payload(engine)
    digest = payload_digest(payload)
    counts = _counts(payload)
    snapshot = rule_snapshot(engine)
    counts_match = counts == manifest.row_counts
    rule_matches = True if source_rule_snapshot is None else snapshot == source_rule_snapshot
    report = VerificationReport(
        backup_id=manifest.backup_id,
        content_matches=digest == manifest.payload_sha256,
        target_sha256=digest,
        source_sha256=manifest.payload_sha256,
        row_counts_match=counts_match,
        rule_matches=rule_matches,
        rule_snapshot=snapshot,
    )
    logger.info(
        "Verification of backup %s: content=%s counts=%s rule=%s",
        manifest.backup_id,
        report.content_matches,
        counts_match,
        rule_matches,
    )
    return report
