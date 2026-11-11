"""Speed of the main operations against a running dev environment (python scripts/dev.py) and the 'apex' demo company.

    python scripts/measure.py

Prints median and 95th percentile in milliseconds. Logins come from the git-ignored .dev-credentials. Safe to repeat: it
only reads, except for one 1 MB test upload.
"""

import statistics
import sys
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx2 as httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
API = "http://127.0.0.1:8000"


def creds(role: str) -> tuple[str, str]:
    for line in (ROOT / ".dev-credentials").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{role}@apexpumps.example"):
            email, password = line.split()[:2]
            return email, password
    raise SystemExit(f"no login for {role}")


def timed(fn: Callable[[], object], n: int) -> list[float]:
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t) * 1000)
    return out


def show(name: str, ms: list[float]) -> None:
    ms = sorted(ms)
    p95 = ms[min(len(ms) - 1, int(len(ms) * 0.95))]
    print(f"{name:42s} n={len(ms):3d}  median {statistics.median(ms):7.1f} ms   p95 {p95:7.1f} ms")


def main() -> None:
    client = httpx.Client(base_url=API, timeout=60)
    email, password = creds("admin")
    show(
        "login (argon2 + token)",
        timed(
            lambda: client.post("/api/auth/login", json={"email": email, "password": password}).raise_for_status(), 10
        ),
    )
    token = client.post("/api/auth/login", json={"email": email, "password": password}).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    def get(path: str) -> Callable[[], None]:
        return lambda: client.get(path, headers=auth).raise_for_status()

    show("GET /api/health", timed(lambda: client.get("/api/health").raise_for_status(), 30))
    show("GET /api/auth/me", timed(get("/api/auth/me"), 50))
    show("GET /api/admin/audit (100 rows)", timed(get("/api/admin/audit?limit=100"), 30))
    show("GET /api/admin/model-calls", timed(get("/api/admin/model-calls"), 30))
    show("GET /api/admin/agent-runs", timed(get("/api/admin/agent-runs"), 30))
    data = b"0" * 1_000_000
    up = []
    for _ in range(5):
        t = time.perf_counter()
        r = client.post("/api/files", headers=auth, files={"file": ("m.bin", data, "application/octet-stream")})
        r.raise_for_status()
        up.append((time.perf_counter() - t) * 1000)
    show("upload 1 MB", up)
    file_id = r.json()["id"]
    show("download 1 MB", timed(lambda: client.get(f"/api/files/{file_id}", headers=auth).raise_for_status(), 10))

    # 20 people at once
    def one(_: int) -> float:
        t = time.perf_counter()
        httpx.Client(base_url=API, timeout=60).get("/api/auth/me", headers=auth).raise_for_status()
        return (time.perf_counter() - t) * 1000

    t0 = time.perf_counter()
    with ThreadPoolExecutor(20) as pool:
        ms = list(pool.map(one, range(100)))
    wall = time.perf_counter() - t0
    show("GET /api/auth/me, 20 at once (100 total)", ms)
    print(f"{'  throughput':42s} {100 / wall:5.0f} requests/second")

    # rules and gateway inside the process
    from sqlalchemy.orm import Session

    from p2p.platform import routing
    from p2p.platform.sod import Actor
    from p2p.rules import engine as rules

    db = Session(routing.engine_for("apex"))
    actor = Actor("service", "measure")
    if not db.execute(
        __import__("sqlalchemy").text("select 1 from rule_table where key = 'measure_demo' and status = 'published'")
    ).first():
        sys.path.insert(0, str(ROOT / "tests"))
        from test_rules_engine import approval_table

        rules.save_draft(db, "measure_demo", approval_table(), actor)
        rules.publish(db, "measure_demo", actor)
    show(
        "rule evaluation (decision table + trace + log)",
        timed(lambda: rules.evaluate(db, "measure_demo", {"value": 60000}, actor), 50),
    )

    from p2p.ai_steps.gateway import Gateway

    gw = Gateway()
    show(
        "model call, Groq gpt-oss-20b (tiny prompt)",
        timed(
            lambda: gw.complete(
                "item_mapping", [{"role": "user", "content": "Reply with the word ok."}], max_tokens=60, use_cache=False
            ),
            5,
        ),
    )


if __name__ == "__main__":
    main()
