"""The daily jobs of the store: the store check at 05:30 India time and replenishment at 06:00 (so restock sees the stock
set aside). Each runs for a company when its setting is on (`auto_store_check`, `auto_replenishment`) and always queues
the next morning's run, so switching a setting on later takes effect without anyone starting anything again."""

from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import IST, business_today
from p2p.platform import audit, routing, settings, tenants, worker
from p2p.requests import material, replenishment

JOB = "replenishment"
RUN_AT = time(6, 0)  # India time
CHECK_JOB = "store_check"
CHECK_AT = time(5, 30)  # India time, before replenishment


def next_run(now: datetime, at: time = RUN_AT) -> datetime:
    """`at` India time on the next calendar day (the run itself skips nothing: stock does not take weekends off)."""
    tomorrow: date = now.astimezone(IST).date() + timedelta(days=1)
    return datetime.combine(tomorrow, at, tzinfo=IST).astimezone(UTC)


def schedule(tenant: str, run_at: datetime | None = None) -> int | None:
    """Queue the daily replenishment for a company unless one is already waiting."""
    return worker.enqueue_unique(tenant, JOB, {}, run_at or datetime.now(UTC))


def schedule_check(tenant: str, run_at: datetime | None = None) -> int | None:
    """Queue the daily store check for a company unless one is already waiting."""
    return worker.enqueue_unique(tenant, CHECK_JOB, {}, run_at or datetime.now(UTC))


def queue_daily_check(db: Session, tenant: str, now: datetime | None = None) -> int | None:
    """A new company: when `auto_store_check` is on (the default), its first store check is queued for 05:30 on the
    next morning, so the setting means what it says without anyone scheduling it."""
    if settings.value(db, "auto_store_check", "true") != "true":
        return None
    return schedule_check(tenant, next_run(now or datetime.now(UTC), CHECK_AT))


@worker.register(JOB)
def _run(ctx: worker.JobContext) -> None:
    with Session(routing.engine_for(ctx.tenant)) as db:
        on = db.execute(text("select value from app_setting where key = 'auto_replenishment'")).scalar()
        if on == "true":
            replenishment.run(db, as_of=ctx.now.astimezone(IST).date(), tenant=ctx.tenant, submit=True)
    worker.enqueue_unique(ctx.tenant, JOB, {}, next_run(ctx.now), exclude_id=ctx.id)


def run_check(db: Session, tenant: str) -> material.CheckResult | None:
    """The store check of the whole company by the system, when `auto_store_check` is on: stock set aside, shortfall
    requests made (sent into approval only when `store_check_submit` is on), late-supply cases worked out. Audited once
    per run with its counts. Returns None when the setting is off."""
    if settings.value(db, "auto_store_check", "true") != "true":
        return None
    today = business_today()
    result = material.store_check(
        db, audit.SYSTEM, tenant=tenant, today=today, submit=material.submit_setting(db), automatic=True
    )
    audit.record(
        db, audit.SYSTEM, "store_check.run", "store_check", today.isoformat(),
        after={
            "material_requests": len(result.requests), "purchase_requests": [r.no for r in result.purchase_requests],
            "reserved": sum(1 for c in result.requests for ln in c.lines if ln.reserved_now),
            "at_risk": sum(1 for c in result.requests for ln in c.lines if ln.at_risk),
            "cases": [c["task_id"] for c in result.cases],
        },
    )  # fmt: skip
    db.commit()
    return result


@worker.register(CHECK_JOB)
def _check(ctx: worker.JobContext) -> None:
    with Session(routing.engine_for(ctx.tenant)) as db:
        run_check(db, ctx.tenant)
    # the next run is the morning after this one: one queued earlier for another morning is replaced, not kept beside it
    with tenants.control_engine().begin() as c:
        c.execute(
            text(
                "update job set status = 'done', finished_at = now(), last_error = 'replaced by the next run' "
                "where tenant_slug = :t and kind = :k and status = 'queued' and id <> :i"
            ),
            {"t": ctx.tenant, "k": CHECK_JOB, "i": ctx.id},
        )
    worker.enqueue_unique(ctx.tenant, CHECK_JOB, {}, next_run(ctx.now, CHECK_AT), exclude_id=ctx.id)


# ---- forward buying (task 13.5) -----------------------------------------------------------------------------------

FORWARD_ITEM_JOB = "forward_buy_item"
FORWARD_DAILY_JOB = "forward_buy_daily"
FORWARD_EXPLAIN_JOB = "forward_buy_explain"


@worker.register(FORWARD_ITEM_JOB)
def _forward_item(ctx: worker.JobContext) -> None:
    """A request with the item was submitted, or a benchmark for it was verified: work out whether to buy ahead."""
    from p2p.requests import forward_buy  # noqa: PLC0415 - forward buying reads budgets and sourcing

    with Session(routing.engine_for(ctx.tenant)) as db:
        try:
            forward_buy.evaluate_item(
                db, ctx.tenant, str(ctx.payload["item"]), str(ctx.payload["location"]), business_today()
            )
        except forward_buy.ForwardBuyError:
            db.rollback()  # the item or place is gone, or no rule is in force: nothing to recommend
    worker.enqueue_unique(ctx.tenant, FORWARD_DAILY_JOB, {}, next_run(ctx.now))


@worker.register(FORWARD_DAILY_JOB)
def _forward_daily(ctx: worker.JobContext) -> None:
    """Every live recommendation is worked out again once a day: it stays, is replaced, or lapses."""
    from p2p.requests import forward_buy  # noqa: PLC0415

    with Session(routing.engine_for(ctx.tenant)) as db:
        live = db.execute(
            text("select item_code, location_code from forward_buy where status in ('open', 'accepted')")
        ).all()
        for item, place in live:
            try:
                forward_buy.evaluate_item(db, ctx.tenant, str(item), str(place), business_today())
            except forward_buy.ForwardBuyError:
                db.rollback()
    if live:
        worker.enqueue_unique(ctx.tenant, FORWARD_DAILY_JOB, {}, next_run(ctx.now), exclude_id=ctx.id)


@worker.register(FORWARD_EXPLAIN_JOB)
def _forward_explain(ctx: worker.JobContext) -> None:
    """The AI step rewords a new recommendation's explanation; the template stays on any failure."""
    from p2p.ai_steps import forward_buy_note  # noqa: PLC0415
    from p2p.ai_steps.gateway import Gateway  # noqa: PLC0415

    with Session(routing.engine_for(ctx.tenant)) as db:
        forward_buy_note.explain(db, int(ctx.payload["id"]), Gateway())
