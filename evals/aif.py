"""Shared pieces of the AI foundation evals (E95-E104, WP-11a), written from the spec only, never from the implementation.

What is here:
- `Smtp`: stands in for the mail server (as `lib.capture_mail` does): it records every message by its Message-ID, and can
  be made slow, down, or refuse one address. The outbox's one sender calls `mail.send_email(..., message_id=m)` (spec
  2.2, `deliver` step 3), so counting by Message-ID is "one Mailpit message with the row's message_id".
- `Model`: a scripted provider behind the real `Gateway`. `no_live_models` makes every `Gateway()` a job handler builds
  use it, so no eval here can ever call a live model.
- Readers of the control database (jobs, their `dedupe_key`) and a runner that runs only the job kinds an eval names,
  at a chosen time (the worker's own claim, written again here so other due work does not run).
- The new interfaces the spec names, called by their names: the capability switch API, hand-over tasks, a waiting agent
  run on the demo registry, a recommendation.

Where the spec leaves a shape open (a list wrapped in an object or not, an id as text or a number) the readers accept
every reasonable shape and say so in a comment.
"""

import contextlib
import dataclasses
import hashlib
import json
import smtplib
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Any

from block1 import js
from sqlalchemy import text

CAPABILITIES = ("intake", "planning", "sourcing", "execution", "invoice_ap", "monitoring", "assistant")
# spec 2.6 table: the worker jobs that do each capability's work, and who gets its handed-over work
JOB_KINDS = {
    "intake": ("intake_email",),
    "planning": ("planning_explain",),
    "sourcing": ("read_supplier_reply",),
    "execution": ("supplier_message",),
    "invoice_ap": ("read_invoice_email",),
}
OWNERS = {  # role or designation words, matched without case (spec 2.6: "Purchase Manager", "Storekeeper", "Buyer", "AP")
    "intake": ("purchase manager",),
    "planning": ("storekeeper", "store", "buyer"),
    "sourcing": ("buyer",),
    "execution": ("buyer",),
    "invoice_ap": ("ap", "accounts payable"),
}
# the D54 checks given at switch-on (spec 2.6), all filled in and passing
CHECKS = {
    "permission_tests": "tests/test_capabilities.py",
    "eval": "E101",
    "eval_threshold": "0.9",
    "eval_result": "1.0",
    "tracing": True,
    "switch_off_tested": True,
}
PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"


# ---- the mail server -------------------------------------------------------------------------------------------------


class Smtp:
    """Records what would have left; `down` refuses every message, `bounce` refuses addresses containing a word, `slow`
    holds each message so two senders overlap."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.attempts = 0
        self.down = False
        self.slow = 0.0
        self.bounce: set[str] = set()
        self.lock = threading.Lock()

    def send_email(self, to: str, subject: str, body: str, **kw: Any) -> str:
        with self.lock:
            self.attempts += 1
        if self.slow:
            time.sleep(self.slow)
        if self.down:
            raise ConnectionRefusedError("eval: the mail server is down")
        if any(word in str(to).lower() for word in self.bounce):
            raise smtplib.SMTPRecipientsRefused({str(to): (550, b"eval: no such mailbox")})
        mid = str(kw.get("message_id") or f"<eval-{uuid.uuid4().hex}@eval.example>")
        with self.lock:
            self.sent.append({"to": to, "subject": subject, "body": body, **kw, "message_id": mid})
        return mid

    def count(self, message_id: str) -> int:
        with self.lock:
            return sum(1 for m in self.sent if m["message_id"] == message_id)

    def to(self, address: str) -> list[dict[str, Any]]:
        with self.lock:
            return [m for m in self.sent if str(m["to"]).lower() == address.lower()]

    def everything(self, m: dict[str, Any]) -> str:
        return f"{m.get('subject', '')}\n{m.get('body', '')}\n{m.get('html') or ''}"


def install_smtp() -> Smtp:
    from p2p.platform import mail

    box = Smtp()
    mail.send_email = box.send_email  # type: ignore[assignment]
    return box


# ---- a scripted model -------------------------------------------------------------------------------------------------


class Model:
    """A provider that answers from a queue (then `default`); each answer is text, a dict (sent as JSON) or a callable of
    the messages. Tokens per call are fixed so costs can be worked out."""

    def __init__(self, default: Any = None, tokens: tuple[int, int] = (100, 50)) -> None:
        self.queue: list[Any] = []
        self.default: Any = default if default is not None else {"final": "Done."}
        self.calls = 0
        self.tokens = tokens
        self.seen: list[Any] = []
        self.lock = threading.Lock()

    def chat(
        self, model: str, messages: Any, *, temperature: float, max_tokens: int, json_output: bool, **kw: Any
    ) -> Any:
        from p2p.ai_steps.adapters import RawResponse

        with self.lock:
            self.calls += 1
            self.seen.append(messages)
            answer = self.queue.pop(0) if self.queue else self.default
        if callable(answer):
            answer = answer(messages)
        body = answer if isinstance(answer, str) else json.dumps(answer)
        return RawResponse(body, self.tokens[0], self.tokens[1], "stop")


def gateway(model: Model, provider: str = "fake", name: str = "m") -> Any:
    from p2p.ai_steps.gateway import Gateway

    return Gateway({provider: model}, {"default": [(provider, name)]}, sleep=lambda s: None)


@contextlib.contextmanager
def no_live_models(model: Model) -> Iterator[Model]:
    """Every gateway made in this process (a job handler's `Gateway()`, the API's) answers from `model`."""
    from p2p.ai_steps import gateway as gw
    from p2p.platform import ui_api

    saved = gw.build_adapters, gw.load_routes
    gw.build_adapters = lambda: {"fake": model}  # type: ignore[assignment]
    gw.load_routes = lambda: {"default": [("fake", "m")]}  # type: ignore[assignment]
    ui_api.use_gateway(gateway(model))
    try:
        yield model
    finally:
        gw.build_adapters, gw.load_routes = saved
        ui_api.use_gateway(None)


# ---- the control database: jobs ---------------------------------------------------------------------------------------


def ctl(sql: str, **p: Any) -> list[Any]:
    from p2p.platform import tenants

    with tenants.control_engine().connect() as c:
        return list(c.execute(text(sql), p).all())


def jobs(slug: str, kinds: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """The company's jobs (every column, `dedupe_key` when the column exists), oldest first."""
    from p2p.platform import tenants

    sql = (
        "select * from job where tenant_slug = :t"
        + (" and kind = any(:k)" if kinds is not None else "")
        + " order by id"
    )
    with tenants.control_engine().connect() as c:
        return [dict(r) for r in c.execute(text(sql), {"t": slug, "k": list(kinds or [])}).mappings()]


def payload_of(job: dict[str, Any]) -> dict[str, Any]:
    p = job.get("payload")
    if isinstance(p, str):
        try:
            p = json.loads(p)
        except ValueError:
            return {}
    return p if isinstance(p, dict) else {}


def context(job_id: int, tenant: str, payload: dict[str, Any], now: datetime, created_at: datetime | None) -> Any:
    """A JobContext; `created_at` is passed when the context has it (spec 2.2: it gains `created_at`)."""
    from p2p.platform import worker

    names = {f.name for f in dataclasses.fields(worker.JobContext)}
    kw: dict[str, Any] = {"id": job_id, "tenant": tenant, "payload": payload, "now": now}
    if "created_at" in names:
        kw["created_at"] = created_at or now
    return worker.JobContext(**kw)


def run_jobs(slug: str, kinds: Iterable[str], *, now: datetime | None = None, rounds: int = 60) -> list[str | None]:
    """Run only the queued jobs of these kinds that are due at `now`, the way the worker claims and finishes them.
    Returns each run's error (None when it finished). A job retried by the worker is due later, so it waits."""
    from p2p.platform import tenants, worker

    worker.load_handlers()
    now = now or datetime.now(UTC)
    kinds = list(kinds)
    errors: list[str | None] = []
    for _ in range(rounds):
        with tenants.control_engine().begin() as c:
            row = c.execute(
                text(
                    "select id, kind, payload, created_at from job where tenant_slug = :t and kind = any(:k) and "
                    "status = 'queued' and run_at <= :n order by run_at, id limit 1 for update skip locked"
                ),
                {"t": slug, "k": kinds, "n": now},
            ).first()
            if row is None:
                break
            c.execute(
                text("update job set status = 'running', locked_at = :n, attempts = attempts + 1 where id = :i"),
                {"n": now, "i": row[0]},
            )
        handler = worker.HANDLERS.get(str(row[1]))
        error: str | None = None
        try:
            if handler is None:
                raise RuntimeError(f"no handler registered for job kind {row[1]}")
            handler(context(int(row[0]), slug, payload_of({"payload": row[2]}), now, row[3]))
        except Exception as exc:  # noqa: BLE001 - recorded on the job as the worker does
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
        worker._finish(int(row[0]), now, error)
        errors.append(error)
    return errors


def drain(slug: str, *, now: datetime | None = None, rounds: int = 80) -> int:
    """The worker for this company until nothing is due."""
    from p2p.platform import worker

    n = 0
    for _ in range(rounds):
        took = worker.run_once(now=now, tenant=slug)
        n += took
        if not took:
            break
    return n


# ---- the company database ----------------------------------------------------------------------------------------------


def has(co: Any, table: str) -> bool:
    return table in co.tables()


def count(co: Any, sql: str, **p: Any) -> int:
    return int(co.one(sql, **p) or 0)


def rows(co: Any, sql: str, **p: Any) -> list[dict[str, Any]]:
    co.db.rollback()
    found = [dict(r) for r in co.db.execute(text(sql), p).mappings().all()]
    co.db.rollback()
    return found


def new_session(co: Any) -> Any:
    """A session of its own on the company (as the app login), for a thread or a long-lived run."""
    from sqlalchemy.orm import Session

    from p2p.platform import routing

    return Session(routing.engine_for(co.slug))


def set_org(co: Any, key: str, value: str) -> None:
    """An org setting, written by the owner (eval set-up, not a step under test)."""
    import lib

    with lib.owner_session(co.slug) as s:
        s.execute(
            text("insert into org_setting (key, value) values (:k, :v) on conflict (key) do update set value = :v"),
            {"k": key, "v": value},
        )
        s.commit()


def login_active(co: Any, user_id: Any) -> bool:
    found = ctl(
        "select u.active from app_user u join tenant t on t.id = u.tenant_id where t.slug = :s and u.id = :i",
        s=co.slug,
        i=int(user_id or 0),
    )
    return bool(found and found[0][0])


def holds(co: Any, user_id: Any, words: Iterable[str]) -> bool:
    """The login's person holds one of these roles, or has one of these words in their designation."""
    code = co.code_of(user_id)
    if code is None:
        return False
    designation = str(co.people.get(code, {}).get("designation") or "").lower()
    roles = {r.lower() for r in co.roles.get(code, set())}
    return any(w in roles or w in designation for w in words)


def tasks(co: Any, kind: str = "ai_handover", status: str | None = "open") -> list[dict[str, Any]]:
    where = " and status = :st" if status else ""
    found = rows(co, f"select * from task where kind = :k{where} order by id", k=kind, st=status)
    for t in found:
        if isinstance(t.get("payload"), str):
            t["payload"] = json.loads(t["payload"])
        t["payload"] = t.get("payload") or {}
    return found


def audit_after(co: Any, action: str, after_id: int = 0) -> list[dict[str, Any]]:
    return rows(co, "select * from audit_event where action = :a and id > :i order by id", a=action, i=after_id)


# ---- mail coming in -----------------------------------------------------------------------------------------------------


def raw_mail(
    to: str,
    subject: str,
    body: str,
    *,
    sender: str = "sales@supplier-eval.example",
    message_id: str | None = None,
    attachments: Iterable[tuple[str, str, bytes]] = (),
) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = format_datetime(datetime.now(UTC))
    msg["Message-ID"] = message_id or f"<eval-{uuid.uuid4().hex}@supplier-eval.example>"
    msg.set_content(body)
    for name, ctype, data in attachments:
        main, _, sub = ctype.partition("/")
        msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return msg.as_bytes()


class Box:
    """A mailbox that hands out the same messages every time it is read (a poll repeated after a crash)."""

    def __init__(self, raws: list[bytes]) -> None:
        self.raws = raws

    def fetch_new(self) -> list[Any]:
        from p2p.platform import mailbox

        return [mailbox.parse_rfc822(r) for r in self.raws]


def ingest(co: Any, raw: bytes) -> int | None:
    from p2p.platform import emails, mailbox

    db = new_session(co)
    try:
        got = emails.ingest(db, co.slug, mailbox.parse_rfc822(raw))
        db.commit()
        return got
    finally:
        db.close()


# ---- the capability switch (spec 2.6) -------------------------------------------------------------------------------------


def admins(co: Any) -> tuple[str, str]:
    found = co.holders("admin")
    return found[0], found[1]


def listing(co: Any, by: str | None = None) -> tuple[int, dict[str, dict[str, Any]], dict[str, Any]]:
    """GET /api/setup/capabilities: (status, rows by code, whole body)."""
    r = co.call(by or admins(co)[0], "GET", "/api/setup/capabilities")
    body = js(r) if r.status_code == 200 else {}
    found = body.get("capabilities") if isinstance(body, dict) else body  # an object with a list, or the list itself
    return r.status_code, {str(c.get("code")): c for c in found or [] if isinstance(c, dict)}, body or {}


def enabled(co: Any) -> dict[str, bool]:
    return {str(r["code"]): bool(r["enabled"]) for r in rows(co, "select code, enabled from ai_capability")}


def switch_off(co: Any, code: str, reason: str = "eval: switched off", by: str | None = None) -> Any:
    body: dict[str, Any] = {"on": False}
    if reason:
        body["reason"] = reason
    return co.call(by or admins(co)[0], "PUT", f"/api/setup/capabilities/{code}", body)


def ask_on(
    co: Any, code: str, checks: dict[str, Any] | None = None, by: str | None = None, reason: str = "eval"
) -> Any:
    body = {"on": True, "reason": reason, "checks": dict(CHECKS if checks is None else checks)}
    return co.call(by or admins(co)[0], "PUT", f"/api/setup/capabilities/{code}", body)


def switch_on(co: Any, code: str) -> tuple[Any, Any]:
    """The first administrator asks with the checks, the second approves."""
    maker, checker = admins(co)
    asked = ask_on(co, code, by=maker)
    pid = js(asked).get("proposal_id")
    if asked.status_code != 202 or pid is None:
        return asked, None
    return asked, co.call(checker, "POST", f"/api/admin/proposals/{pid}/approve", {})


# ---- agents: a waiting run on the demo registry, a recommendation ---------------------------------------------------------


@dataclasses.dataclass
class Waiting:
    runtime: Any
    db: Any
    outcome: Any
    thread: str
    decisions: list[int]  # how many times the scripted decider was asked


def waiting_run(co: Any, capability: str, user_id: int | None, *, agent: str = "eval_demo") -> Waiting:
    """An agent run on `agents/demo.py`'s registry: it looks an invoice up (so the run has evidence), then asks for the
    write tool `release_payment`, which waits for a person as a proposal (spec 2.4 runtime hook)."""
    from p2p.agents import demo
    from p2p.agents.runtime import AgentRuntime, Decision

    asked = [0]

    def decide(messages: list[dict[str, Any]], specs: list[dict[str, Any]]) -> Any:
        asked[0] += 1
        names = [str(m.get("name")) for m in messages if m.get("role") == "tool"]
        if "human_decision" in names:
            return Decision(final="The payment for INV-9 is done.")
        if "lookup_invoice" not in names:
            return Decision(tool="lookup_invoice", args={"invoice": "INV-9"}, confidence=0.95)
        return Decision(tool="release_payment", args={"invoice": "INV-9", "amount": "125000.00"}, confidence=0.95)

    db = new_session(co)
    started_by = f"user:{user_id}" if user_id else "service:eval"
    runtime = AgentRuntime(
        co.slug, db, demo.REGISTRY, decide, agent=agent, capability=capability, user_id=user_id, started_by=started_by
    )  # type: ignore[call-arg]  # the keywords spec 2.5 adds
    thread = f"eval-{uuid.uuid4().hex}"
    outcome = runtime.start("Release the payment for INV-9 after checking it.", thread_id=thread)
    return Waiting(runtime, db, outcome, thread, asked)


def recommend(co: Any, capability: str, key: str, *, directed_by: int | None = None, kind: str = "eval.check") -> int:
    """A recommendation recorded through the service (spec 2.4 `record`). The `actor` is an Actor (the spec names the
    keyword, not its type; `created_by` is 'agent:...')."""
    from p2p.agents import recommendations
    from p2p.platform.sod import Actor

    db = new_session(co)
    try:
        rid = recommendations.record(
            db,
            capability=capability,
            kind=kind,
            business_key=key,
            summary="eval: order 5 more, the stock is below the reorder point",
            evidence=[{"source": "tool", "ref": "stock_position", "value": "5"}],
            engine_figures={"qty": "5", "item": "RM-01"},
            rule_versions={"eval_rule": "1"},
            actor=Actor("agent", "eval"),
            directed_by=directed_by,
        )
        db.commit()
        return int(rid)
    finally:
        db.close()


def figures_hashes(figures: Any) -> set[str]:
    """sha256 of the figures as sorted JSON (spec 2.4), in the separators a writer might pick."""
    out = set()
    for seps in ((",", ":"), (", ", ": ")):
        blob = json.dumps(figures, sort_keys=True, separators=seps, default=str)
        out.add(hashlib.sha256(blob.encode()).hexdigest())
    return out


def proposal(co: Any, pid: int) -> dict[str, Any]:
    found = rows(co, "select * from proposal where id = :i", i=pid)
    p = found[0] if found else {}
    if isinstance(p.get("payload"), str):
        p["payload"] = json.loads(p["payload"])
    return p


def _payment_limit(co: Any) -> None:
    """Set-up: the demo tool's proposal is a payment_run with a value, and no demo person has a limit for that document
    (a limit has to be given on purpose: `permissions.within_limit`), so nobody could approve it at all. The eval gives
    the finance holders one, as an administrator would; what is checked (who may confirm, what is recorded) is unchanged."""
    from decimal import Decimal

    from p2p.platform import users

    for code in co.holders("finance"):
        uid = co.user_id(code)
        if not count(co, "select count(*) from user_limit where user_id = :u and document = 'payment_run'", u=uid):
            users.set_limit(co.slug, uid, "payment_run", Decimal("99999999.00"))


def approve_as_anyone(co: Any, pid: int, skip: Iterable[str] = ()) -> tuple[str | None, Any]:
    """Approve a proposal as the first person allowed to (finance, admins, approvers, then the rest); a 403 tries the
    next person. Returns (who answered with something other than 403, that response)."""
    skip = set(skip)
    _payment_limit(co)
    order = [*co.holders("finance"), *co.holders("admin"), *co.holders("approver"), *sorted(co.people)]
    seen: set[str] = set()
    last = None
    for code in order:
        if code in skip or code in seen:
            continue
        seen.add(code)
        r = co.call(code, "POST", f"/api/admin/proposals/{pid}/approve", {})
        last = r
        if r.status_code != 403:
            return code, r
    return None, last


# ---- counting ------------------------------------------------------------------------------------------------------------


def counts(co: Any, tables: dict[str, str]) -> dict[str, int]:
    """name -> count(*) of the SQL's from-clause, for the tables that exist."""
    have = co.tables()
    out = {}
    for name, sql in tables.items():
        if sql.split(" where ")[0].split()[-1] in have:
            out[name] = count(co, f"select count(*) from {sql}")
    return out


def later(minutes: float) -> datetime:
    return datetime.now(UTC) + timedelta(minutes=minutes)


def check_status(check: Callable[..., bool], r: Any, want: int | tuple[int, ...], what: str, sev: str = "high") -> bool:
    wanted = (want,) if isinstance(want, int) else want
    return check(r is not None and r.status_code in wanted, f"{what}: answered "
                 f"{getattr(r, 'status_code', None)} {getattr(r, 'text', '')[:200]}, expected {wanted}", sev)  # fmt: skip


def to_orders(co: Any) -> Any:
    """A0 (Run store check), every request it made approved through its chain, then sourced and ordered (block 1)."""
    r = co.base_state()
    for no in sorted(co.first_requests):
        co.approve(no)
    co.source()
    return r


def at_risk(co: Any, check: Callable[..., bool]) -> None:
    """Set-up (after the base state): MO-1's castings ordered and promised late, then a store check, so a material
    request has a line at risk. Planning explains only requests with short or at-risk lines (block 2, kept by spec 2.2:
    the figures event replaces its queueing), and the Apex base state has none, so without this no event is due at all.
    The same steps as E73's T1; what the evals check is unchanged."""
    from datetime import date

    import block1 as b

    ack = date(2026, 11, 23)
    for no in co.request_of("MO-1"):
        co.approve(no)
    co.source()
    co.with_buyer()
    found = co.po_of("MO-1", "RM-01")
    if not check(found is not None, "set-up: no castings order for MO-1 after sourcing", "medium"):
        return
    po = found[0]
    co.advance_po(po)
    co.acknowledge(po, date(2026, 12, 8), ack)
    with b.on_day(ack):
        co.store_check()


def planning_on(co: Any, check: Callable[..., bool]) -> None:
    """The planning agent is off by default (owner decision 8); two administrators switch `planning.explain` on."""
    asked, approved = co.automation("planning.explain", "auto")
    check(asked.status_code == 202 and approved is not None and approved.status_code == 200, "switching "
          f"planning.explain on answered {asked.status_code} / {getattr(approved, 'status_code', None)}")  # fmt: skip


# ---- revalidation: the eval's own basis for the demo tool (spec 2.3 `@workflows.basis(kind)`) ------------------------


class MovesBasis:
    """The figures behind the demo tool's recommendation, as this eval defines them: the number of stock movements (a
    stock count through the API adds one). While the number is what it was when the recommendation was recorded, the
    basis answers with the recommendation's own `basis_hash` (so the eval does not depend on how the platform hashes);
    once it moved, with another hash. The spec registers a basis per kind; the runtime's kind is `tool.<name>`."""

    def __init__(self, co: Any) -> None:
        self.co = co
        self.expected: str | None = None
        self.moves: int | None = None
        self.asked = 0

    def __call__(self, db: Any, business_key: str) -> Any:
        from p2p.platform.workflows import Basis

        self.asked += 1
        moves = int(db.execute(text("select count(*) from stock_movement")).scalar_one())
        same = self.expected is not None and moves == self.moves
        digest = self.expected if same else f"eval-moved-{moves}"
        return Basis(hash=digest, figures={"stock_movements": moves}, problems=[])

    def pin(self, basis_hash: str) -> None:
        self.expected = basis_hash
        self.moves = count(self.co, "select count(*) from stock_movement")


def register_basis(kind: str, fn: Callable[..., Any]) -> None:
    from p2p.platform import workflows

    reg = getattr(workflows, "basis", None)
    if callable(reg):
        reg(kind)(fn)
    else:
        workflows.BASIS[kind] = fn


def rec_of_proposal(co: Any, pid: int | None) -> dict[str, Any]:
    """The recommendation behind a proposal: by `recommendation.proposal_id`, else the payload's `_recommendation_id`."""
    if pid is None:
        return {}
    found = rows(co, "select * from recommendation where proposal_id = :p order by id desc", p=pid)
    if not found:
        rid = (proposal(co, pid).get("payload") or {}).get("_recommendation_id")
        found = rows(co, "select * from recommendation where id = :i", i=int(rid or 0))
    return found[0] if found else {}


def recommended_run(co: Any, capability: str, maker: str) -> tuple[Waiting, int | None, dict[str, Any]]:
    w = waiting_run(co, capability, co.user_id(maker))
    pid = getattr(w.outcome, "proposal_id", None)
    return w, pid, rec_of_proposal(co, pid)


def count_stock(co: Any, n: int, item: str = "RM-01", loc: str = "PLT-CKN") -> Any:
    return co.call(co.role("store"), "POST", "/api/inventory/count",
                   {"item_code": item, "location": loc, "counted_qty": str(n), "reason": "eval: new figures"})  # fmt: skip
