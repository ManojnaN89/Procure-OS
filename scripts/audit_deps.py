"""Dependency audit: known vulnerabilities in the Python and npm packages the platform uses.

    python scripts/audit_deps.py [--pip-audit PATH] [--offline-ok]

pip-audit (Apache-2.0, from the Python Packaging Authority) is not a dependency of the platform. Install it in a scratch
environment (python -m venv X; X/Scripts/pip install pip-audit) and pass its path, or put it on PATH. npm audit comes
with npm. Both need the network.

Exit 1 when a Python package has any known vulnerability, or an npm package has a high or critical one, that is not in
docs/audit-accepted.json. An accepted entry names the advisory, says why it is accepted and when to look at it again;
past that date it counts as open again.
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCEPTED = ROOT / "docs" / "audit-accepted.json"
NPX = shutil.which("npm") or "npm"


def accepted(today: date) -> tuple[dict[str, dict[str, str]], list[str]]:
    """{advisory id: entry} for entries still in date, and the ids whose review date has passed."""
    if not ACCEPTED.exists():
        return {}, []
    live: dict[str, dict[str, str]] = {}
    lapsed: list[str] = []
    for entry in json.loads(ACCEPTED.read_text(encoding="utf-8")):
        if date.fromisoformat(entry["review_by"]) >= today:
            live[entry["id"]] = entry
        else:
            lapsed.append(entry["id"])
    return live, lapsed


def pip_findings(pip_audit: str) -> list[dict[str, str]]:
    out = subprocess.run(  # noqa: S603
        [pip_audit, "-r", str(ROOT / "backend" / "requirements.txt"), "--no-deps", "--disable-pip", "-f", "json"],
        capture_output=True, text=True, check=False, timeout=600,
    )  # fmt: skip
    data = json.loads(out.stdout)
    found = []
    for dep in data["dependencies"]:
        for v in dep.get("vulns", []):
            found.append(
                {
                    "ecosystem": "pip",
                    "package": dep["name"],
                    "id": v["id"],
                    "severity": "unknown",
                    "aliases": ",".join(v.get("aliases", [])),
                }
            )
    return found


def npm_findings() -> list[dict[str, str]]:
    out = subprocess.run(  # noqa: S603
        [NPX, "audit", "--json"], cwd=ROOT / "frontend", capture_output=True, text=True, check=False, timeout=600, shell=sys.platform == "win32"
    )  # fmt: skip
    data = json.loads(out.stdout)
    found = []
    for name, v in data.get("vulnerabilities", {}).items():
        for via in v["via"]:
            if isinstance(via, dict):
                found.append(
                    {
                        "ecosystem": "npm",
                        "package": name,
                        "id": str(via["url"]).rsplit("/", 1)[-1],
                        "severity": via["severity"],
                        "aliases": "",
                    }
                )
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pip-audit", default=shutil.which("pip-audit") or "")
    ap.add_argument(
        "--offline-ok",
        action="store_true",
        help="exit 0 when the audit tools cannot be run",
    )
    args = ap.parse_args()
    found: list[dict[str, str]] = []
    try:
        if args.pip_audit:
            found += pip_findings(args.pip_audit)
        else:
            print("pip-audit not found: the Python packages were not audited")
        found += npm_findings()
    except (OSError, ValueError, subprocess.TimeoutExpired, KeyError) as exc:
        print(f"the audit could not be run: {type(exc).__name__}: {exc}")
        return 0 if args.offline_ok else 1
    live, lapsed = accepted(datetime.now(UTC).date())
    open_ones = [
        f for f in found
        if (f["ecosystem"] == "pip" or f["severity"] in ("high", "critical")) and f["id"] not in live
    ]  # fmt: skip
    for f in found:
        mark = "ACCEPTED" if f["id"] in live else ("OPEN" if f in open_ones else "below the bar")
        print(f"{mark:14} {f['ecosystem']:4} {f['package']:22} {f['id']:24} {f['severity']}")
    for gone in lapsed:
        print(f"review date passed for accepted advisory {gone}")
    print(f"{len(found)} finding(s), {len(open_ones)} open")
    return 1 if open_ones else 0


if __name__ == "__main__":
    raise SystemExit(main())
