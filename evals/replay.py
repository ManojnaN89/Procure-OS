"""Replay of the dataset's history into a company: orders first, then receipts and invoices in the order they happened.
Shared by the invoice evals (E19 matching, E20 due dates and tax, E21 payment)."""

from collections import defaultdict
from datetime import date, datetime
from types import SimpleNamespace

import lib
from lib import D, rows
from sqlalchemy import text


def columns(db, table: str) -> set[str]:
    return {
        r[0]
        for r in db.execute(
            text("select column_name from information_schema.columns where table_name = :t"), {"t": table}
        )
    }


def insert(db, table: str, row: dict, extra: dict | None = None) -> None:
    have = columns(db, table)
    values = {k: (v if v != "" else None) for k, v in row.items() if k in have}
    values.update(extra or {})
    cols = ", ".join(values)
    marks = ", ".join(f":{k}" for k in values)
    db.execute(text(f"insert into {table} ({cols}) values ({marks})"), values)  # noqa: S608


def when(value: str) -> datetime:
    return datetime.fromisoformat(value)


def replay(world, *, release_blocked: bool = False, load_bank_rates: bool = False):
    from p2p.invoices import service as invoices
    from p2p.platform import sod

    ap = world.logins["AP"]
    history = lambda name: rows("history", name)  # noqa: E731
    if load_bank_rates:
        for r in rows("reference", "rbi_bank_rate"):
            world.db.execute(
                text("insert into ref_rbi_bank_rate (effective_from, rate_pct) values (:d, :r) on conflict do nothing"),
                {"d": r["effective_from"], "r": r["rate_pct"]},
            )
        world.db.commit()
    from p2p.invoices import service as invoices

    db = world.db
    ap = world.logins["AP"]
    history = lambda name: rows("history", name)  # noqa: E731

    # the orders as they ended up; receipts arrive later, one at a time
    for r in history("po"):
        insert(db, "po", r)
    for r in history("po_line"):
        insert(
            db,
            "po_line",
            r,
            {
                "received_qty": 0,
                "invoiced_qty": 0,
                "source": "award",
                "request_no": r["po_no"],
                "request_line_no": int(r["line_no"]),
                "taxable": (D(r["qty"]) * D(r["unit_price"])).quantize(D("0.01")),
            },
        )
    for r in history("po_charge"):
        insert(db, "po_charge", r)
    db.commit()

    receipts = {r["no"]: r for r in history("goods_receipt")}
    receipt_lines: dict[str, list[dict]] = defaultdict(list)
    for r in history("gr_line"):
        receipt_lines[r["gr_no"]].append(r)
    all_invoices = history("invoice")
    inv_lines: dict[str, list[dict]] = defaultdict(list)
    for r in history("invoice_line"):
        inv_lines[r["invoice_no"]].append(r)
    inv_charges: dict[str, list[dict]] = defaultdict(list)
    for r in history("invoice_charge"):
        inv_charges[r["invoice_no"]].append(r)
    truth = {r["invoice_no"]: r for r in rows("truth", "truth_match_expected")}

    # an invoice is entered once the receipt it is for has been posted (the dataset names it in the receipt)
    receipt_for = {
        (g["po_no"], g["supplier_invoice_ref"]): when(g["gate_entry_at"])
        for g in receipts.values()
        if g["supplier_invoice_ref"]
    }
    events = [(when(r["gate_entry_at"]), 0, "receipt", r["no"]) for r in receipts.values()]
    for r in all_invoices:
        first_po = inv_lines[r["no"]][0]["po_no"] if inv_lines[r["no"]] else ""
        arrived = max(
            when(r["received_at"]), receipt_for.get((first_po, r["supplier_invoice_no"]), when(r["received_at"]))
        )
        events.append((arrived, 1, "invoice", r["no"]))
    events.sort(key=lambda e: (e[0], e[1], e[3]))
    by_no = {r["no"]: r for r in all_invoices}

    platform_no: dict[str, str] = {}
    entered: dict[str, object] = {}
    errors: list[str] = []
    released: set[str] = set()
    for _, _, kind, key in events:
        if kind == "receipt":
            g = receipts[key]
            insert(db, "goods_receipt", g, {"posted_by": "import"})
            for ln in receipt_lines[key]:
                insert(db, "gr_line", ln)
                db.execute(
                    text("update po_line set received_qty = received_qty + :q where po_no = :p and line_no = :l"),
                    {"q": ln["qty_accepted"], "p": ln["po_no"], "l": ln["po_line_no"]},
                )
            db.commit()
            continue
        r = by_no[key]
        data = invoices.InvoiceIn(
            supplier_code=r["supplier_code"],
            supplier_invoice_no=r["supplier_invoice_no"],
            invoice_date=date.fromisoformat(r["invoice_date"]),
            type=r["type"],
            supplier_gstin=r["supplier_gstin"] or None,
            buyer_gstin=r["buyer_gstin"] or None,
            place_of_supply=r["place_of_supply"] or None,
            irn=r["irn"] or None,
            ack_no=r["ack_no"] or None,
            ack_date=date.fromisoformat(r["ack_date"]) if r["ack_date"] else None,
            eway_bill_no=r["eway_bill_no"] or None,
            reverse_charge=r["reverse_charge"] == "true",
            channel=r["channel"],
            received_at=when(r["received_at"]),
            taxable=D(r["taxable"]),
            cgst=D(r["cgst"]),
            sgst=D(r["sgst"]),
            igst=D(r["igst"]),
            cess=D(r["cess"]),
            round_off=D(r["round_off"]),
            total=D(r["total"]),
            ref_invoice_no=platform_no.get(r["ref_invoice_no"]) if r["ref_invoice_no"] else None,
            lines=[
                invoices.InvLine(
                    po_no=ln["po_no"],
                    po_line_no=int(ln["po_line_no"]),
                    qty=D(ln["qty"]),
                    unit_price=D(ln["unit_price"]),
                    discount=D(ln["discount"] or "0"),
                    taxable=D(ln["taxable"]),
                    gst_rate=D(ln["gst_rate"] or "0"),
                    cgst=D(ln["cgst"]),
                    sgst=D(ln["sgst"]),
                    igst=D(ln["igst"]),
                    description=ln["description"],
                    hsn_sac=ln["hsn_sac"],
                    uom=ln["uom"],
                )
                for ln in inv_lines[key]
            ],
            charges=[
                invoices.InvCharge(c["type"], D(c["amount"]), D(c["gst_rate"] or "0"), c["sac"])
                for c in inv_charges[key]
            ],
        )
        try:
            done = invoices.enter_invoice(db, data, ap, as_of=date.fromisoformat(r["received_on"]))
        except invoices.InvoiceError as exc:
            db.rollback()
            errors.append(f"{key}: {exc}")
            continue
        platform_no[key] = done.no
        entered[key] = done
        if release_blocked and done.status == "blocked" and r["status"] not in ("cancelled", "received", "blocked"):
            # the dataset resolved this one by accepting it: a person other than the one who entered it releases the block
            invoices._release(db, {"invoice": done.no, "reason": "accepted, as the dataset records"}, sod.user(world.logins["Finance"]))
            released.add(key)

    return SimpleNamespace(
        entered=entered, platform_no=platform_no, by_no=by_no, truth=truth, inv_lines=inv_lines,
        receipt_lines=receipt_lines, receipts=receipts, errors=errors, released=released,
    )
