"""Post-processing: TDS, due dates, payment runs, bank lines, cut-off filtering, statuses, chronological numbering."""
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from common import CUTOFF, START, D, fy, fy4, iso, money, q2, qty_s, ts, read_csv, REF
from simulate import CUT_DT, dt_at

CUT_S = "2026-10-31T23:59:00+05:30"
SECTION_NEW = {"194Q": "393(1) Sl.8(ii) [194Q]", "194C": "393(1) Sl.6(i) [194C]", "194J(a)": "393(1) Sl.6(iii) [194J]", "194J(b)": "393(1) Sl.6(iii) [194J]"}


SEC_OF_CAT = {"RAW": "194Q", "PKG": "194Q", "MRO": "194Q", "ELE": "194Q", "TLS": "194Q", "CHM": "194Q", "PPE": "194Q", "OFC": "194Q", "ITH": "194Q",
              "FUR": "194Q", "CAP": "194Q", "FAC": "194C", "AMC": "194C", "LOG": "194C", "PRO": "194J(b)", "SWS": "194J(a)", "TRV": "none"}


def section_of(cat, hsn):
    sec = SEC_OF_CAT[cat]
    if cat == "TRV" and hsn in ("998596", "998361"):
        sec = "194C"
    if cat == "LOG" and hsn == "996511":
        sec = "none"
    return sec


def tds_for(sim, p, s, cum_before, inv_item_name=""):
    """Returns (section, base, rate_pct, amount) for one invoice, or (None, ...)."""
    cat = p["category"]
    hsn = p["hsn"]
    base = p["taxable"]
    single = p.get("po_taxable", base)
    date_ = p["received_dt"].date()
    pan_bad = s["pan_status"] == "inoperative"
    ptype = s["pan"][3]
    sec = {"RAW": "194Q", "PKG": "194Q", "MRO": "194Q", "ELE": "194Q", "TLS": "194Q", "CHM": "194Q", "PPE": "194Q", "OFC": "194Q", "ITH": "194Q",
           "FUR": "194Q", "CAP": "194Q", "FAC": "194C", "AMC": "194C", "LOG": "194C", "PRO": "194J(b)", "SWS": "194J(a)", "TRV": "none"}[cat]
    if cat == "TRV" and hsn in ("998596", "998361"):
        sec = "194C"
    if cat == "LOG" and hsn == "996511":
        return None, base, D(0), D(0)
    if sec == "194J(b)" and hsn in ("998231", "998221"):
        sec = "194J(b)"
    if sec == "none":
        return None, base, D(0), D(0)
    cum_after = cum_before + base
    if sec == "194Q":
        thr = D("5000000")
        over = max(D(0), cum_after - max(thr, cum_before))
        rate = D("5") if pan_bad else D("0.1")
        return sec, over, rate, q2(over * rate / 100)
    if sec == "194C":
        if not (single > 30000 or cum_after > 100000):
            return None, base, D(0), D(0)
        rate = D("20") if pan_bad else (D(1) if ptype in ("P", "H") else D(2))
        return sec, base, rate, q2(base * rate / 100)
    thr = D("30000") if date_ < date(2025, 4, 1) else D("50000")
    if cum_after <= thr and single <= thr:
        return None, base, D(0), D(0)
    rate = D(2) if sec == "194J(a)" else D(10)
    if s["lower_tds_rate"] and date_.isoformat() <= s["lower_tds_valid_to"] and cum_after <= D(s["lower_tds_limit"]):
        rate = D(s["lower_tds_rate"])
    if pan_bad:
        rate = D(20)
    return sec, base, rate, q2(base * rate / 100)


def bank_rate(d, rates):
    r = rates[0][1]
    for eff, v in rates:
        if eff <= d:
            r = v
    return r


def finalize(sim):
    R = sim.R
    T = sim.T
    terms = sim.terms
    sup = sim.sup
    rates = [(date(2024, 11, 1), D("6.75")), (date(2025, 2, 7), D("6.50")), (date(2025, 4, 9), D("6.25")), (date(2025, 6, 6), D("5.75")),
             (date(2025, 12, 5), D("5.50")), (date(2026, 10, 7), D("5.75"))]
    pays = [r for r in T["_pay"]]
    advs = [a for a in T["_adv"]]
    inv_by_no = {r["no"]: r for r in T["invoice"]}

    # payment dates: weekly run on Thursday (previous working day if a holiday)
    def run_on_or_before(d):
        d2 = d
        while d2.weekday() != 3:
            d2 -= timedelta(days=1)
        return sim.cal.prev_working(d2, "HO-PUN")

    def run_after(d):
        d2 = d
        while d2.weekday() != 3:
            d2 += timedelta(days=1)
        return sim.cal.next_working(d2, "HO-PUN")

    def sec_label(sec, d):
        return SECTION_NEW[sec] if d >= date(2026, 4, 1) else sec

    def quarter_of(d):
        return f"{fy(d)} Q{(d.month - 4) % 12 // 3 + 1}"

    # ------------------------------------------------------------------ advances (30% advance suppliers)
    adv_by_po = {}
    first_inv = {}
    for p in pays:
        if not p["cancelled"] and (p["po_no"] not in first_inv or p["received_dt"] < first_inv[p["po_no"]]):
            first_inv[p["po_no"]] = p["received_dt"]
    for a in advs:
        a["pay_date"] = run_after(a["ready_dt"].date() + timedelta(days=R.randint(0, 2)))
        fi = first_inv.get(a["po_no"])
        if fi is not None and a["pay_date"] >= fi.date() - timedelta(days=1):
            cand = run_on_or_before(fi.date() - timedelta(days=2))
            if cand >= a["ready_dt"].date():
                a["pay_date"] = cand
            else:
                a["skip"] = True
    advs = [a for a in advs if not a.get("skip")]
    for a in advs:
        a["rem_gross"], a["rem_taxable"] = a["gross"], a["taxable"]
        a["_isadv"] = True
        adv_by_po[a["po_no"]] = a
    # ------------------------------------------------------------------ TDS in booking order (invoices and advances together), due dates
    events = [("inv", p, p["received_dt"]) for p in pays if not p["cancelled"]] + [("adv", a, dt_at(a["pay_date"], 12)) for a in advs]
    events.sort(key=lambda e: e[2])
    cum = defaultdict(lambda: D(0))
    tds_rows = []
    for kind, p, when in events:
        s = sup[p["supplier_code"]]
        key = (p["supplier_code"], fy(when.date()), section_of(p["category"], p["hsn"]))
        if kind == "adv":
            probe = dict(category=p["category"], hsn=p["hsn"], taxable=p["taxable"], po_taxable=p["po_taxable"], received_dt=when)
            sec, base, rate, amt = tds_for(sim, probe, s, cum[key])
            cum[key] += p["taxable"]
            p["tds"], p["_sec"], p["_base"], p["_rate"] = amt, sec, base, rate
            p["net"] = p["gross"] - amt
            continue
        adv = adv_by_po.get(p["po_no"])
        used_g = used_t = D(0)
        if adv is not None and adv["rem_gross"] > 0 and adv["pay_date"] <= p["received_dt"].date():
            used_g = min(adv["rem_gross"], p["total"])
            used_t = min(adv["rem_taxable"], p["taxable"])
            adv["rem_gross"] -= used_g
            adv["rem_taxable"] -= used_t
        p["adv_used"], p["adv"] = used_g, (adv if used_g > 0 else None)
        eff = dict(p, taxable=p["taxable"] - used_t)
        sec, base, rate, amt = tds_for(sim, eff, s, cum[key])
        cum[key] += eff["taxable"]
        p["tds"] = amt
        if sec:
            tds_rows.append(dict(invoice_no=p["invoice_no"], payment_no="", section=sec_label(sec, p["received_dt"].date()), base=money(base), rate=str(rate),
                                 amount=money(amt), booked_on=iso(p["received_dt"].date()), quarter=quarter_of(p["received_dt"].date()), _t=p["received_dt"]))
        tm = terms[s["payment_terms_code"]]
        base_date = p["inv_date"] if tm["baseline"] == "invoice_date" else p["accepted_on"]
        due = base_date + timedelta(days=int(tm["days"]))
        if s["msme_category"] in ("micro", "small"):
            if s["msme_agreement"] == "true":
                due = min(due, p["accepted_on"] + timedelta(days=min(45, int(s["agreed_payment_days"]))))
            else:
                due = p["accepted_on"] + timedelta(days=15)
        p["due"] = due
        if p["invoice_no"] in inv_by_no:
            inv_by_no[p["invoice_no"]]["due_date"] = iso(due)
    # ------------------------------------------------------------------ payment dates for invoices
    for p in pays:
        if p["cancelled"] or p.get("blocked_forever"):
            continue
        s = sup[p["supplier_code"]]
        ready = p["ready_dt"].date()
        want = run_on_or_before(p["due"])
        if want < ready:
            want = run_after(ready)
        msme = s["msme_category"] in ("micro", "small")
        late_by = 0
        if msme:
            if R.bern(0.08):
                late_by = R.randint(3, 40)
        elif R.bern(0.25):
            late_by = R.choice([3, 7, 7, 14])
        pdate = run_after(want + timedelta(days=late_by)) if late_by else want
        p["pay_date"] = pdate
        net = p["total"] - p["tds"] - p["credit"] - p["adv_used"]
        p["net"] = max(net, D(0))
        interest = D(0)
        if msme and pdate > p["due"]:
            days = (pdate - p["due"]).days
            r = bank_rate(p["due"], rates) * 3 / 100
            interest = q2(p["total"] * ((1 + r / 12) ** (D(days) / D("30.4")) - 1))
            sim.case("msme_late_payment", p["invoice_no"], f"Paid {days} days after the statutory MSME due date", f"Interest of about Rs {interest} is payable", dt_at(pdate))
            if days > 3:
                sim.email("in", s, "U004", dt_at(p["due"] + timedelta(days=R.randint(2, 6)), 11), p["invoice_no"], "invoice", "payment_followup")
        p["interest"] = interest
    # ------------------------------------------------------------------ payment runs and payments
    by_run = defaultdict(list)
    for p in pays:
        if "pay_date" in p:
            by_run[p["pay_date"]].append(p)
    for a in advs:
        by_run[a["pay_date"]].append(a)
    runs, pay_rows, alloc, bank, adv_adj = [], [], [], [], []
    run_no_n = 0
    pay_n = 0
    for d in sorted(by_run):
        batch = sorted(by_run[d], key=lambda x: 0 if x.get("_isadv") else 1)
        total_run = sum((x["net"] for x in batch), D(0))
        run_no_n += 1
        rno = f"PR-{fy4(d)}-{run_no_n:04d}"
        appr = "U002" if total_run > D("5000000") else "U003"
        appr = "U002"
        runs.append(dict(no=rno, run_date=iso(d), company_bank_account="CBA-01", proposed_by="U004", approved_by=appr, approved_at=ts(dt_at(d - timedelta(days=1), 16.5)),
                         status="paid", _t=dt_at(d, 12)))
        for stp, who, hh in ((1, "U003", 15.0), (2, "U002", 16.5)):
            T["approval_event"].append(dict(document="payment_run", document_no=rno, step=stp, approver=who, on_behalf_of="", action="approved",
                                            at=ts(dt_at(d - timedelta(days=1), hh)), comment="", rule_code="PAY-01", _t=dt_at(d - timedelta(days=1), hh)))
        for x in batch:
            pay_n += 1
            pno = f"PAY-{fy4(d)}-{pay_n:06d}"
            mode = "rtgs" if x["net"] > 200000 else "neft"
            utr = f"{R.choice(['ICIC', 'HDFC'])}{d.strftime('%y%m%d')}{R.randint(10000000, 99999999)}"
            if x.get("_isadv"):
                pay_rows.append(dict(no=pno, run_no=rno, type="advance", supplier_code=x["supplier_code"], po_no=x["po_no"], amount=money(x["net"]),
                                     tds_amount=money(x["tds"]), msme_interest="0.00", mode=mode, reference=x["po_no"], utr=utr, paid_on=iso(d),
                                     remittance_email_ref="", _t=dt_at(d, 14)))
                x["pno"] = pno
                if x["_sec"]:
                    tds_rows.append(dict(invoice_no="", payment_no=pno, section=sec_label(x["_sec"], d), base=money(x["_base"]), rate=str(x["_rate"]),
                                         amount=money(x["tds"]), booked_on=iso(d), quarter=quarter_of(d), _t=dt_at(d, 14)))
                bank.append(dict(account="CBA-01", value_date=iso(d + timedelta(days=R.choice([0, 0, 1]))), amount=money(-x["net"]), utr=utr,
                                 reference=x["po_no"], narration=f"ADVANCE {sup[x['supplier_code']]['trade_name'][:20].upper()}", matched_payment_no=pno,
                                 _t=dt_at(d, 15)))
                continue
            pay_rows.append(dict(no=pno, run_no=rno, type="invoice", supplier_code=x["supplier_code"], po_no=x["po_no"], amount=money(x["net"]),
                                 tds_amount=money(x["tds"]), msme_interest=money(x["interest"]), mode=mode, reference=f"{x['invoice_no']}", utr=utr,
                                 paid_on=iso(d), remittance_email_ref="", _t=dt_at(d, 14)))
            alloc.append(dict(payment_no=pno, invoice_no=x["invoice_no"], amount=money(x["total"] - x["tds"] - x["adv_used"]), _t=dt_at(d, 14)))
            if x["adv_used"] > 0 and x["adv"] is not None and x["adv"].get("pno"):
                adv_adj.append(dict(advance_payment_no=x["adv"]["pno"], invoice_no=x["invoice_no"], amount=money(x["adv_used"]), _t=dt_at(d, 14)))
            if x["credit"]:
                alloc.append(dict(payment_no=pno, invoice_no=x["credit_no"], amount=money(-x["credit"]), _t=dt_at(d, 14)))
            bank.append(dict(account="CBA-01", value_date=iso(d + timedelta(days=R.choice([0, 0, 1]))), amount=money(-x["net"]), utr=utr,
                             reference=x["invoice_no"], narration=f"NEFT/RTGS {sup[x['supplier_code']]['trade_name'][:22].upper()}", matched_payment_no=pno,
                             _t=dt_at(d, 15)))
            p_inv = inv_by_no.get(x["invoice_no"])
            if p_inv is not None:
                p_inv["_paid_on"] = d
    # a little unmatched bank noise
    d = date(2024, 11, 30)
    while d <= CUTOFF:
        bank.append(dict(account="CBA-01", value_date=iso(d), amount=money(R.uniform(-900, -120)), utr="", reference="BANKCHG", narration="BANK CHARGES",
                         matched_payment_no="", _t=dt_at(d, 23)))
        d = sim.cal.add_working(d + timedelta(days=28), 0, "HO-PUN")
    # ------------------------------------------------------------------ TDS deposit register (challans): by the 7th of the next month, a few late
    chal = defaultdict(lambda: D(0))
    for tr in tds_rows:
        code = "194Q" if "194Q" in tr["section"] or "8(ii)" in tr["section"] else ("194C" if "194C" in tr["section"] or "6(i)" in tr["section"] else "194J")
        chal[(tr["booked_on"][:7], code)] += D(tr["amount"])
    challans = []
    used_ch = set()
    cn = 0
    for (period, code), amount in sorted(chal.items()):
        if amount <= 0:
            continue
        y, m = int(period[:4]), int(period[5:])
        due7 = date(y + (m == 12), m % 12 + 1, 7)
        late = R.bern(0.05)
        paid_on = sim.cal.next_working(due7 + timedelta(days=R.randint(5, 20) if late else R.randint(-3, 0)), "HO-PUN")
        cn += 1
        chn = f"{R.randint(10000, 99999)}"
        while chn in used_ch:
            chn = f"{R.randint(10000, 99999)}"
        used_ch.add(chn)
        challans.append(dict(period=period, section=code, amount=money(amount), paid_on=iso(paid_on), bsr_code="0510308", challan_no=chn,
                             _t=dt_at(paid_on, 13)))
        if late:
            sim.case("tds_late_deposit", f"{period}/{code}", f"TDS for {period} deposited on {paid_on.isoformat()}, after the 7th", "Interest for late deposit is due", dt_at(paid_on))
    T["tds_entry"], T["payment_run"], T["payment"], T["payment_allocation"], T["bank_statement_line"] = tds_rows, runs, pay_rows, alloc, bank
    T["advance_adjustment"], T["tds_challan"] = adv_adj, challans
    # ------------------------------------------------------------------ cut-off filter
    tables = {}
    for name, rows in T.items():
        if name.startswith("_"):
            continue
        tables[name] = [r for r in rows if r["_t"] <= CUT_DT]
    # drop children whose parent was cut
    keep_inv = {r["no"] for r in tables["invoice"]}
    tables["invoice_line"] = [r for r in tables["invoice_line"] if r["invoice_no"] in keep_inv]
    keep_pay = {r["no"] for r in tables["payment"]}
    tables["payment_allocation"] = [r for r in tables["payment_allocation"] if r["payment_no"] in keep_pay]
    for r in tables["tds_entry"]:
        pass
    # events that happen after the cut-off are not part of the data: blank them and let the statuses follow
    for po in tables["po"]:
        if po["approved_at"] > CUT_S:
            po["approved_at"] = ""
            po["sent_at"] = ""
        elif po["sent_at"] > CUT_S:
            po["sent_at"] = ""
    for r in tables["rfq"]:
        if r["sent_at"] > CUT_S:
            r["sent_at"] = ""
    for r in tables["rfq_supplier"]:
        for col in ("invited_at", "responded_at"):
            if r[col] and r[col] > CUT_S:
                r[col] = ""
    # statuses
    paid_inv = {a["invoice_no"] for a in tables["payment_allocation"]}
    ready = {p["invoice_no"]: p["ready_dt"] for p in pays}
    for r in tables["invoice"]:
        if r["status"] == "cancelled":
            continue
        if r["type"] == "credit_note":
            r["status"] = "paid" if r["no"] in paid_inv else "approved"
        elif r["no"] in paid_inv:
            r["status"] = "paid"
        elif r["status"] == "approved" and ready.get(r["no"], dt_at(CUTOFF)) > CUT_DT:
            r["status"] = "received"
        elif r["status"] == "approved" and r["no"] in ready:
            r["status"] = "approved"
    # PO / lines
    gr_acc = defaultdict(lambda: D(0))
    for g in tables["gr_line"]:
        gr_acc[(g["po_no"], g["po_line_no"])] += D(g["qty_accepted"])
    for sr in tables["service_receipt"]:
        gr_acc[(sr["po_no"], sr["po_line_no"])] += D(1)
    inv_qty = defaultdict(lambda: D(0))
    inv_status = {r["no"]: r["status"] for r in tables["invoice"]}
    for l in tables["invoice_line"]:
        if inv_status.get(l["invoice_no"]) not in ("cancelled",) and not l["description"].startswith("Credit note"):
            inv_qty[(l["po_no"], l["po_line_no"])] += D(l["qty"])
    po_lines = defaultdict(list)
    for pl in tables["po_line"]:
        po_lines[pl["po_no"]].append(pl)
    for pl in tables["po_line"]:
        key = (pl["po_no"], pl["line_no"])
        item_kind = sim.items[pl["item_code"]]["kind"]
        if item_kind == "goods":
            pl["received_qty"] = qty_s(gr_acc[key])
        else:
            pl["received_qty"] = pl["qty"] if gr_acc[key] > 0 else "0.000"
        pl["invoiced_qty"] = qty_s(inv_qty[key])
        full = D(pl["received_qty"]) >= D(pl["qty"]) - D("0.0005")
        pl["status"] = "cancelled" if False else ("received" if full else ("part_received" if D(pl["received_qty"]) > 0 else "open"))
    inv_paid_by_po = defaultdict(list)
    for r in tables["invoice"]:
        pass
    inv_po = defaultdict(set)
    for l in tables["invoice_line"]:
        inv_po[l["po_no"]].add(l["invoice_no"])
    ack = {e["po_no"] for e in tables["po_event"] if e["type"] in ("acknowledged", "date_change", "qty_change", "price_change")}
    for po in tables["po"]:
        if po["status"] == "cancelled":
            continue
        pls = po_lines[po["no"]]
        recv = sum(D(pl["received_qty"]) for pl in pls)
        full = all(pl["status"] == "received" for pl in pls)
        if not po["approved_at"]:
            po["status"] = "draft"
        elif not po["sent_at"]:
            po["status"] = "approved"
        elif po["no"] not in ack and recv == 0:
            po["status"] = "sent"
        elif full:
            invs = [r for r in tables["invoice"] if r["no"] in inv_po[po["no"]] and r["status"] != "cancelled"]
            po["status"] = "closed" if invs and all(r["status"] == "paid" for r in invs) else "received"
        elif recv > 0:
            po["status"] = "part_received"
        else:
            po["status"] = "acknowledged"
    po_by_req = defaultdict(list)
    for pl in tables["po_line"]:
        if pl["request_no"]:
            po_by_req[(pl["request_no"], int(pl["request_line_no"]))].append(pl)
    po_status = {p["no"]: p["status"] for p in tables["po"]}
    rfq_req = {(l["request_no"], int(l["request_line_no"])) for l in tables["rfq_line"]}
    for rl in tables["request_line"]:
        if rl["status"] == "cancelled":
            continue
        key = (rl["request_no"], int(rl["line_no"]))
        rl["status"] = "ordered" if po_by_req.get(key) else ("sourcing" if key in rfq_req else "open")
    lines_by_req = defaultdict(list)
    for rl in tables["request_line"]:
        lines_by_req[rl["request_no"]].append(rl)
    for rq in tables["request"]:
        if rq["status"] in ("rejected", "cancelled"):
            continue
        ls = lines_by_req[rq["no"]]
        if not any(e["document"] == "request" and e["document_no"] == rq["no"] and e["action"] in ("approved", "auto_approved") for e in tables["approval_event"]):
            rq["status"] = "submitted"
        elif ls and all(po_status.get(pl["po_no"]) == "closed" for rl in ls for pl in po_by_req.get((rl["request_no"], int(rl["line_no"])), [])) \
                and all(po_by_req.get((rl["request_no"], int(rl["line_no"]))) for rl in ls):
            rq["status"] = "closed"
        else:
            rq["status"] = "approved"
    awarded = {a["rfq_no"] for a in tables["award"]}
    for r in tables["rfq"]:
        r["status"] = "awarded" if r["no"] in awarded else ("sent" if r["sent_at"] else "draft")
    # stock opening and stockout truth
    so = defaultdict(list)
    for item, loc, d, q in sim.stockouts:
        so[(item, loc)].append(iso(d))
    for tr in sim.t["truth_item_demand"]:
        tr["stockout_periods"] = ";".join(so.get((tr["item_code"], tr["location_code"]), [])[:60])
    # FY trap cases
    seen = defaultdict(set)
    for r in tables["invoice"]:
        if r["type"] == "invoice" and r["status"] != "cancelled":
            seen[(r["supplier_code"], r["supplier_invoice_no"])].add(r["fy"])
    n = 0
    for (sc, no), fys in seen.items():
        if len(fys) > 1:
            n += 1
            tables["truth_case"].append(dict(case_id=f"CASE-FY{n:03d}", type="fy_reuse_not_duplicate", documents=f"{sc};{no}", cause="Supplier restarts invoice numbers every financial year",
                                             expected_result="NOT a duplicate: same number in a different financial year", planted_on="", _t=dt_at(START)))
    return tables


_NUM_RE = re.compile(r"\b(?:REQ|RFQ|PO|GRN|SRN|PI|PR|PAY|RTV|DN)-(?:[A-Z]{3}-)?\d{4}-\d{4,6}\b")


def renumber(tables, rows_time_key="_t"):
    """Re-sequence document numbers chronologically within each series and rewrite every reference."""
    docs = [("request", "no"), ("rfq", "no"), ("po", "no"), ("goods_receipt", "no"), ("service_receipt", "no"), ("invoice", "no"),
            ("payment_run", "no"), ("payment", "no"), ("return_to_vendor", "no"), ("return_to_vendor", "debit_note_no")]
    mapping = {}
    for tname, col in docs:
        rows = sorted(tables[tname], key=lambda r: (r["_t"], r[col]))
        counters = defaultdict(int)
        for r in rows:
            old = r[col]
            m = re.match(r"^((?:REQ|RFQ|PO|GRN|SRN|PI|PR|PAY|RTV|DN)-(?:[A-Z]{3}-)?\d{4}-)(\d+)$", old)
            if not m:
                continue
            width = len(m.group(2))
            counters[m.group(1)] += 1
            new = f"{m.group(1)}{counters[m.group(1)]:0{width}d}"
            mapping[old] = new
    qt_re = re.compile(r"\bQT-(\d{4}-\d{5})-(\d+)\b")

    def sub(v):
        if isinstance(v, str) and v:
            v = qt_re.sub(lambda m: "QT-" + mapping.get("RFQ-" + m.group(1), "RFQ-" + m.group(1))[4:] + "-" + m.group(2), v)
            return _NUM_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), v)
        return v
    for name, rows in tables.items():
        for r in rows:
            for k, v in list(r.items()):
                if isinstance(v, str):
                    r[k] = sub(v)
    return mapping
