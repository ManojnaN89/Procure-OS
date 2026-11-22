"""E109: a revised order is never touchless (Rev s5, OP-7), written without the implementation.

Independence: written from the block 4 spec (sections 4.1 rules 3 and 8, 4.3, 6.3 V5) and the dataset only. The
contract price comes from `price_record.csv`, the bands and their people from `approval_rule.csv` and the profile.

V5 on a fresh Apex reset: production asks for 10 pump shafts (RM-05) on the castings rate contract; the request
approves itself (REQ-01), the worker makes the order, the touchless rule approves and sends it (Apex's `po.issue` is
automatic). Its first approved version is recorded by the rule (`approved_by` 'rule:PO-01', the contract price). The
buyer moves the need-by +5 days: material, so the order goes back for approval; the touchless tests run again and
`touchless.revision` fails with "the order was changed after approval: a person approves it"; the step is the purchase
manager's (PO-02, the automatic band moved up), not the rule's; he approves version 2. Ends with a tenant check.
Mutations: the revision approved by the rule; the check missing from `touchless_checks`.
"""

from typing import Any

import block1 as b
import block4 as k
import env
import lib
from lib import Outcome

ID = "E109"
TITLE = "A revised order is never touchless: a person approves the change"
REQUIREMENT = (
    "Rev s5, OP-7 (block 4 spec 4.1 rules 3 and 8, V5): a touchless order's approval by the rule records its first "
    "approved version ('rule:PO-01', the contract price); after a material revision the touchless tests run again, "
    "touchless.revision fails with its text, the order is not approved by the rule but waits for the next band's "
    "person (the purchase manager), who approves the new version; tenant check"
)
PREFIX = "e109"
REVISION_TEXT = "the order was changed after approval: a person approves it"


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
        po = _v5(out, check, apex)
        k.tenant_check(check, apex, bw, [po], start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        ("The shaft request is dated 14 Dec like the other scenarios (demo_cases dates it a week out); the spec's 'Rs "
        "44,000' example is not the figure of 10 shafts at the contract price, so the eval works the total out itself."),
        ("Touchless checks are read from po.touchless_checks (a list of {test, passed, detail}; 'code' or 'name' are "
        "accepted for the test's name)."),
    ]
    return out


def _checks(row: dict[str, Any]) -> list[dict[str, Any]]:
    found = k.as_json(row.get("touchless_checks")) or []
    return [c for c in found if isinstance(c, dict)]


def _name(c: dict[str, Any]) -> str:
    return str(c.get("test") or c.get("code") or c.get("name") or "")


def _v5(out: Outcome, check: b.Checker, co: k.Co) -> str:
    oracle = k.Oracle(co)
    m = k.Mutations()
    pm = oracle.person("purchase_manager")
    po = co.contract_order(check, k.SHAFT, 10, "V5")
    if not po:
        return ""
    row, ln = co.po_row(po), co.po_line1(po)
    price = oracle.contract_price(k.SHAFT)
    supplier = str(row.get("supplier_code"))
    a = oracle.amounts(k.SHAFT, 10, price, supplier)
    band = oracle.band(a["total"])
    check(price is not None and k.D(ln.get("unit_price")) == price and k.D(row.get("total")) == a["total"],
          f"V5: {po} at {ln.get('unit_price')} total {row.get('total')}; oracle contract price {price}, total "
          f"{a['total']}")  # fmt: skip
    check(band["approver_chain"] == "auto" and row.get("touchless") is True and row.get("status") in ("sent", "acknowledged"),
          f"V5: precondition: {po} touchless {row.get('touchless')}, status {row.get('status')} in band "
          f"{band['rule_code']}; expected a touchless order sent by the rule")  # fmt: skip
    snaps = co.approved_versions(po)
    first = snaps[0] if snaps else {}
    check(len(snaps) == 1 and int(first.get("version") or 0) == 1 and str(first.get("approved_by")) == f"rule:{band['rule_code']}"
          and row.get("approved_version") == 1,
          f"V5: the rule's approval recorded {[(s.get('version'), s.get('approved_by')) for s in snaps]}, approved_version "
          f"{row.get('approved_version')}; expected one row v1 by 'rule:{band['rule_code']}'")  # fmt: skip
    snap = k.as_json(first.get("snapshot")) or {}
    lines = snap.get("lines") if isinstance(snap, dict) else None
    check(bool(lines) and k.D(lines[0].get("unit_price")) == price and k.D(first.get("band_max")) == k.D(band["max_value"]),
          f"V5: the first approved snapshot {snap} band_max {first.get('band_max')}; expected the contract price {price} "
          f"and band_max {band['max_value']}")  # fmt: skip

    # the buyer moves the need-by +5 days: material; never touchless again
    new_date = k.days(k.NEED_BY, 5)
    base = k.Snap(1, k.D(10), price or k.D(0), k.NEED_BY, a["total"], k.D(band["max_value"]), band["rule_code"], supplier)
    new = k.Snap(2, k.D(10), price or k.D(0), new_date, a["total"], None, band["rule_code"], supplier)
    want = k.material(base, new, base, oracle.date_days())
    r = co.revise(po, need_by=new_date)
    co.work()
    row = co.po_row(po)
    checks = _checks(row)
    rev = [c for c in checks if _name(c) == "touchless.revision"]
    status = row.get("status")
    m.case("rule_reapproves", "V5 +5 days", (status, row.get("touchless")), ("submitted", False), ("approved", True))
    m.case("no_revision_check", "V5 +5 days", bool(rev), True, False)
    check(r.status_code == 200 and status == "submitted" and row.get("touchless") is False,
          f"V5: after +5 days {po} is {status}, touchless {row.get('touchless')} ({r.status_code} {r.text[:150]}); "
          "expected submitted for a person, not touchless")  # fmt: skip
    k.check_reasons(check, "V5", co.revisions(po).get(2, {}).get("material"), want)
    check(len(rev) == 1 and rev[0].get("passed") is False and k.norm(REVISION_TEXT) in k.norm(rev[0].get("detail")),
          f"V5: touchless.revision in the checks: {rev}; expected failed with '{REVISION_TEXT}'")  # fmt: skip
    check(bool(checks) and _name(checks[-1]) == "touchless.revision", f"V5: touchless.revision is not the last check: "
          f"{[_name(c) for c in checks]}", "low")  # fmt: skip
    rule, who = oracle.approver(a["total"])
    pend = co.open_steps(po)
    check(who == pm and len(pend) == 1 and co.code_of(pend[0].get("approver")) == pm and pend[0].get("version") == 2,
          f"V5: open steps {[(s.get('approver'), s.get('role'), s.get('version')) for s in pend]}; expected {pm} "
          f"({rule}, the band above the automatic one) with version 2")  # fmt: skip
    check(row.get("approved_version") == 1, f"V5: approved_version {row.get('approved_version')} before the new approval; "
          "expected 1 (kept until the new approval)", "medium")  # fmt: skip
    k.approve_ok(check, co, pm, po, 2, "V5 approve version 2")
    snaps = co.approved_versions(po)
    check([int(s["version"]) for s in snaps] == [1, 2] and pm in str(snaps[-1].get("approved_by") or "").split(",")
          if snaps else False,
          f"V5: snapshot rows {[(s.get('version'), s.get('approved_by')) for s in snaps]}; expected v1 by the rule and v2 "
          f"by {pm}")  # fmt: skip
    out.metrics["V5"] = f"{po}: {a['total']} in {band['rule_code']}; after +5 days {status}, step {pm}"
    m.report(check, out, ["rule_reapproves", "no_revision_check"])
    return po
