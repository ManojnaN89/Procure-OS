"""The period RFQ scenarios of block 5 (tasks 12.1 to 12.3): `period_scenarios:` of a company profile, and what each
line of each scenario must come to, worked out here from the profile and the generated dataset alone (never from the
backend).

File written: truth/truth_period_sourcing.csv, one row per scenario and line.

The rules, as the block 5 spec says them:
- the suggested quantity of a line is the larger of the open requirements of the item at the location needed inside
  the period (production order lines, MRP results and joiner kits of the dataset's inputs) and what was bought of it
  there in the 12 full calendar months before the month the RFQ is made, times the months the period touches, over 12;
  rounded up to a whole unit. What was bought is the history's own orders (`history/po.csv` and `po_line.csv`, by
  the day the order was made; orders not draft, submitted or cancelled, lines not cancelled);
- an awarded line: taxable = qty x price, half up to paise; inside one state CGST = SGST = taxable x half the GST rate,
  each half up; between states IGST = taxable x the rate; the award's total is the sum over its lines;
- the approver is the person of the `po` band whose range holds the total; an automatic band goes up to the next band
  with a person (an award is never automatic);
- an approved award makes one draft contract per supplier, numbered after the profile's contracts, with a ceiling of
  that supplier's taxable value;
- a scenario with `order:` is a later order on that contract: its band is the order's (the same rule).
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from approval_scenarios import Company
from master_files import read, write_csv

CENT = Decimal("0.01")
TRUTH = [
    "scenario", "line", "item", "location", "period_from", "period_to", "qty", "suggested_qty", "supplier",
    "net_price", "taxable", "cgst", "sgst", "igst", "total", "rule", "approver", "ceiling", "contract_code",
]  # fmt: skip


def _paise(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)


def _months(start: date, end: date) -> int:
    return (end.year * 12 + end.month) - (start.year * 12 + start.month) + 1


def _window(made: date) -> tuple[date, date]:
    """The 12 full calendar months before the month `made` is in."""
    first = made.replace(day=1)
    n = first.year * 12 + first.month - 1 - 12
    return date(n // 12, n % 12 + 1, 1), first - timedelta(days=1)


@dataclass(frozen=True)
class Places:
    """States of the locations and suppliers, and each item's unit, from the profile."""

    location_state: dict[str, str]
    supplier_state: dict[str, str]
    unit: dict[str, str]

    @classmethod
    def of(cls, data: dict[str, Any]) -> "Places":
        locs = [data["location"], *data.get("locations", [])]
        return cls(
            {str(x["code"]): str(x["state_code"]) for x in locs},
            {str(s["code"]): str(s["state"]) for s in data["suppliers"]},
            {str(i["code"]): str(i["unit"]) for i in data["items"]},
        )


def bought(out: Path, item: str, location: str, made: date) -> Decimal:
    """Ordered of the item for the location in the 12 months before the month the RFQ is made (the history's orders)."""
    start, end = _window(made)
    folder = out / "history"
    if not (folder / "po.csv").exists():
        return Decimal(0)
    orders = {
        o["no"]: o for o in read(folder / "po.csv")
        if o["status"] not in ("draft", "submitted", "cancelled") and o["ship_to_location"] == location
        and start <= date.fromisoformat(o["created_at"][:10]) <= end
    }  # fmt: skip
    return sum(
        (Decimal(r["qty"]) for r in read(folder / "po_line.csv")
         if r["po_no"] in orders and r["item_code"] == item and r["status"] != "cancelled"),
        Decimal(0),
    )  # fmt: skip


def needed(out: Path, item: str, location: str, start: date, end: date) -> Decimal:
    """Open requirements of the item at the location needed inside the period, from the dataset's ERP inputs."""
    inputs = out / "inputs"

    def rows(name: str) -> list[dict[str, str]]:
        return read(inputs / f"{name}.csv") if (inputs / f"{name}.csv").exists() else []

    total = Decimal(0)
    where = {o["order_no"]: o.get("location_code") or location for o in rows("production_order")}
    for r in rows("production_order_line"):
        if r["item_code"] == item and where.get(r["order_no"]) == location and start <= date.fromisoformat(r["need_by"]) <= end:
            total += Decimal(r["qty"])
    for r in rows("mrp_result"):
        if r["item_code"] == item and r["location_code"] == location and start <= date.fromisoformat(r["need_by"]) <= end:
            total += Decimal(r["qty"])
    kits = rows("joiner_kit")
    for p in rows("joiner_plan"):
        if p["location_code"] == location and start <= date.fromisoformat(p["start_date"]) <= end:
            total += sum((Decimal(k["qty"]) * int(p["count"]) for k in kits if k["kit"] == p["kit"] and k["item_code"] == item),
                         Decimal(0))  # fmt: skip
    return total


def suggested(out: Path, item: str, location: str, start: date, end: date, made: date) -> Decimal:
    history = bought(out, item, location, made) * _months(start, end) / 12
    return max(needed(out, item, location, start, end), history).to_integral_value(rounding=ROUND_CEILING)


def _tax(taxable: Decimal, rate: Decimal, supplier_state: str, place: str) -> tuple[Decimal, Decimal, Decimal]:
    if supplier_state == place:
        half = _paise(taxable * rate / 200)
        return half, half, Decimal("0.00")
    return Decimal("0.00"), Decimal("0.00"), _paise(taxable * rate / 100)


def _next_contracts(data: dict[str, Any]) -> list[str]:
    """The contract numbers the scenarios' approved awards take, in order, after the profile's own contracts."""
    last = max((int(str(c["code"])[4:]) for c in data.get("contracts", [])), default=0)
    out, n = [], last
    for sc in data.get("period_scenarios") or []:
        if sc.get("contract"):
            for _ in dict.fromkeys(ln["supplier"] for ln in sc["lines"] if ln.get("supplier")):
                n += 1
                out.append(f"CNT-{n:04d}")
    return out


def scenario_rows(sc: dict[str, Any], data: dict[str, Any], out: Path, codes: list[str]) -> list[dict[str, str]]:
    company, places = Company(data), Places.of(data)
    gst = Decimal(str(sc.get("gst", "18")))
    loc = str(sc["location"])
    start = date.fromisoformat(sc["period_from"]) if sc.get("period_from") else None
    end = date.fromisoformat(sc["period_to"]) if sc.get("period_to") else None
    made = date.fromisoformat(sc["as_of"])
    lines = []
    for n, ln in enumerate(sc["lines"], start=1):
        sug = suggested(out, ln["item"], loc, start, end, made) if start and end else None
        qty = Decimal(str(ln["qty"])) if ln.get("qty") else sug
        lines.append((n, ln, qty, sug))
    priced = [(n, ln, qty) for n, ln, qty, _ in lines if ln.get("price")]
    amounts = {}
    for n, ln, qty in priced:
        taxable = _paise(Decimal(str(qty)) * Decimal(str(ln["price"])))
        cgst, sgst, igst = _tax(taxable, gst, places.supplier_state[ln["supplier"]], places.location_state[loc])
        amounts[n] = (taxable, cgst, sgst, igst)
    total = sum((sum(a, Decimal(0)) for a in amounts.values()), Decimal(0))
    rule, approver = ("", "")
    if amounts:
        band, approver = company.approver(company.band(total))
        rule = band.code
    contract_of: dict[str, tuple[str, Decimal]] = {}
    if sc.get("contract"):
        for supplier in dict.fromkeys(ln["supplier"] for _, ln, _ in priced):
            ceiling = sum((amounts[n][0] for n, ln, _ in priced if ln["supplier"] == supplier), Decimal(0))
            contract_of[supplier] = (codes.pop(0), ceiling)
    if sc.get("contract_ref"):
        contract_of = {ln["supplier"]: (str(sc["contract_ref"]), Decimal(str(sc["ceiling"]))) for _, ln, _ in priced}
    rows = []
    for n, ln, qty, sug in lines:
        a = amounts.get(n)
        code, ceiling = contract_of.get(ln.get("supplier", ""), ("", None))
        rows.append(
            {
                "scenario": sc["name"], "line": str(n), "item": ln["item"], "location": loc,
                "period_from": start.isoformat() if start else "", "period_to": end.isoformat() if end else "",
                "qty": f"{qty}", "suggested_qty": "" if sug is None else f"{sug}", "supplier": ln.get("supplier", ""),
                "net_price": str(ln.get("price", "")), "taxable": str(a[0]) if a else "", "cgst": str(a[1]) if a else "",
                "sgst": str(a[2]) if a else "", "igst": str(a[3]) if a else "", "total": str(total) if a else "",
                "rule": rule, "approver": approver, "ceiling": "" if ceiling is None else str(ceiling),
                "contract_code": code,
            }
        )  # fmt: skip
    return rows


def write_period(data: dict[str, Any], out: Path) -> dict[str, int]:
    """truth/truth_period_sourcing.csv from the profile's `period_scenarios:` (nothing when it has none)."""
    scenarios = data.get("period_scenarios") or []
    if not scenarios:
        return {}
    codes = _next_contracts(data)
    rows = [row for sc in scenarios for row in scenario_rows(sc, data, out, codes)]
    (out / "truth").mkdir(parents=True, exist_ok=True)
    write_csv(out / "truth" / "truth_period_sourcing.csv", TRUTH, list(rows))
    return {"period_scenarios": len(scenarios), "period_lines": len(rows)}


def check(out: Path) -> list[str]:
    """Each priced row adds up: taxable = qty x price; CGST = SGST or IGST alone; a scenario's lines add to its total."""
    path = out / "truth" / "truth_period_sourcing.csv"
    if not path.exists():
        return []
    problems: list[str] = []
    totals: dict[str, Decimal] = {}
    stated: dict[str, Decimal] = {}
    for r in read(path):
        where = f"truth_period_sourcing {r['scenario']} line {r['line']}"
        if r["suggested_qty"] and Decimal(r["suggested_qty"]) != Decimal(r["suggested_qty"]).to_integral_value():
            problems.append(f"{where}: the suggested quantity is not a whole unit")
        if not r["net_price"]:
            continue
        taxable, cgst, sgst, igst = (Decimal(r[k]) for k in ("taxable", "cgst", "sgst", "igst"))
        if _paise(Decimal(r["qty"]) * Decimal(r["net_price"])) != taxable:
            problems.append(f"{where}: taxable is not qty x price")
        if cgst != sgst or (igst and cgst):
            problems.append(f"{where}: the tax is neither CGST and SGST nor IGST alone")
        totals[r["scenario"]] = totals.get(r["scenario"], Decimal(0)) + taxable + cgst + sgst + igst
        stated[r["scenario"]] = Decimal(r["total"])
        if r["contract_code"] and not r["ceiling"]:
            problems.append(f"{where}: a contract with no ceiling")
    for name, total in totals.items():
        if total != stated[name]:
            problems.append(f"truth_period_sourcing {name}: the lines add up to {total}, not {stated[name]}")
    return problems

