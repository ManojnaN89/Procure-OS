"""Replay of the dataset's history into a company: orders first, then receipts and invoices in the order they happened.
Shared by the invoice evals (E19 matching, E20 due dates and tax, E21 payment)."""

from collections import defaultdict
from datetime import date, datetime, time
from pathlib import Path
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


def post_service_entry(db, entry: dict) -> None:
    """A service entry of the dataset (the requester's confirmation, by amount) as the platform keeps it: the amount at
    the order price counts as received on the line, so the service's invoice is matched against it."""
    key = {"p": entry["po_no"], "l": int(entry["po_line_no"])}
    price = D(db.execute(text("select unit_price from po_line where po_no = :p and line_no = :l"), key).scalar_one())
    qty = (D(entry["amount_accepted"]) / price).quantize(D("0.0001"))
    extra = {"basis": "amount", "value": D(entry["amount_accepted"]), "qty_accepted": qty, "posted_by": "import"}
    insert(db, "service_entry", entry, extra)
    db.execute(
        text("update po_line set received_qty = received_qty + :q where po_no = :p and line_no = :l"), {"q": qty, **key}
    )


def service_events(rows_: list[dict], allowed: set[str], zone) -> list[tuple]:
    """The dataset's service entries as replay events, from the start of their day (the dataset counts a confirmation
    for an invoice that arrives the same day)."""
    at = lambda r: datetime.combine(date.fromisoformat(r["accepted_on"]), time(0), tzinfo=zone)  # noqa: E731
    return [(at(r), 0, "service", r["no"]) for r in rows_ if r["po_no"] in allowed]


def replay(
    world,
    *,
    release_blocked: bool = False,
    load_bank_rates: bool = False,
    dataset: Path | None = None,
    suppliers: set[str] | None = None,
    goods_only: bool = False,
    advances: bool = False,
):
    """Replay the dataset's history (goods receipts, service entries and invoices in the order they happened).
    `suppliers` and `goods_only` restrict it to the orders (and what follows from them) of
    those suppliers that carry no service line; `advances` pays the dataset's advances (through a payment run approved by
    the Approver login) on the day it paid them, so that invoices received later can take them off."""
    from p2p.invoices import advances, payment, runs
    from p2p.invoices import service as invoices
    from p2p.platform import proposals, sod

    ds = dataset or lib.DATASET
    ap = world.logins["AP"]
    finance, owner = world.logins["Finance"], world.logins["Approver"]
    history = lambda name: rows("history", name, ds)  # noqa: E731
    if load_bank_rates:
        for r in rows("reference", "rbi_bank_rate", lib.ROOT / "data"):  # the repo's reference tables
            world.db.execute(
                text("insert into ref_rbi_bank_rate (effective_from, rate_pct) values (:d, :r) on conflict do nothing"),
                {"d": r["effective_from"], "r": r["rate_pct"]},
            )
        world.db.commit()
    db = world.db

    all_po = history("po")
    all_po_lines = history("po_line")
    if suppliers is not None:
        all_po = [p for p in all_po if p["supplier_code"] in suppliers]
    if goods_only:
        kind_of = {i["code"]: i["kind"] for i in rows("master", "item", ds)}
        service_pos = {ln["po_no"] for ln in all_po_lines if kind_of.get(ln["item_code"]) == "service"}
        all_po = [p for p in all_po if p["no"] not in service_pos]
    allowed = {p["no"] for p in all_po}

    # the orders as they ended up; receipts arrive later, one at a time
    for r in all_po:
        insert(db, "po", r)
    for r in all_po_lines:
        if r["po_no"] not in allowed:
            continue
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
        if r["po_no"] in allowed:
            insert(db, "po_charge", r)
    db.commit()

    receipts = {r["no"]: r for r in history("goods_receipt") if r["po_no"] in allowed}
    receipt_lines: dict[str, list[dict]] = defaultdict(list)
    for r in history("gr_line"):
        receipt_lines[r["gr_no"]].append(r)
    all_invoices = history("invoice")
    inv_lines: dict[str, list[dict]] = defaultdict(list)
    for r in history("invoice_line"):
        inv_lines[r["invoice_no"]].append(r)
    if suppliers is not None or goods_only:
        keep = {n for n, ls in inv_lines.items() if ls and all(ln["po_no"] in allowed for ln in ls)}
        for r in all_invoices:  # a credit note without lines follows the invoice it refers to
            if not inv_lines.get(r["no"]) and r["ref_invoice_no"] in keep:
                keep.add(r["no"])
        all_invoices = [r for r in all_invoices if r["no"] in keep]
    inv_charges: dict[str, list[dict]] = defaultdict(list)
    for r in history("invoice_charge"):
        inv_charges[r["invoice_no"]].append(r)
    truth = {r["invoice_no"]: r for r in rows("truth", "truth_match_expected", ds)}

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
    advance_rows = {
        p["no"]: p for p in history("payment") if advances and p["type"] == "advance" and p["po_no"] in allowed
    }
    zone = events[0][0].tzinfo if events else None  # the dataset's times may or may not carry an offset
    for no, p in advance_rows.items():  # paid at noon, after the morning's receipts and invoices
        events.append((datetime.combine(date.fromisoformat(p["paid_on"]), time(12), tzinfo=zone), 2, "advance", no))
    services = {r["no"]: r for r in history("service_receipt")}
    events += service_events(list(services.values()), allowed, zone)
    events.sort(key=lambda e: (e[0], e[1], e[3]))
    by_no = {r["no"]: r for r in all_invoices}

    platform_no: dict[str, str] = {}
    entered: dict[str, object] = {}
    errors: list[str] = []
    released: set[str] = set()
    advance_payment: dict[str, str] = {}  # the dataset's advance payment -> the platform's
    for _, _, kind, key in events:
        if kind == "advance":
            p = advance_rows[key]
            on = date.fromisoformat(p["paid_on"])
            try:
                # the order is inserted as it ended up (received); an advance is asked for while it was acknowledged
                original = db.execute(text("select status from po where no = :p"), {"p": p["po_no"]}).scalar_one()
                db.execute(text("update po set status = 'acknowledged' where no = :p"), {"p": p["po_no"]})
                asked = advances.request(db, p["po_no"], finance, reason="the dataset's advance", tenant=world.slug)
                db.execute(text("update po set status = :s where no = :p"), {"s": original, "p": p["po_no"]})
                proposals.approve(
                    db,
                    int(asked["proposal"]),
                    world.org_logins.get(str(asked["approver"])) or world.logins["Approver"],
                )
                run = runs.propose_run(db, on, finance, advances_only=True, po_nos=[p["po_no"]])["run"]
                lib.approve_run(world, run)
                runs.generate_file(db, run, finance)
                lib.settle_run(world, run, on)
                advance_payment[key] = db.execute(
                    text("select no from payment where run_no = :r"), {"r": run}
                ).scalar_one()
            except Exception as exc:  # noqa: BLE001 - recorded, and the eval reports it
                db.rollback()
                errors.append(f"advance {key}: {exc}")
            db.commit()
            continue
        if kind == "service":
            post_service_entry(db, services[key])
            db.commit()
            continue
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
            invoices._release(
                db,
                {"invoice": done.no, "reason": "accepted, as the dataset records"},
                sod.user(world.logins["Finance"]),
            )
            released.add(key)

    return SimpleNamespace(
        entered=entered,
        platform_no=platform_no,
        by_no=by_no,
        truth=truth,
        inv_lines=inv_lines,
        receipt_lines=receipt_lines,
        receipts=receipts,
        errors=errors,
        released=released,
        advance_payment=advance_payment,
        advance_rows=advance_rows,
    )
