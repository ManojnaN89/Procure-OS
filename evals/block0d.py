"""Shared pieces of the block 0d evals (E78-E83): company states and go-live. Written from the block 0d spec (and the
interfaces block 0c names), the plan and the dataset files only, never from the implementation.

- **Reading the platform's own databases** as the database owner, for reading only (block 0c: the app login cannot read
  an archive, and the control tables `tenant`, `tenant_state_change`, `tenant_archive` are the platform team's): the
  owner URL is `CONTROL_DATABASE_URL`, which `evals/env.py` points at the evals' own control database.
- **A company** (`Company`): a demo company made by `demo.reset_company(..., state="setup")` (spec 4.8, `--state setup`)
  in a tenant of its own, its people signed in with their own logins (the profile's `login` names and `demo.DEV_PASSWORD`),
  the public API, the CLI run in this process (`block1.cli`), and the tables the spec names.
- **The cutover files** (`cutover/`, `cutover-bad/`, `truth/truth_cutover.csv`, spec section 6) read as plain CSV.
- **A short business flow** (`test_flow`): a request approved and ordered on a contract price, received, invoiced, as the
  role logins do it on the screens.

Where the spec leaves a shape open (a JSON key, a line number convention), the helpers accept the reasonable shapes and
say so in a comment.
"""

import contextlib
import csv
import io
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import env
import lib
from block1 import cli, js, read_csv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Connection, make_url
from sqlalchemy.pool import NullPool

CENT = Decimal("0.01")
Z = Decimal(0)
STATES = ("setup", "cutover", "live", "suspended")
DOMAINS = {"apex": "apexpumps.example", "brightwave": "brightwave.example"}
CUTOFF = date(2026, 10, 31)  # the datasets' cutoff: the open documents are as at this day (spec section 6)

# the exact texts of the spec (4.1, 4.6)
SIGN_IN_CUTOVER = "Sign-in opens at go-live: the company is moving its open documents to the platform"
SUSPENDED = "This company is suspended. Contact the platform team."
BANNER_SETUP = "Setup: what you do here is test data. It is archived at cutover."
BANNER_CUTOVER = "Cutover: loading open documents. Sign-in opens at go-live."
NO_GOING_BACK = "a live company does not go back"
REASON_NEEDED = "a reason is needed"
LOCK_STOCK_HISTORY = "opening stock and its history are locked after go-live; correct stock with a count"
LOCK_OPENING = "opening stock is locked after go-live; post a stock count instead"
LOCK_BANK = "bank accounts are locked after go-live; use a bank change (a second person verifies it)"
CUTOVER_FILES = (
    "po",
    "po_line",
    "goods_receipt",
    "gr_line",
    "invoice",
    "invoice_line",
    "advance",
    "supplier_ytd",
    "control",
)


def money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def D(value: Any) -> Decimal:
    try:
        return Decimal(str(value)) if value not in (None, "") else Z
    except ArithmeticError:
        return Z


# ---- the owner's view (reading only) ----------------------------------------------------------------------------------


def owner_url(database: str | None = None) -> URL:
    url = make_url(os.environ["CONTROL_DATABASE_URL"])
    return url.set(database=database) if database else url


def app_url(database: str) -> URL:
    """The restricted app login of block 0c: APP_DATABASE_USER, else `<control database>_app`."""
    user = os.environ.get("APP_DATABASE_USER") or f"{owner_url().database}_app"
    return owner_url(database).set(username=user, password=os.environ.get("APP_DATABASE_PASSWORD") or "")


@contextlib.contextmanager
def connect(url: URL) -> Iterator[Connection]:
    """One connection of its own (no pool), closed afterwards."""
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as conn:
            yield conn
    finally:
        engine.dispose()


def rows_of(database: str | None, sql: str, **params: Any) -> list[dict[str, Any]]:
    """Rows of a query on a database (None: the control database), read as the owner."""
    with connect(owner_url(database)) as conn:
        return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def control(sql: str, **params: Any) -> list[dict[str, Any]]:
    return rows_of(None, sql, **params)


def control_write(sql: str, **params: Any) -> None:
    """A write on the control database as the owner: only to simulate what the spec says can happen (a process that
    died between two steps, time that has passed)."""
    with connect(owner_url()) as conn:
        conn.execute(text(sql), params)
        conn.commit()


def tenant_database(slug: str) -> str:
    found = control(
        "select d.database_name from tenant_database d join tenant t on t.id = d.tenant_id where t.slug = :s", s=slug
    )
    return str(found[0]["database_name"]) if found else ""


def databases(like: str) -> set[str]:
    return {r["datname"] for r in control("select datname from pg_database where datname like :p", p=like)}


def tenant_row(slug: str) -> dict[str, Any]:
    found = control("select * from tenant where slug = :s", s=slug)
    return found[0] if found else {}


def state_of(slug: str) -> str | None:
    return tenant_row(slug).get("state")


def moves(slug: str) -> list[dict[str, Any]]:
    """The control table of state changes (spec 5.1), oldest first."""
    return control(
        "select c.* from tenant_state_change c join tenant t on t.id = c.tenant_id where t.slug = :s order by c.id",
        s=slug,
    )


def count_in(database: str, table: str, where: str = "true") -> int | None:
    """Rows of a table (None when the table is not there)."""
    try:
        return int(rows_of(database, f"select count(*) as n from {table} where {where}")[0]["n"])  # noqa: S608 - fixed names
    except Exception:  # noqa: BLE001 - a table that does not exist reads as None
        return None


def has_no(database: str, table: str, no: str) -> bool:
    """Whether a database holds a document of a table by its number (fixed table names only)."""
    return bool(rows_of(database, f"select 1 from {table} where no = :n", n=no))  # noqa: S608 - fixed table names


def snapshot(slug: str) -> dict[str, Any]:
    """What another company must keep while one company changes state: its state, logins, history and rows."""
    t = tenant_row(slug)
    database = tenant_database(slug)
    logins = control(
        "select count(*) as n from app_user u join tenant x on x.id = u.tenant_id where x.slug = :s", s=slug
    )[0]["n"]
    found: dict[str, Any] = {
        "state": t.get("state"),
        "state_changed_at": str(t.get("state_changed_at")),
        "moves": len(moves(slug)),
        "logins": logins,
    }
    for table in ("audit_event", "request", "po", "invoice", "stock_movement", "setup_change", "supplier"):
        found[table] = count_in(database, table)
    return found


@contextlib.contextmanager
def patched(owner: Any, name: str, replacement: Any) -> Iterator[int]:
    """Replace a function everywhere it is bound in the p2p package (the module itself and every module that imported
    the name), for a mutation check. Yields how many places were changed (0: the mutation could not be applied)."""
    original = getattr(owner, name, None)
    places: list[Any] = []
    if original is not None:
        for module in list(sys.modules.values()):
            if module is not None and getattr(module, "__name__", "").startswith("p2p"):
                if getattr(module, name, None) is original:
                    places.append(module)
        if owner not in places:
            places.append(owner)
    for module in places:
        setattr(module, name, replacement)
    try:
        yield len(places)
    finally:
        for module in places:
            setattr(module, name, original)


# ---- the dataset (what the oracles read) ------------------------------------------------------------------------------


def csv_rows(path: Path) -> list[dict[str, str]]:
    return read_csv(path)


def master(dataset: Path, name: str) -> list[dict[str, str]]:
    return read_csv(dataset / "master" / f"{name}.csv")


def history(dataset: Path, name: str) -> list[dict[str, str]]:
    return read_csv(dataset / "history" / f"{name}.csv")


def truth_cutover(dataset: Path, company: str) -> list[dict[str, str]]:
    """`truth/truth_cutover.csv`: company,kind,key,expected,reason_contains,amount (spec section 6). The company column
    may name the profile (apex) or the company; both are accepted."""
    found = read_csv(dataset / "truth" / "truth_cutover.csv")
    names = {company, company.lower(), DOMAINS.get(company, "")}
    mine = [r for r in found if r.get("company", "").lower() in names or company in r.get("company", "").lower()]
    return mine or found  # a truth file of one company only


def on_hand_oracle(
    dataset: Path, extra: list[tuple[str, str, str, Decimal]] | None = None
) -> dict[tuple[str, str], Decimal]:
    """Stock per (item, location) replayed from `stock_opening.csv` and `history/stock_movement.csv` (receipts, transfers
    in and adjustments add; the rest take away; movements before the opening date are inside the opening), plus extra
    movements (type, item, location, qty) the eval made itself."""
    opening = {(r["item_code"], r["location_code"]): r for r in master(dataset, "stock_opening")}
    stock = {k: D(r["qty"]) for k, r in opening.items()}
    moved = [
        (m["type"], m["item_code"], m["location_code"], D(m["qty"]), m["date"])
        for m in history(dataset, "stock_movement")
    ]
    moved += [(t, i, loc, q, "9999-12-31") for t, i, loc, q in extra or []]
    for kind, item, loc, qty, day in moved:
        o = opening.get((item, loc))
        if o and o.get("as_of") and day < o["as_of"]:
            continue
        sign = 1 if kind in ("receipt", "transfer_in", "adjust") else -1
        stock[(item, loc)] = stock.get((item, loc), Z) + sign * qty
    return stock


# ---- a company ----------------------------------------------------------------------------------------------------


@dataclass
class Company:
    slug: str
    profile: str
    dataset: Path
    client: Any
    db: Any  # a Session as the app (the platform's own engine)
    people: dict[str, dict[str, str]] = field(default_factory=dict)  # login name -> master user row

    @classmethod
    def make(cls, prefix: str, profile: str = "apex", *, state: str | None = "setup") -> "Company":
        """A demo company from its profile. `state="setup"` is the spec's `--state setup` (4.8); None is a plain reset,
        which the spec says ends live."""
        from fastapi.testclient import TestClient
        from sqlalchemy.orm import Session

        from p2p.main import app
        from p2p.platform import demo, routing

        slug = env.slug(prefix)
        folder = Path(tempfile.mkdtemp(prefix=f"{prefix}_"))
        dataset = folder / "dataset"
        if state is None:
            demo.reset_company(demo.company(profile), dataset, credentials=None, slug=slug)
        else:
            demo.reset_company(demo.company(profile), dataset, credentials=None, slug=slug, state=state)  # type: ignore[call-arg]
        people = {u["email"].split("@")[0]: u for u in master(dataset, "user")}
        return cls(slug, profile, dataset, TestClient(app), Session(routing.engine_for(slug)), people)

    # -- people
    @property
    def domain(self) -> str:
        return DOMAINS[self.profile]

    def email(self, login: str) -> str:
        return f"{login}@{self.domain}"

    def login_of(self, code: str) -> str:
        """The login name of an organisation person by code (U01 ...)."""
        return next(k for k, u in self.people.items() if u["code"] == code)

    def by_designation(self, designation: str) -> str:
        return next(k for k, u in self.people.items() if u["designation"] == designation)

    def holders(self, role: str) -> list[str]:
        """Login names of the people who hold an organisation role (user_role.csv: requester, approver, store, ...)."""
        codes = {r["user_code"] for r in master(self.dataset, "user_role") if r["role"] == role}
        return sorted(k for k, u in self.people.items() if u["code"] in codes)

    def uid(self, login: str) -> int:
        from p2p.platform import users

        return int(users.user_id_by_email(self.slug, self.email(login)) or 0)

    def role(self, login: str) -> str:
        return str(self.one("select role from user_profile where user_id = :u", u=self.uid(login)) or "Requester")

    def h(self, login: str) -> dict[str, str]:
        """A fresh access token of the person (minted per call, so a long eval never meets an expired one)."""
        from p2p.platform.tokens import create_access_token

        return {"Authorization": f"Bearer {create_access_token(self.uid(login), self.slug, self.role(login))}"}

    def sign_in(self, login: str) -> Any:
        """`/api/auth/login` with the dev password. The evals sign in many times on purpose, refused ones included: the
        in-memory limit of failed sign-ins per address is cleared first so it never answers 429 for the eval."""
        from p2p.platform import ratelimit

        ratelimit.reset()
        return self.client.post("/api/auth/login", json={"email": self.email(login), "password": _dev_password()})

    # -- the API and the database
    def call(self, login: str | dict[str, str], method: str, path: str, body: Any = None, **kw: Any) -> Any:
        self.db.rollback()
        headers = login if isinstance(login, dict) else self.h(login)
        r = self.client.request(method, path, json=body, headers=headers, **kw)
        self.db.rollback()
        return r

    def q(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        self.db.rollback()
        found = [dict(r) for r in self.db.execute(text(sql), params).mappings()]
        self.db.rollback()
        return found

    def one(self, sql: str, **params: Any) -> Any:
        self.db.rollback()
        value = self.db.execute(text(sql), params).scalar()
        self.db.rollback()
        return value

    def owner_q(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        return rows_of(tenant_database(self.slug), sql, **params)

    def state(self) -> str | None:
        return state_of(self.slug)

    def audit(self, action: str) -> list[dict[str, Any]]:
        return self.owner_q("select * from audit_event where action = :a order by id", a=action)

    def audit_count(self) -> int:
        return int(self.owner_q("select count(*) as n from audit_event")[0]["n"])

    def work(self, rounds: int = 60) -> None:
        from p2p.platform import worker

        self.db.commit()
        for _ in range(rounds):
            if not worker.run_once(tenant=self.slug):
                break
        self.db.rollback()

    def approve(self, pid: int, login: str) -> Any:
        return self.call(login, "POST", f"/api/admin/proposals/{pid}/approve", {})

    def reject(self, pid: int, login: str, reason: str = "eval") -> Any:
        return self.call(login, "POST", f"/api/admin/proposals/{pid}/reject", {"reason": reason})

    def cli(self, *args: str) -> tuple[int, str]:
        self.db.rollback()
        return cli(*args)

    def drop(self) -> None:
        from p2p.platform import tenants

        with contextlib.suppress(Exception):
            self.db.close()
        tenants.drop_tenant(self.slug)

    # -- block 0d's interfaces
    def readiness(self, group: str = "all", login: str = "admin") -> dict[str, Any]:
        r = self.call(login, "GET", "/api/company/readiness", params={"group": group})
        found = js(r) if r.status_code == 200 else {}
        found["_status"] = r.status_code
        return found

    def go_live(self, maker: str = "admin", reason: str = "eval: ready to go live") -> Any:
        return self.call(maker, "POST", "/api/company/go-live", {"reason": reason})

    def cutover(self, reason: str = "eval cutover", operator: str = "Eval Operator", *more: str) -> tuple[int, str]:
        return self.cli("company", "cutover", self.slug, "--reason", reason, "--operator", operator, *more)

    def cutover_import(self, folder: Path, login: str, *more: str) -> tuple[int, str]:
        return self.cli("company", "cutover-import", self.slug, str(folder), "--as", self.email(login), *more)

    def upload(self, folder: Path, login: str, dry_run: bool) -> Any:
        """`POST /api/company/cutover/import` with every CSV of a folder (multipart `files`, form `dry_run`)."""
        from p2p.platform import ratelimit

        files = [("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted(folder.glob("*.csv"))]
        ratelimit.reset()  # uploads per person per minute: the eval uploads many times in a minute
        self.db.rollback()
        return self.client.post(
            "/api/company/cutover/import",
            files=files,
            data={"dry_run": "true" if dry_run else "false"},
            headers=self.h(login),
        )

    def make_live(self, folder: Path | None = None, maker: str = "admin", checker: str = "admin2") -> list[str]:
        """setup -> cutover -> (cutover import of `cutover/`) -> live, as the spec's steps do it. Returns what went
        wrong (empty when the company is live)."""
        wrong: list[str] = []
        if self.state() == "setup":
            code, said = self.cutover()
            if code != 0:
                wrong.append(f"company cutover exit {code}: {said[-400:]}")
        if self.state() == "cutover":
            code, said = self.cutover_import(folder or self.dataset / "cutover", "finance")
            if code != 0:
                wrong.append(f"company cutover-import exit {code}: {said[-400:]}")
            r = self.go_live(maker)
            pid = js(r).get("proposal_id")
            if r.status_code != 202 or not pid:
                wrong.append(f"go-live answered {r.status_code} {r.text[:300]}")
            else:
                done = self.approve(int(pid), checker)
                if done.status_code != 200:
                    wrong.append(f"go-live approval answered {done.status_code} {done.text[:300]}")
        if self.state() != "live":
            wrong.append(f"the company is {self.state()}, not live")
        return wrong


def _dev_password() -> str:
    from p2p.platform import demo

    return str(demo.DEV_PASSWORD)


def failed(report: dict[str, Any], severity: str = "blocker") -> set[str]:
    """Codes of the checks that failed with this severity."""
    return {c.get("code", "") for c in report.get("checks", []) if not c.get("ok") and c.get("severity") == severity}


def check_of(report: dict[str, Any], code: str) -> dict[str, Any]:
    return next((c for c in report.get("checks", []) if c.get("code") == code), {})


def problems_of(report: dict[str, Any], code: str) -> list[str]:
    return [str(p) for p in check_of(report, code).get("problems") or []]


def copy_folder(src: Path, prefix: str) -> Path:
    dst = Path(tempfile.mkdtemp(prefix=prefix)) / src.name
    shutil.copytree(src, dst)
    return dst


def write_csv(path: Path, rows: list[dict[str, str]], header: list[str] | None = None) -> None:
    header = header or (list(rows[0].keys()) if rows else [])
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=header, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: r.get(k, "") for k in header})
    path.write_text(buf.getvalue(), encoding="utf-8")


def header_of(path: Path) -> list[str]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f), [])


# ---- a short business flow, as the role logins do it ---------------------------------------------------------------


def contract_item(co: Company) -> tuple[str, str, Decimal]:
    """(item, supplier, price) of the cheapest goods item on an active contract price in the company's files."""
    kinds = {c["code"]: c["kind"] for c in master(co.dataset, "category")}
    items = {i["code"]: i for i in master(co.dataset, "item")}
    found = [
        p
        for p in master(co.dataset, "price_record")
        if p.get("contract_code")
        and p.get("status", "active") != "draft"
        and kinds.get(items.get(p["item_code"], {}).get("category_code", ""), "") == "goods"
    ]
    best = min(found, key=lambda p: D(p["price"]))
    return best["item_code"], best["supplier_code"], D(best["price"])


def test_flow(co: Company, when: str, wrong: list[str], qty: Decimal = Decimal(2)) -> dict[str, str]:
    """A request for a contract item, approved by whoever its chain names, ordered, received and invoiced, each step
    by the person whose job it is. Returns the document numbers; adds a line to `wrong` for each step that failed."""
    item, supplier, price = contract_item(co)
    locs = [r for r in master(co.dataset, "item_location") if r["item_code"] == item]
    location = locs[0]["location_code"] if locs else master(co.dataset, "location")[0]["code"]
    requester = co.holders("requester")
    who = next((k for k in requester if co.people[k]["code"] == "U01"), requester[0])
    made: dict[str, str] = {}
    r = co.call(
        who,
        "POST",
        "/api/requisitions",
        {
            "lines": [{"item_code": item, "qty": str(qty)}],
            "need_by": (lib.TODAY + timedelta(days=21)).isoformat(),
            "location": location,
            "reason": f"eval {when}",
        },
    )
    no = str(js(r).get("id") or js(r).get("no") or "")
    if r.status_code != 200 or not no:
        wrong.append(f"{when}: the request was not made: {r.status_code} {r.text[:200]}")
        return made
    made["request"] = no
    co.call(who, "POST", f"/api/requisitions/{no}/submit", {})
    for _ in range(4):  # each step by the person the chain names
        waiting = co.q("select approver from approval_step where request_no = :n and status = 'pending'", n=no)
        if not waiting:
            break
        for step in waiting:
            co.call(co.login_of(step["approver"]), "POST", f"/api/requisitions/{no}/approve", {})
    status = co.one("select status from request where no = :n", n=no)
    if status not in ("approved", "sourcing", "ordered", "closed"):
        wrong.append(f"{when}: request {no} is {status}, not approved")
    co.work()
    po = co.one("select po_no from po_line where request_no = :n order by po_no limit 1", n=no)
    if not po:
        wrong.append(f"{when}: no order was made for request {no} ({item} on contract with {supplier})")
        return made
    made["po"] = str(po)
    buyer = co.holders("buyer")[0]
    approvers = co.holders("approver")
    for _ in range(3):
        status = co.one("select status from po where no = :n", n=po)
        if status == "submitted":
            for person in approvers:
                if co.call(person, "POST", f"/api/purchase-orders/{po}/approve", {"comment": "ok"}).status_code == 200:
                    break
        elif status == "approved":
            co.call(buyer, "POST", f"/api/purchase-orders/{po}/send")
        elif status == "sent":
            co.call(buyer, "POST", f"/api/purchase-orders/{po}/acknowledge", {"note": "eval: confirmed by phone"})
    lines = co.q("select line_no, qty, unit_price from po_line where po_no = :n order by line_no", n=po)
    store = co.holders("store")[0]
    # block 9 (spec 9.2 and 10.1): one unit above the order is held in quarantine as excess, never refused
    item_row = next(i for i in master(co.dataset, "item") if i["code"] == item)
    over = Decimal(1) if item_row.get("inspection_required") != "true" and len(lines) == 1 else Decimal(0)
    received = [{**ln, "qty_received": D(ln["qty"]) + over} for ln in lines]
    got = co.call(
        store,
        "POST",
        f"/api/purchase-orders/{po}/receive",
        {"challan": f"DC-{str(po)[-5:]}", "vehicle": "MH12AB1234",
         "lines": [{"line_no": ln["line_no"], "qty_received": str(ln["qty_received"])} for ln in received]},
    )  # fmt: skip
    if got.status_code != 200:
        wrong.append(f"{when}: receiving {po} answered {got.status_code} {got.text[:200]}")
    elif over and not D(js(got).get("excess", {}).get(str(lines[0]["line_no"])) or 0) > 0:
        wrong.append(f"{when}: one unit above {po} was not held as excess: {js(got).get('excess')}")
    quality = co.holders("quality")[0]  # block 9 (spec 9.1, 10.1): the receiver never inspects
    for gr in co.q("select no from goods_receipt where po_no = :n and inspection_status = 'pending'", n=po):
        co.call(
            quality,
            "POST",
            f"/api/receipts/{gr['no']}/inspect",
            {
                "lines": [
                    {"line_no": ln["line_no"], "qty_accepted": str(ln["qty"]), "qty_rejected": "0"} for ln in lines
                ]
            },
        )
    gr_no = co.one("select no from goods_receipt where po_no = :n order by no limit 1", n=po)
    if gr_no:
        made["receipt"] = str(gr_no)
    sup = next(s for s in master(co.dataset, "supplier") if s["code"] == supplier)
    body = {
        "supplier_code": supplier,
        "number": f"EV-{when[:4].upper()}-{str(po)[-5:]}",
        "date": lib.TODAY.isoformat(),
        "supplier_gstin": sup["gstin"],
        "buyer_gstin": master(co.dataset, "gst_registration")[0]["gstin"],
        "lines": [{"po_no": po, "po_line_no": ln["line_no"], "qty": str(ln["qty"]), "unit_price": str(ln["unit_price"])} for ln in lines],
    }  # fmt: skip
    if sup.get("e_invoice_applicable") == "true":
        body["irn"] = "B" * 64
    inv = co.call(co.holders("ap")[0], "POST", "/api/invoices", body)
    if inv.status_code != 200:
        wrong.append(f"{when}: the invoice for {po} answered {inv.status_code} {inv.text[:200]}")
    else:
        made["invoice"] = str(js(inv).get("invoice") or "")
    return made


def first_rows(rows: list[dict[str, Any]], n: int = 3) -> list[dict[str, Any]]:
    return [{k: v for k, v in r.items() if k in ("no", "code", "key", "reason", "state", "status")} for r in rows[:n]]


Check = Callable[..., bool]
