"""TDS, due dates, payment-run days and chronological numbering, shared by the profile companies' history."""
import re
from collections import defaultdict
from datetime import date, timedelta

from common import D, fy, q2

SECTION_NEW = {"194Q": "393(1) Sl.8(ii) [194Q]", "194C": "393(1) Sl.6(i) [194C]", "194J(a)": "393(1) Sl.6(iii) [194J]", "194J(b)": "393(1) Sl.6(iii) [194J]"}


def tds_on(sec, base, single, cum_before, on, s):
    """Tax at source on one bill: (section, base, rate_pct, amount), or (None, base, 0, 0) when none is due.
    sec is the section ('194Q', '194C', '194J(a)', '194J(b)' or 'none'); single is the contract or order value the
    single-sum threshold is judged on; cum_before is what was already booked with this supplier under this section in
    the financial year; s is the supplier row (PAN, PAN status, lower-rate certificate)."""
    if sec == "none":
        return None, base, D(0), D(0)
    pan_bad = s["pan_status"] == "inoperative"
    cum_after = cum_before + base
    if sec == "194Q":
        thr = D("5000000")
        over = max(D(0), cum_after - max(thr, cum_before))
        rate = D("5") if pan_bad else D("0.1")
        return sec, over, rate, q2(over * rate / 100)
    if sec == "194C":
        if not (single > 30000 or cum_after > 100000):
            return None, base, D(0), D(0)
        rate = D("20") if pan_bad else (D(1) if s["pan"][3] in ("P", "H") else D(2))
        return sec, base, rate, q2(base * rate / 100)
    thr = D("30000") if on < date(2025, 4, 1) else D("50000")
    if cum_after <= thr and single <= thr:
        return None, base, D(0), D(0)
    rate = D(2) if sec == "194J(a)" else D(10)
    if s["lower_tds_rate"] and on.isoformat() <= s["lower_tds_valid_to"] and cum_after <= D(s["lower_tds_limit"]):
        rate = D(s["lower_tds_rate"])
    if pan_bad:
        rate = D(20)
    return sec, base, rate, q2(base * rate / 100)


def section_label(sec, d):
    """The section as the books name it on that date: the Income-tax Act 2025 numbering from 1 April 2026."""
    return SECTION_NEW[sec] if d >= date(2026, 4, 1) else sec


def quarter_of(d):
    return f"{fy(d)} Q{(d.month - 4) % 12 // 3 + 1}"


def due_date(term, s, inv_date, accepted_on):
    """When an invoice falls due: the payment terms from the invoice or acceptance date, and for a micro or small
    enterprise no later than the MSME limit (the agreed days, at most 45, else 15 days from acceptance)."""
    base_date = inv_date if term["baseline"] == "invoice_date" else accepted_on
    due = base_date + timedelta(days=int(term["days"]))
    if s["msme_category"] in ("micro", "small"):
        if s["msme_agreement"] == "true":
            due = min(due, accepted_on + timedelta(days=min(45, int(s["agreed_payment_days"]))))
        else:
            due = accepted_on + timedelta(days=15)
    return due


def run_on_or_before(d, cal, loc, weekday=3):
    """The weekly payment run on or before a date (the working day before, when the run day is a holiday)."""
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return cal.prev_working(d, loc)


def run_after(d, cal, loc, weekday=3):
    """The weekly payment run on or after a date (the next working day, when the run day is a holiday)."""
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return cal.next_working(d, loc)


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
