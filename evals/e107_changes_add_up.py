"""E107: changes add up from the approved version (DF-07, OP-2, OP-5, OP-22), written without the implementation.

Independence: written from the block 4 spec (sections 4.1 rule 4, 4.3, 6.3 V3) and the dataset only; every expected
verdict is `block4.material` against the last approved version the eval itself tracks.

V3 and the boundaries, on one order of a fresh Apex reset: Q(120, 905.00), approved, sent. +2 days (16 Dec): version 2,
not material. +2 again (18 Dec): version 3 is material, "line 1: date moved 4 days from the approved 14 Dec 2026, more
than the 3 accepted" (compared with the version before it would not be); the purchase manager approves version 3; sent.
Quantity 120 -> 100 (version 4) -> 110 (version 5): neither is material (110 is below the approved 120; compared with
the version before it would be); 121 is refused (more than the request asked for). Then 14 Dec, four days earlier than
the approved 18 Dec: material (an earlier date counts too). Approved again (version 6), sent; +2 then -2: neither is
material. The unused respond link of version 1 is refused once version 2 is mailed. The history on the controls API
carries the reasons. Ends with a tenant check.
Mutations: comparison with the previous version; abs() dropped (an earlier date not material).
"""

from datetime import date
from typing import Any

import block1 as b
import block4 as k
import env
import lib
from lib import Outcome

ID = "E107"
TITLE = "Changes add up from the approved version: dates, quantities, earlier dates"
REQUIREMENT = (
    "Task 0.2, DF-07, OP-2, OP-5, OP-22 (block 4 spec 4.1 rule 4, 4.3, V3): every revision is compared with the last "
    "approved version; two +2 day slips make the second material; a quantity back below the approved one is not; an "
    "earlier date beyond tolerance is; +2 then -2 is not; reasons on the revision and in the history; tenant check"
)
PREFIX = "e107"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient
    from p2p.main import app
    from p2p.platform import mail

    out = Outcome(True)
    check = b.Checker(out)
    saved = mail.send_email
    box = lib.capture_mail()
    try:
        client = TestClient(app)
        bw = k.Co("brightwave", PREFIX, client, box)
        apex = k.Co("apex", PREFIX, client, box)
        start = apex.mark()
        po = _v3(out, check, apex)
        k.tenant_check(check, apex, bw, [po], start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        ("Spec gap: for an earlier date the reason's `<d>` is read as the size of the move ('moved 4 days'); a signed "
        "'moved -4 days' is accepted too (the comparison drops the sign)."),
        "The quantity boundary 121 is checked only as a refusal (the request asked for 120), as spec 4.3 says.",
    ]
    return out


class Track:
    """The eval's own record of the order: the last approved version and the version before (the oracle's input)."""

    def __init__(self, co: k.Co, oracle: k.Oracle, po: str, supplier: str) -> None:
        self.co, self.oracle, self.po, self.supplier = co, oracle, po, supplier
        self.version, self.qty, self.need_by = 1, 120, k.NEED_BY
        self.approved = oracle.snap(1, 120, "905.00", k.NEED_BY, supplier)
        self.prev = self.approved
        self.rows: list[dict[str, Any]] = []

    def next(self, *, qty: int | None = None, need_by: date | None = None) -> tuple[k.Snap, list[str], list[str], list[str]]:
        """(the new version, the right reasons, the previous-version reasons, the date-later-only reasons)."""
        self.version += 1
        self.qty = qty if qty is not None else self.qty
        self.need_by = need_by or self.need_by
        new = self.oracle.snap(self.version, self.qty, "905.00", self.need_by, self.supplier)
        days = self.oracle.date_days()
        right = k.material(self.approved, new, self.prev, days)
        by_prev = k.material(self.approved, new, self.prev, days, k.Wrong(previous_baseline=True))
        later_only = k.material(self.approved, new, self.prev, days, k.Wrong(no_abs=True))
        self.prev = new
        return new, right, by_prev, later_only


def _v3(out: Outcome, check: b.Checker, co: k.Co) -> str:
    oracle = k.Oracle(co)
    m = k.Mutations()
    pm = oracle.person("purchase_manager")
    po = co.quotation_order(check, 120, "905.00", "V3")
    if not po:
        return ""
    k.approve_ok(check, co, pm, po, 1, "V3 approve version 1")
    link1 = k.send_ok(check, co, po, "V3 send version 1")
    t = Track(co, oracle, po, str(co.po_row(po).get("supplier_code")))
    summary: dict[str, str] = {}

    def step(label: str, *, qty: int | None = None, need_by: date | None = None, exact: tuple[str, ...] = ()) -> list[str]:
        was = str(co.po_row(po).get("status"))
        new, right, by_prev, later_only = t.next(qty=qty, need_by=need_by)
        r = co.revise(po, qty=qty, need_by=need_by)
        co.work()
        row, rev = co.po_row(po), co.revisions(po).get(new.version, {})
        got = k.as_list(rev.get("material"))
        check(r.status_code == 200 and int(row.get("version") or 0) == new.version,
              f"{label}: revise answered {r.status_code} {r.text[:150]}; version {row.get('version')}, expected "
              f"{new.version}")  # fmt: skip
        k.check_reasons(check, label, got, right, exact)
        want_status = k.after_revision(right, was)
        check(row.get("status") == want_status and rev.get("reapproval") is bool(right)
              and rev.get("baseline_version") == t.approved.version,
              f"{label}: {row.get('status')}, reapproval {rev.get('reapproval')}, baseline {rev.get('baseline_version')}; "
              f"expected {want_status}, {bool(right)}, {t.approved.version}")  # fmt: skip
        material = bool(got)
        m.case("previous_baseline", label, material, bool(right), bool(by_prev))
        m.case("no_abs", label, material, bool(right), bool(later_only))
        t.rows.append(k.truth_row("V3", new, oracle, approver=pm, reasons=right))
        summary[label] = f"v{new.version} {'material' if right else 'not material'}"
        return right

    since = co.mark()
    step("V3 +2 days (16 Dec)", need_by=k.days(k.NEED_BY, 2))
    check(co.supplier_token(po, since) is not None, "V3: no respond link was mailed for version 2", "medium")
    k.version_bound_link(check, co, link1, "V3 unused respond link of version 1")
    reasons = step("V3 +2 again (18 Dec)", need_by=k.days(k.NEED_BY, 4),
                   exact=("line 1: date moved 4 days from the approved 14 Dec 2026, more than the 3 accepted",))  # fmt: skip
    hist = co.history(po, co.buyer()).get(3, {})
    check(k.reasons_match(k.as_list(hist.get("material")), reasons) and hist.get("baseline_version") == 1,
          f"V3: the history of version 3 shows {hist.get('material')} (baseline {hist.get('baseline_version')}); "
          f"expected {reasons} from version 1", "medium")  # fmt: skip
    _reapprove(check, co, t, pm, "V3 version 3")

    step("V3 qty 100", qty=100)
    step("V3 qty 110 (below the approved 120)", qty=110)
    before = int(co.po_row(po).get("version") or 0)
    r = co.revise(po, qty=121)
    check(r.status_code in (409, 422) and int(co.po_row(po).get("version") or 0) == before,
          f"V3 qty 121: answered {r.status_code} {r.text[:150]}; expected a refusal (the request asked for 120)", "medium")

    earlier = step("V3 14 Dec (4 days earlier than the approved 18 Dec)", need_by=k.NEED_BY,
                   exact=("line 1: date moved 4 days from the approved 18 Dec 2026, more than the 3 accepted",))  # fmt: skip
    if earlier:
        _reapprove(check, co, t, pm, f"V3 version {t.version}")
    step("V3 +2 from the approved 14 Dec", need_by=k.days(k.NEED_BY, 2))
    step("V3 back to 14 Dec", need_by=k.NEED_BY)

    out.metrics["V3 revisions"] = summary
    out.metrics["truth rows compared"] = k.compare_truth(check, co, [
        k.truth_row("V3", oracle.snap(1, 120, "905.00", k.NEED_BY, t.supplier), oracle, approver=pm), *t.rows[:2],
    ])  # fmt: skip
    m.report(check, out, ["previous_baseline", "no_abs"])
    return po


def _reapprove(check: b.Checker, co: k.Co, t: Track, pm: str, label: str) -> None:
    pend = co.open_steps(t.po)
    check(len(pend) == 1 and co.code_of(pend[0].get("approver")) == pm and pend[0].get("version") == t.version,
          f"{label}: open steps {[(s.get('approver'), s.get('version')) for s in pend]}; expected {pm} with version "
          f"{t.version}")  # fmt: skip
    if k.approve_ok(check, co, pm, t.po, t.version, f"{label} approved"):
        t.approved = t.prev
    check(co.po_row(t.po).get("approved_version") == t.version, f"{label}: approved_version "
          f"{co.po_row(t.po).get('approved_version')}, expected {t.version}")  # fmt: skip
    k.send_ok(check, co, t.po, f"{label} sent")
