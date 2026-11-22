"""Loading the documents still open in the old system at cutover (task 7.4): orders, receipts, service confirmations,
invoices, advances, and what was bought from each supplier this financial year.

Only in state cutover. Every row is checked first; a bad row is left out with its file, line and reason, and a document
whose order was left out goes with it. Nothing is written on a dry run; a real run writes everything in one transaction.
A document loaded before (same number) counts as `already` when it is unchanged, and is left out when it changed. The
counts and totals of what the company now holds from the files are compared with the old system's own (`control.csv`).

Each order sits on a carrier request `CUT-<order>` (an order line always names its request line). Receipts and service
confirmations count against the order line but post no stock: the stock at cutover comes from the stock import.
Invoices go through the platform's own entry and three-way match (its blocks, tolerances and due dates apply from
go-live). Advances are paid, with what later invoices already recovered; what is left is recovered by the next invoices.
"""

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas import receiving as receiving_rules
from p2p.formulas.gst import Registration
from p2p.formulas.matching import normalize_invoice_no
from p2p.formulas.money import format_qty
from p2p.formulas.orders import line_amounts
from p2p.formulas.payment import advance_amounts
from p2p.invoices import service as invoices
from p2p.masters.importer import read_table
from p2p.masters.report import ImportReport
from p2p.masters.schema import Table
from p2p.platform import audit, company_state, numbering
from p2p.platform.sod import user
from p2p.receiving import goods

D = Decimal
ZERO = D(0)
ONE_RUPEE = D("1.00")
IST = ZoneInfo("Asia/Kolkata")

# file -> its columns (sql type) and those a file may leave out; the templates are these headers
_SPEC: dict[str, tuple[tuple[tuple[str, str], ...], frozenset[str]]] = {
    "po": ((
        ("no", "text"), ("supplier_code", "text"), ("ship_to_location", "text"), ("created_at", "timestamptz"),
        ("payment_terms_code", "text"), ("status", "text"), ("buyer", "text"), ("contract_code", "text"),
        ("delivery_terms", "text"), ("freight_terms", "text"), ("taxable", "numeric"), ("cgst", "numeric"),
        ("sgst", "numeric"), ("igst", "numeric"), ("total", "numeric"),
    ), frozenset({"buyer", "contract_code", "delivery_terms", "freight_terms", "taxable", "cgst", "sgst", "igst", "total"})),
    "po_line": ((
        ("po_no", "text"), ("line_no", "integer"), ("item_code", "text"), ("qty", "numeric"), ("uom", "text"),
        ("unit_price", "numeric"), ("gst_rate", "numeric"), ("description", "text"), ("need_by", "date"),
        ("promised_date", "date"), ("cost_centre_code", "text"), ("gl_code", "text"), ("received_qty", "numeric"),
        ("invoiced_qty", "numeric"),
    ), frozenset({"description", "need_by", "promised_date", "cost_centre_code", "gl_code", "received_qty", "invoiced_qty"})),
    "goods_receipt": ((
        ("no", "text"), ("po_no", "text"), ("location_code", "text"), ("gate_entry_at", "timestamptz"),
        ("received_by", "text"), ("delivery_challan_no", "text"), ("inspection_status", "text"), ("accepted_on", "date"),
    ), frozenset({"gate_entry_at", "delivery_challan_no"})),
    "gr_line": ((
        ("gr_no", "text"), ("po_no", "text"), ("po_line_no", "integer"), ("qty_received", "numeric"),
        ("qty_accepted", "numeric"), ("qty_rejected", "numeric"), ("reject_reason", "text"),
    ), frozenset({"qty_rejected", "reject_reason"})),
    "service_entry": ((
        ("no", "text"), ("po_no", "text"), ("po_line_no", "integer"), ("period_from", "date"), ("period_to", "date"),
        ("amount_accepted", "numeric"), ("accepted_by", "text"), ("accepted_on", "date"),
    ), frozenset({"period_from", "period_to"})),
    "invoice": ((
        ("no", "text"), ("supplier_code", "text"), ("supplier_gstin", "text"), ("buyer_gstin", "text"),
        ("supplier_invoice_no", "text"), ("invoice_date", "date"), ("irn", "text"), ("received_on", "date"),
        ("taxable", "numeric"), ("cgst", "numeric"), ("sgst", "numeric"), ("igst", "numeric"), ("round_off", "numeric"),
        ("total", "numeric"), ("due_date", "date"), ("status", "text"), ("paid_amount", "numeric"),
    ), frozenset({"supplier_gstin", "buyer_gstin", "irn", "due_date", "paid_amount"})),
    "invoice_line": ((
        ("invoice_no", "text"), ("line_no", "integer"), ("po_no", "text"), ("po_line_no", "integer"), ("qty", "numeric"),
        ("unit_price", "numeric"), ("taxable", "numeric"), ("gst_rate", "numeric"), ("cgst", "numeric"),
        ("sgst", "numeric"), ("igst", "numeric"),
    ), frozenset({"cgst", "sgst", "igst"})),
    "advance": ((
        ("payment_no", "text"), ("po_no", "text"), ("paid_on", "date"), ("gross", "numeric"), ("taxable", "numeric"),
        ("tds_amount", "numeric"), ("recovered_gross", "numeric"), ("recovered_taxable", "numeric"), ("utr", "text"),
    ), frozenset({"recovered_gross", "recovered_taxable", "utr"})),
    "supplier_ytd": ((("supplier_code", "text"), ("fy", "text"), ("taxable", "numeric")), frozenset()),
    "control": ((("kind", "text"), ("count", "integer"), ("total", "numeric")), frozenset()),
}  # fmt: skip
FILES: dict[str, list[str]] = {name: [c for c, _ in cols] for name, (cols, _) in _SPEC.items()}
OPEN_ORDER = ("approved", "sent", "acknowledged", "part_received", "received")
OPEN_INVOICE = ("received", "approved", "blocked")
INSPECTED = ("not_required", "passed", "failed")
KINDS = ("po", "goods_receipt", "service_entry", "invoice", "advance", "supplier_ytd")
CONTROLLED = ("po", "invoice", "advance")

Row = dict[str, Any]


@dataclass
class Left:
    file: str
    line: int
    reason: str


@dataclass
class CutoverReport:
    dry_run: bool
    loaded: dict[str, int] = field(default_factory=lambda: dict.fromkeys(KINDS, 0))
    already: dict[str, int] = field(default_factory=lambda: dict.fromkeys(KINDS, 0))
    rejected: list[Left] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    control: dict[str, dict[str, Any]] = field(default_factory=dict)
    control_ok: bool = False
    import_id: int | None = None

    def view(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "import_id": self.import_id,
            "loaded": self.loaded,
            "already": self.already,
            "rejected": [{"file": r.file, "line": r.line, "reason": r.reason} for r in self.rejected],
            "warnings": self.warnings,
            "control": {k: {**v, "file_total": str(v["file_total"]), "loaded_total": str(v["loaded_total"])} for k, v in self.control.items()},
            "control_ok": self.control_ok,
        }  # fmt: skip


def _q2(value: Decimal) -> Decimal:
    return value.quantize(D("0.01"))


def _read(folder: Path, report: CutoverReport) -> dict[str, list[tuple[int, Row]]]:
    """Every file there is, typed; a missing file has no rows."""
    out: dict[str, list[tuple[int, Row]]] = {}
    for name, (cols, optional) in _SPEC.items():
        path = folder / f"{name}.csv"
        if not path.is_file() and not path.with_suffix(".xlsx").is_file():
            out[name] = []
            continue
        reading = ImportReport()
        out[name] = read_table(path, Table(name, name, cols, None, ()), reading, optional=optional)
        report.rejected += [Left(f"{name}.csv", p.line, p.reason) for p in reading.rejected]
    return out


class _Masters:
    """What the files' rows refer to, read once."""

    def __init__(self, db: Session) -> None:
        def table(sql: str) -> dict[str, Row]:
            return {str(r["code"]): dict(r) for r in db.execute(text(sql)).mappings()}

        self.suppliers = table("select code, status, gstin, state_code, gst_reg_type from supplier")
        self.locations = table("select code, gstin, state_code from location")
        self.terms = table("select code, advance_pct from payment_terms")
        self.items = table("select code, name, category_code, purchase_uom as uom, kind from item")
        self.centres = table("select code, department_code from cost_centre")
        self.gl = table("select code from gl_account")
        self.contracts = table("select code from contract")
        self.people = table("select code from org_user")
        kinds = table("select code, kind from item_category")
        self.service_items = {
            c
            for c, i in self.items.items()
            if (i["kind"] or kinds.get(i["category_code"] or "", {}).get("kind")) == "service"
        }


class _Company:
    """The documents the company already holds (a load before, or the platform's own)."""

    def __init__(self, db: Session) -> None:
        def keyed(sql: str) -> dict[str, Row]:
            return {str(r["k"]): dict(r) for r in db.execute(text(sql)).mappings()}

        self.pos = keyed("select no as k, supplier_code, ship_to_location, payment_terms_code, total, taxable from po")
        self.lines = {
            (str(r["po_no"]), int(r["line_no"])): dict(r)
            for r in db.execute(
                text("select po_no, line_no, item_code, qty, unit_price, received_qty from po_line")
            ).mappings()
        }
        self.receipts = keyed("select no as k, po_no from goods_receipt")
        self.services = keyed("select no as k, po_no, amount_accepted from service_entry")
        self.invoices = keyed("select no as k, supplier_code, supplier_invoice_no, invoice_date, total from invoice")
        self.dedupe = {
            (str(r[0]), str(r[1])): str(r[2])
            for r in db.execute(text("select supplier_code, dedupe_key, no from invoice where status <> 'cancelled'"))
        }
        self.invoiced: dict[tuple[str, int], Decimal] = defaultdict(D)
        for po_no, line_no, qty in db.execute(
            text(
                "select l.po_no, l.po_line_no, l.qty from invoice_line l join invoice i on i.no = l.invoice_no "
                "where i.type = 'invoice' and i.status not in ('cancelled')"
            )
        ):
            self.invoiced[(str(po_no), int(line_no))] += D(qty)
        self.advances = keyed("select payment_no as k, po_no, gross from advance")
        self.ytd = {
            (str(r[0]), str(r[1])): D(r[2])
            for r in db.execute(text("select supplier_code, fy, taxable from cutover_supplier_ytd"))
        }


def _changed(old: Row, new: Row, columns: tuple[str, ...]) -> str | None:
    """'already loaded; changed: ...' for a document loaded before that differs, else None."""
    diff = [
        f"{c} {old.get(c)} -> {new.get(c)}"
        for c in columns
        if (
            D(str(old[c])) != D(str(new[c]))
            if isinstance(new.get(c), Decimal) and old.get(c) is not None
            else str(old.get(c)) != str(new.get(c))
        )
    ]
    return f"already loaded; changed: {'; '.join(diff)}" if diff else None


@dataclass
class _Plan:
    """What passed the checks, ready to write."""

    pos: list[tuple[Row, list[Row]]] = field(default_factory=list)
    receipts: list[tuple[Row, list[Row]]] = field(default_factory=list)
    services: list[Row] = field(default_factory=list)
    invoices: list[tuple[Row, list[Row]]] = field(default_factory=list)
    advances: list[Row] = field(default_factory=list)
    ytd: list[Row] = field(default_factory=list)
    held: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))  # kind -> numbers loaded or already


class _Checker:
    """Checks every row of the files in the order the documents depend on each other."""

    def __init__(self, db: Session, f: dict[str, list[tuple[int, Row]]], report: CutoverReport) -> None:
        self.db, self.f, self.report = db, f, report
        self.m, self.c = _Masters(db), _Company(db)
        self.plan = _Plan()
        self.file_lines: dict[tuple[str, int], Row] = {(r["po_no"], r["line_no"]): r for _, r in f["po_line"]}
        self.left_pos: set[str] = set()
        self.accepted: dict[tuple[str, int], Decimal] = defaultdict(D)  # accepted so far by the files' receipts
        self.billed: dict[tuple[str, int], Decimal] = defaultdict(D)  # invoiced so far by the files' invoices
        self.price: dict[tuple[str, int], Decimal] = {}
        self.order_of: dict[str, Row] = {}

    def leave(self, file: str, line: int, reason: str) -> None:
        self.report.rejected.append(Left(f"{file}.csv", line, reason))

    def _left(self, file: str, line: int, why: "str | tuple[str, int, str]") -> None:
        """A document left out: on its own line, or on the line of the row that caused it."""
        if isinstance(why, tuple):
            self.leave(*why)
        else:
            self.leave(file, line, why)

    def run(self) -> _Plan:
        self.orders()
        self.goods_receipts()
        self.service_entries()
        self.year_to_date()
        self.invoices()
        self.advances()
        return self.plan

    # ---- orders
    def orders(self) -> None:
        lines: dict[str, list[tuple[int, Row]]] = defaultdict(list)
        for n, r in self.f["po_line"]:
            lines[r["po_no"]].append((n, r))
        for n, p in self.f["po"]:
            why = self._order(p, lines.get(p["no"], []))
            if why:
                self.left_pos.add(p["no"])
                self._left("po", n, why)

    def _order(self, p: Row, lines: list[tuple[int, Row]]) -> str | tuple[str, int, str] | None:
        no = p["no"]
        if p["status"] not in OPEN_ORDER:
            return f"{no}: status {p['status']}; an open order is approved, sent, acknowledged or received"
        for col, known, what in (
            ("supplier_code", self.m.suppliers, "supplier"), ("ship_to_location", self.m.locations, "location"),
            ("payment_terms_code", self.m.terms, "payment terms"), ("buyer", self.m.people, "buyer"),
            ("contract_code", self.m.contracts, "contract"),
        ):  # fmt: skip
            if p.get(col) and p[col] not in known:
                return f"{no}: {what} {p[col]} not found"
        if not lines:
            return f"{no}: no lines in po_line.csv"
        sup, loc = self.m.suppliers[p["supplier_code"]], self.m.locations[p["ship_to_location"]]
        if sup["status"] != "active":
            self.report.warnings.append(
                f"{no}: supplier {p['supplier_code']} is {sup['status']}; its open order is loaded"
            )
        state = sup["state_code"] or str(sup["gstin"] or "")[:2]
        total = ZERO
        for n, ln in lines:
            why = self._line(ln)
            if why:
                return ("po_line", n, f"{no} line {ln['line_no']}: {why}")
            a = line_amounts(
                ln["qty"],
                ln["unit_price"],
                ln["gst_rate"],
                state,
                loc["state_code"],
                Registration(sup["gst_reg_type"] or "regular"),
            )
            ln["_amounts"] = a
            total += a.taxable + a.cgst + a.sgst + a.igst
        if p.get("total") is not None and abs(p["total"] - total) > ONE_RUPEE:
            return f"{no}: total {p['total']:.2f} in the file, {total:.2f} by the lines"
        p["_total"], p["_lines"] = total, [ln for _, ln in lines]
        old = self.c.pos.get(no)
        if old is not None:
            changed = _changed(
                old, {**p, "total": total}, ("supplier_code", "ship_to_location", "payment_terms_code", "total")
            )
            if changed:
                return f"{no}: {changed}"
            self.report.already["po"] += 1
        else:
            self.plan.pos.append((p, p["_lines"]))
        self.plan.held["po"].add(no)
        self.order_of[no] = p
        for ln in p["_lines"]:
            self.price[(no, ln["line_no"])] = ln["unit_price"]
        return None

    def _line(self, ln: Row) -> str | None:
        for col, known, what in (
            ("item_code", self.m.items, "item"), ("cost_centre_code", self.m.centres, "cost centre"),
            ("gl_code", self.m.gl, "GL account"),
        ):  # fmt: skip
            if ln.get(col) and ln[col] not in known:
                return f"{what} {ln[col]} not found"
        if not ln.get("qty") or ln["qty"] <= 0:
            return "the quantity must be more than zero"
        if not ln.get("unit_price") or ln["unit_price"] <= 0:
            return "the price must be more than zero"
        if not ln.get("item_code") and not ln.get("description"):
            return "an item or a description is needed"
        return None

    def _ordered(self, po_no: str, line_no: int) -> Decimal | None:
        """The ordered quantity of an order line in the files or the company."""
        f = self.file_lines.get((po_no, line_no))
        if f is not None:
            return D(f["qty"])
        c = self.c.lines.get((po_no, line_no))
        return D(c["qty"]) if c else None

    def _po_gone(self, po_no: str) -> str | None:
        if po_no in self.left_pos:
            return f"{po_no} was left out, so its documents are left out too"
        if po_no not in self.plan.held["po"] and po_no not in self.c.pos:
            return f"{po_no}: not found"
        return None

    def _received_before(self, key: tuple[str, int]) -> Decimal:
        c = self.c.lines.get(key)
        return (D(c["received_qty"]) if c else ZERO) + self.accepted[key]

    # ---- receipts and service confirmations
    def goods_receipts(self) -> None:
        lines: dict[str, list[tuple[int, Row]]] = defaultdict(list)
        for n, r in self.f["gr_line"]:
            lines[r["gr_no"]].append((n, r))
        for n, g in self.f["goods_receipt"]:
            why = self._receipt(g, lines.get(g["no"], []))
            if why:
                self._left("goods_receipt", n, why)

    def _receipt(self, g: Row, lines: list[tuple[int, Row]]) -> str | tuple[str, int, str] | None:
        no = g["no"]
        if g["inspection_status"] == "pending":
            return f"{no}: the inspection is still pending: finish it in the old system first"
        if g["inspection_status"] not in INSPECTED:
            return f"{no}: inspection_status {g['inspection_status']}; one of {', '.join(INSPECTED)}"
        if g["location_code"] not in self.m.locations:
            return f"{no}: location {g['location_code']} not found"
        if not lines:
            return f"{no}: no lines in gr_line.csv"
        if no in self.c.receipts:
            if self.c.receipts[no]["po_no"] != g["po_no"]:
                return f"{no}: already loaded; changed: po_no {self.c.receipts[no]['po_no']} -> {g['po_no']}"
            self.report.already["goods_receipt"] += 1
            return None
        counted: list[tuple[tuple[str, int], Decimal]] = []
        for n, ln in lines:
            key = (ln["po_no"], ln["po_line_no"])
            ordered = self._ordered(*key)
            if ordered is None or ln["po_no"] != g["po_no"]:
                return ("gr_line", n, f"{no}: {ln['po_no']} line {ln['po_line_no']}: not found")
            acc, rej = ln.get("qty_accepted") or ZERO, ln.get("qty_rejected") or ZERO
            if acc + rej != ln["qty_received"]:
                return (
                    "gr_line",
                    n,
                    f"{no}: accepted {format_qty(acc)} and rejected {format_qty(rej)} are not the {format_qty(ln['qty_received'])} received",
                )
            before = self._received_before(key)
            if before + acc > ordered:
                tail = f" ({before:.3f} received before)" if before else ""
                return ("gr_line", n, f"{no}: receives {acc:.3f} of {ordered:.3f} ordered{tail}")
            counted.append((key, acc))
        for key, acc in counted:
            self.accepted[key] += acc
        gone = self._po_gone(g["po_no"])
        if gone:
            return f"{no}: {gone}"
        self.plan.receipts.append((g, [ln for _, ln in lines]))
        self.plan.held["goods_receipt"].add(no)
        return None

    def service_entries(self) -> None:
        for n, s in self.f["service_entry"]:
            no, key = s["no"], (s["po_no"], s["po_line_no"])
            if no in self.c.services:
                self.report.already["service_entry"] += 1
                continue
            ordered, price = self._ordered(*key), self._unit_price(key)
            if ordered is None or price is None:
                self.leave("service_entry", n, f"{no}: {s['po_no']} line {s['po_line_no']}: not found")
                continue
            if not s.get("amount_accepted") or s["amount_accepted"] <= 0:
                self.leave("service_entry", n, f"{no}: the amount confirmed must be more than zero")
                continue
            qty = (s["amount_accepted"] / price).quantize(D("0.001"))
            before = self._received_before(key)
            if before + qty > ordered:
                self.leave("service_entry", n, f"{no}: confirms {qty:.3f} of {ordered:.3f} ordered")
                continue
            self.accepted[key] += qty
            gone = self._po_gone(s["po_no"])
            if gone:
                self.leave("service_entry", n, f"{no}: {gone}")
                continue
            self.plan.services.append({**s, "_qty": qty})
            self.plan.held["service_entry"].add(no)

    def _unit_price(self, key: tuple[str, int]) -> Decimal | None:
        if key in self.price:
            return self.price[key]
        c = self.c.lines.get(key)
        return D(c["unit_price"]) if c else None

    # ---- year to date, invoices, advances
    def year_to_date(self) -> None:
        for n, y in self.f["supplier_ytd"]:
            key = (y["supplier_code"], y["fy"])
            if y["supplier_code"] not in self.m.suppliers:
                self.leave("supplier_ytd", n, f"supplier {y['supplier_code']} not found")
            elif len(y["fy"]) != 7 or y["fy"][4] != "-" or not (y["fy"][:4] + y["fy"][5:]).isdigit():
                self.leave(
                    "supplier_ytd", n, f"{y['supplier_code']}: fy {y['fy']} is not a financial year like 2026-27"
                )
            elif key in self.c.ytd and self.c.ytd[key] != y["taxable"]:
                self.leave(
                    "supplier_ytd",
                    n,
                    f"{y['supplier_code']} {y['fy']}: already loaded; changed: taxable {self.c.ytd[key]} -> {y['taxable']}",
                )
            elif key in self.c.ytd:
                self.report.already["supplier_ytd"] += 1
            else:
                self.plan.ytd.append(y)

    def invoices(self) -> None:
        lines: dict[str, list[tuple[int, Row]]] = defaultdict(list)
        for n, r in self.f["invoice_line"]:
            lines[r["invoice_no"]].append((n, r))
        seen: dict[tuple[str, str], str] = {}
        for n, i in self.f["invoice"]:
            why = self._invoice(i, lines.get(i["no"], []), seen)
            if why:
                self._left("invoice", n, why)

    def _invoice(
        self, i: Row, lines: list[tuple[int, Row]], seen: dict[tuple[str, str], str]
    ) -> str | tuple[str, int, str] | None:
        no = i["no"]
        if i["status"] in ("paid", "cancelled"):
            return f"{no}: {i['status']} in the old system: not an open document"
        if i["status"] not in OPEN_INVOICE:
            return f"{no}: status {i['status']}; an open invoice is received, approved or blocked"
        if i.get("paid_amount") and i["paid_amount"] > 0:
            return f"{no}: part paid ({i['paid_amount']:.2f}): settle it in the old system first"
        if i["supplier_code"] not in self.m.suppliers:
            return f"{no}: supplier {i['supplier_code']} not found"
        if no in self.c.invoices:
            changed = _changed(
                self.c.invoices[no], i, ("supplier_code", "supplier_invoice_no", "invoice_date", "total")
            )
            if changed:
                return f"{no}: {changed}"
            self.report.already["invoice"] += 1
            self.plan.held["invoice"].add(no)
            return None
        key = (i["supplier_code"], normalize_invoice_no(i["supplier_invoice_no"]))
        twin = seen.get(key) or self.c.dedupe.get(key)
        if twin:
            return f"{no}: duplicate of {twin} ({i['supplier_code']} {i['supplier_invoice_no']})"
        if not lines:
            return f"{no}: no lines in invoice_line.csv"
        billed: list[tuple[tuple[str, int], Decimal]] = []
        taxable = tax = ZERO
        for n, ln in lines:
            k = (ln["po_no"], ln["po_line_no"])
            if self._ordered(*k) is None:
                return ("invoice_line", n, f"{no}: {ln['po_no']} line {ln['po_line_no']}: not found")
            gone = self._po_gone(ln["po_no"])
            if gone:
                return ("invoice_line", n, f"{no}: {gone}")
            supplier = (self.order_of.get(ln["po_no"]) or self.c.pos.get(ln["po_no"]) or {}).get("supplier_code")
            if supplier != i["supplier_code"]:
                return (
                    "invoice_line",
                    n,
                    f"{no}: {ln['po_no']} is an order of {supplier}, not of {i['supplier_code']}",
                )
            accepted = self._received_before(k)
            invoiced = self.c.invoiced[k] + self.billed[k] + ln["qty"]
            if invoiced > accepted:
                return (
                    "invoice_line",
                    n,
                    f"{no}: {ln['po_no']} line {ln['po_line_no']}: invoices {invoiced:.3f} of {accepted:.3f} accepted",
                )
            billed.append((k, ln["qty"]))
            taxable += ln["taxable"]
            tax += (ln.get("cgst") or ZERO) + (ln.get("sgst") or ZERO) + (ln.get("igst") or ZERO)
        head_tax = (i.get("cgst") or ZERO) + (i.get("sgst") or ZERO) + (i.get("igst") or ZERO)
        if abs(i["taxable"] - taxable) > ONE_RUPEE or abs(head_tax - tax) > ONE_RUPEE:
            return f"{no}: taxable {i['taxable']:.2f} and GST {head_tax:.2f} in the file, {taxable:.2f} and {tax:.2f} by the lines"
        for k, q in billed:
            self.billed[k] += q
        seen[key] = no
        self.plan.invoices.append((i, [ln for _, ln in lines]))
        self.plan.held["invoice"].add(no)
        return None

    def advances(self) -> None:
        given: dict[str, Decimal] = defaultdict(D)
        for po_no, a in ((str(r["po_no"]), r) for r in self.c.advances.values()):
            given[po_no] += D(a["gross"])
        for n, a in self.f["advance"]:
            why = self._advance(a, given)
            if why:
                self.leave("advance", n, why)

    def _advance(self, a: Row, given: dict[str, Decimal]) -> str | None:
        no, po_no = a["payment_no"], a["po_no"]
        if no in self.c.advances:
            changed = _changed(self.c.advances[no], a, ("po_no", "gross"))
            if changed:
                return f"{no}: {changed}"
            self.report.already["advance"] += 1
            self.plan.held["advance"].add(no)
            return None
        gone = self._po_gone(po_no)
        if gone:
            return f"{no}: {gone}"
        order = self.order_of.get(po_no) or self.c.pos[po_no]
        pct = (
            D(self.m.terms[order["payment_terms_code"]]["advance_pct"] or 0)
            if order.get("payment_terms_code")
            else ZERO
        )
        if pct <= 0:
            return f"{no}: the payment terms of {po_no} ({order.get('payment_terms_code')}) have no advance"
        total = order.get("_total") if "_total" in order else D(order["total"])
        taxable = (
            sum((x["_amounts"].taxable for x in order["_lines"]), ZERO) if "_lines" in order else D(order["taxable"])
        )
        most, _ = advance_amounts(D(str(total)), taxable, pct)
        if given[po_no] + a["gross"] > most:
            return f"{no}: {a['gross']:.2f} is above the advance of {most:.2f} ({pct.normalize():f}% of {po_no})"
        recovered = a.get("recovered_gross") or ZERO
        if recovered > a["gross"]:
            return f"{no}: recovered {recovered:.2f} is more than the advance {a['gross']:.2f}"
        given[po_no] += a["gross"]
        a["_taxable_total"] = taxable
        self.plan.advances.append(a)
        self.plan.held["advance"].add(no)
        return None


# ---- writing ----------------------------------------------------------------------------------------------------------


class _Writer:
    def __init__(self, db: Session, user_id: int, m: _Masters) -> None:
        self.db, self.user_id, self.m = db, user_id, m
        self.actor = user(user_id)

    def order(self, p: Row, lines: list[Row]) -> None:
        db, no = self.db, p["no"]
        loc, sup = self.m.locations[p["ship_to_location"]], self.m.suppliers[p["supplier_code"]]
        first = self.m.centres.get(lines[0].get("cost_centre_code") or "", {})
        kind = "service" if all(ln.get("item_code") in self.m.service_items for ln in lines) else "goods"
        a = [ln["_amounts"] for ln in lines]
        taxable = sum((x.taxable for x in a), ZERO)
        cgst, sgst, igst = (sum((getattr(x, t) for x in a), ZERO) for t in ("cgst", "sgst", "igst"))
        req = f"CUT-{no}"
        db.execute(
            text(
                "insert into request (no, department_code, ship_to_location, type, created_at, channel, source_ref, "
                "justification, status, total_value, decided_at) values (:n, :d, :l, :t, :c, 'cutover', :p, :j, 'approved', :v, :c)"
            ),
            {"n": req, "d": first.get("department_code"), "l": p["ship_to_location"], "t": kind, "c": p["created_at"],
             "p": no, "j": "open in the old system at cutover", "v": taxable},
        )  # fmt: skip
        db.execute(
            text(
                "insert into po (no, po_type, supplier_code, supplier_gstin, buyer_gstin, ship_to_location, buyer, request_no, "
                "contract_code, created_at, approved_at, sent_at, acknowledged_at, payment_terms_code, delivery_terms, "
                "taxable, cgst, sgst, igst, total, status, version) values (:n, :pt, :s, :sg, :bg, :l, :b, :r, :k, :c, :c, :c, "
                ":ack, :terms, :dt, :tx, :cg, :sgs, :ig, :tot, :st, 1)"
            ),
            {
                "n": no, "pt": "contract_release" if p.get("contract_code") else "standard", "s": p["supplier_code"],
                "sg": sup["gstin"], "bg": loc["gstin"], "l": p["ship_to_location"], "b": p.get("buyer"), "r": req,
                "k": p.get("contract_code"), "c": p["created_at"],
                "ack": p["created_at"] if p["status"] in ("acknowledged", "part_received", "received") else None,
                "terms": p["payment_terms_code"], "dt": p.get("delivery_terms"), "tx": taxable, "cg": cgst, "sgs": sgst,
                "ig": igst, "tot": taxable + cgst + sgst + igst,
                "st": "acknowledged" if p["status"] in ("part_received", "received") else p["status"],
            },
        )  # fmt: skip
        for ln in lines:
            item = self.m.items.get(ln.get("item_code") or "", {})
            am = ln["_amounts"]
            db.execute(
                text(
                    "insert into request_line (request_no, line_no, item_code, free_text, qty, uom, est_price, est_source, "
                    "need_by, delivery_location, cost_centre_code, gl_code, category_code, status) values (:r, :l, :i, :f, :q, "
                    ":u, :p, 'cutover', :nb, :dl, :cc, :gl, :cat, 'ordered')"
                ),
                {"r": req, "l": ln["line_no"], "i": ln.get("item_code"), "f": None if ln.get("item_code") else ln.get("description"),
                 "q": ln["qty"], "u": ln.get("uom") or item.get("uom"), "p": ln["unit_price"], "nb": ln.get("need_by"),
                 "dl": p["ship_to_location"], "cc": ln.get("cost_centre_code"), "gl": ln.get("gl_code"),
                 "cat": item.get("category_code")},
            )  # fmt: skip
            db.execute(
                text(
                    "insert into po_line (po_no, line_no, request_no, request_line_no, item_code, description, qty, uom, "
                    "unit_price, est_price, gst_rate, taxable, cgst, sgst, igst, need_by, promised_date, received_qty, "
                    "invoiced_qty, gl_code, source, status) values (:po, :l, :r, :l, :i, :d, :q, :u, :p, :p, :g, :tx, :cg, :sg, "
                    ":ig, :nb, :pd, 0, 0, :gl, 'cutover', 'open')"
                ),
                {"po": no, "l": ln["line_no"], "r": req, "i": ln.get("item_code"), "d": ln.get("description") or item.get("name"),
                 "q": ln["qty"], "u": ln.get("uom") or item.get("uom"), "p": ln["unit_price"], "g": ln["gst_rate"],
                 "tx": am.taxable, "cg": am.cgst, "sg": am.sgst, "ig": am.igst, "nb": ln.get("need_by"),
                 "pd": ln.get("promised_date"), "gl": ln.get("gl_code")},
            )  # fmt: skip
        numbering.keep_above(db, "po", no)
        audit.record(db, self.actor, "cutover.po", "po", no, after={"supplier": p["supplier_code"], "taxable": taxable, "total": taxable + cgst + sgst + igst, "lines": len(lines)})  # fmt: skip

    def _count(self, po_no: str, line_no: int, qty: Decimal) -> None:
        """What was accepted counts against the order line; no stock moves (the stock import holds it)."""
        row = self.db.execute(
            text("select qty, received_qty from po_line where po_no = :p and line_no = :l"), {"p": po_no, "l": line_no}
        ).one()
        total = D(row[1]) + qty
        self.db.execute(
            text("update po_line set received_qty = :t, status = :s where po_no = :p and line_no = :l"),
            {"t": total, "s": receiving_rules.line_status(D(row[0]), total), "p": po_no, "l": line_no},
        )

    def receipt(self, g: Row, lines: list[Row]) -> None:
        db = self.db
        db.execute(
            text(
                "insert into goods_receipt (no, po_no, location_code, gate_entry_at, received_by, delivery_challan_no, "
                "inspection_status, accepted_on, posted_by) values (:n, :p, :l, :g, :r, :dc, :i, :a, 'cutover')"
            ),
            {"n": g["no"], "p": g["po_no"], "l": g["location_code"], "g": g["gate_entry_at"] or datetime.combine(g["accepted_on"], time(10), IST),
             "r": g["received_by"], "dc": g.get("delivery_challan_no"), "i": g["inspection_status"], "a": g["accepted_on"]},
        )  # fmt: skip
        for ln in lines:
            db.execute(
                text(
                    "insert into gr_line (gr_no, po_no, po_line_no, qty_received, qty_accepted, qty_rejected, reject_reason) "
                    "values (:g, :p, :l, :r, :a, :x, :why)"
                ),
                {"g": g["no"], "p": ln["po_no"], "l": ln["po_line_no"], "r": ln["qty_received"], "a": ln.get("qty_accepted") or ZERO,
                 "x": ln.get("qty_rejected") or ZERO, "why": ln.get("reject_reason")},
            )  # fmt: skip
            self._count(ln["po_no"], ln["po_line_no"], ln.get("qty_accepted") or ZERO)
        numbering.keep_above(db, "goods_receipt", g["no"])
        audit.record(db, self.actor, "cutover.receipt", "goods_receipt", g["no"], after={"po": g["po_no"], "accepted": sum((ln.get("qty_accepted") or ZERO for ln in lines), ZERO)})  # fmt: skip

    def service(self, s: Row) -> None:
        self.db.execute(
            text(
                "insert into service_entry (no, po_no, po_line_no, period_from, period_to, basis, value, qty_accepted, "
                "amount_accepted, accepted_by, accepted_on, via, posted_by) values (:n, :p, :l, :f, :t, 'amount', :a, :q, :a, "
                ":by, :on, 'cutover', 'cutover')"
            ),
            {"n": s["no"], "p": s["po_no"], "l": s["po_line_no"], "f": s.get("period_from"), "t": s.get("period_to"),
             "a": s["amount_accepted"], "q": s["_qty"], "by": s["accepted_by"], "on": s["accepted_on"]},
        )  # fmt: skip
        self._count(s["po_no"], s["po_line_no"], s["_qty"])
        numbering.keep_above(self.db, "service_receipt", s["no"])
        audit.record(self.db, self.actor, "cutover.service_entry", "service_entry", s["no"], after={"po": s["po_no"], "amount": s["amount_accepted"]})  # fmt: skip

    def invoice(self, i: Row, lines: list[Row], report: CutoverReport) -> None:
        data = invoices.InvoiceIn(
            no=i["no"], supplier_code=i["supplier_code"], supplier_invoice_no=i["supplier_invoice_no"],
            invoice_date=i["invoice_date"], supplier_gstin=i.get("supplier_gstin"), buyer_gstin=i.get("buyer_gstin"),
            irn=i.get("irn"), channel="cutover", received_at=datetime.combine(i["received_on"], time(12), IST),
            taxable=i["taxable"], cgst=i.get("cgst") or ZERO, sgst=i.get("sgst") or ZERO, igst=i.get("igst") or ZERO,
            round_off=i.get("round_off"), total=i["total"],
            lines=[
                invoices.InvLine(ln["po_no"], ln["po_line_no"], ln["qty"], ln["unit_price"], taxable=ln["taxable"],
                                 gst_rate=ln["gst_rate"], cgst=ln.get("cgst") or ZERO, sgst=ln.get("sgst") or ZERO,
                                 igst=ln.get("igst") or ZERO)
                for ln in lines
            ],
        )  # fmt: skip
        entered = invoices.enter_invoice(self.db, data, self.user_id, as_of=i["received_on"], commit=False)
        numbering.keep_above(self.db, "invoice_booking", i["no"])
        due = self.db.execute(text("select due_date from invoice where no = :n"), {"n": i["no"]}).scalar()
        if i.get("due_date") and due and due != i["due_date"]:
            report.warnings.append(f"{i['no']}: due {due} by the platform's terms, {i['due_date']} in the old system")
        if entered.status != i["status"] and "blocked" in (entered.status, i["status"]):
            report.warnings.append(
                f"{i['no']}: {i['status']} in the old system, {entered.status} by the platform's match"
            )
        audit.record(self.db, self.actor, "cutover.invoice", "invoice", i["no"], after={"supplier": i["supplier_code"], "total": i["total"], "status": entered.status, "legacy_status": i["status"]})  # fmt: skip

    def advance(self, a: Row, run_no: str) -> None:
        db = self.db
        order = db.execute(text("select supplier_code from po where no = :p"), {"p": a["po_no"]}).scalar_one()
        tds = a.get("tds_amount") or ZERO
        db.execute(
            text(
                "insert into payment (no, run_no, type, supplier_code, po_no, amount, tds_amount, msme_interest, mode, "
                "reference, utr, paid_on) values (:n, :r, 'advance', :s, :p, :a, :t, 0, 'neft', :p, :u, :d)"
            ),
            {"n": a["payment_no"], "r": run_no, "s": order, "p": a["po_no"], "a": a["gross"] - tds, "t": tds, "u": a.get("utr"), "d": a["paid_on"]},
        )  # fmt: skip
        db.execute(
            text(
                "insert into advance (payment_no, po_no, gross, taxable, used_gross, used_taxable, paid_on) "
                "values (:n, :p, :g, :t, :ug, :ut, :d)"
            ),
            {"n": a["payment_no"], "p": a["po_no"], "g": a["gross"], "t": a["taxable"], "ug": a.get("recovered_gross") or ZERO,
             "ut": a.get("recovered_taxable") or ZERO, "d": a["paid_on"]},
        )  # fmt: skip
        numbering.keep_above(db, "payment", a["payment_no"])
        audit.record(db, self.actor, "cutover.advance", "advance", a["payment_no"], after={"po": a["po_no"], "gross": a["gross"], "recovered": a.get("recovered_gross") or ZERO})  # fmt: skip

    def run_for(self, advances: list[Row]) -> str:
        """The one paid run the old system's advances sit in."""
        day = max(a["paid_on"] for a in advances)
        no = f"CUT-{day:%Y%m%d}"
        total = sum((a["gross"] - (a.get("tds_amount") or ZERO) for a in advances), ZERO)
        self.db.execute(
            text(
                "insert into payment_run (no, run_date, proposed_by, approved_by, status, total) values (:n, :d, :u, :u, 'paid', :t) "
                "on conflict (no) do update set total = payment_run.total + :t"
            ),
            {"n": no, "d": day, "u": str(self.user_id), "t": total},
        )
        return no

    def ytd(self, y: Row) -> None:
        self.db.execute(
            text("insert into cutover_supplier_ytd (supplier_code, fy, taxable) values (:s, :f, :t)"),
            {"s": y["supplier_code"], "f": y["fy"], "t": y["taxable"]},
        )


def _control(f: dict[str, list[tuple[int, Row]]], held: dict[str, set[str]], report: CutoverReport) -> None:
    """The files' documents now in the company against the old system's own counts and totals."""
    totals = {
        "po": {r["no"]: r.get("_total", r.get("total") or ZERO) for _, r in f["po"]},
        "invoice": {r["no"]: r["total"] for _, r in f["invoice"]},
        "advance": {r["payment_no"]: r["gross"] - (r.get("recovered_gross") or ZERO) for _, r in f["advance"]},
    }
    given = {r["kind"]: r for _, r in f["control"]}
    for kind in CONTROLLED:
        mine = [no for no in totals[kind] if no in held[kind]]
        loaded_total = _q2(sum((D(totals[kind][no]) for no in mine), ZERO))
        c = given.get(kind)
        file_count, file_total = (int(c["count"]), _q2(D(c["total"]))) if c else (0, ZERO)
        report.control[kind] = {
            "file_count": file_count, "file_total": file_total, "loaded_count": len(mine), "loaded_total": loaded_total,
            "ok": c is not None and file_count == len(mine) and file_total == loaded_total,
        }  # fmt: skip
    report.control_ok = bool(given) and all(v["ok"] for v in report.control.values())


def import_cutover(db: Session, folder: Path, *, user_id: int, dry_run: bool) -> CutoverReport:
    """Check, and unless a dry run load, the open documents of the old system in `folder`."""
    company_state.require_state(db, "cutover")
    report = CutoverReport(dry_run=dry_run)
    f = _read(folder, report)
    checker = _Checker(db, f, report)
    plan = checker.run()
    _control(f, plan.held, report)
    write = _Writer(db, user_id, checker.m)
    try:
        for y in plan.ytd:
            write.ytd(y)
        for p, lines in plan.pos:
            write.order(p, lines)
        for g, lines in plan.receipts:
            write.receipt(g, lines)
        for s in plan.services:
            write.service(s)
        for po_no in sorted(
            {p["no"] for p, _ in plan.pos}
            | {g["po_no"] for g, _ in plan.receipts}
            | {s["po_no"] for s in plan.services}
        ):
            goods.refresh_order(db, po_no)
        for i, lines in plan.invoices:
            write.invoice(i, lines, report)
        if plan.advances:
            run_no = write.run_for(plan.advances)
            for a in plan.advances:
                write.advance(a, run_no)
        report.loaded = {
            "po": len(plan.pos), "goods_receipt": len(plan.receipts), "service_entry": len(plan.services),
            "invoice": len(plan.invoices),
            "advance": len(plan.advances), "supplier_ytd": len(plan.ytd),
        }  # fmt: skip
        if dry_run:
            db.rollback()
            return report
        report.import_id = int(
            db.execute(
                text(
                    "insert into cutover_import (imported_by, files, dry_run, loaded, already, rejected, control, control_ok) "
                    "values (:u, cast(:f as jsonb), false, cast(:l as jsonb), cast(:a as jsonb), :r, cast(:c as jsonb), :ok) returning id"
                ),
                {
                    "u": user_id, "f": json.dumps({k: len(v) for k, v in f.items() if v}),
                    "l": json.dumps(report.loaded), "a": json.dumps(report.already), "r": len(report.rejected),
                    "c": json.dumps(report.view()["control"]), "ok": report.control_ok,
                },
            ).scalar_one()
        )  # fmt: skip
        audit.record(
            db, user(user_id), "cutover.import", "cutover_import", report.import_id,
            after={"loaded": report.loaded, "already": report.already, "rejected": len(report.rejected), "control": report.view()["control"], "control_ok": report.control_ok},
        )  # fmt: skip
        db.commit()
    except Exception:
        db.rollback()
        raise
    return report
