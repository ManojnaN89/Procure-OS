"""The approval scenarios of block 4 (task 0.2, approval bound to the version; task 1.2, the order approval code):
`approval_scenarios:` of a company profile, and what every version of each scenario's order must be, worked out here
from the profile alone (never from the backend).

File written: truth/truth_approval_binding.csv, one row per scenario and order version.

The rules, as the plan and the block 4 spec say them:
- an order line: taxable = qty x price; inside one state CGST = SGST = taxable x half the GST rate, each rounded half up
  to paise; total = taxable + CGST + SGST;
- the order's band is the `po` rule whose range holds the total; a band with no approver approves only a touchless order
  (by the rule, approver `rule:<code>`); any other order there goes up to the next band's person;
- a change is measured from the last approved version: price up, quantity up, a need-by moved more than
  `supplier_date_days` either way, a total above the approved band's top, a higher total; any of these is material and
  is approved again; a change while the order waits for approval restarts it too;
- a supplier's price is judged from the first approved price: within when new - base <= base x `supplier_price_pct` /
  100 (exact); out of it, the price variance is (new - base) x qty, half up to paise;
- approving an order needs a one-time code when its total is above `approval_otp_above`.
"""

import csv
import io
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from master_files import write_csv

CENT = Decimal("0.01")
TRUTH = [
    "scenario", "version", "qty", "unit_price", "need_by", "taxable", "cgst", "sgst", "igst", "total", "rule",
    "approver", "material", "reasons", "base_price", "within_tolerance", "variance_value", "needs_code",
]  # fmt: skip


def _paise(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)


@dataclass(frozen=True)
class State:
    qty: Decimal
    price: Decimal
    need_by: date

    @property
    def taxable(self) -> Decimal:
        return _paise(self.qty * self.price)


@dataclass(frozen=True)
class Band:
    code: str
    low: Decimal
    high: Decimal
    roles: tuple[str, ...]


class Company:
    """What the scenarios need from the profile: the order bands, who holds a role, the tolerances and the code limit."""

    def __init__(self, data: dict[str, Any]) -> None:
        rows = csv.DictReader(io.StringIO(data["approval_rules"]))
        self.bands = sorted(
            (
                Band(r["rule_code"], Decimal(r["min_value"]), Decimal(r["max_value"]),
                     () if r["approver_chain"] == "auto" else tuple(r["approver_chain"].split(">")))
                for r in rows
                if r["document"] == "po"
            ),
            key=lambda b: b.low,
        )  # fmt: skip
        designation = {role: value for role, how, value in data["approver_roles"] if how == "designation"}
        self.holder = {
            role: next(p["code"] for p in data["people"] if p["designation"] == value)
            for role, value in designation.items()
        }
        values = {k: v for k, v, *_ in data["tolerances"][0]["values"]}
        self.price_pct = Decimal(values["supplier_price_pct"])
        self.date_days = int(values["supplier_date_days"])
        settings = {k: v for k, v, *_ in data["settings"]}
        self.code_above = Decimal(settings["approval_otp_above"])

    def band(self, total: Decimal) -> Band:
        return next(b for b in self.bands if b.low <= total <= b.high)

    def top(self, band: Band) -> Decimal | None:
        """The band's top; None for the top band."""
        return band.high if band is not self.bands[-1] else None

    def approver(self, band: Band) -> tuple[Band, str]:
        """The band a person approves in (an automatic band goes up a band) and that person."""
        while not band.roles:
            band = self.bands[self.bands.index(band) + 1]
        return band, self.holder[band.roles[-1]]


def _reasons(base: State, base_total: Decimal, base_top: Decimal | None, rule: str, new: State, total: Decimal,
             company: Company) -> list[str]:  # fmt: skip
    reasons = []
    if new.price > base.price:
        reasons.append("line 1: price up")
    if new.qty > base.qty:
        reasons.append("line 1: quantity up")
    moved = abs((new.need_by - base.need_by).days)
    if moved > company.date_days:
        reasons.append(
            f"line 1: date moved {moved} days from the approved {base.need_by:%d %b %Y}, "
            f"more than the {company.date_days} accepted"
        )
    if base_top is not None and total > base_top:
        reasons.append(f"the total is above the {rule} band (up to {base_top})")
    if total > base_total:
        reasons.append("the total went up")
    return reasons


def scenario_rows(sc: dict[str, Any], company: Company) -> list[dict[str, str]]:
    """Every version of the scenario's order, in turn."""
    gst = Decimal(str(sc.get("gst", "18")))
    state = State(Decimal("0"), Decimal("0"), date.fromisoformat(sc["need_by"]))
    first_price: Decimal | None = None
    baseline: tuple[State, Decimal, Decimal | None, str] | None = None
    rows: list[dict[str, str]] = []
    for n, v in enumerate(sc["versions"], start=1):
        new = replace(
            state,
            qty=Decimal(str(v.get("qty", state.qty))),
            price=Decimal(str(v.get("price", v.get("asked", state.price)))),
            need_by=date.fromisoformat(v["need_by"]) if "need_by" in v else state.need_by,
        )
        half = _paise(new.taxable * gst / 2 / 100)
        total = new.taxable + half + half
        band = company.band(total)
        reasons = _reasons(*baseline, new, total, company) if baseline and n > 1 else []
        if v.get("touchless") and n == 1:
            approver, approving = f"rule:{band.code}", band
        else:
            approving, approver = company.approver(band)
        base = first_price if first_price is not None else state.price
        within, variance = "", ""
        if "asked" in v:
            ok = new.price - base <= base * company.price_pct / 100
            within = "true" if ok else "false"
            variance = "" if ok else str(_paise((new.price - base) * new.qty))
        rows.append(
            {
                "scenario": sc["name"], "version": str(n), "qty": f"{new.qty}", "unit_price": f"{new.price}",
                "need_by": new.need_by.isoformat(), "taxable": str(new.taxable), "cgst": str(half), "sgst": str(half),
                "igst": "0.00", "total": str(total), "rule": approving.code, "approver": approver,
                "material": "true" if reasons else "false", "reasons": "; ".join(reasons),
                "base_price": "" if first_price is None else str(first_price), "within_tolerance": within,
                "variance_value": variance, "needs_code": "true" if total > company.code_above else "false",
            }
        )  # fmt: skip
        if v.get("approved"):
            first_price = new.price if first_price is None else first_price
            baseline = (new, total, company.top(approving), approving.code)
        state = new
    return rows


def write_approval(profile_data: dict[str, Any], out: Path) -> dict[str, int]:
    """truth/truth_approval_binding.csv from the profile's `approval_scenarios:` (nothing when it has none)."""
    scenarios = profile_data.get("approval_scenarios") or []
    if not scenarios:
        return {}
    company = Company(profile_data)
    rows = [row for sc in scenarios for row in scenario_rows(sc, company)]
    (out / "truth").mkdir(parents=True, exist_ok=True)
    write_csv(out / "truth" / "truth_approval_binding.csv", TRUTH, list(rows))
    return {"approval_scenarios": len(scenarios), "approval_versions": len(rows)}


def check(out: Path) -> list[str]:
    """Each row adds up: taxable = qty x price, CGST = SGST = half the tax, total = the sum."""
    path = out / "truth" / "truth_approval_binding.csv"
    if not path.exists():
        return []
    problems = []
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            where = f"truth_approval_binding {r['scenario']} v{r['version']}"
            taxable, cgst, sgst = Decimal(r["taxable"]), Decimal(r["cgst"]), Decimal(r["sgst"])
            if _paise(Decimal(r["qty"]) * Decimal(r["unit_price"])) != taxable:
                problems.append(f"{where}: taxable is not qty x price")
            if cgst != sgst or taxable + cgst + sgst + Decimal(r["igst"]) != Decimal(r["total"]):
                problems.append(f"{where}: the tax does not add up to the total")
            if r["material"] == "true" and not r["reasons"]:
                problems.append(f"{where}: material with no reason")
    return problems
