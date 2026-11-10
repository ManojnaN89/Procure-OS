"""E22: company setup. Only an Admin can change it; every change is logged with who, why, old and new; values outside what
a setting allows are refused and the edges are accepted; payment runs, vendor status and releasing a payment block can
never be set to act alone; a new version of an approval rule never leaves a gap or changes yesterday's decision; Admin
never gains a business permission; and every setting the screen offers is really read by the code that uses it."""

import random
import re
from datetime import timedelta
from decimal import Decimal

import lib
from fastapi.testclient import TestClient
from lib import Outcome
from sqlalchemy import text

ID = "E22"
TITLE = "Company setup is admin-only, logged, validated, and its locks cannot be undone"
REQUIREMENT = (
    "Plan P1-24 and rule 5/8: who approves, tolerances and automation are data an Admin changes with a reason; "
    "payment runs, vendor activation and releasing a payment block are never automatic; past decisions stay reproducible"
)

BACKEND = lib.ROOT / "backend" / "p2p"
SETUP = "/api/setup"
LOCKED = ("payment_run.approve", "supplier.set_status", "invoice.release_block")
BUSINESS = (
    "po.approve",
    "po.create",
    "po.send",
    "request.approve",
    "invoice.approve",
    "invoice.book",
    "payment.approve",
    "payment.propose",
    "rfq.send",
    "grn.create",
)
ADMIN_ACTIONS = ("admin.users", "admin.rules", "admin.settings")


def source_reads(key: str) -> set[str]:
    """Which settings table(s) the platform's own code reads this key from, found by reading the source."""
    found: set[str] = set()
    for path in BACKEND.rglob("*.py"):
        if path.name in ("setup_api.py",) or "migrations" in path.parts:
            continue
        body = path.read_text(encoding="utf-8")
        if key not in body:
            continue
        for m in re.finditer(re.escape(key), body):
            line_start = body.rfind("\n", 0, m.start()) + 1
            window = body[max(0, line_start - 200) : m.end() + 160]
            helper = re.search(r"(\w+)\(\s*db,\s*[\"']" + re.escape(key), body[line_start : m.end() + 5])
            if helper:  # read through a small function: look at which table that function queries
                definition = re.search(r"def " + re.escape(helper.group(1)) + r"\(.*?(?=\n\ndef |\n\n\n)", body, re.S)
                window += definition.group(0) if definition else ""
            if re.search(r"org_setting|_org_setting|org_set", window):
                found.add("org")
            if re.search(r"app_setting|_app_setting|app_set", window):
                found.add("app")
            if "DEFAULT_SETTINGS" in window:
                found.add("app")
    return found


def run(ctx) -> Outcome:
    from p2p.agents import autonomy
    from p2p.approvals import rules as approval_rules
    from p2p.formulas.calendar import business_today
    from p2p.main import app
    from p2p.platform import sod

    out = Outcome(True)
    rng = random.Random(22)
    world = lib.build_world("e22", with_stock=False)
    db = world.db
    db.commit()
    client = TestClient(app)
    admin = world.headers("Admin")
    findings: list[str] = []

    def snapshot(table: str) -> list:
        db.rollback()
        return [tuple(r) for r in db.execute(text(f"select * from {table} order by 1, 2")).all()]  # noqa: S608

    def changes() -> int:
        db.rollback()
        return int(db.execute(text("select count(*) from setup_change")).scalar_one())

    # 1. who may do what: the screens and every change are for the Admin role only
    reads = ["people", "roles", "approvers", "approval-rules", "policies", "automation", "changes"]
    writes = [
        ("put", f"{SETUP}/people/1/limits", {"document": "po", "max_value": "1", "reason": "x"}),
        ("put", f"{SETUP}/roles/Buyer", {"permissions": [], "reason": "x"}),
        ("put", f"{SETUP}/policies/budget_policy", {"value": "block", "reason": "x"}),
        ("put", f"{SETUP}/tolerances/price_pct", {"value": "1", "reason": "x"}),
        ("put", f"{SETUP}/automation/rfq.send", {"level": "auto", "reason": "x"}),
        ("put", f"{SETUP}/automation-kill-switch", {"on": True, "reason": "x"}),
        ("put", f"{SETUP}/approvers/owner", {"source": "requester_manager", "reason": "x"}),
        (
            "post",
            f"{SETUP}/approval-rules",
            {
                "document": "po",
                "min_value": "0",
                "max_value": "1",
                "approver_chain": "auto",
                "effective_from": "2030-01-01",
                "reason": "x",
            },
        ),
        (
            "post",
            f"{SETUP}/people",
            {"email": "a@b.example", "password": "long enough pw", "display_name": "x", "reason": "x"},
        ),
    ]
    before_all = changes()
    cells = wrong_gate = 0
    for role in [r for r in lib.ROLES if r != "Admin"] + [None]:
        h = world.headers(role) if role else {}
        want = 403 if role else 401
        for r in reads:
            cells += 1
            got = client.get(f"{SETUP}/{r}", headers=h).status_code
            if got != want:
                wrong_gate += 1
                findings.append(f"{role} GET {r} answered {got}, expected {want}")
        for verb, path, body in writes:
            cells += 1
            got = getattr(client, verb)(path, json=body, headers=h).status_code
            if got != want:
                wrong_gate += 1
                findings.append(f"{role} {verb} {path} answered {got}, expected {want}")
    if changes() != before_all:
        findings.append("a change was recorded for someone who is not an Admin")
    out.metrics["permission cells checked"] = cells
    out.metrics["permission cells wrong"] = wrong_gate

    # 2. every policy the screen offers: edges accepted, outside refused, each change logged with old and new
    spec = client.get(f"{SETUP}/policies", headers=admin).json()
    refused_wrong = accepted_wrong = logged_wrong = checked = 0
    for p in spec["settings"]:
        key, kind = p["key"], p["kind"]
        if kind == "bool":
            good, bad = [("true" if p["value"] == "false" else "false")], ["maybe", "1", ""]
        elif kind == "choice":
            good, bad = [c for c in p["choices"] if c != p["value"]][:1], ["nonsense", "WARN "]
        else:
            lo, hi = p["min"], p["max"]
            edge = [
                str(int(lo)) if kind == "int" else format(Decimal(str(lo)), "f"),
                str(int(hi)) if kind == "int" else format(Decimal(str(hi)), "f"),
            ]
            good = [e for e in edge if e != p["value"]][:2]
            bad = [str(lo - 1), str(hi + 1), "abc", "NaN", "Infinity", ""] + (["1.5"] if kind == "int" else [])
        for value in bad:
            checked += 1
            r = client.put(f"{SETUP}/policies/{key}", json={"value": value, "reason": "probe"}, headers=admin)
            if r.status_code != 422:
                accepted_wrong += 1
                findings.append(f"{key}={value!r} was not refused ({r.status_code})")
        for value in good:
            checked += 1
            n0, db_before = (
                changes(),
                {
                    t: db.execute(text(f"select value from {t} where key = :k"), {"k": key}).scalar()
                    for t in ("org_setting", "app_setting")
                },
            )  # noqa: S608
            r = client.put(f"{SETUP}/policies/{key}", json={"value": value, "reason": f"edge {value}"}, headers=admin)
            db.rollback()
            if r.status_code != 200:
                refused_wrong += 1
                findings.append(f"{key}={value!r} is inside its range but was refused: {r.text[:100]}")
                continue
            landed = [
                t
                for t in ("org_setting", "app_setting")
                if db.execute(text(f"select value from {t} where key = :k"), {"k": key}).scalar() == value
            ]  # noqa: S608
            row = db.execute(
                text(
                    "select old_value->>'value', new_value->>'value', reason, by_user from setup_change where area = 'policy' and key = :k order by id desc limit 1"
                ),
                {"k": key},
            ).one()
            expected_old = next((v for v in db_before.values() if v is not None), p["default"])
            audit_ok = db.execute(
                text(
                    "select count(*) from audit_event where action = 'setup.policy' and entity_id = :k and after->>'value' = :v"
                ),
                {"k": key, "v": value},
            ).scalar_one()
            if (
                len(landed) != 1
                or changes() != n0 + 1
                or row[1] != value
                or row[0] != expected_old
                or row[2] != f"edge {value}"
                or row[3] != str(world.logins["Admin"])
                or not audit_ok
            ):
                logged_wrong += 1
                findings.append(
                    f"{key}={value!r}: stored in {landed}, history old={row[0]!r} (expected {expected_old!r}), new={row[1]!r}"
                )
    out.metrics["policy values tried"] = checked
    out.metrics["policy problems (accepted bad / refused good / badly logged)"] = (
        f"{accepted_wrong} / {refused_wrong} / {logged_wrong}"
    )

    # 3. every policy offered is really read by the platform's own code, from the table the screen writes it to
    dead, wrong_store = [], []
    for p in spec["settings"]:
        reads_from = source_reads(p["key"])
        landed_in = [
            t
            for t in ("org", "app")
            if db.execute(text(f"select 1 from {t}_setting where key = :k"), {"k": p["key"]}).first()
        ]  # noqa: S608
        if not reads_from:
            dead.append(p["key"])
        elif landed_in and not set(landed_in) & reads_from:
            wrong_store.append(f"{p['key']} is saved to {landed_in} but read from {sorted(reads_from)}")
    out.metrics["policies the code never reads"] = len(dead)
    if dead:
        out.find("medium", f"settings offered on the screen that nothing reads: {dead}")
    if wrong_store:
        findings.append("; ".join(wrong_store))

    # 4. tolerances: edges, categories, and an update replaces rather than duplicates
    tol_problems = 0
    for t in spec["tolerances"]:
        for value, ok in (
            ("0", True),
            (str(int(t["max"])) if t["max"] is not None else "1000", True),
            ("-0.01", False),
            ("abc", False),
        ) + ((("100.01", False),) if t["max"] == 100 else ()):
            r = client.put(
                f"{SETUP}/tolerances/{t['key']}",
                json={"category": "ANY", "value": value, "reason": "edge"},
                headers=admin,
            )
            if (r.status_code == 200) != ok:
                tol_problems += 1
                findings.append(f"tolerance {t['key']}={value} answered {r.status_code}")
        db.rollback()
        rows = db.execute(
            text("select count(*) from tolerance where key = :k and category_code = 'ANY'"), {"k": t["key"]}
        ).scalar_one()
        if rows != 1:
            tol_problems += 1
            findings.append(f"tolerance {t['key']} has {rows} rows for ANY after updates")
    out.metrics["tolerance problems"] = tol_problems

    # 5. automation: the lock holds against every route there is
    locked_open = 0
    for dt in LOCKED:
        for level in ("off", "propose", "auto"):
            r = client.put(
                f"{SETUP}/automation/{dt}", json={"level": level, "value_cap": "1", "reason": "faster"}, headers=admin
            )
            if r.status_code == 200:
                locked_open += 1
                findings.append(f"{dt} was set to {level} through the screen")
            try:
                autonomy.configure(db, sod.user(world.logins["Admin"]), dt, level=level)
                if level != "propose":
                    locked_open += 1
                    findings.append(f"{dt} was set to {level} directly")
            except ValueError:
                db.rollback()
        db.execute(
            text(
                "insert into autonomy_setting (decision_type, level, min_confidence, max_wrong_rate) values (:d, 'auto', 0, 1) on conflict (decision_type) do update set level = 'auto', value_cap = null"
            ),
            {"d": dt},
        )
        db.commit()
        if autonomy.gate(db, dt, value=Decimal(1), confidence=1.0).allowed:
            locked_open += 1
            findings.append(f"the gate let {dt} act alone")
    shown = {d["decision_type"]: d for d in client.get(f"{SETUP}/automation", headers=admin).json()["decisions"]}
    if not all(shown[dt]["locked"] and shown[dt]["levels"] == ["propose"] for dt in LOCKED):
        locked_open += 1
        findings.append("the screen does not show the locked decisions as person-only")
    out.metrics["locked decisions that could be opened"] = locked_open

    # ... and the others obey what is set, including the kill switch
    gate_problems = 0
    for dt in ("rfq.send", "rfq.award", "quote.read"):
        cap, conf = Decimal(rng.randint(1000, 90000)), Decimal(rng.choice(["0.8", "0.9", "0.95"]))
        client.put(
            f"{SETUP}/automation/{dt}",
            json={
                "level": "auto",
                "value_cap": str(cap),
                "min_confidence": str(conf),
                "max_wrong_rate": "0.5",
                "reason": "trial",
            },
            headers=admin,
        )
        db.rollback()
        for value, c, want in (
            (cap, float(conf), True),
            (cap + Decimal("0.01"), float(conf), False),
            (cap, float(conf) - 0.01, False),
        ):
            if autonomy.gate(db, dt, value=value, confidence=c).allowed != want:
                gate_problems += 1
                findings.append(f"{dt}: value {value} confidence {c} should be {'allowed' if want else 'refused'}")
        client.put(f"{SETUP}/automation-kill-switch", json={"on": True, "reason": "test"}, headers=admin)
        db.rollback()
        if autonomy.gate(db, dt, value=Decimal(1), confidence=1.0).allowed:
            gate_problems += 1
            findings.append(f"{dt} acted with the kill switch on")
        client.put(f"{SETUP}/automation-kill-switch", json={"on": False, "reason": "test over"}, headers=admin)
        client.put(f"{SETUP}/automation/{dt}", json={"level": "propose", "reason": "trial over"}, headers=admin)
    out.metrics["automation gate problems"] = gate_problems

    # 6. Admin stays a setup role; nobody else gets the setup permissions
    admin_gain = other_gain = 0
    roles = {r["role"]: set(r["permissions"]) for r in client.get(f"{SETUP}/roles", headers=admin).json()["roles"]}
    for action in BUSINESS:
        r = client.put(
            f"{SETUP}/roles/Admin",
            json={"permissions": sorted(roles["Admin"] | {action}), "reason": "convenience"},
            headers=admin,
        )
        admin_gain += r.status_code == 200
    for action in ADMIN_ACTIONS:
        r = client.put(
            f"{SETUP}/roles/Buyer",
            json={"permissions": sorted(roles["Buyer"] | {action}), "reason": "convenience"},
            headers=admin,
        )
        other_gain += r.status_code == 200
    r = client.put(f"{SETUP}/roles/Admin", json={"permissions": ["audit.view"], "reason": "tidy"}, headers=admin)
    admin_gain += r.status_code == 200  # would lock everybody out of the setup
    db.rollback()
    now = {r[0] for r in db.execute(text("select action from role_permission where role = 'Admin'"))}
    if any(not a.startswith(("admin.", "audit.")) for a in now) or not set(ADMIN_ACTIONS) <= now:
        admin_gain += 1
        findings.append(f"Admin now holds {sorted(now)}")
    out.metrics["Admin or other roles given what they must not have"] = admin_gain + other_gain

    # 7. approval chains: a new version starts tomorrow, yesterday's decisions keep the old one, the table has no gaps
    chain_problems = 0
    today = business_today()
    tomorrow = today + timedelta(days=1)
    in_force = [
        r
        for r in client.get(f"{SETUP}/approval-rules", headers=admin).json()["rules"]
        if r["in_force"] and r["document"] in ("request", "po")
    ]
    if client.get(f"{SETUP}/approval-rules", headers=admin).json()["problems"]:
        findings.append("the dataset's own rule table has a gap or an overlap")
        chain_problems += 1
    vocabulary = ["manager", "dept_head", "director", "procurement_head", "finance_manager"]
    changed = 0
    for rule in rng.sample(in_force, min(8, len(in_force))):
        value = (
            (Decimal(rule["min_value"]) + Decimal(rule["max_value"])) / 2
            if Decimal(rule["max_value"]) < 900_000_000
            else Decimal(rule["min_value"]) + 1
        )
        probe = dict(spend_type=rule["spend_type"] if rule["spend_type"] != "ANY" else "opex")
        old = (
            approval_rules.find_rule(db, rule["document"], value, today, **probe)
            if rule["document"] == "request"
            else approval_rules.find_rule(db, rule["document"], value, today)
        )
        chain = ["auto"] if rule["chain"] == ["auto"] else rng.sample(vocabulary, rng.randint(1, 3))
        before = snapshot("approval_rule")
        r = client.post(
            f"{SETUP}/approval-rules",
            json={
                "rule_code": rule["rule_code"],
                "document": rule["document"],
                "min_value": rule["min_value"],
                "max_value": rule["max_value"],
                "category_code": rule["category"],
                "department_code": rule["department"],
                "spend_type": rule["spend_type"],
                "approver_chain": ">".join(chain),
                "effective_from": tomorrow.isoformat(),
                "reason": "review",
            },
            headers=admin,
        )
        db.rollback()
        if r.status_code != 201:
            chain_problems += 1
            findings.append(f"a valid new version of {rule['rule_code']} was refused: {r.text[:120]}")
            continue
        changed += 1
        if rule["document"] == "request":
            same_day = approval_rules.find_rule(db, "request", value, today, **probe)
            next_day = approval_rules.find_rule(db, "request", value, tomorrow, **probe)
        else:
            same_day = approval_rules.find_rule(db, rule["document"], value, today)
            next_day = approval_rules.find_rule(db, rule["document"], value, tomorrow)
        if (same_day.code, same_day.version, same_day.roles) != (old.code, old.version, old.roles):
            chain_problems += 1
            findings.append(f"{rule['rule_code']}: today's decision changed after a version that starts tomorrow")
        if next_day.version != old.version + 1 or list(next_day.roles) != ([] if chain == ["auto"] else chain):
            chain_problems += 1
            findings.append(f"{rule['rule_code']}: tomorrow does not use the new chain {chain}")
        if snapshot("approval_rule") == before:
            chain_problems += 1
    invalid = [
        {"effective_from": (today - timedelta(days=1)).isoformat()},
        {"approver_chain": "manager>wizard"},
        {"approver_chain": "manager>manager"},
        {"max_value": "0.5", "min_value": "1"},
    ]
    if in_force:
        base = in_force[0]
        for patch in invalid:
            before = snapshot("approval_rule")
            body = {
                "rule_code": base["rule_code"],
                "document": base["document"],
                "min_value": base["min_value"],
                "max_value": base["max_value"],
                "category_code": base["category"],
                "department_code": base["department"],
                "spend_type": base["spend_type"],
                "approver_chain": ">".join(base["chain"]),
                "effective_from": tomorrow.isoformat(),
                "reason": "bad",
                **patch,
            }
            r = client.post(f"{SETUP}/approval-rules", json=body, headers=admin)
            if r.status_code != 422 or snapshot("approval_rule") != before:
                chain_problems += 1
                findings.append(f"a bad rule change {sorted(patch)} was not refused cleanly ({r.status_code})")
        mid = next(
            (
                r
                for r in in_force
                if r["document"] == "request" and Decimal(r["min_value"]) > 0 and Decimal(r["max_value"]) < 900_000_000
            ),
            None,
        )
        if mid:  # a band that leaves a gap, and one that overlaps its neighbour
            for lo, hi in (
                (str(Decimal(mid["min_value"]) + 5), mid["max_value"]),
                (mid["min_value"], str(Decimal(mid["max_value"]) + 5000)),
            ):
                before = snapshot("approval_rule")
                body = {
                    "rule_code": mid["rule_code"],
                    "document": "request",
                    "min_value": lo,
                    "max_value": hi,
                    "category_code": mid["category"],
                    "department_code": mid["department"],
                    "spend_type": mid["spend_type"],
                    "approver_chain": ">".join(mid["chain"]),
                    "effective_from": tomorrow.isoformat(),
                    "reason": "bad",
                }
                r = client.post(f"{SETUP}/approval-rules", json=body, headers=admin)
                if r.status_code != 422 or snapshot("approval_rule") != before:
                    chain_problems += 1
                    findings.append(f"a band {lo}..{hi} leaving a gap or an overlap was accepted ({r.status_code})")
    if client.get(f"{SETUP}/approval-rules", headers=admin).json()["problems"]:
        chain_problems += 1
        findings.append("after the changes the rule table has a gap or an overlap")
    out.metrics["rule versions created / chain problems"] = f"{changed} / {chain_problems}"

    # 8. a policy's value at a past moment can be read back
    from datetime import UTC, datetime

    from p2p.platform.setup_api import policy_value_on

    client.put(f"{SETUP}/policies/price_band_pct", json={"value": "7", "reason": "history probe"}, headers=admin)
    client.put(f"{SETUP}/policies/price_band_pct", json={"value": "9", "reason": "history probe"}, headers=admin)
    db.rollback()
    ids = [
        r[0]
        for r in db.execute(text("select id from setup_change where key = 'price_band_pct' order by id desc limit 2"))
    ][::-1]
    for stamp, i in zip((datetime(2025, 1, 1, tzinfo=UTC), datetime(2025, 6, 1, tzinfo=UTC)), ids, strict=True):
        db.execute(text("update setup_change set at = :t where id = :i"), {"t": stamp, "i": i})
    db.commit()
    replay_ok = [policy_value_on(db, "price_band_pct", datetime(2025, m, 15, tzinfo=UTC)) for m in (2, 8)] == ["7", "9"]
    if not replay_ok:
        findings.append("the value of a policy on a past date could not be read back from the history")
    out.metrics["history replay works"] = replay_ok

    # 9. nobody can lock the last administrator out
    people = client.get(f"{SETUP}/people", headers=admin).json()["people"]
    me = next(p for p in people if p["email"] == world.emails["Admin"])
    own_role = next(r["id"] for r in me["roles"] if r["role"] == "Admin")
    removed = client.delete(f"{SETUP}/people/{me['user_id']}/roles/{own_role}?reason=oops", headers=admin).status_code
    deactivated = client.post(
        f"{SETUP}/people/{me['user_id']}/active", json={"active": False, "reason": "oops"}, headers=admin
    ).status_code
    if removed == 200 or deactivated == 200:
        findings.append("an administrator could remove their own Admin role or deactivate themselves")
    out.metrics["self-lockout refused"] = removed == 409 and deactivated == 409

    if findings:
        out.find("high", f"{len(findings)} problem(s); first: {findings[0]}")
        out.notes.extend(findings[:12])
    return out
