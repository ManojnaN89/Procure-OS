"""E36: the buying route of each line (D37), against an oracle written from the plan's rule and the raw dataset.

The oracle never calls the platform: it knows the history it planted (last order, deliveries, price on file), reads the
contract and category from the dataset, and applies the plan's thresholds in the plan's order.
"""

import itertools
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

import lib
from e03_sourcing import oracle as contract_oracle
from lib import D, Outcome, pct, rows
from sqlalchemy import text

ID = "E36"
TITLE = "The buying route: contract, single source, emergency, safe reorder, else quotation with the right quotes"
REQUIREMENT = (
    "Plan D37 / P1-33: first yes wins; Rs 25,000 agreement and reorder limit; 90 days; 80% on time; 2% price; "
    "2 quotes from Rs 25,000 to Rs 2,00,000 and 3 above; value counted per item over 30 days; categories only stricter"
)

PLAN = {  # the plan's numbers, written out here and not read from the platform
    "agree_above": D(25000),
    "within_days": 90,
    "on_time_pct": D(80),
    "reorder_below": D(25000),
    "price_pct": D(2),
    "two_from": D(25000),
    "three_above": D(200000),
}
SUPPLIER = "SUP-002"  # an active supplier approved for RAW, with every document it needs
CATEGORY = "RAW"


class Case:
    def __init__(self, key: str, item: str, qty: str, est: str | None, **facts: object) -> None:
        self.key, self.item, self.qty, self.est = key, item, D(qty), D(est) if est else None
        self.facts = facts  # days, on_time, late, drift, last_price, single_price, emergency, category, ...


def money(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def expected(case: Case, value: Decimal, window_before: Decimal, contract: bool, min_quotes: int, plan: dict) -> tuple:
    """(route, quotes wanted or None, purchase manager must agree)."""
    f = case.facts
    window = window_before + value
    if contract:
        return ("contract", None, False)
    if f.get("single_price") is not None:
        return ("single_source", None, window > plan["agree_above"])
    if f.get("emergency"):
        return ("emergency", None, True)
    days, on_time, late = f.get("days"), int(f.get("on_time", 0)), int(f.get("late", 0))
    judged = on_time + late
    safe = (
        days is not None
        and days <= plan["within_days"]
        and judged > 0
        and D(on_time) * 100 >= plan["on_time_pct"] * judged
        and window < plan["reorder_below"]
        and D(str(f.get("drift", "0"))) <= plan["price_pct"]
    )
    if safe:
        return ("reorder", None, False)
    band = 1 if window < plan["two_from"] else 2 if window <= plan["three_above"] else 3
    return ("rfq", max(band, min_quotes), False)


def make_supplier(db, code: str) -> None:
    """A supplier of our own, so its delivery record is only the orders planted for one case."""
    db.execute(text("insert into supplier (code, legal_name, status) values (:c, :c, 'active')"), {"c": code})
    for doc in ("pan", "cancelled_cheque"):
        db.execute(
            text(
                "insert into supplier_document (supplier_code, doc_type, number, issued_on, verified) "
                "values (:c, :t, 'X', '2020-01-01', true)"
            ),
            {"c": code, "t": doc},
        )


def plant(db, item: str, supplier: str, last_price: Decimal, days: int, on_time: int, late: int, n: int) -> None:
    """An order for the item from the supplier some days ago, and its deliveries (on time, or three days late)."""
    placed = datetime.combine(lib.TODAY - timedelta(days=days), time(10))
    promised = placed.date() + timedelta(days=2)
    po = f"E36-PO-{n:03d}"
    db.execute(
        text(
            "insert into po (no, supplier_code, ship_to_location, status, created_at) "
            "values (:po, :s, :loc, 'received', cast(:at as timestamp) at time zone 'Asia/Kolkata')"
        ),
        {"po": po, "s": supplier, "loc": lib.location(), "at": placed},
    )
    db.execute(
        text(
            "insert into po_line (po_no, line_no, request_no, request_line_no, item_code, description, qty, uom, "
            "unit_price, gst_rate, taxable, promised_date, source, status) values (:po, 1, :po, 1, :i, 'x', 10, "
            "'NOS', :p, 18, 0, :pd, 'award', 'received')"
        ),
        {"po": po, "i": item, "p": last_price, "pd": promised},
    )
    for k in range(on_time + late):
        arrived = datetime.combine(promised + timedelta(days=0 if k < on_time else 3), time(11))
        db.execute(
            text(
                "insert into goods_receipt (no, po_no, location_code, gate_entry_at, received_by, posted_by) values "
                "(:g, :po, :loc, cast(:at as timestamp) at time zone 'Asia/Kolkata', 'eval', 'eval')"
            ),
            {"g": f"{po}-{k}", "po": po, "loc": lib.location(), "at": arrived},
        )
        db.execute(
            text(
                "insert into gr_line (gr_no, po_no, po_line_no, qty_received, qty_accepted, qty_rejected) "
                "values (:g, :po, 1, 1, 1, 0)"
            ),
            {"g": f"{po}-{k}", "po": po},
        )


def make_item(db, code: str, category: str = CATEGORY) -> None:
    db.execute(
        text(
            "insert into item (code, name, category_code, base_uom, purchase_uom, active, stocked, gst_rate_before, "
            "gst_rate_after) values (:c, :c, :cat, 'NOS', 'NOS', true, true, 18, 18)"
        ),
        {"c": code, "cat": category},
    )


def price_on_file(db, item: str, price: Decimal, supplier: str = SUPPLIER) -> None:
    db.execute(
        text(
            "insert into price_record (supplier_code, item_code, uom, price, min_qty, valid_from, valid_to) "
            "values (:s, :i, 'NOS', :p, 1, '2026-10-01', '2026-12-30')"
        ),
        {"s": supplier, "i": item, "p": price},
    )


def cases(quick: bool) -> list[Case]:
    out: list[Case] = [
        # the Apex example, on the dataset's own items
        Case("casting on contract", "RM-01", "20", None),
        Case("copper wire 90 kg", "RM-08", "90", None),
        Case(
            "bearings from the known supplier",
            "RM-06",
            "100",
            None,
            days=17,
            on_time=5,
            late=0,
            drift="0",
            last_price="38",
        ),
    ]
    days_set = (89, 91) if quick else (89, 90, 91)
    pace_set = ((4, 1), (3, 1)) if quick else ((4, 1), (3, 1), (0, 0))
    drift_set = ("2", "2.01") if quick else ("0", "2", "2.01")
    for n, (days, (on, late), value, drift) in enumerate(
        itertools.product(days_set, pace_set, ("24999.99", "25000"), drift_set), start=1
    ):
        out.append(
            Case(
                f"reorder grid {days}d {on}/{on + late} on time Rs {value} drift {drift}%",
                f"E36-G{n:03d}",
                "100",
                str(D(value) / 100),
                days=days,
                on_time=on,
                late=late,
                drift=drift,
                last_price="100",
            )
        )
    for n, value in enumerate(("24999.99", "25000", "200000", "200000.01"), start=1):
        out.append(Case(f"quotation band Rs {value}", f"E36-Q{n}", "100", str(D(value) / 100)))
    out += [
        Case("single source at Rs 25,000", "E36-S1", "250", None, single_price="100"),
        Case("single source above Rs 25,000", "E36-S2", "251", None, single_price="100"),
        Case(
            "emergency", "E36-E1", "10", "100", emergency=True, days=10, on_time=5, late=0, drift="0", last_price="100"
        ),
        Case(
            "stricter category, under its limit",
            "E36-C1",
            "90",
            "100",
            category="E36S",
            days=10,
            on_time=5,
            late=0,
            drift="0",
            last_price="100",
        ),
        Case(
            "stricter category, over its limit",
            "E36-C2",
            "120",
            "100",
            category="E36S",
            days=10,
            on_time=5,
            late=0,
            drift="0",
            last_price="100",
        ),
        Case(
            "looser category is ignored",
            "E36-L1",
            "260",
            "100",
            category="E36L",
            days=10,
            on_time=5,
            late=0,
            drift="0",
            last_price="100",
        ),
    ]
    return out


def run(ctx) -> Outcome:
    from p2p.platform import sod
    from p2p.sourcing import route, service

    out = Outcome(True)
    world = lib.build_world("e36", with_stock=False)
    db = world.db
    prices = rows("master", "price_record")
    contracts = {r["code"]: r for r in rows("master", "contract")}
    suppliers = {r["code"]: r for r in rows("master", "supplier")}
    items = {r["code"]: r for r in rows("master", "item")}
    min_quotes = {r["code"]: int(r["min_quotes"]) for r in rows("master", "category") if r["min_quotes"]}
    default_quotes = int(next(r["value"] for r in rows("master", "setting") if r["key"] == "min_quotes_default"))

    # two categories of our own: one stricter than the company (reorders under Rs 10,000 only), one looser
    for code in ("E36S", "E36L"):
        db.execute(text("insert into item_category (code, name, buyer_user) values (:c, :c, 'U03')"), {"c": code})
        db.execute(
            text(
                "insert into supplier_category (supplier_code, category_code, approved_from) values (:s, :c, '2024-04-01')"
            ),
            {"s": SUPPLIER, "c": code},
        )
    db.commit()
    route.save_rule(db, sod.user(1), {"reorder_below": D(10000)}, category="E36S", effective_from=lib.TODAY)
    route.save_rule(db, sod.user(1), {"reorder_below": D(90000)}, category="E36L", effective_from=lib.TODAY)
    strict = {"E36S": {**PLAN, "reorder_below": D(10000)}}

    todo = cases(ctx.quick)
    planted = 0
    for case in todo:
        f = case.facts
        if case.item.startswith("E36-"):
            make_item(db, case.item, str(f.get("category", CATEGORY)))
        if "last_price" in f:
            last = D(str(f["last_price"]))
            planted += 1
            supplier = SUPPLIER  # the dataset's bearing supplier, for the Apex example
            if case.item.startswith("E36-"):
                supplier = f"E36-V{planted:03d}"
                make_supplier(db, supplier)
                price_on_file(db, case.item, money(last * (1 + D(str(f["drift"])) / 100)), supplier)
            plant(db, case.item, supplier, last, int(f["days"]), int(f["on_time"]), int(f["late"]), planted)
        if "single_price" in f:
            price_on_file(db, case.item, D(str(f["single_price"])))
            route.mark_single_source(db, sod.user(1), case.item, SUPPLIER, "eval")
    db.commit()

    agree = 0
    bad: list[str] = []
    for n, case in enumerate(todo, start=1):
        f = case.facts
        no = f"E36-R{n:03d}"
        db.execute(
            text(
                "insert into request (no, ship_to_location, type, channel, status, total_value, priority) "
                "values (:n, :loc, 'goods', 'form', 'approved', 0, :p)"
            ),
            {"n": no, "loc": lib.location(), "p": "emergency" if f.get("emergency") else "normal"},
        )
        uom = items[case.item]["purchase_uom"] if case.item in items else "NOS"
        db.execute(
            text(
                "insert into request_line (request_no, line_no, item_code, qty, uom, est_price, need_by, status) "
                "values (:r, 1, :i, :q, :u, :e, '2026-11-30', 'open')"
            ),
            {"r": no, "i": case.item, "q": case.qty, "u": uom, "e": case.est},
        )
        db.commit()
        service.source_request(db, world.slug, no, as_of=lib.TODAY, dispatch=False)
        got_row = db.execute(
            text(
                "select d.route, d.needs_agreement, r.min_quotes from sourcing_decision d left join rfq r on r.no = d.rfq_no "
                "where d.request_no = :n"
            ),
            {"n": no},
        ).one()
        got = (got_row[0], got_row[2], bool(got_row[1]))

        # the oracle's own reading of the facts
        # the value: the contract or single-source price when there is one, else the estimate, else the standard price
        pick = contract_oracle(prices, contracts, suppliers, case.item, uom, case.qty, lib.TODAY)
        on_contract = pick is not None
        if pick is not None:
            unit = pick[2]
        elif "single_price" in f:
            unit = D(str(f["single_price"]))
        else:
            unit = case.est or D(items[case.item]["standard_price"])
        category = str(f.get("category", CATEGORY))
        want = expected(
            case,
            money(case.qty * D(unit)),
            D(0),
            on_contract,
            min_quotes.get(category, default_quotes),
            strict.get(category, PLAN),
        )
        if got == want:
            agree += 1
        else:
            bad.append(f"{case.key}: platform {got}, oracle {want}")

    # split-order protection: a second request for an item already asked for today is counted with the first
    make_item(db, "E36-W1")
    make_supplier(db, "E36-VW")
    plant(db, "E36-W1", "E36-VW", D(100), 10, 5, 0, planted + 1)
    price_on_file(db, "E36-W1", D(100), "E36-VW")
    db.commit()
    window_routes = []
    for k, qty in enumerate(("150", "150"), start=1):  # Rs 15,000 then Rs 15,000 more
        no = f"E36-W{k}"
        db.execute(
            text(
                "insert into request (no, ship_to_location, type, channel, status, total_value) "
                "values (:n, :loc, 'goods', 'form', 'approved', 0)"
            ),
            {"n": no, "loc": lib.location()},
        )
        db.execute(
            text(
                "insert into request_line (request_no, line_no, item_code, qty, uom, est_price, need_by, status) "
                "values (:r, 1, 'E36-W1', :q, 'NOS', 100, '2026-11-30', 'open')"
            ),
            {"r": no, "q": qty},
        )
        db.commit()
        service.source_request(db, world.slug, no, as_of=lib.TODAY, dispatch=False)
        window_routes.append(
            db.execute(text("select route from sourcing_decision where request_no = :n"), {"n": no}).scalar_one()
        )
    oracle_window = ["reorder", "rfq"]  # 15,000 is under 25,000; 30,000 in the window is not
    total = len(todo) + 1
    agree += window_routes == oracle_window
    if window_routes != oracle_window:
        bad.append(f"30-day window: platform {window_routes}, oracle {oracle_window}")
    unexplained = db.execute(
        text(
            "select count(*) from sourcing_decision where request_no like 'E36-%' and (route_rule is null or checks is null)"
        )
    ).scalar_one()

    out.metrics = {
        "cases": total,
        "agreement %": pct(agree, total),
        "routes": dict(
            db.execute(
                text(
                    "select route, count(*) from sourcing_decision where request_no like 'E36-%' group by route order by route"
                )
            ).all()
        ),
        "decisions without rule version or tests": unexplained,
    }
    if bad:
        out.find("high", f"{len(bad)} route(s) differ from the oracle; first: {bad[0]}")
        out.notes.extend(bad[:10])
    if unexplained:
        out.find("high", f"{unexplained} decision(s) do not record the rule version and the tests they ran")
    return out
