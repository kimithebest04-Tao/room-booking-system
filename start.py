"""Start script for the complete system (backend, frontend and maintenance checks).

    python start.py                 # backend + frontend + scheduled status checks
    python start.py --no-frontend   # backend + checks only

The script starts the components and waits; Ctrl+C stops all of them.  It never
deletes or replaces a database: replacement requires an explicit, confirmed
``python -m maintenance.run replace --confirm`` run.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _wait_for_backend(health_url: str, timeout: float = 60.0) -> bool:
    """Poll the backend health endpoint until it answers or the timeout expires."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=2) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(1.5)
    return False


def _launch(name: str, command: list[str]) -> subprocess.Popen:
    print(f"[start] {name}: {' '.join(command)}")
    return subprocess.Popen(command, cwd=str(ROOT))


def _stop(processes: list[tuple[str, subprocess.Popen]]) -> None:
    for name, process in reversed(processes):
        if process.poll() is not None:
            continue
        print(f"[start] stopping {name}")
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Start the room booking system")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000, help="FastAPI port")
    parser.add_argument("--frontend-port", type=int, default=8501, help="Streamlit port")
    parser.add_argument("--no-frontend", action="store_true", help="Do not start Streamlit")
    parser.add_argument("--no-maintenance", action="store_true", help="Do not start the status checks")
    parser.add_argument("--reload", action="store_true", help="Use uvicorn --reload (development only)")
    args = parser.parse_args(argv)

    # The frontend must call this backend; the value is only a default for the
    # child process, the real setting still comes from the environment.
    os.environ.setdefault("API_BASE_URL", f"http://{args.host}:{args.port}")

    from backend.config import get_settings  # imported after the environment default

    settings = get_settings()
    health_url = f"http://{args.host}:{args.port}/health"
    processes: list[tuple[str, subprocess.Popen]] = []

    backend_command = [
        sys.executable,
        "-m",
        "uvicorn",
        "backend.main:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    if args.reload:
        backend_command.append("--reload")
    processes.append(("backend", _launch("backend", backend_command)))

    if not _wait_for_backend(health_url):
        print("[start] the backend did not become healthy; stopping", file=sys.stderr)
        _stop(processes)
        return 1

    if not args.no_frontend:
        processes.append(
            (
                "frontend",
                _launch(
                    "frontend",
                    [
                        sys.executable,
                        "-m",
                        "streamlit",
                        "run",
                        "frontend/app.py",
                        "--server.port",
                        str(args.frontend_port),
                        "--server.headless",
                        "true",
                    ],
                ),
            )
        )

    if not args.no_maintenance:
        if settings.neon_api_key:
            processes.append(
                (
                    "maintenance",
                    _launch("maintenance", [sys.executable, "-m", "maintenance.run", "watch"]),
                )
            )
        else:
            print("[start] NEON_API_KEY is not set: scheduled status checks are skipped")

    print("[start] system is up. Press Ctrl+C to stop everything.")
    try:
        while True:
            for name, process in list(processes):
                if process.poll() is not None:
                    print(f"[start] {name} exited with code {process.returncode}")
                    processes = [(item_name, item) for item_name, item in processes if item is not process]
            if not processes:
                return 1
            time.sleep(1.5)
    except KeyboardInterrupt:
        print("\n[start] shutting down")
        _stop(processes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
