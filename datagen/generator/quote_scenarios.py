"""The quotes and supplier selection scenarios of block 6 (tasks 12.6, 11b.2): `quote_scenarios:` of a company profile,
and what each figure must come to, worked out here from the profile and the generated dataset alone (never from the
backend).

File written: truth/truth_quotes_selection.csv, one row per (scenario, figure): scenario, figure, item, location, on,
value, source.

The rules, as the block 6 spec says them:
- the last price paid for an item at a place, as of a date: the history's orders (`history/po.csv`, `po_line.csv`; not
  draft or cancelled, lines not cancelled) made in the 365 days before the date, the latest by the day made, then the
  order number. Every item and location with one gets a row (scenario LAST);
- a gap test of a price against references (the negotiation brief, with the lowest offer; the path A/B case, with the
  contract price): gap = price - reference (4 places, half up), gap % = gap / price x 100 (4 places, half up), saving
  = gap x the line quantity (paise, half up); it goes ahead when the gap % is above the rule's percent, the gap above
  its rupees per unit and the saving above its minimum (all strictly);
- the brief's target is the lowest triggering reference (paise), the saving at target (price - target) x quantity;
- an order: taxable = qty x price, paise half up; inside one state CGST = SGST = taxable x half the GST rate, each
  half up; the total is their sum.
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from master_files import read, write_csv

FOUR, CENT, HUNDRED = Decimal("0.0001"), Decimal("0.01"), Decimal(100)
TRUTH = ["scenario", "figure", "item", "location", "on", "value", "source"]
NOT_COUNTED = ("draft", "submitted", "cancelled")


def _q4(v: Decimal) -> Decimal:
    return v.quantize(FOUR, ROUND_HALF_UP)


def _paise(v: Decimal) -> Decimal:
    return v.quantize(CENT, ROUND_HALF_UP)


def last_paid(out: Path, on: date) -> dict[tuple[str, str], tuple[Decimal, str, date]]:
    """(item, location) to (price, order, day made) of the latest history order line in the 365 days before `on`."""
    folder = out / "history"
    if not (folder / "po.csv").is_file():
        return {}
    orders = {r["no"]: r for r in read(folder / "po.csv") if r["status"] not in NOT_COUNTED}
    best: dict[tuple[str, str], tuple[Decimal, str, date]] = {}
    for ln in read(folder / "po_line.csv"):
        po = orders.get(ln["po_no"])
        if po is None or ln["status"] == "cancelled":
            continue
        made = date.fromisoformat(po["created_at"][:10])
        if not (on - timedelta(days=365) < made <= on):
            continue
        key = (ln["item_code"], po["ship_to_location"])
        held = best.get(key)
        if held is None or (made, ln["po_no"]) > (held[2], held[1]):
            best[key] = (Decimal(ln["unit_price"]), ln["po_no"], made)
    return best


def gap_test(
    price: Decimal, ref: Decimal, qty: Decimal, rule: dict[str, Any]
) -> tuple[Decimal, Decimal, Decimal, bool]:
    gap = _q4(price - ref)
    pct = _q4((price - ref) / price * HUNDRED)
    saving = _paise(gap * qty)
    ahead = (
        pct > Decimal(str(rule["gap_pct"]))
        and gap > Decimal(str(rule["gap_inr_per_unit"]))
        and saving > Decimal(str(rule["min_saving_inr"]))
    )
    return gap, pct, saving, ahead


def _rows_of(sc: dict[str, Any], rule: dict[str, Any], paid: dict[tuple[str, str], Any]) -> list[dict[str, str]]:
    name, item, loc, on = sc["name"], sc["item"], sc["location"], sc["as_of"]
    qty = Decimal(str(sc["qty"]))
    rows: list[dict[str, str]] = []

    def add(figure: str, value: Any, source: str = "") -> None:
        rows.append({"scenario": name, "figure": figure, "item": item, "location": loc, "on": on, "value": str(value),
                     "source": source})  # fmt: skip

    if sc.get("kind") == "order":  # an order's totals
        price, rate = Decimal(str(sc["price"])), Decimal(str(sc["gst"]))
        taxable = _paise(qty * price)
        half = _paise(taxable * rate / 2 / HUNDRED)
        add("taxable", taxable)
        add("cgst", half)
        add("sgst", half)
        add("total", taxable + half + half)
        return rows
    if sc.get("kind") == "path":  # the A/B case: the contract price against the market
        contract, market = Decimal(str(sc["contract"])), Decimal(str(sc["market"]))
        gap, pct, saving, ahead = gap_test(contract, market, qty, rule)
        add("contract_price", contract)
        add("market", _q4(market))
        add("gap", gap)
        add("gap_pct", pct)
        add("saving", saving)
        add("case", "yes" if ahead else "no")
        return rows
    l1 = Decimal(str(sc["l1"]))
    triggers: list[Decimal] = []
    found = paid.get((item, loc))
    if found is not None:
        gap, pct, saving, ahead = gap_test(l1, found[0], qty, rule)
        add("last_paid", found[0], f"{found[1]} {found[2].isoformat()}")
        add("last_paid_gap", gap)
        add("last_paid_gap_pct", pct)
        add("last_paid_saving", saving)
        if ahead:
            triggers.append(found[0])
    if sc.get("market"):
        market = Decimal(str(sc["market"]))
        gap, pct, saving, ahead = gap_test(l1, market, qty, rule)
        add("market", _q4(market))
        add("market_gap", gap)
        add("market_gap_pct", pct)
        add("market_saving", saving)
        if ahead:
            triggers.append(market)
    if triggers:
        target = _paise(min(triggers))
        add("target", target)
        add("saving_at_target", _paise((l1 - target) * qty))
        if sc.get("l2"):
            add("l2", Decimal(str(sc["l2"])))
    add("brief", "yes" if triggers else "no")
    return rows


def write_quotes(data: dict[str, Any], out: Path) -> dict[str, int]:
    """truth/truth_quotes_selection.csv from the profile's `quote_scenarios:` (nothing when it has none)."""
    scenarios = data.get("quote_scenarios") or []
    if not scenarios:
        return {}
    rule = data["price_thresholds"]
    on = date.fromisoformat(str(scenarios[0]["as_of"]))
    paid = last_paid(out, on)
    rows = [
        {"scenario": "LAST", "figure": "last_paid", "item": item, "location": loc, "on": on.isoformat(),
         "value": str(price), "source": f"{po} {made.isoformat()}"}
        for (item, loc), (price, po, made) in sorted(paid.items())
    ]  # fmt: skip
    rows += [row for sc in scenarios for row in _rows_of(sc, rule, last_paid(out, date.fromisoformat(sc["as_of"])))]
    (out / "truth").mkdir(parents=True, exist_ok=True)
    write_csv(out / "truth" / "truth_quotes_selection.csv", TRUTH, rows)
    return {"quote_scenarios": len(scenarios), "quote_figures": len(rows)}


def check(out: Path) -> list[str]:
    """Each order adds up; a brief's target is one of its triggering references and never above the lowest offer."""
    path = out / "truth" / "truth_quotes_selection.csv"
    if not path.exists():
        return []
    by: dict[str, dict[str, str]] = {}
    for r in read(path):
        by.setdefault(r["scenario"], {})[r["figure"]] = r["value"]
    problems = []
    for name, f in by.items():
        if "total" in f and Decimal(f["taxable"]) + Decimal(f["cgst"]) + Decimal(f["sgst"]) != Decimal(f["total"]):
            problems.append(f"truth_quotes_selection {name}: the order does not add up")
        if "target" in f and Decimal(f["target"]) not in {
            _paise(Decimal(f[k])) for k in ("last_paid", "market") if k in f
        }:
            problems.append(f"truth_quotes_selection {name}: the target is not a reference")
    return problems
