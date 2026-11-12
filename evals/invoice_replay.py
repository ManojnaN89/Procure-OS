"""Enter a dataset's invoices into a company in the order they arrived, with the receipts that had happened by then.

The orders and receipts come from the dataset's history; each invoice is entered through the platform's own intake and
match. Used by the evals that need real blocked invoices (E25). It is the same replay E19 does, kept here so other evals
can reuse it without running E19."""

from collections import defaultdict
from datetime import date, datetime
from typing import Any

import lib
from lib import D, rows
from replay import post_service_entry, service_events
from sqlalchemy import text


def _columns(db: Any, table: str) -> set[str]:
    return {
        r[0]
        for r in db.execute(
            text("select column_name from information_schema.columns where table_name = :t"), {"t": table}
        )
    }


def _insert(db: Any, table: str, row: dict[str, Any], extra: dict[str, Any] | None = None) -> None:
    have = _columns(db, table)
    values = {k: (v if v != "" else None) for k, v in row.items() if k in have}
    values.update(extra or {})
    db.execute(text(f"insert into {table} ({', '.join(values)}) values ({', '.join(':' + k for k in values)})"), values)  # noqa: S608


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value)


def replay(world: Any, *, limit_invoices: int | None = None) -> dict[str, Any]:
    """Orders for services are matched against the dataset's service entries (the requester's confirmations)."""
    from p2p.invoices import service as invoices

    db, ap = world.db, world.logins["AP"]
    history = lambda name: rows("history", name)  # noqa: E731
    for r in history("po"):
        _insert(db, "po", r)
    for r in history("po_line"):
        _insert(
            db, "po_line", r,
            {"received_qty": 0, "invoiced_qty": 0, "source": "award", "request_no": r["po_no"], "request_line_no": int(r["line_no"]),
             "taxable": (D(r["qty"]) * D(r["unit_price"])).quantize(D("0.01"))},
        )  # fmt: skip
    for r in history("po_charge"):
        _insert(db, "po_charge", r)
    db.commit()

    receipts = {r["no"]: r for r in history("goods_receipt")}
    receipt_lines: dict[str, list[dict]] = defaultdict(list)
    for r in history("gr_line"):
        receipt_lines[r["gr_no"]].append(r)
    inv_lines: dict[str, list[dict]] = defaultdict(list)
    for r in history("invoice_line"):
        inv_lines[r["invoice_no"]].append(r)
    all_invoices = history("invoice")
    if limit_invoices:
        all_invoices = all_invoices[:limit_invoices]
    inv_charges: dict[str, list[dict]] = defaultdict(list)
    for r in history("invoice_charge"):
        inv_charges[r["invoice_no"]].append(r)
    truth = {r["invoice_no"]: r for r in rows("truth", "truth_match_expected")}

    receipt_for = {
        (g["po_no"], g["supplier_invoice_ref"]): _when(g["gate_entry_at"])
        for g in receipts.values()
        if g["supplier_invoice_ref"]
    }
    events = [(_when(r["gate_entry_at"]), 0, "receipt", r["no"]) for r in receipts.values()]
    for r in all_invoices:
        first_po = inv_lines[r["no"]][0]["po_no"] if inv_lines[r["no"]] else ""
        arrived = max(
            _when(r["received_at"]),
            receipt_for.get((first_po, r["supplier_invoice_no"]), _when(r["received_at"])),
        )
        events.append((arrived, 1, "invoice", r["no"]))
    services = {r["no"]: r for r in history("service_receipt")}
    events += service_events(
        list(services.values()),
        {r["no"] for r in history("po")},
        events[0][0].tzinfo if events else None,
    )
    events.sort(key=lambda e: (e[0], e[1], e[3]))
    by_no = {r["no"]: r for r in all_invoices}

    platform_no: dict[str, str] = {}
    entered: dict[str, Any] = {}
    errors: list[str] = []
    for _, _, kind, key in events:
        if kind == "service":
            post_service_entry(db, services[key])
            db.commit()
            continue
        if kind == "receipt":
            _insert(db, "goods_receipt", receipts[key], {"posted_by": "import"})
            for ln in receipt_lines[key]:
                _insert(db, "gr_line", ln)
                db.execute(
                    text("update po_line set received_qty = received_qty + :q where po_no = :p and line_no = :l"),
                    {"q": ln["qty_accepted"], "p": ln["po_no"], "l": ln["po_line_no"]},
                )
            db.commit()
            continue
        r = by_no[key]
        data = invoices.InvoiceIn(
            supplier_code=r["supplier_code"], supplier_invoice_no=r["supplier_invoice_no"], invoice_date=date.fromisoformat(r["invoice_date"]),
            type=r["type"], supplier_gstin=r["supplier_gstin"] or None, buyer_gstin=r["buyer_gstin"] or None,
            place_of_supply=r["place_of_supply"] or None, irn=r["irn"] or None, ack_no=r["ack_no"] or None,
            ack_date=date.fromisoformat(r["ack_date"]) if r["ack_date"] else None, eway_bill_no=r["eway_bill_no"] or None,
            reverse_charge=r["reverse_charge"] == "true", channel=r["channel"], received_at=_when(r["received_at"]),
            taxable=D(r["taxable"]), cgst=D(r["cgst"]), sgst=D(r["sgst"]), igst=D(r["igst"]), cess=D(r["cess"]),
            round_off=D(r["round_off"]), total=D(r["total"]),
            ref_invoice_no=platform_no.get(r["ref_invoice_no"]) if r["ref_invoice_no"] else None,
            lines=[
                invoices.InvLine(
                    po_no=ln["po_no"], po_line_no=int(ln["po_line_no"]), qty=D(ln["qty"]), unit_price=D(ln["unit_price"]),
                    discount=D(ln["discount"] or "0"), taxable=D(ln["taxable"]), gst_rate=D(ln["gst_rate"] or "0"),
                    cgst=D(ln["cgst"]), sgst=D(ln["sgst"]), igst=D(ln["igst"]), description=ln["description"],
                    hsn_sac=ln["hsn_sac"], uom=ln["uom"],
                )
                for ln in inv_lines[key]
            ],
            charges=[invoices.InvCharge(c["type"], D(c["amount"]), D(c["gst_rate"] or "0"), c["sac"]) for c in inv_charges[key]],
        )  # fmt: skip
        try:
            done = invoices.enter_invoice(db, data, ap, as_of=date.fromisoformat(r["received_on"]))
        except invoices.InvoiceError as exc:
            db.rollback()
            errors.append(f"{key}: {exc}")
            continue
        platform_no[key] = done.no
        entered[key] = done
    db.commit()
    return {
        "entered": entered,
        "platform_no": platform_no,
        "by_no": by_no,
        "truth": truth,
        "errors": errors,
        "lib": lib,
    }
