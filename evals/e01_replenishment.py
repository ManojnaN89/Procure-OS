"""E01: what to order, checked against an independent recomputation from the raw dataset."""

from collections import defaultdict
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

import lib
from lib import D, Outcome, pct, rows

ID = "E01"
TITLE = "Replenishment orders the right items in the right quantities"
REQUIREMENT = "Plan: read stock levels, create requests for items at their reorder point (quantity from the planning data, whole packs, supplier minimum)"

QTY = Decimal("0.001")


def expected() -> dict[tuple[str, str], dict]:
    """The lines replenishment should produce, worked out here from the files alone."""
    on_hand: dict[tuple[str, str], Decimal] = {
        (r["item_code"], r["location_code"]): D(r["qty"])
        for r in rows("master", "stock_opening")
    }
    for m in rows("history", "stock_movement"):
        key = (m["item_code"], m["location_code"])
        sign = 1 if m["type"] in ("receipt", "transfer_in", "adjust") else -1
        on_hand[key] = on_hand.get(key, Decimal(0)) + sign * D(m["qty"])
    items = {r["code"]: r for r in rows("master", "item")}
    conv = {
        (r["item_code"], r["from_uom"], r["to_uom"]): D(r["factor"])
        for r in rows("master", "uom_conversion")
    }
    out: dict[tuple[str, str], dict] = {}
    for p in rows("master", "item_location"):
        key = (p["item_code"], p["location_code"])
        have = on_hand.get(key, Decimal(0))
        rop = D(p["reorder_point"]) if p["reorder_point"] else None
        safety = D(p["safety_stock"]) if p["safety_stock"] else None
        mx = D(p["max_qty"]) if p["max_qty"] else None
        policy = p["reorder_policy"]
        if have < 0:
            continue
        if have == 0:
            status = "stockout"
        elif policy in ("", "none"):
            continue
        elif safety is not None and have < safety:
            status = "below_safety"
        elif rop is not None and have <= rop:
            status = "reorder"
        else:
            continue
        item = items[p["item_code"]]
        if (
            item["active"] != "true"
            or not item["standard_price"]
            or D(item["standard_price"]) <= 0
        ):
            continue
        if policy == "minmax":
            need = (mx or Decimal(0)) - have
        elif policy == "rop":
            need = D(p["order_qty"]) if p["order_qty"] else Decimal(0)
        else:
            need = Decimal(0)
        need = max(need, Decimal(0))
        buy_uom = item["purchase_uom"] or item["base_uom"]
        factor = conv.get((item["code"], buy_uom, item["base_uom"])) or (
            D(item["pack_size"], "1") if buy_uom != item["base_uom"] else Decimal(1)
        )
        if need == 0:
            continue
        step = QTY if factor == 1 else Decimal(1)
        units = ((need / factor) / step).to_integral_value(
            rounding=ROUND_CEILING
        ) * step
        moq = D(item["moq"], "0")
        qty = max(units, moq)
        out[key] = {
            "qty": qty,
            "uom": buy_uom,
            "value": (qty * D(item["standard_price"])).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            ),
            "group": (
                p["location_code"],
                item["category_code"],
                "urgent" if status in ("stockout", "below_safety") else "normal",
            ),
        }
    return out


def run(ctx) -> Outcome:
    from p2p.requests import replenishment

    out = Outcome(True)
    world = lib.build_world("e01")
    want = expected()
    got_requests, skipped = replenishment.plan(world.db, lib.TODAY)
    got = {
        (ln.item_code, req.location_code): {
            "qty": ln.qty,
            "uom": ln.uom,
            "value": ln.value,
            "group": (req.location_code, req.category_code, req.priority),
        }
        for req in got_requests
        for ln in req.lines
    }

    missing = sorted(set(want) - set(got))
    extra = sorted(set(got) - set(want))
    exact = sum(
        1
        for k in set(want) & set(got)
        if want[k]["qty"] == got[k]["qty"] and want[k]["uom"] == got[k]["uom"]
    )
    value_ok = sum(
        1 for k in set(want) & set(got) if want[k]["value"] == got[k]["value"]
    )
    group_ok = sum(
        1 for k in set(want) & set(got) if want[k]["group"] == got[k]["group"]
    )
    both = len(set(want) & set(got))
    out.metrics = {
        "lines expected": len(want),
        "lines planned": len(got),
        "missing": len(missing),
        "extra": len(extra),
        "quantity exact %": pct(exact, both),
        "value exact %": pct(value_ok, both),
        "grouping exact %": pct(group_ok, both),
        "requests": len(got_requests),
        "total value": str(sum((r.total_value for r in got_requests), Decimal(0))),
    }
    if missing:
        out.find(
            "high",
            f"{len(missing)} item(s) that need ordering were not planned, e.g. {missing[:3]}",
        )
    if extra:
        out.find(
            "high",
            f"{len(extra)} item(s) were planned that the data does not call for, e.g. {extra[:3]}",
        )
    for label, ok in (("quantity", exact), ("value", value_ok), ("grouping", group_ok)):
        if ok != both:
            bad = [
                k
                for k in set(want) & set(got)
                if (
                    want[k]["qty"] != got[k]["qty"]
                    if label == "quantity"
                    else want[k]["value"] != got[k]["value"]
                    if label == "value"
                    else want[k]["group"] != got[k]["group"]
                )
            ]
            out.find(
                "high",
                f"{both - ok} line(s) differ in {label}, e.g. {bad[:2]}: expected {want[bad[0]]} got {got[bad[0]]}",
            )
    # the request total equals the sum of its lines
    for req in got_requests:
        if req.total_value != sum((ln.value for ln in req.lines), Decimal(0)):
            out.find(
                "high",
                f"request total differs from its lines for {req.location_code}/{req.category_code}",
            )
    by_group = defaultdict(int)
    for g in (v["group"] for v in want.values()):
        by_group[g] += 1
    if len(by_group) != len(got_requests):
        out.find(
            "medium", f"expected {len(by_group)} requests, planned {len(got_requests)}"
        )
    return out
