"""Idempotent API supervisor for the SIH26104 service.

Launched by Windows Task Scheduler task ``SIH26104_api``:

    .venv\\Scripts\\pythonw.exe D:\\SIH26104\\tools\\serve.py

Behaviour:
  * Only one supervisor may run (Windows named mutex); repeated task runs are no-ops.
  * Liveness is a fast TCP connect probe on 127.0.0.1:8000, not GET /health:
    /health used to cold-load every model on first call, so the probe timed out
    and the supervisor started duplicate API processes.
  * If the port is listening, it monitors instead of spawning.
  * If the port is closed and no child is alive, it starts ``service/api.py``
    under the project venv and restarts it after a crash.
  * A child that has not opened the port within STARTUP_GRACE seconds is
    treated as hung and restarted (the whole process tree is terminated).
  * All output goes to ``logs/api.log`` (server) and ``logs/serve.log`` (supervisor).

Kept as Python rather than .bat/.ps1 per CLAUDE.md ("any shell script must be
written as cross-platform Python using subprocess and pathlib").
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
API_SCRIPT = ROOT / "service" / "api.py"
LOG_DIR = ROOT / "logs"
HOST = "127.0.0.1"
PORT = 8000
CHECK_INTERVAL = 5.0
CONNECT_TIMEOUT = 1.0
STARTUP_GRACE = 120.0
WARN_INTERVAL = 60.0

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "serve.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("serve")


def port_open(timeout: float = CONNECT_TIMEOUT) -> bool:
    """True when something accepts TCP connections on the API port."""
    try:
        with socket.create_connection((HOST, PORT), timeout=timeout):
            return True
    except OSError:
        return False


def start_api() -> subprocess.Popen:
    python = VENV_PYTHONW if VENV_PYTHONW.is_file() else Path(sys.executable)
    log = open(LOG_DIR / "api.log", "ab", buffering=0)
    logger.info("starting %s with %s", API_SCRIPT, python)
    return subprocess.Popen(
        [str(python), str(API_SCRIPT)],
        cwd=str(ROOT),
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def stop_api(child: subprocess.Popen) -> None:
    """Kill the child and its re-exec'd worker (venv pythonw is a launcher stub)."""
    if child.poll() is not None:
        return
    try:
        subprocess.run(
            ["taskkill", "/PID", str(child.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        child.terminate()
    try:
        child.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - taskkill should win
        child.kill()


def acquire_single_instance():
    """Return a handle that must stay alive, or None if another supervisor runs."""
    if os.name != "nt":
        return object()
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, "Global\\SIH26104_api_supervisor")
    if not handle:
        return None
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return handle


def main() -> int:
    mutex = acquire_single_instance()
    if mutex is None:
        logger.info("another supervisor is already running; exiting")
        return 0
    if not API_SCRIPT.is_file():
        logger.error("missing %s", API_SCRIPT)
        return 1

    child: subprocess.Popen | None = None
    child_started = 0.0
    last_warn = 0.0
    port_was_open = port_open()
    if port_was_open:
        logger.info("API already listening on %s:%d; monitoring it", HOST, PORT)

    while True:
        if child is not None and child.poll() is not None:
            logger.warning("API exited with code %s", child.returncode)
            child = None
            time.sleep(1.0)

        if child is None:
            open_now = port_open()
            if open_now:
                if not port_was_open:
                    logger.info("API is listening on %s:%d (managed elsewhere)", HOST, PORT)
            else:
                child = start_api()
                child_started = time.monotonic()
                last_warn = 0.0
            port_was_open = open_now
        elif port_open():
            last_warn = 0.0
        else:
            elapsed = time.monotonic() - child_started
            if elapsed > STARTUP_GRACE:
                logger.error(
                    "API child pid %s has not opened %s:%d after %.0fs; restarting",
                    child.pid, HOST, PORT, elapsed,
                )
                stop_api(child)
                child = None
            elif time.monotonic() - last_warn > WARN_INTERVAL:
                logger.info(
                    "API child pid %s is starting (port closed for %.0fs)",
                    child.pid, elapsed,
                )
                last_warn = time.monotonic()

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        logger.info("supervisor stopped")
