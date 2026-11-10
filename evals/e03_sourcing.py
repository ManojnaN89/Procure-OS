"""E03: contract or quotation for every stocked item, checked against an independent reading of the price records."""

from datetime import date
from decimal import Decimal

import lib
from lib import D, Outcome, pct, rows

ID = "E03"
TITLE = "Sourcing picks the right contract price, or sends the line to quotation"
REQUIREMENT = "Plan: contract vs RFQ per line; only an active contract in force, for an active supplier, in the right unit, with the quantity break"


def d(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


def oracle(
    prices: list[dict],
    contracts: dict,
    suppliers: dict,
    item: str,
    uom: str,
    qty: Decimal,
    on: date,
):
    per: dict[str, dict] = {}
    for r in prices:
        if r["item_code"] != item or r["uom"] != uom or not r["contract_code"]:
            continue
        c = contracts.get(r["contract_code"])
        if not c or c["status"] != "active" or suppliers[r["supplier_code"]]["status"] != "active":
            continue
        if not (d(c["valid_from"]) <= on and (d(c["valid_to"]) is None or on <= d(c["valid_to"]))):
            continue
        if not (d(r["valid_from"]) <= on and (d(r["valid_to"]) is None or on <= d(r["valid_to"]))):
            continue
        if D(r["min_qty"], "0") > qty:
            continue
        held = per.get(r["supplier_code"])
        if held is None or (D(r["min_qty"], "0"), -D(r["price"])) > (
            D(held["min_qty"], "0"),
            -D(held["price"]),
        ):
            per[r["supplier_code"]] = r
    if not per:
        return None
    best = min(
        per.values(),
        key=lambda r: (
            D(r["price"]),
            int(r["lead_days"]) if r["lead_days"] else 10_000,
            r["supplier_code"],
        ),
    )
    return best["supplier_code"], best["contract_code"], D(best["price"])


def run(ctx) -> Outcome:
    from p2p.formulas.sourcing import pick_contract_price
    from p2p.sourcing import service

    out = Outcome(True)
    world = lib.build_world("e03", with_stock=False)
    db = world.db
    prices = rows("master", "price_record")
    contracts = {r["code"]: r for r in rows("master", "contract")}
    suppliers = {r["code"]: r for r in rows("master", "supplier")}
    items = [r for r in rows("master", "item") if r["stocked"] == "true"]
    qtys = [Decimal(q) for q in (["1", "50"] if ctx.quick else ["1", "10", "100", "1000"])]
    dates = (
        [date(2026, 11, 6), date(2025, 12, 31)]
        if ctx.quick
        else [
            date(2026, 11, 6),
            date(2025, 12, 31),
            date(2026, 3, 31),
            date(2026, 7, 1),
            date(2024, 10, 1),
            date(2027, 1, 1),
        ]
    )
    cases = agree = on_contract = 0
    bad: list[str] = []
    for item in items:
        uom = item["purchase_uom"] or item["base_uom"]
        for qty in qtys:
            for on in dates:
                want = oracle(prices, contracts, suppliers, item["code"], uom, qty, on)
                pick = pick_contract_price(service.contract_options(db, item["code"], uom, on), qty)
                got = (pick.supplier_code, pick.contract_code, pick.price) if pick else None
                cases += 1
                on_contract += want is not None
                if got == want:
                    agree += 1
                else:
                    bad.append(f"{item['code']} qty {qty} on {on}: platform {got}, oracle {want}")
    out.metrics = {
        "items": len(items),
        "cases": cases,
        "decided on a contract": on_contract,
        "sent to quotation": cases - on_contract,
        "agreement %": pct(agree, cases),
    }
    if agree != cases:
        out.find(
            "high",
            f"{cases - agree} sourcing decision(s) differ from the oracle; first: {bad[0]}",
        )
    if on_contract == 0:
        out.find(
            "medium",
            "no case was decided on a contract: the dataset or the dates may be wrong for this eval",
        )
    return out
