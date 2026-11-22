"""E80: readiness checks and separation coverage (task 7.3, Plan line 1086, D-22), written from the block 0d spec only.

Both demo profiles are made with `--state setup` and read through `GET /api/company/readiness`: no failed blocker of the
setup group (the only failed blocker of all groups is `cutover_import`, nothing loaded yet); the twelve checks of 4.3 are
there with their group and severity; the requester warnings of `approvers` name exactly the people an independent oracle
finds (from the dataset's people, departments, locations and request rules in force, the company's `approver_role`, and
who holds `request.create`: a requester whose every approver of some rule is themself); `supplier_bank` lists every
account the dataset marks verified (by a file, `****` and four digits) and every active supplier without one.

Then gaps are planted one by one on the main demo company through its two administrators (each a D-22 change approved
by the other): a policy change left waiting (`pending_changes`, then rejected); a new version of REQ-02 from today that
starts at Rs 20,000.00 (`approval_bands`, with `coverage_problems` text); `supplier.verify_bank` taken from Finance and
given to Buyer, so the buyer is the only one who may verify the bank account the buyer may propose (`separation`, SEP-06
naming the buyer: a maker is never their own checker); the plant head, the purchase manager and the director deactivated
one by one (`approvers`, then `duties` and the `chain:po` and `chain:payment_run` controls with nobody to check). Each
plant fails exactly its code and no other blocker. A company with one administrator fails `admins`, and still fails
when that person holds the Admin role twice. API and CLI agree (`--json` equals the API's checks; plain lines and exit
code); reading readiness writes no audit row; the store may not read it; an unknown group is 422. The second company is
unchanged.

Mutations (cases that tell a wrong rule from the right one): (1) separation counting the maker as their own checker
(the buyer-only SEP-06 case would pass); (2) the two-administrator rule counting one person's two Admin roles twice.
"""

import json
import re
from collections import defaultdict
from typing import Any

import env
import lib
from block0d import Company, check_of, control, failed, master, problems_of, snapshot
from block1 import Checker, js
from lib import Outcome

ID = "E80"
TITLE = "Readiness checks find every gap, including separation coverage, before a company may cut over or go live"
REQUIREMENT = (
    "Task 7.3 (readiness checks including separation coverage: every rule that needs a second or different person has "
    "enough eligible people), Plan line 1086 (go-live readiness requires two administrators), FR32 (five different "
    "people for the five duties)"
)
CODES = {
    "masters": "setup", "admins": "setup", "approval_bands": "setup", "approvers": "setup", "separation": "setup",
    "duties": "setup", "pending_changes": "setup", "company_bank": "setup", "supplier_bank": "setup",
    "holidays": "setup", "cutover_import": "cutover", "stock_position": "cutover",
}  # fmt: skip
SPEC_WARNED = {"apex": {"Meera Iyer"}, "brightwave": {"Farah Khan", "Vivek Menon"}}  # spec section 6
WARNING = re.compile(r"^(.+?): a request in (\S+) has no one else to approve it")


def run(ctx: lib.Ctx) -> Outcome:
    env.forget_tenants("e80")
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e80")


def _run(ctx: lib.Ctx) -> Outcome:
    out = Outcome(True)
    check = Checker(out)
    lib.capture_mail()
    bw = Company.make("e80b", "brightwave", state="setup")
    ap = Company.make("e80", "apex", state="setup")
    for co in (ap, bw):
        _baseline(co, check, out)
    before_bw = snapshot(bw.slug)
    _plants(ap, check, out)
    _one_admin(ap, check, out)
    check(snapshot(bw.slug) == before_bw, "the second company changed while the first was planted with gaps")
    out.metrics["checks"] = f"{check.done - len(check.failed)} of {check.done} passed"
    return out


# ---- the oracle ---------------------------------------------------------------------------------------------------


def holders(co: Company, action: str) -> set[str]:
    """Login names of the eligible people holding a permission (active, provisioned login; profile; role grants it)."""
    emails = {
        int(r["id"]): r["email"]
        for r in control(
            "select u.id, u.email from app_user u join tenant t on t.id = u.tenant_id where t.slug = :s and u.active "
            "and u.provisioned_at is not null",
            s=co.slug,
        )
    }
    found = co.q(
        "select distinct ur.user_id from user_role ur join role_permission rp on rp.role = ur.role "
        "join user_profile p on p.user_id = ur.user_id where rp.action = :a",
        a=action,
    )
    return {emails[int(r["user_id"])].split("@")[0] for r in found if int(r["user_id"]) in emails}


def warned_oracle(co: Company) -> dict[str, set[str]]:
    """Requesters for whom some request rule in force (in their department and location) leaves nobody but themself."""
    people = {u["code"]: u for u in master(co.dataset, "user") if u.get("active", "true") != "false"}
    heads = {d["code"]: d.get("head_user", "") for d in master(co.dataset, "department")}
    loc_heads = {loc["code"]: loc.get("head_user", "") for loc in master(co.dataset, "location")}
    sources = {r["role"]: (r["source"], r["value"]) for r in co.q("select role, source, value from approver_role")}
    may_ask = holders(co, "request.create")
    today = lib.TODAY.isoformat()
    rules = [
        r for r in master(co.dataset, "approval_rule")
        if r["document"] == "request" and r["approver_chain"] not in ("auto", "")
        and not r["approver_chain"].startswith("+")
        and r["effective_from"] <= today and (not r.get("effective_to") or r["effective_to"] >= today)
    ]  # fmt: skip

    def person(role: str, who: dict[str, str]) -> str | None:
        source, value = sources.get(role, ("", ""))
        if source == "designation":
            found = sorted(c for c, u in people.items() if u["designation"] == value)
            return found[0] if found else None
        if source == "location_head":
            return loc_heads.get(who["location_code"]) or None
        if source == "department_head":
            return heads.get(who["department_code"]) or None
        if source == "department_head_of":
            return heads.get(value or "") or None
        if source == "requester_manager":
            return who.get("manager_code") or None
        return None

    warned: dict[str, set[str]] = defaultdict(set)
    for code, who in people.items():
        if who["email"].split("@")[0] not in may_ask:
            continue
        for r in rules:
            if r["department_code"] not in ("ANY", who["department_code"]) or r.get("location_code", "ANY") not in (
                "ANY",
                who["location_code"],
            ):
                continue
            others = {person(role, who) for role in r["approver_chain"].split(">")} - {code, None}
            if not others:
                warned[who["name"]].add(r["rule_code"])
    return dict(warned)


def _baseline(co: Company, check: Checker, out: Outcome) -> None:  # noqa: C901 - one company's checks
    name = co.profile
    audit_before = co.audit_count()
    setup = co.readiness("setup")
    every = co.readiness("all")
    check(setup.get("_status") == 200, f"{name}: readiness answered {setup.get('_status')}")
    check(setup.get("ready") is True and not failed(setup), f"{name}: setup blockers {sorted(failed(setup))}")
    check(failed(every) <= {"cutover_import"}, f"{name}: blockers of all groups {sorted(failed(every))}")
    check(
        every.get("ready") is False if failed(every) else True, f"{name}: ready is {every.get('ready')} with blockers"
    )
    check(every.get("state") == "setup", f"{name}: readiness says state {every.get('state')}")
    check(
        every.get("blockers") == len([c for c in every.get("checks", []) if not c.get("ok") and c.get("severity") == "blocker"]),
        f"{name}: the blocker count {every.get('blockers')} does not match the checks",
    )  # fmt: skip
    seen = {c.get("code"): c.get("group") for c in every.get("checks", [])}
    check(set(CODES) <= set(seen), f"{name}: checks missing: {sorted(set(CODES) - set(seen))}")
    check(all(seen.get(c) == g for c, g in CODES.items() if c in seen), f"{name}: groups {seen}")
    check(check_of(every, "supplier_bank").get("severity") == "warning", f"{name}: supplier_bank is not a warning")

    # requester warnings against the oracle
    platform: dict[str, set[str]] = defaultdict(set)
    for c in every.get("checks", []):
        if c.get("code") == "approvers":
            for p in c.get("problems") or []:
                m = WARNING.match(str(p))
                if m:
                    platform[m.group(1)].add(m.group(2))
    oracle = warned_oracle(co)
    out.metrics[f"{name}: requesters warned (platform / oracle)"] = (
        f"{ {k: sorted(v) for k, v in platform.items()} } / { {k: sorted(v) for k, v in oracle.items()} }"
    )
    check(
        set(platform) == set(oracle), f"{name}: requester warnings name {sorted(platform)}, the oracle {sorted(oracle)}"
    )
    for who, codes in platform.items():
        check(
            codes <= oracle.get(who, set()),
            f"{name}: {who} warned for {sorted(codes)}, the oracle {sorted(oracle.get(who, set()))}",
        )
    if set(oracle) != SPEC_WARNED[name]:
        out.notes.append(
            f"spec gap: section 6 expects warnings for {sorted(SPEC_WARNED[name])} on {name}; the rules of 4.3 applied to "
            f"the dataset give {sorted(oracle)} (resolve_chain refuses a request whose every approver is the requester)"
        )

    # bank accounts verified by a file, suppliers without a verified account
    banks = master(co.dataset, "supplier_bank_account")
    probs = problems_of(every, "supplier_bank")
    missing = [b for b in banks if b.get("verified") == "true"
               and not any(f"{b['supplier_code']} ****{b['account_no'][-4:]}" in p and "verified by a file" in p for p in probs)]  # fmt: skip
    check(not missing, f"{name}: accounts verified by a file not listed: {[b['supplier_code'] for b in missing]}")
    paid = {b["supplier_code"] for b in banks if b.get("verified") == "true"}
    unpaid = {
        s["code"] for s in master(co.dataset, "supplier") if s.get("status") == "active" and s["code"] not in paid
    }
    said = {p.split(" ")[0] for p in probs if "cannot be paid" in p}
    check(said == unpaid, f"{name}: suppliers that cannot be paid {sorted(said)}, the oracle {sorted(unpaid)}")
    check(not any(b["account_no"] in p for b in banks for p in probs), f"{name}: a whole account number in readiness")

    # API and CLI agree; reading writes no audit row; permissions
    code, said_json = co.cli("company", "readiness", co.slug, "--json")
    try:
        cli_report = json.loads(said_json[said_json.index("{") :])
    except ValueError:
        cli_report = {}

    def shape(rep: dict[str, Any]) -> list[tuple[Any, ...]]:
        return sorted(
            (c.get("code"), c.get("severity"), c.get("ok"), tuple(c.get("problems") or []))
            for c in rep.get("checks", [])
        )

    check(
        shape(cli_report) == shape(every),
        f"{name}: the CLI's --json differs from the API ({len(cli_report.get('checks', []))} checks)",
    )
    code_all, plain = co.cli("company", "readiness", co.slug)
    code_setup, plain_setup = co.cli("company", "readiness", co.slug, "--group", "setup")
    check(code_all == 1 and re.search(r"^FAIL\s+cutover_import", plain, re.M) is not None,
          f"{name}: plain readiness of all groups: exit {code_all}: {plain[:300]}")  # fmt: skip
    check(code_setup == 0 and re.search(r"^warn\s+supplier_bank", plain_setup, re.M) is not None,
          f"{name}: plain readiness of the setup group: exit {code_setup}: {plain_setup[:300]}")  # fmt: skip
    check(co.audit_count() == audit_before, f"{name}: reading readiness wrote audit rows")
    store = co.holders("store")[0]
    check(co.call(store, "GET", "/api/company/readiness").status_code == 403, f"{name}: the store read readiness")
    check(co.call("admin", "GET", "/api/company/readiness", params={"group": "nope"}).status_code == 422,
          f"{name}: an unknown group is not 422")  # fmt: skip


# ---- planted gaps -----------------------------------------------------------------------------------------------


def _plants(co: Company, check: Checker, out: Outcome) -> None:  # noqa: C901 - one plant after another
    results: dict[str, str] = {}

    def blockers() -> set[str]:
        return failed(co.readiness("setup"))

    def change(maker: str, method: str, path: str, body: dict[str, Any]) -> int | None:
        r = co.call(maker, method, path, {**body, "reason": "eval: a planted gap"})
        pid = js(r).get("proposal_id")
        if r.status_code != 202 or not pid:
            out.find("high", f"proposing {method} {path}: {r.status_code} {r.text[:200]}")
            return None
        return int(pid)

    def approved(maker: str, method: str, path: str, body: dict[str, Any]) -> None:
        pid = change(maker, method, path, body)
        if pid and co.approve(pid, "admin2" if maker == "admin" else "admin").status_code != 200:
            out.find("high", f"the other administrator could not approve {method} {path}")

    def expect(name: str, before: set[str], code: str, exact: bool = True) -> set[str]:
        now = blockers()
        new = now - before
        results[name] = f"new blockers {sorted(new)}"
        check(code in now, f"{name}: {code} does not fail ({sorted(now)})")
        if exact:
            check(new <= {code}, f"{name}: newly failing {sorted(new)}, expected only {code}", "medium")
        check(before <= now, f"{name}: blockers cleared by the plant: {sorted(before - now)}", "medium")
        return now

    base = blockers()
    check(not base, f"the main demo company has setup blockers before any plant: {sorted(base)}")

    # 1. a change waiting for the second administrator
    pid = change("admin", "PUT", "/api/setup/policies/combine_window_days", {"value": "10"})
    expect("a change waiting", base, "pending_changes")
    if pid:
        co.reject(pid, "admin2")
    check("pending_changes" not in blockers(), "a rejected change still blocks")

    # 2. a gap in the request bands
    # review of 23 Nov: the setup screen rightly refuses a rule version that leaves a gap (422), so the gap is planted
    # as the owner, straight into the rule row (REQ-02 now starts at Rs 20,000.00), and readiness must report it
    from sqlalchemy import text as sql

    with lib.owner_session(co.slug) as owner:
        owner.execute(sql("update approval_rule set min_value = 20000.00 where rule_code = 'REQ-02'"))
        owner.commit()
    now = expect("a band gap (REQ-02 from 20,000.00)", base, "approval_bands")
    from p2p.platform import setup_api

    co.db.rollback()
    gaps = setup_api.coverage_problems(co.db, "request", lib.TODAY)
    co.db.rollback()
    said = problems_of(co.readiness("setup"), "approval_bands")
    check(
        bool(gaps) and all(any(g in p for p in said) for g in gaps),
        f"approval_bands problems {said}, coverage says {gaps}",
    )

    # 3. the buyer becomes the only one who may verify a bank account the buyer may propose
    roles = {r["role"]: set(r["permissions"]) for r in js(co.call("admin", "GET", "/api/setup/roles")).get("roles", [])}
    approved(
        "admin",
        "PUT",
        "/api/setup/roles/Finance",
        {"permissions": sorted(roles.get("Finance", set()) - {"supplier.verify_bank"})},
    )
    approved(
        "admin2",
        "PUT",
        "/api/setup/roles/Buyer",
        {"permissions": sorted(roles.get("Buyer", set()) | {"supplier.verify_bank"})},
    )
    checkers, makers = holders(co, "supplier.verify_bank"), holders(co, "supplier.propose")
    case = checkers == {"buyer"} and "buyer" in makers
    check(case, f"the SEP-06 case is not set up: verify_bank {sorted(checkers)}, propose {sorted(makers)}", "medium")
    now = expect("the buyer verifies only their own proposals", now, "separation")
    buyer = co.people["buyer"]["name"]
    sep = problems_of(co.readiness("setup"), "separation")
    named = [p for p in sep if p.startswith(f"{buyer} could not get") and p.rstrip(". ").endswith(buyer)]
    check(bool(named), f"SEP-06 must name {buyer} as maker and only checker: {sep}")
    results["mutation: the maker counted as their own checker"] = "caught" if (case and named) else "NOT caught"

    # 4. approvers leave one by one
    # (who else holds request.approve and payment.approve is the company's: only the first leaver is held to "exactly")
    for login, code, exact in (("planthead", "approvers", True), ("purchasemanager", "approvers", False),
                               ("director", "duties", False)):  # fmt: skip
        approved("admin", "POST", f"/api/setup/people/{co.uid(login)}/active", {"active": False})
        now = expect(f"{login} deactivated", now, code, exact)
    sep = problems_of(co.readiness("setup"), "separation")
    nobody = [p for p in sep if p.startswith("nobody may check")]
    check(len(nobody) >= 2, f"with no approvers left, order and payment-run controls must have nobody to check: {sep}")
    appr = " ".join(problems_of(co.readiness("setup"), "approvers"))
    check("plant_head" in appr and "purchase_manager" in appr and "director" in appr,
          f"approvers problems do not name the chain roles left without a person (the spec gives no text): {appr[:400]}",
          "low")  # fmt: skip
    out.metrics["plants"] = results
    if results.get("mutation: the maker counted as their own checker") != "caught":
        out.find("high", "mutation 'maker counted as own checker': the SEP-06 case does not tell it")


def _one_admin(ap: Company, check: Checker, out: Outcome) -> None:
    """A company made with one administrator, then the same person given the Admin role a second time."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from p2p.masters.importer import import_masters
    from p2p.platform import routing, tenants, users
    from p2p.platform.tokens import create_access_token

    slug = env.slug("e80one")
    check(tenants.create_tenant(slug, "One admin").ok, "the one-admin company could not be made")
    with Session(routing.engine_for(slug)) as db:
        import_masters(db, ap.dataset)
    uid = users.create_user(slug, f"solo.{slug}@eval.example", lib.PASSWORD, "Solo Admin", "Admin")

    def admins_ok() -> bool | None:
        h = {"Authorization": f"Bearer {create_access_token(uid, slug, 'Admin')}"}
        r = ap.client.get("/api/company/readiness", params={"group": "setup"}, headers=h)
        return check_of(js(r), "admins").get("ok") if r.status_code == 200 else None

    first = admins_ok()
    check(first is False, f"a company with one administrator: admins ok = {first}")
    with Session(routing.engine_for(slug)) as db:
        db.execute(
            text("insert into user_role (user_id, role, scope_location) values (:u, 'Admin', 'ANY')"), {"u": uid}
        )
        db.commit()
    twice = admins_ok()
    check(twice is False, f"one person holding Admin twice counts as two administrators: admins ok = {twice}")
    out.metrics["mutation: one person's two Admin roles counted twice"] = "caught" if twice is False else "NOT caught"
