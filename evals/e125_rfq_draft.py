"""E125: the RFQ draft says why these suppliers, and a stale draft is never sent (task 11b.2, D42, D54), written
without the implementation.

Independence: written from the block 6 spec (sections 6.2, 8.3 R and D1, 9, 10.2) and the dataset only. Who is invited
and in which order comes from `block5.Oracle5.invitees` (approved for RAW, active, prices on file first then by code:
SUP-003, SUP-005, SUP-001); names from `supplier.csv`, the place from `location.csv`, the closing date from the eval's
calendar; the template is written here from the spec's words (the 'left out' notes and the min-quotes rule words are
not fixed by the spec and are read as any words).

Given a fresh Apex reset on Mon 23 Nov 2026 and R before confirmation (RM-08 500 KGS at PLT-CKN, approved, sourced):
- the RFQ waits with a pending `rfq.send` proposal and an `rfq_draft` recommendation (capability sourcing, key the RFQ,
  linked to the proposal, `directed_by` null) whose summary is "<rfq> asks 3 suppliers for 1 item delivered to Plant,
  Chakan (Pune), replies by <D>: <names in order>. Left out: <...>. At least 3 quotes are wanted (<rule>)."
- D1: SUP-001 is blocked through the supplier status proposal and the director's approval; the buyer confirms the
  send: 409 "the RFQ draft is stale: ...", nothing mailed, the RFQ still a draft, a fresh `rfq_draft` written (the old
  one stale). Confirming again sends: SUP-003 and SUP-005 mailed, SUP-001 not, its `send_error` "not sent: ...".
Ends with a tenant check.
Mutations: a send without the re-check (SUP-001 mailed); a stale draft confirmed.
"""

import re
from typing import Any

import block1 as b
import block4 as k
import block6 as s
import env
import lib
from lib import Outcome

ID = "E125"
TITLE = "The RFQ draft says why these suppliers, and a stale draft is never sent"
REQUIREMENT = (
    "Task 11b.2, D42 (block 6 spec 6.2): an rfq_draft record linked to the rfq.send proposal in the spec's words; its "
    "basis revalidated at confirmation (a supplier no longer eligible -> 409 stale and a fresh draft); send_rfq checks "
    "each invitee again and does not mail one no longer eligible (send_error); tenant check"
)
PREFIX = "e125"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        s.pin(lib.TODAY)
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
        bw = s.Co6("brightwave", PREFIX, client, box)
        apex = s.Co6("apex", PREFIX, client, box)
        s.pin(s.TODAY)
        apex.work()
        start = apex.mark()
        oracle = s.Oracle6(apex)
        m = k.Mutations()
        _, rfq = apex.r_rfq(check, "R", confirm=False)
        if rfq:
            first = _draft(out, check, apex, oracle, rfq)
            _d1(out, check, apex, oracle, m, rfq, first)
        m.report(check, out, ["send_without_recheck", "stale_draft_confirmed"])
        s.f.tenant_check(check, apex, bw, [rfq], [], start)
        s.tenant_tasks(check, apex, bw, [rfq])
        theirs = bw.qd("select id from recommendation where business_key = :k", k=rfq)
        check(not theirs or bool(bw.one("select count(*) from rfq where no = :n", n=rfq)), "tenant check: Brightwave "
              f"holds a recommendation keyed {rfq}")  # fmt: skip
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        "Spec gap: the separator between invitee names is not fixed; the names are checked in order.",
        (
            "Spec gap: 'suspended' (spec 8.3 D1) is not a supplier status of the data dictionary (draft, active, blocked); "
            "the eval blocks SUP-001 through the existing supplier status proposal."
        ),
    ]
    return out


def _draft(out: Outcome, check: b.Checker, co: s.Co6, oracle: s.Oracle6, rfq: str) -> dict[str, Any]:
    row = co.rfq(rfq)
    prop = co.proposal(row.get("proposal_id"))
    check(row.get("status") == "draft" and prop.get("action") == "rfq.send" and prop.get("status") == "pending",
          f"R: {rfq} is {row.get('status')} with proposal {prop.get('action')} {prop.get('status')}; expected a draft "
          "waiting for rfq.send")  # fmt: skip
    rec = co.live_rec("rfq_draft", rfq)
    if not check(bool(rec), f"R: no rfq_draft recommendation keyed {rfq}"):
        return {}
    linked = rec.get("proposal_id") == row.get("proposal_id") or str(rec.get("id")) in str(prop.get("payload"))
    check(linked, f"R: rfq_draft {rec.get('id')} is not linked to the rfq.send proposal {row.get('proposal_id')}")
    check(rec.get("capability") == "sourcing" and rec.get("directed_by") is None, f"R: the record's capability "
          f"{rec.get('capability')}, directed_by {rec.get('directed_by')}; expected sourcing and none (OP-19)", "medium")  # fmt: skip
    d = co.due(rfq)
    want_d = oracle.due_from(s.TODAY)
    check(d == want_d, f"R: {rfq} closes on {d}; oracle {want_d}", "medium")
    invitees = oracle.invitees("RAW", [s.WIRE], s.LOC, s.TODAY, 3)
    names = [oracle.supplier_name(c) for c in invitees]
    head = (f"{rfq} asks 3 suppliers for 1 item delivered to {oracle.location_name(s.LOC)}, replies by "
            f"{s.long_date(want_d)}:")  # fmt: skip
    summary = s.norm(rec.get("summary"))
    pattern = re.escape(s.norm(head)) + r" (.*?)\. left out: (.*)\. at least 3 quotes are wanted \((.+)\)\.?$"
    found = re.match(pattern, summary)
    check(found is not None, f"R: the rfq_draft summary is not the spec's words:\n  got:  {rec.get('summary')}\n  want: "
          f"{head} <names>. Left out: <notes>. At least 3 quotes are wanted (<rule>).")  # fmt: skip
    if found:
        where = [found.group(1).find(n.lower()) for n in names]
        check(all(x >= 0 for x in where) and where == sorted(where), f"R: the names in the summary "
              f"{found.group(1)!r}; expected in order {names}")  # fmt: skip
    missing = [c for c in invitees if c not in str(rec["engine_figures"])]
    check(not missing, f"R: the record's figures do not name the invitees {missing}", "medium")
    out.metrics["rfq_draft summary"] = rec.get("summary")
    return rec


def _d1(out: Outcome, check: b.Checker, co: s.Co6, oracle: s.Oracle6, m: k.Mutations, rfq: str,
        first: dict[str, Any]) -> None:  # fmt: skip
    if not co.block_supplier(check, "SUP-001", "D1: quality hold"):
        return
    buyer = co.login("buyer")
    since = co.mark()
    pid = co.rfq(rfq).get("proposal_id")
    r = co.call(buyer, "POST", f"/api/admin/proposals/{pid}/approve", {"note": "eval: confirm"})
    co.work()
    m.case("stale_draft_confirmed", "D1: confirming after SUP-001 was blocked", r.status_code, 409, 200)
    check(r.status_code == 409, f"D1: confirming {rfq} after SUP-001 was blocked answered {r.status_code} "
          f"{r.text[:200]}; the spec says 409 (stale RFQ draft)")  # fmt: skip
    check("the rfq draft is stale" in s.norm(r.text), f"D1: the refusal's words {r.text[:200]}", "medium")
    check(co.rfq(rfq).get("status") == "draft" and not co.supplier_mails(since), f"D1: after the refusal {rfq} is "
          f"{co.rfq(rfq).get('status')} and {len(co.supplier_mails(since))} supplier mail(s) went")  # fmt: skip
    fresh = co.live_rec("rfq_draft", rfq)
    check(bool(fresh) and fresh.get("id") != first.get("id"), f"D1: no fresh rfq_draft after the stale one "
          f"(live {fresh.get('id')}, first {first.get('id')})", "medium")  # fmt: skip
    old = co.recommendation(first.get("id"))
    check(old.get("status") in ("stale", "superseded") or old.get("superseded_by"), f"D1: the first rfq_draft is "
          f"{old.get('status')}; expected stale", "medium")  # fmt: skip
    ok = co.confirm(buyer, rfq)
    check(ok, f"D1: confirming {rfq} again did not send it ({co.rfq(rfq).get('status')})")
    mailed = sorted(str(x.get("to", "")).lower() for x in co.supplier_mails(since))
    m.case("send_without_recheck", "D1: the send after the block", oracle.contact_email("SUP-001") in mailed, False,
           True)  # fmt: skip
    want = sorted(oracle.contact_email(c) for c in ("SUP-003", "SUP-005"))
    check(mailed == want, f"D1: the send mailed {mailed}; the spec says {want} (SUP-001 is blocked)")
    row = next((x for x in co.invited(rfq) if x["supplier_code"] == "SUP-001"), {})
    check(str(row.get("send_error") or "").lower().startswith("not sent:"), f"D1: SUP-001's send_error "
          f"{row.get('send_error')!r}; the spec says 'not sent: <why>'", "medium")  # fmt: skip
    out.metrics["D1"] = {"first confirm": r.status_code, "mailed": mailed, "SUP-001 send_error": row.get("send_error")}
