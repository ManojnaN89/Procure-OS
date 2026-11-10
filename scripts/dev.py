"""Start everything for development: Docker services, backend (port 8000), frontend (port 5173) and the job worker.

    python scripts/dev.py

Stop with Ctrl-C.
"""

import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
PY = BACKEND / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / "python"
PORTS = {8000: "the backend", 5173: "the frontend"}


def _in_use(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def _stop(p: "subprocess.Popen[bytes]") -> None:
    """End a process and what it started (the reloader and npm each leave a child that keeps the port)."""
    if p.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, check=False)  # noqa: S603, S607
    else:
        p.terminate()


def main() -> int:
    busy = [f"port {port} ({what})" for port, what in PORTS.items() if _in_use(port)]
    if busy:
        print(
            "Something is already running on " + " and ".join(busy) + ".\n"
            "It is probably an earlier `python scripts/dev.py` that was not stopped, and it may be serving old code.\n"
            "Stop it first (close its terminal, or end the python and node processes that listen on those ports), then run this again."
        )
        return 1
    subprocess.run(["docker", "compose", "up", "-d", "--wait"], cwd=ROOT, check=True)  # noqa: S607
    npm = shutil.which("npm") or "npm"
    procs = [
        subprocess.Popen(  # noqa: S603
            [str(PY), "-m", "uvicorn", "p2p.main:app", "--reload", "--port", "8000"], cwd=BACKEND
        ),
        subprocess.Popen([npm, "run", "dev"], cwd=FRONTEND),  # noqa: S603
        subprocess.Popen([str(PY), "-m", "p2p.cli", "worker"], cwd=BACKEND),  # noqa: S603
    ]
    print(
        "\nBackend  http://127.0.0.1:8000/api/health\nFrontend http://127.0.0.1:5173\nMailpit  http://127.0.0.1:8025\n"
    )
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            _stop(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
