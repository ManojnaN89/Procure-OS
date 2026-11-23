"""E112: a request for quotation is never sent without the buyer's confirmation (task 1.4, D42), written without the
implementation.

Independence: written from the block 5 spec (sections 4.1, 9, 10.2) and the dataset only. Who is invited and how many
come from `block5.Oracle5` over the dataset files; mails are read from the mail caught in this process.

On fresh Apex and Brightwave resets, business date Mon 23 Nov 2026. The administrator cannot set `rfq.send` to auto or
off (422); the automation list shows it locked with levels ["propose"] and the D42 note. A stored `auto` row (planted as
the owner, as a company that had set it before the migration) changes nothing: the gate refuses, and an operational RFQ
made from an approved request (production asks for 120 kg of copper wire, the director approves, the worker sources)
waits in draft with a pending `rfq.send` proposal and no mail. P1's period RFQ waits the same way. `send_rfq` called
straight on either is refused with no mail, no invitation and no `rfq.send` audit row. confirm-send: 403 for a login
without `rfq.send`, 422 for none or 51; the buyer confirms both RFQs (and an unknown number, refused alone) in one call:
both sent, one mail per invited supplier, `confirmed_by` the buyer, `confirmed_via` bulk, audit `rfq.confirm`. An
unconfirmed RFQ made to look sent and aged gets no reminder while a confirmed one aged the same does. Ends with a tenant
check (Brightwave's buyer cannot read or confirm Apex's RFQs; no mail).
Mutations: the gate still allows auto; a send path without the confirmation check.
"""

from typing import Any

import block1 as b
import block4 as k
import block5 as f
import env
import lib
from block1 import js
from lib import Outcome
from sqlalchemy import text

ID = "E112"
TITLE = "A request for quotation is never sent without the buyer's confirmation (D42)"
REQUIREMENT = (
    "Task 1.4, D42 (block 5 spec 4.1): rfq.send locked to propose (API 422, list locked, a stored auto row ignored); "
    "send_rfq refuses an unconfirmed RFQ with no mail and no rfq.send audit; operational and period RFQs wait for the "
    "buyer; confirm-send sends several at once (bulk, confirmed_by, one failure alone), 403 without rfq.send, 422 for "
    "none or more than 50; reminders skip unconfirmed RFQs; tenant check"
)
PREFIX = "e112"
LOCKED_WORDS = "is always decided by a person"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        f.pin(lib.TODAY)
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
        bw = f.Co5("brightwave", PREFIX, client, box)
        apex = f.Co5("apex", PREFIX, client, box)
        f.start(apex)
        start = apex.mark()
        m = k.Mutations()
        _lock(check, apex, m)
        op = _operational(check, apex, m)
        p1 = f.p1_create(check, apex)
        for no, label in ((op, "operational"), (p1, "P1 period")):
            _waits(check, apex, no, label, start)
            _send_refused(check, apex, m, no, label)
        _confirm(out, check, apex, [x for x in (op, p1) if x])
        _reminders(check, apex, p1)
        m.report(check, out, ["gate_auto", "send_unconfirmed"])
        f.tenant_check(check, apex, bw, [op, p1], [], start)
        out.metrics["RFQs"] = {"operational": op, "period": p1}
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        (
            "The migration's reset of a stored auto row (OP-17) cannot be replayed on a fresh company; the eval plants an "
            "auto row as the owner instead and checks that nothing reads it: the list shows propose, the gate refuses and "
            "sourcing proposes."
        ),
        (
            "send_rfq is called in this process on its own session, as a sweep or a retry would; the refusal's words are "
            "checked as medium, the refusal itself as high."
        ),
        (
            "Reminders: the unconfirmed RFQ is made to look sent (status, invited_at) as the owner, since the platform never "
            "sends it; when even the confirmed control gets no reminder the check is reported low as inconclusive."
        ),
    ]
    return out


def _lock(check: b.Checker, co: f.Co5, m: k.Mutations) -> None:
    admin = co.login("admin")
    for level in ("auto", "off"):
        r = co.call(
            admin,
            "PUT",
            "/api/setup/automation/rfq.send",
            {"level": level, "reason": "eval: try to unlock"},
        )
        m.case("gate_auto", f"PUT rfq.send {level}", r.status_code, 422, 202)
        check(r.status_code == 422, f"PUT /api/setup/automation/rfq.send level {level} answered {r.status_code} "
              f"{r.text[:200]}; the spec says 422 (D42)")  # fmt: skip
        check(
            LOCKED_WORDS in r.text.lower() or "never be set" in r.text.lower(),
            f"the refusal of level {level} does not say it is always decided by a person: {r.text[:200]}",
            "medium",
        )
    # a stored auto row, as a company had before the migration: nothing may read it
    with lib.owner_session(co.slug) as db:
        db.execute(
            text("delete from autonomy_setting where decision_type = 'rfq.send'")
        )
        db.execute(text("insert into autonomy_setting (decision_type, level, min_confidence, max_wrong_rate, updated_at) "
                        "values ('rfq.send', 'auto', 0.9, 0.02, now())"))  # fmt: skip
        db.commit()
    r = co.call(admin, "GET", "/api/setup/automation")
    row = next(
        (
            d
            for d in js(r).get("decisions") or []
            if d.get("decision_type") == "rfq.send"
        ),
        {},
    )
    check(bool(row) and row.get("locked") is True and row.get("levels") == ["propose"] and row.get("level") == "propose",
          f"the automation list shows rfq.send as {row}; the spec says locked, levels ['propose'], level propose")  # fmt: skip
    check("D42" in str(row.get("note") or ""), f"the automation list's rfq.send note is {row.get('note')!r}; the spec's "
          "note starts 'D42: a buyer confirms every new RFQ'", "medium")  # fmt: skip
    from p2p.agents import autonomy
    from sqlalchemy.orm import Session

    with Session(_engine(co)) as db:
        g = autonomy.gate(db, "rfq.send", value=None, confidence=1.0)
    m.case("gate_auto", "gate with a stored auto row", g.allowed, False, True)
    check(not g.allowed and LOCKED_WORDS in g.reason.lower(), f"the gate with a stored auto row answers {g}; the spec "
          "says it always refuses ('is always decided by a person')")  # fmt: skip


def _engine(co: f.Co5) -> Any:
    from p2p.platform import routing

    return routing.engine_for(co.slug)


def _operational(check: b.Checker, co: f.Co5, m: k.Mutations) -> str:
    """Production asks for 120 kg of wire (Rs 1,10,400 at the standard price, the director), approved, sourced."""
    prod = co.login("production")
    body = {"lines": [{"item_code": f.WIRE, "qty": "120"}], "need_by": "2026-12-14", "location": f.LOC,
            "reason": "E112 winding wire"}  # fmt: skip
    r = co.call(prod, "POST", "/api/requisitions", body)
    no = str(js(r).get("id") or "")
    if not check(
        r.status_code == 200 and bool(no),
        f"operational: the request was not made: {r.status_code} {r.text[:200]}",
    ):
        return ""
    co.call(
        prod,
        "POST",
        f"/api/requisitions/{no}/submit",
        {"override_reason": "E112: a separate case"},
    )
    status = co.approve(no)
    check(
        status == "approved",
        f"operational: request {no} is {status} after its approvers, not approved",
        "medium",
    )
    co.work()
    rfq = str(
        co.one("select no from rfq where request_no = :r order by no limit 1", r=no)
        or ""
    )
    check(bool(rfq), f"operational: request {no} made no quotation request")
    if rfq:
        sent = co.rfq(rfq).get("status") == "sent"
        m.case("gate_auto", "operational RFQ with a stored auto row", sent, False, True)
    return rfq


def _waits(check: b.Checker, co: f.Co5, no: str, label: str, since: int) -> None:
    if not no:
        return
    row = co.rfq(no)
    pid = row.get("proposal_id")
    prop = co.proposal(pid)
    check(row.get("status") == "draft" and row.get("confirmed_at") is None and row.get("sent_at") is None,
          f"{label} {no}: status {row.get('status')}, confirmed_at {row.get('confirmed_at')}, sent_at {row.get('sent_at')}; "
          "expected a draft nobody confirmed (D42)")  # fmt: skip
    check(prop.get("action") == "rfq.send" and prop.get("status") == "pending",
          f"{label} {no}: proposal {pid} is {prop.get('action')} {prop.get('status')}; expected a pending rfq.send")  # fmt: skip
    invited = co.invited(no)
    mailed = [
        x
        for x in co.mails(since)
        if no in str(x.get("subject", "")) + str(x.get("body", ""))
    ]
    check(bool(invited) and not any(s.get("invited_at") for s in invited) and not mailed,
          f"{label} {no}: {len(invited)} supplier(s), {sum(1 for s in invited if s.get('invited_at'))} invited, "
          f"{len(mailed)} mail(s) before the buyer confirmed")  # fmt: skip


def _send_refused(
    check: b.Checker, co: f.Co5, m: k.Mutations, no: str, label: str
) -> None:
    from p2p.sourcing import service
    from sqlalchemy.orm import Session

    if not no:
        return
    since, mark = co.mark(), co.last_audit_id()
    with Session(_engine(co)) as db:
        try:
            n = service.send_rfq(db, no)
            db.commit()
            answer = f"sent {n}"
        except Exception as exc:  # noqa: BLE001 - the refusal is the point; its type is the platform's
            db.rollback()
            answer = f"{type(exc).__name__}: {exc}"
    co.work()
    sent = answer.startswith("sent") and not answer.endswith(" 0")
    m.case(
        "send_unconfirmed",
        f"send_rfq on the unconfirmed {label} RFQ",
        sent,
        False,
        True,
    )
    mailed = [
        x
        for x in co.mails(since)
        if no in str(x.get("subject", "")) + str(x.get("body", ""))
    ]
    audits = [a for a in co.since("rfq.send", mark) if a["entity_id"] == no]
    invited = [s for s in co.invited(no) if s.get("invited_at")]
    check(not answer.startswith("sent") and not mailed and not audits and not invited,
          f"send_rfq on the unconfirmed {label} {no}: {answer}; {len(mailed)} mail(s), {len(audits)} rfq.send audit, "
          f"{len(invited)} invited; the spec says refused, nothing queued, no audit (D42)")  # fmt: skip
    check("confirmation" in answer.lower() and "D42" in answer, f"send_rfq's refusal of {no} does not say it has no "
          f"buyer's confirmation (D42): {answer[:200]}", "medium")  # fmt: skip


def _confirm(out: Outcome, check: b.Checker, co: f.Co5, nos: list[str]) -> None:
    buyer, prod = co.login("buyer"), co.login("production")
    r = co.confirm_send(prod, nos)
    check(
        r.status_code == 403,
        f"confirm-send by production@ (no rfq.send) answered {r.status_code}; expected 403",
    )
    for body, words in (([], "none"), ([f"RFQ-X{i}" for i in range(51)], "51")):
        r = co.confirm_send(buyer, body)
        check(
            r.status_code == 422,
            f"confirm-send of {words} RFQs answered {r.status_code}; expected 422",
        )
    for no in nos:
        check(
            co.rfq(no).get("status") == "draft",
            f"{no} left draft after refused confirm-sends",
        )
    since, mark = co.mark(), co.last_audit_id()
    r = co.confirm_send(buyer, [*nos, "RFQ-NONE-0000"])
    results = {
        str(x.get("rfq_no")): x
        for x in js(r).get("results") or []
        if isinstance(x, dict)
    }
    if (
        r.status_code != 200
    ):  # one unknown number may not stop the others (spec 4.1 rule 5)
        check(False, f"confirm-send with one unknown number answered {r.status_code} {r.text[:200]}; the spec says each "
              "result alone", "medium")  # fmt: skip
        r = co.confirm_send(buyer, nos)
        results = {
            str(x.get("rfq_no")): x
            for x in js(r).get("results") or []
            if isinstance(x, dict)
        }
    else:
        bad = results.get("RFQ-NONE-0000", {})
        check(bad.get("status") == "refused" and bool(bad.get("reason")), f"the unknown RFQ's result is {bad}; expected "
              "refused with a reason", "medium")  # fmt: skip
    buyer_id = co.user_id(buyer)
    total = 0
    for no in nos:
        row, invited = co.rfq(no), co.invited(no)
        res = results.get(no, {})
        total += len(invited)
        check(res.get("status") == "sent" and int(res.get("sent") or 0) == len(invited),
              f"confirm-send result for {no}: {res}; expected sent {len(invited)}")  # fmt: skip
        check(row.get("status") == "sent" and row.get("confirmed_at") is not None and row.get("confirmed_via") == "bulk"
              and f.same_person(co, row.get("confirmed_by"), buyer),
              f"{no} after confirm-send: status {row.get('status')}, confirmed_by {row.get('confirmed_by')} (buyer "
              f"{buyer_id}), via {row.get('confirmed_via')}, at {row.get('confirmed_at')}")  # fmt: skip
        for s in invited:
            got = f.mails_to(co, str(s.get("email") or ""), since, no)
            check(
                len(got) == 1,
                f"{no}: {len(got)} mail(s) to {s.get('supplier_code')} ({s.get('email')}), expected 1",
            )
        confirms = [a for a in co.since("rfq.confirm", mark) if a["entity_id"] == no]
        check(len(confirms) == 1 and "bulk" in str(confirms[0]["after"]),
              f"{no}: rfq.confirm audit rows {[a['after'] for a in confirms]}; expected one with via bulk")  # fmt: skip
        check(any(a["entity_id"] == no for a in co.since("rfq.send", mark)), f"{no}: no rfq.send audit row after "
              "confirming", "medium")  # fmt: skip
    out.metrics["suppliers mailed by one confirm-send"] = total


def _reminders(check: b.Checker, co: f.Co5, confirmed: str) -> None:
    """An unconfirmed RFQ (P4's seals, never confirmed) made to look sent and aged, beside the confirmed P1 aged alike."""
    from p2p.sourcing import reminders
    from sqlalchemy.orm import Session

    if not confirmed:
        return
    r = co.period_rfq(co.login("buyer"), f.LOC, f.P_FROM, f.P_TO, [(f.SEAL, 1000)])
    lone = f.created(check, co, r, "reminders")
    if not lone:
        return
    aged = "timestamp with time zone '2026-11-12 10:00:00+05:30'"
    try:
        with lib.owner_session(co.slug) as db:
            db.execute(
                text(
                    "update rfq set status = 'sent', sent_at = "
                    + aged
                    + " where no = :n"
                ),
                {"n": lone},
            )
            db.execute(text("update rfq_supplier set invited_at = " + aged + " where rfq_no in (:a, :b)"),
                       {"a": lone, "b": confirmed})  # fmt: skip
            db.commit()
    except Exception as exc:  # noqa: BLE001 - a constraint may forbid a sent RFQ with no confirmation: then it is safe
        check(False, f"reminders: an unconfirmed RFQ cannot even be made to look sent ({type(exc).__name__}); the "
              "defensive skip is not reachable", "low")  # fmt: skip
        return
    since = co.mark()
    with Session(_engine(co)) as db:
        reminders.run(db, as_of=f.TODAY)
        db.commit()
    co.work()
    lone_mails = [x for x in co.mails(since) if lone in str(x.get("subject", ""))]
    control = [x for x in co.mails(since) if confirmed in str(x.get("subject", ""))]
    check(
        not lone_mails,
        f"reminders: {len(lone_mails)} reminder(s) went for the unconfirmed {lone} (D42: never)",
    )
    check(bool(control), f"reminders: the confirmed {confirmed} aged alike got no reminder either: the check is "
          "inconclusive", "low")  # fmt: skip
