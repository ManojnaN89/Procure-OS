"""E51: maker-checker on the company's controls (D-22) on the main demo company, with its two administrators.

Apex Pumps has two logins with the Admin role (admin and admin2). For every kind of control change the setup screens
offer (a policy, a tolerance, an approval rule version, a buying route, supplier selection and touchless rule version, a
role given, an approval limit, an automation level, the kill switch turned off, a person deactivated, a manager changed)
one administrator proposes it, the two take turns as maker. The eval reads the value itself (the setup tables, the
login in the control database, the automation gate) and the expected old value from the dataset's own files where they
hold it, and checks: the change is only waiting; nothing changed; the maker cannot approve it, nor can a director who is
no administrator; the other administrator approves it and exactly the proposed value applies; the history names maker
and approver; the audit log has the proposal, its approval and its run by the right people.

Then: a rejected change keeps the old value and can never run later; the kill switch on is immediate and needs nobody;
a change expires seven days after it is proposed and then applies nothing; a second change to a setting that waits is
refused, another setting is not held up; "Add person" never makes an Admin; nobody changes or approves a change to their
own access, roles, limits, login or manager. Four rules are broken on purpose and the eval must notice each.
"""

import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import env
import lib
from lib import D, Outcome, rows
from sqlalchemy import text

ID = "E51"
TITLE = "Control changes wait for a second administrator; nobody approves their own change or their own access"
REQUIREMENT = (
    "D-22 (second administrator): a change to rules, rule versions, policies, tolerances, automation, roles, limits, "
    "logins and managers applies only when a different administrator approves it; the proposer never approves; a "
    "rejection keeps the old value; stopping automation is immediate; an unapproved change expires after seven days; one "
    "change at a time per setting; the history shows maker and approver; nobody widens or approves their own access"
)
DOMAIN = "apexpumps.example"
FUTURE = "2027-04-01"  # a new rule version from the next financial year


@dataclass
class Case:
    name: str
    verb: str
    path: str
    body: dict[str, Any]
    read: Callable[[], Any]  # the value as it stands, read by the eval
    want_before: Any  # the oracle: what it is now
    want_after: Any  # and what it must be once approved


def run(ctx: lib.Ctx) -> Outcome:
    env.forget_tenants("e51")  # left by an earlier run that stopped half way (the demo logins are unique)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e51")


def _run(ctx: lib.Ctx) -> Outcome:  # noqa: C901 - one scenario, step by step
    from fastapi.testclient import TestClient
    from p2p.agents import autonomy
    from p2p.main import app
    from p2p.platform import demo, routing, users
    from p2p.platform.tokens import create_access_token
    from sqlalchemy.orm import Session

    out = Outcome(True)
    lib.capture_mail()
    slug = env.slug("e51")
    folder = Path(tempfile.mkdtemp(prefix="e51_"))
    demo.reset_company(demo.company("apex"), folder / "dataset", credentials=None, slug=slug)
    db = Session(routing.engine_for(slug))
    client = TestClient(app)
    who = {"admin": "Admin", "admin2": "Admin", "director": "Approver", "buyer": "Buyer", "planthead": "Approver",
           "store": "Store", "ap": "AP"}  # fmt: skip
    uid = {k: int(users.user_id_by_email(slug, f"{k}@{DOMAIN}") or 0) for k in who}
    h = {k: {"Authorization": f"Bearer {create_access_token(uid[k], slug, r)}"} for k, r in who.items()}
    if not uid["admin"] or not uid["admin2"]:
        out.find("high", f"the demo company has no two administrators: {uid}")
        return out

    def one(sql: str, **p: Any) -> Any:
        db.rollback()
        return db.execute(text(sql), p).scalar()

    def call(verb: str, path: str, body: dict[str, Any], who_: str) -> Any:
        if verb == "delete":
            return client.request("DELETE", path, json=body, headers=h[who_])
        return getattr(client, verb)(path, json=body, headers=h[who_])

    def approve(pid: int, who_: str) -> Any:
        return client.post(f"/api/admin/proposals/{pid}/approve", json={}, headers=h[who_])

    def login_works(person: str) -> bool:
        r = client.post("/api/auth/login", json={"email": f"{person}@{DOMAIN}", "password": demo.DEV_PASSWORD})
        return r.status_code == 200

    def setting(name: str, key: str) -> str:
        return next(r["value"] for r in rows("master", name) if r["key"] == key)

    def count(table: str) -> int:
        return int(one(f"select count(*) from {table}"))  # noqa: S608 - fixed names

    def policy(key: str) -> str:
        found = client.get("/api/setup/policies", headers=h["admin"]).json()["settings"]
        return str(next(p["value"] for p in found if p["key"] == key))

    def in_force(path: str, **match: Any) -> dict[str, Any]:
        versions = client.get(f"/api/setup/{path}", headers=h["admin"]).json()["versions"]
        return next(v for v in versions if v["in_force"] and all(v.get(k) == x for k, x in match.items()))

    # the oracle's old values, from the dataset's own files
    manager = {r["code"]: r["manager_code"] for r in rows("master", "user")}
    store_code = next(r["code"] for r in rows("master", "user") if r["email"] == f"store@{DOMAIN}")
    req02 = next(r for r in rows("master", "approval_rule") if r["rule_code"] == "REQ-02")
    route_now = in_force("buying-route", category=None)
    selection_now = in_force("supplier-selection")
    touchless_now = in_force("touchless")
    new_route = {**{k: v for k, v in route_now["values"].items() if v is not None}}
    new_route["two_quotes_from"] = str(D(new_route["two_quotes_from"]) + 1000)

    cases = [
        Case("policy (budget_policy)", "put", "/api/setup/policies/budget_policy", {"value": "warn"},
             lambda: policy("budget_policy"),
             setting("setting", "budget_policy"), "warn"),
        Case("tolerance (price_pct)", "put", "/api/setup/tolerances/price_pct", {"value": "3.00"},
             lambda: D(one("select value from tolerance where key = 'price_pct' and category_code = 'ANY'")),
             D(setting("tolerance", "price_pct")), D("3.00")),
        Case("approval rule version (REQ-02 to the purchase manager)", "post", "/api/setup/approval-rules",
             {"rule_code": "REQ-02", "document": "request", "min_value": req02["min_value"],
              "max_value": req02["max_value"], "approver_chain": "purchase_manager", "effective_from": FUTURE},
             lambda: one("select string_agg(version || ':' || approver_chain || ':' || coalesce(effective_to::text, '-'), "
                         "' ' order by version) from approval_rule where rule_code = 'REQ-02'"),
             f"1:{req02['approver_chain']}:-",
             f"1:{req02['approver_chain']}:{date.fromisoformat(FUTURE) - timedelta(days=1)} 2:purchase_manager:-"),
        Case("buying route version", "post", "/api/setup/buying-route",
             {"values": new_route, "category": None, "effective_from": FUTURE},
             lambda: count("buying_route_rule where category_code is null"),
             count("buying_route_rule where category_code is null"),
             count("buying_route_rule where category_code is null") + 1),
        Case("supplier selection version", "post", "/api/setup/supplier-selection",
             {"method": selection_now["method"], "weights": selection_now["weights"],
              "tie_break": selection_now["tie_break"], "input_credit": not selection_now["input_credit"],
              "effective_from": FUTURE},
             lambda: count("supplier_selection_rule"), count("supplier_selection_rule"),
             count("supplier_selection_rule") + 1),
        Case("touchless rule version", "post", "/api/setup/touchless",
             {"value_limit": touchless_now["value_limit"], "price_pct": "1", "effective_from": FUTURE},
             lambda: count("touchless_rule"), count("touchless_rule"), count("touchless_rule") + 1),
        Case("role given (Store to the buyer)", "post", f"/api/setup/people/{uid['buyer']}/roles", {"role": "Store"},
             lambda: one("select count(*) from user_role where user_id = :u and role = 'Store'", u=uid["buyer"]), 0, 1),
        Case("approval limit (plant head, orders)", "put", f"/api/setup/people/{uid['planthead']}/limits",
             {"document": "po", "max_value": "50000.00"},
             lambda: one("select max_value from user_limit where user_id = :u and document = 'po'", u=uid["planthead"]),
             None, D("50000.00")),
        Case("automation level (rfq.award to act alone)", "put", "/api/setup/automation/rfq.award", {"level": "auto"},
             lambda: one("select level from autonomy_setting where decision_type = 'rfq.award'"), None, "auto"),
        Case("person deactivated (AP)", "post", f"/api/setup/people/{uid['ap']}/active", {"active": False},
             lambda: login_works("ap"), True, False),
        Case("manager changed (storekeeper)", "put", f"/api/setup/people/{uid['store']}/manager",
             {"manager_code": "U08"}, lambda: one("select manager_code from org_user where code = :c", c=store_code),
             manager[store_code], "U08"),
    ]  # fmt: skip

    def audit_of(pid: int) -> list[tuple[str, str]]:
        db.rollback()
        got = db.execute(
            text("select action, actor_id from audit_event where entity = 'proposal' and entity_id = :e order by id"),
            {"e": str(pid)},
        )
        return [(r[0], str(r[1])) for r in got]

    def proposed(r: Any) -> int | None:
        return int(r.json()["proposal_id"]) if r.status_code == 202 and r.json().get("status") == "waiting" else None

    # 1. every kind of change: waits, the maker and a non-admin cannot approve, the other admin can
    results: dict[str, str] = {}
    for n, case in enumerate(cases):
        maker, checker = ("admin", "admin2") if n % 2 == 0 else ("admin2", "admin")
        problems: list[str] = []
        before = case.read()
        if before != case.want_before:
            problems.append(f"before: {before!r}, oracle {case.want_before!r}")
        r = call(case.verb, case.path, {**case.body, "reason": f"E51 {case.name}"}, maker)
        pid = proposed(r)
        if pid is None:
            out.find("high", f"{case.name}: proposing answered {r.status_code} {r.text[:200]}, expected 202 waiting")
            results[case.name] = "not proposed"
            continue
        if case.read() != before:
            problems.append(f"applied before approval: {case.read()!r}")
        own, director = approve(pid, maker), approve(pid, "director")
        if own.status_code != 403 or director.status_code != 403:
            problems.append(
                f"approved by the maker {own.status_code} / a director {director.status_code}, expected 403"
            )
        if case.read() != before:
            problems.append(f"applied after a refused approval: {case.read()!r}")
        done = approve(pid, checker)
        if done.status_code != 200 or done.json().get("execution") != "ok":
            problems.append(f"the other admin's approval: {done.status_code} {done.text[:200]}")
        after = case.read()
        if after != case.want_after:
            problems.append(f"after approval: {after!r}, oracle {case.want_after!r}")
        area, key = db.execute(
            text("select payload->>'area', payload->>'key' from proposal where id = :i"), {"i": pid}
        ).one()
        history = client.get("/api/setup/changes", params={"area": area, "key": key}, headers=h["admin"]).json()
        if not history or (history[0]["by_user"], history[0]["approved_by"]) != (str(uid[maker]), str(uid[checker])):
            problems.append(f"history {area}/{key}: {[(x['by_user'], x['approved_by']) for x in history[:1]]}")
        trail = audit_of(pid)
        want = [("proposal.create", str(uid[maker])), ("proposal.approved", str(uid[checker])),
                ("proposal.executed_ok", str(uid[checker]))]  # fmt: skip
        if [t for t in trail if t[0] in {w[0] for w in want}] != want:
            problems.append(f"audit {trail}")
        results[case.name] = "ok" if not problems else "; ".join(problems)
        for p in problems:
            out.find("high", f"{case.name} ({maker} proposes, {checker} approves): {p}")
    out.metrics["kinds of change checked / correct"] = f"{len(cases)} / {sum(v == 'ok' for v in results.values())}"
    out.metrics["per kind"] = results

    # 2. the kill switch: on at once, off waits for the other admin
    def gate() -> bool:
        db.rollback()
        return autonomy.gate(db, "po.issue", value=None, confidence=1.0).allowed

    open_before = gate()
    on = client.put("/api/setup/automation-kill-switch", json={"on": True, "reason": "E51 stop"}, headers=h["admin"])
    stopped = gate()
    off = client.put("/api/setup/automation-kill-switch", json={"on": False, "reason": "E51 go"}, headers=h["admin"])
    still = gate()
    off_pid = proposed(off)
    own_off = approve(off_pid, "admin").status_code if off_pid else None
    other_off = approve(off_pid, "admin2").status_code if off_pid else None
    again = gate()
    stop_row = client.get(
        "/api/setup/changes", params={"area": "automation", "key": "kill_switch"}, headers=h["admin"]
    ).json()
    out.metrics["kill switch: gate before / on at once / off waits / after approval"] = (
        f"{open_before} / {on.status_code} {not stopped} / {off.status_code} {not still} / {other_off} {again}"
    )
    if not open_before or on.status_code != 200 or stopped:
        out.find("high", f"the kill switch on was not immediate: {on.status_code}, gate open {stopped}")
    if off_pid is None or still or own_off != 403 or other_off != 200 or not again:
        out.find(
            "high", f"kill switch off: {off.status_code}, applied early {not still}, own {own_off}, other {other_off}"
        )
    on_rows = [x for x in stop_row if x["new_value"] == {"on": True}]
    if not on_rows or on_rows[0]["approved_by"] is not None or on_rows[0]["by_user"] != str(uid["admin"]):
        out.find("high", f"the kill switch on in the history: {on_rows[:1]}")

    # 3. a rejection keeps the old value and can never run later
    qty = lambda: one("select value from tolerance where key = 'qty_pct' and category_code = 'ANY'")  # noqa: E731
    r = call("put", "/api/setup/tolerances/qty_pct", {"value": "9", "reason": "E51 looser"}, "admin")
    pid = proposed(r)
    rejected = client.post(f"/api/admin/proposals/{pid}/reject", json={"reason": "too loose"}, headers=h["admin2"])
    late = approve(pid, "admin2") if pid else None
    out.metrics["rejected: value kept / approve later"] = f"{qty()} / {late.status_code if late else None}"
    if pid is None or rejected.status_code != 200 or D(qty()) != D(setting("tolerance", "qty_pct")):
        out.find(
            "high",
            f"a rejected change: {rejected.status_code}, value {qty()}, oracle {setting('tolerance', 'qty_pct')}",
        )
    if late is None or late.status_code != 409:
        out.find(
            "high", f"a rejected change approved later answered {late.status_code if late else None}, expected 409"
        )
    if ("proposal.rejected", str(uid["admin2"])) not in audit_of(pid or 0):
        out.find("high", "the rejection is not in the audit log")

    # 4. a change expires seven days after it is proposed, and then applies nothing
    days = lambda: policy("default_rfq_response_days")  # noqa: E731
    pid = proposed(
        call("put", "/api/setup/policies/default_rfq_response_days", {"value": "7", "reason": "E51"}, "admin")
    )
    span = seven_days(db, pid)
    db.execute(text("update proposal set expires_at = now() - interval '1 second' where id = :i"), {"i": pid})
    db.commit()  # seven days have gone by
    shown = [p for p in client.get("/api/setup/pending", headers=h["admin2"]).json() if p["proposal_id"] == pid]
    late = approve(pid, "admin2") if pid else None
    out.metrics["expiry: days to expire / approve after / value"] = (
        f"{span} / {late.status_code if late else None} / {days()}"
    )
    if (
        span != 7
        or late is None
        or late.status_code != 409
        or days() != setting("setting", "default_rfq_response_days")
    ):
        out.find("high", f"expiry: {span} days, approved after {late.status_code if late else None}, value {days()}")
    if not shown or not shown[0]["expired"] or "proposal.expired" not in [a for a, _ in audit_of(pid or 0)]:
        out.find("high", f"an expired change is not shown or audited as expired: {shown}")

    # 5. one change at a time per setting
    first = proposed(call("put", "/api/setup/policies/combine_window_days", {"value": "10", "reason": "E51"}, "admin"))
    second = call("put", "/api/setup/policies/combine_window_days", {"value": "12", "reason": "E51"}, "admin2")
    other = call("put", "/api/setup/policies/duplicate_window_days", {"value": "21", "reason": "E51"}, "admin2")
    out.metrics["duplicate pending change / another setting"] = f"{second.status_code} / {other.status_code}"
    if first is None or second.status_code != 409 or other.status_code != 202:
        out.find(
            "high", f"a second change to a waiting setting: {second.status_code}, another setting {other.status_code}"
        )

    # 6. "Add person" never makes an Admin
    email = f"twin.{slug}@eval.example"
    body = {"email": email, "password": lib.PASSWORD, "display_name": "Twin", "role": "Admin", "reason": "E51"}
    added = client.post("/api/setup/people", json=body, headers=h["admin"])
    out.metrics["add a person as Admin"] = added.status_code
    if added.status_code != 403 or users.user_id_by_email(slug, email):
        out.find("high", f"'Add person' with the Admin role answered {added.status_code}")

    # 7. nobody changes or approves a change to their own access
    own = {
        "give yourself a role": call("post", f"/api/setup/people/{uid['admin']}/roles", {"role": "Store", "reason": "x"}, "admin"),
        "set your own limit": call("put", f"/api/setup/people/{uid['admin']}/limits", {"document": "po", "max_value": "1", "reason": "x"}, "admin"),
        "deactivate yourself": call("post", f"/api/setup/people/{uid['admin']}/active", {"active": False, "reason": "x"}, "admin"),
    }  # fmt: skip
    out.metrics["own access, at once"] = {k: v.status_code for k, v in own.items()}
    for k, v in own.items():
        if v.status_code not in (403, 409) or proposed(v) is not None:
            out.find("high", f"an administrator may {k}: {v.status_code}")
    for_admin2 = {
        "role": call("post", f"/api/setup/people/{uid['admin2']}/roles", {"role": "Store", "reason": "E51"}, "admin"),
        "limit": call("put", f"/api/setup/people/{uid['admin2']}/limits", {"document": "po", "max_value": "1", "reason": "E51"}, "admin"),
        "login": call("post", f"/api/setup/people/{uid['admin2']}/active", {"active": False, "reason": "E51"}, "admin"),
        "manager": call("put", f"/api/setup/people/{uid['admin2']}/manager", {"manager_code": "U09", "reason": "E51"}, "admin"),
    }  # fmt: skip
    pending_own = {k: proposed(v) for k, v in for_admin2.items()}
    decided = {k: own_access_refused(db, approve, pid, "admin2") for k, pid in pending_own.items()}
    out.metrics["admin2 approves a change to their own (role, limit, login, manager)"] = decided
    for k, ok in decided.items():
        if ok is not True:
            out.find("high", f"admin2 could approve a change to their own {k} (or it was not proposed): {ok}")

    out.metrics["mutation checks (rule broken on purpose: caught?)"] = _mutations(
        db, call, approve, proposed, pending_own["role"], qty, out
    )
    return out


def seven_days(db: Any, pid: int | None) -> int | None:
    """Days between a proposal and its expiry, rounded to whole days."""
    if pid is None:
        return None
    db.rollback()
    made, ends = db.execute(text("select created_at, expires_at from proposal where id = :i"), {"i": pid}).one()
    return None if ends is None else round((ends - made).total_seconds() / 86400)


def own_access_refused(db: Any, approve: Callable[[int, str], Any], pid: int | None, person: str) -> bool | str:
    """The person a change is for cannot approve it, and it stays waiting."""
    if pid is None:
        return "not proposed"
    r = approve(pid, person)
    db.rollback()
    status = db.execute(text("select status from proposal where id = :i"), {"i": pid}).scalar()
    return True if r.status_code == 403 and status == "pending" else f"{r.status_code}, {status}"


def _mutations(
    db: Any, call: Any, approve: Any, proposed: Any, own_pid: int | None, qty: Callable[[], Any], out: Outcome
) -> dict[str, str]:
    from p2p.platform import proposals, setting_changes, sod

    results: dict[str, str] = {}

    def reject(pid: int | None, who: str) -> None:
        if pid:
            call("post", f"/api/admin/proposals/{pid}/reject", {"reason": "E51 mutation"}, who)

    # the proposer may approve their own change
    saved_sod = sod.check_segregation
    sod.check_segregation = lambda *a, **k: None
    try:
        pid = proposed(call("put", "/api/setup/policies/supplier_ack_due_days", {"value": "3", "reason": "m"}, "admin"))
        got = approve(pid, "admin").status_code if pid else None
        results["the proposer may approve"] = "caught" if got == 200 else f"NOT caught ({got})"
    finally:
        sod.check_segregation = saved_sod

    # a change applies when it is proposed
    saved_submit = setting_changes.submit

    def at_once(db_: Any, maker: Any, kind: str, args: dict[str, Any], reason: str, **kw: Any) -> dict[str, Any]:
        r = saved_submit(db_, maker, kind, args, reason, **kw)
        setting_changes.HANDLERS[kind][1](db_, maker, args, reason, True)
        return r

    setting_changes.submit = at_once
    try:
        before = qty()
        pid = proposed(call("put", "/api/setup/tolerances/qty_pct", {"value": "7", "reason": "m"}, "admin"))
        results["a change applies before approval"] = "caught" if qty() != before else "NOT caught"
        reject(pid, "admin2")
    finally:
        setting_changes.submit = saved_submit

    # the person a change is for may approve it
    saved_guard = proposals.GUARDS[setting_changes.ACTION]
    proposals.GUARDS[setting_changes.ACTION] = lambda *a: None
    try:
        verdict = own_access_refused(db, approve, own_pid, "admin2")
        results["the person a change is for may approve it"] = "caught" if verdict is not True else "NOT caught"
    finally:
        proposals.GUARDS[setting_changes.ACTION] = saved_guard

    # a change waits thirty days instead of seven
    saved_expiry = setting_changes.EXPIRES_AFTER
    setting_changes.EXPIRES_AFTER = timedelta(days=30)
    try:
        pid = proposed(
            call("put", "/api/setup/policies/rfq_reminder_after_days", {"value": "3", "reason": "m"}, "admin")
        )
        results["a change waits thirty days"] = "caught" if seven_days(db, pid) != 7 else "NOT caught"
        reject(pid, "admin2")
    finally:
        setting_changes.EXPIRES_AFTER = saved_expiry
    for name, verdict in results.items():
        if verdict != "caught":
            out.find("high", f"mutation '{name}' was not caught: the eval cannot tell this rule is broken")
    return results
