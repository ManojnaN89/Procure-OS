"""E78: company states and who may sign in (task 7.1, D-04, DF-36), written from the block 0d spec only.

A fresh company is made from the main demo profile in state setup (`demo reset --state setup`, spec 4.8) and walked
through every state the spec allows: setup -> cutover (the platform team's CLI) -> live (an administrator proposes, the
second administrator approves) -> suspended (security) -> resumed. In each state the eval signs in as the store, a
requester, a director, Finance and both administrators through `/api/auth/login` and compares with the spec's table
(4.1): everyone in setup and live; only `company.cutover` and `admin.*` logins in cutover, the others refused with the
spec's text; nobody when suspended, and a token issued before is refused at its next request. `/api/auth/me` carries the
state; the main app holds both banner texts. A worker job of the company stays queued in cutover and while suspended and
runs after go-live and after resume. Every move the spec refuses is tried (exit 2 or 409, with its text) and changes
nothing; every allowed move leaves exactly one `tenant_state_change` row and one `company.state` audit row with its reason
(and approver for go-live). The proposer cannot approve the go-live alone.

DF-36: `POST /api/admin/users` with role Admin is refused and makes no login; a user creation that fails in the company
database leaves no login and no profile; a login whose last step never ran (`provisioned_at` null) is refused like a
wrong password until `p2p.cli user repair` finishes it; a login with no profile older than ten minutes is removed by the
repair, a younger one is kept; `create_user(role="Admin")` by a person is refused, by the system allowed.

Another company is unchanged at the end. Two rules are broken on purpose and must be caught: (1) a suspended company
still signs in (`company_state.allows_sign_in` answering None), (2) `create_user` committing the profile before the login
(an orphan profile after a failure in the login step).
"""

from typing import Any

import env
import lib
from block0d import (
    BANNER_CUTOVER,
    BANNER_SETUP,
    NO_GOING_BACK,
    REASON_NEEDED,
    SIGN_IN_CUTOVER,
    SUSPENDED,
    Company,
    control,
    control_write,
    moves,
    patched,
    snapshot,
    state_of,
)
from block1 import Checker, js
from lib import Outcome

ID = "E78"
TITLE = "Company states: setup, cutover, live, suspended; who may sign in; user creation in two databases is safe"
REQUIREMENT = (
    "Task 7.1, D-04 (states Setup / Cutover / Live / Suspended and what each allows), D-22 (go-live by two "
    "administrators), DF-36 (user creation writes both databases safely; no Admin login made by one administrator)"
)
PEOPLE = ("admin", "admin2", "finance", "store", "production", "director")
MAY_IN_CUTOVER = {"admin", "admin2", "finance"}  # Admin and Finance hold company.cutover or admin.* (tenant 0080)


def run(ctx: lib.Ctx) -> Outcome:
    env.forget_tenants("e78")
    try:
        return _run(ctx)
    finally:
        env.forget_tenants("e78")


def detail(r: Any) -> str:
    return str(js(r).get("detail") or r.text)


def _run(ctx: lib.Ctx) -> Outcome:  # noqa: C901 - one scenario, step by step
    from p2p.platform import tenants, users, worker

    out = Outcome(True)
    check = Checker(out)
    lib.capture_mail()

    # ---- another company: made new, it starts in setup ----------------------------------------------------------
    other = env.slug("e78b")
    check(tenants.create_tenant(other, "Other company").ok, "the second company could not be made")
    check(state_of(other) == "setup", f"a new company starts in setup; it is {state_of(other)}")
    check(moves(other) == [], f"a new company has no state history yet: {moves(other)}")
    users.create_user(other, f"admin.{other}@eval.example", lib.PASSWORD, "Other Admin", "Admin")
    before_other = snapshot(other)

    co = Company.make("e78", "apex", state="setup")
    check(co.state() == "setup", f"demo reset --state setup leaves the company in setup; it is {co.state()}")
    check(moves(co.slug) == [], f"--state setup makes no move: {moves(co.slug)}")

    # ---- the banners of the main app (4.1, 4.7) ------------------------------------------------------------------
    ui = "\n".join(p.read_text(encoding="utf-8") for p in (lib.ROOT / "ui" / "js").rglob("*.js"))
    check(BANNER_SETUP in ui and BANNER_CUTOVER in ui, "the main app (ui/js) does not carry both banner texts of 4.1")
    admin_ui = "\n".join(p.read_text(encoding="utf-8") for p in (lib.ROOT / "frontend" / "src").rglob("*.tsx"))
    check(
        BANNER_SETUP in admin_ui and BANNER_CUTOVER in admin_ui,
        "the admin app does not carry the banner texts ('Banner in both apps', 4.1; 4.7 names only ui/js)",
        "low",
    )

    matrix: dict[str, dict[str, int]] = {}

    def signs_in(state: str, people: tuple[str, ...] = PEOPLE) -> None:
        """The sign-in table of 4.1 for one state."""
        got: dict[str, int] = {}
        for person in people:
            r = co.sign_in(person)
            got[person] = r.status_code
            if state in ("setup", "live") or (state == "cutover" and person in MAY_IN_CUTOVER):
                check(r.status_code == 200, f"{state}: {person} must sign in, got {r.status_code} {detail(r)[:150]}")
                if r.status_code == 200:
                    me = co.client.get("/api/auth/me", headers={"Authorization": "Bearer " + js(r)["access_token"]})
                    check(
                        js(me).get("company_state") == state,
                        f"{state}: /api/auth/me company_state is {js(me).get('company_state')!r}",
                    )
            else:
                text_ = SIGN_IN_CUTOVER if state == "cutover" else SUSPENDED
                check(
                    r.status_code == 403 and text_ in detail(r),
                    f"{state}: {person} must be refused 403 '{text_}', got {r.status_code} {detail(r)[:150]}",
                )
        matrix[state] = got

    def refused_move(args: tuple[str, ...], words: str, what: str) -> None:
        state, n = co.state(), len(moves(co.slug))
        code, said = co.cli(*args)
        usage = "invalid choice" in said or "error: argument" in said  # argparse also exits 2: not a refusal
        check(
            code == 2 and not usage and words.lower() in said.lower(),
            f"{what}: exit {code}, expected 2 with '{words}': {said.strip()[-200:]}",
        )
        check(co.state() == state and len(moves(co.slug)) == n, f"{what} changed the state or wrote a move")

    probes: list[str] = []

    def probe(job: Any) -> None:
        probes.append(job.tenant)

    worker.register("e78.probe")(probe)

    def job_status(job_id: int) -> str:
        found = control("select status from job where id = :i", i=job_id)
        return str(found[0]["status"]) if found else "-"

    def run_jobs() -> None:
        for _ in range(5):
            if not worker.run_once(tenant=co.slug):
                break

    op = ("--reason", "eval", "--operator", "Eval Operator")

    # ---- setup: everyone signs in; the refused moves -------------------------------------------------------------
    signs_in("setup")
    refused_move(("company", "state", co.slug, "setup", *op), "is not allowed", "setup -> setup")
    refused_move(("company", "state", co.slug, "live", *op), "is not allowed", "setup -> live outside a demo reset")
    refused_move(("company", "resume", co.slug, *op), "", "resume a company that is not suspended")
    refused_move(
        ("company", "suspend", co.slug, "--kind", "security", "--reason", "", "--operator", "Eval Operator"),
        REASON_NEEDED,
        "a move without a reason",
    )
    early = co.go_live()
    check(
        early.status_code == 409 and "only a company in cutover goes live" in detail(early),
        f"go-live in setup: {early.status_code} {detail(early)[:200]}",
    )
    store_setup = js(co.sign_in("store"))

    # ---- setup -> cutover (the platform team) ------------------------------------------------------------------
    code, said = co.cutover()
    check(code == 0 and co.state() == "cutover", f"company cutover: exit {code}, state {co.state()}: {said[-300:]}")
    if co.state() != "cutover":
        return out
    signs_in("cutover")
    token = store_setup.get("access_token")
    if token:
        r = co.client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
        check(r.status_code == 403, f"the store's token from setup still works in cutover: {r.status_code}")
    if store_setup.get("refresh_token"):
        r = co.client.post("/api/auth/refresh", json={"refresh_token": store_setup["refresh_token"]})
        check(r.status_code in (401, 403), f"the store refreshed its setup token in cutover: {r.status_code}")
    j1 = worker.enqueue(co.slug, "e78.probe", {"n": 1})
    run_jobs()
    check(job_status(j1) == "queued" and not probes, f"cutover: a job ran ({job_status(j1)}, {probes})")
    refused_move(("company", "cutover", co.slug, *op), "not allowed", "cutover -> cutover")
    code, said = co.cutover_import(co.dataset / "cutover", "finance")
    check(code == 0, f"the cutover import of cutover/ (needed for go-live) exit {code}: {said[-300:]}", "medium")

    none = co.call("admin", "POST", "/api/company/go-live", {"reason": ""})
    check(none.status_code == 422, f"go-live without a reason: {none.status_code}")
    asked = co.go_live("admin")
    body = js(asked)
    pid = body.get("proposal_id")
    check(
        asked.status_code == 202 and body.get("status") == "waiting" and (body.get("old"), body.get("new")) == ("cutover", "live"),
        f"go-live proposed: {asked.status_code} {asked.text[:300]}",
    )  # fmt: skip
    again = co.go_live("admin2")
    check(again.status_code == 409, f"a second go-live while one waits: {again.status_code}")
    check(co.state() == "cutover", f"go-live applied before a second administrator approved: {co.state()}")
    if pid:
        own = co.approve(int(pid), "admin")
        director = co.approve(int(pid), "director")
        check(own.status_code == 403, f"the proposer approved the go-live alone: {own.status_code}")
        check(
            director.status_code == 403, f"a director (no admin.settings) approved the go-live: {director.status_code}"
        )
        check(co.state() == "cutover", "a refused approval changed the state")
        done = co.approve(int(pid), "admin2")
        check(done.status_code == 200, f"the second administrator's approval: {done.status_code} {done.text[:200]}")
    check(co.state() == "live", f"after the second administrator's approval the company is {co.state()}")
    if co.state() != "live":
        return out

    # ---- live ----------------------------------------------------------------------------------------------------
    signs_in("live")
    run_jobs()
    check(
        job_status(j1) == "done" and co.slug in probes, f"live: the job left from cutover did not run: {job_status(j1)}"
    )
    refused_move(("company", "state", co.slug, "setup", *op), NO_GOING_BACK, "live -> setup")
    refused_move(("company", "cutover", co.slug, *op), "", "live -> cutover")
    late = co.go_live()
    check(late.status_code == 409, f"go-live of a live company: {late.status_code}")
    tokens = {p: js(co.sign_in(p)).get("access_token") for p in ("admin", "store")}

    # ---- live -> suspended (security) ---------------------------------------------------------------------------
    code, said = co.cli("company", "suspend", co.slug, "--kind", "security", "--reason", "eval: a leaked password", "--operator", "Eval Operator")  # fmt: skip
    row = control("select state, state_before_suspension from tenant where slug = :s", s=co.slug)[0]
    check(
        code == 0 and (row["state"], row["state_before_suspension"]) == ("suspended", "live"),
        f"suspend: exit {code}, {row}: {said[-200:]}",
    )
    signs_in("suspended")
    for person, tok in tokens.items():
        if tok:
            r = co.client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"})
            check(
                r.status_code == 403 and SUSPENDED in detail(r),
                f"suspended: {person}'s token from before still works: {r.status_code} {detail(r)[:100]}",
            )
    j2 = worker.enqueue(co.slug, "e78.probe", {"n": 2})
    run_jobs()
    check(job_status(j2) == "queued", f"suspended: a job ran ({job_status(j2)})")
    refused_move(("company", "suspend", co.slug, "--kind", "commercial", *op), "not allowed", "suspend twice")
    refused_move(("company", "state", co.slug, "setup", *op), "not allowed", "suspended -> setup")

    # mutation 1: a suspended company still signs in
    from p2p.platform import company_state

    with patched(company_state, "allows_sign_in", lambda *a, **k: None) as n:
        r = co.sign_in("admin")
        tok = tokens.get("admin")
        kept = co.client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok}"}) if tok else None
        caught = r.status_code == 200 or (kept is not None and kept.status_code == 200)
    m1 = "caught" if (n and caught) else ("not applied" if not n else "NOT caught")
    out.metrics["mutation: a suspended company still signs in"] = m1
    if m1 != "caught":
        out.find("high" if m1 == "NOT caught" else "medium", f"mutation 'suspended company signs in': {m1}")

    # ---- suspended -> live (resume) ---------------------------------------------------------------------------
    code, said = co.cli("company", "resume", co.slug, *op)
    check(code == 0 and co.state() == "live", f"resume: exit {code}, state {co.state()}: {said[-200:]}")
    signs_in("live", ("admin", "store"))
    run_jobs()
    check(job_status(j2) == "done", f"after resume the waiting job did not run: {job_status(j2)}")

    # ---- the history: one control row and one audit row per move ------------------------------------------------
    hist = moves(co.slug)
    path = [(m["from_state"], m["to_state"]) for m in hist]
    want = [("setup", "cutover"), ("cutover", "live"), ("live", "suspended"), ("suspended", "live")]
    check(path == want, f"tenant_state_change rows {path}, expected {want}")
    check(all(m.get("reason") for m in hist), "a state change has no reason")
    if len(hist) == 4:
        go = hist[1]
        check(
            (go["by_kind"], str(go["by_id"]), str(go["approved_by"])) == ("user", str(co.uid("admin")), str(co.uid("admin2"))),
            f"the go-live row names {go['by_kind']} {go['by_id']} approved by {go['approved_by']}",
        )  # fmt: skip
        check(
            hist[0]["by_kind"] == "operator" and hist[2]["by_kind"] == "operator",
            "cutover and suspend are the platform team's",
        )
        check(hist[2]["kind"] == "security", f"the suspension kind is {hist[2]['kind']}")
    trail = co.audit("company.state")
    seen = [((a.get("before") or {}).get("state"), (a.get("after") or {}).get("state")) for a in trail]
    check(seen == want, f"company.state audit rows {seen}, expected {want}")
    check(all(a.get("entity") == "company" and a.get("entity_id") == co.slug and a.get("reason") for a in trail),
          "a company.state audit row lacks entity company, the slug or the reason")  # fmt: skip
    out.metrics["sign-in by state (status per person)"] = matrix
    out.metrics["moves"] = path

    # ---- DF-36 ----------------------------------------------------------------------------------------------------
    _user_creation(co, check, out)

    after_other = snapshot(other)
    check(after_other == before_other, f"another company changed: {before_other} -> {after_other}")
    out.metrics["checks"] = f"{check.done - len(check.failed)} of {check.done} passed"
    return out


def _logins(co: Company) -> dict[str, int]:
    found = control(
        "select u.email, u.id from app_user u join tenant t on t.id = u.tenant_id where t.slug = :s", s=co.slug
    )
    return {r["email"]: int(r["id"]) for r in found}


def _orphans(co: Company) -> list[int]:
    """Profiles in the company database whose login does not exist."""
    ids = set(_logins(co).values())
    return [int(r["user_id"]) for r in co.q("select user_id from user_profile") if int(r["user_id"]) not in ids]


def _user_creation(co: Company, check: Checker, out: Outcome) -> None:  # noqa: C901 - one scenario
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from p2p.platform import audit, ratelimit, routing, users
    from p2p.platform.sod import Actor

    tag = co.slug[-6:]
    # an Admin login through the admin API
    email = f"twin.{tag}@eval.example"
    r = co.call("admin", "POST", "/api/admin/users",
                {"email": email, "password": lib.PASSWORD, "display_name": "Twin", "role": "Admin"})  # fmt: skip
    check(r.status_code in (422, 403), f"POST /api/admin/users with role Admin: {r.status_code}, expected 422")
    if r.status_code == 403:
        out.notes.append("POST /api/admin/users role Admin answered 403 (block 0c's choice); the 0d spec says 422")
    check(email not in _logins(co), "an Admin login was made by one administrator")

    # the company step fails: no login and no profile are left
    before = _logins(co)
    real = audit.record

    def failing(db: Any, actor: Any, action: str, *a: Any, **k: Any) -> Any:
        if action == "user.create":
            raise RuntimeError("eval: the company database step fails")
        return real(db, actor, action, *a, **k)

    lost = f"lost.{tag}@eval.example"
    with patched(audit, "record", failing):
        try:
            users.create_user(co.slug, lost, lib.PASSWORD, "Forced Failure", "Requester")
            raised = False
        except Exception:  # noqa: BLE001 - the failure we forced
            raised = True
    check(raised, "a forced failure in the company step did not reach the caller")
    check(_logins(co) == before, f"a failed creation left a login: {set(_logins(co)) - set(before)}")
    check(
        not co.q("select 1 from user_profile where display_name = 'Forced Failure'"), "a failed creation left a profile"
    )
    check(_orphans(co) == [], f"profiles without a login: {_orphans(co)}")

    # the process died after the company step: provisioned_at stays null
    half = f"half.{tag}@eval.example"
    uid = users.create_user(co.slug, half, lib.PASSWORD, "Half Made", "Requester")
    control_write("update app_user set provisioned_at = null where id = :i", i=uid)

    def login() -> Any:
        ratelimit.reset()  # refused sign-ins of this eval must not end in 429
        return co.client.post("/api/auth/login", json={"email": half, "password": lib.PASSWORD})

    ratelimit.reset()
    wrong = co.client.post("/api/auth/login", json={"email": half, "password": "not the password at all"})
    first = login()
    check(first.status_code == 401 and detail(first) == detail(wrong),
          f"a login whose last step never ran: {first.status_code} {detail(first)} (wrong password says {detail(wrong)})")  # fmt: skip

    # a login without a profile: removed by the repair when older than ten minutes, kept when younger
    hashed = users.hasher.hash(lib.PASSWORD)
    tenant_id = control("select id from tenant where slug = :s", s=co.slug)[0]["id"]
    for name, minutes in (("old", 11), ("young", 2)):
        control_write(
            "insert into app_user (tenant_id, email, password_hash, active, created_at, provisioned_at) "
            "values (:t, :e, :h, true, now() - make_interval(mins => :m), null)",
            t=tenant_id, e=f"{name}.{tag}@eval.example", h=hashed, m=minutes,
        )  # fmt: skip
    code, said = co.cli("user", "repair", co.slug)
    check(code == 0, f"user repair: exit {code}: {said[-300:]}")
    check(login().status_code == 200, "after the repair the half-made login still cannot sign in")
    left = _logins(co)
    check(f"old.{tag}@eval.example" not in left, "the repair kept a login without a profile older than ten minutes")
    check(f"young.{tag}@eval.example" in left, "the repair removed a login without a profile younger than ten minutes")
    fixes = [(a.get("after") or {}).get("fixed") for a in co.audit("user.repair")]
    check("provisioned" in fixes and "login_removed" in fixes, f"user.repair audit rows: {fixes}")
    out.metrics["user repair"] = {"exit": code, "fixes": fixes}
    control_write("delete from app_user where email = :e", e=f"young.{tag}@eval.example")

    # the Admin role only from the system
    try:
        users.create_user(co.slug, f"adm.{tag}@eval.example", lib.PASSWORD, "Self Made Admin", "Admin",
                          actor=Actor("user", str(co.uid("admin"))))  # fmt: skip
        by_person = "made"
    except ValueError as exc:
        by_person = str(exc)
    check("another administrator approves it" in by_person, f"create_user(role='Admin') by a person: {by_person}")
    try:
        users.create_user(co.slug, f"sys.{tag}@eval.example", lib.PASSWORD, "System Admin", "Admin")
        by_system = "made"
    except ValueError as exc:
        by_system = str(exc)
    check(by_system == "made", f"create_user(role='Admin') by the system was refused: {by_system}")

    # mutation 2: the profile is committed before the login, and the login step fails
    def profile_first(
        slug: str, email_: str, password: str, display_name: str, role: str = "Requester", **_: Any
    ) -> int:
        new_id = max(_logins(co).values()) + 1000
        with Session(routing.engine_for(slug)) as ts:
            ts.execute(text("insert into user_profile (user_id, display_name, role) values (:i, :n, :r)"),
                       {"i": new_id, "n": display_name, "r": role})  # fmt: skip
            ts.commit()
        raise RuntimeError("eval: the login step fails")

    with patched(users, "create_user", profile_first) as n:
        try:
            users.create_user(co.slug, f"m2.{tag}@eval.example", lib.PASSWORD, "Orphan", "Requester")
        except RuntimeError:
            pass
        caught = bool(_orphans(co))
    m2 = "caught" if (n and caught) else ("not applied" if not n else "NOT caught")
    out.metrics["mutation: profile committed before the login"] = m2
    if m2 != "caught":
        out.find("high", f"mutation 'profile before login': {m2}")
    co.db.execute(text("delete from user_profile where display_name = 'Orphan'"))
    co.db.commit()
