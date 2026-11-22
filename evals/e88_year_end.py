"""E88: year end carries the open commitments (task 3.7, D-09, OP-5), written without the implementation.

Independence: written from the block 3 spec (sections 3.3 YE1, 4.1, 4.3, 4.6, 4.7, 9), the plan and the dataset only.
The oracle: the months of FY 2026-27 not locked by `master/budget_period_lock.csv` are the ones the close locks; the
carried commitments are A0's request values per GL account (block 2's store-check oracle x the estimate; the requests
have no cost centre and ship to Chakan), none held by orders; the check in FY 2027-28 reads only the new year's budget
rows from `budget.csv` (April 2027) and still counts last year's open requests.

Scenario (Apex Pumps, A0 only): on 31 March 2027 closing 2026-27 -> 422 (not ended). On 5 April 2027: closing 2027-28
-> 422, the buyer -> 403, Finance closes 2026-27 -> 200: `locked` = November 2026 to March 2027, `carried` and the total
equal the oracle and the spec (Rs 6,40,870 and 11,250; 6,52,120), audit `budget.year_close`; again -> 409; `GET
/api/budgets/years/2026-27` = the response; the periods all locked, `year_closed` true; the MRP request's check in FY
2027-28 against the oracle and `truth_budget.csv` (1410: 15,70,000 budget, 2,76,780 committed, 40.82%). The CLI `budget
close-year` on Brightwave by its finance person, then the tenant checks.

Mutations: open commitments dropped at 1 April (the old engine's created-this-year filter); unspent budget carried into
the new year.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import block1 as b
import block3 as b3
import env
import lib
from block1 import D, Z, js
from lib import Outcome

ID = "E88"
TITLE = "Year end locks the year, records the open commitments it carries, and the new year counts them"
REQUIREMENT = (
    "D-09, OP-5 (block 3 spec 3.3 YE1, 4.3, 4.6, 4.7): close allowed from 1 April by budget.manage; locks every month "
    "of the FY not yet locked; carried per GL, cost centre, location (held by requests, held by orders) and total; "
    "409 twice, 422 early or for a later FY; GET years/{fy} = the close; open commitments count in the new FY, unspent "
    "budget lapses; CLI close-year; audit budget.year_close; tenant isolation"
)
PREFIX = "e88"
FY = "2026-27"
NEXT = "2027-28"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:  # one story, told in order
    from fastapi.testclient import TestClient
    from p2p.main import app

    out = Outcome(True)
    check = b.Checker(out)
    lib.capture_mail()
    client = TestClient(app)
    apex = b3.Co3("apex", PREFIX, client)
    st = b3.start3(apex, check)
    bw = b3.Co3("brightwave", PREFIX, client)
    budgets = b3.Budgets(apex.dataset)
    reqs = {label: b3.request_no(apex, label) for label in ("MO-1", "MO-2", b3.MRP)}
    values = {label: b3.a0_values(st, label) for label in reqs}
    dept = b3.department_of(apex, reqs[b3.MRP]) or "PRD"
    fin = apex.finance()

    to_lock = [p for p in b3.fy_periods(FY) if p not in set(budgets.locked())]
    check(to_lock == ["2026-11", "2026-12", "2027-01", "2027-02", "2027-03"],
          f"oracle: the close should lock {to_lock}; the spec says Nov 2026 to Mar 2027 (self-check)", "medium")  # fmt: skip
    carried = {gl: sum((v.get(gl, Z) for v in values.values()), Z) for gl in ("1410", "1420")}
    check(carried == {"1410": Decimal(640870), "1420": Decimal(11250)},
          f"oracle: carried {carried}, the spec 6,40,870 and 11,250 (self-check)", "medium")  # fmt: skip

    with b3.day(date(2027, 3, 31)):
        r = apex.close_year(FY, fin)
        check(r.status_code == 422, f"closing {FY} on 31 March 2027 answered {r.status_code}, expected 422")
    with b3.day(b3.YEAR_END_DAY):
        r = apex.close_year(NEXT, fin)
        check(r.status_code == 422, f"closing {NEXT} on 5 April 2027 answered {r.status_code}, expected 422")
        r = apex.close_year("2026-2027", fin)
        check(r.status_code == 422, f"closing a badly written FY answered {r.status_code}, expected 422", "medium")
        r = apex.close_year(FY, apex.buyer())
        check(r.status_code == 403, f"the buyer closing the year answered {r.status_code}, expected 403")
        audit0 = apex.audit_count("budget.year_close")
        r = apex.close_year(FY, fin, note="eval: year end")
        body = js(r)
        check(r.status_code == 200, f"closing {FY} answered {r.status_code} {r.text[:300]}")
        check(sorted(str(p) for p in body.get("locked") or []) == to_lock,
              f"the close locked {body.get('locked')}, expected {to_lock}")  # fmt: skip
        _carried(check, body, carried, "the close")
        check(apex.audit_count("budget.year_close") == audit0 + 1, "the close: not one budget.year_close audit row")
        r2 = apex.close_year(FY, fin)
        check(r2.status_code == 409, f"closing {FY} again answered {r2.status_code}, expected 409")
        got = apex.call(fin, "GET", f"/api/budgets/years/{FY}")
        stored = js(got)
        check(got.status_code == 200, f"GET /api/budgets/years/{FY} answered {got.status_code}")
        _carried(check, stored, carried, "the stored close")
        check(sorted(str(p) for p in stored.get("locked") or body.get("locked") or []) == to_lock,
              "the stored close does not list the locked months", "medium")  # fmt: skip
        missing = apex.call(fin, "GET", f"/api/budgets/years/{NEXT}")
        check(missing.status_code == 404, f"GET /api/budgets/years/{NEXT} answered {missing.status_code}, expected 404")
        status, per = apex.periods(fin, FY)
        statuses = {str(p.get("period")): p.get("status") for p in b3.listed(per, "periods")}
        check(set(statuses.values()) == {"locked"} and per.get("year_closed") is True,
              f"after the close the FY's periods are {statuses}, year_closed {per.get('year_closed')}")  # fmt: skip
        rows = apex.q("select fy from budget_year_end") if "budget_year_end" in apex.tables() else []
        check([r[0] for r in rows] == [FY], f"budget_year_end holds {rows}", "medium")

        # the new year: the MRP request's check
        status, body = apex.check_of(reqs[b3.MRP], fin)
        check(status == 200, f"YE1: the MRP request's check answered {status}")
        got_groups = b3.groups_of(body)
        want: dict = {}
        for gl in ("1410", "1420"):
            want.update(b3.expect_groups(budgets, gl, "2027-04", dept,
                        hreq=values["MO-1"].get(gl, Z) + values["MO-2"].get(gl, Z), requested=values[b3.MRP].get(gl, Z)))  # fmt: skip
        check(str(body.get("fy")) == NEXT, f"the check on 5 April 2027 is for FY {body.get('fy')}, expected {NEXT}")
        b3.compare_groups(check, "YE1 FY 2027-28", got_groups, want)
        w = want[("1410", "department")]
        check((w.budget, w.committed, w.used_pct) == (Decimal(1570000), Decimal(276780), Decimal("40.82")),
              f"oracle YE1 1410 {w.as_dict()}, the spec 15,70,000 / 2,76,780 / 40.82 (self-check)", "medium")  # fmt: skip
        b3.compare_truth(check, "YE1", apex, "YE1", b3.YEAR_END_DAY, b3.MRP, got_groups, want)
        # mutations
        g = got_groups.get(("1410", "department"), {})
        dropped = Z
        lapsed_budget = budgets.total("1410", "2027-04", cost_centres=budgets.scope(dept, b3.CKN)) or Z
        unspent = (budgets.total("1410", "2027-03", cost_centres=budgets.scope(dept, b3.CKN)) or Z) - w.committed
        check(D(g.get("committed")) != dropped or w.committed == dropped,
              "YE1: the new year counts none of last year's open requests (mutation: dropped at 1 April)")  # fmt: skip
        check(D(g.get("budget")) != lapsed_budget + unspent or unspent == 0,
              "YE1: the new year's budget includes last year's unspent budget (mutation: carried)")  # fmt: skip

    # the CLI on the other company (its own finance person), then the tenant checks
    with b3.day(b3.YEAR_END_DAY):
        code, said = b.cli("budget", "close-year", bw.slug, FY, "--as", bw.buyer())
        check(code != 0, f"CLI budget close-year as the buyer (no budget.manage) exited {code}", "medium")
        code, said = b.cli("budget", "close-year", bw.slug, FY, "--as", bw.finance(), "--note", "eval: CLI")
        check(code == 0, f"CLI budget close-year on Brightwave: exit {code}: {said[:300]}", "medium")
        _, theirs = bw.periods(bw.finance(), FY)
        check(theirs.get("year_closed") is True, f"Brightwave's FY after the CLI close: {str(theirs)[:200]}", "medium")
    b3.tenant_check3(check, apex, bw)
    b3.tenant_check3(check, bw, apex)
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes.append(
        "Spec gap: `carried` rows name cost_centre_code null for A0's department-level lines; the spec's example "
        "keys rows by (gl_code, cost_centre_code, location_code) and the eval matches on that key."
    )
    return out


def _carried(check: b.Checker, body: dict[str, Any], carried: dict[str, Decimal], label: str) -> None:
    rows = b3.listed(body, "carried")
    got = {}
    for r in rows:
        key = (str(r.get("gl_code")), r.get("cost_centre_code") or None, str(r.get("location_code")))
        got[key] = (D(r.get("held_by_requests")), D(r.get("held_by_orders")))
    want = {(gl, None, b3.CKN): (v, Z) for gl, v in carried.items() if v}
    check(got == want, f"{label}: carried {got}, expected {want}")
    total = sum(carried.values(), Z)
    t = body.get("carried_total")
    t = t.get("total") if isinstance(t, dict) else t
    check(b3.dec(t) == total or (isinstance(body.get("totals"), dict) and b3.dec(body["totals"].get("total")) == total),
          f"{label}: carried_total {body.get('carried_total')}, expected {total}")  # fmt: skip
