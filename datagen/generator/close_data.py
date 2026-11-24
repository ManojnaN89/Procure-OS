"""The reconcile-and-close data of a demo company (block 13): the GSTR-2B files and the answer keys for the four
measures and the accounting export. The arithmetic here is the generator's own, from the dataset's files, so the evals
have an oracle that does not call the product's code.
"""

import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from master_files import read

D = Decimal
PAISE = D("0.01")
FOUR = D("0.0001")


def _q2(value: Any) -> Decimal:
    return D(str(value or 0)).quantize(PAISE, rounding=ROUND_HALF_UP)


def _month_end(period: str) -> date:
    y, m = int(period[:4]), int(period[5:])
    return (date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)) - timedelta(days=1)


def _next(period: str) -> str:
    y, m = int(period[:4]), int(period[5:])
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def _period(d: date) -> str:
    return f"{d.year}-{d.month:02d}"


def _fy(d: date) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{(y + 1) % 100:02d}"


def _normal(value: str) -> str:
    return "".join(c for c in value.upper() if c.isalnum())


def _standard(prices: dict[str, list[tuple[date, date | None, Decimal]]], item: str, on: date) -> Decimal | None:
    for start, end, price in prices.get(item, []):
        if start <= on and (end is None or end >= on):
            return price
    return None


def _version(prices: dict[str, list[tuple[date, date | None, Decimal]]], item: str, on: date) -> str:
    for n, (start, end, _) in enumerate(prices.get(item, []), start=1):
        if start <= on and (end is None or end >= on):
            return str(n)
    return "-"


def _prices(out: Path) -> dict[str, list[tuple[date, date | None, Decimal]]]:
    rows = read(out / "master" / "item_standard_price.csv")
    prices: dict[str, list[tuple[date, date | None, Decimal]]] = defaultdict(list)
    for r in rows:
        prices[r["item_code"]].append(
            (date.fromisoformat(r["effective_from"]), date.fromisoformat(r["effective_to"]) if r["effective_to"] else None,
             D(r["price"]))
        )
    for item, spans in prices.items():
        spans.sort()
    return prices


def _write(path: Path, header: str, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header.split(","), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _gstin_of(suppliers: dict[str, dict[str, str]], code: str) -> str:
    return suppliers[code]["gstin"]


def _doc(inv: dict[str, str], *, taxable: Decimal | None = None, cgst: Decimal | None = None,
         igst: Decimal | None = None, sgst: Decimal | None = None, gstin: str | None = None,
         number: str | None = None, itc: bool = True, reason: str | None = None) -> dict[str, Any]:
    return {
        "inum": number or inv["supplier_invoice_no"],
        "dt": inv["invoice_date"],
        "val": str(_q2(D(inv["taxable"]) + D(inv["cgst"]) + D(inv["sgst"]) + D(inv["igst"]) + D(inv["cess"])
                      + D(inv["round_off"]))),
        "txval": str(_q2(D(inv["taxable"]) if taxable is None else taxable)),
        "igst": str(_q2(D(inv["igst"]) if igst is None else igst)),
        "cgst": str(_q2(D(inv["cgst"]) if cgst is None else cgst)),
        "sgst": str(_q2(D(inv["sgst"]) if sgst is None else sgst)),
        "cess": str(_q2(D(inv["cess"]))),
        "itcavl": "Y" if itc else "N",
        "rsn": reason or "",
        "_gstin": gstin or inv["supplier_gstin"],
    }  # fmt: skip


def gstr2b(profile: Any, out: Path) -> dict[str, int]:
    """One file per history month of the default GSTIN, with G1-G8 planted in the target month (Apex Oct, Brightwave Sep)."""
    history = out / "history"
    invoices = [r for r in read(history / "invoice.csv") if r["type"] == "invoice" and r["status"] != "cancelled"]
    suppliers = {r["code"]: r for r in read(out / "master" / "supplier.csv")}
    our_gstin = profile.gstin
    target = "2026-09" if profile.name == "brightwave" else "2026-10"
    months = sorted({_period(date.fromisoformat(r["invoice_date"])) for r in invoices})
    by_month: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in invoices:
        by_month[_period(date.fromisoformat(r["invoice_date"]))].append(r)
    planted: set[str] = set()
    truth: list[dict[str, Any]] = []
    for month in months:
        mine = [r for r in by_month[month] if r["buyer_gstin"] == our_gstin]
        docs: list[dict[str, Any]] = []
        if month == target and len(mine) >= 8:
            chosen = mine[:8]
            planted |= {r["no"] for r in chosen}
            g1, g2, g3, g4, g6, g7, g8 = chosen[0], chosen[1], chosen[2], chosen[3], chosen[4], chosen[5], chosen[6]
            docs.append(_doc(g1))
            docs.append(_doc(g2, taxable=D(g2["taxable"]) + 100))
            docs.append(_doc(g3, cgst=D(g3["cgst"]) + D("0.50")))
            # G4 is left out and filed in the next month
            other_gstin = "24" + g7["supplier_gstin"][2:]
            docs.append(_doc(g7, gstin=other_gstin))
            docs.append(
                {
                    "inum": f"2BONLY/{month.replace('-', '')}/001", "dt": f"{month}-15", "val": "1180.00",
                    "txval": "1000.00", "igst": "0.00", "cgst": "90.00", "sgst": "90.00", "cess": "0.00",
                    "itcavl": "Y", "rsn": "", "_gstin": _gstin_of(suppliers, g5_supplier(suppliers)),
                }
            )  # fmt: skip
            docs.append(_doc(g6, itc=False, reason="P"))
            docs.append(_doc(g8))
            docs.append(_doc(g8))  # G8: the same document twice
            for r in mine:
                if r["no"] in {c["no"] for c in chosen}:
                    continue
                docs.append(_doc(r))
            truth += _planted_truth(
                g1=g1, g2=g2, g3=g3, g4=g4, g6=g6, g7=g7, g8=g8, period=month, gstin=our_gstin
            )
        else:
            for r in mine:
                docs.append(_doc(r))
        if month == target and len(mine) >= 8:
            docs = [d for d in docs if d["inum"] != g4["supplier_invoice_no"]]
        _write_file(out, our_gstin, month, docs)
        if month == target and len(mine) >= 8:
            g4_month = _next(month)
            _write_file(out, our_gstin, g4_month, [_doc(g4)])
            truth.append({"gstin": our_gstin, "period": g4_month, "doc": g4["supplier_invoice_no"],
                          "outcome": "matched", "field": "", "ours": "", "theirs": ""})
    _write(out / "truth" / "truth_gstr2b.csv", "gstin,period,doc,outcome,field,ours,theirs", truth)
    return {"months": len(months), "truth": len(truth)}


def g5_supplier(suppliers: dict[str, dict[str, str]]) -> str:
    return next(code for code in sorted(suppliers) if suppliers[code].get("gstin"))


def _planted_truth(*, g1: dict[str, str], g2: dict[str, str], g3: dict[str, str], g4: dict[str, str],
                   g6: dict[str, str], g7: dict[str, str], g8: dict[str, str], period: str, gstin: str) -> list[dict[str, Any]]:
    def row(inv: dict[str, str], outcome: str, field: str = "", ours: str = "", theirs: str = "") -> dict[str, Any]:
        return {"gstin": gstin, "period": period, "doc": inv["supplier_invoice_no"], "outcome": outcome,
                "field": field, "ours": ours, "theirs": theirs}

    return [
        row(g1, "matched"),
        row(g2, "differs", "taxable", g2["taxable"], str(_q2(D(g2["taxable"]) + 100))),
        row(g3, "matched"),
        row(g4, "only_books", "months", "0", ""),
        {"gstin": gstin, "period": period, "doc": f"2BONLY/{period.replace('-', '')}/001", "outcome": "only_2b",
         "field": "", "ours": "", "theirs": "1000.00"},
        row(g6, "matched"),
        row(g7, "gstin_differs"),
        row(g8, "duplicate"),
    ]  # fmt: skip


def _write_file(out: Path, gstin: str, period: str, docs: list[dict[str, Any]]) -> None:
    by_supplier: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for doc in docs:
        cleaned = {k: v for k, v in doc.items() if not k.startswith("_")}
        by_supplier[str(doc["_gstin"])].append(cleaned)
    body = {
        "data": {
            "gstin": gstin,
            "rtnprd": f"{period[5:]}{period[:4]}",
            "gendt": (_month_end(period) + timedelta(days=11)).isoformat(),
            "docdata": {"b2b": [{"ctin": ctin, "inv": invs} for ctin, invs in sorted(by_supplier.items())]},
        }
    }
    path = out / "gst" / f"gstr2b_{gstin}_{period.replace('-', '')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")


def vendor_statement_period(profile: Any, out: Path) -> None:
    """Apex SUP-003's February statement: a January invoice and a payment dated 2 March, both outside its period (RC1)."""
    if profile.name != "apex":
        return
    history = out / "history"
    supplier = "SUP-003"
    invoices = [
        r for r in read(history / "invoice.csv")
        if r["supplier_code"] == supplier and r["type"] == "invoice" and r["invoice_date"].startswith("2026-01")
    ]
    if not invoices:
        return
    inv = invoices[0]
    payments = [
        r for r in read(history / "payment.csv")
        if r["supplier_code"] == supplier and (r.get("paid_on") or "").startswith("2026-03")
    ]
    rows = [
        {"period_from": "2026-02-01", "period_to": "2026-02-28", "date": inv["invoice_date"], "type": "invoice",
         "document_no": inv["supplier_invoice_no"], "amount": inv["total"], "reference": ""},
    ]  # fmt: skip
    if payments:
        rows.append(
            {"period_from": "2026-02-01", "period_to": "2026-02-28", "date": payments[0]["paid_on"], "type": "payment",
             "document_no": payments[0].get("utr") or payments[0]["no"], "amount": payments[0]["amount"],
             "reference": payments[0]["no"]}
        )  # fmt: skip
    _write(out / "history" / "vendor_statement_period.csv",
           "period_from,period_to,date,type,document_no,amount,reference", rows)  # fmt: skip


def price_measures(profile: Any, out: Path) -> dict[str, int]:
    """The history's PPV at the three recognition points, contract variance and realised saving, line by line."""
    history = out / "history"
    prices = _prices(out)
    invoices = {r["no"]: r for r in read(history / "invoice.csv")}
    po_lines = {(r["po_no"], r["line_no"]): r for r in read(history / "po_line.csv")}
    rows: list[dict[str, Any]] = []
    for line in read(history / "invoice_line.csv"):
        inv = invoices.get(line["invoice_no"])
        if inv is None or inv["status"] not in ("approved", "paid") or inv["status"] == "cancelled":
            continue
        qty, taxable = D(line["qty"]), D(line["taxable"])
        if qty <= 0:
            continue
        unit = (taxable / qty).quantize(FOUR, rounding=ROUND_HALF_UP)
        on = date.fromisoformat(inv["received_on"])
        item = po_lines.get((line["po_no"], line["po_line_no"]), {}).get("item_code") or ""
        standard = _standard(prices, item, on)
        common = {"source_ref": inv["no"], "source_line": line["line_no"], "item_code": item,
                  "po_no": line["po_no"], "po_line_no": line["po_line_no"], "on_date": on.isoformat(),
                  "qty": str(qty), "unit_price": str(unit)}
        if standard is not None:
            amount = ((unit - standard) * qty).quantize(PAISE, rounding=ROUND_HALF_UP)
            rows.append({**common, "measure": "ppv", "part": "invoice", "recognition": "invoice",
                         "reference_price": str(standard), "reference": f"standard v{_version(prices, item, on)}",
                         "amount": str(amount)})  # fmt: skip
            rows.append({**common, "measure": "ppv", "part": "receipt", "recognition": "receipt",
                         "reference_price": str(standard), "reference": f"standard v{_version(prices, item, on)}",
                         "amount": str(amount)})  # fmt: skip
            rows.append({**common, "measure": "ppv", "part": "true_up", "recognition": "both",
                         "reference_price": str(standard), "reference": f"standard v{_version(prices, item, on)}",
                         "amount": str(amount)})  # fmt: skip
        contract = _contract_price(out, line["po_no"], line["po_line_no"], on)
        if contract is not None:
            rows.append({**common, "measure": "contract_variance", "part": "invoice", "recognition": "invoice",
                         "reference_price": str(contract), "reference": "contract price in force",
                         "amount": str(((unit - contract) * qty).quantize(PAISE, rounding=ROUND_HALF_UP))})  # fmt: skip
    _write(
        out / "truth" / "truth_price_measures.csv",
        "measure,part,recognition,source_ref,source_line,item_code,po_no,po_line_no,on_date,qty,unit_price,"
        "reference_price,reference,amount",
        rows,
    )
    _accounting(profile, out)
    return {"price_measures": len(rows)}


def _contract_price(out: Path, po_no: str, line_no: int, on: date) -> Decimal | None:
    po_line = next(
        (r for r in read(out / "history" / "po_line.csv") if r["po_no"] == po_no and r["line_no"] == str(line_no)),
        None,
    )
    if po_line is None:
        return None
    for r in read(out / "master" / "price_record.csv"):
        if r["item_code"] == po_line["item_code"] and r["contract_code"] and r["valid_from"] <= on.isoformat() <= r["valid_to"]:
            return D(r["price"])
    return None


def _accounting(profile: Any, out: Path) -> None:
    """The purchase and payment vouchers of the target month, by ledger (the export's own shape)."""
    target = "2026-09" if profile.name == "brightwave" else "2026-10"
    history = out / "history"
    maps = {r["purpose"]: r for r in read(out / "master" / "ledger_map.csv")}
    suppliers = {r["code"]: r for r in read(out / "master" / "supplier.csv")}
    items = {r["code"]: r for r in read(out / "master" / "item.csv")}
    po_lines = {(r["po_no"], r["line_no"]): r for r in read(history / "po_line.csv")}
    rows: list[dict[str, Any]] = []

    def put(kind: str, no: str, on: str, ledger: str, debit: Decimal, credit: Decimal, party: str) -> None:
        rows.append({"voucher_type": kind, "voucher_no": no, "date": on, "event_period": on[:7],
                     "ledger_code": ledger, "ledger_name": maps.get(ledger, {}).get("ledger_name", ledger),
                     "party_code": party, "debit": f"{debit:.2f}", "credit": f"{credit:.2f}"})

    for inv in read(history / "invoice.csv"):
        if inv["type"] != "invoice" or inv["status"] not in ("approved", "paid"):
            continue
        on = inv["received_on"]
        if on[:7] != target:
            continue
        party = inv["supplier_code"]
        msme = suppliers[party]["msme_category"] in ("micro", "small")
        payable = maps["payable_msme" if msme else "payable_other"]["gl_code"]
        for line in read(history / "invoice_line.csv"):
            if line["invoice_no"] != inv["no"]:
                continue
            po_line = po_lines.get((line["po_no"], line["po_line_no"]), {})
            gl = po_line.get("gl_code") or items.get(po_line.get("item_code", ""), {}).get("default_gl") or "1410"
            put("purchase", inv["no"], on, str(gl), D(line["taxable"]), D(0), party)
        for head, ledger in (("cgst", "gst_input_cgst"), ("sgst", "gst_input_sgst"), ("igst", "gst_input_igst")):
            if D(inv[head]):
                put("purchase", inv["no"], on, maps[ledger]["gl_code"], D(inv[head]), D(0), party)
        tds = D(inv.get("_tds") or 0)
        gross = sum((D(r["debit"]) for r in rows if r["voucher_no"] == inv["no"] and r["voucher_type"] == "purchase"), D(0))
        if tds:
            put("purchase", inv["no"], on, maps["tds_payable"]["gl_code"], D(0), tds, party)
        put("purchase", inv["no"], on, payable, D(0), gross - tds, party)
    for pay in read(history / "payment.csv"):
        on = pay.get("paid_on") or ""
        if on[:7] != target:
            continue
        party = pay["supplier_code"]
        msme = suppliers[party]["msme_category"] in ("micro", "small")
        payable = maps["payable_msme" if msme else "payable_other"]["gl_code"]
        put("payment", pay["no"], on, payable, D(pay["amount"]), D(0), party)
        put("payment", pay["no"], on, maps["bank:CBA-01"].get("gl_code") or "bank:CBA-01", D(0), D(pay["amount"]), party)
    _write(
        out / "truth" / "truth_accounting_export.csv",
        "voucher_type,voucher_no,date,event_period,ledger_code,ledger_name,party_code,debit,credit",
        rows,
    )
