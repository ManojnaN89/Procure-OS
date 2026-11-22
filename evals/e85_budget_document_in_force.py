"""E85: the budget counts the document in force (task 3.7, FR27), written without the implementation.

Independence: written from the block 3 spec (sections 3.3, 4.1, 4.5, 4.6, 9), the plan and the dataset only. The oracle
(`block3.Budgets`) reads `master/budget.csv` and `cost_centre.csv` itself: the budget of a GL account from April to the
check month for the department's cost centres at the plant, and for the plant. The request values of A0 come from block
2's own store-check oracle (requested quantity x the estimate); the order values from the order lines the eval itself
made (quantity x the agreed price), never from the platform's sums.

Scenarios (Apex Pumps, as of Monday 23 November 2026):
- BG0: `GET /api/budgets/check/{the MRP request}`: per group budget, held by requests (MO-1 + MO-2), held by orders 0,
  spent 0, committed, requested, used %, status, against the oracle and `truth/truth_budget.csv`. Shape, 404 and 403.
- BG1: MO-1 approved, sourced, RM-08 bought directly from SUP-005 at Rs 880, orders made: MO-2's 1410 check holds the
  MRP request at its estimate and MO-1's three ordered lines at their order prices (Rs 4,34,070), never MO-1 at its
  estimate (Rs 4,35,670, the old engine) and never both (Rs 5,05,650). MO-1's own check with final prices. The variance
  (`left = budget - the three parts`, location rows). Then the RM-08 order is cancelled: its line is back at its
  estimate.
Ends with a tenant check against Brightwave.

Mutations: closed or ordered requests still counted at estimate (the old engine); order lines counted on top of their
request lines.
"""

from decimal import Decimal
from typing import Any

import block1 as b
import block3 as b3
import env
import lib
from block1 import D, Z
from lib import Outcome

ID = "E85"
TITLE = "The budget counts each request line once: at its estimate until ordered, then at the order's price"
REQUIREMENT = (
    "Task 3.7, FR27 (block 3 spec 3.3 BG0/BG1, 4.1, 4.5, 4.6): used = held by requests (open or sourcing lines of "
    "submitted or approved requests) + held by orders (open order balance before GST, less actuals) + spent; the request "
    "checked excluded; final prices; variance left = budget - the three parts, per location too; a cancelled order puts "
    "the estimate back; GET /api/budgets/check fields, 404, 403; tenant isolation"
)
PREFIX = "e85"
GROUP_KEYS = {"request_no", "as_of", "fy", "groups", "summary", "blocked"}
PART_KEYS = {"held_by_requests", "held_by_orders", "spent", "committed"}


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:
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
    gls = b3.gl_of_items(apex.dataset)
    check(any(r["period"] >= "2027-04" for r in budgets.rows), "budget.csv has no FY 2027-28 rows (spec 3.2 step 1)",
          "medium")  # fmt: skip

    reqs = {label: b3.request_no(apex, label) for label in ("MO-1", "MO-2", b3.MRP)}
    if not check(all(reqs.values()), f"A0 does not have the three requests: {reqs}"):
        return out
    values = {label: b3.a0_values(st, label) for label in reqs}
    dept = b3.department_of(apex, reqs[b3.MRP])
    check(dept == "PRD", f"the MRP request is department {dept!r}, the spec's PRD", "low")
    out.metrics["A0 values by GL (oracle)"] = {k: {g: str(v) for g, v in d.items()} for k, d in values.items()}

    _bg0(check, apex, budgets, reqs, values, dept or "PRD")
    lines = b3.bg1(apex, check, st)
    _bg1(out, check, apex, budgets, reqs, values, dept or "PRD", lines, gls)
    b3.tenant_check3(check, apex, bw)
    b3.tenant_check3(check, bw, apex)

    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes.append(
        "Spec gap: the variance rows per cost centre for request lines with no cost centre (A0's lines are the "
        "department's) are not described; only `left = budget - the three parts` on every row and the location rows' "
        "parts are checked."
    )
    out.notes.append(
        "Spec gap: whether a request line returns to `open` or `sourcing` after its order is cancelled is not said; "
        "either status counts it at its estimate again (4.1), which is what is checked."
    )
    return out


def _bg0(check: b.Checker, co: b3.Co3, budgets: b3.Budgets, reqs: dict[str, str], values: dict, dept: str) -> None:
    mrp = reqs[b3.MRP]
    with b3.day(b3.AS_OF):
        status, body = co.check_of(mrp)
        check(status == 200, f"BG0: GET /api/budgets/check/{mrp} answered {status}: {str(body)[:200]}")
        check(GROUP_KEYS <= set(body), f"BG0: the check has keys {sorted(body)}, the spec {sorted(GROUP_KEYS)}",
              "medium")  # fmt: skip
        check(str(body.get("fy")) == "2026-27", f"BG0: fy {body.get('fy')}, expected 2026-27", "medium")
        for g in b3.listed(body, "groups"):
            check(PART_KEYS <= set(g), f"BG0: a group lacks {sorted(PART_KEYS - set(g))}")
        got = b3.groups_of(body)
        want: dict = {}
        for gl in ("1410", "1420"):
            want.update(b3.expect_groups(budgets, gl, "2026-11", dept,
                        hreq=values["MO-1"].get(gl, Z) + values["MO-2"].get(gl, Z),
                        requested=values[b3.MRP].get(gl, Z)))  # fmt: skip
        b3.compare_groups(check, "BG0", got, want)
        # the spec's own numbers (3.3) as a self-check of the oracle
        w = want[("1410", "department")]
        check((w.budget, w.committed, w.requested, w.used_pct) ==
              (Decimal(11600000), Decimal(276780), Decimal(364090), Decimal("5.52")),
              f"BG0 oracle: 1410 {w.as_dict()} differs from the spec's 1,16,00,000 / 2,76,780 / 3,64,090 / 5.52",
              "medium")  # fmt: skip
        n = b3.compare_truth(check, "BG0", co, "BG0", b3.AS_OF, b3.MRP, got, want)
        check(n > 0, "BG0: truth/truth_budget.csv has no rows for BG0 (spec 3.5)", "medium")
        status404, _ = co.check_of("REQ-NOT-THERE")
        check(status404 == 404, f"BG0: an unknown request answered {status404}, expected 404")
        refused, _ = co.check_of(mrp, by=co.buyer())
        check(refused == 403, f"BG0: the buyer (no budget.view) checking the budget answered {refused}, expected 403")


def _bg1(out: Outcome, check: b.Checker, co: b3.Co3, budgets: b3.Budgets, reqs: dict[str, str], values: dict, dept: str,
         lines: dict[str, dict[str, Any]], gls: dict[str, str]) -> None:  # fmt: skip
    mo1, mo2 = reqs["MO-1"], reqs["MO-2"]
    # MO-1's lines that were not ordered still count at their estimate (PK-01 here); recomputed from the oracle's values
    left_mo1 = {gl: values["MO-1"].get(gl, Z) - sum((b3.r2(D(ln["qty"]) * _est(co, item)) for item, ln in lines.items()
                                                     if gls.get(item) == gl), Z) for gl in ("1410", "1420")}  # fmt: skip
    with b3.day(b3.AS_OF):
        status, body = co.check_of(mo2)
        check(status == 200, f"BG1: GET /api/budgets/check/{mo2} answered {status}")
        got = b3.groups_of(body)
        want: dict = {}
        for gl in ("1410", "1420"):
            want.update(b3.expect_groups(budgets, gl, "2026-11", dept,
                        hreq=values[b3.MRP].get(gl, Z) + max(left_mo1[gl], Z),
                        hord=b3.held_by(lines, gls, gl), requested=values["MO-2"].get(gl, Z)))  # fmt: skip
        b3.compare_groups(check, "BG1 MO-2", got, want)
        w = want[("1410", "department")]
        setup_ok = all(i in lines and D(lines[i]["price"]) == p for i, p in b3.BG1_PRICES.items())
        check(not setup_ok or w.committed == Decimal(434070),
              f"BG1 oracle: 1410 committed {w.committed}, the spec's 4,34,070", "medium")  # fmt: skip
        g = got.get(("1410", "department"), {})
        out.metrics["BG1 1410 committed (platform / oracle)"] = f"{g.get('committed')} / {w.committed}"
        # mutations: the old engine (MO-1 at its estimate, the order ignored) and both counted
        old = values[b3.MRP]["1410"] + values["MO-1"]["1410"]
        both = old + b3.held_by(lines, gls, "1410")
        check(b3.D(g.get("committed")) not in (old, both) or old == w.committed,
              f"BG1: 1410 committed {g.get('committed')} is the wrong rule's ({old} old engine, {both} both)")  # fmt: skip
        check(old != w.committed and both != w.committed, "BG1: the case does not tell the old engine from the spec "
              "(mutation check)", "medium")  # fmt: skip
        b3.compare_truth(check, "BG1", co, "BG1", b3.AS_OF, "MO-2", got, want)

        # MO-1's own check with final prices: its ordered lines at the order price, its own orders not held twice
        status, body = co.check_of(mo1, final_prices=True)
        own = b3.groups_of(body).get(("1410", "department"), {})
        want_own = b3.expect_groups(budgets, "1410", "2026-11", dept,
                                    hreq=values["MO-2"]["1410"] + values[b3.MRP]["1410"] + max(left_mo1["1410"], Z),
                                    requested=b3.held_by(lines, gls, "1410"))[("1410", "department")]  # fmt: skip
        wrong = b3.group_diff(own, want_own.as_dict())
        check(status == 200 and not wrong, f"BG1: MO-1 with final prices: {status} {'; '.join(wrong)}")

        _variance(check, co, budgets, values, lines, gls, left_mo1, "BG1 variance")

        # the RM-08 order cancelled: its line is back at its estimate
        wire = lines.get("RM-08")
        if not check(wire is not None, "BG1: no live RM-08 order to cancel", "medium"):
            return
        assert wire is not None
        r = co.call(co.buyer(), "POST", f"/api/purchase-orders/{wire['po_no']}/cancel",
                    {"reason": "eval: supplier cannot deliver", "reorder": True})  # fmt: skip
        # `reorder`: the platform's cancel keeps the need (the line goes back to the buyer); without it the need is
        # cancelled with the order (step 7). Spec 8's case is the former.
        check(r.status_code == 200, f"BG1: cancelling {wire['po_no']} answered {r.status_code} {r.text[:200]}")
        after = co.ordered_lines(mo1)
        check("RM-08" not in after, f"BG1: the RM-08 order line is still live after the cancel: {after.get('RM-08')}")
        back = b3.r2(D(wire["qty"]) * _est(co, "RM-08"))
        line_status = co.one("select status from request_line where request_no = :n and item_code = 'RM-08'", n=mo1)
        check(line_status in ("open", "sourcing"), f"BG1: MO-1's RM-08 line is {line_status} after its order was "
              "cancelled (spec 8: 'a PO cancel returns the line to its estimate')")  # fmt: skip
        status, body = co.check_of(mo2)
        got = b3.groups_of(body)
        want_c = b3.expect_groups(budgets, "1410", "2026-11", dept,
                                  hreq=values[b3.MRP]["1410"] + max(left_mo1["1410"], Z) + back,
                                  hord=b3.held_by(after, gls, "1410"), requested=values["MO-2"]["1410"])  # fmt: skip
        b3.compare_groups(check, "BG1 after the RM-08 order was cancelled", got, want_c)


def _variance(check: b.Checker, co: b3.Co3, budgets: b3.Budgets, values: dict, lines: dict, gls: dict, left_mo1: dict,
              label: str) -> None:  # fmt: skip
    v = co.variance()
    rows = b3.listed(v, "rows")
    check(bool(rows), f"{label}: GET /api/budgets/variance has no rows: {str(v)[:200]}")
    for r in rows + b3.listed(v, "locations"):
        if not PART_KEYS - {"committed"} <= set(r):
            check(False, f"{label}: a row lacks held_by_requests / held_by_orders / spent: {sorted(r)}")
            continue
        left = D(r.get("budget")) - D(r["held_by_requests"]) - D(r["held_by_orders"]) - D(r["spent"])
        check(D(r.get("left")) == left, f"{label}: row {r.get('cost_centre') or r.get('location') or r.get('location_code')} "
              f"{r.get('gl_code')}: left {r.get('left')}, expected budget - the three parts = {left}")  # fmt: skip
    locs = {(str(r.get("location") or r.get("location_code")), str(r.get("gl_code"))): r
            for r in b3.listed(v, "locations")}  # fmt: skip
    for gl in ("1410", "1420"):
        r = locs.get((b3.CKN, gl))
        hreq = sum((values[m].get(gl, Z) for m in ("MO-2", b3.MRP)), Z) + max(left_mo1[gl], Z)
        want = (budgets.total(gl, "2026-11", location=b3.CKN), hreq, b3.held_by(lines, gls, gl), Z)
        got = None if r is None else (b3.dec(r.get("budget")), D(r.get("held_by_requests")), D(r.get("held_by_orders")),
                                      D(r.get("spent")))  # fmt: skip
        check(got == want, f"{label}: location {b3.CKN} {gl}: (budget, by requests, by orders, spent) {got}, "
              f"expected {want}")  # fmt: skip


def _est(co: b3.Co3, item: str) -> Decimal:
    """The estimate of A0's lines: contract, else standard price (the same number on the demo datasets, block 1 oracle)."""
    return b.Plant.price(_plant(co), item)


_PLANTS: dict[str, b.Plant] = {}


def _plant(co: b3.Co3) -> b.Plant:
    if co.slug not in _PLANTS:
        _PLANTS[co.slug] = b.Plant(co.dataset)
    return _PLANTS[co.slug]
