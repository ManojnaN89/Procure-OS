"""E123: quotes come only from registered contacts, and the sender's authentication is read from our own server's
header only (task 1.3 quotes half, D53), written without the implementation.

Independence: written from the block 6 spec (sections 4, 8.3 S1-S5, 9, 10.2) and the dataset only. Registered contacts
are `supplier_contact.csv`; the verdicts follow spec 4.1 rule 1 as the eval reads it (dmarc=pass -> pass; dmarc=fail
-> fail; a header from any other server than `mail_authserv_id` is ignored -> none).

Given a fresh Apex reset on Mon 23 Nov 2026, two administrators set `mail_authserv_id` = mx.apexpumps.example, and R
(RM-08 500 KGS at PLT-CKN, three invitees, confirmed). Mails reach the RFQ's plus address and are stored by the
mailbox's own intake; the worker reads them with a scripted model.
- S1: from sales@punecopper.example (SUP-005's contact) with our server's header spf, dkim and dmarc pass: verdict pass
  on the email; the draft `pending`; the buyer confirms: quote channel `email`, `email_id`, `sender_address`,
  `contact_id` (SUP-005's contact row), `sender_auth.verdict` pass; audit `quote.enter` with channel and verdict.
- S2: from sales@shreewires.example with our server's header dmarc=fail: draft `needs_person` "sender authentication
  failed (dmarc=fail) for <address>: the email was not read", no model call, a task `quote_sender_check` for U03.
- S4: from quotes@punecopper.example (not registered): `needs_person` "... is not a registered contact of a supplier
  invited to <rfq> ...", no model call, a task. Also: SUP-005's `rfq_supplier.email` planted (as the owner) as
  bids@punecopper.example, a mail from it is not read either (the RFQ row is a copy, OP-13).
- S5: the buyer types SUP-003's quote: channel `entered`, `sender_auth` {"method": "login", "user": <buyer's id>}.
- S3: with `quote.read` set to automatic by two administrators, a mail from sales@deccancastings.example whose only
  header is `relay.other.example; dmarc=pass`: verdict none; it is read (one model call) and the draft carries the
  assumption "the sender could not be authenticated"; it is not confirmed by itself (no quote of SUP-001).
`GET /api/rfqs/{no}` lists each quote with channel, sender_address, sender_auth and received_on. Ends with a tenant
check.
Mutations: `rfq_supplier.email` still trusted; an untrusted header believed.
"""

from typing import Any

import aif
import block1 as b
import block4 as k
import block6 as s
import env
import lib
from block1 import js
from lib import Outcome

ID = "E123"
TITLE = "Quotes come only from registered contacts; sender authentication is read from our own server only"
REQUIREMENT = (
    "Task 1.3, D53 (block 6 spec 4): Authentication-Results trusted only from mail_authserv_id; pass read, fail and "
    "unknown senders not read and a quote_sender_check task; rfq_supplier.email alone identifies nobody; verdict none "
    "read but never confirmed without a person; each quote records channel, email, sender, contact and sender_auth; a "
    "typed quote records the buyer's login; tenant check"
)
PREFIX = "e123"


def _hdr(server: str, result: str, domain: str = "punecopper.example") -> str:
    if result == "pass":
        return (f"{server}; spf=pass smtp.mailfrom={domain}; dkim=pass header.d={domain}; dmarc=pass "
                f"header.from={domain}")  # fmt: skip
    return f"{server}; spf=pass smtp.mailfrom={domain}; dkim=none; dmarc=fail header.from={domain}"


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
        notes: list[str] = []
        apex.set_policy(check, "mail_authserv_id", s.AUTHSERV, "S1")
        _, rfq = apex.r_rfq(check, "R")
        if rfq:
            model = aif.Model(default=s.quote_answer(s.WIRE, "960.00"))
            _s1(out, check, apex, oracle, model, rfq, notes)
            _s2(check, apex, oracle, model, rfq, notes)
            _s4(check, apex, m, model, rfq, notes)
            _s5(check, apex, rfq)
            _s3(out, check, apex, oracle, m, model, rfq, notes)
            _detail(check, apex, rfq)
        m.report(check, out, ["rfq_supplier_email_trusted", "untrusted_header_believed"])
        s.f.tenant_check(check, apex, bw, [rfq], [], start)
        s.tenant_tasks(check, apex, bw, [rfq])
        out.notes += sorted(set(notes))
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        "Spec gap: `email_message.auth` and `quote.sender_auth` are read as JSON with a 'verdict' key (spec 4.1 rule 1).",
        (
            "S4's second half plants a copy address on rfq_supplier as the owner (the platform offers no way to write "
            "it): it is the case that tells 'rfq_supplier.email still trusted' from the right rule."
        ),
        "Spec gap: problems and assumptions may be strings or objects; their words are compared in lower case.",
    ]
    return out


def _verdict(value: Any) -> str | None:
    v = k.as_json(value)
    return str(v.get("verdict")) if isinstance(v, dict) and v.get("verdict") is not None else None


def _not_read(check: b.Checker, co: s.Co6, model: aif.Model, rfq: str, sender: str, words: str, label: str,
              auth: tuple[str, ...], notes: list[str], tasks_before: int) -> dict[str, Any]:  # fmt: skip
    calls0, mark = model.calls, co.model_mark()
    eid, drafts = co.mail_in(rfq, sender, "Our rate is Rs 960 per kg, GST 18%, 5 days.", model, auth=auth, notes=notes)
    d = drafts[-1] if drafts else {}
    check(d.get("status") == "needs_person", f"{label}: the draft of the mail from {sender} is {d.get('status')!r} "
          f"(email {eid}); the spec says needs_person")  # fmt: skip
    check(words.lower() in s.text_of(d.get("problems")), f"{label}: the draft's problems "
          f"{s.text_of(d.get('problems'))[:250]}; the spec says '{words}'", "medium")  # fmt: skip
    s.no_model_call(check, co, model, calls0, mark, label)
    tasks = co.tasks_of("quote_sender_check", rfq)
    check(len(tasks) > tasks_before and all(s.person_is(co, t.get("assignee"), "U03") for t in tasks[tasks_before:]),
          f"{label}: quote_sender_check tasks {len(tasks)} (before "
          f"{tasks_before}), owners {[co.code_of(t.get('assignee')) for t in tasks]}; the spec says a new one for U03")  # fmt: skip
    if d:
        r = co.confirm_draft(d.get("id"))
        check(r.status_code == 409, f"{label}: confirming the unread mail's draft answered {r.status_code}; expected 409")
    return d


def _s1(out: Outcome, check: b.Checker, co: s.Co6, oracle: s.Oracle6, model: aif.Model, rfq: str,
        notes: list[str]) -> None:  # fmt: skip
    sender = oracle.contact_email("SUP-005")
    model.default = s.quote_answer(s.WIRE, "960.00")
    audit0 = co.last_audit_id()
    eid, drafts = co.mail_in(rfq, sender, "Our rate is Rs 960 per kg, GST 18%, 5 days, valid to 31 Dec 2026.", model,
                             auth=(_hdr(s.AUTHSERV, "pass"),), notes=notes)  # fmt: skip
    email = co.email_row(eid)
    check(_verdict(email.get("auth")) == "pass", f"S1: the email's auth {email.get('auth')}; the spec says verdict pass")
    rec = [a for a in co.since("email.receive", audit0)]
    check(any(str(a["after"].get("auth_verdict")) == "pass" for a in rec), f"S1: the email.receive audit carries no "
          f"auth_verdict pass: {[a['after'] for a in rec][:2]}", "medium")  # fmt: skip
    d = drafts[-1] if drafts else {}
    check(d.get("status") == "pending", f"S1: the authenticated mail's draft is {d.get('status')!r} "
          f"({s.text_of(d.get('problems'))[:200]}); the spec says pending")  # fmt: skip
    if not d:
        return
    audit0 = co.last_audit_id()
    r = co.confirm_draft(d.get("id"))
    check(r.status_code == 200, f"S1: confirming the draft answered {r.status_code} {r.text[:200]}")
    q = co.quotes(rfq, "SUP-005")
    q = q[-1] if q else {}
    contact = co.one("select id from supplier_contact where lower(email) = :e and supplier_code = 'SUP-005'", e=sender)
    check(q.get("channel") == "email" and str(q.get("sender_address", "")).lower() == sender
          and int(q.get("email_id") or 0) == int(eid or -1), f"S1: the quote's channel {q.get('channel')}, sender "
          f"{q.get('sender_address')}, email {q.get('email_id')}; expected email, {sender}, {eid}")  # fmt: skip
    check(contact is not None and str(q.get("contact_id")) == str(contact), f"S1: the quote's contact_id "
          f"{q.get('contact_id')}; SUP-005's contact row is {contact}", "medium")  # fmt: skip
    check(_verdict(q.get("sender_auth")) == "pass", f"S1: the quote's sender_auth {q.get('sender_auth')}; the spec says "
          "verdict pass")  # fmt: skip
    ent = co.since("quote.enter", audit0)
    check(any(a["after"].get("channel") == "email" and str(a["after"].get("verdict")) == "pass" for a in ent),
          f"S1: the quote.enter audit carries channel/verdict {[a['after'] for a in ent][:2]}", "medium")  # fmt: skip
    out.metrics["S1 quote"] = {x: str(q.get(x)) for x in ("no", "channel", "sender_address", "contact_id", "sender_auth")}


def _s2(check: b.Checker, co: s.Co6, oracle: s.Oracle6, model: aif.Model, rfq: str, notes: list[str]) -> None:
    sender = oracle.contact_email("SUP-003")
    before = len(co.tasks_of("quote_sender_check", rfq))
    _not_read(check, co, model, rfq, sender, f"sender authentication failed (dmarc=fail) for {sender}: the email was not "
              "read", "S2", (_hdr(s.AUTHSERV, "fail", "shreewires.example"),), notes, before)  # fmt: skip
    t = co.tasks_of("quote_sender_check", rfq)
    check(bool(t) and float(t[-1].get("sla_hours") or 0) == 9, "S2: the task's SLA is not 9 working hours", "low")
    check(not co.quotes(rfq, "SUP-003"), "S2: the failed sender's mail became a quote of SUP-003")


def _s4(check: b.Checker, co: s.Co6, m: k.Mutations, model: aif.Model, rfq: str, notes: list[str]) -> None:
    sender = "quotes@punecopper.example"
    before = len(co.tasks_of("quote_sender_check", rfq))
    _not_read(check, co, model, rfq, sender, f"the sender {sender} is not a registered contact of a supplier invited to "
              f"{rfq}: the email was not read", "S4", (), notes, before)  # fmt: skip
    # the RFQ's own copy of an address identifies nobody (OP-13)
    copy = "bids@punecopper.example"
    with lib.owner_session(co.slug) as db:
        from sqlalchemy import text

        db.execute(text("update rfq_supplier set email = :e where rfq_no = :r and supplier_code = 'SUP-005'"),
                   {"e": copy, "r": rfq})  # fmt: skip
        db.commit()
    quotes0 = len(co.quotes(rfq, "SUP-005"))
    before = len(co.tasks_of("quote_sender_check", rfq))
    d = _not_read(check, co, model, rfq, copy, f"the sender {copy} is not a registered contact", "S4 (rfq_supplier "
                  "copy)", (), notes, before)  # fmt: skip
    m.case("rfq_supplier_email_trusted", "S4: a mail from the RFQ row's copy address", d.get("status"), "needs_person",
           "pending")  # fmt: skip
    check(len(co.quotes(rfq, "SUP-005")) == quotes0, "S4: the copy address's mail became a quote of SUP-005")


def _s5(check: b.Checker, co: s.Co6, rfq: str) -> None:
    r = co.quote6(rfq, "SUP-003", "975.00")
    check(r.status_code == 200, f"S5: the buyer typing SUP-003's quote answered {r.status_code} {r.text[:200]}")
    q = co.quotes(rfq, "SUP-003")
    q = q[-1] if q else {}
    auth = k.as_json(q.get("sender_auth")) or {}
    uid = co.user_id(co.login("buyer"))
    check(q.get("channel") == "entered" and isinstance(auth, dict) and auth.get("method") == "login"
          and str(auth.get("user")) == str(uid), f"S5: the typed quote's channel {q.get('channel')}, sender_auth {auth}; "
          f"the spec says entered and {{'method': 'login', 'user': {uid}}}")  # fmt: skip


def _s3(out: Outcome, check: b.Checker, co: s.Co6, oracle: s.Oracle6, m: k.Mutations, model: aif.Model, rfq: str,
        notes: list[str]) -> None:  # fmt: skip
    asked, done = co.automation("quote.read", "auto")
    auto = asked.status_code == 202 and done is not None and done.status_code == 200
    check(auto, f"S3: quote.read could not be set to automatic ({asked.status_code} "
          f"{getattr(done, 'status_code', None)}); the case 'never confirmed without a person' is then weaker", "low")  # fmt: skip
    sender = oracle.contact_email("SUP-001")
    model.default = s.quote_answer(s.WIRE, "990.00")
    calls0 = model.calls
    eid, drafts = co.mail_in(rfq, sender, "Our rate is Rs 990 per kg, GST 18%, 5 days, valid to 31 Dec 2026.", model,
                             auth=("relay.other.example; dmarc=pass header.from=deccancastings.example",),
                             notes=notes)  # fmt: skip
    verdict = _verdict(co.email_row(eid).get("auth"))
    m.case("untrusted_header_believed", "S3: only a foreign server's header", verdict, "none", "pass")
    check(verdict == "none", f"S3: the email's verdict {verdict!r}; a header from relay.other.example is not trusted, "
          "the spec says none")  # fmt: skip
    d = drafts[-1] if drafts else {}
    check(model.calls > calls0, "S3: the mail with verdict none was not read (no model call); the spec reads it")
    check("could not be authenticated" in s.text_of(d.get("assumptions")) + s.text_of(d.get("problems")),
          f"S3: the draft carries no assumption 'the sender could not be authenticated': "
          f"{s.text_of(d.get('assumptions'))[:200]}", "medium")  # fmt: skip
    confirmed = bool(co.quotes(rfq, "SUP-001")) or d.get("status") == "confirmed"
    m.case("untrusted_header_believed", "S3: automatic quote.read with verdict none", confirmed, False, True)
    check(not confirmed, f"S3: the unauthenticated mail was confirmed without a person (draft {d.get('status')}, "
          f"quote.read automatic {auto})")  # fmt: skip
    out.metrics["S3"] = {"verdict": verdict, "draft": d.get("status"), "quote.read automatic": auto}


def _detail(check: b.Checker, co: s.Co6, rfq: str) -> None:
    body = js(co.call(co.login("buyer"), "GET", f"/api/rfqs/{rfq}"))
    quotes = [q for q in body.get("quotes") or [] if isinstance(q, dict)]
    keys = ("quote_no", "supplier_code", "revision", "channel", "sender_address", "sender_auth", "received_on")
    ok = bool(quotes) and all(all(x in q for x in keys) for q in quotes)
    check(ok, f"GET /api/rfqs/{rfq}: quotes {[sorted(q) for q in quotes][:2]}; the spec adds {list(keys)}", "medium")
    by = {str(q.get("supplier_code")): q for q in quotes}
    check(by.get("SUP-003", {}).get("channel") == "entered" and by.get("SUP-005", {}).get("channel") == "email",
          f"GET /api/rfqs/{rfq}: channels {[(c, q.get('channel')) for c, q in by.items()]}", "medium")  # fmt: skip
