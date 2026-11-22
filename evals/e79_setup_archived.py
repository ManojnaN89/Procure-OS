"""E79: setup is archived and live starts from a clean database (task 7.2, D-05, owner decision OP-6), written from the
block 0d spec only.

A fresh company of the main demo profile in setup. As its role logins the eval makes test data: a request approved and
ordered on a contract price, its receipt and invoice; a stock issue by the store; a policy change proposed by one
administrator and approved by the other; a supplier's bank account changed and verified on the screen by Finance with a
proof. A job of the company waits in the queue and the store holds a refresh token. Then `p2p.cli company cutover
--dry-run` (databases, rows and audit unchanged) and the real cutover.

The oracle reads both databases as the owner: every carried table (the master tables of `masters/schema.py`, people,
roles, limits, rules, settings, spec 4.2) has exactly the same rows in live as in the archive; none of the test documents,
setup history, proposals or number counters is in live and all are in the archive; live `stock_movement` holds only the
loaded history (`source = 'import'`) and on hand per item equals opening plus the dataset's history replayed (the store's
issue is not there); the bank account verified on the screen is still verified by Finance; the first live audit row is
`company.cutover` naming the archive, its audit row count and its last `row_hash` (and the setup history's last hash);
both hash chains verify; `tenant_archive` holds the archive with those figures; the app login cannot connect to the
archive; the queued job is cancelled and the refresh token no longer works; after go-live the first request number ends
`00001`. Another company is unchanged.

Mutations: (1) transactions carried into live (the platform's `cutover.CARRIED` widened to `request` and its lines, a
setup request made after the company went back to setup is then visible in live); (2) carried stock including the
platform's own movements (an oracle that keeps the store's issue differs from what live holds by exactly the issue).
"""

import time
from decimal import Decimal
from typing import Any

import env
import lib
from block0d import (
    Company,
    D,
    app_url,
    connect,
    control,
    databases,
    has_no,
    on_hand_oracle,
    patched,
    rows_of,
    snapshot,
    tenant_database,
    test_flow,
)
from block1 import Checker, js
from lib import Outcome

ID = "E79"
TITLE = "Setup is archived at cutover; live starts from a clean database with the configuration carried over"
REQUIREMENT = (
    "Task 7.2, D-05 (testing in setup, archived at cutover; cutover rebuilds a clean live database), OP-6 (new database, "
    "the test database kept as an archive), OP-7 (what is carried), OP-22 (queued jobs cancelled)"
)
PDF = ("proof.pdf", b"%PDF-1.4 bank letter for the eval", "application/pdf")
# carried besides the master tables (spec 4.2); stock_movement and document are carried in part and checked apart
CARRIED_TOO = (
    "supplier_bank_account", "user_profile", "user_role", "user_limit", "role_permission", "approver_role",
    "app_setting", "autonomy_setting", "autonomy_global", "buying_route_rule", "supplier_selection_rule",
    "touchless_rule", "rule_table", "item_category", "item_single_source", "erp_column_map", "joiner_kit",
)  # fmt: skip
NOT_CARRIED = ("request", "request_line", "po", "po_line", "goods_receipt", "gr_line", "invoice", "invoice_line",
               "proposal", "setup_change", "number_counter", "task")  # fmt: skip


def run(ctx: lib.Ctx) -> Outcome:
    env.forget_tenants("e79")
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e79")


def table_rows(database: str, table: str) -> list[tuple[str, ...]] | None:
    """Every row of a table as sorted text tuples (None: no such table)."""
    try:
        found = rows_of(database, f"select * from {table}")  # noqa: S608 - names from the schema
    except Exception:  # noqa: BLE001 - a table missing in one database reads as None
        return None
    return sorted(tuple(f"{k}={r[k]}" for k in sorted(r)) for r in found)


def _run(ctx: lib.Ctx) -> Outcome:  # noqa: C901 - one scenario, step by step
    from p2p.masters import schema
    from p2p.platform import audit, worker

    out = Outcome(True)
    check = Checker(out)
    lib.capture_mail()
    other = Company.make("e79b", "brightwave", state="setup")
    before_other = snapshot(other.slug)
    co = Company.make("e79", "apex", state="setup")
    db_name = tenant_database(co.slug)

    # ---- test data made in setup by the role logins ---------------------------------------------------------------
    wrong: list[str] = []
    docs = test_flow(co, "setup", wrong)
    for w in wrong:
        out.find("medium", f"the setup test flow: {w}")
    out.metrics["setup test documents"] = docs
    stock = {k: v for k, v in on_hand_oracle(co.dataset).items() if v > 1}
    item, loc = max(stock, key=lambda k: stock[k])
    issued = Decimal(1)
    r = co.call("store", "POST", "/api/inventory/issue", {"item_code": item, "location": loc, "qty": str(issued)})
    check(r.status_code == 200, f"the store's issue in setup: {r.status_code} {r.text[:200]}", "medium")
    pid = js(
        co.call("admin", "PUT", "/api/setup/policies/duplicate_window_days", {"value": "21", "reason": "eval"})
    ).get("proposal_id")
    if pid:
        co.approve(int(pid), "admin2")
    check(bool(co.q("select 1 from setup_change")), "the setup change was not applied in setup", "medium")
    supplier = "SUP-003"
    asked = co.call("buyer", "POST", f"/api/vendors/{supplier}/bank",
                    {"account_no": "000977000003", "ifsc": "HDFC0007703", "reason": "moved bank"})  # fmt: skip
    bank_pid = js(asked).get("proposal") or js(asked).get("proposal_id")
    for person in ("director", "purchasemanager", "planthead", "finance"):
        if bank_pid and co.approve(int(bank_pid), person).status_code == 200:
            break
    acc = co.q(
        "select id from supplier_bank_account where supplier_code = :c and account_no = '000977000003'", c=supplier
    )
    if acc:
        co.client.post(f"/api/vendors/{supplier}/bank/{acc[0]['id']}/verify", data={"method": _method(co)},
                       files={"file": PDF}, headers=co.h("finance"))  # fmt: skip
    verified = co.q("select verified, verified_by from supplier_bank_account where account_no = '000977000003'")
    check(
        bool(verified) and verified[0]["verified"], f"the screen-verified bank account in setup: {verified}", "medium"
    )
    job = worker.enqueue(co.slug, "e79.never", {})
    store_tokens = js(co.sign_in("store"))
    setup_numbers = {"request": docs.get("request"), "po": docs.get("po"), "invoice": docs.get("invoice")}

    # ---- the dry run changes nothing -----------------------------------------------------------------------------
    dbs = databases(f"{db_name}%")
    counts = {t: _count(db_name, t) for t in ("audit_event", "request", "po", "stock_movement", "setup_change")}
    code, said = co.cutover("eval cutover", "Eval Operator", "--dry-run")
    check(code == 0, f"company cutover --dry-run: exit {code}: {said[-300:]}")
    check(databases(f"{db_name}%") == dbs, f"the dry run made or renamed a database: {databases(f'{db_name}%') ^ dbs}")
    check(co.state() == "setup", f"the dry run moved the company to {co.state()}")
    after_dry = {t: _count(db_name, t) for t in counts}
    check(after_dry == counts, f"the dry run wrote rows: {counts} -> {after_dry}")

    # ---- what setup holds, read before the real cutover --------------------------------------------------------
    carried = sorted({t.name for t in schema.TABLES} | set(CARRIED_TOO))
    setup_rows = {t: table_rows(db_name, t) for t in carried}

    code, said = co.cutover("eval cutover", "Eval Operator")
    check(code == 0 and co.state() == "cutover", f"company cutover: exit {code}, state {co.state()}: {said[-300:]}")
    archives = control(
        "select a.* from tenant_archive a join tenant t on t.id = a.tenant_id where t.slug = :s", s=co.slug
    )
    check(len(archives) == 1, f"one tenant_archive row expected: {archives}")
    if not archives:
        return out
    arch = archives[0]
    archive = str(arch["database_name"])
    check(archive.startswith(f"{db_name}__setup_"), f"the archive is named {archive}")
    check(tenant_database(co.slug) == db_name, "the company's database name changed")
    check(archive in databases(f"{db_name}%"), "the archive database does not exist")
    out.metrics["archive"] = archive

    # carried tables: the same rows in live as in setup (and in the archive)
    differ = {}
    for t in carried:
        live_rows, arch_rows = table_rows(db_name, t), table_rows(archive, t)
        if setup_rows[t] is None:
            continue
        if live_rows != setup_rows[t] or arch_rows != setup_rows[t]:
            differ[t] = f"setup {len(setup_rows[t] or [])}, live {len(live_rows or [])}, archive {len(arch_rows or [])}"
    out.metrics["carried tables compared / different"] = (
        f"{sum(v is not None for v in setup_rows.values())} / {len(differ)}"
    )
    check(not differ, f"carried tables differ between setup and live: {differ}")

    # not carried: the test documents are only in the archive
    for t in NOT_CARRIED:
        live_n = _count(db_name, t)
        check(live_n in (0, None), f"live holds {live_n} rows of {t} (not carried)")
    for kind, no in setup_numbers.items():
        if no:
            check(
                not has_no(db_name, kind, no),
                f"the setup {kind} {no} is in live",
            )
            check(
                has_no(archive, kind, no),
                f"the setup {kind} {no} is not in the archive",
            )
    check(bool(rows_of(archive, "select 1 from setup_change")), "the archive lost the setup history")

    # stock: only the loaded history; on hand = opening + history
    sources = {
        r["source"]: r["n"]
        for r in rows_of(db_name, "select source, count(*) as n from stock_movement group by source")
    }
    check(set(sources) <= {"import"}, f"live stock movements by source: {sources}")
    oracle = on_hand_oracle(co.dataset)
    live_stock = {
        (r["item_code"], r["location_code"]): D(r["on_hand"]) for r in rows_of(db_name, "select * from stock_level")
    }
    off = {k: (live_stock.get(k), v) for k, v in oracle.items() if live_stock.get(k, Decimal(0)) != v}
    check(not off, f"on hand in live differs from opening + history: {dict(list(off.items())[:4])}")
    wrong_oracle = on_hand_oracle(co.dataset, [("issue", item, loc, issued)])
    m2 = "caught" if wrong_oracle.get((item, loc)) != live_stock.get((item, loc)) and not off else "NOT caught"
    out.metrics["mutation: platform stock movements carried"] = m2
    if m2 != "caught":
        out.find("high", "mutation 'platform stock movements carried': the stock check cannot tell")

    acc_live = rows_of(
        db_name, "select verified, verified_by from supplier_bank_account where account_no = '000977000003'"
    )
    check(
        bool(acc_live) and acc_live[0]["verified"] and str(acc_live[0]["verified_by"]) == str(co.uid("finance")),
        f"the screen-verified bank account in live: {acc_live}",
    )

    # the audit trail: a new chain starting with company.cutover that names the archive's last hashes
    first = rows_of(db_name, "select * from audit_event order by id limit 1")
    last_audit = rows_of(archive, "select row_hash from audit_event order by id desc limit 1")
    last_setup = rows_of(archive, "select row_hash from setup_change order by id desc limit 1")
    n_audit = _count(archive, "audit_event")
    after = (first[0].get("after") or {}) if first else {}
    check(
        bool(first) and first[0]["action"] == "company.cutover",
        f"the first live audit row is {first[0]['action'] if first else None}",
    )
    check(after.get("archive") == archive, f"company.cutover names the archive {after.get('archive')}")
    check(
        after.get("archive_audit_rows") == n_audit,
        f"archive_audit_rows {after.get('archive_audit_rows')}, archive has {n_audit}",
    )
    check(
        bool(last_audit) and after.get("archive_audit_last_hash") == last_audit[0]["row_hash"],
        "archive_audit_last_hash is not the archive's last row_hash",
    )
    check(bool(last_setup) and after.get("archive_setup_change_last_hash") == last_setup[0]["row_hash"],
          "archive_setup_change_last_hash is not the archive's last setup_change hash")  # fmt: skip
    archived = rows_of(archive, "select after from audit_event where action = 'company.archived'")
    check(
        bool(archived) and (archived[0]["after"] or {}).get("archive") == archive,
        "no company.archived row in the archive",
    )
    check((arch["audit_rows"], arch["audit_last_hash"]) == (n_audit, last_audit[0]["row_hash"] if last_audit else None),
          f"tenant_archive figures {arch['audit_rows']} / {arch['audit_last_hash']}")  # fmt: skip
    for name in (db_name, archive):
        with OwnerSession(name) as s:
            for chained in ("audit_event", "setup_change"):
                broken = audit.verify_chain(s, chained)
                check(broken is None, f"the {chained} chain of {name} is broken: {broken}")

    # the app login cannot open the archive
    try:
        with connect(app_url(archive)) as conn:
            conn.exec_driver_sql("select 1")
        refused = "connected"
    except Exception as exc:  # noqa: BLE001 - the refusal we expect
        refused = str(exc)
    check(
        "permission denied" in refused.lower() or "42501" in refused, f"the app login on the archive: {refused[:200]}"
    )

    # jobs cancelled, refresh tokens revoked
    status = control("select status from job where id = :i", i=job)
    check(bool(status) and status[0]["status"] == "cancelled", f"the queued job after cutover: {status}")
    if store_tokens.get("refresh_token"):
        r = co.client.post("/api/auth/refresh", json={"refresh_token": store_tokens["refresh_token"]})
        check(r.status_code in (401, 403), f"a refresh token from setup still works: {r.status_code}")
    live_tokens = control(
        "select count(*) as n from refresh_token r join app_user u on u.id = r.user_id join tenant t on t.id = u.tenant_id "
        "where t.slug = :s and r.revoked_at is null and r.expires_at > now()", s=co.slug,
    )[0]["n"]  # fmt: skip
    check(live_tokens == 0, f"{live_tokens} refresh tokens of the company were not revoked at cutover")

    # numbers restart: the first request after go-live
    wrong = co.make_live()
    check(not wrong, f"going live: {wrong}", "medium")
    if co.state() == "live":
        made = co.call("production", "POST", "/api/requisitions",
                       {"lines": [{"item_code": item, "qty": "1"}], "need_by": "2026-11-30", "location": loc, "reason": "first"})  # fmt: skip
        no = str(js(made).get("id") or js(made).get("no") or "")
        check(no.endswith("00001"), f"the first request number in live is {no!r}; numbers restart at cutover")

    # mutation 1: transactions carried (the company goes back to setup, makes a request, cutover with request carried)
    out.metrics["mutation: transactions carried into live"] = _carry_mutation(co, out)

    after_other = snapshot(other.slug)
    check(after_other == before_other, f"another company changed: {before_other} -> {after_other}")
    out.metrics["checks"] = f"{check.done - len(check.failed)} of {check.done} passed"
    return out


def _count(database: str, table: str) -> int | None:
    from block0d import count_in

    return count_in(database, table)


def _method(co: Company) -> str:
    from block0d import master

    found = next((r["value"] for r in master(co.dataset, "setting") if r["key"] == "bank_verification_methods"), "")
    return [w.strip() for w in found.split(",") if w.strip()][0] if found else "bank_letter"


class OwnerSession:
    """A Session as the owner on any database (live or archive), for audit.verify_chain."""

    def __init__(self, database: str) -> None:
        self.database = database

    def __enter__(self) -> Any:
        from block0d import owner_url
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from sqlalchemy.pool import NullPool

        self.engine = create_engine(owner_url(self.database), poolclass=NullPool)
        self.session = Session(self.engine)
        return self.session

    def __exit__(self, *exc: Any) -> None:
        self.session.close()
        self.engine.dispose()


def _widened(container: Any, names: tuple[str, ...], remove: bool = False) -> Any:
    """The same kind of collection with names added (or removed)."""
    if isinstance(container, dict):
        return (
            {k: v for k, v in container.items() if k not in names} if remove else {**container, **dict.fromkeys(names)}
        )
    items = (
        [x for x in container if x not in names] if remove else [*container, *[n for n in names if n not in container]]
    )
    return type(container)(items)


def _carry_mutation(co: Company, out: Outcome) -> str:
    """A live company never goes back to setup and the demo logins are unique across the control database, so the
    company is dropped and a fresh one made in setup; a request is made there, then a cutover whose `CARRIED` also holds
    `request` and `request_line` (and `NOT_CARRIED` no longer does). The check of this eval must see the request."""
    from p2p.platform import cutover

    co.drop()
    mut = Company.make("e79m", "apex", state="setup")
    wrong: list[str] = []
    no = test_flow(mut, "mutation", wrong).get("request")
    if not no:
        return f"not applied (no setup request: {wrong[:1]})"
    with (
        patched(cutover, "CARRIED", _widened(cutover.CARRIED, ("request", "request_line"))) as n,
        patched(cutover, "NOT_CARRIED", _widened(cutover.NOT_CARRIED, ("request", "request_line"), remove=True)),
    ):
        code, said = mut.cutover("eval mutation", "Eval Operator")
        if code != 0 and "exists" in said:  # the archive's name has minutes only (spec 4.2 step 5): the next minute
            time.sleep(61)
            code, said = mut.cutover("eval mutation", "Eval Operator")
    if not n or code != 0:
        return f"not applied ({'no CARRIED constant' if not n else said.strip()[-160:]})"
    seen = bool(rows_of(tenant_database(mut.slug), "select 1 from request where no = :n", n=no))
    if not seen:
        out.find(
            "high", "mutation 'transactions carried into live': the setup request is not visible; the check cannot tell"
        )
    return "caught" if seen else "NOT caught"
