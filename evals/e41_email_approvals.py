"""E41: email approvals on the main demo company (plan step 4, D36), against an oracle from the raw files."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import e02_approvals
import lib
from e02_approvals import NoRule, oracle_people, oracle_rule
from fastapi.testclient import TestClient
from lib import D, Outcome, rows
from sqlalchemy import text

ID = "E41"
TITLE = "Approval by email: the right person, a full email, one reminder, escalation on time, a code on every link"
REQUIREMENT = (
    "Plan step 4 and D36: who approves follows the rule table by value, category, department and location (rows that add "
    "an approver too); each step keeps its rule row; the email carries the lines, the estimated total, the budget, why it "
    "came to this person and approve / reject / return links that work once; one reminder after a working day and "
    "escalation after two, counted on the business calendar with holidays; a one-time code on every decision from a link "
    "(D-07, block 4: approve, reject and return, at any value) that is hashed, expires in 10 minutes, allows 5 tries and "
    "works once; a deputy; the requester never approves"
)
IST = ZoneInfo("Asia/Kolkata")
HOLIDAY = date(2026, 11, 9)  # a Monday made a holiday for the timing checks


def setting(key: str, default: str) -> str:
    return next((r["value"] for r in rows("master", "setting") if r["key"] == key), default)


def inr(value: Decimal) -> str:
    """Indian grouping, written here on its own: 1234567.5 is '₹ 12,34,567.50'."""
    whole, frac = f"{value:.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    parts = []
    while head:
        parts.insert(0, head[-2:])
        head = head[:-2]
    return "₹ " + ",".join([*parts, tail]) + "." + frac


def working_time_later(start: datetime, days: Decimal, holidays: set[date]) -> datetime:
    """Working hours 09:00-18:00 IST, Monday to Friday, not on a holiday; `days` working days of nine hours each."""
    left = timedelta(hours=float(days * 9))  # whole and half days only, so this is exact
    t = start.astimezone(IST)

    def working(d: date) -> bool:
        return d.weekday() < 5 and d not in holidays

    while True:
        if not working(t.date()) or t.time() >= time(18):
            t = datetime.combine(t.date() + timedelta(days=1), time(9), tzinfo=IST)
            continue
        t = max(t, datetime.combine(t.date(), time(9), tzinfo=IST))
        room = datetime.combine(t.date(), time(18), tzinfo=IST) - t
        if left <= room:
            return t + left
        left -= room
        t = datetime.combine(t.date() + timedelta(days=1), time(9), tzinfo=IST)


def run(ctx) -> Outcome:  # noqa: C901 - one scenario, step by step
    from p2p.approvals import chasing, deputies, email_links, workflow
    from p2p.formulas.calendar import pin_today
    from p2p.main import app
    from p2p.platform import mail, sod, tasks, tenants, worker

    out = Outcome(True)
    e02_approvals.ROLES = lib.demo_company().approver_roles  # how the company names its chain roles, for the oracle
    pin_today(lib.TODAY)
    world = lib.build_world("e41", with_stock=False)
    db, slug = world.db, world.slug
    db.execute(text("delete from budget"))  # routing is checked here; the budget has its own eval (E38)
    db.commit()
    client = TestClient(app)
    rule_rows = rows("master", "approval_rule")
    org = {r["code"]: r for r in rows("master", "user")}
    depts = {r["code"]: r["head_user"] for r in rows("master", "department")}
    loc = rows("master", "location")[0]
    loc_heads = {loc["code"]: loc["head_user"]}
    delegations = rows("master", "delegation")
    login_of = {code: uid for code, uid in world.org_logins.items()}
    otp_limit = D(setting("approval_otp_above", "500000.00"))
    remind_days, escalate_days = D(setting("approval_reminder_days", "1")), D(setting("approval_escalation_days", "2"))
    sent: list[dict[str, Any]] = []
    previous_send = mail.send_email
    mail.send_email = lambda to, subject, body, **kw: sent.append({"to": to, "subject": subject, "body": body}) or "id"  # type: ignore[assignment]
    seq = iter(range(1, 10_000))

    def jobs(kind: str) -> list[dict[str, Any]]:
        with tenants.control_engine().connect() as c:
            return [
                dict(r[0])
                for r in c.execute(
                    text("select payload from job where tenant_slug = :t and kind = :k order by id"), {"t": slug, "k": kind}
                )
            ]

    def request(value: Decimal, requester: str, kind: str = "goods") -> str:
        no = f"E41-{next(seq):04d}"
        db.execute(
            text(
                "insert into request (no, requester, department_code, ship_to_location, type, channel, status, total_value) "
                "values (:no, :r, :d, :loc, :k, 'form', 'draft', :v)"
            ),
            {"no": no, "r": requester, "d": org[requester]["department_code"], "loc": loc["code"], "k": kind, "v": value},
        )
        db.execute(
            text(
                "insert into request_line (request_no, line_no, free_text, qty, est_price, status) "
                "values (:no, 1, 'Eval part', 1, :v, 'open')"
            ),
            {"no": no, "v": value},
        )
        db.commit()
        return no

    def steps(no: str) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in db.execute(
                text("select * from approval_step where request_no = :n order by round, step"), {"n": no}
            ).mappings()
        ]

    try:
        # 1. who approves: value edges, opex and capex, every requester; against the oracle from the raw rule table
        requesters = sorted(c for c in org if c in {r["user_code"] for r in rows("master", "user_role")})
        values = [D("10000.00"), D("10000.01"), D("100000.00"), D("100000.01"), otp_limit, otp_limit + D("0.01")]
        routed = agree = 0
        made: list[tuple[str, str, Decimal, list[str]]] = []  # (request, requester, value, approvers in order)
        for value in values:
            for spend in ("opex", "capex"):
                for who in requesters:
                    routed += 1
                    dept = org[who]["department_code"]
                    try:
                        code, version, chain = oracle_rule(rule_rows, "request", value, lib.TODAY, spend, None, dept, loc["code"])
                        people = oracle_people(chain, who, dept, lib.TODAY, org, depts, delegations, loc["code"], loc_heads)
                        want: Any = ("approved", []) if not chain else ("submitted", [p for p, _ in people])
                    except NoRule as exc:
                        want = ("refused", str(exc))
                    no = request(value, who, "capex" if spend == "capex" else "goods")
                    try:
                        result = workflow.submit(db, slug, no, sod.user(1), today=lib.TODAY)
                        got: Any = (result.status, result.approvers)
                    except Exception as exc:  # noqa: BLE001 - a refusal is an answer too
                        db.rollback()
                        got = ("refused", f"{type(exc).__name__}: {exc}")
                    if want[0] == "refused" and got[0] == "refused" or want == got:
                        agree += 1
                    else:
                        out.find("high", f"{no} ({value} {spend} by {who}): platform {got}, oracle {want}")
                        continue
                    if got[0] != "submitted":
                        continue
                    made.append((no, who, value, got[1]))
                    first = steps(no)
                    for st in first:
                        source = next(
                            (r for r in rule_rows if r["rule_code"] == st["rule_code"] and int(r["version"]) == st["rule_version"]),
                            None,
                        )
                        if source is None or st["rule_id"] is None or not st["route_reason"]:
                            out.find("high", f"{no} step {st['step']}: no rule row recorded ({st['rule_code']})")
        out.metrics["requests routed / agree with the oracle"] = f"{routed} / {agree}"
        if not made:
            out.find("high", "no request went to a person; nothing else can be checked")
            return out
        refused_own = sum(1 for no, who, _, chain in made if who in chain)
        if refused_own:
            out.find("high", f"{refused_own} request(s) name their own requester as an approver")

        # 2. the email of each first step: lines, the estimated total to the paisa, the budget, why, three links
        mails = [m for m in jobs("send_email") if m["subject"].startswith("Approve request")]
        checked = 0
        for no, _asker, value, chain in made:
            mine = [m for m in mails if f"request {no} " in m["subject"]]
            if len(mine) != 1:
                out.find("high", f"{no}: {len(mine)} approval emails, expected one")
                continue
            m, checked = mine[0], checked + 1
            body = m["body"]
            st = steps(no)[0]
            for part in ("Eval part", f"Estimated total: {inr(value)}", "Budget:", f"{st['rule_code']} v{st['rule_version']}"):
                if part not in body:
                    out.find("high", f"{no}: the email lacks '{part}'")
            tokens = {
                a: next((ln.split("/approve/")[1].split("?")[0] for ln in body.splitlines() if f"?do={a}" in ln), None)
                for a in ("approve", "reject", "return")
            }
            link = email_links.read(tokens["approve"]) if tokens["approve"] else None
            if link is None or len(set(tokens.values())) != 1:
                out.find("high", f"{no}: the email does not carry the three signed links")
            elif (link.request_no, link.step, link.user_id) != (no, 1, login_of.get(chain[0])):
                out.find("high", f"{no}: the link names {link}, not {chain[0]} at step 1")
            if not all(f"?do={a}" in m.get("html", "") for a in ("approve", "reject", "return")):
                out.find("medium", f"{no}: the HTML email lacks a button")
        if db.execute(text("select count(*) from notification where body like '%/approve/%'")).scalar_one():
            out.find("high", "a signed link was kept in an in-app notification")
        out.metrics["approval emails checked"] = checked

        # 3. reminder after one working day, escalation after two: a request opened on Friday 15:30, Monday a holiday
        db.execute(
            text(
                "insert into ref_holiday (holiday_date, name, kind, scope) values (:d, 'Eval holiday', 'closed', 'central')"
            ),
            {"d": HOLIDAY},
        )
        db.commit()
        holidays = {HOLIDAY}
        no, who, value, chain = next(x for x in made if x[2] == D("10000.01"))
        friday = datetime(2026, 11, 6, 15, 30, tzinfo=IST)
        db.execute(text("update approval_step set opened_at = :t where request_no = :n"), {"t": friday, "n": no})
        db.commit()
        remind_at = working_time_later(friday, remind_days, holidays)
        payload = {"request_no": no, "round": 1, "step": 1}
        before = len(jobs("send_email"))
        chasing._remind(worker.JobContext(0, slug, payload, remind_at - timedelta(seconds=1)))
        chasing._remind(worker.JobContext(0, slug, payload, remind_at))
        chasing._remind(worker.JobContext(0, slug, payload, remind_at + timedelta(hours=2)))
        db.rollback()
        reminded = db.execute(text("select reminded_at from approval_step where request_no = :n"), {"n": no}).scalar()
        audits = db.execute(
            text("select count(*) from audit_event where action = 'request.remind' and entity_id = :n"), {"n": no}
        ).scalar_one()
        reminders = [m for m in jobs("send_email")[before:] if m["subject"].startswith("Reminder: request")]
        out.metrics["reminder due (oracle) / sent at"] = f"{remind_at:%a %d %b %H:%M} / {reminded and reminded.astimezone(IST):%a %d %b %H:%M}"
        if reminded != remind_at or audits != 1 or len(reminders) != 1:
            out.find("high", f"reminder: at {reminded}, {audits} audit rows, {len(reminders)} emails; expected one at {remind_at}")

        # the escalation: the task's due time against the oracle (from when the step opened), then who it moves to
        row = db.execute(
            text("select s.opened_at, t.due_at, t.id from approval_step s join task t on t.id = s.task_id where s.request_no = :n"),
            {"n": made[1][0]},
        ).one()
        due = working_time_later(row.opened_at, escalate_days, set())
        if abs((row.due_at - due).total_seconds()) > 1:
            out.find("high", f"escalation due {row.due_at}, oracle {due}")
        no2, who2, _, chain2 = made[1]
        manager = org[chain2[0]]["manager_code"]
        target = manager if manager and manager not in (who2, chain2[0]) and manager in login_of else (chain2[1] if len(chain2) > 1 else None)
        tasks._escalate(worker.JobContext(0, slug, {"task_id": row.id}, row.due_at + timedelta(minutes=1)))
        db.rollback()
        delegate = db.execute(text("select delegate from approval_step where request_no = :n and step = 1"), {"n": no2}).scalar()
        out.metrics["escalated to (platform / oracle)"] = f"{delegate} / {target}"
        if delegate != target:
            out.find("high", f"{no2} escalated to {delegate}, oracle {target}")

        # 4. the one-time code (D-07: on every link decision, the limit included): refused without, five wrong tries
        # lock it, a new code works once
        big = next(x for x in made if x[2] == otp_limit + D("0.01"))
        at_limit = next(x for x in made if x[2] == otp_limit)
        token = email_links.make(slug, big[0], 1, 1, login_of[big[3][0]])
        flat = email_links.make(slug, at_limit[0], 1, 1, login_of[at_limit[3][0]])
        code_checks = {
            "no code refused": client.post(f"/approve/{token}", data={"action": "approve"}).status_code == 409,
            "at the limit needs one too": client.post(f"/approve/{flat}", data={"action": "approve"}).status_code == 409,
        }
        def older() -> None:  # as if a quarter of an hour passed: the last code is no longer new, nor still valid
            db.execute(
                text(
                    "update one_time_code set created_at = created_at - interval '15 minutes', "
                    "expires_at = expires_at - interval '15 minutes'"
                )
            )
            db.commit()

        client.post(f"/approve/{token}/code")
        first_code = sent[-1]["body"].split("code is ")[1][:6]
        wrong = "000000" if first_code != "000000" else "999999"
        tries = [client.post(f"/approve/{token}", data={"action": "approve", "code": wrong}).status_code for _ in range(5)]
        code_checks["five wrong tries refused"] = tries == [409] * 5
        code_checks["locked after five"] = (
            client.post(f"/approve/{token}", data={"action": "approve", "code": first_code}).status_code == 409
        )
        hashes = [r[0] for r in db.execute(text("select code_hash from one_time_code"))]
        code_checks["only hashes at rest"] = all(len(h) == 64 and first_code not in h for h in hashes)
        older()
        client.post(f"/approve/{token}/code")
        late = sent[-1]["body"].split("code is ")[1][:6]
        older()
        late_post = client.post(f"/approve/{token}", data={"action": "approve", "code": late})
        code_checks["expired after ten minutes"] = late_post.status_code == 409 and "expired" in late_post.text
        older()
        client.post(f"/approve/{token}/code")
        good = sent[-1]["body"].split("code is ")[1][:6]
        code_checks["the right code approves"] = (
            client.post(f"/approve/{token}", data={"action": "approve", "code": good}).status_code == 200
        )
        code_checks["the code works once"] = (
            client.post(f"/approve/{token}", data={"action": "approve", "code": good}).status_code == 409
        )
        out.metrics["one-time code checks passed"] = f"{sum(code_checks.values())} / {len(code_checks)}"
        for name, ok in code_checks.items():
            if not ok:
                out.find("high", f"one-time code: {name} failed")

        # 5. reject and return from the link ask for a reason; the link works once; every decision is audited
        free = [x for x in made if x[2] == D("10000.01") and x[0] not in (no, no2)][:2]
        for (rno, _, _, rchain), action in zip(free, ("reject", "return"), strict=False):
            t = email_links.make(slug, rno, 1, 1, login_of[rchain[0]])
            bare = client.post(f"/approve/{t}", data={"action": action, "reason": "eval"}).status_code  # no code
            client.post(f"/approve/{t}/code")
            code = sent[-1]["body"].split("code is ")[1][:6]
            no_reason = client.post(f"/approve/{t}", data={"action": action, "code": code}).status_code
            done = client.post(f"/approve/{t}", data={"action": action, "reason": "eval", "code": code}).status_code
            again = client.post(f"/approve/{t}", data={"action": "approve", "code": code}).status_code
            if no_reason != 409:
                out.find("high", f"{action} by link without a reason: {no_reason}, expected 409")
            db.rollback()
            audited = db.execute(
                text("select count(*) from audit_event where action = :a and entity_id = :n and after->>'via' = 'emailed link'"),
                {"a": f"request.{action}", "n": rno},
            ).scalar_one()
            if (bare, done, again, audited) != (409, 200, 409, 1):
                out.find("high", f"{action} by link: {(bare, done, again, audited)}, expected (409, 200, 409, 1)")

        # 6. a deputy: the plant head names the purchase manager; the next request goes to the deputy
        head = loc["head_user"]
        deputy = next(c for c in login_of if c != head and c != org[head]["manager_code"])
        deputies.name_deputy(db, slug, login_of[head], deputy, lib.TODAY, lib.TODAY + timedelta(days=3), "eval leave")
        asker = next(c for c in requesters if c not in login_of)
        dno = request(D("50000.00"), asker)
        workflow.submit(db, slug, dno, sod.user(1), today=lib.TODAY)
        got_deputy = db.execute(text("select delegate from approval_step where request_no = :n"), {"n": dno}).scalar()
        if got_deputy != deputy:
            out.find("high", f"the deputy {deputy} did not get {dno} (delegate {got_deputy})")
    finally:
        mail.send_email = previous_send  # type: ignore[assignment]
        pin_today(None)
        db.close()
    return out
