"""E07: running any stage again, or several at once, changes nothing and makes no duplicates."""

import time
from concurrent.futures import ThreadPoolExecutor

import lib
import pipeline
from lib import Outcome
from sqlalchemy import text
from sqlalchemy.orm import Session

ID = "E07"
TITLE = "Repeating or racing any stage makes no duplicates"
REQUIREMENT = "Plan: no duplicate requests, RFQs, emails or orders; document numbers have no gaps and no repeats"


def run(ctx) -> Outcome:
    from p2p.orders import service as orders
    from p2p.platform import routing, sod
    from p2p.requests import replenishment
    from p2p.sourcing import service as sourcing

    out = Outcome(True)
    state = pipeline.get_state(ctx)
    world = state.world
    db, slug = world.db, world.slug
    before = pipeline.counts(db)
    mails_before = len(state.outbox.sent)

    # every stage again, three times in a row
    for _ in range(3):
        replenishment.run(
            db,
            as_of=lib.TODAY,
            tenant=slug,
            submit=True,
            actor=sod.user(world.logins["Buyer"]),
        )
        pipeline.walk_request_approvals(state)
        sourcing.sweep(db, slug, as_of=lib.TODAY)
        orders.sweep(db, slug, as_of=lib.TODAY)
        pipeline.accept_variances(state)
        pipeline.walk_po_approvals(state)
    after_serial = pipeline.counts(db)

    # and all of them at once from several connections
    def hammer(i: int) -> str:
        with Session(routing.engine_for(slug)) as mine:
            replenishment.run(
                mine,
                as_of=lib.TODAY,
                tenant=slug,
                submit=True,
                actor=sod.user(world.logins["Buyer"]),
            )
            sourcing.sweep(mine, slug, as_of=lib.TODAY)
            orders.sweep(mine, slug, as_of=lib.TODAY)
        return "done"

    started = time.monotonic()
    with ThreadPoolExecutor(6) as pool:
        results = list(pool.map(hammer, range(6)))
    raced = round(time.monotonic() - started, 1)
    after_race = pipeline.counts(db)

    grew = {k: (before[k], after_race[k]) for k in before if after_race[k] != before[k]}
    if grew:
        out.find(
            "high",
            f"repeating or racing the stages changed the data (before, after): {grew}",
        )
    if len(state.outbox.sent) != mails_before:
        out.find(
            "high",
            f"{len(state.outbox.sent) - mails_before} extra RFQ email(s) went out when the stages were repeated",
        )

    # document numbers: one per document, none repeated, none skipped
    gaps: list[str] = []
    for table, doc in (("request", "request"), ("po", "po")):
        by_series: dict[tuple[str, str], list[int]] = {}
        for (no,) in db.execute(text(f"select no from {table}")):
            prefix, loc, fy, seq = (
                no.rsplit("-", 3)[0],
                no.split("-")[1],
                no.split("-")[2],
                int(no.split("-")[3]),
            )
            by_series.setdefault((loc, fy), []).append(seq)
        for key, seqs in by_series.items():
            if sorted(seqs) != list(range(1, len(seqs) + 1)):
                gaps.append(f"{doc} {key}: {sorted(seqs)[:8]}...")
    rfq_seqs = sorted(int(n.rsplit("-", 1)[1]) for (n,) in db.execute(text("select no from rfq")))
    if rfq_seqs != list(range(1, len(rfq_seqs) + 1)):
        gaps.append(f"rfq: {rfq_seqs[:10]}")
    if gaps:
        out.find("high", f"document numbers have gaps or repeats: {gaps[0]}")
    dup_invites = db.execute(
        text(
            "select count(*) from (select rfq_no, supplier_code from rfq_supplier group by 1, 2 having count(*) > 1) x"
        )
    ).scalar_one()
    dup_po_lines = db.execute(
        text(
            "select count(*) from (select request_no, request_line_no from po_line where status <> 'cancelled' group by 1, 2 having count(*) > 1) x"
        )
    ).scalar_one()
    if dup_invites or dup_po_lines:
        out.find(
            "high",
            f"{dup_invites} duplicate invitation(s), {dup_po_lines} request line(s) on two live orders",
        )
    out.metrics = {
        "serial repeats": 3,
        "concurrent callers": len(results),
        "seconds for the race": raced,
        "tables that grew": grew or "none",
        "extra emails": len(state.outbox.sent) - mails_before,
    }
    return out
