"""E02: which approval rule applies and who is in the chain, checked against an independent reading of the rule table."""

import random
from datetime import date, timedelta
from decimal import Decimal

import lib
from lib import D, Outcome, pct, rows

ID = "E02"
TITLE = "Approval routing follows the rule table and the organisation"
REQUIREMENT = "Rule 8, the plan and D36: a rule is chosen by value band, spend type, category, department, location and the date it is in force, rows starting '+' add approvers; its chain becomes real people"

# the order in which a named dimension makes a row more specific (the setting approval_precedence, else this)
PRECEDENCE = ["category_code", "department_code", "location_code", "spend_type"]


class NoRule(Exception):
    pass


def d(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


def oracle_rule(
    rules: list[dict[str, str]],
    doc: str,
    value: Decimal,
    on: date,
    spend: str,
    category: str | None,
    dept: str | None,
    loc: str | None = None,
) -> tuple[str, int, list[str]]:
    fits = []
    for r in rules:
        if r["document"] != doc or not (D(r["min_value"]) <= value <= D(r["max_value"])):
            continue
        if not (d(r["effective_from"]) <= on and (d(r["effective_to"]) is None or on <= d(r["effective_to"]))):
            continue
        if (
            r["category_code"] not in ("ANY", category or "ANY")
            or r["department_code"] not in ("ANY", dept or "ANY")
            or r["spend_type"] not in ("ANY", spend)
            or (r.get("location_code") or "ANY") not in ("ANY", loc or "ANY")
        ):
            continue
        # a row named in an earlier dimension outranks any mix of later ones: compare the named flags in order
        score = tuple((r.get(col) or "ANY") != "ANY" for col in PRECEDENCE)
        fits.append((score, int(r["version"]), r))
    adding = [f for f in fits if f[2]["approver_chain"].startswith("+")]
    fits = [f for f in fits if not f[2]["approver_chain"].startswith("+")]
    if not fits:
        raise NoRule("none")
    best = max(f[0] for f in fits)
    top = [f for f in fits if f[0] == best]
    if len({f[2]["rule_code"] for f in top}) > 1:
        raise NoRule("ambiguous")
    pick = max(top, key=lambda f: f[1])[2]
    chain = [c.strip() for c in pick["approver_chain"].split(">") if c.strip()]
    chain = [] if chain == ["auto"] else chain
    best_version: dict[str, tuple] = {}
    for f in adding:  # one version (the highest in force) of each adding rule
        if f[2]["rule_code"] not in best_version or f[1] > best_version[f[2]["rule_code"]][1]:
            best_version[f[2]["rule_code"]] = f
    for f in sorted(best_version.values(), key=lambda f: ([not x for x in f[0]], f[2]["rule_code"])):
        for role in f[2]["approver_chain"].lstrip("+").split(">"):
            if role.strip() and role.strip() not in chain:
                chain.append(role.strip())
    return pick["rule_code"], int(pick["version"]), chain


def oracle_people(
    chain: list[str],
    requester: str | None,
    dept: str | None,
    on: date,
    org: dict,
    depts: dict,
    delegations: list[dict],
    loc: str | None = None,
    loc_heads: dict | None = None,
) -> list[tuple[str, str | None]]:
    by_designation: dict[str, str] = {}
    for code in sorted(org):
        by_designation.setdefault(org[code]["designation"], code)
    # how the company names its chain roles (its approver roles: a designation, the requester's manager, the head)
    named = {role: value for role, source, value in ROLES if source == "designation"}
    managers = {role for role, source, _ in ROLES if source == "requester_manager"} | {"manager"}
    heads = {role for role, source, _ in ROLES if source == "department_head"} | {"dept_head"}
    plant_heads = {role for role, source, _ in ROLES if source == "location_head"}
    people: list[tuple[str, str | None]] = []
    for role in chain:
        if role in managers:
            who = org[requester]["manager_code"] if requester else depts.get(dept, "")
        elif role in heads:
            who = depts.get(dept, "") if dept else ""
        elif role in plant_heads:
            who = (loc_heads or {}).get(loc, "") if loc else ""
        elif role == "procurement_head":
            who = depts.get("PUR", "")
        elif role in named:
            who = by_designation.get(named[role], "")
        else:
            raise NoRule(f"unknown role {role}")
        if not who:
            raise NoRule(f"nobody for {role}")
        if who == requester or (people and people[-1][0] == who):
            continue
        stand_in = next(
            (
                x["to_user"]
                for x in sorted(delegations, key=lambda x: x["start_date"], reverse=True)
                if x["from_user"] == who
                and d(x["start_date"]) <= on
                and (d(x["end_date"]) is None or on <= d(x["end_date"]))
            ),
            None,
        )
        people.append((who, stand_in if stand_in != requester else None))
    if chain and not people:
        raise NoRule("only the requester")
    return people


ROLES: list[tuple[str, str, str | None]] = []  # the company's approver roles: (role, source, value)


def run(ctx) -> Outcome:
    from p2p.approvals import rules as platform

    global ROLES
    out = Outcome(True)
    ROLES = lib.demo_company().approver_roles
    world = lib.build_world("e02", with_stock=False)
    db = world.db
    rule_rows = rows("master", "approval_rule")
    org = {r["code"]: r for r in rows("master", "user")}
    depts = {r["code"]: r["head_user"] for r in rows("master", "department")}
    loc_heads = {r["code"]: r.get("head_user", "") for r in rows("master", "location")}
    order = next((s["value"] for s in rows("master", "setting") if s["key"] == "approval_precedence"), "")
    if order:
        PRECEDENCE[:] = [f"{p.strip()}_code" if p.strip() != "spend_type" else "spend_type" for p in order.split(">")]
    delegations = rows("master", "delegation")
    rng = random.Random(11)

    values: list[Decimal] = []
    for r in rule_rows:
        for edge in (D(r["min_value"]), D(r["max_value"])):
            values += [
                edge,
                edge + Decimal("0.01"),
                max(edge - Decimal("0.01"), Decimal(0)),
            ]
    values += [Decimal(str(round(10 ** rng.uniform(0, 7.3), 2))) for _ in range(150 if ctx.quick else 700)]
    dates = [
        date(2024, 11, 1),
        date(2024, 10, 31),
        date(2026, 3, 31),
        date(2026, 4, 1),
        date(2026, 11, 6),
        date(2027, 6, 1),
    ]
    for r in rule_rows:
        for key in ("effective_from", "effective_to"):
            if r[key]:
                dates += [
                    d(r[key]) - timedelta(days=1),
                    d(r[key]),
                    d(r[key]) + timedelta(days=1),
                ]
    dates += [date(2024, 11, 1) + timedelta(days=rng.randint(0, 800)) for _ in range(40)]
    docs = sorted({r["document"] for r in rule_rows})

    cases = rule_ok = rule_none_ok = chain_ok = chain_cases = chain_people = 0
    mismatches: list[str] = []
    for value in values:
        for on in rng.sample(dates, k=3):
            doc = rng.choice(docs)
            spend = rng.choice(["opex", "capex"])
            requester = rng.choice([None, *org]) if doc == "request" else None
            dept = org[requester]["department_code"] if requester else rng.choice([None, *sorted(depts)])
            loc = rng.choice([None, *sorted(loc_heads)]) if doc == "request" else None
            cases += 1
            try:
                want = oracle_rule(rule_rows, doc, value, on, spend, None, dept, loc)
            except NoRule as exc:
                want = None
                reason = str(exc)
            try:
                got_rule = platform.find_rule(
                    db, doc, value, on, spend_type=spend, category=None, department=dept, location=loc
                )
                got = (got_rule.code, got_rule.version, list(got_rule.roles))
            except platform.ApprovalRuleError:
                got = None
            if want == got:
                rule_ok += 1
                rule_none_ok += want is None
            else:
                mismatches.append(f"{doc} {value} on {on} {spend}: platform {got}, oracle {want if want else reason}")
                continue
            if want is None:
                continue
            chain_cases += 1
            try:
                people = oracle_people(want[2], requester, dept, on, org, depts, delegations, loc, loc_heads)
            except NoRule:
                people = None
            try:
                steps = platform.resolve_chain(
                    db, tuple(want[2]), requester=requester, department=dept, on_date=on, location=loc
                )
                got_people = [(s.approver, s.delegate) for s in steps]
            except platform.ApprovalRuleError:
                got_people = None
            if people == got_people:
                chain_ok += 1
                chain_people += people is not None
            else:
                mismatches.append(
                    f"chain {want[2]} requester {requester} dept {dept} on {on}: platform {got_people}, oracle {people}"
                )
    out.metrics = {
        "cases": cases,
        "rule agreement %": pct(rule_ok, cases),
        "cases with no rule": rule_none_ok,
        "chain cases": chain_cases,
        "chain agreement %": pct(chain_ok, chain_cases),
        "chains that name people": chain_people,
    }
    if chain_cases and not chain_people:
        out.find("high", "no approval chain resolved to a person: the chains were not really compared")
    if rule_ok != cases:
        out.find(
            "high",
            f"{cases - rule_ok} rule lookup(s) disagree with the oracle; first: {mismatches[0]}",
        )
    if chain_ok != chain_cases:
        out.find(
            "high",
            f"{chain_cases - chain_ok} chain(s) resolve differently from the oracle; first: {[m for m in mismatches if m.startswith('chain')][0]}",
        )
    return out
