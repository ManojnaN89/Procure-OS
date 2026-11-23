"""E120: late bids are refused and a closing date can be extended (task 12.6, DF-33, G-8), written without the
implementation.

Independence: written from the block 6 spec (sections 5.1, 8.3 L1-L4, 9, 10.2) and the dataset only. The closing date
D is read from the RFQ and checked against the eval's own calendar (`location.csv` working days, the central and state
holiday files: 23 Nov 2026 + 5 working days, 24 Nov a holiday, Saturdays working = 30 Nov 2026); the extension mail is
written here from the spec's words and `supplier_contact.csv` / `company.csv`.

Given a fresh Apex reset on Mon 23 Nov 2026 and R (production asks for RM-08 500 KGS at PLT-CKN, need-by 21 Dec 2026,
approved, sourced; SUP-003, SUP-005, SUP-001 invited; the buyer confirms the send):
- L1: on D SUP-005 quotes 960.00 (accepted, `received_on` D); on D+1 (calendar) SUP-001's first quote 990.00 is refused
  409 "... closed on <D> ... (late bid)" with no quote row and no `quote.enter` audit; SUP-005's revision 950.00 is
  accepted (a negotiation round).
- L2: a mail from `sales@shreewires.example` stored on D and confirmed on D+1 becomes a quote with channel `email` and
  `received_on` D; a mail from `sales@deccancastings.example` stored on D+1 is not read (draft `needs_person`
  "arrived on <D+1>, after <rfq> closed on <D>: a late quote is not read", no model call) and its confirmation is 409.
- L3: extension refusals (no reason, a non-working day, a date not after the business date, a login without `rfq.send`)
  then the buyer extends to the third working day after D+1: `due_date` moved, `mailed` 3, one mail per invited
  supplier in the spec's words, audit `rfq.extend`; SUP-001's 990.00 is then accepted.
- L4: a second R sourced on 23 Nov and confirmed on pinned 30 Nov gets `due_date` = 30 Nov + 5 working days (5 Dec).
Ends with a tenant check (Brightwave cannot read or extend Apex's RFQs; no mail).
Mutations: the due date ignored (a late first quote accepted); `received_on` taken from the confirm day.
"""

from datetime import date, timedelta
from typing import Any

import aif
import block1 as b
import block4 as k
import block6 as s
import env
import lib
from block1 import js
from lib import Outcome

ID = "E120"
TITLE = "Late bids are refused, a revision after closing is a negotiation round, and a closing date can be extended"
REQUIREMENT = (
    "Task 12.6, DF-33 (block 6 spec 5.1): a first quote received after the closing date refused 409 with nothing "
    "written; a revision of an on-time supplier accepted; an emailed quote dated by the day the mail was stored, a late "
    "mail not read; the extension (rfq.send, reason, a later working day) mails every invited supplier in fixed words; "
    "the first send sets a fair closing date; tenant check"
)
PREFIX = "e120"


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
        model = aif.Model(default=s.quote_answer(s.WIRE, "975.00"))
        _, rfq = apex.r_rfq(check, "R")
        req2, rfq2 = apex.r_rfq(check, "L4", confirm=False)
        if rfq:
            d = _closing(out, check, apex, oracle, rfq)
            if d:
                _l1_l2(out, check, apex, oracle, m, model, rfq, d)
                _l3(out, check, apex, oracle, rfq, d, bw)
        if rfq2:
            _l4(out, check, apex, oracle, m, rfq2)
        m.report(check, out, ["due_date_ignored", "received_on_confirm_day"])
        s.f.tenant_check(check, apex, bw, [rfq, rfq2], [], start)
        s.tenant_tasks(check, apex, bw, [rfq, rfq2])
        out.metrics["RFQs"] = {"R": rfq, "L4": rfq2, "L4 request": req2}
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        "Spec gap: the late refusal's exact words are checked as medium (closing date and 'late bid'); the 409 as high.",
        "Spec gap: the extension mail may sign with the company's legal or short name; both are accepted.",
        (
            "The emailed quotes are stored through the mailbox's own intake (emails.ingest) on the pinned business date "
            "and read by the worker with a scripted model (the platform's reading step and its checks are unchanged)."
        ),
    ]
    return out


def _closing(out: Outcome, check: b.Checker, co: s.Co6, oracle: s.Oracle6, rfq: str) -> date | None:
    d = co.due(rfq)
    want = oracle.due_from(s.TODAY)
    out.metrics["D (closing date)"] = {"stored": str(d), "oracle": str(want)}
    check(d == want, f"R: {rfq} closes on {d}; the oracle says 23 Nov 2026 + 5 working days on the company calendar = "
          f"{want}", "medium")  # fmt: skip
    sends = co.since("rfq.send", 0)
    mine = [a for a in sends if a.get("entity_id") == rfq]
    after = mine[-1]["after"] if mine else {}
    check(bool(mine) and str(want) in str(after), f"R: the rfq.send audit of {rfq} does not carry the closing date "
          f"{want} (before and after): {str(after)[:200]}", "medium")  # fmt: skip
    return d


def _l1_l2(out: Outcome, check: b.Checker, co: s.Co6, oracle: s.Oracle6, m: k.Mutations, model: aif.Model, rfq: str,
           d: date) -> None:  # fmt: skip
    notes: list[str] = []
    d1 = d + timedelta(days=1)
    # ---- on D: SUP-005 types 960.00; SUP-003's mail is stored
    with s.pinned(d):
        r = co.quote6(rfq, "SUP-005", "960.00")
        check(r.status_code == 200, f"L1: SUP-005's quote on the closing day {d} answered {r.status_code} {r.text[:200]}")
        check(js(r).get("channel") in ("entered", None), f"L1: the quote's answer gives channel {js(r).get('channel')}",
              "low")  # fmt: skip
        q = co.quotes(rfq, "SUP-005")
        check(bool(q) and b.day(q[0].get("received_on")) == d, f"L1: SUP-005's quote received_on "
              f"{q[0].get('received_on') if q else None}; expected {d}", "medium")  # fmt: skip
        model.default = s.quote_answer(s.WIRE, "975.00")
        shree, shree_drafts = co.mail_in(rfq, oracle.contact_email("SUP-003"), _body(rfq, "975.00"), model, notes=notes)
        check(shree is not None and bool(shree_drafts), f"L2: the mail from SUP-003 stored on {d} gave no draft "
              f"(email {shree})")  # fmt: skip
        check(b.day(co.email_row(shree).get("received_on")) == d, f"L2: the mail's received_on is "
              f"{co.email_row(shree).get('received_on')}; expected the day it was stored, {d}", "medium")  # fmt: skip
    # ---- on D+1 (calendar day): SUP-001's first quote is late; SUP-005 revises; SUP-003's draft is confirmed
    with s.pinned(d1):
        audit0 = co.last_audit_id()
        r = co.quote6(rfq, "SUP-001", "990.00")
        m.case("due_date_ignored", "L1: SUP-001's first quote on D+1", r.status_code, 409, 200)
        check(r.status_code == 409, f"L1: SUP-001's first quote on {d1}, after {rfq} closed on {d}, answered "
              f"{r.status_code} {r.text[:200]}; the spec says 409 (late bid)")  # fmt: skip
        words = s.norm(r.text)
        check(f"closed on {s.long_date(d).lower()}" in words and "late bid" in words, f"L1: the late refusal's words: "
              f"{r.text[:250]}; the spec says '{rfq} closed on {s.long_date(d)}: a quote received after the closing date "
              "is refused (late bid)'", "medium")  # fmt: skip
        check(not co.quotes(rfq, "SUP-001") and not co.since("quote.enter", audit0), f"L1: the refused late quote left "
              f"{len(co.quotes(rfq, 'SUP-001'))} quote row(s) and {len(co.since('quote.enter', audit0))} quote.enter "
              "audit row(s)")  # fmt: skip
        r = co.quote6(rfq, "SUP-005", "950.00")
        check(r.status_code == 200, f"L1: SUP-005's revision 950.00 after closing (it quoted on time) answered "
              f"{r.status_code} {r.text[:200]}; the spec says accepted (a negotiation round)")  # fmt: skip
        revs = [int(x.get("revision") or 0) for x in co.quotes(rfq, "SUP-005")]
        check(len(revs) == 2 and max(revs) == min(revs) + 1, f"L1: SUP-005's quotes have revisions {revs}, expected "
              "two in a row", "medium")  # fmt: skip
        # L2: SUP-003's draft from D confirmed on D+1
        draft = shree_drafts[-1] if shree_drafts else {}
        check(draft.get("status") not in ("needs_person",), f"L2: the on-time mail's draft is {draft.get('status')} "
              f"with {s.text_of(draft.get('problems'))[:200]}; the spec reads it", "medium")  # fmt: skip
        r = co.confirm_draft(draft.get("id")) if draft else None
        check(r is not None and r.status_code == 200, f"L2: confirming the on-time mail's draft on {d1} answered "
              f"{getattr(r, 'status_code', None)} {getattr(r, 'text', '')[:200]}")  # fmt: skip
        q = co.quotes(rfq, "SUP-003")
        got_on = b.day(q[-1].get("received_on")) if q else None
        m.case("received_on_confirm_day", "L2: SUP-003's emailed quote confirmed on D+1", got_on, d, d1)
        check(bool(q) and got_on == d and q[-1].get("channel") == "email", f"L2: SUP-003's quote: received_on {got_on}, "
              f"channel {q[-1].get('channel') if q else None}; expected {d} (the day the mail was stored) and email")  # fmt: skip
        check(bool(q) and int(q[-1].get("email_id") or 0) == int(shree or -1), f"L2: the quote's email_id "
              f"{q[-1].get('email_id') if q else None}; expected {shree}", "medium")  # fmt: skip
        # L2: SUP-001's mail stored on D+1 is late and is not read
        calls0, mark = model.calls, co.model_mark()
        model.default = s.quote_answer(s.WIRE, "990.00")
        deccan, late = co.mail_in(rfq, oracle.contact_email("SUP-001"), _body(rfq, "990.00"), model, notes=notes)
        draft = late[-1] if late else {}
        problems = s.text_of(draft.get("problems"))
        want = f"arrived on {s.long_date(d1)}, after {rfq} closed on {s.long_date(d)}: a late quote is not read".lower()
        check(draft.get("status") == "needs_person", f"L2: the late mail's draft is {draft.get('status')!r} "
              f"(email {deccan}); the spec says needs_person")  # fmt: skip
        check(want in problems, f"L2: the late mail's problem is {problems[:250]}; the spec says '{want}'", "medium")
        s.no_model_call(check, co, model, calls0, mark, "L2: the late mail")
        if draft:
            r = co.confirm_draft(draft.get("id"))
            check(r.status_code == 409, f"L2: confirming the late mail's draft answered {r.status_code}; expected 409")
            check(not co.quotes(rfq, "SUP-001"), "L2: the late mail became a quote of SUP-001")
    out.notes += sorted(set(notes))


def _l3(out: Outcome, check: b.Checker, co: s.Co6, oracle: s.Oracle6, rfq: str, d: date, bw: s.Co6) -> None:
    d1 = d + timedelta(days=1)
    with s.pinned(d1):
        new = oracle.next_working(d1, 3)
        sunday = oracle.non_working_after(d1)
        refused = {
            "no reason": co.extend(rfq, new, ""),
            f"a non-working day ({sunday})": co.extend(rfq, sunday, "eval: more time"),
            f"not after the business date ({d1})": co.extend(rfq, d1, "eval: more time"),
        }
        for what, r in refused.items():
            check(r.status_code == 422, f"L3: extending {rfq} with {what} answered {r.status_code} {r.text[:150]}; the "
                  "spec says 422", "medium")  # fmt: skip
        r = co.extend(rfq, new, "eval: no reason to refuse", by=co.login("production"))
        check(r.status_code == 403, f"L3: production (no rfq.send) extending {rfq} answered {r.status_code}", "medium")
        r = bw.call(bw.login("buyer"), "POST", f"/api/rfqs/{rfq}/extend", {"due_date": new.isoformat(), "reason": "x"})
        check(r.status_code in (403, 404) or bw.one("select count(*) from rfq where no = :n", n=rfq),
              f"tenant check: Brightwave's buyer extending Apex's {rfq} answered {r.status_code}")  # fmt: skip
        check(co.due(rfq) == d, f"L3: after the refusals {rfq} closes on {co.due(rfq)}, expected {d} unchanged")
        since = co.mark()
        audit0 = co.last_audit_id()
        r = co.extend(rfq, new, "eval: suppliers asked for two more days")
        body = js(r)
        check(r.status_code == 200 and co.due(rfq) == new, f"L3: extending {rfq} to {new} answered {r.status_code} "
              f"{r.text[:200]}; due_date now {co.due(rfq)}")  # fmt: skip
        check(str(body.get("due_date", ""))[:10] == new.isoformat() and str(body.get("mailed")) == "3",
              f"L3: the answer {body}; expected due_date {new} and mailed 3", "medium")  # fmt: skip
        mails = [x for x in co.mails(since) if "closing date extended" in str(x.get("subject", "")).lower()]
        to = sorted(str(x.get("to", "")).lower() for x in mails)
        want_to = sorted(oracle.contact_email(c) for c in ("SUP-001", "SUP-003", "SUP-005"))
        check(to == want_to, f"L3: extension mails went to {to}; expected one to each invited supplier {want_to}")
        for x in mails:
            sup = next((c for c in ("SUP-001", "SUP-003", "SUP-005") if oracle.contact_email(c) == str(x["to"]).lower()),
                       "SUP-001")  # fmt: skip
            subject, words = oracle.extension_words(rfq, new, sup)
            body_n = s.norm(x.get("body"))
            check(s.norm(subject) == s.norm(x.get("subject")), f"L3: the subject {x.get('subject')!r}; expected "
                  f"{subject!r}", "medium")  # fmt: skip
            check(s.norm(words) in body_n and any(s.norm(n) in body_n for n in oracle.company_names()),
                  f"L3: the mail to {x['to']} is not the spec's words:\n  got:  {str(x.get('body'))[:400]}\n  want: "
                  f"{words} <company>")  # fmt: skip
        rows = [a for a in co.since("rfq.extend", audit0) if a.get("entity_id") == rfq]
        after = rows[-1]["after"] if rows else {}
        check(bool(rows) and str(new) in str(after) and str(d) in str(after) and "two more days" in str(after) + str(
            rows[-1].get("reason")), f"L3: the rfq.extend audit row: {str(rows[-1] if rows else None)[:300]}; expected "
              "from, to and the reason", "medium")  # fmt: skip
        r = co.quote6(rfq, "SUP-001", "990.00")
        check(r.status_code == 200, f"L3: SUP-001's 990.00 after the extension answered {r.status_code} {r.text[:200]}")
        out.metrics["extension"] = {"from": str(d), "to": str(new), "mails": len(mails)}


def _l4(out: Outcome, check: b.Checker, co: s.Co6, oracle: s.Oracle6, m: k.Mutations, rfq: str) -> None:
    day = date(2026, 11, 30)
    made = co.due(rfq)
    with s.pinned(day):
        ok = co.confirm(co.login("buyer"), rfq)
        check(ok, f"L4: the buyer could not confirm sending {rfq} on {day}")
        want = oracle.due_from(day)
        got = co.due(rfq)
        m.case("due_date_ignored", "L4: closing date at a late first send", got, want, made)
        check(got == want, f"L4: {rfq} made on 23 Nov (closing {made}) and first sent on {day} closes on {got}; the "
              f"spec says at least {day} + 5 working days = {want}")  # fmt: skip
    out.metrics["L4 closing date"] = {"as made": str(made), "after the send": str(co.due(rfq)), "oracle": str(want)}


def _body(rfq: str, price: str) -> str:
    return (f"Dear Sir / Madam,\n\nThank you for your enquiry {rfq}. Our quotation is below.\n\n1. Copper winding wire "
            f"(RM-08) - 500 kgs\n   Rate: Rs {price} per kg\n   GST: 18%\n   Delivery: 5 days from the order\n\n"
            "This quotation is valid until 2026-12-31.\n\nRegards,\nSales Desk\n")  # fmt: skip
