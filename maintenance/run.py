"""Command line entry point of the maintenance controller.

Examples (run from the repository root, inside the virtual environment)::

    python -m maintenance.run check            # one status check, prints a JSON report
    python -m maintenance.run watch            # scheduled checks until Ctrl+C
    python -m maintenance.run replace --reason "planned maintenance" --confirm
    python -m maintenance.run resume --confirm # continue an interrupted run

Replacement (which may delete an instance) always requires ``--confirm``; a
plain ``watch`` or ``check`` can never delete or replace anything.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from backend.config import Settings, get_settings

from .backend_client import BackendClient
from .controller import MaintenanceController, ReplacementError
from .neon_provider import NeonProvider
from .provider import ProviderError
from .render_client import RenderDeployer

logger = logging.getLogger("maintenance")


def configure_logging(settings: Settings) -> None:
    """Log maintenance activity to the console and to its own log file."""
    log_path = Path(settings.maintenance_log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    formatter = logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    if not any(isinstance(handler, logging.FileHandler) for handler in root.handlers):
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(formatter)
        root.addHandler(handler)
    if not any(isinstance(handler, logging.StreamHandler) for handler in root.handlers):
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def build_controller(settings: Settings) -> MaintenanceController:
    """Wire the real provider, deployment client and backend client together."""
    provider = NeonProvider(
        settings.neon_api_key,
        project_id=settings.neon_project_id,
        region_id=settings.neon_region_id,
        pg_version=settings.neon_pg_version,
        timeout=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
    )
    deployer = None
    if settings.render_api_key and settings.render_service_id:
        deployer = RenderDeployer(
            settings.render_api_key,
            settings.render_service_id,
            timeout=settings.http_timeout_seconds,
            max_retries=settings.http_max_retries,
        )
    backend = BackendClient(
        settings.backend_base_url,
        settings.admin_token,
        timeout=settings.http_timeout_seconds,
        max_retries=settings.http_max_retries,
    )
    return MaintenanceController(provider=provider, deployer=deployer, backend=backend, settings=settings)


async def _run_check(settings: Settings, trigger: bool) -> None:
    controller = build_controller(settings)
    report = await controller.check_status(trigger_if_needed=trigger)
    print(json.dumps(report, indent=2, ensure_ascii=False))


async def _run_replace(settings: Settings, reason: str, delete_source: bool, resume: bool) -> None:
    controller = build_controller(settings)
    state = await controller.replace_instance(reason, resume=resume, delete_source=delete_source)
    print(json.dumps(state.to_dict(), indent=2, ensure_ascii=False))


async def _run_watch(settings: Settings) -> None:
    controller = build_controller(settings)
    controller.start_scheduler()
    print(
        f"Status checks every {settings.maintenance_interval_minutes} minutes. "
        "Replacement only runs when AUTHORISED (MAINTENANCE_AUTO_REPLACE=true)."
    )
    try:
        await asyncio.Event().wait()  # run until cancelled (Ctrl+C)
    except (KeyboardInterrupt, asyncio.CancelledError):
        controller.shutdown_scheduler()
        logger.warning("Maintenance watch stopped")


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and dispatch one controller action."""
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Room booking maintenance controller")
    parser.add_argument(
        "command",
        choices=["check", "watch", "replace", "resume", "state"],
        help="check: one status report; watch: scheduled checks; replace/resume: authorised workflow",
    )
    parser.add_argument("--reason", default="planned maintenance", help="Reason recorded in the run state")
    parser.add_argument("--confirm", action="store_true", help="Authorise the replacement workflow")
    parser.add_argument("--delete-source", action="store_true", help="Delete the old instance after verification")
    parser.add_argument("--trigger", action="store_true", help="With check: also replace when conditions are met")
    args = parser.parse_args(argv)

    configure_logging(settings)

    if args.command in ("replace", "resume") and not args.confirm:
        print("Refusing to replace an instance without --confirm (authorisation is required).", file=sys.stderr)
        return 2

    try:
        if args.command == "check":
            asyncio.run(_run_check(settings, args.trigger))
        elif args.command == "watch":
            asyncio.run(_run_watch(settings))
        elif args.command == "replace":
            asyncio.run(_run_replace(settings, args.reason, args.delete_source, resume=False))
        elif args.command == "resume":
            asyncio.run(_run_replace(settings, args.reason, args.delete_source, resume=True))
        elif args.command == "state":
            state_path = Path(settings.maintenance_state_dir) / "run_state.json"
            print(state_path.read_text(encoding="utf-8") if state_path.is_file() else "{}")
    except (ReplacementError, ProviderError) as exc:
        logger.error("Controller stopped: %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
