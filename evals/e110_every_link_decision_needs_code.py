"""E110: every decision from an email link needs a one-time code (D-07, OP-9, OP-10), written without the
implementation.

Independence: written from the block 4 spec (sections 5.1, 6.3 O1, 7) and the dataset only. Who approves each request
is worked out here from `approval_rule.csv`, `item.csv` (category and standard price), `user.csv` and the profile's
approver roles; the code limit from `setting.csv` (it no longer governs links). Codes are read from the mail caught in
this process, the way E41 reads them.

O1 on fresh Apex and Brightwave resets. Apex: production asks for 120 mechanical seals (RM-07, Rs 11,400, the plant
head) and 120 kg copper wire (RM-08, Rs 1,10,400, the director). Each approval email has the code note and the app link
(`#/requisitions/<no>`); the link page has the code input, "Email me a code" and the app link. On the plant head's link,
approve, reject and return without a code are each refused 409 and change nothing (the old rule let all three through
below Rs 5,00,000). Asking for a code mails one message, to planthead@ only; neither the page nor any other mail holds
the code; a wrong code is refused with "4 tries left"; the right code approves (step approved, audit `otp.use` on
`approval:<no>:<round>:<step>`, the decision audited `via` "emailed link"); the same code again is refused; the plant
head's code on the director's link is refused. On the director's link: five wrong tries, then the right code is refused
(locked); an expired code is refused; a fresh code sends the request back. Brightwave: employee@ asks for 2 laptops
(REQ-06, the IT head then the CFO): both link steps refuse without a code and go through with one. A Brightwave link
re-signed for Apex decides nothing and mails nobody. Ends with a tenant check.
Mutations: a code required only above the limit; reject or return allowed without a code.
"""

import re
from decimal import Decimal
from typing import Any

import block1 as b
import block4 as k
import env
import lib
from block1 import D, js
from lib import Outcome
from sqlalchemy import text

ID = "E110"
TITLE = "Every decision from an email link needs a one-time code (D-07)"
REQUIREMENT = (
    "D-07, OP-9, OP-10 (block 4 spec 5.1, O1): approve, reject and return from /approve/{token} each need a valid code "
    "whatever the amount; the code is mailed only to the login the link names and never shown on the page or in another "
    "mail; wrong codes count down and lock after five; a code works once, for one step of one round for one person; "
    "expired codes are refused; the email and page carry the code note and the app link; audit otp.* and 'emailed "
    "link'; Apex and Brightwave; a link re-signed for another company decides nothing; tenant check"
)
PREFIX = "e110"
NEED_BY = "2026-12-14"
CODE_NOTE = "Deciding from this email needs a one-time code"


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
        codes: list[str] = []
        pages: list[str] = []
        apex_reqs = _apex(out, check, apex, m, codes, pages)
        bw_reqs = _brightwave(out, check, bw, m, codes, pages)
        _foreign_link(check, apex, bw, bw_reqs)
        _never_shown(check, apex, codes, pages, start)
        m.report(check, out, ["code_above_only", "reject_free"])
        _tenant(check, apex, bw, apex_reqs, bw_reqs, start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        ("Codes and approval emails are read from the mail caught in this process (the worker sends the queued approval "
        "emails into it; the codes go to it straight, as platform/otp.py sends them)."),
        ("Code ageing (a lock's 30-second resend gap, an expired code) is done by moving one_time_code times back as the "
        "owner; the spec names the rules, not a clock to move."),
        ("The cross-company link is re-signed with email_links.make (unchanged by block 4) for Apex's slug and "
        "Brightwave's request and approver: it must decide nothing and send no code."),
    ]
    return out


# ---- the oracle: who decides a request, from the raw files -----------------------------------------------------------


def request_value(co: k.Co, item: str, qty: int) -> Decimal:
    it = next(r for r in co.rows("master", "item") if r["code"] == item)
    return k.money(D(it["standard_price"]) * qty)


def request_chain(co: k.Co, oracle: k.Oracle, item: str, value: Decimal, requester: str) -> list[str]:
    """The persons of the request rule for this value and category (a category row before ANY; '+' rows ignored: none
    apply to these cases)."""
    cat = next(r["category_code"] for r in co.rows("master", "item") if r["code"] == item)
    rows = [r for r in oracle.bands("request") if D(r["min_value"]) <= value <= D(r["max_value"])
            and r["category_code"] in (cat, "ANY")]  # fmt: skip
    rows.sort(key=lambda r: r["category_code"] == "ANY")
    if not rows or rows[0]["approver_chain"] == "auto":
        return []
    people = []
    for role in rows[0]["approver_chain"].split(">"):
        kind, _val = oracle.role_map[role]
        if kind == "department_head":
            dept = co.people[requester]["department_code"]
            people.append(next(d["head_user"] for d in co.rows("master", "department") if d["code"] == dept))
        else:
            people.append(oracle.person(role))
    return people


# ---- the platform ------------------------------------------------------------------------------------------------


def make_request(check: b.Checker, co: k.Co, by: str, item: str, qty: int, loc: str, label: str) -> str:
    body = {"lines": [{"item_code": item, "qty": str(qty)}], "need_by": NEED_BY, "location": loc, "reason": label}
    r = co.call(by, "POST", "/api/requisitions", body)
    no = str(js(r).get("id") or "")
    if not check(r.status_code == 200 and bool(no), f"{label}: the request was not made: {r.status_code} {r.text[:200]}"):
        return ""
    r = co.call(by, "POST", f"/api/requisitions/{no}/submit", {"override_reason": f"{label}: a separate case"})
    check(r.status_code == 200, f"{label}: submitting {no} answered {r.status_code} {r.text[:200]}")
    co.work()
    return no


def pending_step(co: k.Co, no: str) -> dict[str, Any]:
    found = co.qd("select * from approval_step where request_no = :n and status = 'pending' order by round, step", n=no)
    return found[0] if found else {}


def link_of(co: k.Co, no: str, to: str, since: int = 0) -> tuple[str | None, dict[str, Any]]:
    """The newest approval email for this request to this address, and the token of its links."""
    for mm in reversed(co.mails(since)):
        body = str(mm.get("body") or "")
        if str(mm.get("to", "")).lower() == to.lower() and no in str(mm.get("subject", "")) + body and "/approve/" in body:
            found = re.search(r"/approve/([^\s?\"'<>]+)", body)
            if found:
                return found.group(1), mm
    return None, {}


def post(co: k.Co, token: str, **data: str) -> Any:
    co.db.rollback()
    r = co.client.post(f"/approve/{token}", data=data)
    co.db.rollback()
    return r


def ask_code(check: b.Checker, co: k.Co, token: str, to: str, label: str, pages: list[str]) -> str | None:
    since = len(co.box.sent)
    co.db.rollback()
    r = co.client.post(f"/approve/{token}/code")
    co.db.rollback()
    pages.append(r.text)
    code, found = co.code_mail(to, since)
    check(r.status_code == 200 and code is not None, f"{label}: asking for a code answered {r.status_code}; code mailed "
          f"{code is not None}")  # fmt: skip
    check(len(found) == 1 and str(found[0].get("to", "")).lower() == to.lower(),
          f"{label}: asking for a code sent {[x.get('to') for x in found]}; expected one mail to {to} only")  # fmt: skip
    return code


def age_codes(co: k.Co, minutes: int = 15) -> None:
    """As if a quarter of an hour passed: the last code is no longer new, nor still valid (owner's login, test only)."""
    with lib.owner_session(co.slug) as db:
        db.execute(text(
            "update one_time_code set created_at = created_at - make_interval(mins => :m), "
            "expires_at = expires_at - make_interval(mins => :m) where purpose = 'approval'"
        ), {"m": minutes})  # fmt: skip
        db.commit()


def state(co: k.Co, no: str) -> tuple[Any, ...]:
    steps = co.qd("select round, step, status from approval_step where request_no = :n order by round, step", n=no)
    return co.one("select status from request where no = :n", n=no), tuple((s["round"], s["step"], s["status"]) for s in steps)


def check_mail(check: b.Checker, co: k.Co, no: str, mm: dict[str, Any], label: str) -> None:
    body = str(mm.get("body") or "")
    check("one-time code" in body.lower(), f"{label}: the approval email has no code note: {body[:300]}")
    check(f"#/requisitions/{no}" in body, f"{label}: the approval email has no app link #/requisitions/{no}")
    check(k.norm(CODE_NOTE) in k.norm(body), f"{label}: the email's code note is not the spec's '{CODE_NOTE}...'", "medium")


def check_page(check: b.Checker, co: k.Co, token: str, no: str, label: str, pages: list[str]) -> None:
    co.db.rollback()
    r = co.client.get(f"/approve/{token}")
    pages.append(r.text)
    html = r.text
    check(r.status_code == 200 and re.search(r"name=['\"]?code", html) is not None and "email me a code" in html.lower(),
          f"{label}: the link page has no code input or 'Email me a code' ({r.status_code})")  # fmt: skip
    check(f"#/requisitions/{no}" in html, f"{label}: the link page has no app link #/requisitions/{no}")


def decided_with_code(check: b.Checker, co: k.Co, no: str, step: dict[str, Any], mark: int, label: str) -> None:
    ref = f"approval:{no}:{step.get('round')}:{step.get('step')}"
    used = co.qd("select * from audit_event where action = 'otp.use' and entity_id = :e and id > :i", e=ref, i=mark)
    issued = co.qd("select * from audit_event where action = 'otp.issue' and entity_id = :e and id > :i", e=ref, i=mark)
    via = co.qd(
        "select * from audit_event where action like 'request.%' and entity_id = :n and id > :i "
        "and after->>'via' = 'emailed link'", n=no, i=mark,
    )  # fmt: skip
    check(len(used) == 1 and len(issued) >= 1, f"{label}: audit otp.use {len(used)}, otp.issue {len(issued)} on {ref}; "
          "expected one use and the issue")  # fmt: skip
    check(len(via) >= 1, f"{label}: no decision row 'via emailed link'")


def _no_code_refused(check: b.Checker, co: k.Co, m: k.Mutations, token: str, no: str, value: Decimal, limit: Decimal,
                     label: str) -> None:  # fmt: skip
    for action in ("approve", "reject", "return"):
        before = state(co, no)
        r = post(co, token, action=action, reason="eval: no code")
        observed = r.status_code
        right = 409 if k.link_needs_code(action, value, limit) else 200
        for name in ("code_above_only", "reject_free"):
            wrong = 409 if k.link_needs_code(action, value, limit, k.Wrong(**{name: True})) else 200
            m.case(name, f"{label} {action} without a code", observed, right, wrong)
        check(observed == 409 and state(co, no) == before,
              f"{label}: {action} without a code answered {observed}; state {before} -> {state(co, no)}; expected 409 "
              "and nothing changed (D-07)")  # fmt: skip
        check("one-time code" in r.text.lower(), f"{label}: {action} without a code does not ask for one: "
              f"{r.text[:200]}", "medium")  # fmt: skip


def _apex(out: Outcome, check: b.Checker, co: k.Co, m: k.Mutations, codes: list[str], pages: list[str]) -> list[str]:
    oracle = k.Oracle(co)
    limit = oracle.code_limit()
    prod = co.designation("Production Manager")
    r1 = make_request(check, co, prod, "RM-07", 120, k.LOC, "O1 seals")
    r2 = make_request(check, co, prod, "RM-08", 120, k.LOC, "O1 wire")
    if not (r1 and r2):
        return [r1, r2]
    v1, v2 = request_value(co, "RM-07", 120), request_value(co, "RM-08", 120)
    check((v1, v2) == (Decimal("11400.00"), Decimal("110400.00")), f"oracle self-check: request values {v1}, {v2}; the "
          "spec says 11,400 and 1,10,400", "medium")  # fmt: skip
    for no, value in ((r1, v1), (r2, v2)):
        got = D(co.one("select total_value from request where no = :n", n=no))
        check(got == value, f"O1: {no} estimated at {got}, oracle {value}", "medium")
    head = request_chain(co, oracle, "RM-07", v1, prod)
    director = request_chain(co, oracle, "RM-08", v2, prod)
    s1, s2 = pending_step(co, r1), pending_step(co, r2)
    check(co.code_of(s1.get("approver")) == head[0] and co.code_of(s2.get("approver")) == director[0],
          f"O1: steps wait for {s1.get('approver')} and {s2.get('approver')}; oracle {head[0]} and {director[0]}")  # fmt: skip
    head_mail, dir_mail = co.login_email(head[0]), co.login_email(director[0])
    t1, mail1 = link_of(co, r1, head_mail)
    t2, mail2 = link_of(co, r2, dir_mail)
    if not check(t1 is not None and t2 is not None, f"O1: approval emails with links: plant head {bool(t1)}, director "
                 f"{bool(t2)}"):  # fmt: skip
        return [r1, r2]
    t1, t2 = str(t1), str(t2)
    check_mail(check, co, r1, mail1, "O1 plant head's email")
    check_mail(check, co, r2, mail2, "O1 director's email")
    check_page(check, co, t1, r1, "O1 plant head's page", pages)

    # below the old limit: approve, reject and return without a code are all refused
    _no_code_refused(check, co, m, t1, r1, v1, limit, "O1 Rs 11,400")
    _no_code_refused(check, co, m, t2, r2, v2, limit, "O1 Rs 1,10,400")

    # a code for the plant head; wrong, right, again; on the director's link
    mark = int(co.one("select coalesce(max(id), 0) from audit_event") or 0)
    c1 = ask_code(check, co, t1, head_mail, "O1 plant head", pages)
    if c1:
        codes.append(c1)
        wrong = "000000" if c1 != "000000" else "999999"
        r = post(co, t1, action="approve", code=wrong)
        check(r.status_code == 409 and "4 tries left" in r.text, f"O1: a wrong code answered {r.status_code}: "
              f"{r.text[:200]}; expected 409 '4 tries left'")  # fmt: skip
        r = post(co, t1, action="approve", code=c1)
        co.work()
        st = co.qd("select * from approval_step where request_no = :n order by round, step", n=r1)
        check(r.status_code == 200 and st and st[0]["status"] == "approved",
              f"O1: the right code answered {r.status_code}; steps {[(x['step'], x['status']) for x in st]}")  # fmt: skip
        decided_with_code(check, co, r1, s1, mark, "O1 plant head")
        again = post(co, t1, action="approve", code=c1)
        check(again.status_code == 409, f"O1: the same code again answered {again.status_code}")
        other = post(co, t2, action="approve", code=c1)
        check(other.status_code == 409 and state(co, r2)[0] == "submitted",
              f"O1: the plant head's code on the director's link answered {other.status_code}")  # fmt: skip

    # the director's link: five wrong tries lock the code; an expired code; a fresh code sends it back
    c2 = ask_code(check, co, t2, dir_mail, "O1 director", pages)
    if c2:
        codes.append(c2)
        wrong = "000000" if c2 != "000000" else "999999"
        tries = [post(co, t2, action="approve", code=wrong) for _ in range(5)]
        check([x.status_code for x in tries] == [409] * 5 and "4 tries left" in tries[0].text,
              f"O1: five wrong tries answered {[x.status_code for x in tries]}")  # fmt: skip
        locked = post(co, t2, action="approve", code=c2)
        check(locked.status_code == 409 and state(co, r2)[0] == "submitted",
              f"O1: the right code after five wrong tries answered {locked.status_code}; expected locked (409)")  # fmt: skip
        age_codes(co)
        c3 = ask_code(check, co, t2, dir_mail, "O1 director again", pages)
        if c3:
            codes.append(c3)
            age_codes(co)
            late = post(co, t2, action="approve", code=c3)
            check(late.status_code == 409 and "expired" in late.text.lower(),
                  f"O1: an expired code answered {late.status_code}: {late.text[:160]}")  # fmt: skip
        age_codes(co)
        # the mark goes before the code is asked for, so its otp.issue row counts (it was taken after: an eval bug)
        mark = int(co.one("select coalesce(max(id), 0) from audit_event") or 0)
        c4 = ask_code(check, co, t2, dir_mail, "O1 director, fresh", pages)
        if c4:
            codes.append(c4)
            r = post(co, t2, action="return", reason="eval: with a code", code=c4)
            check(r.status_code == 200 and state(co, r2)[0] == "returned",
                  f"O1: return with the right code answered {r.status_code}; request {state(co, r2)[0]}")  # fmt: skip
            decided_with_code(check, co, r2, s2, mark, "O1 director return")
    out.metrics["Apex requests (value, first approver)"] = {r1: f"{v1} {head[0]}", r2: f"{v2} {director[0]}"}
    return [r1, r2]


def _brightwave(out: Outcome, check: b.Checker, co: k.Co, m: k.Mutations, codes: list[str], pages: list[str]) -> list[str]:
    oracle = k.Oracle(co)
    asker = next(c for c, u in co.people.items() if u["email"].startswith("employee@"))
    loc = co.location()["code"]
    no = make_request(check, co, asker, "LP-01", 2, loc, "O1 laptops")
    if not no:
        return []
    value = request_value(co, "LP-01", 2)
    chain = request_chain(co, oracle, "LP-01", value, asker)
    check(len(chain) == 2, f"O1 Brightwave: the oracle's chain for {value} is {chain}; the spec says IT head then CFO",
          "medium")  # fmt: skip
    for n, who in enumerate(chain, 1):
        step = pending_step(co, no)
        check(co.code_of(step.get("approver")) == who and step.get("step") == n,
              f"O1 Brightwave step {n}: waits for {step.get('approver')} (step {step.get('step')}); oracle {who}")  # fmt: skip
        token, mm = link_of(co, no, co.login_email(who))
        if not check(token is not None, f"O1 Brightwave step {n}: no approval email with a link to {who}"):
            break
        check_mail(check, co, no, mm, f"O1 Brightwave step {n} email")
        before = state(co, no)
        r = post(co, str(token), action="approve")
        m.case("code_above_only", f"Brightwave step {n} approve without a code", r.status_code, 409,
               409 if k.link_needs_code("approve", value, oracle.code_limit(), k.Wrong(code_above_only=True)) else 200)
        check(r.status_code == 409 and state(co, no) == before,
              f"O1 Brightwave step {n}: approve without a code answered {r.status_code}")  # fmt: skip
        mark = int(co.one("select coalesce(max(id), 0) from audit_event") or 0)
        code = ask_code(check, co, str(token), co.login_email(who), f"O1 Brightwave step {n}", pages)
        if not code:
            break
        codes.append(code)
        r = post(co, str(token), action="approve", code=code)
        co.work()
        check(r.status_code == 200, f"O1 Brightwave step {n}: approve with the code answered {r.status_code}")
        decided_with_code(check, co, no, step, mark, f"O1 Brightwave step {n}")
    # approved by both steps; the worker then orders it, which closes the request (spec silent on the later status)
    status, steps = state(co, no)
    check(status in ("approved", "closed") and steps and all(s[2] == "approved" for s in steps),
          f"O1 Brightwave: {no} is {status} with steps {steps} after both steps, not approved")  # fmt: skip
    out.metrics["Brightwave request (value, chain)"] = f"{no}: {value} {chain}"
    return [no]


def _foreign_link(check: b.Checker, apex: k.Co, bw: k.Co, bw_reqs: list[str]) -> None:
    """A Brightwave request's link re-signed for Apex decides nothing and mails no code."""
    from p2p.approvals import email_links

    if not bw_reqs or not bw_reqs[0]:
        return
    no = bw_reqs[0]
    who = next(c for c, u in bw.people.items() if (u.get("designation") or "") == "IT Head")
    try:
        token = email_links.make(apex.slug, no, 1, 1, bw.user_id(who))
    except Exception as exc:  # noqa: BLE001 - the interface may have moved; say so
        check(False, f"cross-company link: email_links.make failed: {exc}", "low")
        return
    before_apex = apex.qd("select no, status from request order by no")
    since = len(apex.box.sent)
    apex.db.rollback()
    page = apex.client.get(f"/approve/{token}")
    code = apex.client.post(f"/approve/{token}/code")
    act = post(apex, token, action="approve", code="000000")
    check(page.status_code != 200 or no not in page.text or "LP-01" not in page.text,
          "cross-company link: a Brightwave request shows through an Apex-signed link")  # fmt: skip
    check(act.status_code != 200 and not apex.mails(since) and apex.qd("select no, status from request order by no") == before_apex,
          f"cross-company link: answered {code.status_code}/{act.status_code}, {len(apex.mails(since))} mail(s); expected "
          "nothing decided and no code sent")  # fmt: skip


def _never_shown(check: b.Checker, co: k.Co, codes: list[str], pages: list[str], start: int) -> None:
    """D-07: a code is only in its own mail, never on a page or in another mail."""
    for c in codes:
        holders = [x for x in co.mails(start) if c in str(x.get("body", "")) + str(x.get("html", ""))]
        check(len(holders) == 1 and "code" in str(holders[0].get("subject", "")).lower(),
              f"a code is in {len(holders)} mail(s): {[x.get('subject') for x in holders]}; expected only its own")  # fmt: skip
        check(not any(c in p for p in pages), "a code is shown on a link page")


def _tenant(check: b.Checker, apex: k.Co, bw: k.Co, apex_reqs: list[str], bw_reqs: list[str], start: int) -> None:
    for mine, other, reqs, item in ((apex, bw, apex_reqs, "RM-0"), (bw, apex, bw_reqs, "LP-01")):
        for no in reqs:
            if not no:
                continue
            r = other.call(other.buyer(), "GET", f"/api/requisitions/{no}")
            check(r.status_code == 404 or item not in r.text, f"tenant check: {other.name} reads {mine.name}'s {no} "
                  f"({r.status_code})")  # fmt: skip
            leaked = [x for x in mine.mails(start) if str(x.get("to", "")).lower().endswith("@" + other.domain)
                      and no in str(x.get("subject", "")) + str(x.get("body", "")) and item in str(x.get("body", ""))]  # fmt: skip
            check(not leaked, f"tenant check: {len(leaked)} mail(s) about {mine.name}'s {no} went to {other.name}")
