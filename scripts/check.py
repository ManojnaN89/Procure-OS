"""Run every quality check: lint, types and tests for backend and frontend.

python scripts/check.py
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
PY = str(BACKEND / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / "python")
NPX = shutil.which("npx") or "npx"

STEPS = [
    ("ruff lint", [PY, "-m", "ruff", "check", ".", "../tests", "../scripts"], BACKEND),
    ("ruff format", [PY, "-m", "ruff", "format", "--check", ".", "../tests", "../scripts"], BACKEND),
    ("mypy", [PY, "-m", "mypy"], BACKEND),
    # The browser tests run in their own process: the OCR models of the main run use gigabytes and make them flaky.
    ("pytest", [PY, "-m", "pytest", "-q", "--ignore=../tests/test_web_shell.py", "--ignore=../tests/test_main_ui.py"], BACKEND),
    ("pytest browser", [PY, "-m", "pytest", "-q", "../tests/test_web_shell.py", "../tests/test_main_ui.py"], BACKEND),
    ("biome", [NPX, "biome", "check", "."], FRONTEND),
    ("tsc", [NPX, "tsc", "-b"], FRONTEND),
]


def main() -> int:
    failed = []
    for name, cmd, cwd in STEPS:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)  # noqa: S603, PLW1510
        print(f"{'ok  ' if r.returncode == 0 else 'FAIL'} {name}")
        if r.returncode:
            failed.append(name)
            print(r.stdout[-2000:], r.stderr[-2000:])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
