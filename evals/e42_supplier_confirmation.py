"""E42: the supplier's answer to an order (plan step 8, P1-36), on the main demo company, against an oracle from the raw files.

Orders the pipeline made are sent with their signed link. The plan's worked example: two suppliers confirm from the link
and the bearing supplier (here: the supplier of the first order) asks to move a line to 6 Dec, the buyer accepts, and
the order is version 2 with the new date and the requester is told. Price changes at the tolerance edge (the largest
two-decimal price within `supplier_price_pct` of the dataset, worked out with exact fractions, and one paisa above): the
first is the buyer's, the second waits for the person the dataset's price variance rule names, found from the raw rule
and approver role, and only that person can accept it. A decline cancels the order and the line goes back to the buyer;
nothing orders it again by itself. A part dispatch leaves exactly the rest to ship. Links reused, expired, altered, for
another supplier's order or signed for another purpose are refused. Delivery reminders are walked day by day over a
holiday Monday and checked against an independent working-day count from the dataset's settings. Every change is
audited. Two rules are broken on purpose and the eval must notice each.
"""

from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal
from fractions import Fraction
from typing import Any

import lib
import pipeline
from fastapi.testclient import TestClient
from lib import Outcome, rows
from sqlalchemy import text

ID = "E42"
TITLE = "Supplier confirmation: the link works once, every change is the buyer's, a new version tells the requester"
REQUIREMENT = (
    "Plan step 8, P1-36: accept, decline or ask for a change from a signed link that works once (or by email); each change "
    "goes to the buyer old value to new; an accepted date, quantity or price makes a new version and tells the requester; "
    "a price above tolerance needs the purchase manager; a decline goes back to the buyer to source again; dispatch notes "
    "for the store; reminders before the promised date and after it while nothing has arrived, on the business calendar, "
    "each once. Done when a date change makes PO version 2 and tells the requester"
)
NEW_DATE = date(2026, 12, 6)
HOLIDAY = date(2026, 11, 16)  # a Monday made a holiday for the reminder walk
PROMISED = date(2026, 11, 17)  # a Tuesday, the day after the holiday


def setting(key: str, default: str) -> int:
    return int(next((r["value"] for r in rows("master", "setting") if r["key"] == key), default))


def tolerance(key: str, default: str) -> Fraction:
    found = next(
        (r["value"] for r in rows("master", "tolerance") if r["key"] == key and r["category_code"] in ("ANY", "")), None
    )
    return Fraction(found or default)


def price_approver() -> str:
    """Who accepts a price rise, from the raw rule table and the company's approver roles: the role of the price variance
    rule, then the person of that designation."""
    rule = next(r for r in rows("master", "approval_rule") if r["document"] == "price_variance")
    role = rule["approver_chain"].split(">")[0].lstrip("+")
    source, value = next((s, v) for r, s, v in lib.demo_company().approver_roles if r == role)
    assert source == "designation", source
    return next(u["code"] for u in rows("master", "user") if u["designation"] == value)


def working(d: date) -> bool:
    return d.weekday() < 5 and d != HOLIDAY


def step(d: date, n: int, direction: int) -> date:
    while n:
        d += timedelta(days=direction)
        n -= working(d)
    return d


def reminder_oracle(start: date, end: date) -> list[tuple[date, str, int]]:
    """Which reminder goes on which day, with nothing ever arriving."""
    before, after = setting("delivery_reminder_before_days", "2"), setting("delivery_overdue_after_days", "1")
    every, most = setting("delivery_overdue_every_days", "3"), setting("delivery_overdue_max", "3")
    out = [(step(PROMISED, before, -1), "before", 1)]
    out += [(step(PROMISED, after + n * every, 1), "overdue", n + 1) for n in range(most)]
    return [x for x in out if start <= x[0] <= end]


def edge_prices(old: Decimal, pct: Fraction) -> tuple[Decimal, Decimal]:
    """The largest two-decimal price within the tolerance, and one paisa more."""
    limit = Fraction(old) * (1 + pct / 100)
    inside = (Decimal(limit.numerator) / Decimal(limit.denominator)).quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    assert Fraction(inside) <= limit < Fraction(inside + Decimal("0.01"))
    return inside, inside + Decimal("0.01")


def _copy_order(db: Any, source: str, no: str) -> None:
    """A copy of an order, approved and not sent, for a copy of its request: the same supplier, lines, prices and tax."""

    def columns(table: str) -> list[str]:
        return [
            r[0]
            for r in db.execute(
                text(
                    "select column_name from information_schema.columns where table_name = :t "
                    "and coalesce(column_default, '') not like 'nextval%' order by ordinal_position"
                ),
                {"t": table},
            )
        ]

    request = db.execute(text("select request_no from po where no = :n"), {"n": source}).scalar_one()
    copies = [  # (table, key column, key value, columns to set)
        ("request", "no", request, {"no": ":r"}),
        ("request_line", "request_no", request, {"request_no": ":r"}),
        ("sourcing_decision", "request_no", request, {"request_no": ":r"}),
        ("po", "no", source, {"no": ":n", "request_no": ":r", "status": "'approved'", "sent_at": "null",
                              "sent_to": "null", "acknowledged_at": "null", "version": "1", "hold_reason": "null",
                              "hold_proposal_id": "null", "touchless": "false"}),
        ("po_line", "po_no", source, {"po_no": ":n", "request_no": ":r", "status": "'open'", "received_qty": "0",
                                      "promised_date": "null"}),
        ("po_charge", "po_no", source, {"po_no": ":n"}),
    ]  # fmt: skip
    for table, key, value, set_to in copies:
        cols = columns(table)
        picked = ", ".join(set_to.get(c, c) for c in cols)
        db.execute(
            text(f"insert into {table} ({', '.join(cols)}) select {picked} from {table} where {key} = :s"),  # noqa: S608
            {"n": no, "r": f"{request}-{no[-5:]}", "s": value},
        )
    db.commit()


def run(ctx: lib.Ctx) -> Outcome:  # noqa: C901 - one scenario, step by step
    from p2p.approvals import email_links
    from p2p.main import app
    from p2p.orders import confirmation, delivery_reminders, dispatch, supplier_links
    from p2p.orders import service as orders
    from p2p.platform import sod, users

    out = Outcome(True)
    state = pipeline.run_pipeline(lib.build_world("e42"))
    world, db, box = state.world, state.world.db, state.outbox
    slug, client = world.slug, TestClient(app)
    buyer = world.headers("Buyer")

    # every approved order goes to its supplier with the link; the pipeline makes few orders, so each scenario gets its
    # own copy of one (same supplier, lines, prices and tax), approved and not yet sent
    out.metrics["orders the pipeline made, by status"] = dict(
        db.execute(text("select status, count(*) from po group by status order by 1")).all()
    )
    source = db.execute(text("select no from po where status in ('approved', 'sent') order by no limit 1")).scalar()
    if source is None:
        out.find("high", "the pipeline made no order that could be sent: nothing to test")
        return out
    for n in range(1, 7):
        _copy_order(db, str(source), f"{source}-E42-{n}")
    for (no,) in db.execute(text("select no from po where status = 'approved' order by no")).all():
        try:
            dispatch.send_po(db, no, sod.user(world.logins["Buyer"]), tenant=slug)
        except dispatch.DispatchError:
            db.rollback()
    tokens: dict[str, str] = {}
    for m in box.sent:
        if m["subject"].startswith("Purchase order ") and "/supplier/" in m["body"]:
            tokens[m["subject"].split()[2]] = m["body"].split("/supplier/")[1].split()[0]
    pos = [
        r[0] for r in db.execute(text("select no from po where status = 'sent' order by no")).all() if r[0] in tokens
    ]
    out.metrics["orders sent with a link"] = len(pos)
    if len(pos) < 5:
        out.find("high", f"only {len(pos)} orders went out with a link; five are needed")
        return out
    example, within_po, above_po, declined_po, *confirming = pos
    db.commit()

    def audits(action: str) -> int:
        db.rollback()
        return int(db.execute(text("select count(*) from audit_event where action = :a"), {"a": action}).scalar_one())

    def post(token: str, **data: str) -> Any:
        return client.post(f"/supplier/{token}", data=data)

    def one_change(po_no: str) -> dict[str, Any]:
        changes = client.get(f"/api/purchase-orders/{po_no}/supplier-answer", headers=buyer).json()["changes"]
        return dict(changes[0])

    # 1. the worked example: the others confirm, the first supplier asks to move line 1 to 6 Dec, the buyer accepts
    code, _ = lib.requester()
    own = f"requester.{slug}@eval.example"
    db.execute(text("update org_user set email = :e where code = :c"), {"e": own, "c": code})
    requester_login = users.create_user(slug, own, lib.PASSWORD, "Requester", "Requester")
    db.execute(
        text("update request set requester = :c where no in (select request_no from po_line where po_no = :p)"),
        {"c": code, "p": example},
    )
    db.commit()
    confirmed = [no for no in confirming[:2] if post(tokens[no], action="accept").status_code == 200]
    db.rollback()
    acks = db.execute(
        text("select count(*) from po where no = any(:n) and status = 'acknowledged'"), {"n": confirmed}
    ).scalar_one()
    if len(confirmed) != len(confirming[:2]) or acks != len(confirmed):
        out.find("high", f"accepting from the link: {len(confirmed)} answered, {acks} acknowledged")
    asked = post(tokens[example], action="change", date_1=NEW_DATE.isoformat(), reason="casting delay")
    before = db.execute(text("select version from po where no = :n"), {"n": example}).scalar_one()
    change = one_change(example)
    decided = client.post(f"/api/supplier-changes/{change['id']}/decide", json={"accept": True}, headers=buyer)
    db.rollback()
    version = db.execute(text("select version from po where no = :n"), {"n": example}).scalar_one()
    line = db.execute(
        text("select need_by, promised_date from po_line where po_no = :n and line_no = 1"), {"n": example}
    ).one()
    told = db.execute(
        text("select count(*) from notification where user_id = :u and title = :t"),
        {"u": requester_login, "t": f"Order {example} changed (version 2)"},
    ).scalar_one()
    out.metrics["worked example: version / new date / requester told"] = f"{version} / {line[0]} / {bool(told)}"
    if asked.status_code != 200 or before != 1 or (change["old_value"], change["new_value"]) == (None, None):
        out.find("high", f"the date change was not proposed: {asked.status_code}, version {before}, {change}")
    if decided.status_code != 200 or version != 2 or tuple(line) != (NEW_DATE, NEW_DATE) or told != 1:
        out.find(
            "high", f"accepting the date change: {decided.status_code}, version {version}, {tuple(line)}, told {told}"
        )

    # 2. the price edge: within is the buyer's, a paisa above waits for the person the rule names
    pct = tolerance("supplier_price_pct", "2")
    approver = price_approver()
    for po_no, above in ((within_po, False), (above_po, True)):
        old, qty = db.execute(
            text("select unit_price, qty from po_line where po_no = :n and line_no = 1"), {"n": po_no}
        ).one()
        inside, outside = edge_prices(Decimal(old), pct)
        new = outside if above else inside
        post(tokens[po_no], action="change", price_1=str(new))
        ch = one_change(po_no)
        first = client.post(f"/api/supplier-changes/{ch['id']}/decide", json={"accept": True}, headers=buyer).json()
        if above:
            others = [c for c in world.org_logins if c != approver]
            wrong = client.post(
                f"/api/supplier-changes/{ch['id']}/decide", json={"accept": True}, headers=world.org_headers(others[0])
            )
            right = client.post(
                f"/api/supplier-changes/{ch['id']}/decide", json={"accept": True}, headers=world.org_headers(approver)
            )
            if first.get("status") != "waiting_approval" or wrong.status_code != 403 or right.status_code != 200:
                out.find(
                    "high",
                    f"{new} on {old} (above {pct}%): {first}, another approver {wrong.status_code}, {approver} {right.status_code}",
                )
        elif first.get("status") != "accepted":
            out.find("high", f"{new} on {old} (within {pct}%) was not the buyer's to accept: {first}")
        db.rollback()
        got = db.execute(
            text("select unit_price, taxable from po_line where po_no = :n and line_no = 1"), {"n": po_no}
        ).one()
        want_taxable = (Decimal(qty) * new).quantize(Decimal("0.01"))
        if Decimal(got[0]) != new or Decimal(got[1]) != want_taxable:
            out.find("high", f"{po_no}: price {got[0]} taxable {got[1]}, oracle {new} and {want_taxable}")
        out.metrics[f"price {'one paisa above' if above else 'at'} the {pct}% edge"] = (
            f"{old} -> {new}: {'purchase manager' if above else 'buyer'}"
        )

    # 3. a decline goes back to the buyer, and nothing orders it again by itself
    post(tokens[declined_po], action="decline", reason="our furnace is down")
    ch = one_change(declined_po)
    client.post(f"/api/supplier-changes/{ch['id']}/decide", json={"accept": True}, headers=buyer)
    db.rollback()
    orders_before = db.execute(text("select count(*) from po")).scalar_one()
    orders.sweep(db, slug, as_of=lib.TODAY)
    status = db.execute(text("select status from po where no = :n"), {"n": declined_po}).scalar_one()
    manual = db.execute(
        text(
            "select count(*) from sourcing_decision d join po_line l on l.request_no = d.request_no "
            "and l.request_line_no = d.line_no where l.po_no = :n and d.method <> 'manual'"
        ),
        {"n": declined_po},
    ).scalar_one()
    orders_after = db.execute(text("select count(*) from po")).scalar_one()
    out.metrics["declined order / lines left for a buyer / new orders made"] = (
        f"{status} / {manual == 0} / {orders_after - orders_before}"
    )
    if status != "cancelled" or manual or orders_after != orders_before:
        out.find("high", f"decline: {status}, {manual} line(s) not manual, {orders_after - orders_before} new order(s)")

    # 4. a part dispatch leaves exactly the rest
    if confirmed:
        po_no = confirmed[0]
        link = next(
            m["body"].split("/supplier/")[1].split()[0]
            for m in reversed(box.sent)
            if po_no in m["subject"] and "/supplier/" in m["body"]
        )
        qty = int(
            db.execute(text("select qty from po_line where po_no = :n and line_no = 1"), {"n": po_no}).scalar_one()
        )
        half = max(1, qty // 2)
        first = post(link, action="dispatch", ship_1=str(half), shipped_on=lib.TODAY.isoformat(), lr_no="LR-42")
        rest = (
            next(
                m["body"].split("/supplier/")[1].split()[0]
                for m in reversed(box.sent)
                if "the rest to ship" in m["subject"]
            )
            if qty > half
            else None
        )
        checks = [first.status_code == 200]
        if rest:
            checks += [post(rest, action="dispatch", ship_1=str(qty - half + 1)).status_code == 409]
            checks += [post(rest, action="dispatch", ship_1=str(qty - half)).status_code == 200]
        waiting = client.get("/api/receiving/waiting", headers=world.headers("Store")).json()
        shown = sum(len(o["dispatches"]) for o in waiting if o["id"] == po_no)
        out.metrics["part dispatch: checks passed / notes the store sees"] = f"{sum(checks)} of {len(checks)} / {shown}"
        if not all(checks) or shown != len(checks) - (1 if rest else 0):
            out.find("high", f"dispatch notes on {po_no}: {checks}, the store sees {shown}")

    # 5. links: reused, expired, altered, another supplier's order, another purpose's key
    link = supplier_links.read(tokens[example])
    far = 4_102_444_800
    other_supplier = db.execute(
        text("select code from supplier where code <> (select supplier_code from po where no = :n) limit 1"),
        {"n": example},
    ).scalar_one()
    db.execute(
        text(
            "insert into supplier_link (po_no, supplier_code, purpose, po_version, expires_at) values (:p, :s, 'respond', 2, now() + interval '1 day')"
        ),
        {"p": example, "s": other_supplier},
    )
    other_id = db.execute(text("select max(id) from supplier_link")).scalar_one()
    db.commit()
    probes = {
        "reused": (post(tokens[confirming[0]], action="accept").status_code if confirming else 409, 409),
        "expired": (
            client.get(
                f"/supplier/{supplier_links.SIGNER.sign([slug, link.id, example, link.supplier_code, 'respond', 1000])}"
            ).status_code,
            400,
        ),
        "altered": (client.get(f"/supplier/{tokens[example][:-2]}xx").status_code, 400),
        "another supplier's order": (
            client.get(
                f"/supplier/{supplier_links.SIGNER.sign([slug, other_id, example, other_supplier, 'respond', far])}"
            ).status_code,
            409,
        ),
        "an approval key": (
            client.get(
                f"/supplier/{email_links.SIGNER.sign([slug, link.id, example, link.supplier_code, 'respond', far])}"
            ).status_code,
            400,
        ),
    }
    out.metrics["links refused (reused, expired, altered, other supplier, other key)"] = sum(
        g == w for g, w in probes.values()
    )
    for name, (got, want) in probes.items():
        if got != want:
            out.find("high", f"a link {name}: answered {got}, expected {want}")

    # 6. reminders, day by day over a holiday Monday, against the oracle
    target = confirmed[-1] if confirmed else None
    if target:
        db.execute(
            text(
                "insert into ref_holiday (holiday_date, name, kind, scope) values (:d, 'Eval holiday', 'closed', 'central')"
            ),
            {"d": HOLIDAY},
        )
        db.execute(
            text("update po_line set promised_date = :d, received_qty = 0 where po_no = :n"),
            {"d": PROMISED, "n": target},
        )
        db.execute(text("delete from dispatch_note where po_no = :n"), {"n": target})
        db.execute(text("delete from delivery_reminder where po_no = :n"), {"n": target})
        db.commit()
        start, end = date(2026, 11, 5), date(2026, 12, 10)
        got: list[tuple[date, str, int]] = []
        day = start
        while day <= end:
            for po_no, kind, n in delivery_reminders.run(db, slug, as_of=day).sent:
                if po_no == target:
                    got.append((day, kind, n))
            delivery_reminders.run(db, slug, as_of=day)  # the same day again sends nothing more
            day += timedelta(days=1)
        want = reminder_oracle(start, end)
        stored = db.execute(text("select count(*) from delivery_reminder where po_no = :n"), {"n": target}).scalar_one()
        out.metrics["reminders (platform / oracle)"] = (
            f"{[f'{d:%d %b} {k} {n}' for d, k, n in got]} / {len(want)} expected"
        )
        if got != want or stored != len(want):
            out.find("high", f"reminders for {target}: {got}, oracle {want}, {stored} stored")

    # 7. every step audited
    trail = {
        a: audits(a)
        for a in (
            "po.change_proposed",
            "po.change_accepted",
            "po.revise",
            "po.change_referred",
            "po.dispatch_note",
            "po.delivery_reminder",
            "po.acknowledge",
        )
    }
    out.metrics["audit rows"] = trail
    if any(v == 0 for v in trail.values()):
        out.find("high", f"no audit rows for {[a for a, v in trail.items() if v == 0]}")

    out.metrics["mutation checks (rule broken on purpose: caught?)"] = _mutations(
        db, slug, within_po, confirmation, supplier_links, client, box, out
    )
    return out


def _mutations(
    db: Any, slug: str, po_no: str, confirmation: Any, supplier_links: Any, client: Any, box: Any, out: Outcome
) -> dict[str, str]:
    from p2p.formulas import supplier_changes

    results: dict[str, str] = {}
    pct = tolerance("supplier_price_pct", "2")

    # a price rise of any size is the buyer's: the stored verdict must disagree with the oracle
    saved = supplier_changes.judge_price
    supplier_changes.judge_price = lambda o, n, t: supplier_changes.Verdict(supplier_changes.IN_TOLERANCE, "mutated")
    try:
        old = Decimal(
            db.execute(
                text("select unit_price from po_line where po_no = :n and line_no = 1"), {"n": po_no}
            ).scalar_one()
        )
        _, outside = edge_prices(old, pct)
        (cid,) = confirmation.propose(
            db,
            slug,
            po_no,
            [confirmation.Proposed("price", 1, str(outside))],
            source="link",
            reason="mutation",
            actor=confirmation.supplier_actor("eval"),
        )
        within = db.execute(text("select within_tolerance from po_change where id = :i"), {"i": cid}).scalar_one()
        oracle_within = Fraction(outside) - Fraction(old) <= Fraction(old) * pct / 100
        results["a price rise of any size is the buyer's"] = "caught" if within != oracle_within else "NOT caught"
        db.execute(text("update po_change set status = 'withdrawn' where id = :i"), {"i": cid})
        db.commit()
    finally:
        supplier_changes.judge_price = saved

    # a link that works more than once
    saved_use = supplier_links.use
    supplier_links.use = lambda db_, link, what: None
    try:
        token = supplier_links.issue(db, slug, po_no, "dispatch")
        db.commit()
        codes = [
            client.post(f"/supplier/{token}", data={"action": "dispatch", "ship_1": "0.0001"}).status_code
            for _ in range(2)
        ]
        results["a link works more than once"] = "caught" if codes[1] != 409 else "NOT caught"
    finally:
        supplier_links.use = saved_use
    for name, verdict in results.items():
        if verdict != "caught":
            out.find("high", f"mutation '{name}' was not caught: the eval cannot tell this rule is broken")
    return results
