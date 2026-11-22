"""E89: budget adjustments need a second person (task 3.7, D-09, OP-8), written without the implementation.

Independence: written from the block 3 spec (sections 4.3, 4.6, 9, 10), the plan and the dataset only. The oracle
(`block3.Budgets`) reads `master/budget.csv` and adds the approved adjustments itself: the budget of CC-MNT 5240 from
April to December 2026 before and after.

Scenario (Apex Pumps, 7 December 2026): setup gives the Director (U09) the Finance role through the setup API (one
administrator asks, the other approves). Finance (U05) proposes a supplement of Rs 50,000 to CC-MNT 5240 for 2026-12:
201 pending, no budget row yet; U05 approving it -> 403, the buyer -> 403, Brightwave's director cannot approve it and
its finance person does not list it; U09 approves: one `budget` row of kind adjustment, +50,000.00, with the reason and
the proposal id; the CC-MNT 5240 budget in the variance and in a request's check rises by exactly 50,000; audit
`budget.adjust`. A transfer of Rs 10,000 from CC-MNT 5240 to CC-MNT 1430 makes two rows that sum to 0. Refusals (422):
amount 0 or below, no reason, both or neither holder, unknown GL, a locked month. Ends with a tenant check.

Mutations: the proposer approves their own adjustment; the adjustment written before approval.
"""

from decimal import Decimal
from typing import Any

import block1 as b
import block3 as b3
import env
import lib
from block1 import D, Z, js
from lib import Outcome

ID = "E89"
TITLE = "Budget supplements, returns and transfers are proposals another finance person approves"
REQUIREMENT = (
    "D-09, OP-8 (block 3 spec 4.3, 4.6): POST /api/budgets/adjustments by budget.manage -> 201 pending proposal; the "
    "proposer never approves; on approval `budget` rows of kind adjustment with reason and proposal id (a transfer two "
    "rows summing 0); open periods only; 422 for bad amounts, holders, GL, reason; the check and the variance read the "
    "new budget; audit budget.adjust; tenant isolation"
)
PREFIX = "e89"
PERIOD = "2026-12"


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
    bw = b3.Co3("brightwave", PREFIX, client)
    budgets = b3.Budgets(apex.dataset)
    fin, second, buyer = apex.finance(), apex.director(), apex.buyer()
    has_kind = "kind" in apex.columns("budget")
    check(has_kind and {"reason", "proposal_id"} <= apex.columns("budget"),
          f"`budget` lacks kind / reason / proposal_id: {sorted(apex.columns('budget'))} (spec 6.1)")  # fmt: skip

    with b3.day(b3.LOCK_DAY):
        # setup: a second holder of budget.manage
        asked, done = apex.d22(f"/api/setup/people/{apex.user_id(second)}/roles", {"role": "Finance"}, method="POST")
        check(done is not None and done.status_code == 200, f"setup: giving {second} the Finance role: "
              f"{asked.status_code} {asked.text[:150]} / {getattr(done, 'status_code', None)}", "medium")  # fmt: skip
        apex.relogin(second)
        req = _request(check, apex)

        before_var = _var(apex, "CC-MNT", "5240")
        before_chk = _chk(apex, req)
        rows0 = _adjust_rows(apex)
        audit0 = apex.audit_count("budget.adjust")
        body = {"kind": "supplement", "gl_code": "5240", "cost_centre_code": "CC-MNT", "period": PERIOD,
                "amount": "50000", "reason": "Calibration of the second test bench brought forward"}  # fmt: skip
        r = apex.adjust(body, fin)
        pid = js(r).get("proposal_id")
        check(r.status_code == 201 and pid and js(r).get("status") == "pending",
              f"the supplement answered {r.status_code} {r.text[:200]}, expected 201 pending")  # fmt: skip
        check(_adjust_rows(apex) == rows0, "a budget row was written before the adjustment was approved")
        if pid:
            prop = apex.q("select action, status, required_permission from proposal where id = :i", i=pid)
            check(bool(prop) and str(prop[0][0]).startswith("budget.adjust") and prop[0][1] == "pending",
                  f"proposal {pid}: {prop}", "medium")  # fmt: skip
            own = apex.call(fin, "POST", f"/api/admin/proposals/{pid}/approve", {})
            check(own.status_code == 403, f"the proposer approving their own adjustment answered {own.status_code}")
            no = apex.call(buyer, "POST", f"/api/admin/proposals/{pid}/approve", {})
            check(no.status_code in (403, 404), f"the buyer approving the adjustment answered {no.status_code}")
            other = bw.call(bw.director(), "POST", f"/api/admin/proposals/{pid}/approve", {})
            theirs = bw.q("select id from proposal where id = :i", i=pid)
            check(other.status_code in (403, 404) or bool(theirs),
                  f"Brightwave's director approving Apex's adjustment answered {other.status_code}")  # fmt: skip
            listed = b3.listed(js(bw.call(bw.finance(), "GET", "/api/admin/proposals?status=pending")), "proposals")
            check(not any(p.get("id") == pid and str(p.get("action", "")).startswith("budget") for p in listed)
                  or bool(theirs), "Brightwave lists Apex's budget adjustment")  # fmt: skip
            check(_adjust_rows(apex) == rows0, "refused approvals wrote budget rows")
            ok = apex.call(second, "POST", f"/api/admin/proposals/{pid}/approve", {})
            check(ok.status_code == 200, f"{second} approving the adjustment answered {ok.status_code} {ok.text[:200]}")
            new = _adjust_rows(apex)[len(rows0):]
            check(len(new) == 1 and new[0][:4] == ("CC-MNT", "5240", PERIOD, Decimal("50000.00"))
                  and new[0][4] == body["reason"] and str(new[0][5]) == str(pid),
                  f"after approval the adjustment rows are {new}; expected one CC-MNT 5240 {PERIOD} +50,000.00 with the "
                  f"reason and proposal {pid}")  # fmt: skip
            check(apex.audit_count("budget.adjust") == audit0 + 1, "no budget.adjust audit row", "medium")
            budgets.extra[("CC-MNT", "5240", "", PERIOD)] += Decimal(50000)
        after_var = _var(apex, "CC-MNT", "5240")
        want_budget = budgets.total("5240", PERIOD, cost_centres=["CC-MNT"])
        check(after_var is not None and D(after_var.get("budget")) == want_budget,
              f"variance CC-MNT 5240 budget {None if after_var is None else after_var.get('budget')}, expected "
              f"{want_budget} (oracle with the adjustment)")  # fmt: skip
        if before_var is not None and after_var is not None:
            check(D(after_var["budget"]) - D(before_var["budget"]) == Decimal(50000), "the variance budget did not rise "
                  "by exactly 50,000")  # fmt: skip
        after_chk = _chk(apex, req)
        if before_chk is not None and after_chk is not None:
            check(after_chk - before_chk == Decimal(50000), f"the request's 5240 budget went {before_chk} -> "
                  f"{after_chk}, expected +50,000")  # fmt: skip

        # a transfer: two rows that sum to 0
        rows1 = _adjust_rows(apex)
        r = apex.adjust({"kind": "transfer", "gl_code": "5240", "cost_centre_code": "CC-MNT", "period": PERIOD,
                         "amount": "10000", "to": {"gl_code": "1430", "cost_centre_code": "CC-MNT"},
                         "reason": "Spares overspend covered from calibration"}, fin)  # fmt: skip
        tid = js(r).get("proposal_id")
        check(r.status_code == 201 and bool(tid), f"the transfer answered {r.status_code} {r.text[:200]}")
        if tid:
            apex.call(second, "POST", f"/api/admin/proposals/{tid}/approve", {})
            new = _adjust_rows(apex)[len(rows1):]
            check(len(new) == 2 and sum((x[3] for x in new), Z) == 0
                  and {(x[0], x[1], x[3]) for x in new} == {("CC-MNT", "5240", Decimal(-10000)),
                                                             ("CC-MNT", "1430", Decimal(10000))},
                  f"the transfer wrote {new}; expected -10,000 on 5240 and +10,000 on 1430")  # fmt: skip

        # refusals
        base = {"kind": "supplement", "gl_code": "5240", "cost_centre_code": "CC-MNT", "period": PERIOD,
                "amount": "1000", "reason": "eval"}  # fmt: skip
        cases = {
            "amount 0": {**base, "amount": "0"},
            "a negative amount": {**base, "amount": "-5"},
            "no reason": {k: v for k, v in base.items() if k != "reason"},
            "both holders": {**base, "location_code": b3.CKN},
            "neither holder": {k: v for k, v in base.items() if k != "cost_centre_code"},
            "an unknown GL": {**base, "gl_code": "9999"},
            "a locked month": {**base, "period": "2026-10"},
            "an unknown cost centre": {**base, "cost_centre_code": "CC-NONE"},
        }
        rows2 = _adjust_rows(apex)
        props = int(apex.one("select count(*) from proposal") or 0)
        for name, bad in cases.items():
            r = apex.adjust(bad, fin)
            check(r.status_code == 422, f"an adjustment with {name} answered {r.status_code}, expected 422")
        r = apex.adjust(base, buyer)
        check(r.status_code == 403, f"the buyer proposing an adjustment answered {r.status_code}, expected 403")
        check(_adjust_rows(apex) == rows2 and int(apex.one("select count(*) from proposal") or 0) == props,
              "refused adjustments wrote rows or proposals")  # fmt: skip
    b3.tenant_check3(check, apex, bw)
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes.append(
        "Spec gap: 4.3 says adjustments go 'into open periods only' while 4.3 also calls a month that has not ended "
        "'future'; the spec's own scenario adjusts 2026-12 on 7 December, so the eval reads 'open' as 'not locked'. "
        "The 422 for a 'future-FY-only period' in 4.6 is not explained and is not tested."
    )
    out.notes.append(
        "Spec gap: the transfer's `to` object is not specified beyond '{...}'; the eval sends {gl_code, "
        "cost_centre_code} and accepts the rows in either order."
    )
    return out


def _request(check: b.Checker, co: b3.Co3) -> str | None:
    """A submitted SV-01 request on CC-MNT (5240), so a budget check shows the CC-MNT 5240 group."""
    from sqlalchemy import text

    asker = next(c for c, u in sorted(co.people.items()) if "production" in (u.get("designation") or "").lower())
    r = co.call(asker, "POST", "/api/requisitions", {"lines": [{"item_code": "SV-01", "qty": "1"}],
                "need_by": "2026-12-21", "location": b3.CKN, "reason": "eval: calibration"})  # fmt: skip
    no = js(r).get("id")
    if not check(bool(no), f"setup: the SV-01 request was refused: {r.status_code} {r.text[:200]}", "medium"):
        return None
    with lib.owner_session(co.slug) as s:
        s.execute(text("update request_line set cost_centre_code = 'CC-MNT' where request_no = :n"), {"n": no})
        s.commit()
    co.call(asker, "POST", f"/api/requisitions/{no}/submit", {})
    return str(no)


def _var(co: b3.Co3, cc: str, gl: str) -> dict[str, Any] | None:
    rows = [r for r in b3.listed(co.variance(), "rows") if r.get("cost_centre") == cc and str(r.get("gl_code")) == gl]
    return rows[0] if rows else None


def _chk(co: b3.Co3, no: str | None) -> Decimal | None:
    if not no:
        return None
    found = [g for (gl, _), g in co.groups(no).items() if gl == "5240"]
    return b3.dec(found[0].get("budget")) if found else None


def _adjust_rows(co: b3.Co3) -> list[tuple[Any, ...]]:
    if "kind" not in co.columns("budget"):
        return []
    return [
        (r[0], r[1], r[2], D(r[3]), r[4], r[5])
        for r in co.q(
            "select cost_centre_code, gl_code, period, amount, reason, proposal_id from budget where kind = 'adjustment' "
            "order by id"
        )
    ]
