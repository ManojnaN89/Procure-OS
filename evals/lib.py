"""Shared pieces of the evals: results, a company built from the generated dataset, a fake mail server, readers for the
raw dataset (so the oracles never touch the platform's own code), and a few small helpers."""

import contextlib
import csv
import json
import os
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import env
from p2p.platform import mail, routing, tenants, users
from sqlalchemy import text
from sqlalchemy.orm import Session

ROOT = env.ROOT
DATASET = ROOT / "data" / "datasets" / os.environ.get("P2P_EVAL_DATASET", "apex-v1")  # the main demo company
PASSWORD = "correct horse battery"
TODAY = date(2026, 11, 6)  # a Friday, a working day a week after the datasets' cutoff


# ---- results ----------------------------------------------------------------------------------


@dataclass
class Finding:
    severity: str  # high, medium or low
    text: str


@dataclass
class Outcome:
    """What one eval found. `passed` is decided by the eval from its own thresholds."""

    passed: bool
    metrics: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def find(self, severity: str, message: str) -> None:
        self.findings.append(Finding(severity, message))
        if severity in ("high", "medium"):
            self.passed = False


@dataclass
class Ctx:
    live: bool = False  # call the real models in the AI evals
    quick: bool = False  # smaller samples
    only: set[str] = field(default_factory=set)
    cache: dict[str, Any] = field(default_factory=dict)  # things several evals share (a built company, a pipeline run)


# ---- the raw dataset (what the oracles read) --------------------------------------------------


def rows(folder: str, name: str, dataset: Path = DATASET) -> list[dict[str, str]]:
    with (dataset / folder / f"{name}.csv").open(encoding="utf-8-sig", newline="") as f:
        found = list(csv.DictReader(f))
    if name == "price_record":  # a draft price version is not in force until a second person activates it (D48)
        found = [r for r in found if r.get("status", "active") != "draft"]
    return found


def location(dataset: Path = DATASET) -> str:
    """The company's first location (the demo companies each have one)."""
    return rows("master", "location", dataset)[0]["code"]


def demo_company(dataset: Path = DATASET) -> Any:
    """The demo company whose profile made the dataset (its chain roles and settings that are not master data)."""
    from p2p.platform import demo

    return demo.company(json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))["profile"])


def department(dataset: Path = DATASET) -> str:
    """The department whose head approves the requests made from stock levels."""
    return str(demo_company(dataset).replenishment_department)


def requester(dataset: Path = DATASET) -> tuple[str, str]:
    """(code, name) of the first person whose only job is to ask for things."""
    roles: dict[str, set[str]] = {}
    for r in rows("master", "user_role", dataset):
        roles.setdefault(r["user_code"], set()).add(r["role"])
    code = min(c for c, held in roles.items() if held == {"requester"})
    return code, next(u["name"] for u in rows("master", "user", dataset) if u["code"] == code)


def busiest_category(dataset: Path = DATASET) -> str:
    """The category with the most approved suppliers, so that a quotation request can ask several."""
    approved = [r["category_code"] for r in rows("master", "supplier_category", dataset)]
    return max(sorted(set(approved)), key=approved.count)


def D(value: str | float | Decimal | None, default: str = "0") -> Decimal:
    return Decimal(str(value)) if value not in (None, "") else Decimal(default)


# ---- fake mail --------------------------------------------------------------------------------


class Outbox:
    """Stands in for the mail server, so the evals never send anything."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send_email(self, to: str, subject: str, body: str, **kw: Any) -> str:
        self.sent.append({"to": to, "subject": subject, "body": body, **kw})
        return str(kw.get("message_id"))


def capture_mail() -> Outbox:
    box = Outbox()
    mail.send_email = box.send_email  # type: ignore[assignment]
    return box


# ---- a company built from the dataset ---------------------------------------------------------

ROLES = ("Admin", "Requester", "Buyer", "Approver", "Store", "AP", "Finance")


@dataclass
class World:
    slug: str
    db: Session
    logins: dict[str, int]  # role -> user id
    emails: dict[str, str]  # role -> email
    org_logins: dict[str, int]  # org person code -> user id (every org person with an approver role has a login)
    seconds_to_build: float = 0.0

    def headers(self, role: str) -> dict[str, str]:
        from p2p.platform.tokens import create_access_token

        return {"Authorization": f"Bearer {create_access_token(self.logins[role], self.slug, role)}"}

    def org_headers(self, code: str) -> dict[str, str]:
        from p2p.platform.tokens import create_access_token

        return {"Authorization": f"Bearer {create_access_token(self.org_logins[code], self.slug, 'Approver')}"}


def build_world(prefix: str = "ev", *, with_stock: bool = True, dataset: Path = DATASET) -> World:
    """A company with the dataset's masters and stock history, a login for every role, and a login for every person in
    the organisation who may approve (found by email, so approval chains can be walked by real approvers)."""
    from p2p.masters.importer import import_masters
    from p2p.receiving.stock import import_movements

    started = time.monotonic()
    name = env.slug(prefix)
    assert tenants.create_tenant(name, load_reference=False).ok
    db = Session(routing.engine_for(name))
    report = import_masters(db, dataset)
    assert report.rejected == [], report.rejected[:3]
    if with_stock:
        assert import_movements(db, dataset).rejected == []
    sfx = uuid.uuid4().hex[:5]
    emails = {r: f"{r.lower()}{sfx}@eval.example" for r in ROLES}
    logins = {r: users.create_user(name, emails[r], PASSWORD, f"{r} Person", r) for r in ROLES}
    # the organisation's own people with the approver role get a login under their own address
    approver_codes = {r["user_code"] for r in rows("master", "user_role", dataset) if r["role"] == "approver"}
    org_logins: dict[str, int] = {}
    for person in rows("master", "user", dataset):
        if person["code"] in approver_codes and person["email"]:
            # a login's email is unique across every company, so each company gets its own address for the same person
            local, _, domain = person["email"].partition("@")
            own = f"{local}.{sfx}@{domain}"
            db.execute(
                text("update org_user set email = :e where code = :c"),
                {"e": own, "c": person["code"]},
            )
            org_logins[person["code"]] = users.create_user(name, own, PASSWORD, person["name"], "Approver")
    from p2p.platform import demo

    demo.configure(db, demo_company(dataset), name)  # who the chain roles are, and who approves planned requests
    return World(name, db, logins, emails, org_logins, round(time.monotonic() - started, 2))


@contextlib.contextmanager
def owner_session(slug: str) -> Iterator[Session]:
    """A session on a company's database as its owner (the app never signs in as the owner): for an eval that does what
    only the owner can, such as back-dating the insert-only setup history, or an oracle that reads."""
    engine = tenants.owner_engine(slug)
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def timed(fn: Callable[[], Any]) -> tuple[Any, float]:
    t = time.monotonic()
    out = fn()
    return out, round(time.monotonic() - t, 3)


def pct(part: float, whole: float) -> float:
    return round(100 * part / whole, 2) if whole else 100.0
