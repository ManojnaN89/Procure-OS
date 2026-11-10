"""E12: hygiene the rules ask for, checked mechanically: no floats in money, money as exact text, India's calendar day,
no secrets in the repository, synthetic data kept synthetic."""

import ast
import re
import subprocess

import lib
import pipeline
from fastapi.testclient import TestClient
from lib import Outcome
from sqlalchemy import text

ID = "E12"
TITLE = "No floats in money, exact amounts on the wire, India's calendar, no secrets, synthetic data stays synthetic"
REQUIREMENT = "Rules 1, 9, 10 and the generated-data safety rule"

BACKEND = lib.ROOT / "backend" / "p2p"
MONEY_DIRS = ["formulas", "orders", "requests", "sourcing", "approvals", "receiving"]
MONEY_KEYS = {
    "amount",
    "total",
    "price",
    "unit_price",
    "net_price",
    "est_price",
    "value",
    "taxable",
    "cgst",
    "sgst",
    "igst",
    "freight",
    "packing_forwarding",
    "total_value",
    "total_tax",
    "total_spend",
    "on_hand",
    "on_order",
}
SECRET_PATTERNS = [
    r"gsk_[A-Za-z0-9]{20,}",
    r"nvapi-[A-Za-z0-9_\-]{20,}",
    r"sk-ant-[A-Za-z0-9_\-]{20,}",
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN (RSA |EC )?PRIVATE KEY-----",
    r"AIza[0-9A-Za-z_\-]{30,}",
]


NOT_MONEY = {"calendar.py", "duplicates.py", "document.py"}  # hours and days; a ratio of two wordings; page layout (line widths in points)


def float_uses() -> list[str]:
    found = []
    for d in MONEY_DIRS:
        for path in sorted((BACKEND / d).glob("*.py")):
            if path.name in NOT_MONEY:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "float":
                    found.append(f"{path.relative_to(lib.ROOT)}:{node.lineno} float(...)")
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, float)
                    and "confidence" not in path.read_text(encoding="utf-8").splitlines()[node.lineno - 1]
                ):
                    found.append(f"{path.relative_to(lib.ROOT)}:{node.lineno} float literal {node.value}")
                if (
                    isinstance(node, ast.arg)
                    and isinstance(node.annotation, ast.Name)
                    and node.annotation.id == "float"
                ):
                    found.append(f"{path.relative_to(lib.ROOT)}:{node.lineno} parameter typed float ({node.arg})")
    return found


def money_as_numbers(obj, path: str = "") -> list[str]:
    bad = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            counted = path.endswith("/summary")  # counts such as summary.total are not money
            if k in MONEY_KEYS and not counted and isinstance(v, (int, float)) and not isinstance(v, bool):
                bad.append(f"{path}/{k} = {v!r}")
            bad += money_as_numbers(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:50]):
            bad += money_as_numbers(v, f"{path}[{i}]")
    return bad


def run(ctx) -> Outcome:
    from p2p.main import app

    out = Outcome(True)
    # 1. floats in money code
    floats = float_uses()
    if floats:
        out.find("medium", f"{len(floats)} float use(s) in money code, e.g. {floats[0]}")
    # 2. money on the wire is exact text
    state = pipeline.get_state(ctx)
    world, db = state.world, state.world.db
    client = TestClient(app)
    h = world.headers("Admin")
    req, rfq, po = (
        db.execute(text(f"select no from {t} order by no limit 1")).scalar() for t in ("request", "rfq", "po")
    )
    paths = [
        "/api/requisitions",
        f"/api/requisitions/{req}",
        "/api/rfqs",
        f"/api/rfqs/{rfq}",
        "/api/purchase-orders",
        f"/api/purchase-orders/{po}",
        "/api/inventory",
        "/api/vendors",
        "/api/dashboard",
    ]
    wire_bad: list[str] = []
    for p in paths:
        r = client.get(p, headers=h)
        if r.status_code != 200:
            out.find("low", f"{p} answered {r.status_code}")
            continue
        wire_bad += [f"{p}{x}" for x in money_as_numbers(r.json())]
    kinds = sorted({re.sub(r"\[\d+\]", "[]", x).split(" = ")[0] for x in wire_bad})
    out.notes.append(f"money fields sent as numbers: {kinds}")
    if wire_bad:
        out.find(
            "medium",
            f"{len(wire_bad)} money value(s) are sent as JSON numbers, not exact text, e.g. {wire_bad[0]}",
        )
    # 3. business dates use India's calendar day (rule 9)
    utc_dates = []
    for d in ("approvals", "orders", "sourcing", "requests", "receiving", "platform"):
        for path in sorted((BACKEND / d).glob("*.py")):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if "now(UTC).date()" in line:
                    utc_dates.append(f"{path.relative_to(lib.ROOT)}:{n}")
    if utc_dates:
        out.find(
            "medium",
            f"{len(utc_dates)} place(s) take today's date from UTC, which is the wrong day in India from 18:30 to midnight (rule 9), e.g. {utc_dates[0]}",
        )
    # 4. no secrets in the repository
    tracked = subprocess.run(["git", "ls-files"], cwd=lib.ROOT, capture_output=True, text=True).stdout.splitlines()
    hits = []
    for rel in tracked:
        path = lib.ROOT / rel
        if (
            path.suffix.lower()
            in {
                ".png",
                ".jpg",
                ".pdf",
                ".woff",
                ".woff2",
                ".ttf",
                ".ico",
                ".xlsx",
                ".zip",
                ".gz",
                ".csv",
            }
            or "legacy/" in rel
            or not path.is_file()
        ):
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for pat in SECRET_PATTERNS:
            if re.search(pat, content):
                hits.append(f"{rel} matches {pat[:14]}")
    if hits:
        out.find("high", f"possible secret(s) in tracked files: {hits[:3]}")
    if ".env" in tracked or any(t.endswith(".dev-credentials") for t in tracked):
        out.find("high", "a file with secrets is tracked by git")
    # 5. synthetic data stays synthetic
    bad_pan = bad_phone = bad_email = 0
    for r in lib.rows("master", "supplier"):
        bad_pan += bool(r["pan"]) and "0000" not in r["pan"]
    for folder, name, col in (
        ("master", "supplier_contact", "phone"),
        ("master", "user", "phone"),
    ):
        bad_phone += sum(1 for r in lib.rows(folder, name) if r[col] and not r[col].startswith("+91 00000"))
    for folder, name, col in (
        ("master", "supplier_contact", "email"),
        ("master", "user", "email"),
    ):
        bad_email += sum(1 for r in lib.rows(folder, name) if r[col] and not r[col].endswith(".example"))
    if bad_pan or bad_phone or bad_email:
        out.find(
            "high",
            f"generated data breaks the safety rule: {bad_pan} PAN(s) without 0000, {bad_phone} phone(s), {bad_email} email(s) not ending .example",
        )
    out.metrics = {
        "money modules scanned": len(MONEY_DIRS),
        "float uses": len(floats),
        "money fields as JSON numbers": len(wire_bad),
        "UTC dates in business logic": len(utc_dates),
        "tracked files scanned for secrets": len(tracked),
        "secret hits": len(hits),
        "unsafe generated values": bad_pan + bad_phone + bad_email,
    }
    return out
