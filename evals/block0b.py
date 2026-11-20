"""Shared pieces of the block 0b evals (E55-E59), written without the implementation.

A demo company made by `demo reset` into its own tenant, its own dataset folder (the oracles read only that), each person
signed in with their own login, calls to the public API, audit rows, the business day pinned for a step, the CLI run in
this process (so a pinned day applies), working days from the reference holiday files, and tolerant readers for answers
whose exact JSON shape the spec leaves open (each eval says where it had to read one that way).
"""

import contextlib
import csv
import io
import json
import runpy
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import env
import lib
import yaml
from sqlalchemy import text

CENT = Decimal("0.01")
REFUSED = (403, 409, 422)


def money(value: Decimal | int | str) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def js(r: Any) -> Any:
    """The JSON of an answer, or {} when it has none."""
    try:
        return r.json()
    except ValueError:
        return {}


def pick(obj: Any, *names: str, default: Any = None) -> Any:
    """The first of these keys an object has (the spec names the content, not always the key)."""
    if isinstance(obj, dict):
        for n in names:
            if n in obj and obj[n] is not None:
                return obj[n]
    return default


def listed(obj: Any, *names: str) -> list[Any]:
    found = obj if isinstance(obj, list) and not names else pick(obj, *names, default=[])
    if isinstance(found, dict):
        found = pick(found, "items", "rows", "records", default=[])
    return found if isinstance(found, list) else []


def dicts(obj: Any) -> Iterator[dict[str, Any]]:
    """Every dict inside an answer, depth first."""
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from dicts(v)


ROW_KEYS = ("rows", "items", "records", "prices", "entries", "lines")


def section(payload: Any, *words: str) -> list[dict[str, Any]] | None:
    """The rows of the section whose key or label holds one of the words (the price view's sections are named in the spec,
    their JSON keys are not); None when the answer has no such section."""
    if isinstance(payload, dict):
        for k, v in payload.items():
            if any(w in k.lower() for w in words):
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]
                if isinstance(v, dict):
                    inner = pick(v, *ROW_KEYS)
                    return [x for x in inner if isinstance(x, dict)] if isinstance(inner, list) else [v]
    for d in dicts(payload):
        label = str(pick(d, "type", "kind", "section", "label", "name", default="")).lower()
        rows = pick(d, *ROW_KEYS)
        if any(w in label for w in words) and isinstance(rows, list):
            return [x for x in rows if isinstance(x, dict)]
    return None


def dec(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value not in (None, "") else None
    except ArithmeticError:
        return None


@dataclass
class Checker:
    """Counts checks and turns a failed one into a finding."""

    out: lib.Outcome
    done: int = 0
    failed: list[str] = field(default_factory=list)

    def __call__(self, ok: bool, message: str, severity: str = "high") -> bool:
        self.done += 1
        if not ok:
            self.failed.append(message)
            self.out.find(severity, message)
        return ok


# ---- the calendar ---------------------------------------------------------------------------------------------------

DAYS = {"Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3, "Fri": 4, "Sat": 5, "Sun": 6}


def open_days(spec: str) -> set[int]:
    """'Mon-Sat' -> {0..5}."""
    first, _, last = spec.partition("-")
    return set(range(DAYS[first], DAYS[last or first] + 1))


def holidays(state: str) -> set[date]:
    """Central closed holidays and the state's list, from data/reference (never from the platform)."""
    ref = lib.ROOT / "data" / "reference"
    closed: set[date] = set()
    for path in sorted(ref.glob("holidays_*central*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                if r.get("type", "closed") == "closed":
                    closed.add(date.fromisoformat(r["date"]))
    for path in sorted(ref.glob("holidays_state_*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as f:
            closed |= {date.fromisoformat(r["date"]) for r in csv.DictReader(f) if r["state_code"] == state}
    return closed


def add_working(start: date, n: int, days: set[int], closed: set[date]) -> date:
    d = start
    while n:
        d += timedelta(days=1)
        if d.weekday() in days and d not in closed:
            n -= 1
    return d


@contextlib.contextmanager
def on_day(day: date) -> Iterator[None]:
    """The business day pinned to `day` for the block (the evals' own day afterwards)."""
    from p2p.formulas.calendar import pin_today

    pin_today(day)
    try:
        yield
    finally:
        pin_today(lib.TODAY)


def cli(*args: str) -> tuple[int, str]:
    """`python -m p2p.cli ...` run inside this process, so the pinned day and the evals' databases apply."""
    saved = sys.argv
    sys.argv = ["p2p.cli", *args]
    buf = io.StringIO()
    code = 0
    try:
        with contextlib.redirect_stdout(buf):
            runpy.run_module("p2p.cli", run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        code = int(exc.code or 0) if not isinstance(exc.code, str) else 1
    finally:
        sys.argv = saved
    return code, buf.getvalue()


# ---- a demo company -------------------------------------------------------------------------------------------------


class Company:
    """A demo company (`demo reset`) in a tenant of its own; its people act through the API with their own logins."""

    def __init__(self, profile: str, prefix: str, client: Any) -> None:
        from sqlalchemy.orm import Session

        from p2p.platform import demo, routing

        self.name = profile
        self.folder = Path(tempfile.mkdtemp(prefix=f"{prefix}_{profile}_"))
        self.dataset = self.folder / "dataset"
        self.slug = env.slug(prefix)
        demo.reset_company(demo.company(profile), self.dataset, credentials=None, slug=self.slug)
        self.db = Session(routing.engine_for(self.slug))
        self.client = client
        self.password = demo.DEV_PASSWORD
        self.profile = yaml.safe_load(
            (lib.ROOT / "datagen" / "catalog" / profile / "company.yaml").read_text(encoding="utf-8")
        )
        self.people = {u["code"]: u for u in self.rows("master", "user")}
        self.code_of_name = {u["name"]: c for c, u in self.people.items()}
        self._headers: dict[str, dict[str, str]] = {}

    # the company's own dataset files (the oracles read these and nothing else)
    def has(self, folder: str, name: str) -> bool:
        return (self.dataset / folder / f"{name}.csv").exists()

    def rows(self, folder: str, name: str) -> list[dict[str, str]]:
        path = self.dataset / folder / f"{name}.csv"
        if not path.exists():
            return []
        with path.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))

    # people and calls
    def headers(self, code: str) -> dict[str, str]:
        if code not in self._headers:
            email = self.people[code]["email"]
            r = self.client.post("/api/auth/login", json={"email": email, "password": self.password})
            self._headers[code] = {"Authorization": "Bearer " + str(js(r).get("access_token", ""))}
        return self._headers[code]

    def forget(self, code: str) -> None:
        self._headers.pop(code, None)

    def call(self, code: str, method: str, path: str, body: Any = None, **kw: Any) -> Any:
        self.db.rollback()
        r = self.client.request(method, path, json=body, headers=self.headers(code), **kw)
        self.db.rollback()
        return r

    def user_id(self, code: str) -> int:
        from p2p.platform import users

        return int(users.user_id_by_email(self.slug, self.people[code]["email"]) or 0)

    # the database, read only (writes go through the API, except where an eval plants data and says so)
    def q(self, sql: str, **p: Any) -> list[Any]:
        self.db.rollback()
        found = list(self.db.execute(text(sql), p).all())
        self.db.rollback()
        return found

    def one(self, sql: str, **p: Any) -> Any:
        found = self.q(sql, **p)
        return found[0][0] if found else None

    def columns(self, table: str) -> set[str]:
        return {
            r[0] for r in self.q("select column_name from information_schema.columns where table_name = :t", t=table)
        }

    def audit(self, action: str, entity_id: str | None = None) -> list[dict[str, Any]]:
        sql = "select actor_id, entity, entity_id, rule_version, after::text, reason from audit_event where action = :a"
        found = self.q(sql + (" and entity_id = :e" if entity_id else "") + " order by id", a=action, e=entity_id)
        keys = ("actor_id", "entity", "entity_id", "rule_version", "after", "reason")
        return [dict(zip(keys, r, strict=True)) for r in found]

    def work(self, rounds: int = 60, now: Any = None) -> None:
        """Run the worker's due jobs; `now` runs them as at that moment (a daily job of a pinned day)."""
        from p2p.platform import worker

        self.db.commit()
        for _ in range(rounds):
            if not worker.run_once(now=now):
                break
        self.db.rollback()

    def proposals(self, action: str, status: str = "pending") -> list[dict[str, Any]]:
        found = self.q(
            "select id, payload::text, proposed_by_id from proposal where action = :a and status = :s order by id",
            a=action,
            s=status,
        )
        return [{"id": r[0], "payload": json.loads(r[1] or "{}"), "by": r[2]} for r in found]

    def approve(self, code: str, proposal: int, note: str = "block 0b eval") -> Any:
        return self.call(code, "POST", f"/api/admin/proposals/{proposal}/approve", {"note": note})

    def grant(self, maker: str, checker: str, code: str, role: str) -> bool:
        """A second job for a person, through the two administrators (D-22)."""
        people = {
            p["person"]["code"]: p for p in js(self.call(maker, "GET", "/api/setup/people"))["people"] if p["person"]
        }
        r = self.call(
            maker, "POST", f"/api/setup/people/{people[code]['user_id']}/roles", {"role": role, "reason": "eval"}
        )
        pid = js(r).get("proposal_id")
        if pid:
            self.approve(checker, int(pid), "second job for an eval")
        self.forget(code)
        roles = {
            p["person"]["code"]: [x["role"] for x in p["roles"]]
            for p in js(self.call(maker, "GET", "/api/setup/people"))["people"]
            if p["person"]
        }
        return role in roles.get(code, [])

    # the flow, as people do it
    def request(self, by: str, item: str, qty: str, location: str, need_by: str = "2026-12-15") -> str:
        """A request made, submitted and approved by whoever each step names; returns its number."""
        body = {"lines": [{"item_code": item, "qty": qty}], "need_by": need_by, "location": location, "reason": "eval"}
        no = str(js(self.call(by, "POST", "/api/requisitions", body)).get("id"))
        r = self.call(by, "POST", f"/api/requisitions/{no}/submit", {})
        if r.status_code == 409 and "repeat" in r.text:  # same item as an earlier case: a separate need
            self.call(by, "POST", f"/api/requisitions/{no}/duplicate-override", {"reason": "a separate eval case"})
            self.call(by, "POST", f"/api/requisitions/{no}/submit", {})
        for _ in range(4):
            steps = pick(js(self.call(by, "GET", f"/api/requisitions/{no}")).get("approval") or {}, "steps", default=[])
            waiting = [s for s in steps if s.get("status") == "pending" and s.get("name") in self.code_of_name]
            if not waiting:
                break
            for s in waiting:
                self.call(self.code_of_name[s["name"]], "POST", f"/api/requisitions/{no}/approve", {})
        return no

    def line(self, by: str, no: str) -> dict[str, Any]:
        lines = js(self.call(by, "GET", f"/api/requisitions/{no}")).get("lines") or [{}]
        return dict(lines[0])

    def rfq_of(self, buyer: str, no: str) -> str:
        """The quotation request a request line went to, its sending confirmed by the buyer (D42)."""
        self.work()
        rfq = str((self.line(buyer, no).get("source") or {}).get("rfq_no") or "")
        if rfq:
            for p in self.proposals("rfq.send"):
                if p["payload"].get("rfq_no") == rfq:
                    self.approve(buyer, p["id"])
            self.work()
        return rfq

    def quote(self, buyer: str, rfq: str, supplier: str, price: str, gst: str, valid_until: str = "2026-12-31") -> Any:
        body = {
            "supplier_code": supplier,
            "valid_until": valid_until,
            "freight": "0",
            "lines": [
                {"rfq_line_no": 1, "unit_price": price, "discount_pct": "0", "gst_rate": gst, "delivery_days": 7}
            ],
        }
        return self.call(buyer, "POST", f"/api/rfqs/{rfq}/quotes", body)

    def award(self, by: str, rfq: str, supplier: str, reason: str | None = None) -> Any:
        body: dict[str, Any] = {"choices": {"1": supplier}}
        if reason:
            body["reason"] = reason
        return self.call(by, "POST", f"/api/rfqs/{rfq}/award", body)

    def approve_po(self, po: str, reader: str = "U06") -> str:
        """Approve an order by whoever it waits for, step by step; returns its status."""
        status = ""
        for _ in range(4):
            d = js(self.call(reader, "GET", f"/api/purchase-orders/{po}"))
            status = str(d.get("status", ""))
            who = self.code_of_name.get(str(d.get("waiting_for") or ""))
            if not who:
                break
            self.call(who, "POST", f"/api/purchase-orders/{po}/approve", {"comment": "eval"})
        return status

    def region_of(self) -> dict[str, str]:
        return {r["code"]: r.get("region_code", "") for r in self.rows("master", "location")}
