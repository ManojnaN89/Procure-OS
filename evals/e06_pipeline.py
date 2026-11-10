"""E06: the whole flow from stock level to approved purchase order, checked against the plan's invariants."""

from collections import Counter, defaultdict
from decimal import Decimal

import pipeline
from e04_gst import oracle_line
from lib import Outcome, rows
from sqlalchemy import text

ID = "E06"
TITLE = "The whole flow, stock level to approved purchase order, keeps its promises"
REQUIREMENT = "Plan: nothing is lost, money reconciles, every change is audited, only eligible suppliers are asked, stock on the way is counted once"


def run(ctx) -> Outcome:
    out = Outcome(True)
    state = pipeline.get_state(ctx)
    world, db = state.world, state.world.db
    counts = pipeline.counts(db)

    # 1. every replenished line ends up somewhere that can be explained
    fates: Counter[str] = Counter()
    stuck: list[str] = []
    for r in db.execute(
        text(
            "select rl.request_no, rl.line_no, rl.status, r.status as rs, d.method, d.rfq_no, "
            "exists (select 1 from po_line p where p.request_no = rl.request_no and p.request_line_no = rl.line_no and p.status <> 'cancelled') as ordered, "
            "exists (select 1 from award a join rfq_line l on l.rfq_no = a.rfq_no and l.line_no = a.rfq_line_no "
            "        where l.request_no = rl.request_no and l.request_line_no = rl.line_no) as awarded "
            "from request_line rl join request r on r.no = rl.request_no "
            "left join sourcing_decision d on d.request_no = rl.request_no and d.line_no = rl.line_no"
        )
    ).mappings():
        key = f"{r['request_no']} line {r['line_no']}"
        if r["ordered"]:
            fates["ordered"] += 1
        elif r["rs"] in ("rejected", "cancelled"):
            fates["request refused"] += 1
        elif r["rs"] in ("draft", "submitted", "returned"):
            fates["waiting for approval or not routable"] += 1
        elif r["method"] is None:
            fates["STUCK approved, not sourced"] += 1
            stuck.append(f"{key}: approved but never sourced")
        elif r["method"] == "manual":
            fates["left for a buyer (nobody to ask)"] += 1
        elif r["method"] == "rfq" and not r["awarded"]:
            fates["quotation open"] += 1
        else:
            fates["STUCK sourced, not ordered"] += 1
            stuck.append(f"{key}: {r['method']} sourced but no purchase order")
    for k in [k for k in fates if k.startswith("STUCK")]:
        out.find("medium", f"{fates[k]} line(s) {k[6:]}; e.g. {stuck[0]}")

    # 2. money reconciles, recomputed with exact fractions from the raw dataset
    sup = {r["code"]: r for r in rows("master", "supplier")}
    loc = {r["code"]: r for r in rows("master", "location")}
    po_bad = line_bad = checked_lines = 0
    for po in db.execute(text("select * from po order by no")).mappings():
        place = loc[po["ship_to_location"]]["gstin"][:2]
        s = sup[po["supplier_code"]]
        lines = (
            db.execute(
                text("select * from po_line where po_no = :n and status <> 'cancelled'"),
                {"n": po["no"]},
            )
            .mappings()
            .all()
        )
        parts = []
        for ln in lines:
            want = oracle_line(
                ln["qty"],
                ln["unit_price"],
                ln["gst_rate"],
                s["state_code"],
                place,
                s["gst_reg_type"],
            )
            checked_lines += 1
            if (ln["taxable"], ln["cgst"], ln["sgst"], ln["igst"]) != want:
                line_bad += 1
            parts.append(want)
        charge_rate = max((ln["gst_rate"] for ln in lines), default=Decimal(0))
        for ch in db.execute(text("select * from po_charge where po_no = :n"), {"n": po["no"]}).mappings():
            parts.append(
                oracle_line(
                    Decimal(1),
                    ch["amount"],
                    charge_rate,
                    s["state_code"],
                    place,
                    s["gst_reg_type"],
                )
            )
        taxable = sum((p[0] for p in parts), Decimal(0))
        tax = [sum((p[i] for p in parts), Decimal(0)) for i in (1, 2, 3)]
        if po["status"] != "cancelled" and (
            po["taxable"],
            po["cgst"],
            po["sgst"],
            po["igst"],
            po["total"],
        ) != (taxable, *tax, taxable + sum(tax)):
            po_bad += 1
    if line_bad or po_bad:
        out.find(
            "high",
            f"{line_bad} line(s) and {po_bad} order total(s) differ from the exact recomputation",
        )

    # 3. every change left an audit row
    audit = Counter(r[0] for r in db.execute(text("select action from audit_event")))
    n_req, n_rfq, n_po = counts["request"], counts["rfq"], counts["po"]
    submitted = db.execute(text("select count(*) from request where approval_round >= 1")).scalar_one()
    approved_reqs = db.execute(text("select count(*) from request where status in ('approved', 'closed')")).scalar_one()
    approved_pos = db.execute(text("select count(*) from po where status = 'approved'")).scalar_one()
    expectations = {
        "request.create": n_req,
        "request.submit": submitted,
        "request.auto_approve + request.approved": approved_reqs,
        "rfq.create": n_rfq,
        "rfq.send": db.execute(text("select count(*) from rfq where status in ('sent', 'awarded')")).scalar_one(),
        "quote.enter": counts["quote"],
        "po.create": n_po,
        "po.auto_approve + po.approved": approved_pos,
    }
    got = {
        "request.create": audit["request.create"],
        "request.submit": audit["request.submit"],
        "request.auto_approve + request.approved": audit["request.auto_approve"] + audit["request.approved"],
        "rfq.create": audit["rfq.create"],
        "rfq.send": audit["rfq.send"],
        "quote.enter": audit["quote.enter"],
        "po.create": audit["po.create"],
        "po.auto_approve + po.approved": audit["po.auto_approve"] + audit["po.approved"],
    }
    for k, want in expectations.items():
        if got[k] != want:
            out.find("high", f"audit gap: {want} {k} expected, {got[k]} audit rows found")

    # 4. only suppliers that may be asked were asked, once each
    sanctioned = {
        n.lower() for r in rows("master", "sanction_entry") for n in [r["name"], *r["aliases"].split(";")] if n
    }
    approved_for = defaultdict(set)
    for r in rows("master", "supplier_category"):
        approved_for[r["category_code"]].add(r["supplier_code"])
    contacts = defaultdict(set)
    for r in rows("master", "supplier_contact"):
        contacts[r["supplier_code"]].add(r["email"].lower())
    asked_wrong: list[str] = []
    for rfq_no, category, supplier, email in db.execute(
        text(
            "select r.no, r.category_code, s.supplier_code, s.email from rfq r join rfq_supplier s on s.rfq_no = r.no where s.invited_at is not null"
        )
    ):
        row = sup[supplier]
        if supplier not in approved_for[category]:
            asked_wrong.append(f"{rfq_no}: {supplier} is not approved for {category}")
        if row["status"] != "active":
            asked_wrong.append(f"{rfq_no}: {supplier} is {row['status']}")
        if (email or "").lower() not in contacts[supplier]:
            asked_wrong.append(f"{rfq_no}: {email} is not a contact of {supplier}")
        if row["legal_name"].lower() in sanctioned:
            asked_wrong.append(f"{rfq_no}: {supplier} is on the sanctions list")
    dup = [k for k, v in Counter((m["to"], m["subject"]) for m in state.outbox.sent).items() if v > 1]
    invited_total = db.execute(text("select count(*) from rfq_supplier where invited_at is not null")).scalar_one()
    if asked_wrong:
        out.find(
            "high",
            f"{len(asked_wrong)} RFQ invitation(s) went to a supplier that may not be asked; e.g. {asked_wrong[0]}",
        )
    if dup:
        out.find(
            "high",
            f"{len(dup)} supplier(s) received the same RFQ email twice, e.g. {dup[0]}",
        )
    if len(state.outbox.sent) != invited_total:
        out.find(
            "high",
            f"{len(state.outbox.sent)} emails sent but {invited_total} suppliers marked as invited",
        )

    # 5. stock on the way is counted once: recomputed here from the tables
    from p2p.receiving.stock import stock_levels

    want_on_order: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for item, location, qty in db.execute(
        text(
            "select rl.item_code, rl.delivery_location, rl.base_qty from request_line rl join request r on r.no = rl.request_no where rl.status in ('open','sourcing') and r.status in ('draft','submitted','approved','returned')"
        )
    ):
        want_on_order[(item, location)] += qty or 0
    for item, location, base, qty, received in db.execute(
        text(
            "select pl.item_code, po.ship_to_location, pl.base_qty, pl.qty, pl.received_qty from po_line pl join po on po.no = pl.po_no where pl.status in ('open','part_received') and po.status in ('draft','submitted','approved','sent','acknowledged','part_received')"
        )
    ):
        want_on_order[(item, location)] += (base or 0) * max(qty - received, 0) / qty
    got_on_order = {(x.item_code, x.location_code): x.on_order for x in stock_levels(db) if x.on_order}
    wrong_stock = [
        k
        for k in set(want_on_order) | set(got_on_order)
        if abs(want_on_order.get(k, Decimal(0)) - got_on_order.get(k, Decimal(0))) > Decimal("0.0001")
    ]
    if wrong_stock:
        out.find(
            "high",
            f"stock on the way differs from the recomputation for {len(wrong_stock)} item(s), e.g. {wrong_stock[0]}",
        )

    # 6. a login for everyone the chains name
    if state.missing_logins:
        out.find(
            "medium",
            f"people named in approval chains have no login, so their steps could not be done: {sorted(state.missing_logins)}",
        )
    total_seconds = round(sum(state.seconds.values()), 1)
    lines_planned = state.replenishment.line_count
    out.metrics = {
        "requests": n_req,
        "lines": lines_planned,
        "lines ordered": fates["ordered"],
        "RFQs": n_rfq,
        "quotes": counts["quote"],
        "purchase orders": n_po,
        "orders approved": approved_pos,
        "line fates": dict(fates),
        "stage seconds": state.seconds,
        "total seconds": total_seconds,
        "company build seconds": world.seconds_to_build,
        "orders lines checked": checked_lines,
    }
    if n_po == 0:
        out.find("high", "the flow produced no purchase order at all")
    return out
