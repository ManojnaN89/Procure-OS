"""Looks up the GST rate in force from 22 Sep 2025 for a goods HSN code, using the parsed official schedule
(data/reference/gst_goods_rates_2025-09-22.csv, from Notification 9/2025-Central Tax (Rate)).

Rules used: an entry matches when one of its included codes is a prefix of the item code (or the item code is a
prefix of it) and none of its excluded codes match. The longest matching code wins. Ambiguities are reported,
never guessed.
"""
import csv
import re
from pathlib import Path

REF = Path(__file__).resolve().parents[2] / "data" / "reference"


def _digits(s):
    return re.sub(r"\D", "", s)


def _parse_entry(raw):
    """Returns (include_prefixes, exclude_prefixes) for one hsn_raw cell."""
    raw = raw.replace("–", "-")
    excl = []
    # exclusions in [...] or (...) starting with except / other than
    def grab(m):
        excl.extend(_digits(x) for x in re.findall(r"\d[\d ]*\d|\d", m.group(1)))
        return " "
    main = re.sub(r"[\[(]\s*(?:[Ee]xcept|[Oo]ther than)\s*([^\])]*)[\])]", grab, raw)
    if re.search(r"(?i)any chapter|^\s*all\b", main) and not re.search(r"\d{4}", main):
        return [], excl
    inc = []
    # ranges: "3901 to 3913" -> 4-digit headings in range
    for a, b in re.findall(r"(\d{4})\s*to\s*(\d{4})", main):
        inc.extend(str(n) for n in range(int(a), int(b) + 1))
    main = re.sub(r"\d{4}\s*to\s*\d{4}", " ", main)
    for tok in re.split(r",|\bor\b|\band\b", main):
        d = _digits(tok)
        if len(d) >= 2:
            inc.append(d)
    return inc, excl


_entries = None


def entries():
    global _entries
    if _entries is None:
        _entries = []
        with open(REF / "gst_goods_rates_2025-09-22.csv", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                inc, exc = _parse_entry(r["hsn_raw"])
                _entries.append({"schedule": r["schedule"], "s_no": int(r["s_no"]), "inc": inc, "exc": exc,
                                 "igst": float(r["igst_rate"]), "desc": r["description"], "raw": r["hsn_raw"]})
    return _entries


def new_rate(hsn):
    """Returns (rate_percent or None, [matching entries]) for a goods HSN code."""
    code = _digits(hsn)
    hits = []
    for e in entries():
        best = 0
        for p in e["inc"]:
            if code.startswith(p) or p.startswith(code):
                best = max(best, min(len(p), len(code)))
        if not best:
            continue
        if any(code.startswith(x) or x.startswith(code) for x in e["exc"] if x):
            continue
        hits.append((best, e))
    if not hits:
        return None, []
    top = max(h[0] for h in hits)
    best = [h[1] for h in hits if h[0] == top]
    rates = {b["igst"] for b in best}
    return (rates.pop() if len(rates) == 1 else None), best
