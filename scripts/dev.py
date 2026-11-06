"""Start everything for development: Docker services, backend (port 8000), frontend (port 5173) and the job worker.

    python scripts/dev.py

Stop with Ctrl-C.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
PY = BACKEND / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / "python"


def main() -> int:
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
        "\nBackend  http://localhost:8000/api/health\nFrontend http://localhost:5173\nMailpit  http://localhost:8025\n"
    )
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
