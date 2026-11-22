"""The open documents a company moves to the platform at go-live (block 0d, task 7.4): `cutover/`, `cutover-bad/` and
`truth/truth_cutover.csv`, derived from the history at its cutoff plus a few documents planted in the profile.

A purchase order is open at the cutoff when a line is not fully received or not fully invoiced, or an invoice on it is
unpaid (approved, blocked or received). Its receipts, service confirmations and unpaid invoices go with it. What an
invoice paid in the old system already settled stays there: a line is carried with its open quantity (ordered less
invoiced on paid invoices), and the receipts that paid invoices cover are left out, oldest first. A line nothing is left
open on is not carried. The files keep the columns of history/ so a legacy extract maps one to one; `control.csv` holds
the old system's own totals and `supplier_ytd.csv` what was bought from each supplier this financial year (for tax at
source). `cutover-bad/` is the good files plus planted rows the import must leave out, each named in the truth file.
"""

from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from common import D, money, q2, qty_s
from master_files import read, write_csv

ZERO = Decimal(0)
OPEN_INVOICE = ("approved", "blocked", "received")
OPEN_ORDER = ("approved", "sent", "acknowledged", "part_received")

COLUMNS: dict[str, list[str]] = {
    "po": [
        "no", "supplier_code", "ship_to_location", "created_at", "payment_terms_code", "status", "buyer",
        "contract_code", "delivery_terms", "freight_terms", "taxable", "cgst", "sgst", "igst", "total",
    ],
    "po_line": [
        "po_no", "line_no", "item_code", "qty", "uom", "unit_price", "gst_rate", "description", "need_by",
        "promised_date", "cost_centre_code", "gl_code", "received_qty", "invoiced_qty",
    ],
    "goods_receipt": [
        "no", "po_no", "location_code", "gate_entry_at", "received_by", "delivery_challan_no", "inspection_status",
        "accepted_on",
    ],
    "gr_line": ["gr_no", "po_no", "po_line_no", "qty_received", "qty_accepted", "qty_rejected", "reject_reason"],
    "service_entry": [
        "no", "po_no", "po_line_no", "period_from", "period_to", "amount_accepted", "accepted_by", "accepted_on",
    ],
    "invoice": [
        "no", "supplier_code", "supplier_gstin", "buyer_gstin", "supplier_invoice_no", "invoice_date", "irn",
        "received_on", "taxable", "cgst", "sgst", "igst", "round_off", "total", "due_date", "status", "paid_amount",
    ],
    "invoice_line": [
        "invoice_no", "line_no", "po_no", "po_line_no", "qty", "unit_price", "taxable", "gst_rate", "cgst", "sgst",
        "igst",
    ],
    "advance": [
        "payment_no", "po_no", "paid_on", "gross", "taxable", "tds_amount", "recovered_gross", "recovered_taxable",
        "utr",
    ],
    "supplier_ytd": ["supplier_code", "fy", "taxable"],
    "control": ["kind", "count", "total"],
}  # fmt: skip
TRUTH = ["company", "kind", "key", "expected", "reason_contains", "amount"]


def _gst(taxable: Decimal, rate: Decimal, same_state: bool) -> tuple[Decimal, Decimal, Decimal]:
    if same_state:
        half = q2(taxable * rate / 200)
        return half, half, ZERO
    return ZERO, ZERO, q2(taxable * rate / 100)


class Cutover:
    """The open documents of one dataset at its cutoff, and the planted ones of the profile's `cutover:` block."""

    def __init__(self, out: Path, plan: dict[str, Any], company: str) -> None:
        self.out, self.plan, self.company = out, plan, company
        h = out / "history"
        self.h = {n: read(h / f"{n}.csv") for n in (
            "po", "po_line", "goods_receipt", "gr_line", "service_receipt", "invoice", "invoice_line",
        )}  # fmt: skip
        m = out / "master"
        self.state = {s["code"]: s["state_code"] for s in read(m / "supplier.csv")}
        self.loc_state = {loc["code"]: loc["state_code"] for loc in read(m / "location.csv")}
        self.files: dict[str, list[dict[str, str]]] = {k: [] for k in COLUMNS}

    # ---- the open documents of the history
    def build(self) -> dict[str, list[dict[str, str]]]:
        inv = {i["no"]: i for i in self.h["invoice"]}
        lines_of: dict[str, list[dict[str, str]]] = defaultdict(list)
        for ln in self.h["invoice_line"]:
            lines_of[ln["invoice_no"]].append(ln)
        unpaid = [n for n, i in inv.items() if i["status"] in OPEN_INVOICE]
        open_pos = {ln["po_no"] for n in unpaid for ln in lines_of[n]}
        for p in self.h["po"]:
            if p["status"] in OPEN_ORDER:
                open_pos.add(p["no"])
        paid_qty: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
        for n, i in inv.items():
            if i["status"] == "paid" and i["type"] == "invoice":
                for ln in lines_of[n]:
                    paid_qty[(ln["po_no"], ln["po_line_no"])] += D(ln["qty"])
        terms = self.plan.get("advance_terms") or {}
        for p in sorted(self.h["po"], key=lambda r: r["no"]):
            if p["no"] in open_pos:
                self._order(p, paid_qty, terms.get(p["no"]))
        for n in sorted(unpaid):
            self._invoice(inv[n], lines_of[n])
        for plant in self.plan.get("received_not_invoiced") or []:
            self._planted_receipt(plant)
        for adv in self.plan.get("advances") or []:
            self._advance(adv)
        self._ytd(inv)
        self._control()
        return self.files

    def _order(self, p: dict[str, str], paid: dict[tuple[str, str], Decimal], advance_terms: str | None) -> None:
        same = self.state.get(p["supplier_code"]) == self.loc_state.get(p["ship_to_location"])
        kept: list[dict[str, str]] = []
        for ln in (x for x in self.h["po_line"] if x["po_no"] == p["no"] and x["status"] != "cancelled"):
            settled = paid.get((p["no"], ln["line_no"]), ZERO)
            qty = D(ln["qty"]) - settled
            if qty <= 0:
                continue  # everything on the line was invoiced and paid in the old system
            received = self._receipts(p["no"], ln, settled)
            taxable = q2(qty * D(ln["unit_price"]))
            kept.append(
                {
                    **{k: ln[k] for k in ("po_no", "line_no", "item_code", "uom", "unit_price", "gst_rate",
                                          "description", "need_by", "promised_date", "cost_centre_code", "gl_code")},
                    "qty": qty_s(qty), "received_qty": qty_s(received),
                    "invoiced_qty": qty_s(max(D(ln["invoiced_qty"]) - settled, ZERO)), "_taxable": str(taxable),
                }
            )  # fmt: skip
        if kept:
            self._header(dict(p, payment_terms_code=advance_terms or p["payment_terms_code"]), kept, same)

    def _header(self, p: dict[str, str], lines: list[dict[str, str]], same: bool) -> None:
        taxable = cgst = sgst = igst = ZERO
        for ln in lines:
            t = D(ln.pop("_taxable"))
            c, s, i = _gst(t, D(ln["gst_rate"]), same)
            taxable, cgst, sgst, igst = taxable + t, cgst + c, sgst + s, igst + i
            self.files["po_line"].append(ln)
        self.files["po"].append(
            {
                **{k: p.get(k, "") for k in COLUMNS["po"]},
                "taxable": money(taxable), "cgst": money(cgst), "sgst": money(sgst), "igst": money(igst),
                "total": money(taxable + cgst + sgst + igst),
            }
        )  # fmt: skip

    def _receipts(self, po_no: str, ln: dict[str, str], settled: Decimal) -> Decimal:
        """The line's receipts and service confirmations less what paid invoices covered, oldest first; returns what
        stays accepted."""
        left, kept = settled, ZERO
        heads = {g["no"]: g for g in self.h["goods_receipt"]}
        goods = sorted(
            (g for g in self.h["gr_line"] if g["po_no"] == po_no and g["po_line_no"] == ln["line_no"]),
            key=lambda g: (heads[g["gr_no"]]["accepted_on"] or heads[g["gr_no"]]["gate_entry_at"], g["gr_no"]),
        )
        for g in goods:
            accepted = D(g["qty_accepted"] or 0)
            take = min(left, accepted)
            left -= take
            if accepted - take <= 0:
                continue
            row = dict(g)
            if take:
                row["qty_accepted"] = qty_s(accepted - take)
                row["qty_received"] = qty_s(D(g["qty_received"]) - take)
            kept += accepted - take
            self.files["gr_line"].append({k: row.get(k, "") for k in COLUMNS["gr_line"]})
            self._receipt_head(heads[g["gr_no"]])
        price = D(ln["unit_price"])
        for s in sorted(
            (x for x in self.h["service_receipt"] if x["po_no"] == po_no and x["po_line_no"] == ln["line_no"]),
            key=lambda x: (x["accepted_on"], x["no"]),
        ):
            qty = D(s["amount_accepted"]) / price if price else ZERO
            take = min(left, qty)
            left -= take
            if qty - take <= 0:
                continue
            self.files["service_entry"].append(
                {**{k: s.get(k, "") for k in COLUMNS["service_entry"]}, "amount_accepted": money((qty - take) * price)}
            )
            kept += qty - take
        return kept

    def _receipt_head(self, g: dict[str, str]) -> None:
        if any(r["no"] == g["no"] for r in self.files["goods_receipt"]):
            return
        self.files["goods_receipt"].append({k: g.get(k, "") for k in COLUMNS["goods_receipt"]})

    def _invoice(self, i: dict[str, str], lines: list[dict[str, str]]) -> None:
        self.files["invoice"].append({**{k: i.get(k, "") for k in COLUMNS["invoice"]}, "paid_amount": ""})
        for ln in sorted(lines, key=lambda x: int(x["line_no"])):
            self.files["invoice_line"].append({k: ln.get(k, "") for k in COLUMNS["invoice_line"]})

    # ---- the planted documents
    def _planted_receipt(self, x: dict[str, Any]) -> None:
        """An order received and not invoiced at the cutoff (the history has none)."""
        qty, price = D(x["qty"]), D(x["unit_price"])
        same = self.state.get(x["supplier_code"]) == self.loc_state.get(x["ship_to_location"])
        line = {
            "po_no": x["po_no"], "line_no": "1", "item_code": x["item_code"], "qty": qty_s(qty), "uom": x["uom"],
            "unit_price": money(price), "gst_rate": str(x["gst_rate"]), "description": x["description"],
            "need_by": x["need_by"], "promised_date": x["need_by"], "cost_centre_code": x["cost_centre_code"],
            "gl_code": x["gl_code"], "received_qty": qty_s(D(x["received"])), "invoiced_qty": qty_s(ZERO),
            "_taxable": str(q2(qty * price)),
        }  # fmt: skip
        head = {k: str(x.get(k, "")) for k in COLUMNS["po"]} | {"no": x["po_no"]}
        self._header(head, [line], same)
        self.files["goods_receipt"].append(
            {
                "no": x["gr_no"], "po_no": x["po_no"], "location_code": x["ship_to_location"],
                "gate_entry_at": f"{x['received_on']}T11:00:00+05:30", "received_by": x["received_by"],
                "delivery_challan_no": x.get("challan", ""), "inspection_status": "not_required",
                "accepted_on": x["received_on"],
            }
        )  # fmt: skip
        self.files["gr_line"].append(
            {
                "gr_no": x["gr_no"], "po_no": x["po_no"], "po_line_no": "1", "qty_received": qty_s(D(x["received"])),
                "qty_accepted": qty_s(D(x["received"])), "qty_rejected": qty_s(ZERO), "reject_reason": "",
            }
        )  # fmt: skip

    def _advance(self, a: dict[str, Any]) -> None:
        po = next(p for p in self.files["po"] if p["no"] == a["po_no"])
        pct = D(a["pct"])
        self.files["advance"].append(
            {
                "payment_no": a["payment_no"], "po_no": a["po_no"], "paid_on": a["paid_on"],
                "gross": money(D(po["total"]) * pct / 100), "taxable": money(D(po["taxable"]) * pct / 100),
                "tds_amount": money(D(a.get("tds_amount", 0))), "recovered_gross": money(ZERO),
                "recovered_taxable": money(ZERO), "utr": a["utr"],
            }
        )  # fmt: skip

    def _ytd(self, inv: dict[str, dict[str, str]]) -> None:
        cutoff = date.fromisoformat(self.plan["cutoff"])
        start = date(cutoff.year if cutoff.month >= 4 else cutoff.year - 1, 4, 1)
        fy = f"{start.year}-{(start.year + 1) % 100:02d}"
        bought: dict[str, Decimal] = defaultdict(Decimal)
        for i in inv.values():
            if i["status"] != "cancelled" and start.isoformat() <= i["received_on"] <= cutoff.isoformat():
                sign = -1 if i["type"] == "credit_note" else 1
                bought[i["supplier_code"]] += sign * D(i["taxable"])
        self.files["supplier_ytd"] = [
            {"supplier_code": s, "fy": fy, "taxable": money(v)} for s, v in sorted(bought.items())
        ]

    def _control(self) -> None:
        f = self.files
        self.files["control"] = [
            {"kind": "po", "count": str(len(f["po"])), "total": money(sum((D(p["total"]) for p in f["po"]), ZERO))},
            {"kind": "invoice", "count": str(len(f["invoice"])), "total": money(sum((D(i["total"]) for i in f["invoice"]), ZERO))},
            {"kind": "advance", "count": str(len(f["advance"])),
             "total": money(sum((D(a["gross"]) - D(a["recovered_gross"]) for a in f["advance"]), ZERO))},
        ]  # fmt: skip


# ---- the planted refusals ------------------------------------------------------------------------------------------


def bad_files(
    good: dict[str, list[dict[str, str]]], bad: dict[str, Any]
) -> tuple[dict[str, list[dict[str, str]]], list[dict[str, str]]]:
    """The good files plus the planted rows of the profile's `cutover.bad` block; returns them and the truth rows."""
    f = {k: [dict(r) for r in v] for k, v in good.items()}
    truth: list[dict[str, str]] = []

    def left_out(kind: str, key: str, reason: str) -> None:
        truth.append({"kind": kind, "key": key, "expected": "left_out", "reason_contains": reason, "amount": ""})

    p = bad["price_line"]  # an order line whose price does not give the header's total
    for ln in f["po_line"]:
        if ln["po_no"] == p["po_no"] and ln["line_no"] == "1":
            ln["unit_price"] = p["unit_price"]
    left_out("po", p["po_no"], "by the lines")
    for g in good["goods_receipt"]:  # its receipts go with it
        if g["po_no"] == p["po_no"]:
            left_out("goods_receipt", g["no"], "left out")
    o = bad["over_receipt"]
    f["goods_receipt"].append({**next(g for g in good["goods_receipt"] if g["po_no"] == o["po_no"]), "no": o["gr_no"]})
    f["gr_line"].append(
        {"gr_no": o["gr_no"], "po_no": o["po_no"], "po_line_no": "1", "qty_received": o["qty"], "qty_accepted": o["qty"],
         "qty_rejected": "0.000", "reject_reason": ""}
    )  # fmt: skip
    left_out("goods_receipt", o["gr_no"], f"receives {o['qty']} of {o['ordered']} ordered")
    pend = bad["pending_receipt"]
    src = next(g for g in good["goods_receipt"] if g["no"] == pend["copy_of"])
    f["goods_receipt"].append({**src, "no": pend["gr_no"], "inspection_status": "pending"})
    for g in [g for g in good["gr_line"] if g["gr_no"] == pend["copy_of"]]:
        f["gr_line"].append({**g, "gr_no": pend["gr_no"]})
    left_out("goods_receipt", pend["gr_no"], "inspection")
    model = good["invoice"][0]
    nf = bad["unknown_order"]
    f["invoice"].append({**model, "no": nf["invoice_no"], "supplier_invoice_no": nf["supplier_invoice_no"]})
    model_line = next(ln for ln in good["invoice_line"] if ln["invoice_no"] == model["no"])
    f["invoice_line"].append({**model_line, "invoice_no": nf["invoice_no"], "po_no": nf["po_no"], "line_no": "1"})
    for ln in good["invoice_line"]:
        if ln["invoice_no"] == model["no"] and ln["line_no"] != "1":
            f["invoice_line"].append({**ln, "invoice_no": nf["invoice_no"]})
    left_out("invoice", nf["invoice_no"], "not found")
    dup = bad["duplicate"]
    src_inv = next(i for i in good["invoice"] if i["no"] == dup["copy_of"])
    f["invoice"].append({**src_inv, "no": dup["invoice_no"]})
    f["invoice_line"] += [
        {**ln, "invoice_no": dup["invoice_no"]} for ln in good["invoice_line"] if ln["invoice_no"] == dup["copy_of"]
    ]
    left_out("invoice", dup["invoice_no"], "duplicate")
    paid = bad["paid"]  # an invoice paid in the old system
    f["invoice"].append({**paid["row"], "paid_amount": ""})
    f["invoice_line"] += paid["lines"]
    left_out("invoice", paid["row"]["no"], "not an open document")
    part = bad["part_paid"]
    src_inv = next(i for i in good["invoice"] if i["no"] == part["copy_of"])
    f["invoice"].append({**src_inv, "no": part["invoice_no"], "supplier_invoice_no": part["supplier_invoice_no"],
                         "paid_amount": part["paid_amount"]})  # fmt: skip
    f["invoice_line"] += [
        {**ln, "invoice_no": part["invoice_no"]} for ln in good["invoice_line"] if ln["invoice_no"] == part["copy_of"]
    ]
    left_out("invoice", part["invoice_no"], "part paid")
    adv = bad["advance"]
    src_adv = next(a for a in good["advance"] if a["po_no"] == adv["po_no"])
    po = next(x for x in good["po"] if x["no"] == adv["po_no"])
    pct = D(adv["pct"])
    f["advance"].append({**src_adv, "payment_no": adv["payment_no"], "gross": money(D(po["total"]) * pct / 100),
                         "taxable": money(D(po["taxable"]) * pct / 100)})  # fmt: skip
    left_out("advance", adv["payment_no"], "above the advance")
    sup = bad["unknown_supplier"]
    src_po = next(x for x in good["po"] if x["no"] == sup["copy_of"])
    f["po"].append({**src_po, "no": sup["po_no"], "supplier_code": sup["supplier_code"], "payment_terms_code": "NET30"})
    f["po_line"] += [{**ln, "po_no": sup["po_no"], "received_qty": "0.000", "invoiced_qty": "0.000"}
                     for ln in good["po_line"] if ln["po_no"] == sup["copy_of"]]  # fmt: skip
    left_out("po", sup["po_no"], sup["supplier_code"])
    for c in f["control"]:
        if c["kind"] == "invoice":
            c["total"] = money(D(c["total"]) + D(bad["control_invoice_off"]))
    truth.append(
        {"kind": "control", "key": "invoice", "expected": "control_fails", "reason_contains": "", "amount": ""}
    )
    return f, truth


# ---- the answer key ------------------------------------------------------------------------------------------------


def truth_rows(files: dict[str, list[dict[str, str]]]) -> list[dict[str, str]]:
    """One row per document of cutover/ (loaded) and the totals the platform must show after loading them: what is
    payable, received and not invoiced (value received less value invoiced per order line, as on the cutoff), and the
    advances still to be recovered; overall and per supplier."""
    rows: list[dict[str, str]] = []

    def add(kind: str, key: str, amount: str = "") -> None:
        rows.append({"kind": kind, "key": key, "expected": "loaded", "reason_contains": "", "amount": amount})

    for p in files["po"]:
        add("po", p["no"], p["total"])
    for g in files["goods_receipt"]:
        add("goods_receipt", g["no"])
    for s in files["service_entry"]:
        add("service_entry", s["no"], s["amount_accepted"])
    for i in files["invoice"]:
        add("invoice", i["no"], i["total"])
    for a in files["advance"]:
        add("advance", a["payment_no"], money(D(a["gross"]) - D(a["recovered_gross"])))
    supplier = {p["no"]: p["supplier_code"] for p in files["po"]}
    price = {(ln["po_no"], ln["line_no"]): D(ln["unit_price"]) for ln in files["po_line"]}
    gap: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for g in files["gr_line"]:
        gap[(g["po_no"], g["po_line_no"])] += q2(D(g["qty_accepted"]) * price[(g["po_no"], g["po_line_no"])])
    for s in files["service_entry"]:
        gap[(s["po_no"], s["po_line_no"])] += D(s["amount_accepted"])
    for ln in files["invoice_line"]:
        gap[(ln["po_no"], ln["po_line_no"])] -= D(ln["taxable"])
    totals: dict[str, dict[str, Decimal]] = {
        k: defaultdict(Decimal) for k in ("open_payable", "grni", "advance_balance")
    }
    for (po_no, _), v in gap.items():
        if v > 0:
            totals["grni"][supplier[po_no]] += v
    for i in files["invoice"]:
        totals["open_payable"][i["supplier_code"]] += D(i["total"])
    for a in files["advance"]:
        totals["advance_balance"][supplier[a["po_no"]]] += D(a["gross"]) - D(a["recovered_gross"])
    for measure, by in totals.items():
        rows.append({"kind": "total", "key": measure, "expected": "loaded", "reason_contains": "",
                     "amount": money(sum(by.values(), ZERO))})  # fmt: skip
        for s, v in sorted(by.items()):
            if v:
                rows.append({"kind": "total", "key": f"{measure}/{s}", "expected": "loaded", "reason_contains": "",
                             "amount": money(v)})  # fmt: skip
    return rows


def write(out: Path, profile: Any) -> dict[str, int]:
    """cutover/, cutover-bad/ (when the profile plants refusals) and truth/truth_cutover.csv."""
    plan = dict(profile["cutover"], cutoff=profile["history"]["cutoff"])
    good = Cutover(out, plan, profile.name).build()
    truth = truth_rows(good)
    for name, rows in good.items():
        write_csv(out / "cutover" / f"{name}.csv", COLUMNS[name], rows)
    if plan.get("bad"):
        paid = _paid_invoice(out, plan["bad"]["paid_invoice"])
        bad, more = bad_files(good, {**plan["bad"], "paid": paid})
        truth += more
        for name, rows in bad.items():
            write_csv(out / "cutover-bad" / f"{name}.csv", COLUMNS[name], rows)
    write_csv(out / "truth" / "truth_cutover.csv", TRUTH, [{"company": profile.name, **r} for r in truth])
    return {name: len(rows) for name, rows in good.items()}


def _paid_invoice(out: Path, no: str) -> dict[str, Any]:
    row = next(i for i in read(out / "history" / "invoice.csv") if i["no"] == no)
    lines = [ln for ln in read(out / "history" / "invoice_line.csv") if ln["invoice_no"] == no]
    return {
        "row": {k: row.get(k, "") for k in COLUMNS["invoice"]},
        "lines": [{k: ln.get(k, "") for k in COLUMNS["invoice_line"]} for ln in lines],
    }


# ---- checks ----------------------------------------------------------------------------------------------------------


def problems(out: Path) -> list[str]:
    """What is wrong with cutover/: references to the masters, control totals, quantities."""
    folder = out / "cutover"
    if not folder.is_dir():
        return []
    f = {name: read(folder / f"{name}.csv") if (folder / f"{name}.csv").is_file() else [] for name in COLUMNS}
    m = out / "master"
    known = {
        "supplier_code": {r["code"] for r in read(m / "supplier.csv")},
        "ship_to_location": {r["code"] for r in read(m / "location.csv")},
        "payment_terms_code": {r["code"] for r in read(m / "payment_terms.csv")},
        "item_code": {r["code"] for r in read(m / "item.csv")},
        "cost_centre_code": {r["code"] for r in read(m / "cost_centre.csv")},
        "gl_code": {r["code"] for r in read(m / "gl_account.csv")},
        "contract_code": {r["code"] for r in read(m / "contract.csv")},
        "buyer": {r["code"] for r in read(m / "user.csv")},
    }
    found: list[str] = []
    for name in ("po", "po_line", "invoice", "supplier_ytd"):
        for r in f[name]:
            for col, codes in known.items():
                if r.get(col) and r[col] not in codes:
                    found.append(f"cutover/{name}.csv: {col} '{r[col]}' is not in the masters")
    pos = {p["no"] for p in f["po"]}
    ordered = {(ln["po_no"], ln["line_no"]): D(ln["qty"]) for ln in f["po_line"]}
    accepted: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for g in f["gr_line"]:
        accepted[(g["po_no"], g["po_line_no"])] += D(g["qty_accepted"])
    for key, q in accepted.items():
        if key not in ordered:
            found.append(f"cutover/gr_line.csv: {key[0]} line {key[1]} is not an order line of the files")
        elif q > ordered[key]:
            found.append(f"cutover/gr_line.csv: {key[0]} line {key[1]} accepts {q} of {ordered[key]} ordered")
    for name, col in (("invoice_line", "po_no"), ("advance", "po_no"), ("goods_receipt", "po_no")):
        for r in f[name]:
            if r[col] not in pos:
                found.append(f"cutover/{name}.csv: {r[col]} is not an order of the files")
    control = {c["kind"]: c for c in f["control"]}
    sums = {
        "po": (len(f["po"]), sum((D(p["total"]) for p in f["po"]), ZERO)),
        "invoice": (len(f["invoice"]), sum((D(i["total"]) for i in f["invoice"]), ZERO)),
        "advance": (len(f["advance"]), sum((D(a["gross"]) - D(a["recovered_gross"]) for a in f["advance"]), ZERO)),
    }
    for kind, (n, total) in sums.items():
        c = control.get(kind)
        if c is None or int(c["count"]) != n or D(c["total"]) != total:
            found.append(
                f"cutover/control.csv: {kind} says {c and (c['count'], c['total'])}, the files {n}, {money(total)}"
            )
    return found
