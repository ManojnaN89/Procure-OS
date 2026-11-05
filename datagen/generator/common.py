"""Shared helpers for the data generator: money, dates, calendar, random numbers, CSV I/O.

No third-party packages are required except PyYAML (only to read company.yaml).
"""
import csv
import hashlib
import math
import random
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # datagen/
REPO = ROOT.parent
REF = REPO / "data" / "reference"
CAT = ROOT / "catalog"

START = date(2024, 11, 1)    # first day of history
CUTOFF = date(2026, 10, 31)  # "today" in the demo; later events are not recorded
IST = "+05:30"


# ------------------------------------------------------------------ money
def D(x):
    return x if isinstance(x, Decimal) else Decimal(str(x))


def q2(x):
    return D(x).quantize(Decimal("0.01"), ROUND_HALF_UP)


def q3(x):
    return D(x).quantize(Decimal("0.001"), ROUND_HALF_UP)


def money(x):
    return f"{q2(x):.2f}"


def qty_s(x):
    return f"{q3(x):.3f}"


# ------------------------------------------------------------------ dates
def fy(d):
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{str(y + 1)[2:]}"


def fy4(d):
    y = d.year if d.month >= 4 else d.year - 1
    return f"{str(y)[2:]}{str(y + 1)[2:]}"


def fy_start(d):
    return date(d.year if d.month >= 4 else d.year - 1, 4, 1)


def ts(d, hour=10, minute=0):
    """ISO timestamp in India time. d is a date or a datetime."""
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%dT%H:%M:00") + IST
    return f"{d.isoformat()}T{hour:02d}:{minute:02d}:00{IST}"


def iso(d):
    return d.isoformat() if d else ""


def months_between(a, b):
    return (b.year - a.year) * 12 + (b.month - a.month)


def month_start(d):
    return date(d.year, d.month, 1)


def add_months(d, n):
    m = d.month - 1 + n
    return date(d.year + m // 12, m % 12 + 1, 1)


def daterange(a, b):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


# ------------------------------------------------------------------ random
class Rng(random.Random):
    def bern(self, p):
        return self.random() < p

    def poisson(self, lam):
        if lam <= 0:
            return 0
        if lam > 40:
            return max(0, int(round(self.gauss(lam, math.sqrt(lam)))))
        L, k, p = math.exp(-lam), 0, 1.0
        while True:
            k += 1
            p *= self.random()
            if p <= L:
                return k - 1

    def lognorm(self, mean_mult=1.0, sd=0.2):
        """Multiplicative noise with the given mean (about) and log-sd."""
        return mean_mult * math.exp(self.gauss(-0.5 * sd * sd, sd))

    def weighted(self, items, weights):
        return self.choices(items, weights=weights, k=1)[0]


def sub_rng(seed, name):
    h = int(hashlib.sha256(f"{seed}:{name}".encode()).hexdigest(), 16) % (2 ** 32)
    return Rng(h)


def stable_hash(s, mod=1_000_000):
    return int(hashlib.sha256(s.encode()).hexdigest(), 16) % mod


# ------------------------------------------------------------------ csv
def read_csv(path, enc="utf-8"):
    with open(path, encoding=enc, newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    fields = fields or list(rows[0].keys())
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------ calendar
class Calendar:
    """Working-day logic by location (weekly off days plus central and state holidays)."""

    def __init__(self, locations):
        self.loc = {l["code"]: l for l in locations}
        self.hol = {"central": set()}
        for f, col in [("holidays_central_2024_2025.csv", None), ("holidays_2026_central_gazetted.csv", None)]:
            for r in read_csv(REF / f):
                if r.get("type", "closed") in ("closed", "closed_additional"):
                    self.hol["central"].add(date.fromisoformat(r["date"]))
        for r in read_csv(REF / "holidays_state_MH_GJ_KA_2024_2026.csv"):
            self.hol.setdefault(r["state_code"], set()).add(date.fromisoformat(r["date"]))

    def off_days(self, loc):
        wd = self.loc[loc]["working_days"]
        return {5, 6} if wd == "Mon-Fri" else {6}

    def is_holiday(self, d, loc):
        l = self.loc[loc]
        if d in self.hol["central"]:
            return True
        return d in self.hol.get(l["state_code"], set())

    def is_working(self, d, loc):
        return d.weekday() not in self.off_days(loc) and not self.is_holiday(d, loc)

    def next_working(self, d, loc):
        while not self.is_working(d, loc):
            d += timedelta(days=1)
        return d

    def prev_working(self, d, loc):
        while not self.is_working(d, loc):
            d -= timedelta(days=1)
        return d

    def add_working(self, d, n, loc):
        step = 1 if n >= 0 else -1
        k = abs(n)
        while k > 0:
            d += timedelta(days=step)
            if self.is_working(d, loc):
                k -= 1
        return d


# ------------------------------------------------------------------ numbering
class Numbering:
    """Document numbers per series (document type, location, financial year)."""

    def __init__(self, series_rows, loc_short):
        self.rules = {r["document_type"]: r for r in series_rows}
        self.loc_short = loc_short
        self.counters = {}

    def next(self, doc_type, d, loc_code=None):
        r = self.rules[doc_type]
        loc = self.loc_short.get(loc_code, "") if r["scope"] == "location" else ""
        key = (doc_type, loc, fy4(d))
        self.counters[key] = self.counters.get(key, 0) + 1
        return r["format"].format(prefix=r["prefix"], loc=loc, fy4=fy4(d), seq=self.counters[key]).replace("--", "-")
