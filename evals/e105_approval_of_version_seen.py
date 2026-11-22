"""E105: an order's approval is of the version the approver saw (task 0.2, DF-07), written without the implementation.

Independence: written from the block 4 spec (sections 3.2, 4.1 rules 1-3, 4.3, 6.1, 6.3 V1, 7) and the dataset only.
The oracle (`block4.Oracle`) works out GST, the band and its person from `approval_rule.csv`, `item.csv`,
`supplier.csv` and the company profile; the spec's figures and `truth/truth_approval_binding.csv` are checked against it.

Scenario V1 on a fresh Apex reset: Q(120 kg, 905.00) gives version 1 (1,28,148.00, PO-02, one step for the purchase
manager with version 1). The buyer cuts the quantity to 100 while it waits: version 2 (1,06,790.00), round 1 cancelled,
round 2's step carries version 2. The purchase manager approving version 1 (and version 3) is refused 409 with nothing
written; rejecting version 1 likewise; approving version 2 works: `approved_version` 2, one `po_approved_version` row of
version 2 (total, band_max 5,00,000.00, PO-02, the snapshot), audit `po.approve` with version 2 and `with_code` false.
Then (spec 4.3, "omitted: approves the current version and records it"): a cut to 90 carries the approval; 110 is a
quantity up from the approved 100, so it goes back to him with version 4, and approving without a version approves and
records version 4. Ends with a tenant check against Brightwave.
Mutations: the version ignored by approve; the snapshot not written or written for version 1.
"""

from typing import Any

import block1 as b
import block4 as k
import env
import lib
from lib import Outcome

ID = "E105"
TITLE = "An order's approval is of the version the approver saw; the approved version is recorded"
REQUIREMENT = (
    "Task 0.2, DF-07 (block 4 spec 4.1 rules 1-3, 4.3, V1): each step carries the version of its round; approve and "
    "reject with a stale version are 409 and write nothing; approving records po.approved_version and one "
    "po_approved_version snapshot (total, band, rule, approver, lines) in the same step; audit po.approve with version "
    "and with_code; omitted version approves the current one; amounts against the oracle and the truth file; tenant check"
)
PREFIX = "e105"


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
        _v1(out, check, apex, bw)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        ("Spec 4.3 'omitted: approves the current version and records it' is checked on the same order after a carried "
        "cut to 90 and a material rise to 110 (version 4), not on a second order: the rule is the same."),
        ("A refused stale decision is checked to write nothing by counting audit rows of the order and comparing its "
        "steps and status before and after."),
    ]
    return out


def _v1(out: Outcome, check: b.Checker, co: k.Co, other: k.Co) -> None:
    oracle = k.Oracle(co)
    m = k.Mutations()
    pm = oracle.person("purchase_manager")
    start_mail = co.mark()
    po = co.quotation_order(check, 120, "905.00", "V1")
    if not po:
        return
    supplier = str(co.po_row(po).get("supplier_code"))
    k.self_check(check, oracle, supplier)
    days = oracle.date_days()

    # version 1: amounts, band, one step for the purchase manager carrying version 1
    v1 = oracle.snap(1, 120, "905.00", k.NEED_BY, supplier)
    k.check_amounts(check, co, oracle, po, 120, "905.00", "V1 v1")
    rule, who = oracle.approver(v1.total)
    row = co.po_row(po)
    check((row.get("rule_code"), row.get("rule_version"), row.get("status"), int(row.get("version") or 0))
          == (rule, 1, "submitted", 1), f"V1 v1: {po} rule {row.get('rule_code')} v{row.get('rule_version')}, status "
          f"{row.get('status')}, version {row.get('version')}; oracle {rule} v1, submitted, version 1")  # fmt: skip
    steps = co.open_steps(po)
    check(len(steps) == 1 and co.code_of(steps[0].get("approver")) == who == pm and steps[0].get("version") == 1,
          f"V1 v1: open steps {[(s.get('approver'), s.get('version'), s.get('status')) for s in steps]}; oracle one step "
          f"for {who} with version 1")  # fmt: skip
    seen = co.detail(po, pm).get("approval") or {}
    check(seen.get("version") == 1 and seen.get("needs_code") is False,
          f"V1 v1: the purchase manager's view says approval version {seen.get('version')}, needs_code "
          f"{seen.get('needs_code')}; expected 1 and false (1,28,148.00 is not above 5,00,000.00)", "medium")  # fmt: skip

    # the buyer cuts the quantity to 100 while it waits: version 2, a new round for version 2
    r = co.revise(po, qty=100)
    check(r.status_code == 200, f"V1: the cut to 100 answered {r.status_code} {r.text[:200]}")
    v2 = oracle.snap(2, 100, "905.00", k.NEED_BY, supplier)
    k.check_amounts(check, co, oracle, po, 100, "905.00", "V1 v2")
    steps = co.steps(po)
    if not check(bool(steps), f"V1 v2: {po} has no approval steps"):
        return
    r1 = [s for s in steps if s["round"] == min(x["round"] for x in steps)]
    r2 = [s for s in steps if s["status"] in ("pending", "waiting")]
    check(all(s["status"] == "cancelled" for s in r1) and r1[0].get("version") == 1,
          f"V1 v2: the first round is {[(s['status'], s.get('version')) for s in r1]}; expected cancelled, version 1")  # fmt: skip
    check(len(r2) == 1 and r2[0]["round"] > r1[0]["round"] and r2[0].get("version") == 2
          and co.code_of(r2[0].get("approver")) == pm,
          f"V1 v2: open steps {[(s['round'], s.get('approver'), s.get('version')) for s in r2]}; expected one step of a "
          f"new round for {pm} with version 2")  # fmt: skip
    rev = co.revisions(po).get(2, {})
    check(rev.get("reapproval") is True and k.as_list(rev.get("material")) == [] and rev.get("baseline_version") is None,
          f"V1 v2: revision row reapproval {rev.get('reapproval')}, material {rev.get('material')}, baseline "
          f"{rev.get('baseline_version')}; expected true (changed while submitted), [] and none (never approved)", "medium")  # fmt: skip

    # stale decisions: refused, nothing written
    co.headers(pm)  # signed in before counting, so a login row is not counted as a write
    before = k.state_of(co, po)
    stale = co.approve_po(pm, po, version=1)
    k.stale_refused(check, co, po, stale, "V1 approve with version 1", before, 2)
    # the wrong rule approves whatever version it is given
    m.case("version_ignored", "approve with version 1 after version 2", stale.status_code, 409, 200)
    above = co.approve_po(pm, po, version=3)
    k.stale_refused(check, co, po, above, "V1 approve with version 3", before, 2)
    rej = co.reject_po(pm, po, version=1)
    k.stale_refused(check, co, po, rej, "V1 reject with version 1", before, 2)

    # approving version 2
    mark = int(co.one("select coalesce(max(id), 0) from audit_event") or 0)
    k.approve_ok(check, co, pm, po, 2, "V1 approve version 2")
    row = co.po_row(po)
    check(row.get("status") == "approved" and row.get("approved_version") == 2,
          f"V1: after approval {po} is {row.get('status')} with approved_version {row.get('approved_version')}; "
          "expected approved and 2")  # fmt: skip
    snaps = co.approved_versions(po)
    got_versions = [int(s["version"]) for s in snaps]
    m.case("no_snapshot", "snapshot rows after approving version 2", got_versions, [2], [])
    m.case("snapshot_first", "snapshot rows after approving version 2", got_versions, [2], [1])
    if check(got_versions == [2], f"V1: po_approved_version rows {got_versions}, expected one for version 2"):
        k.check_snapshot(check, co, po, snaps[0], v2, pm, "V1 v2")
    _audit(check, co, po, mark, 2, pm, "V1 v2")

    # spec 4.3: a carried cut, a material rise, and an approval with no version given
    r = co.revise(po, qty=90)
    v3 = oracle.snap(3, 90, "905.00", k.NEED_BY, supplier)
    want3 = k.material(v2, v3, v2, days)
    row, rev = co.po_row(po), co.revisions(po).get(3, {})
    check(r.status_code == 200 and want3 == [] and row.get("status") == "approved" and row.get("approved_version") == 2
          and rev.get("reapproval") is False and rev.get("baseline_version") == 2,
          f"V1 v3 (90, below the approved 100): {r.status_code}, {row.get('status')}, approved_version "
          f"{row.get('approved_version')}, reapproval {rev.get('reapproval')}, baseline {rev.get('baseline_version')}; "
          "expected the approval of version 2 to carry")  # fmt: skip
    r = co.revise(po, qty=110)
    v4 = oracle.snap(4, 110, "905.00", k.NEED_BY, supplier)
    want4 = k.material(v2, v4, v3, days)
    rev, row = co.revisions(po).get(4, {}), co.po_row(po)
    check(r.status_code == 200 and row.get("status") == "submitted" and row.get("approved_version") == 2,
          f"V1 v4 (110, above the approved 100): {r.status_code} {r.text[:120]}, {row.get('status')}, approved_version "
          f"{row.get('approved_version')}; expected submitted with approved_version still 2")  # fmt: skip
    k.check_reasons(check, "V1 v4", rev.get("material"), want4)
    k.check_amounts(check, co, oracle, po, 110, "905.00", "V1 v4")
    pend = co.open_steps(po)
    check(len(pend) == 1 and pend[0].get("version") == 4 and co.code_of(pend[0].get("approver")) == pm,
          f"V1 v4: open steps {[(s.get('approver'), s.get('version')) for s in pend]}; expected {pm} with version 4")  # fmt: skip
    mark = int(co.one("select coalesce(max(id), 0) from audit_event") or 0)
    k.approve_ok(check, co, pm, po, None, "V1 approve with no version (version 4)")
    snaps = co.approved_versions(po)
    check([int(s["version"]) for s in snaps] == [2, 4] and co.po_row(po).get("approved_version") == 4,
          f"V1: after approving with no version, snapshot rows {[s['version'] for s in snaps]}, approved_version "
          f"{co.po_row(po).get('approved_version')}; expected [2, 4] and 4")  # fmt: skip
    if len(snaps) == 2:
        k.check_snapshot(check, co, po, snaps[1], v4, pm, "V1 v4")
    _audit(check, co, po, mark, 4, pm, "V1 v4")

    # the truth file and the oracle
    n = k.compare_truth(check, co, [
        k.truth_row("V1", v1, oracle, approver=pm),
        k.truth_row("V1", v2, oracle, approver=pm, reasons=[]),
    ])  # fmt: skip
    out.metrics["truth rows compared"] = n
    out.metrics["V1 totals v1 / v2 / v4"] = f"{v1.total} / {v2.total} / {v4.total}"
    m.report(check, out, ["version_ignored", "no_snapshot", "snapshot_first"])
    k.tenant_check(check, co, other, [po], start_mail)


def _audit(check: b.Checker, co: k.Co, po: str, mark: int, version: int, pm: str, label: str) -> None:
    approve = co.audits("po.approve", po, mark)
    approved = co.audits("po.approved", po, mark)
    a = approve[-1]["after"] if approve else {}
    check(bool(approve) and a.get("version") == version and a.get("with_code") is False,
          f"{label}: audit po.approve after {a}; expected version {version} and with_code false")  # fmt: skip
    check(bool(approved) and (approved[-1]["after"] or {}).get("version") == version,
          f"{label}: audit po.approved {[x['after'] for x in approved]}; expected version {version}", "medium")
    if approve:
        check(co.code_of(approve[-1]["actor_id"]) == pm, f"{label}: po.approve by {approve[-1]['actor_id']}, not {pm}",
              "medium")

