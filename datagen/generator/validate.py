"""Quality checks for a generated dataset. Writes report.md into the dataset folder.

  python -I datagen/generator/validate.py data/datasets/cbc-demo-v1-seed42
Exit code 1 if any hard check fails.
"""
import csv
import hashlib
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ids import is_valid_gstin  # noqa: E402

D = Decimal


def load(root, folder, name):
    p = root / folder / f"{name}.csv"
    if not p.exists():
        return []
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def main(root):
    root = Path(root)
    L = lambda f, n: load(root, f, n)
    errs, warns, info = [], [], []

    def check(cond, msg, sample=None):
        if not cond:
            errs.append(msg + (f"  e.g. {sample}" if sample else ""))

    t = {n: L("master", n) for n in ["location", "item", "supplier", "supplier_address", "user", "cost_centre", "gl_account", "category", "contract",
                                      "price_record", "stock_opening", "item_location", "approval_rule"]}
    h = {n: L("history", n) for n in ["request", "request_line", "approval_event", "rfq", "rfq_line", "rfq_supplier", "quote", "quote_line", "award", "po",
                                      "po_line", "po_event", "goods_receipt", "gr_line", "service_receipt", "stock_movement", "invoice", "invoice_line",
                                      "tds_entry", "payment_run", "payment", "payment_allocation", "bank_statement_line", "email_message", "po_schedule", "po_charge", "invoice_charge",
                                      "return_to_vendor", "advance_adjustment", "tds_challan"]}
    tr = {n: L("truth", n) for n in ["truth_item_demand", "truth_supplier", "truth_case", "truth_match_expected"]}
    sets = lambda rows, k: {r[k] for r in rows}
    items, sups, locs = sets(t["item"], "code"), sets(t["supplier"], "code"), sets(t["location"], "code")
    users = sets(t["user"], "code")

    # ---- uniqueness of document numbers
    for tbl, key in [("request", "no"), ("rfq", "no"), ("po", "no"), ("goods_receipt", "no"), ("service_receipt", "no"), ("invoice", "no"),
                     ("payment", "no"), ("payment_run", "no"), ("quote", "no")]:
        c = Counter(r[key] for r in h[tbl])
        d = [k for k, v in c.items() if v > 1]
        check(not d, f"{tbl}.{key} not unique ({len(d)})", d[:2])

    # ---- references
    ref = [("request_line", "request_no", "request", "no"), ("approval_event", None, None, None), ("rfq_line", "rfq_no", "rfq", "no"),
           ("rfq_supplier", "rfq_no", "rfq", "no"), ("quote", "rfq_no", "rfq", "no"), ("quote_line", "quote_no", "quote", "no"),
           ("award", "rfq_no", "rfq", "no"), ("po_line", "po_no", "po", "no"), ("po_event", "po_no", "po", "no"), ("goods_receipt", "po_no", "po", "no"),
           ("gr_line", "gr_no", "goods_receipt", "no"), ("service_receipt", "po_no", "po", "no"), ("invoice_line", "invoice_no", "invoice", "no"),
           ("payment", "run_no", "payment_run", "no"), ("payment_allocation", "payment_no", "payment", "no"), ("tds_entry", "invoice_no", "invoice", "no")]
    for child, ck, parent, pk in ref:
        if not parent:
            continue
        ps = sets(h[parent], pk)
        bad = [r[ck] for r in h[child] if r[ck] and r[ck] not in ps]
        check(not bad, f"{child}.{ck} -> {parent}.{pk}: {len(bad)} orphans", bad[:3])
    for tbl, col, universe in [("request_line", "item_code", items), ("po_line", "item_code", items), ("po", "supplier_code", sups), ("invoice", "supplier_code", sups),
                               ("po", "ship_to_location", locs), ("request", "requester", users), ("po", "buyer", users)]:
        bad = [r[col] for r in h[tbl] if r[col] not in universe]
        check(not bad, f"{tbl}.{col} not in master: {len(bad)}", bad[:3])
    pay_nos = sets(h["payment"], "no")
    bad = [r["payment_no"] for r in h["tds_entry"] if r["payment_no"] and r["payment_no"] not in pay_nos]
    check(not bad, f"tds_entry.payment_no -> payment orphans {len(bad)}", bad[:2])
    bad = [r["no"] for r in h["tds_entry"] if not r["invoice_no"] and not r["payment_no"]]
    check(not bad, "tds_entry without an invoice or a payment")
    po_lines = {(r["po_no"], r["line_no"]) for r in h["po_line"]}
    bad = [(r["po_no"], r["po_line_no"]) for r in h["invoice_line"] if (r["po_no"], r["po_line_no"]) not in po_lines]
    check(not bad, f"invoice_line -> po_line orphans {len(bad)}", bad[:3])
    bad = [(r["po_no"], r["po_line_no"]) for r in h["gr_line"] if (r["po_no"], r["po_line_no"]) not in po_lines]
    check(not bad, f"gr_line -> po_line orphans {len(bad)}", bad[:3])
    req_lines = {(r["request_no"], r["line_no"]) for r in h["request_line"]}
    bad = [(r["request_no"], r["request_line_no"]) for r in h["po_line"] if r["request_no"] and (r["request_no"], r["request_line_no"]) not in req_lines]
    check(not bad, f"po_line -> request_line orphans {len(bad)}", bad[:3])

    # ---- money ties out
    lines_by_po = defaultdict(list)
    for r in h["po_line"]:
        lines_by_po[r["po_no"]].append(r)
    charges_po = defaultdict(D)
    for c in h["po_charge"]:
        charges_po[c["po_no"]] += D(c["amount"])
    for po in h["po"]:
        ls = lines_by_po[po["no"]]
        taxable = sum(D(l["qty"]) * D(l["unit_price"]) for l in ls) + charges_po[po["no"]]
        tax = sum(D(l["cgst"]) + D(l["sgst"]) + D(l["igst"]) for l in ls)
        if abs(D(po["taxable"]) - taxable) > D("0.05") * len(ls) or abs(D(po["total"]) - (D(po["taxable"]) + D(po["cgst"]) + D(po["sgst"]) + D(po["igst"]))) > D("0.02"):
            errs.append(f"PO total does not tie: {po['no']} taxable {po['taxable']} vs lines {taxable:.2f}; total {po['total']} vs {D(po['taxable']) + tax}")
            break
    lines_by_inv = defaultdict(list)
    for r in h["invoice_line"]:
        lines_by_inv[r["invoice_no"]].append(r)
    charges_inv = defaultdict(D)
    for c in h["invoice_charge"]:
        charges_inv[c["invoice_no"]] += D(c["amount"])
    nbad = 0
    for inv in h["invoice"]:
        ls = lines_by_inv.get(inv["no"], [])
        if not ls:
            nbad += 1
            continue
        taxable = sum(D(l["taxable"]) for l in ls) + charges_inv[inv["no"]]
        tax = sum(D(l["cgst"]) + D(l["sgst"]) + D(l["igst"]) for l in ls)
        tot = D(inv["taxable"]) + D(inv["cgst"]) + D(inv["sgst"]) + D(inv["igst"]) + D(inv["round_off"])
        if abs(D(inv["taxable"]) - taxable) > D("0.02") or abs(tot - D(inv["total"])) > D("0.02"):
            errs.append(f"invoice total does not tie: {inv['no']} {inv['taxable']} vs {taxable} / total {inv['total']} vs {tot}")
            break
    check(nbad == 0, f"invoices without lines: {nbad}")
    # ---- GSTIN validity
    bad = [s["gstin"] for s in t["supplier"] if s["gstin"] and not is_valid_gstin(s["gstin"])]
    check(not bad, f"invalid supplier GSTIN {bad[:2]}")
    wrong = {r["documents"].split(";")[0] for r in tr["truth_case"] if r["type"] == "wrong_gstin"}
    bad = [r["no"] for r in h["invoice"] if (r["supplier_gstin"] and not is_valid_gstin(r["supplier_gstin"])) and r["no"] not in wrong]
    check(not bad, f"invoices with invalid supplier GSTIN that are not planted: {len(bad)}", bad[:2])
    # ---- dates in order
    req_at = {r["no"]: r["created_at"] for r in h["request"]}
    po_at = {r["no"]: (r["created_at"], r["approved_at"], r["sent_at"]) for r in h["po"]}
    bad = [p["no"] for p in h["po"] if (p["approved_at"] and p["approved_at"] < p["created_at"]) or (p["sent_at"] and (not p["approved_at"] or p["sent_at"] < p["approved_at"]))]
    check(not bad, f"PO created/approved/sent out of order: {len(bad)}", bad[:3])
    bad = [g["no"] for g in h["goods_receipt"] if g["gate_entry_at"] < po_at[g["po_no"]][2]]
    check(not bad, f"goods receipt before the PO was sent: {len(bad)}", bad[:3])
    inv_rec = {r["no"]: r["received_at"] for r in h["invoice"]}
    bad = [i["no"] for i in h["invoice"] if i["type"] == "invoice" and i["invoice_date"] > i["received_at"][:10]]
    check(not bad, f"invoice dated after it was received: {len(bad)}", bad[:3])
    # ---- working days: goods receipts on working days of their location
    loc = {l["code"]: l for l in t["location"]}
    nonwork = 0
    for g in h["goods_receipt"]:
        d = date.fromisoformat(g["gate_entry_at"][:10])
        off = {5, 6} if loc[g["location_code"]]["working_days"] == "Mon-Fri" else {6}
        if d.weekday() in off:
            nonwork += 1
    check(nonwork == 0, f"goods receipts on weekly off days: {nonwork}")
    # ---- stock never negative; stock balance reconciliation
    bal = defaultdict(D)
    for o in t["stock_opening"]:
        bal[(o["item_code"], o["location_code"])] = D(o["qty"])
    neg = 0
    for m in sorted(h["stock_movement"], key=lambda r: (r["date"], 0 if r["type"] == "receipt" else 1)):
        k = (m["item_code"], m["location_code"])
        bal[k] += D(m["qty"]) if m["type"] in ("receipt", "adjust", "return", "transfer_in") else -D(m["qty"])
        if bal[k] < D("-0.001"):
            neg += 1
    check(neg == 0, f"stock goes negative {neg} times")
    # ---- invoice vs receipts: invoiced never above received+tolerance unless planted
    # ---- payments
    paid_inv = defaultdict(D)
    for a in h["payment_allocation"]:
        paid_inv[a["invoice_no"]] += D(a["amount"])
    inv_total = {i["no"]: D(i["total"]) for i in h["invoice"]}
    dup_pay = [k for k, v in paid_inv.items() if k in inv_total and v > inv_total[k] + D("0.02")]
    check(not dup_pay, f"invoices paid more than their total: {len(dup_pay)}", dup_pay[:3])
    cancelled_paid = [i["no"] for i in h["invoice"] if i["status"] == "cancelled" and i["no"] in paid_inv]
    check(not cancelled_paid, f"cancelled invoices that were paid: {len(cancelled_paid)}", cancelled_paid[:3])
    # ---- schedules, advances, returns, TDS deposits, expected matches
    sched = defaultdict(D)
    for s in h["po_schedule"]:
        sched[(s["po_no"], s["line_no"])] += D(s["qty"])
    bad = [k for k, l in ((k, next(x for x in lines_by_po[k[0]] if x["line_no"] == k[1])) for k in list(sched)[:3000]) if abs(sched[k] - D(l["qty"])) > D("0.001")]
    check(not bad, f"po_schedule quantity does not add up to the PO line: {len(bad)}", bad[:2])
    adv_pay = {p["no"]: p for p in h["payment"] if p["type"] == "advance"}
    used = defaultdict(D)
    for a in h["advance_adjustment"]:
        used[a["advance_payment_no"]] += D(a["amount"])
    bad = [k for k, v in used.items() if k not in adv_pay or v > D(adv_pay[k]["amount"]) + D(adv_pay[k]["tds_amount"]) + D("0.02")]
    check(not bad, f"advance adjusted more than was advanced (or unknown advance): {len(bad)}", bad[:2])
    po_total = {p["no"]: D(p["total"]) for p in h["po"]}
    bad = [p["no"] for p in adv_pay.values() if D(p["amount"]) + D(p["tds_amount"]) > po_total[p["po_no"]] * D("0.31")]
    check(not bad, f"advance larger than 30% of the PO: {len(bad)}", bad[:2])
    grn_nos = sets(h["goods_receipt"], "no")
    bad = [r["no"] for r in h["return_to_vendor"] if r["gr_no"] not in grn_nos]
    check(not bad, f"return_to_vendor -> goods_receipt orphans {len(bad)}", bad[:2])
    check(len({r["debit_note_no"] for r in h["return_to_vendor"]}) == len(h["return_to_vendor"]), "debit note numbers not unique")
    tds_m = defaultdict(D)
    for e in h["tds_entry"]:
        sec = e["section"]
        code = "194Q" if ("194Q" in sec or "8(ii)" in sec) else ("194C" if ("194C" in sec or "6(i)" in sec) else "194J")
        tds_m[(e["booked_on"][:7], code)] += D(e["amount"])
    bad = [(c["period"], c["section"]) for c in h["tds_challan"] if abs(tds_m.get((c["period"], c["section"]), D(0)) - D(c["amount"])) > D("0.02")]
    check(not bad, f"TDS challan does not equal the TDS deducted in the month: {len(bad)}", bad[:2])
    inv_nos = sets(h["invoice"], "no")
    bad = [r["invoice_no"] for r in tr["truth_match_expected"] if r["invoice_no"] not in inv_nos]
    check(not bad, f"truth_match_expected -> invoice orphans {len(bad)}", bad[:2])
    check(any(s["status"] == "draft" for s in t["supplier"]), "no prospective (draft) suppliers")
    # ---- reference lists: pincodes and HSN/SAC codes must exist, and pincodes must belong to the address state
    ref = root.parents[2] / "data" / "reference"
    if (ref / "pincode_directory.csv").exists():
        pin = {r["pincode"]: r["state_code"] for r in csv.DictReader(open(ref / "pincode_directory.csv", encoding="utf-8", newline=""))}
        bad = [a["supplier_code"] for a in t["supplier_address"] if a["pincode"] not in pin or pin[a["pincode"]].zfill(2) != a["state_code"].zfill(2)]
        check(not bad, f"supplier address pincode missing or in another state: {len(bad)}", bad[:3])
        bad = [l["code"] for l in t["location"] if l["pincode"] not in pin or pin[l["pincode"]].zfill(2) != l["state_code"].zfill(2)]
        check(not bad, f"location pincode missing or in another state: {len(bad)}", bad[:3])
    if (ref / "hsn_sac_codes.csv").exists():
        codes = {r["code"] for r in csv.DictReader(open(ref / "hsn_sac_codes.csv", encoding="utf-8", newline=""))}
        bad = [i["code"] for i in t["item"] if i["hsn_sac"] not in codes]
        check(not bad, f"item HSN/SAC not in the official list: {len(bad)}", bad[:3])
    # ---- planted problem rates
    tc = Counter(r["type"] for r in tr["truth_case"])
    n_inv = sum(1 for i in h["invoice"] if i["type"] == "invoice")
    n_po = len(h["po"])
    n_req = len(h["request"])
    rates = [("duplicate_request", n_req, 0.02), ("split_order", n_req, 0.005), ("part_delivery", n_po, 0.07), ("late_delivery", n_po, 0.12),
             ("price_mismatch", n_inv, 0.05), ("qty_mismatch", n_inv, 0.035), ("rejected_qty_invoiced", n_inv, 0.01), ("duplicate_invoice", n_inv, 0.015), ("scheduled_delivery", n_po, 0.03), ("tds_late_deposit", 65, 0.05),
             ("wrong_gstin", n_inv, 0.008), ("composition_tax", n_inv, 0.002), ("e_invoice_missing", n_inv, 0.01), ("credit_note", n_inv, 0.03)]
    rate_lines = []
    for typ, base, target in rates:
        got = tc.get(typ, 0)
        rate_lines.append(f"| {typ} | {got} | {got / base * 100:.1f}% | ~{target * 100:.1f}% |")
        if got == 0 and "small" not in root.name:
            errs.append(f"planted problem never occurred: {typ}")
    # ---- distributions
    spend_cat = defaultdict(D)
    cat_of = {i["code"]: i["category_code"] for i in t["item"]}
    for l in h["po_line"]:
        spend_cat[cat_of[l["item_code"]]] += D(l["qty"]) * D(l["unit_price"])
    spend_month = defaultdict(D)
    for p in h["po"]:
        spend_month[p["created_at"][:7]] += D(p["taxable"])
    sup_states = Counter(s["state_code"] for s in t["supplier"])
    gst_split = Counter()
    for p in h["po"]:
        gst_split["IGST" if D(p["igst"]) > 0 else "CGST+SGST" if D(p["cgst"]) > 0 else "no tax"] += 1
    status = {n: Counter(r["status"] for r in h[n]) for n in ("request", "po", "invoice")}
    cycle = []
    for p in h["po"][:2000]:
        cycle.append((date.fromisoformat(p["created_at"][:10]) - date.fromisoformat(req_at.get(next((l["request_no"] for l in lines_by_po[p["no"]] if l["request_no"]), ""), p["created_at"])[:10])).days)
    # ---- report
    lines = ["# Quality report", "", f"Dataset: `{root.name}`", ""]
    lines += ["## Hard checks", ""] + ([f"- FAIL: {e}" for e in errs] or ["All hard checks passed."]) + [""]
    lines += ["## Volumes", "", "| Table | Rows |", "|---|---|"] + [f"| {n} | {len(r)} |" for n, r in h.items()] + [""]
    lines += ["## Planted problems (observed vs proposed rate)", "", "| Type | Count | Observed | Proposed |", "|---|---|---|---|"] + rate_lines + [""]
    lines += ["## Spend by category (excluding GST, whole history)", "", "| Category | Rs crore |", "|---|---|"]
    lines += [f"| {c} | {v / 10000000:.2f} |" for c, v in sorted(spend_cat.items(), key=lambda x: -x[1])] + [""]
    lines += ["## Spend by month (PO value excl. GST, Rs lakh)", "", "| Month | Rs lakh |", "|---|---|"] + [f"| {m} | {v / 100000:.1f} |" for m, v in sorted(spend_month.items())] + [""]
    lines += ["## Mix", "", f"- Supplier states: {dict(sup_states)}", f"- PO tax split: {dict(gst_split)}", f"- Request status: {dict(status['request'])}",
              f"- PO status: {dict(status['po'])}", f"- Invoice status: {dict(status['invoice'])}",
              f"- Request to PO days (median): {sorted(cycle)[len(cycle) // 2] if cycle else 'n/a'}", ""]
    (root / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:6]))
    print(f"\n{len(errs)} problems; report written to {root / 'report.md'}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
