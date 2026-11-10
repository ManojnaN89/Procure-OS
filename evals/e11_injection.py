"""E11: hostile text in names and identifiers does not run as script or reach the database as SQL."""

import os
import subprocess
import sys
import time

import httpx
import lib
import pipeline
from fastapi.testclient import TestClient
from lib import Outcome
from sqlalchemy import text

ID = "E11"
TITLE = "Hostile text is shown as text and never run"
REQUIREMENT = "Security: names from imported data and typed identifiers cannot inject script into the screens or SQL into the database"

PAYLOADS = [
    '<img src=x onerror="window.__pwn=1">',
    '"><script>window.__pwn=1</script>',
    "<svg/onload=window.__pwn=1>",
    "'-window.__pwn=1-'",
    "javascript:window.__pwn=1",
    "'; drop table request; --",
]
PORT = 18181


def serve() -> subprocess.Popen[bytes]:
    py = (
        lib.ROOT
        / "backend"
        / ".venv"
        / ("Scripts" if sys.platform == "win32" else "bin")
        / "python"
    )
    proc = subprocess.Popen(
        [
            str(py),
            "-m",
            "uvicorn",
            "p2p.main:app",
            "--port",
            str(PORT),
            "--log-level",
            "warning",
        ],
        cwd=lib.ROOT / "backend",
        env={**os.environ},
    )
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            if (
                httpx.get(f"http://127.0.0.1:{PORT}/api/health", timeout=1).status_code
                == 200
            ):
                return proc
        except httpx.HTTPError:
            time.sleep(0.4)
    proc.terminate()
    raise RuntimeError("the API did not start")


def run(ctx) -> Outcome:
    from p2p.main import app

    out = Outcome(True)
    world = lib.build_world("e11")
    db = world.db
    # every supplier, item and category name becomes hostile text, then the whole flow runs so every screen has data
    for i, (code,) in enumerate(
        db.execute(text("select code from supplier order by code")).all()
    ):
        db.execute(
            text(
                "update supplier set legal_name = :n, trade_name = :n where code = :c"
            ),
            {"n": PAYLOADS[i % len(PAYLOADS)] + f" S{i}", "c": code},
        )
    for i, (code,) in enumerate(
        db.execute(text("select code from item order by code")).all()
    ):
        db.execute(
            text("update item set name = :n where code = :c"),
            {"n": PAYLOADS[i % len(PAYLOADS)] + f" I{i}", "c": code},
        )
    for i, (code,) in enumerate(
        db.execute(text("select code from item_category order by code")).all()
    ):
        db.execute(
            text("update item_category set name = :n where code = :c"),
            {"n": PAYLOADS[(i + 2) % len(PAYLOADS)] + f" C{i}", "c": code},
        )
    db.commit()
    state = pipeline.run_pipeline(world, seed=3)
    po = db.execute(text("select no from po order by no limit 1")).scalar()
    sup = db.execute(text("select code from supplier order by code limit 1")).scalar()
    req = db.execute(text("select no from request order by no limit 1")).scalar()
    rfq = db.execute(text("select no from rfq order by no limit 1")).scalar()

    # 1. the screens, in a real browser
    from playwright.sync_api import sync_playwright

    proc = serve()
    fired: list[str] = []
    pages_visited = 0
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.on(
                "dialog", lambda d: (fired.append(f"dialog: {d.message}"), d.dismiss())
            )
            page.goto(f"http://127.0.0.1:{PORT}/")
            page.locator("#loginEmail").fill(world.emails["Admin"])
            page.locator("#loginPassword").fill(lib.PASSWORD)
            page.locator("#loginButton").click()
            page.wait_for_selector("#loginOverlay", state="hidden")
            routes = [
                "dashboard",
                "vendors",
                f"vendors/{sup}",
                "contracts",
                "payments",
                "help",
                "notification-history",
                "inventory",
                "requisitions",
                f"requisitions/{req}",
                "rfqs",
                f"rfqs/{rfq}",
                "purchase-orders",
                f"purchase-orders/{po}",
                "receiving",
                "assistant",
                "trail",
                "settings",
            ]
            for r in routes:
                page.evaluate(
                    "window.__pwn = undefined"
                )  # the screens are one page: reset what the last one may have set
                page.goto(f"http://127.0.0.1:{PORT}/#/{r}")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(500)
                pages_visited += 1
                if page.evaluate("window.__pwn") is not None:
                    fired.append(f"script ran on #/{r}")
                # the hostile text must be on the page as text, not as elements
                if page.locator(
                    "#appContent script, #appContent img[onerror], #appContent svg[onload]"
                ).count():
                    fired.append(f"hostile markup became an element on #/{r}")
            browser.close()
    finally:
        proc.terminate()
        proc.wait(timeout=15)

    # 2. SQL through identifiers, query strings and bodies
    client = TestClient(app)
    h = world.headers("Admin")
    before = pipeline.counts(db)
    probes = [
        "/api/requisitions/' OR '1'='1",
        "/api/rfqs/%27%3B%20select%20pg_sleep(8)%3B--",
        "/api/purchase-orders/1%20or%201=1",
        "/api/vendors/' UNION SELECT password_hash FROM x --",
        "/api/search?q=%27%20OR%201%3D1%20--",
        "/api/search?q=%27%3B%20select%20pg_sleep(8)%3B--",
        "/api/inventory?location=%27%20or%20%271%27%3D%271&status=%27",
        "/api/requisitions/..%2F..%2Fadmin%2Fusers",
        "/api/notifications?limit=-1%20union%20select%201",
    ]
    slow, errors500, odd = [], [], []
    for path in probes:
        t = time.monotonic()
        r = client.get(path, headers=h)
        took = time.monotonic() - t
        if took > 4:
            slow.append(f"{path} took {took:.1f}s")
        if r.status_code >= 500:
            errors500.append(f"{path} -> {r.status_code}")
        if r.status_code == 200 and path.startswith(
            ("/api/requisitions/'", "/api/purchase-orders/1", "/api/vendors/'")
        ):
            odd.append(f"{path} -> 200 {r.text[:80]}")
    body_probes = [
        (
            "/api/rfqs/" + str(rfq) + "/quotes",
            {
                "supplier_code": "S'; drop table quote;--",
                "lines": [
                    {
                        "rfq_line_no": "1; drop table quote",
                        "unit_price": "1",
                        "gst_rate": "18",
                    }
                ],
            },
        ),
        ("/api/settings", {"stale_requisition_days'; drop table app_setting;--": "1"}),
        ("/api/requisitions/x'/reject", {"reason": "'; drop table request;--"}),
    ]
    for path, body in body_probes:
        r = client.post(path, json=body, headers=h)
        if r.status_code >= 500:
            errors500.append(f"POST {path} -> {r.status_code}")
    after = pipeline.counts(db)
    if before != after:
        out.find("high", f"the data changed during the probes: {before} -> {after}")
    tables_ok = (
        db.execute(
            text(
                "select count(*) from information_schema.tables where table_name in ('request', 'quote', 'app_setting')"
            )
        ).scalar_one()
        == 3
    )
    if not tables_ok:
        out.find("high", "a table disappeared after the SQL probes")
    out.metrics = {
        "screens opened": pages_visited,
        "hostile payloads": len(PAYLOADS),
        "script ran or markup injected": len(fired),
        "SQL probes": len(probes) + len(body_probes),
        "server errors": len(errors500),
        "slow (sleep ran)": len(slow),
        "orders on screen": pipeline.counts(db)["po"],
        "pipeline note": len(state.notes),
    }
    if fired:
        out.find("high", f"hostile text was executed or became markup: {fired[:3]}")
    if errors500:
        out.find(
            "medium",
            f"{len(errors500)} probe(s) made the server fail (500) instead of being refused: {errors500[:3]}",
        )
    if slow:
        out.find("high", f"a SQL fragment ran in the database: {slow[0]}")
    if odd:
        out.find("medium", f"an injected identifier returned data: {odd[0]}")
    return out
