"""E87: budget period locks (task 3.7, D-09), written without the implementation.

Independence: written from the block 3 spec (sections 3.2, 3.3 PL1, 4.3, 4.4, 4.6, 4.7, 9), the plan and the dataset
only. The oracle reads `master/budget_period_lock.csv` (the months the dataset locks), works out each month's status
(locked, open = ended and not locked, future = not ended) and the month that may be locked next (the earliest open one,
when every earlier month of its financial year is locked), and moves a late posting to the first open month after its
event month.

Scenario (Apex Pumps): on 23 November 2026 the periods of FY 2026-27 against the oracle (April to October locked by
the dataset, November not ended). A service (SV-01) is ordered and confirmed; its invoice is received on 28 November.
On 7 December (Finance, U05): lock 2026-11 -> 200 (fields, audit `budget.period_lock`), 2026-12 -> 422 (not ended),
2026-11 again -> 409, the buyer -> 403, no reason -> 422; the CLI `budget periods` lists the same. The invoice is paid
that day: its row has `period` 2026-12, `event_period` 2026-11, audit `budget.actual_moved`. An adjustment into 2026-11
-> 422 "2026-11 is locked". A masters import of a changed budget row for 2026-11 is left out ("period 2026-11 is
locked") and the budget stays. No row posted after the lock has a locked period. A scratch company with no locks: lock
2026-05 before 2026-04 -> 409 "lock 2026-04 first"; then 2026-04, then 2026-05. Ends with a tenant check.

Mutations: posting into a locked month; locking out of order.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import block1 as b
import block3 as b3
import env
import lib
from block1 import D, js
from lib import Outcome

ID = "E87"
# Block 13 (spec 10.1): the lock keeps the month's period_close snapshot and final export (R7.3).
TITLE = "A locked budget month is frozen: nothing posts into it, it is locked in order, and late postings move on"
REQUIREMENT = (
    "D-09 (block 3 spec 3.3 PL1, 4.3, 4.4, 4.6, 4.7): lock only ended months, in order, once, with a reason, by "
    "budget.manage; a late actual goes to the first open month keeping event_period (audit budget.actual_moved); "
    "adjustments and imported budget rows for a locked month are refused; periods API and CLI; tenant isolation"
)
PREFIX = "e87"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def statuses(locked: set[str], fy: str, today: date) -> dict[str, str]:
    current = b3.month(today)
    return {p: "locked" if p in locked else ("open" if p < current else "future") for p in b3.fy_periods(fy)}


def lockable(locked: set[str], fy: str, today: date) -> str | None:
    """The month the oracle may lock next in this FY: the earliest ended unlocked month, all earlier ones locked."""
    for p in b3.fy_periods(fy):
        if p in locked:
            continue
        return p if p < b3.month(today) else None
    return None


def post_period(event: str, locked: set[str], current: str, wrong: bool = False) -> str:
    """Spec 4.4: the event month if not locked, else the first month after it not locked (never after the current)."""
    if wrong or event not in locked:
        return event
    p = event
    while p in locked and p < current:
        p = b3.shift(p, 1)
    return p


def _run(ctx: Any) -> Outcome:  # one story, told in order
    from fastapi.testclient import TestClient
    from p2p.main import app

    out = Outcome(True)
    check = b.Checker(out)
    lib.capture_mail()
    client = TestClient(app)
    apex = b3.Co3("apex", PREFIX, client)
    bw = b3.Co3("brightwave", PREFIX, client)
    budgets = b3.Budgets(apex.dataset)
    locked = set(budgets.locked())
    want_locks = {b3.shift("2025-11", i) for i in range(12)}
    check(locked == want_locks, f"budget_period_lock.csv locks {sorted(locked)}, the spec 2025-11 to 2026-10 (3.2)",
          "medium")  # fmt: skip
    lock_rows = budgets.locks
    check(all(r.get("reason") == "Month-end close" and r.get("locked_by") == "U05" for r in lock_rows),
          "budget_period_lock.csv: not every lock is U05's 'Month-end close' (spec 3.2 step 2)", "low")  # fmt: skip
    fin, buyer = apex.finance(), apex.buyer()

    # ---- the periods on 23 November ----
    with b3.day(b3.AS_OF):
        _periods(check, apex, locked, "2026-27", b3.AS_OF, "23 Nov")
        code, said = b.cli("budget", "periods", apex.slug, "--fy", "2026-27")
        check(code == 0 and "2026-10" in said and "2026-11" in said, f"CLI budget periods: exit {code}: {said[:300]}",
              "medium")  # fmt: skip
        status, _ = apex.periods(fin, "2026-2027")
        check(status == 422, f"periods?fy=2026-2027 answered {status}, expected 422 (bad fy)", "medium")
    svc = apex.service_order(check, "PL1 SV-01", b3.AS_OF)
    inv = None
    if svc:
        _, po_svc, ln_svc = svc
        price = D(next(r for r in apex.rows("master", "item") if r["code"] == "SV-01")["standard_price"])
        inv, said = apex.invoice(po_svc, [(ln_svc, Decimal(1), price)], b3.LATE_INVOICE_DAY, "EV87-S1")
        check(inv is not None and said.get("status") == "approved", f"PL1 invoice of 28 Nov: {said}", "medium")
        rec_on = str(apex.one("select received_on from invoice where no = :n", n=inv) or "")
        check(rec_on.startswith("2026-11"), f"PL1: the invoice was received on {rec_on}, not in November (setup)", "medium")

    # ---- 7 December: the locks ----
    with b3.day(b3.LOCK_DAY):
        _periods(check, apex, locked, "2026-27", b3.LOCK_DAY, "7 Dec, before")
        audit0 = apex.audit_count("budget.period_lock")
        r = apex.lock("2026-11", buyer)
        check(r.status_code == 403, f"the buyer locking 2026-11 answered {r.status_code}, expected 403")
        r = apex.lock("2026-11", fin, reason=None)
        check(r.status_code == 422, f"locking without a reason answered {r.status_code}, expected 422")
        r = apex.lock("2026-13", fin)
        check(r.status_code == 422, f"locking 2026-13 answered {r.status_code}, expected 422", "medium")
        r = apex.lock("2026-12", fin)
        check(r.status_code == 422, f"locking 2026-12 (not ended) answered {r.status_code}, expected 422")
        check(apex.audit_count("budget.period_lock") == audit0, "a refused lock wrote an audit row")
        r = apex.lock("2026-11", fin, reason="Month-end close")
        body = js(r)
        check(r.status_code == 200 and body.get("period") == "2026-11" and body.get("status") == "locked"
              and body.get("locked_by") not in (None, "") and body.get("locked_at"),
              f"locking 2026-11 answered {r.status_code} {str(body)[:200]}")  # fmt: skip
        check(apex.code_of(body.get("locked_by")) in (fin, None) or body.get("locked_by") == fin,
              f"locked_by {body.get('locked_by')}, expected {fin}", "medium")  # fmt: skip
        check(apex.audit_count("budget.period_lock") == audit0 + 1, "locking 2026-11: not one budget.period_lock audit row")
        # block 13 R7.3: the lock also keeps the month's snapshot and its final export
        check([r[0] for r in apex.q("select period from period_close")] == ["2026-11"],
              f"period_close holds {apex.q('select period from period_close')}, expected 2026-11")
        check(bool(apex.q("select checklist, grni, grni_total from period_close where period = '2026-11'")),
              "period_close has no checklist or frozen GRNI")
        check(len(apex.q("select id from accounting_export where period = '2026-11'")) == 1,
              "the month close kept no final accounting export")
        r = apex.lock("2026-11", fin)
        check(r.status_code == 409, f"locking 2026-11 again answered {r.status_code}, expected 409")
        locked.add("2026-11")
        _periods(check, apex, locked, "2026-27", b3.LOCK_DAY, "7 Dec, after")
        last_id = max([int(x["id"]) for x in apex.actuals()] or [0])

    # ---- the late payment ----
    moved0 = apex.audit_count("budget.actual_moved")
    if inv:
        run_no = apex.pay(b3.LOCK_DAY)
        check(":" not in run_no and not run_no.startswith("no run"), f"PL1 payment run: {run_no}", "medium")
        rows = [x for x in apex.actuals() if x.get("invoice_no") == inv]
        want = post_period("2026-11", locked, "2026-12")
        wrong = post_period("2026-11", locked, "2026-12", wrong=True)
        check(want != wrong, "PL1 cannot tell posting into a locked month from moving it (mutation check)", "medium")
        check(len(rows) == 1 and rows[0].get("period") == want and rows[0].get("event_period") == "2026-11",
              f"PL1: the late invoice posted {rows}; expected period {want}, event_period 2026-11")  # fmt: skip
        check(apex.audit_count("budget.actual_moved") == moved0 + 1, "PL1: no budget.actual_moved audit row")
    after_lock = [x for x in apex.actuals() if int(x["id"]) > last_id]
    bad = [x for x in after_lock if x.get("period") in locked]
    check(not bad, f"rows posted after the lock carry a locked period: {bad}")

    # ---- adjustments and imports into a locked month ----
    with b3.day(b3.LOCK_DAY):
        r = apex.adjust({"kind": "supplement", "gl_code": "5240", "cost_centre_code": "CC-MNT", "period": "2026-11",
                         "amount": "1000", "reason": "eval: into a locked month"}, fin)  # fmt: skip
        check(r.status_code == 422 and "2026-11" in r.text and "locked" in r.text.lower(),
              f"an adjustment into 2026-11 answered {r.status_code} {r.text[:200]}, expected 422 '2026-11 is locked'")  # fmt: skip
        _import(check, apex)
    b3.tenant_check3(check, apex, bw)
    _scratch(check, apex)
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes.append(
        "Spec gap: 4.3 calls a month 'future' until it has ended, so the current month (2026-12 on 7 December) is future; "
        "the spec still lets a payment of 7 December post into it and E89 adjusts it. The eval reads 'open' as 'ended "
        "and not locked' for the periods list only."
    )
    out.notes.append(
        "Spec gap: the CLI's output format is not given; `budget periods` is checked for its exit code and that it names "
        "the months."
    )
    return out


def _periods(check: b.Checker, co: b3.Co3, locked: set[str], fy: str, today: date, label: str) -> None:
    status, body = co.periods(co.finance(), fy)
    check(status == 200 and str(body.get("fy")) == fy, f"{label}: periods?fy={fy} answered {status} {str(body)[:200]}")
    got = {str(p.get("period")): str(p.get("status")) for p in b3.listed(body, "periods")}
    want = statuses(locked, fy, today)
    check(got == want, f"{label}: period statuses {got}, expected {want}")
    nxt = lockable(locked, fy, today)
    can = body.get("can_lock")
    can = can.get("period") if isinstance(can, dict) else can
    check((can or None) == nxt or (can is True and nxt is not None) or (can is False and nxt is None),
          f"{label}: can_lock {body.get('can_lock')}, expected {nxt}", "medium")  # fmt: skip
    check(body.get("year_closed") in (False, None), f"{label}: year_closed {body.get('year_closed')}", "medium")
    for p in b3.listed(body, "periods"):
        if p.get("status") == "locked" and p.get("period") in locked:
            check(bool(p.get("locked_by")) and bool(p.get("locked_at")) and bool(p.get("reason")),
                  f"{label}: locked {p.get('period')} lacks who, when or why: {p}", "medium")  # fmt: skip


def _import(check: b.Checker, co: b3.Co3) -> None:
    """A masters import of a changed budget row for 2026-11 is left out with the reason (spec 4.3, OP-9)."""
    folder = b3.copy_dataset(co)
    path = folder / "master" / "budget.csv"
    rows = b.read_csv(path)
    hit = [r for r in rows if r["cost_centre_code"] == "CC-MNT" and r["gl_code"] == "5240" and r["period"] == "2026-11"]
    if not check(bool(hit), "budget.csv has no CC-MNT 5240 row for 2026-11 to change", "medium"):
        return
    before = D(hit[0]["amount"])
    hit[0]["amount"] = f"{before + 10000:.2f}"
    header = list(rows[0])
    path.write_bytes(b.csv_bytes(header, ([r.get(h, "") for h in header] for r in rows)))
    code, said = b.cli("import", "masters", co.slug, str(folder))
    check("period 2026-11 is locked" in said, f"the import of a 2026-11 budget row did not say 'period 2026-11 is "
          f"locked': exit {code}: {said[:400]}")  # fmt: skip
    now = co.q("select amount from budget where cost_centre_code = 'CC-MNT' and gl_code = '5240' and period = '2026-11'")
    check([D(x[0]) for x in now] == [before], f"after the import CC-MNT 5240 2026-11 is {now}, expected {before}")


def _scratch(check: b.Checker, apex: b3.Co3) -> None:
    """Order of locking on a company with no locks (built from the dataset without `budget_period_lock.csv`)."""
    from fastapi.testclient import TestClient
    from p2p.main import app

    folder = b3.copy_dataset(apex, drop=["master/budget_period_lock.csv"])
    world = lib.build_world(PREFIX + "s", dataset=folder)
    client = TestClient(app)
    fin = world.headers("Finance")

    def lock(p: str) -> Any:
        return client.post(f"/api/budgets/periods/{p}/lock", json={"reason": "eval: order"}, headers=fin)

    with b3.day(b3.AS_OF):
        r = lock("2026-05")
        wrong_first = statuses(set(), "2026-27", b3.AS_OF)["2026-05"] == "open"
        check(r.status_code == 409 and "lock 2026-04 first" in r.text,
              f"scratch: locking 2026-05 before 2026-04 answered {r.status_code} {r.text[:200]}, expected 409 'lock "
              "2026-04 first'")  # fmt: skip
        check(wrong_first and lockable(set(), "2026-27", b3.AS_OF) == "2026-04",
              "scratch: the case cannot tell locking out of order (mutation check)", "medium")  # fmt: skip
        r = lock("2026-04")
        check(r.status_code == 200, f"scratch: locking 2026-04 (first of the FY) answered {r.status_code} {r.text[:200]}")
        r = lock("2026-05")
        check(r.status_code == 200, f"scratch: then 2026-05 answered {r.status_code} {r.text[:200]}")
    world.db.close()
