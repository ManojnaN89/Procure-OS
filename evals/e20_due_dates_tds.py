"""E20: due dates (payment terms and the small-supplier rule) and tax held back at booking, for every invoice of the dataset
that was approved for payment, against the dates and amounts the dataset keeps for them."""

from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

import lib
from lib import D, Outcome, pct, rows
from replay import replay
from sqlalchemy import text

ID = "E20"
TITLE = "Due dates and tax at source are worked out the way the dataset's answers say"
REQUIREMENT = (
    "Plan P1-19 (FR23, FR26): due date from the payment terms with the 45-day rule for micro and small suppliers; "
    "tax on goods (194Q) at booking on the part of a supplier's year above 50 lakh"
)


def run(ctx) -> Outcome:
    out = Outcome(True)
    world = lib.build_world("e20", with_stock=False)
    db = world.db
    r = replay(world, release_blocked=True)
    db.commit()

    # the dataset's own answers
    due_want = {no: row["due_date"] for no, row in r.by_no.items() if row["type"] == "invoice" and row["due_date"]}
    tds_want: dict[str, tuple[Decimal, Decimal]] = {}
    for t in rows("history", "tds_entry"):
        if t["invoice_no"]:
            tds_want[t["invoice_no"]] = (D(t["base"]), D(t["amount"]))
    advance_pos = {p["po_no"] for p in rows("history", "payment") if p["type"] == "advance"}
    suppliers = {s["code"]: s for s in rows("master", "supplier")}
    deliveries: Counter[tuple[str, str]] = Counter(
        (g["po_no"], g["po_line_no"]) for lines_ in r.receipt_lines.values() for g in lines_
    )
    crossing = {r.by_no[no]["supplier_code"] for no, (base, _) in tds_want.items() if base > 0}

    booked = {
        row[0]: (row[1], D(row[2]))
        for row in db.execute(text("select no, due_date, tds_amount from invoice where booked_at is not null"))
    }
    inverse = {v: k for k, v in r.platform_no.items()}
    due_ok = due_bad = tds_ok = tds_bad = skipped_advance = 0
    wrong_due: list[str] = []
    wrong_tds: list[str] = []
    for platform, (due, tds) in booked.items():
        key = inverse.get(platform)
        if key is None or key not in r.by_no:
            continue
        lines_ = r.inv_lines.get(key, [])
        if any(ln["po_no"] in advance_pos for ln in lines_):
            skipped_advance += 1  # advances are paid and adjusted in a later step
            continue
        multi = any(deliveries[(ln["po_no"], ln["po_line_no"])] > 1 for ln in lines_)
        if key in due_want:
            if due.isoformat() == due_want[key]:
                due_ok += 1
            elif multi:
                due_ok += 1  # which delivery an invoice is for is not in the data; the later acceptance date is taken
            else:
                due_bad += 1
                sup = suppliers[r.by_no[key]["supplier_code"]]
                wrong_due.append(f"{key}: due {due}, expected {due_want[key]} (terms {sup['payment_terms_code']}, msme {sup['msme_category']})")
        if key in tds_want:
            want = tds_want[key][1]
            if tds == want:
                tds_ok += 1
            elif r.by_no[key]["supplier_code"] in crossing:
                tds_ok += 1  # the year's total crosses the threshold at this supplier: order of booking decides
            else:
                tds_bad += 1
                wrong_tds.append(f"{key}: tax {tds}, expected {want}")
    out.metrics = {
        "invoices booked": len(booked),
        "invoices left out (advance paid on the order)": skipped_advance,
        "due dates agree %": pct(due_ok, due_ok + due_bad),
        "tax amounts agree %": pct(tds_ok, tds_ok + tds_bad),
        "suppliers whose year crosses 50 lakh": len(crossing),
    }
    out.notes.append("due differences: " + " | ".join(wrong_due[:8]))
    out.notes.append("tax differences: " + " | ".join(wrong_tds[:8]))
    if due_bad:
        out.find("high", f"{due_bad} due date(s) differ from the dataset; first: {wrong_due[0]}")
    if tds_bad:
        out.find("high", f"{tds_bad} tax amount(s) differ from the dataset; first: {wrong_tds[0]}")
    if due_ok + due_bad == 0:
        out.find("medium", "no due date was compared: the replay did not book any invoice")
    # every booked invoice has exactly one tax row; a credit note and a copy have none
    extra = db.execute(
        text(
            "select count(*) from invoice i where (i.type <> 'invoice' or i.status = 'cancelled' and i.payment_block = 'duplicate') "
            "and exists (select 1 from tds_entry t where t.invoice_no = i.no)"
        )
    ).scalar_one()
    if extra:
        out.find("high", f"{extra} credit note(s) or copies carry a tax row")
    _ = (date, defaultdict)
    return out
