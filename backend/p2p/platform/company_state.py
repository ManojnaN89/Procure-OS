"""Company states (D-04): setup, cutover, live, suspended. The only writer of `tenant.state`.

Setup is for setting up and testing (what is done there is archived at cutover, D-05). In cutover the open documents of
the old system are loaded and only the people who load them or administer the company may sign in; jobs wait. Live is
the running company. A suspended company (commercial or security) is closed to everyone and its jobs wait; it goes back
to the state it was in. The platform team moves a company to cutover, back to setup, into and out of suspension; the
company's administrators take it live (one proposes, another approves, D-22). A live company never goes back. Each move
is a `tenant_state_change` row (control database) and a `company.state` audit row (the company's own).
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from p2p.platform import audit, routing, tenants
from p2p.platform.control_models import Tenant, TenantStateChange
from p2p.platform.sod import Actor

STATES = ("setup", "cutover", "live", "suspended")
MOVES = {
    ("setup", "cutover"),
    ("cutover", "live"),
    ("cutover", "setup"),
    ("setup", "suspended"),
    ("cutover", "suspended"),
    ("live", "suspended"),
    ("suspended", "setup"),  # resume: only to the state it was in
    ("suspended", "cutover"),
    ("suspended", "live"),
    ("setup", "live"),  # a demo company made from its profile
}
SUSPENSION_KINDS = ("commercial", "security")
CUTOVER_SIGN_IN = "Sign-in opens at go-live: the company is moving its open documents to the platform"
SUSPENDED = "This company is suspended. Contact the platform team."
NOT_TAKING_ACTIONS = "This company is not taking actions right now"
NO_GOING_BACK = "a live company does not go back"
CLOSED = ("cutover", "suspended")  # jobs wait and signed links are refused


class StateError(Exception):
    """A move or an action the company's state does not allow. `status` is the HTTP answer (409 by default)."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class StateChange:
    slug: str
    from_state: str
    to_state: str
    at: datetime


def operator(name: str) -> Actor:
    """The platform team member who runs a command (never a company login)."""
    return Actor("operator", name)


def state_of(slug: str) -> str:
    with tenants.control_session() as s:
        state = s.scalar(select(Tenant.state).where(Tenant.slug == slug))
    if state is None:
        raise StateError(f"unknown company '{slug}'", 404)
    return str(state)


def slug_of_session(db: Session) -> str:
    database = str(db.get_bind().engine.url.database or "")
    slug = tenants.slug_of(database)
    if slug is None:
        raise StateError(f"{database} is not a company database", 500)
    return slug


def state_of_session(db: Session) -> str:
    """The state of the company whose database this session is on."""
    return state_of(slug_of_session(db))


def state_here(db: Session) -> str:
    """The state of the company this session is on; setup for a database that is no company's (a bare test database)."""
    if tenants.slug_of(str(db.get_bind().engine.url.database or "")) is None:
        return "setup"
    return state_of_session(db)


def _check(state: str, before: str | None, to: str, kind: str | None, path: str) -> None:
    if to not in STATES:
        raise StateError(f"unknown state '{to}'", 422)
    if state == "live" and to in ("setup", "cutover"):
        raise StateError(NO_GOING_BACK)
    resume_elsewhere = state == "suspended" and to != before
    if (state, to) not in MOVES or resume_elsewhere or ((state, to) == ("setup", "live") and path != "demo"):
        raise StateError(f"{state} -> {to} is not allowed")
    if to == "suspended" and kind not in SUSPENSION_KINDS:
        raise StateError(f"a suspension is {' or '.join(SUSPENSION_KINDS)}", 422)


def check_move(state: str, to: str) -> None:
    """Refuse a move the table does not allow (the platform team's commands check before they start)."""
    _check(state, None, to, None, "normal")


def _audit_actor(actor: Actor) -> Actor:
    """The company's audit log knows users, agents, services and rules: the platform team writes as a service."""
    return Actor("service", f"operator:{actor.id}") if actor.kind == "operator" else actor


def _by_kind(actor: Actor) -> str:
    if actor.kind == "user":
        return "user"
    return "operator" if actor.kind == "operator" else "system"


def move(
    slug: str,
    to: str,
    *,
    actor: Actor,
    reason: str,
    approved_by: str | None = None,
    kind: str | None = None,
    path: str = "normal",
    db: Session | None = None,
) -> StateChange:
    """Move a company to another state. A person's move (go-live) is written on the app's login and its audit row goes
    into `db` (the caller commits); the platform team's and the system's on the owner's login. Raises StateError."""
    if not reason or not reason.strip():
        raise StateError("a reason is needed", 422)
    owner = actor.kind != "user"
    opener = tenants._owner_control_session if owner else tenants.control_session  # noqa: SLF001 - the owner's moves
    now = datetime.now(UTC)
    with opener() as s:
        tenant = s.scalar(select(Tenant).where(Tenant.slug == slug).with_for_update())
        if tenant is None:
            raise StateError(f"unknown company '{slug}'", 404)
        state = tenant.state
        _check(state, tenant.state_before_suspension, to, kind, path)
        tenant.state = to
        tenant.state_changed_at = now
        tenant.state_before_suspension = state if to == "suspended" else None
        s.add(
            TenantStateChange(
                tenant_id=tenant.id, from_state=state, to_state=to, at=now, by_kind=_by_kind(actor), by_id=actor.id,
                approved_by=approved_by, kind=kind if to == "suspended" else None, reason=reason.strip(),
            )
        )  # fmt: skip
        s.commit()
    before, after = {"state": state}, {"state": to, "kind": kind}
    if db is not None:
        audit.record(
            db, _audit_actor(actor), "company.state", "company", slug, before=before, after=after, reason=reason.strip()
        )
    else:
        with Session(routing.engine_for(slug)) as ts:
            audit.record(
                ts,
                _audit_actor(actor),
                "company.state",
                "company",
                slug,
                before=before,
                after=after,
                reason=reason.strip(),
            )
            ts.commit()
    return StateChange(slug, state, to, now)


def resume(slug: str, *, actor: Actor, reason: str) -> StateChange:
    """A suspended company goes back to the state it was in."""
    with tenants.control_session() as s:
        row = s.execute(select(Tenant.state, Tenant.state_before_suspension).where(Tenant.slug == slug)).first()
    if row is None:
        raise StateError(f"unknown company '{slug}'", 404)
    if row[0] != "suspended" or not row[1]:
        raise StateError(f"{slug} is {row[0]}, not suspended: there is nothing to resume")
    return move(slug, str(row[1]), actor=actor, reason=reason)


def history(slug: str) -> list[dict[str, object]]:
    with tenants.control_session() as s:
        rows = s.execute(
            select(TenantStateChange)
            .join(Tenant, Tenant.id == TenantStateChange.tenant_id)
            .where(Tenant.slug == slug)
            .order_by(TenantStateChange.id)
        ).scalars()
        return [
            {
                "from": r.from_state, "to": r.to_state, "at": r.at.isoformat(), "by": f"{r.by_kind}:{r.by_id}",
                "approved_by": r.approved_by, "kind": r.kind, "reason": r.reason,
            }
            for r in rows
        ]  # fmt: skip


def _may_load(slug: str, user_id: int) -> bool:
    """A login that may sign in during cutover: it loads the open documents or administers the company."""
    with Session(routing.engine_for(slug)) as db:
        return bool(
            db.execute(
                text(
                    "select 1 from user_role ur join role_permission rp on rp.role = ur.role where ur.user_id = :u "
                    "and (rp.action = 'company.cutover' or rp.action like 'admin.%') limit 1"
                ),
                {"u": user_id},
            ).first()
        )


def allows_sign_in(slug: str, user_id: int, *, state: str | None = None) -> str | None:
    """None when the person may sign in (and keep working) now, else why not."""
    state = state or state_of(slug)
    if state == "suspended":
        return SUSPENDED
    if state == "cutover" and not _may_load(slug, user_id):
        return CUTOVER_SIGN_IN
    return None


# what each import may do in each state (D-02); the reason is added to the refusal
IMPORTS: dict[str, dict[str, str | None]] = {
    "masters": {"setup": None, "cutover": None, "live": None},
    "stock": {
        "setup": None,
        "cutover": None,
        "live": "opening stock and its history are locked after go-live; correct stock with a count",
    },
    "demand": {"setup": None, "cutover": None, "live": None},
    "cutover": {"cutover": None},
}


def require_state(db: Session, kind: str) -> None:
    """Refuse an import the company's state does not allow (HTTP 409, CLI exit 2)."""
    state = state_here(db)
    allowed = IMPORTS[kind]
    if state in allowed and allowed[state] is None:
        return
    if kind == "cutover":
        raise StateError(f"cutover import is only for a company in cutover; it is {state}")
    why = allowed.get(state)
    raise StateError(f"{kind} import is not allowed while the company is {state}" + (f": {why}" if why else ""))


def banner(state: str) -> str | None:
    return {
        "setup": "Setup: what you do here is test data. It is archived at cutover.",
        "cutover": "Cutover: loading open documents. Sign-in opens at go-live.",
    }.get(state)


def is_closed(slug: str) -> bool:
    """Whether a company takes no actions now (signed links refused)."""
    try:
        return state_of(slug) in CLOSED
    except StateError:
        return False
