"""E106: a material change after approval is approved again; anything else carries the approval (Q19), written without
the implementation.

Independence: written from the block 4 spec (sections 4.1 rules 4-7 and 9, 6.3 V2 and V2b, 7) and the dataset only; the
material reasons come from `block4.material` (spec 4.1 rule 4) over the oracle's versions, the date tolerance from
`tolerance.csv`.

V2 on a fresh Apex reset: Q(120, 905.00); the purchase manager approves version 1; the buyer sends; the supplier accepts
from its link. The buyer moves the need-by to 19 Dec (+5): material ("line 1: date moved 5 days from the approved
14 Dec 2026, more than the 3 accepted"), the order is submitted again (round 2, version 2), the acknowledgement is
cleared, the total is unchanged, the revision row has reapproval true and baseline 1, the old respond link is refused.
The purchase manager approves version 2. Then the send guard (spec 4.1 rule 9): a material revision row after the
approved version is forced into the database as the owner, and sending is refused 409; with it removed, sending works
and the new respond link works.
V2b: another Q(120, 905.00), approved, sent, accepted. The buyer cuts to 100: not material, the approval of version 1
carries (status sent, acknowledgement cleared, no new round, `approval_carried` true), a new respond link is mailed,
1,06,790.00. Then +3 days (17 Dec): still not material (version 3).
Ends with a tenant check. Mutations: a non-material change restarting approval; a material change keeping the old
approval.
"""

from typing import Any

import block1 as b
import block4 as k
import env
import lib
from lib import Outcome
from sqlalchemy import text

ID = "E106"
TITLE = "A material change after approval is approved again; anything else carries the approval (Q19)"
REQUIREMENT = (
    "Task 0.2, Q19, AT-08 (block 4 spec 4.1 rules 4-7 and 9, V2, V2b): a change is measured from the last approved "
    "version; material means approval again (submitted, ack cleared, new round with the new version, reasons and "
    "baseline on the revision); otherwise the approval carries (approved_version unchanged, ack cleared, new respond "
    "link); sending a version changed materially after approval is refused; tenant check"
)
PREFIX = "e106"


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
        m = k.Mutations()
        start = apex.mark()
        oracle = k.Oracle(apex)
        truth: list[dict[str, Any]] = []
        po2 = _v2(out, check, apex, oracle, m, truth)
        po2b = _v2b(out, check, apex, oracle, m, truth)
        out.metrics["truth rows compared"] = k.compare_truth(check, apex, truth)
        m.report(check, out, ["nonmaterial_restarts", "material_keeps"])
        k.tenant_check(check, apex, bw, [po2, po2b], start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        ("V2's old respond link was already used by the supplier's acceptance, so its refusal is expected on two "
        "grounds; V2b checks the version-bound refusal on an unused link of version 2 after version 3 is mailed."),
        ("The send guard is forced by inserting a po_revision row (reapproval true, version above approved_version) and "
        "raising po.version as the owner, then removed again; the spec names the guard, not how a test reaches it."),
    ]
    return out


def _approved_sent_acknowledged(check: b.Checker, co: k.Co, oracle: k.Oracle, label: str) -> tuple[str, str | None]:
    po = co.quotation_order(check, 120, "905.00", label)
    if not po:
        return "", None
    pm = oracle.person("purchase_manager")
    k.approve_ok(check, co, pm, po, 1, f"{label} approve version 1")
    check(co.po_row(po).get("approved_version") == 1, f"{label}: approved_version {co.po_row(po).get('approved_version')}"
          ", expected 1")  # fmt: skip
    token = k.send_ok(check, co, po, label)
    r = co.supplier(token or "", action="accept")
    check(r.status_code == 200 and co.po_row(po).get("status") == "acknowledged",
          f"{label}: the supplier accepting from the link answered {r.status_code}; order {co.po_row(po).get('status')}")  # fmt: skip
    return po, token


def _v2(out: Outcome, check: b.Checker, co: k.Co, oracle: k.Oracle, m: k.Mutations, truth: list) -> str:
    po, first_link = _approved_sent_acknowledged(check, co, oracle, "V2")
    if not po:
        return ""
    pm, supplier, days = oracle.person("purchase_manager"), str(co.po_row(po).get("supplier_code")), oracle.date_days()
    v1 = oracle.snap(1, 120, "905.00", k.NEED_BY, supplier)
    new_date = k.days(k.NEED_BY, 5)
    v2 = oracle.snap(2, 120, "905.00", new_date, supplier)
    want = k.material(v1, v2, v1, days)
    sentence = "line 1: date moved 5 days from the approved 14 Dec 2026, more than the 3 accepted"
    check(want == [sentence], f"oracle self-check: V2 reasons {want}, the spec says [{sentence}]", "medium")

    r = co.revise(po, need_by=new_date)
    row, rev = co.po_row(po), co.revisions(po).get(2, {})
    check(r.status_code == 200, f"V2: moving the need-by to {new_date} answered {r.status_code} {r.text[:200]}")
    status = row.get("status")
    m.case("material_keeps", "V2 +5 days after acknowledgement", status,
           k.after_revision(want, "acknowledged"), k.after_revision(want, "acknowledged", k.Wrong(material_keeps=True)))
    check(status == "submitted" and row.get("acknowledged_at") is None and row.get("approved_version") == 1
          and int(row.get("version") or 0) == 2 and k.D(row.get("total")) == v1.total,
          f"V2: after +5 days {po} is {status}, ack {row.get('acknowledged_at')}, approved_version "
          f"{row.get('approved_version')}, version {row.get('version')}, total {row.get('total')}; expected submitted, "
          f"cleared, 1, 2 and {v1.total} unchanged")  # fmt: skip
    check(rev.get("reapproval") is True and rev.get("baseline_version") == 1,
          f"V2: revision row reapproval {rev.get('reapproval')}, baseline {rev.get('baseline_version')}; expected true, 1")
    k.check_reasons(check, "V2", rev.get("material"), want, (sentence,))
    hist = co.history(po, co.buyer()).get(2, {})
    check(k.reasons_match(k.as_list(hist.get("material")), want) and hist.get("baseline_version") == 1
          and hist.get("approval_carried") is False,
          f"V2: controls history of version 2 {({x: hist.get(x) for x in ('material', 'baseline_version', 'approval_carried')})}"
          "; expected the reason, baseline 1, approval_carried false", "medium")  # fmt: skip
    pend = co.open_steps(po)
    check(len(pend) == 1 and pend[0].get("version") == 2 and co.code_of(pend[0].get("approver")) == pm
          and pend[0]["round"] > 1, f"V2: open steps {[(s['round'], s.get('approver'), s.get('version')) for s in pend]}; "
          f"expected a new round for {pm} with version 2")  # fmt: skip
    k.version_bound_link(check, co, first_link, "V2 respond link of version 1")
    truth += [k.truth_row("V2", v1, oracle, approver=pm),
              k.truth_row("V2", v2, oracle, approver=pm, reasons=want)]  # fmt: skip

    # approved again, then the send guard forced, then sent
    k.approve_ok(check, co, pm, po, 2, "V2 approve version 2")
    check([int(s["version"]) for s in co.approved_versions(po)] == [1, 2] and co.po_row(po).get("approved_version") == 2,
          f"V2: snapshot rows {[s['version'] for s in co.approved_versions(po)]}, approved_version "
          f"{co.po_row(po).get('approved_version')}; expected [1, 2] and 2")  # fmt: skip
    _forced_send(check, co, po)
    token = k.send_ok(check, co, po, "V2 send version 2")
    r = co.supplier(token or "", action="accept")
    check(r.status_code == 200 and co.po_row(po).get("status") == "acknowledged",
          f"V2: the new respond link answered {r.status_code}; order {co.po_row(po).get('status')}")  # fmt: skip
    return po


def _forced_send(check: b.Checker, co: k.Co, po: str) -> None:
    """Spec 4.1 rule 9: a po_revision with version > approved_version and reapproval true blocks sending."""
    row = co.po_row(po)
    version = int(row.get("version") or 0)
    forced = version + 1
    with lib.owner_session(co.slug) as db:
        db.execute(
            text(
                "insert into po_revision (po_no, version, changed_by, reason, changes, total_before, total_after, "
                "reapproval) values (:n, :v, 'eval', 'E106 forced material change', '[]'::jsonb, :t, :t, true)"
            ),
            {"n": po, "v": forced, "t": row.get("total")},
        )
        db.execute(text("update po set version = :v where no = :n"), {"v": forced, "n": po})
        db.commit()
    since = co.mark()
    r = co.send(po)
    told = [x for x in co.mails(since) if "/supplier/" in str(x.get("body", ""))]
    check(r.status_code == 409 and co.po_row(po).get("status") == "approved" and not told,
          f"V2 send guard: sending {po} with a material version {forced} after approved version {version} answered "
          f"{r.status_code} {r.text[:150]}, order {co.po_row(po).get('status')}, {len(told)} supplier mail(s); "
          "expected 409, still approved, no mail")  # fmt: skip
    check(f"version {forced}" in r.text.lower() or "approved first" in r.text.lower(),
          f"V2 send guard: the refusal does not name the version: {r.text[:200]}", "low")
    with lib.owner_session(co.slug) as db:
        db.execute(text("delete from po_revision where po_no = :n and version = :v"), {"n": po, "v": forced})
        db.execute(text("update po set version = :v where no = :n"), {"v": version, "n": po})
        db.commit()


def _v2b(out: Outcome, check: b.Checker, co: k.Co, oracle: k.Oracle, m: k.Mutations, truth: list) -> str:
    po, _ = _approved_sent_acknowledged(check, co, oracle, "V2b")
    if not po:
        return ""
    pm, supplier, days = oracle.person("purchase_manager"), str(co.po_row(po).get("supplier_code")), oracle.date_days()
    v1 = oracle.snap(1, 120, "905.00", k.NEED_BY, supplier)
    v2 = oracle.snap(2, 100, "905.00", k.NEED_BY, supplier)
    want = k.material(v1, v2, v1, days)
    rounds = max((s["round"] for s in co.steps(po)), default=0)
    since = co.mark()
    r = co.revise(po, qty=100)
    co.work()
    row, rev = co.po_row(po), co.revisions(po).get(2, {})
    status = row.get("status")
    m.case("nonmaterial_restarts", "V2b cut to 100 after acknowledgement", status,
           k.after_revision(want, "acknowledged"),
           k.after_revision(want, "acknowledged", k.Wrong(nonmaterial_restarts=True)))  # fmt: skip
    check(r.status_code == 200 and want == [] and status == "sent" and row.get("acknowledged_at") is None
          and row.get("approved_version") == 1 and int(row.get("version") or 0) == 2,
          f"V2b: after the cut to 100 {po} is {status}, ack {row.get('acknowledged_at')}, approved_version "
          f"{row.get('approved_version')}, version {row.get('version')} ({r.status_code}); expected sent, cleared, 1, 2")  # fmt: skip
    k.check_amounts(check, co, oracle, po, 100, "905.00", "V2b v2")
    check(max((s["round"] for s in co.steps(po)), default=0) == rounds and not co.open_steps(po),
          f"V2b: the cut opened an approval round: {[(s['round'], s['status']) for s in co.steps(po)]}")  # fmt: skip
    check(rev.get("reapproval") is False and rev.get("baseline_version") == 1 and k.as_list(rev.get("material")) == [],
          f"V2b: revision row reapproval {rev.get('reapproval')}, baseline {rev.get('baseline_version')}, material "
          f"{rev.get('material')}; expected false, 1, []")  # fmt: skip
    hist = co.history(po, co.buyer()).get(2, {})
    check(hist.get("approval_carried") is True, f"V2b: controls history of version 2 says approval_carried "
          f"{hist.get('approval_carried')}; expected true ('approval of version 1 carries')")  # fmt: skip
    link2 = co.supplier_token(po, since)
    check(link2 is not None and co.supplier_page(link2 or "").status_code == 200,
          f"V2b: no working respond link was mailed for version 2 (link {bool(link2)})")  # fmt: skip

    # +3 days from the approved 14 Dec: the tolerance itself, not material
    v3 = oracle.snap(3, 100, "905.00", k.days(k.NEED_BY, 3), supplier)
    want3 = k.material(v1, v3, v2, days)
    since = co.mark()
    r = co.revise(po, need_by=k.days(k.NEED_BY, 3))
    co.work()
    row, rev = co.po_row(po), co.revisions(po).get(3, {})
    check(r.status_code == 200 and want3 == [] and row.get("status") == "sent" and row.get("approved_version") == 1
          and rev.get("reapproval") is False and int(row.get("version") or 0) == 3,
          f"V2b v3 (+3 days): {r.status_code}, {row.get('status')}, approved_version {row.get('approved_version')}, "
          f"reapproval {rev.get('reapproval')}, version {row.get('version')}; expected sent, 1, false, 3")  # fmt: skip
    link3 = co.supplier_token(po, since)
    k.version_bound_link(check, co, link2, "V2b unused respond link of version 2 after version 3")
    r = co.supplier(link3 or "", action="accept")
    check(r.status_code == 200, f"V2b: the respond link of version 3 answered {r.status_code}")
    truth += [k.truth_row("V2b", v2, oracle, approver=pm, reasons=want),
              k.truth_row("V2b", v3, oracle, approver=pm, reasons=want3)]  # fmt: skip
    out.metrics["V2b totals v2 / v3"] = f"{v2.total} / {v3.total}"
    return po
