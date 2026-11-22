"""E111: approving a purchase order above the limit needs a one-time code bound to its version (task 1.2, OP-11,
OP-14, OP-15), written without the implementation.

Independence: written from the block 4 spec (sections 5.2, 6.3 O2 and O2b, 7) and the dataset only. The total and band
come from the oracle (`block4.Oracle`), the limit from `setting.csv` (`approval_otp_above`), the director from the
profile's approver roles. Codes are read from the mail caught in this process.

O2 on a fresh Apex reset: Q(500 kg, 905.00) gives 5,33,950.00 in PO-03 for the director; only she sees
`approval.needs_code` true (code_above "500000.00"). Approving version 1 without a code is refused 409 and writes
nothing; the purchase manager and the buyer cannot ask for her code; she asks (200, sent to director@, 10 minutes),
again within 30 seconds is refused; the stored code is `po_approval` with ref `<po>:<version>:<round>:<step>`; a wrong
code is refused and leaves the step waiting. O2b: the buyer cuts to 480 kg (version 2, 5,12,592.00, still PO-03): her
version 1 code no longer approves ("ask for one"); a new code does: approved, `with_code` true, `otp.use` on
`po_approval:<ref>`. A second Q(500) order is rejected by her without a code. A Q(120) order (1,28,148.00) needs none:
`approval-code` is 409 "needs no code: it is not above Rs 5,00,000.00" and the purchase manager approves without one
(`with_code` false). Ends with a tenant check (Brightwave's director cannot ask a code for Apex's order; no mail).
Mutations: the code checked after the writes (the step approved on a wrong code); the code's ref without the version.
"""

from typing import Any

import block1 as b
import block4 as k
import env
import lib
from block1 import js
from lib import Outcome

ID = "E111"
TITLE = "Approving an order above Rs 5,00,000 needs a one-time code for that version"
REQUIREMENT = (
    "Task 1.2, D-07, OP-11, OP-14, OP-15 (block 4 spec 5.2, O2, O2b): needs_code = total with GST above "
    "approval_otp_above; approve without, with a wrong or another version's code is 409 and writes nothing; the code "
    "goes only to the person whose turn it is, ref <po>:<version>:<round>:<step>, resend gap 30 s; the right code "
    "approves (with_code true, otp.use); reject needs none; at or below the limit no code (409 'needs no code'); "
    "totals against the oracle and the truth file; tenant check"
)
PREFIX = "e111"


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
        oracle = k.Oracle(apex)
        m = k.Mutations()
        truth: list[dict[str, Any]] = []
        big = _o2(out, check, apex, oracle, m, truth)
        rejected = _reject_without_code(check, apex, oracle)
        small = _below_limit(check, apex, oracle, truth)
        out.metrics["truth rows compared"] = k.compare_truth(check, apex, truth)
        m.report(check, out, ["code_after_writes", "ref_no_version"])
        _foreign_code(check, apex, bw, big)
        k.tenant_check(check, apex, bw, [big, rejected, small], start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        "The 30-second resend gap is checked by asking twice in a row; the eval does not wait it out.",
        ("'Nothing written' on a refused approval is the order's audit rows, steps and status unchanged; the otp.wrong "
        "audit row the code service writes is on the code's own entity (po_approval:<ref>), not on the order."),
        "The buyer asking for the director's code may be 403 (no po.approve) or 409 (not her turn); both are accepted.",
    ]
    return out


def _ref(co: k.Co, po: str) -> tuple[str, dict[str, Any]]:
    """The spec's ref for the open step, from the order's own version, round and step."""
    pend = co.open_steps(po)
    st = pend[0] if pend else {}
    version = int(co.po_row(po).get("version") or 0)
    return k.code_ref(po, version, int(st.get("round") or 0), int(st.get("step") or 0)), st


def _o2(out: Outcome, check: b.Checker, co: k.Co, oracle: k.Oracle, m: k.Mutations, truth: list) -> str:
    po = co.quotation_order(check, 500, "905.00", "O2")
    if not po:
        return ""
    supplier = str(co.po_row(po).get("supplier_code"))
    director, pm, buyer = oracle.person("director"), oracle.person("purchase_manager"), co.buyer()
    v1 = oracle.snap(1, 500, "905.00", k.NEED_BY, supplier)
    k.check_amounts(check, co, oracle, po, 500, "905.00", "O2 v1")
    rule, who = oracle.approver(v1.total)
    row = co.po_row(po)
    pend = co.open_steps(po)
    check(row.get("rule_code") == rule and who == director and len(pend) == 1 and co.code_of(pend[0].get("approver")) == director
          and pend[0].get("version") == 1,
          f"O2: {po} in {row.get('rule_code')}, open steps {[(s.get('approver'), s.get('version')) for s in pend]}; oracle "
          f"{rule}, {director} with version 1")  # fmt: skip
    for person, want in ((director, oracle.needs_code(v1.total)), (pm, False), (buyer, False)):
        seen = co.detail(po, person).get("approval") or {}
        check(seen.get("needs_code") is want, f"O2: {person} sees needs_code {seen.get('needs_code')}, expected {want}")
        if person == director:
            check(k.D(seen.get("code_above")) == oracle.code_limit(), f"O2: code_above {seen.get('code_above')}, expected "
                  f"{oracle.code_limit()}", "medium")  # fmt: skip
    truth.append(k.truth_row("O2", v1, oracle, approver=director))

    # no code: refused, nothing written
    co.headers(director)
    before = k.state_of(co, po)
    r = co.approve_po(director, po, 1)
    check(r.status_code == 409 and "one-time code" in r.text.lower() and k.state_of(co, po) == before,
          f"O2: approving without a code answered {r.status_code} {r.text[:160]}; state {before} -> {k.state_of(co, po)}")  # fmt: skip

    # only the person whose turn it is gets a code
    for person in (pm, buyer):
        since = len(co.box.sent)
        r = co.approval_code(person, po)
        check(r.status_code in (403, 409) and not co.mails(since),
              f"O2: {person} asking for a code on {po} answered {r.status_code} and sent {len(co.mails(since))} mail(s)")  # fmt: skip
    since = len(co.box.sent)
    r = co.approval_code(director, po)
    body = js(r)
    c1, found = co.code_mail(co.login_email(director), since)
    check(r.status_code == 200 and str(body.get("sent_to", "")).lower() == co.login_email(director).lower()
          and body.get("expires_minutes") == 10 and c1 is not None and len(found) == 1,
          f"O2: the director asking for a code answered {r.status_code} {body}; mails {[x.get('to') for x in found]}")  # fmt: skip
    check(bool(found) and str(found[0].get("subject", "")) == f"Your code to approve purchase order {po}",
          f"O2: the code mail's subject is {found[0].get('subject') if found else None!r}", "medium")  # fmt: skip
    again = co.approval_code(director, po)
    check(again.status_code == 409, f"O2: asking again within 30 seconds answered {again.status_code}")
    ref1, st1 = _ref(co, po)
    stored = co.qd("select * from one_time_code where purpose = 'po_approval' and user_id = :u order by id",
                   u=co.user_id(director))  # fmt: skip
    got_ref = str(stored[-1].get("ref")) if stored else None
    m.case("ref_no_version", "O2 the stored code's ref", got_ref, ref1,
           k.code_ref(po, 1, int(st1.get("round") or 0), int(st1.get("step") or 0), k.Wrong(ref_no_version=True)))
    check(got_ref == ref1, f"O2: the code is stored with ref {got_ref}, expected {ref1} (<po>:<version>:<round>:<step>)")

    # a wrong code: refused before anything is written
    if c1:
        wrong = "000000" if c1 != "000000" else "999999"
        before = k.state_of(co, po)
        r = co.approve_po(director, po, 1, wrong)
        step_now = (co.open_steps(po) or [{}])[0].get("status")
        m.case("code_after_writes", "O2 wrong code", step_now, "pending", "approved")
        check(r.status_code == 409 and k.state_of(co, po) == before,
              f"O2: a wrong code answered {r.status_code}; state {before} -> {k.state_of(co, po)}; expected 409, nothing "
              "written")  # fmt: skip

    # O2b: the buyer cuts to 480 kg; the version 1 code no longer approves
    r = co.revise(po, qty=480)
    v2 = oracle.snap(2, 480, "905.00", k.NEED_BY, supplier)
    k.check_amounts(check, co, oracle, po, 480, "905.00", "O2b v2")
    ref2, st2 = _ref(co, po)
    check(r.status_code == 200 and oracle.band(v2.total)["rule_code"] == rule and co.code_of(st2.get("approver")) == director
          and st2.get("version") == 2,
          f"O2b: after the cut {r.status_code}; open step {st2}; expected the director with version 2 ({rule})")  # fmt: skip
    truth.append(k.truth_row("O2b", v2, oracle, approver=director, reasons=[]))
    if c1:
        before = k.state_of(co, po)
        r = co.approve_po(director, po, 2, c1)
        check(r.status_code == 409 and "ask for one" in r.text.lower() and k.state_of(co, po) == before,
              f"O2b: the version 1 code on version 2 answered {r.status_code} {r.text[:160]}; expected 409 'ask for one'")  # fmt: skip
    since = len(co.box.sent)
    r = co.approval_code(director, po)
    c2, _ = co.code_mail(co.login_email(director), since)
    check(r.status_code == 200 and c2 is not None, f"O2b: a new code answered {r.status_code}")
    stored = co.qd("select ref from one_time_code where purpose = 'po_approval' and user_id = :u order by id desc limit 1",
                   u=co.user_id(director))  # fmt: skip
    check(bool(stored) and stored[0]["ref"] == ref2, f"O2b: the new code's ref {stored}, expected {ref2}")
    mark = int(co.one("select coalesce(max(id), 0) from audit_event") or 0)
    if c2 and k.approve_ok(check, co, director, po, 2, "O2b approve version 2 with the code", code=c2):
        a = co.audits("po.approve", po, mark)
        after = a[-1]["after"] if a else {}
        check(after.get("with_code") is True and after.get("version") == 2,
              f"O2b: audit po.approve after {after}; expected with_code true, version 2")  # fmt: skip
        used = co.qd("select * from audit_event where action = 'otp.use' and entity_id = :e and id > :i",
                     e=f"po_approval:{ref2}", i=mark)  # fmt: skip
        check(len(used) == 1, f"O2b: {len(used)} otp.use rows on po_approval:{ref2}, expected one")
        snaps = co.approved_versions(po)
        check(co.po_row(po).get("approved_version") == 2 and [int(s["version"]) for s in snaps] == [2],
              f"O2b: approved_version {co.po_row(po).get('approved_version')}, snapshots "
              f"{[s.get('version') for s in snaps]}; expected 2 and [2]")  # fmt: skip
        if snaps:
            k.check_snapshot(check, co, po, snaps[0], v2, director, "O2b")
    out.metrics["O2 / O2b totals"] = f"{v1.total} / {v2.total}"
    return po


def _reject_without_code(check: b.Checker, co: k.Co, oracle: k.Oracle) -> str:
    po = co.quotation_order(check, 500, "905.00", "O2 reject")
    if not po:
        return ""
    director = oracle.person("director")
    since = len(co.box.sent)
    r = co.reject_po(director, po, 1)
    codes = [x for x in co.mails(since) if "code" in str(x.get("subject", "")).lower()]
    check(r.status_code == 200 and js(r).get("status") == "cancelled" and not codes,
          f"O2: the director rejecting {po} (above the limit) without a code answered {r.status_code} {r.text[:160]}; "
          "expected 200 cancelled with no code (OP-11)")  # fmt: skip
    return po


def _below_limit(check: b.Checker, co: k.Co, oracle: k.Oracle, truth: list) -> str:
    po = co.quotation_order(check, 120, "905.00", "O2 small")
    if not po:
        return ""
    pm = oracle.person("purchase_manager")
    v1 = oracle.snap(1, 120, "905.00", k.NEED_BY, str(co.po_row(po).get("supplier_code")))
    seen = co.detail(po, pm).get("approval") or {}
    check(seen.get("needs_code") is False and oracle.needs_code(v1.total) is False,
          f"O2: {po} ({v1.total}) shows needs_code {seen.get('needs_code')} to {pm}; expected false")  # fmt: skip
    since = len(co.box.sent)
    r = co.approval_code(pm, po)
    check(r.status_code == 409 and "needs no code" in r.text.lower() and not co.mails(since),
          f"O2: a code for {po} answered {r.status_code} {r.text[:160]}; expected 409 'needs no code', no mail")  # fmt: skip
    check("5,00,000.00" in r.text, f"O2: the refusal does not name the limit Rs 5,00,000.00: {r.text[:160]}", "low")
    mark = int(co.one("select coalesce(max(id), 0) from audit_event") or 0)
    if k.approve_ok(check, co, pm, po, 1, "O2 approve 1,28,148.00 without a code"):
        a = co.audits("po.approve", po, mark)
        check(bool(a) and a[-1]["after"].get("with_code") is False, f"O2: audit po.approve after "
              f"{a[-1]['after'] if a else None}; expected with_code false")  # fmt: skip
    truth.append(k.truth_row("O2", v1, oracle, approver=pm))
    return po


def _foreign_code(check: b.Checker, apex: k.Co, bw: k.Co, po: str) -> None:
    """Brightwave's CFO (its top approver) cannot ask a code for Apex's order: 404 or 409, and no mail."""
    if not po:
        return
    cfo = next(c for c, u in sorted(bw.people.items()) if "Chief Financial Officer" in (u.get("designation") or ""))
    since = len(apex.box.sent)
    r = bw.approval_code(cfo, po)
    check(r.status_code in (403, 404, 409) and not apex.mails(since),
          f"tenant check: Brightwave's CFO asking a code for Apex's {po} answered {r.status_code} and sent "
          f"{len(apex.mails(since))} mail(s)")  # fmt: skip
