"""The purchase order version scenarios of block 7 (task 0.2 versions, D48): `version_scenarios:` of a company profile,
and what every version of each scenario's order must be, worked out here from the profile alone (never from the
backend).

File written: truth/truth_po_versions.csv, one row per scenario and order version.

The rules, as the plan and the block 7 spec say them:
- an order line: taxable = qty x price; the place of supply is the delivery place's state; a supplier in that state
  charges CGST = SGST = taxable x half the GST rate, each rounded half up to paise; one in another state charges
  IGST = taxable x the rate, rounded half up once; total = taxable + tax;
- a version is measured from the last approved one: price up, quantity up, another delivery place, other payment terms,
  a need-by moved more than `supplier_date_days` either way, a total above the approved band's top, a higher total; any
  of these is material and is approved again (Q19); delivery terms are not material (OP-2, interim);
- the approver is the band of the total; a band with no approver approves only a touchless order (by the rule); any
  other order, and one whose band's person made the change, goes up to the next band's person (C1);
- a version that is not material carries the approval of the version it is measured from.
"""

import csv
from dataclasses import dataclass, replace
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from approval_scenarios import Band, Company
from master_files import write_csv

CENT = Decimal("0.01")
TRUTH = [
    "scenario", "version", "location", "place", "qty", "unit_price", "price_record_version", "taxable", "cgst", "sgst",
    "igst", "total", "rule", "approver", "material", "reasons",
]  # fmt: skip


def _paise(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)


@dataclass(frozen=True)
class Version:
    location: str
    qty: Decimal
    price: Decimal
    price_record_version: str
    need_by: date
    terms: str
    delivery_terms: str

    @property
    def taxable(self) -> Decimal:
        return _paise(self.qty * self.price)


def _places(data: dict[str, Any]) -> dict[str, str]:
    """Each delivery place's state (the place of supply)."""
    return {str(loc["code"]): str(loc["state_code"]) for loc in [data["location"], *data.get("locations", [])]}


def _tax(taxable: Decimal, rate: Decimal, same_state: bool) -> tuple[Decimal, Decimal, Decimal]:
    if same_state:
        half = _paise(taxable * rate / 2 / 100)
        return half, half, Decimal("0.00")
    return Decimal("0.00"), Decimal("0.00"), _paise(taxable * rate / 100)


def _approver(company: Company, band: Band, makers: set[str]) -> tuple[Band, str]:
    """The band a person approves in and that person: never a band with no approver, never one of the makers."""
    while True:
        if band.roles and company.holder[band.roles[-1]] not in makers:
            return band, company.holder[band.roles[-1]]
        band = company.bands[company.bands.index(band) + 1]


def _reasons(base: Version, base_total: Decimal, base_top: Decimal | None, rule: str, new: Version, total: Decimal,
             date_days: int) -> list[str]:  # fmt: skip
    reasons = []
    if new.price > base.price:
        reasons.append("line 1: price up")
    if new.qty > base.qty:
        reasons.append("line 1: quantity up")
    if new.location != base.location:
        reasons.append("delivery location changed")
    if new.terms != base.terms:
        reasons.append("payment terms changed")
    moved = abs((new.need_by - base.need_by).days)
    if moved > date_days:
        reasons.append(
            f"line 1: date moved {moved} days from the approved {base.need_by:%d %b %Y}, more than the {date_days} accepted"
        )
    if base_top is not None and total > base_top:
        reasons.append(f"the total is above the {rule} band (up to {base_top})")
    if total > base_total:
        reasons.append("the total went up")
    return reasons


def scenario_rows(sc: dict[str, Any], company: Company, places: dict[str, str], states: dict[str, str]) -> list[dict[str, str]]:
    """Every version of the scenario's order, in turn."""
    gst = Decimal(str(sc.get("gst", "18")))
    supplier_state = states[sc["supplier"]]
    makers = {company.holder[r] for r in sc.get("makers", [])}
    state = Version("", Decimal(0), Decimal(0), "", date.fromisoformat(sc["need_by"]), "", "")
    baseline: tuple[Version, Decimal, Decimal | None, str, str] | None = None
    rows: list[dict[str, str]] = []
    for n, v in enumerate(sc["versions"], start=1):
        new = replace(
            state,
            location=str(v.get("location", state.location)),
            qty=Decimal(str(v.get("qty", state.qty))),
            price=Decimal(str(v.get("price", state.price))),
            price_record_version=str(v.get("price_record_version", state.price_record_version)),
            need_by=date.fromisoformat(v["need_by"]) if "need_by" in v else state.need_by,
            terms=str(v.get("terms", state.terms)),
            delivery_terms=str(v.get("delivery_terms", state.delivery_terms)),
        )
        place = places[new.location]
        cgst, sgst, igst = _tax(new.taxable, gst, place == supplier_state)
        total = new.taxable + cgst + sgst + igst
        reasons = _reasons(*baseline[:4], new, total, company.date_days) if baseline and n > 1 else []
        if baseline and n > 1 and not reasons:  # not material: the approval carries
            rule, approver = baseline[3], baseline[4]
        elif v.get("touchless") and n == 1:
            band = company.band(total)
            rule, approver = band.code, f"rule:{band.code}"
            baseline = (new, total, company.top(band), band.code, approver)
        else:
            approving, person = _approver(company, company.band(total), makers if n > 1 else set())
            rule, approver = approving.code, person
            baseline = (new, total, company.top(approving), approving.code, person)
        rows.append(
            {
                "scenario": sc["name"], "version": str(n), "location": new.location, "place": place, "qty": f"{new.qty}",
                "unit_price": f"{new.price}", "price_record_version": new.price_record_version,
                "taxable": str(new.taxable), "cgst": str(cgst), "sgst": str(sgst), "igst": str(igst), "total": str(total),
                "rule": rule, "approver": approver, "material": "true" if reasons else "false",
                "reasons": "; ".join(reasons),
            }
        )  # fmt: skip
        state = new
    return rows


def write_versions(profile_data: dict[str, Any], out: Path) -> dict[str, int]:
    """truth/truth_po_versions.csv from the profile's `version_scenarios:` (nothing when it has none)."""
    scenarios = profile_data.get("version_scenarios") or []
    if not scenarios:
        return {}
    company = Company(profile_data)
    places = _places(profile_data)
    states = {str(s["code"]): str(s["state"]) for s in profile_data["suppliers"]}
    rows = [row for sc in scenarios for row in scenario_rows(sc, company, places, states)]
    (out / "truth").mkdir(parents=True, exist_ok=True)
    write_csv(out / "truth" / "truth_po_versions.csv", TRUTH, list(rows))
    return {"version_scenarios": len(scenarios), "po_versions": len(rows)}


def check(out: Path) -> list[str]:
    """Each row adds up: taxable = qty x price; CGST = SGST with no IGST, or IGST alone; total = the sum."""
    path = out / "truth" / "truth_po_versions.csv"
    if not path.exists():
        return []
    problems = []
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            where = f"truth_po_versions {r['scenario']} v{r['version']}"
            taxable, cgst, sgst, igst = (Decimal(r[k]) for k in ("taxable", "cgst", "sgst", "igst"))
            if _paise(Decimal(r["qty"]) * Decimal(r["unit_price"])) != taxable:
                problems.append(f"{where}: taxable is not qty x price")
            if cgst != sgst or (igst and cgst) or taxable + cgst + sgst + igst != Decimal(r["total"]):
                problems.append(f"{where}: the tax does not add up to the total")
            if r["material"] == "true" and not r["reasons"]:
                problems.append(f"{where}: material with no reason")
    return problems
