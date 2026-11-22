"""E83: setup to live, end to end (task 7.7; Base WP-7 "a company goes Setup to Live; imports refused after go-live"),
written from the block 0d spec only.

For each demo profile (Apex Pumps, then Brightwave) a fresh company is made in setup and taken through the whole path
as the platform team and the company's own people would: readiness in setup; a short test flow (a request approved and
ordered on a contract price, received and invoiced); `p2p.cli company cutover` (the test documents only in the archive);
the cutover import of `cutover/` (for Apex with one goods supplier's purchases this year raised to Rs 50,00,000.00 in
`supplier_ytd.csv`, so the tax at source threshold is already behind it); the stock history import; go-live by two
administrators (first refused while a control change waits: 422 with the blocker); then the first live flow: a request
approved, ordered, received, invoiced and matched, and a payment run that takes the cutover invoices, approved by the
person the `payment_run` rule names (never the proposer). Tax at source on each cutover invoice in the run follows an
oracle written from the plan's numbers (section 194Q: 0.1% of what goes above Rs 50,00,000 in the financial year,
counting the old system's purchases this year), stock history imports are refused after go-live, and a plain demo reset
of both profiles still works and ends live with one `setup -> live` move by the system.

Mutations: (1) go-live allowed with a readiness blocker (`readiness.check` made to say every check passed: the waiting
control change no longer stops the go-live); (2) the year to date ignored (an oracle that starts the supplier from zero
gives no tax on the planted supplier, which the platform must not match).
"""

import dataclasses
from decimal import Decimal
from pathlib import Path
from typing import Any

import env
import lib
from block0d import (
    LOCK_STOCK_HISTORY,
    Company,
    D,
    check_of,
    copy_folder,
    csv_rows,
    failed,
    has_no,
    header_of,
    master,
    money,
    moves,
    patched,
    tenant_database,
    test_flow,
    write_csv,
)
from block1 import Checker, js
from lib import Outcome

ID = "E83"
TITLE = "A company goes from setup to live and pays its cutover invoices in the first live payment run"
REQUIREMENT = (
    "Task 7.7 (evals: Setup to Live), Base WP-7 (a company goes Setup to Live; imports refused after go-live), D-04, "
    "D-05, D-22 (go-live by two administrators), OP-12 (year-to-date purchases for tax at source), plan acceptance gate 9 "
    "(the pilot client rehearses cutover)"
)
THRESHOLD = Decimal("5000000.00")  # section 194Q: Rs 50 lakh per supplier per financial year
RATE = Decimal("0.001")  # 0.1%
FY = "2026-27"


def run(ctx: lib.Ctx) -> Outcome:
    env.forget_tenants("e83")
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e83")


def _run(ctx: lib.Ctx) -> Outcome:
    out = Outcome(True)
    check = Checker(out)
    lib.capture_mail()
    for profile in ("apex", "brightwave"):
        co = Company.make(f"e83{profile[0]}", profile, state="setup")
        try:
            out.metrics[profile] = _walk(co, check, out)
        finally:
            co.drop()
    for profile in ("apex", "brightwave"):  # a plain demo reset still works and ends live
        co = Company.make("e83d", profile, state=None)
        hist = moves(co.slug)
        check(co.state() == "live", f"demo reset {profile} ends {co.state()}, not live")
        check([(m["from_state"], m["to_state"], m["by_kind"]) for m in hist] == [("setup", "live", "system")],
              f"demo reset {profile}: moves {[(m['from_state'], m['to_state'], m['by_kind']) for m in hist]}")  # fmt: skip
        co.drop()
    out.metrics["checks"] = f"{check.done - len(check.failed)} of {check.done} passed"
    return out


def _walk(co: Company, check: Checker, out: Outcome) -> dict[str, Any]:  # noqa: C901 - the whole path, step by step
    name = co.profile
    seen: dict[str, Any] = {}
    check(co.state() == "setup", f"{name}: a new company is in {co.state()}")
    ready = co.readiness("setup")
    check(ready.get("ready") is True, f"{name}: setup readiness blockers {sorted(failed(ready))}")

    wrong: list[str] = []
    setup_docs = test_flow(co, "setup", wrong)
    for w in wrong:
        out.find("medium", f"{name}: {w}")
    code, said = co.cutover()
    check(
        code == 0 and co.state() == "cutover", f"{name}: company cutover exit {code}, state {co.state()}: {said[-200:]}"
    )
    if co.state() != "cutover":
        return seen
    live_db = tenant_database(co.slug)
    archive = next((r["database_name"] for r in _archives(co)), "")
    for kind, no in setup_docs.items():
        table = {"request": "request", "po": "po", "receipt": "goods_receipt", "invoice": "invoice"}[kind]
        check(
            not has_no(live_db, table, no),
            f"{name}: the setup {kind} {no} is in live",
        )
        if archive:
            check(
                has_no(archive, table, no),
                f"{name}: the setup {kind} {no} is not in the archive",
            )

    # the cutover files, with the planted year to date
    folder, planted = _plant_ytd(co)
    seen["planted year to date"] = planted
    code, said = co.cutover_import(folder, "finance")
    check(code == 0, f"{name}: the cutover import exit {code}: {said[-300:]}")
    check(
        check_of(co.readiness("cutover"), "cutover_import").get("ok") is True,
        f"{name}: readiness cutover_import after the import",
    )
    code, said = co.cli("import", "stock", co.slug, str(co.dataset))
    check(code == 0, f"{name}: the stock history import in cutover exit {code}: {said[-200:]}")

    # go-live: refused while a control change waits; then a mutation; then for real
    from p2p.platform import readiness

    waiting = js(
        co.call("admin", "PUT", "/api/setup/policies/combine_window_days", {"value": "9", "reason": "eval"})
    ).get("proposal_id")
    blocked = co.go_live()
    check(blocked.status_code == 422 and "pending_changes" in (js(blocked).get("blockers") or []),
          f"{name}: go-live with a waiting control change: {blocked.status_code} {blocked.text[:200]}")  # fmt: skip
    with patched(readiness, "check", _all_ready(readiness.check)) as n:
        let_through = co.go_live()
    m1 = (
        "not applied"
        if not n
        else ("caught" if let_through.status_code == 202 else "NOT caught (the go-live still refused)")
    )
    seen["mutation: go-live with a readiness blocker"] = m1
    if m1 != "caught":
        out.find("high" if m1.startswith("NOT") else "low", f"{name}: mutation 'go-live with a blocker': {m1}")
    if let_through.status_code == 202 and js(let_through).get("proposal_id"):
        co.reject(int(js(let_through)["proposal_id"]), "admin2")
    if waiting:
        co.reject(int(waiting), "admin2")
    asked = co.go_live()
    pid = js(asked).get("proposal_id")
    check(asked.status_code == 202 and bool(pid), f"{name}: go-live once ready: {asked.status_code} {asked.text[:200]}")
    if pid:
        check(co.approve(int(pid), "admin").status_code == 403, f"{name}: the proposer approved the go-live")
        check(co.approve(int(pid), "admin2").status_code == 200, f"{name}: the second administrator could not approve")
    check(co.state() == "live", f"{name}: the company is {co.state()} after the approved go-live")
    if co.state() != "live":
        return seen

    # the first live flow and the first payment run
    wrong = []
    live_docs = test_flow(co, "live", wrong)
    for w in wrong:
        out.find("medium", f"{name}: first live flow: {w}")
    check(
        str(live_docs.get("request", "")).endswith("00001"),
        f"{name}: the first live request is {live_docs.get('request')}",
    )
    invoice = co.q("select status from invoice where no = :n", n=live_docs.get("invoice", ""))
    check(bool(invoice) and invoice[0]["status"] == "approved", f"{name}: the first live invoice is {invoice}")
    _payment_run(co, check, out, seen, planted)

    code, said = co.cli("import", "stock", co.slug, str(co.dataset))
    check(
        code == 2 and LOCK_STOCK_HISTORY in said, f"{name}: the stock import after go-live: exit {code}: {said[-200:]}"
    )
    seen["first live documents"] = live_docs
    return seen


def _archives(co: Company) -> list[dict[str, Any]]:
    from block0d import control

    return control("select a.* from tenant_archive a join tenant t on t.id = a.tenant_id where t.slug = :s", s=co.slug)


def _all_ready(real: Any) -> Any:
    """readiness.check that reports every check passed (the mutation of 'go-live with a blocker')."""

    def fake(*a: Any, **k: Any) -> Any:
        res = real(*a, **k)
        checks = [
            dataclasses.replace(c, ok=True, problems=[]) if dataclasses.is_dataclass(c) else c for c in res.checks
        ]
        for c in checks:
            if not dataclasses.is_dataclass(c):
                c.ok = True
        try:
            return dataclasses.replace(res, checks=checks, ready=True)
        except (TypeError, ValueError):
            try:
                return dataclasses.replace(res, checks=checks)
            except (TypeError, ValueError):
                res.ready = True
                return res

    return fake


def _goods_suppliers(co: Company) -> set[str]:
    return {s["code"] for s in master(co.dataset, "supplier") if s.get("tds_section_default") == "194Q"}


def _plant_ytd(co: Company) -> tuple[Path, dict[str, Any]]:
    """For Apex: the goods supplier with the most open approved invoices gets Rs 50,00,000.00 bought this year in the
    old system. Brightwave's files are used as they are."""
    if co.profile != "apex":
        return co.dataset / "cutover", {}
    folder = copy_folder(co.dataset / "cutover", "e83_")
    invoices = [
        i for i in csv_rows(folder / "invoice.csv") if i.get("status") == "approved" and D(i.get("paid_amount")) <= 0
    ]
    advanced = {a["po_no"] for a in csv_rows(folder / "advance.csv")}
    lines = csv_rows(folder / "invoice_line.csv")
    on_advance = {ln["invoice_no"] for ln in lines if ln["po_no"] in advanced}
    goods = _goods_suppliers(co)
    by_supplier: dict[str, Decimal] = {}
    for i in invoices:
        if i["supplier_code"] in goods and i["no"] not in on_advance:
            by_supplier[i["supplier_code"]] = by_supplier.get(i["supplier_code"], Decimal(0)) + D(i["taxable"])
    if not by_supplier:
        return folder, {}
    chosen = max(sorted(by_supplier), key=lambda s: by_supplier[s])
    path = folder / "supplier_ytd.csv"
    ytd = [r for r in csv_rows(path) if r["supplier_code"] != chosen]
    ytd.append({"supplier_code": chosen, "fy": FY, "taxable": f"{THRESHOLD:.2f}"})
    write_csv(path, ytd, header_of(path) or ["supplier_code", "fy", "taxable"])
    return folder, {"supplier": chosen, "ytd": str(THRESHOLD), "open taxable": str(by_supplier[chosen])}


def tds_oracle(ytd: Decimal, taxable: Decimal, before: Decimal = Decimal(0)) -> Decimal:
    """Section 194Q on one invoice: 0.1% of what goes above Rs 50 lakh, counting what was bought before in the year."""
    cum = ytd + before
    over = max(Decimal(0), cum + taxable - max(THRESHOLD, cum))
    return money(over * RATE)


def _payment_run(co: Company, check: Checker, out: Outcome, seen: dict[str, Any], planted: dict[str, Any]) -> None:  # noqa: C901
    name = co.profile
    cut = {i["no"] for i in csv_rows((co.dataset / "cutover") / "invoice.csv")}
    finance = "finance"
    r = co.call(finance, "POST", "/api/payment-runs", {"run_date": lib.TODAY.isoformat(), "window_days": 120})
    body = js(r)
    run_no = body.get("run") or body.get("no") or body.get("id")
    check(r.status_code == 200 and bool(run_no), f"{name}: the payment run: {r.status_code} {r.text[:200]}")
    if not run_no:
        return
    paid = co.q(
        "select a.invoice_no, p.supplier_code from payment_allocation a join payment p on p.no = a.payment_no where p.run_no = :r",
        r=run_no,
    )
    in_run = {x["invoice_no"] for x in paid}
    check(bool(in_run & cut), f"{name}: the first live run takes no cutover invoice ({len(in_run)} invoices)")
    seen["first run: cutover invoices"] = len(in_run & cut)

    # who approves: the person the payment_run rule names, never the proposer
    rule = next(r_ for r_ in master(co.dataset, "approval_rule") if r_["document"] == "payment_run")
    role = rule["approver_chain"].lstrip("+").split(">")[0]
    src = co.q("select source, value from approver_role where role = :r", r=role)
    designation = src[0]["value"] if src and src[0]["source"] == "designation" else None
    approver = co.by_designation(designation) if designation else None
    pending = [p for p in js(co.call(finance, "GET", "/api/admin/proposals", params={"status": "pending"}))
               if str(p.get("action", "")).startswith("payment_run")] if approver else []  # fmt: skip
    check(bool(approver) and len(pending) == 1, f"{name}: one payment run proposal for {approver}: {len(pending)}")
    if pending and approver:
        pid = int(pending[0]["id"])
        check(co.approve(pid, finance).status_code == 403, f"{name}: the proposer approved the payment run")
        done = co.approve(pid, approver)
        check(
            done.status_code == 200,
            f"{name}: {approver} could not approve the run: {done.status_code} {done.text[:200]}",
        )
        row = co.q("select approved_by, proposed_by from payment_run where no = :r", r=run_no)
        check(bool(row) and str(row[0]["approved_by"]) in (str(co.uid(approver)), co.people[approver]["code"]),
              f"{name}: the run was approved by {row[0]['approved_by'] if row else '-'}, not {approver}")  # fmt: skip

    # tax at source on the cutover invoices of the run
    ytd = {
        r_["supplier_code"]: D(r_["taxable"])
        for r_ in co.q("select supplier_code, taxable from cutover_supplier_ytd where fy = :f", f=FY)
    }
    applies = (
        next((s["value"] for s in master(co.dataset, "setting") if s["key"] == "tds_applicable_on_goods"), "false")
        == "true"
    )
    goods = _goods_suppliers(co)
    invs = {
        i["no"]: i
        for i in co.q(
            "select no, supplier_code, taxable, advance_taxable_used, tds_amount from invoice where no = any(:n)",
            n=list(in_run & cut),
        )
    }
    per_supplier: dict[str, Decimal] = {}
    for i in invs.values():
        per_supplier[i["supplier_code"]] = (
            per_supplier.get(i["supplier_code"], Decimal(0)) + D(i["taxable"]) - D(i.get("advance_taxable_used"))
        )
    checked, skipped, wrong_rule_differs = 0, 0, False
    for no, i in sorted(invs.items()):
        s = i["supplier_code"]
        base = D(i["taxable"]) - D(i.get("advance_taxable_used"))
        start = ytd.get(s, Decimal(0))
        if not applies or s not in goods:
            want: Decimal | None = Decimal(0) if not applies else None
        elif start >= THRESHOLD:
            want = tds_oracle(start, base)
        elif start + per_supplier[s] <= THRESHOLD:
            want = Decimal(0)
        else:
            want = None  # crosses the threshold inside the run: the order of booking decides; not judged
        if want is None:
            skipped += 1
            continue
        checked += 1
        check(
            abs(D(i["tds_amount"]) - want) <= Decimal("1.00"),
            f"{name}: tax at source on {no} ({s}) is {i['tds_amount']}, the oracle {want}",
        )
        if s == planted.get("supplier"):
            wrong_rule_differs = wrong_rule_differs or tds_oracle(Decimal(0), base) != want
    seen["tax at source: invoices judged / not judged"] = f"{checked} / {skipped}"
    if planted:
        got = [i for i in invs.values() if i["supplier_code"] == planted["supplier"]]
        check(bool(got) and all(D(i["tds_amount"]) > 0 for i in got),
              f"{name}: the supplier past the threshold in the old system pays no tax at source: {[(i['no'], i['tds_amount']) for i in got]}")  # fmt: skip
        m2 = "caught" if (got and wrong_rule_differs and all(D(i["tds_amount"]) > 0 for i in got)) else "NOT caught"
        seen["mutation: year to date ignored"] = m2
        if m2 != "caught":
            out.find("high", f"{name}: mutation 'year to date ignored' is not told apart by the run")
