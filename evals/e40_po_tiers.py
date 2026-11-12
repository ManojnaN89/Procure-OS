"""E40: purchase order tiers, the delivery location and touchless orders (plan step 7, P1-35, D35, D36), against an
oracle written from the plan on random orders.

The oracle never calls the platform's formulas. From the dataset's own files (the 'po' approval bands, the supplier's
state, the plant's GSTIN) and the numbers it planted, it works out each order's GST and total in whole paise, the tier
the band names, and whether the order is touchless: every line on a contract (a single-source line never is), the total
within the buyer's band and the touchless limit (the band's top by default, Rs 50,000 in the second half under rule
version 2), the supplier active, the contract not over its ceiling, the price at most 2% above the estimate and the
autonomy gate on. Then it checks the order's status, tier and touchless flag, that a touchless order was sent with no
person in its audit trail, and that lines for two places become two orders. The plan's worked example is worked out too.
"""

import random
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

import lib
from lib import D, Outcome, pct, rows
from sqlalchemy import text

ID = "E40"
TITLE = "PO tiers by value, delivery location, touchless orders only within every limit"
REQUIREMENT = (
    "Plan step 7 / P1-35, D35, D36: buyer up to Rs 1,00,000 (own issue is the approval), purchase manager to Rs 5,00,000, "
    "director above; touchless for contract and safe-reorder lines within the limit, active supplier, contract room, "
    "price within 2%, budget and the autonomy gate, recorded with the facts and the rule version; delivery place on the PO"
)
SUPPLIER = "SUP-001"
CATEGORY = "RAW"
OTHER_PLACE = ("E40-LOC", "E40 Bengaluru depot", "29EVALX0000X1Z5")
CENT = Decimal("0.01")


def money(v: Decimal) -> Decimal:
    return v.quantize(CENT, rounding=ROUND_HALF_UP)


class Case:
    def __init__(self, name: str, qty: int, price: Decimal, est: Decimal, gst: int, *, ceiling: str | None = None,
                 gate: bool = True, blocked: bool = False, single: bool = False) -> None:  # fmt: skip
        self.name, self.qty, self.price, self.est, self.gst = name, qty, price, est, gst
        self.ceiling = ceiling  # None, "exact", "under" (a paisa short) or "room"
        self.gate, self.blocked, self.single = gate, blocked, single


def oracle(case: Case, bands: list[tuple[Decimal, Decimal, str]], intra: bool, limit: Decimal) -> dict:
    taxable = money(case.price * case.qty)
    if intra:
        half = money(taxable * case.gst / 200)
        total = taxable + 2 * half
    else:
        total = taxable + money(taxable * case.gst / 100)
    chain = next(c for lo, hi, c in bands if lo <= total <= hi)
    tier = "buyer" if chain == "auto" else chain.split(">")[-1]
    breach = case.ceiling == "under" and not case.single  # a single-source line has no contract to go over
    variance = ((case.price - case.est) * 100 / case.est).quantize(CENT, rounding=ROUND_HALF_UP)
    touchless = (
        not case.single and tier == "buyer" and total <= limit and not case.blocked and not breach
        and variance <= 2 and case.gate
    )  # fmt: skip
    status = "draft" if breach else ("sent" if touchless else "approved") if tier == "buyer" else "submitted"
    return {"taxable": taxable, "total": total, "tier": None if breach else tier, "touchless": touchless,
            "status": status, "variance": variance}  # fmt: skip


def fixed_cases() -> list[Case]:
    """The boundaries, written out (GST 0 on one unit, so the total is the price)."""
    return [
        Case("buyer tier top", 1, D("100000.00"), D("100000.00"), 0),
        Case("purchase manager from a paisa above", 1, D("100000.01"), D("100000.01"), 0),
        Case("purchase manager top", 1, D("500000.00"), D("500000.00"), 0),
        Case("director from a paisa above", 1, D("500000.01"), D("500000.01"), 0),
        Case("price 2% above the estimate", 1, D("102.00"), D("100.00"), 0),
        Case("price 2.01% above the estimate", 1, D("102.01"), D("100.00"), 0),
        Case("contract used up exactly", 10, D("100.00"), D("100.00"), 18, ceiling="exact"),
        Case("contract a paisa short", 10, D("100.00"), D("100.00"), 18, ceiling="under"),
        Case("gate off", 10, D("100.00"), D("100.00"), 18, gate=False),
        Case("supplier blocked after sourcing", 10, D("100.00"), D("100.00"), 18, blocked=True),
        Case("single source", 10, D("100.00"), D("100.00"), 18, single=True),
    ]


def second_half_fixed() -> list[Case]:
    return [
        Case("touchless limit v2 exactly", 1, D("50000.00"), D("50000.00"), 0),
        Case("touchless limit v2 and a paisa", 1, D("50000.01"), D("50000.01"), 0),
    ]


def random_cases(rng: random.Random, n: int) -> list[Case]:
    out = []
    for i in range(n):
        est = Decimal(rng.randint(1000, 500000)) / 100
        bump = rng.choice(["0", "0", "1", "2", "2.01", "2.5", "-1"])
        price = money(est * (1 + Decimal(bump) / 100))
        target = rng.choice([rng.randint(1000, 90000), rng.randint(90000, 110000), rng.randint(110000, 700000)])
        qty = max(1, int(Decimal(target) / price))
        single = rng.random() < 0.12
        if single:
            qty = max(1, min(qty, int(Decimal(20000) / price)))  # under the single-source agreement threshold
        out.append(
            Case(
                f"random {i}", qty, price, est, rng.choice([0, 5, 18, 18]),
                ceiling=rng.choice([None, None, None, None, None, "exact", "under", "room"]),
                gate=rng.random() > 0.1, blocked=rng.random() < 0.07, single=single,
            )
        )  # fmt: skip
    return out


def plant(db, i: int, case: Case, place: str) -> tuple[str, str]:
    item, req = f"E40-I{i}", f"E40-REQ-{i}"
    db.execute(
        text(
            "insert into item (code, name, category_code, base_uom, purchase_uom, active, stocked, standard_price, "
            "gst_rate_before, gst_rate_after) values (:i, :n, :c, 'NOS', 'NOS', true, true, :p, :g, :g)"
        ),
        {"i": item, "n": f"E40 part {i}", "c": CATEGORY, "p": case.est, "g": case.gst},
    )
    contract = None
    if case.single:
        db.execute(
            text("insert into item_single_source (item_code, supplier_code, reason) values (:i, :s, 'E40')"),
            {"i": item, "s": SUPPLIER},
        )
    else:
        contract = f"E40-K{i}"
        db.execute(
            text(
                "insert into contract (code, supplier_code, type, valid_from, valid_to, status) "
                "values (:k, :s, 'rate_contract', '2026-04-01', '2027-03-31', 'active')"
            ),
            {"k": contract, "s": SUPPLIER},
        )
    db.execute(
        text(
            "insert into price_record (supplier_code, item_code, contract_code, uom, price, min_qty, valid_from, "
            "valid_to, lead_days) values (:s, :i, :k, 'NOS', :p, 1, '2026-04-01', '2027-03-31', 5)"
        ),
        {"s": SUPPLIER, "i": item, "k": contract, "p": case.price},
    )
    db.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value) "
            "values (:n, :loc, 'goods', 'planning', 'approved', 1)"
        ),
        {"n": req, "loc": place},
    )
    db.execute(
        text(
            "insert into request_line (request_no, line_no, item_code, qty, uom, est_price, category_code, need_by, "
            "status) values (:r, 1, :i, :q, 'NOS', :e, :c, :nb, 'open')"
        ),
        {"r": req, "i": item, "q": case.qty, "e": case.est, "c": CATEGORY, "nb": lib.TODAY + timedelta(days=20)},
    )
    db.commit()
    return req, contract or ""


def run(ctx) -> Outcome:
    from p2p.agents import autonomy
    from p2p.orders import service as orders
    from p2p.orders import touchless
    from p2p.platform import sod
    from p2p.sourcing import service as sourcing

    out = Outcome(True)
    box = lib.capture_mail()
    world = lib.build_world("e40", with_stock=False)
    db, slug = world.db, world.slug
    admin = sod.user(world.logins["Admin"])
    place = lib.location()
    plant_gstin = next(r for r in rows("master", "location") if r["code"] == place)["gstin"] or next(
        r["gstin"] for r in rows("master", "gst_registration") if r["is_default"] == "true"
    )
    supplier_state = next(r for r in rows("master", "supplier") if r["code"] == SUPPLIER)["state_code"]
    intra = supplier_state == plant_gstin[:2]
    bands = sorted(
        (D(r["min_value"]), D(r["max_value"]), r["approver_chain"])
        for r in rows("master", "approval_rule")
        if r["document"] == "po"
    )
    buyer_top = next(hi for lo, hi, c in bands if c == "auto")

    rng = random.Random(40)
    first = fixed_cases() + random_cases(rng, 15 if ctx.quick else 45)
    second = second_half_fixed() + random_cases(rng, 10 if ctx.quick else 30)
    bad: list[str] = []
    checked = tier_ok = total_ok = touch_ok = status_ok = audit_ok = 0
    touchless_made: list[str] = []

    def gate(on: bool) -> None:
        autonomy.configure(db, admin, "po.issue", level="auto" if on else "propose")

    for half, (cases, limit, version) in enumerate(((first, buyer_top, 1), (second, D("50000.00"), 2))):
        if half == 1:
            touchless.save_rule(db, admin, effective_from=lib.TODAY, value_limit=D("50000.00"), price_pct=D(2),
                                note="E40: a limit of its own")  # fmt: skip
        for case in cases:
            i = checked
            want = oracle(case, bands, intra, limit)
            req, contract = plant(db, i, case, place)
            gate(case.gate)
            sourcing.source_request(db, slug, req, as_of=lib.TODAY, dispatch=False)
            if case.ceiling and contract:
                ceiling = {"exact": want["taxable"], "under": want["taxable"] - CENT, "room": want["taxable"] + 1000}
                db.execute(text("update contract set max_value = :m where code = :k"),
                           {"m": ceiling[case.ceiling], "k": contract})  # fmt: skip
            if case.blocked:
                db.execute(text("update supplier set status = 'blocked' where code = :s"), {"s": SUPPLIER})
            db.commit()
            made = orders.order_request(db, slug, req, as_of=lib.TODAY)
            if case.blocked:
                db.execute(text("update supplier set status = 'active' where code = :s"), {"s": SUPPLIER})
                db.commit()
            checked += 1
            if len(made.pos) != 1:
                bad.append(f"{case.name}: {len(made.pos)} orders made ({made.skipped})")
                continue
            po = dict(db.execute(text("select * from po where no = :n"), {"n": made.pos[0]}).mappings().one())
            got = {"total": po["total"], "tier": po["approval_tier"], "touchless": po["touchless"],
                   "status": po["status"]}  # fmt: skip
            total_ok += got["total"] == want["total"]
            tier_ok += got["tier"] == want["tier"]
            touch_ok += got["touchless"] == want["touchless"]
            status_ok += got["status"] == want["status"]
            if any(got[k] != want[k] for k in got):
                bad.append(f"{case.name} (v{version}): platform {got}, oracle {want}; note: {po['touchless_note']}")
            # the audit trail: the tests and their rule version; a touchless order touched by no person
            trail = db.execute(
                text("select action, actor_type, actor_id, rule_version from audit_event where entity = 'po' "
                     "and entity_id = :n order by id"), {"n": po["no"]},
            ).all()  # fmt: skip
            checks = [r for r in trail if r[0] == "po.touchless_check"]
            ok = (want["status"] == "draft" and not checks) or (
                len(checks) == 1
                and checks[0][1] == "rule"
                and str(checks[0][3]).startswith(f"touchless_po v{version}; PO-")
            )
            if want["touchless"]:
                touchless_made.append(po["no"])
                sends = [r for r in trail if r[0] == "po.send"]
                ok = ok and len(sends) == 1 and sends[0][1:3] == ("rule", f"touchless_po v{version}")
                ok = ok and all(r[1] != "user" for r in trail)
                ok = ok and any(po["no"] in (m.get("subject") or "") for m in box.sent)
            audit_ok += ok
            if not ok:
                bad.append(f"{case.name}: audit trail not as the plan says: {[tuple(r) for r in trail]}")

    # ---- two places on one request: two orders, each with its place, its tax and its address in the email ----
    gate(True)
    db.execute(
        text("insert into location (code, name, address, city, state_code, pincode, gstin, is_stock_location) "
             "values (:c, :n, 'Dock 2', 'Bengaluru', '29', '560001', :g, true)"),
        {"c": OTHER_PLACE[0], "n": OTHER_PLACE[1], "g": OTHER_PLACE[2]},
    )  # fmt: skip
    db.commit()
    two = Case("two places", 10, D("100.00"), D("100.00"), 18)
    req, _ = plant(db, 9000, two, place)
    db.execute(
        text("insert into request_line (request_no, line_no, item_code, qty, uom, est_price, category_code, need_by, "
             "status, delivery_location) values (:r, 2, 'E40-I9000', 5, 'NOS', 100, :c, :nb, 'open', :loc)"),
        {"r": req, "c": CATEGORY, "nb": lib.TODAY + timedelta(days=20), "loc": OTHER_PLACE[0]},
    )  # fmt: skip
    db.commit()
    sourcing.source_request(db, slug, req, as_of=lib.TODAY, dispatch=False)
    orders.order_request(db, slug, req, as_of=lib.TODAY)
    placed = {
        r[0]: (r[1], D(r[2]), D(r[3]), D(r[4]))
        for r in db.execute(
            text("select ship_to_location, no, taxable, igst, cgst from po where request_no = :r"), {"r": req}
        )
    }
    want_places = {place: D("1000.00"), OTHER_PLACE[0]: D("500.00")}
    places_ok = set(placed) == set(want_places) and all(placed[p][1] == v for p, v in want_places.items())
    # the other place is in Karnataka (29) and the supplier in Maharashtra: IGST there, CGST and SGST at home
    places_ok = places_ok and placed.get(OTHER_PLACE[0], ("", 0, 0, 0, 0))[2] == D("90.00")
    places_ok = places_ok and (placed.get(place, ("", 0, 0, 0, 0))[3] == D("90.00") if intra else True)
    mailed = [m for no, *_ in placed.values() for m in box.sent if no in (m.get("subject") or "")]
    places_ok = places_ok and any(OTHER_PLACE[1] in m["body"] and "Dock 2" in m["body"] for m in mailed)
    if not places_ok:
        bad.append(f"two places: orders {placed}, emails {[m['subject'] for m in mailed]}")

    # ---- the plan's worked example: the casting on contract goes out touchless at Apex -----------------------
    items = {r["code"]: r for r in rows("master", "item")}
    casting = next(r for r in rows("master", "price_record") if r["item_code"] == "RM-01" and r["contract_code"])
    db.execute(
        text("insert into request (no, ship_to_location, type, channel, status, total_value) "
             "values ('E40-CAST', :loc, 'goods', 'planning', 'approved', 1)"), {"loc": place},
    )  # fmt: skip
    db.execute(
        text("insert into request_line (request_no, line_no, item_code, qty, uom, est_price, category_code, need_by, "
             "status) values ('E40-CAST', 1, 'RM-01', 50, 'NOS', :e, 'RAW', :nb, 'open')"),
        {"e": items["RM-01"]["standard_price"], "nb": lib.TODAY + timedelta(days=20)},
    )  # fmt: skip
    db.commit()
    sourcing.source_request(db, slug, "E40-CAST", as_of=lib.TODAY, dispatch=False)
    orders.order_request(db, slug, "E40-CAST", as_of=lib.TODAY)
    cast = db.execute(text("select total, touchless, status from po where request_no = 'E40-CAST'")).one()
    cast_taxable = money(50 * D(casting["price"]))
    cast_total = cast_taxable + 2 * money(cast_taxable * D(items["RM-01"]["gst_rate_after"]) / 200)
    if tuple(cast) != (cast_total, True, "sent"):
        bad.append(f"worked example: the casting order is {tuple(cast)}, the plan says {cast_total}, touchless, sent")
    # the plan's four orders (casting, bearings, copper wire from Pune Copper at Rs 900/kg, cartons) at today's rates
    prices = {"RM-01": D(casting["price"]), "RM-06": D("38"), "RM-08": D("900"), "PK-01": D("45")}
    qty = {"RM-01": 50, "RM-06": 60, "RM-08": 40, "PK-01": 30}
    who = {"RM-01": "SUP-001", "RM-06": "SUP-002", "RM-08": "SUP-005", "PK-01": "SUP-004"}
    states = {r["code"]: r["state_code"] for r in rows("master", "supplier")}
    four = Decimal(0)
    for code, q in qty.items():
        taxable = money(prices[code] * q)
        rate = D(items[code]["gst_rate_after"])
        tax = 2 * money(taxable * rate / 200) if states[who[code]] == plant_gstin[:2] else money(taxable * rate / 100)
        four += taxable + tax
    out.metrics["worked example: casting order"] = f"{cast[0]} ({'touchless' if cast[1] else 'not touchless'})"
    out.metrics["worked example: the four orders at the dataset's GST rates"] = str(four)
    if four != D("85113.40"):
        out.notes.append(
            f"The plan's four orders total Rs 85,113.40; at the dataset's rates they total Rs {four}. The cartons (PK-01) "
            f"are at {items['PK-01']['gst_rate_after']}% GST in the dataset (the rate changed on 22 Sep 2025); the "
            "plan worked them at 18%."
        )

    out.metrics.update(
        {
            "orders checked": checked,
            "totals correct %": pct(total_ok, checked),
            "tiers correct %": pct(tier_ok, checked),
            "touchless decisions correct %": pct(touch_ok, checked),
            "statuses correct %": pct(status_ok, checked),
            "audit trails as planned %": pct(audit_ok, checked),
            "touchless orders": len(touchless_made),
            "two places, two orders": places_ok,
        }
    )
    if bad:
        out.find("high", f"{len(bad)} disagreement(s) with the oracle; first: {bad[0]}")
    if not touchless_made:
        out.find("medium", "no order went out touchless: the touchless path was not exercised")
    return out
