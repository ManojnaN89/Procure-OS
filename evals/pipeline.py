"""The whole flow, from stock level to approved purchase order, run on a company built from the dataset.

Suppliers' replies are simulated (prices around our estimates, from a seeded random generator), approvers are the real
people of the organisation (each has a login), mail goes to a fake outbox. Every stage is timed.
"""

import random
import time
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import lib
from lib import World
from p2p.agents import autonomy
from p2p.approvals import workflow as request_flow
from p2p.orders import quotes
from p2p.orders import service as orders
from p2p.orders import workflow as po_flow
from p2p.platform import proposals, sod
from p2p.requests import replenishment
from p2p.sourcing import service as sourcing
from sqlalchemy import text


@dataclass
class State:
    world: World
    outbox: lib.Outbox
    seconds: dict[str, float] = field(default_factory=dict)
    replenishment: Any = None
    missing_logins: set[str] = field(default_factory=set)
    approvals_made: int = 0
    po_approvals_made: int = 0
    quotes_entered: int = 0
    notes: list[str] = field(default_factory=list)


def _stage(state: State, name: str, started: float) -> None:
    state.seconds[name] = round(time.monotonic() - started, 2)


def walk_request_approvals(state: State) -> None:
    """Each pending step is decided by the person it names (or their stand-in), who signs in with their own login."""
    db, w = state.world.db, state.world
    for _ in range(50):  # a chain is at most a few steps long
        pending = db.execute(
            text(
                "select s.request_no, s.approver, s.delegate from approval_step s join request r on r.no = s.request_no "
                "and r.approval_round = s.round where s.status = 'pending' and r.status = 'submitted' order by s.request_no"
            )
        ).all()
        if not pending:
            return
        progressed = False
        for no, approver, delegate in pending:
            who = delegate or approver
            user = w.org_logins.get(who) or w.org_logins.get(approver)
            if user is None:
                state.missing_logins.add(who)
                continue
            try:
                request_flow.approve(db, w.slug, no, user, "eval")
                state.approvals_made += 1
                progressed = True
            except Exception as exc:  # noqa: BLE001
                state.notes.append(f"approve {no} by {who}: {exc}")
        if not progressed:
            return


def walk_po_approvals(state: State) -> None:
    db, w = state.world.db, state.world
    for _ in range(50):
        pending = db.execute(
            text(
                "select s.po_no, s.approver, s.delegate from po_approval_step s join po p on p.no = s.po_no "
                "and p.approval_round = s.round where s.status = 'pending' and p.status = 'submitted' order by s.po_no"
            )
        ).all()
        if not pending:
            return
        progressed = False
        for no, approver, delegate in pending:
            who = delegate or approver
            user = w.org_logins.get(who) or w.org_logins.get(approver)
            if user is None:
                state.missing_logins.add(who)
                continue
            try:
                po_flow.approve(db, w.slug, no, user, "eval")
                state.po_approvals_made += 1
                progressed = True
            except Exception as exc:  # noqa: BLE001
                state.notes.append(f"approve PO {no} by {who}: {exc}")
        if not progressed:
            return


def simulate_supplier_quotes(state: State, rng: random.Random) -> None:
    """Every invited supplier except a random few replies with prices around our estimate."""
    db, w = state.world.db, state.world
    buyer = sod.user(w.logins["Buyer"])
    for (rfq_no,) in db.execute(text("select no from rfq where status = 'sent' order by no")).all():
        lines = db.execute(
            text(
                "select l.line_no, l.qty, l.item_code, rl.est_price, i.gst_rate_before, i.gst_rate_after, i.gst_rate_change_date, l.need_by "
                "from rfq_line l join request_line rl on rl.request_no = l.request_no and rl.line_no = l.request_line_no "
                "left join item i on i.code = l.item_code where l.rfq_no = :n order by l.line_no"
            ),
            {"n": rfq_no},
        ).all()
        suppliers = [
            r[0]
            for r in db.execute(
                text("select supplier_code from rfq_supplier where rfq_no = :n order by 1"),
                {"n": rfq_no},
            )
        ]
        silent = set(rng.sample(suppliers, k=1)) if len(suppliers) >= 3 and rng.random() < 0.4 else set()
        from p2p.formulas.orders import current_gst_rate

        for s in suppliers:
            if s in silent:
                db.execute(
                    text(
                        "update rfq_supplier set declined = true, decline_reason = 'no capacity' where rfq_no = :n and supplier_code = :s"
                    ),
                    {"n": rfq_no, "s": s},
                )
                continue
            qlines = []
            for line_no, qty, item, est, before, after, changed, need_by in lines:
                price = (Decimal(est or 100) * Decimal(str(round(rng.uniform(0.85, 1.12), 3)))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                rate = current_gst_rate(before, after, changed, lib.TODAY) or Decimal(18)
                days = rng.randint(2, 9)
                qlines.append(
                    quotes.QuoteLineInput(
                        rfq_line_no=line_no,
                        unit_price=price,
                        gst_rate=rate,
                        discount_pct=Decimal(rng.choice([0, 0, 0, 2, 5])),
                        delivery_days=days,
                    )
                )
            quotes.enter_quote(
                db,
                rfq_no,
                quotes.QuoteInput(
                    supplier_code=s,
                    valid_until=lib.TODAY + timedelta(days=30),
                    freight=Decimal(rng.choice([0, 0, 250, 500])),
                    lines=qlines,
                ),
                buyer,
                now=None,
            )
            state.quotes_entered += 1
        db.commit()
        if quotes.auto_award(db, w.slug, rfq_no, as_of=lib.TODAY) is None:
            comp = quotes.comparison(db, rfq_no, lib.TODAY)
            choices = {ln.rfq_line_no: ln.recommended for ln in comp.lines if ln.recommended}
            if choices:
                quotes.award(
                    db,
                    rfq_no,
                    choices,
                    buyer,
                    reason="eval: awarded the recommendation",
                    as_of=lib.TODAY,
                    tenant=w.slug,
                )


def accept_variances(state: State) -> int:
    """A person accepts each price held above the estimate (here: the procurement head's login)."""
    db, w = state.world.db, state.world
    accepted = 0
    user = w.org_logins.get("U006") or next(iter(w.org_logins.values()))
    for (pid,) in db.execute(
        text("select hold_proposal_id from po where hold_reason is not null and status = 'draft' order by no")
    ).all():
        try:
            proposals.approve(db, int(pid), user)
            accepted += 1
        except Exception as exc:  # noqa: BLE001
            state.notes.append(f"variance proposal {pid}: {exc}")
    return accepted


def run_pipeline(world: World, *, seed: int = 7) -> State:
    """From stock levels to approved purchase orders. Returns the timings and what was done."""
    db = world.db
    state = State(world, lib.capture_mail())
    rng = random.Random(seed)
    autonomy.configure(db, sod.user(1), "rfq.send", level="auto")
    autonomy.configure(db, sod.user(1), "rfq.award", level="auto")

    t = time.monotonic()
    state.replenishment = replenishment.run(
        db,
        as_of=lib.TODAY,
        tenant=world.slug,
        submit=True,
        actor=sod.user(world.logins["Buyer"]),
    )
    _stage(state, "replenishment and submission", t)

    t = time.monotonic()
    walk_request_approvals(state)
    _stage(state, "request approvals", t)

    t = time.monotonic()
    sourcing.sweep(db, world.slug, as_of=lib.TODAY)
    _stage(state, "sourcing and RFQ emails", t)

    t = time.monotonic()
    simulate_supplier_quotes(state, rng)
    _stage(state, "quotes and awards", t)

    t = time.monotonic()
    orders.sweep(db, world.slug, as_of=lib.TODAY)
    accept_variances(state)
    walk_po_approvals(state)
    _stage(state, "purchase orders and their approval", t)
    return state


def counts(db: Any) -> dict[str, int]:
    names = [
        "request",
        "request_line",
        "approval_step",
        "sourcing_decision",
        "rfq",
        "rfq_line",
        "rfq_supplier",
        "quote",
        "award",
        "po",
        "po_line",
        "po_charge",
        "email_message",
        "proposal",
    ]
    return {n: int(db.execute(text(f"select count(*) from {n}")).scalar_one()) for n in names}


def get_state(ctx: Any) -> State:
    """One full pipeline run, shared by the evals that need a company that has been through the whole flow."""
    if "pipeline" not in ctx.cache:
        ctx.cache["pipeline"] = run_pipeline(lib.build_world("pipe"))
    return ctx.cache["pipeline"]  # type: ignore[no-any-return]
