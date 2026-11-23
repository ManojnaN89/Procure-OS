"""E05: comparing quotes and awarding, checked against a brute-force oracle on random sets of quotes."""

import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

import lib
from lib import D, Outcome, pct, rows
from sqlalchemy import text

ID = "E05"
TITLE = "Quote comparison recommends the right supplier and the award rules hold"
REQUIREMENT = "Plan: collect quotes, compare, select; the recommendation is the lowest qualifying offer; an override or too few qualifying quotes needs a reason"


def net(price: Decimal, discount: Decimal) -> Decimal:
    x = Fraction(price) * (100 - Fraction(discount)) / 100
    return Decimal(((x * 10000) + Fraction(1, 2)).__floor__()) / Decimal(10000)


def run(ctx) -> Outcome:
    from p2p.formulas.orders import current_gst_rate
    from p2p.orders import quotes
    from p2p.platform import sod

    out = Outcome(True)
    world = lib.build_world("e05", with_stock=False)
    db = world.db
    rng = random.Random(55)
    items = {r["code"]: r for r in rows("master", "item") if r["stocked"] == "true"}
    category = lib.busiest_category()
    category_items = [c for c, r in items.items() if r["category_code"] == category][:30]
    # block 0b (task 4.4, spec 4.6) plants a blocked supplier and prospects in the category: only active suppliers are
    # the ones whose quotes this comparison judges (a non-active supplier's quote is dropped, as it always was)
    active = {r["code"] for r in rows("master", "supplier") if (r.get("status") or "active") == "active"}
    suppliers = [
        r["supplier_code"]
        for r in rows("master", "supplier_category")
        if r["category_code"] == category and r["supplier_code"] in active
    ][:6]
    min_quotes = int(next(r["min_quotes"] for r in rows("master", "category") if r["code"] == category) or 2)
    buyer = sod.user(world.logins["Buyer"])
    db.execute(
        text(
            "insert into number_series (document_type, prefix, scope, format, reset) values ('rfq','RFQ','company','{prefix}-{fy4}-{seq:05d}','fy') on conflict do nothing"
        )
    )
    db.commit()

    scenarios = 40 if ctx.quick else 160
    lines_checked = rec_ok = flags_ok = order_ok = 0
    award_cases = award_ok = 0
    bad: list[str] = []
    for i in range(scenarios):
        req_no, rfq_no = f"EV-REQ-{i}", f"EV-RFQ-{i}"
        chosen = rng.sample(category_items, k=rng.randint(1, 3))
        need_in = rng.randint(5, 25)
        db.execute(
            text(
                "insert into request (no, ship_to_location, type, channel, status, total_value) values (:n, :loc, 'goods', 'planning', 'approved', 1)"
            ),
            {"n": req_no, "loc": lib.location()},
        )
        db.execute(
            text("insert into rfq (no, request_no, category_code, due_date, status) values (:r, :q, :c, :d, 'sent')"),
            {
                "r": rfq_no,
                "q": req_no,
                "c": category,
                "d": lib.TODAY + timedelta(days=5),
            },
        )
        est: dict[int, Decimal] = {}
        qty: dict[int, Decimal] = {}
        expected_rate: dict[int, Decimal | None] = {}
        for n, code in enumerate(chosen, start=1):
            it = items[code]
            est[n] = D(it["standard_price"], "100")
            qty[n] = Decimal(rng.randint(1, 60))
            before = D(it["gst_rate_before"]) if it["gst_rate_before"] else None
            after = D(it["gst_rate_after"]) if it["gst_rate_after"] else None
            changed = date.fromisoformat(it["gst_rate_change_date"]) if it["gst_rate_change_date"] else None
            expected_rate[n] = current_gst_rate(before, after, changed, lib.TODAY) or Decimal(18)
            db.execute(
                text(
                    "insert into request_line (request_no, line_no, item_code, qty, uom, category_code, need_by, est_price, status) values (:r, :n, :i, :q, 'NOS', :c, :nb, :e, 'sourcing')"
                ),
                {
                    "r": req_no,
                    "n": n,
                    "i": code,
                    "q": qty[n],
                    "c": category,
                    "nb": lib.TODAY + timedelta(days=need_in),
                    "e": est[n],
                },
            )
            db.execute(
                text(
                    "insert into rfq_line (rfq_no, line_no, request_no, request_line_no, item_code, description, qty, uom, need_by) values (:r, :n, :q, :n, :i, :d, :qty, 'NOS', :nb)"
                ),
                {
                    "r": rfq_no,
                    "n": n,
                    "q": req_no,
                    "i": code,
                    "d": it["name"],
                    "qty": qty[n],
                    "nb": lib.TODAY + timedelta(days=need_in),
                },
            )
            db.execute(
                text(
                    "insert into sourcing_decision (request_no, line_no, method, rfq_no, reason) values (:q, :n, 'rfq', :r, 'eval')"
                ),
                {"q": req_no, "n": n, "r": rfq_no},
            )
        invited = rng.sample(suppliers, k=rng.randint(2, min(5, len(suppliers))))
        for s in invited:
            db.execute(
                text("insert into rfq_supplier (rfq_no, supplier_code, email, invited_at) values (:r, :s, :e, now())"),
                {"r": rfq_no, "s": s, "e": f"{s.lower()}@eval.example"},
            )
        db.commit()
        offers: dict[int, list[dict]] = {n: [] for n in est}
        responders = rng.sample(invited, k=rng.randint(1, len(invited)))
        for s in responders:
            valid = lib.TODAY + timedelta(days=rng.choice([-3, -1, 0, 1, 20, 60]))
            lines = []
            for n in est:
                if rng.random() < 0.15:
                    continue  # a partial quote
                price = (est[n] * Decimal(str(round(rng.uniform(0.5, 1.7), 3)))).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                ) or Decimal("0.01")
                disc = Decimal(rng.choice([0, 0, 0, 2, 5, 12]))
                rate = (
                    expected_rate[n]
                    if rng.random() > 0.2
                    else Decimal(rng.choice([r for r in (5, 12, 28) if Decimal(r) != expected_rate[n]]))
                )
                days = rng.randint(1, 30)
                moq = Decimal(rng.randint(1, 80)) if rng.random() < 0.2 else None
                lines.append(
                    quotes.QuoteLineInput(
                        rfq_line_no=n,
                        unit_price=price,
                        gst_rate=rate,
                        discount_pct=disc,
                        delivery_days=days,
                        moq=moq,
                    )
                )
                late = days > need_in
                flags = (valid < lib.TODAY) + (moq is not None and qty[n] < moq) + late + (rate != expected_rate[n])
                offers[n].append(
                    {
                        "supplier": s,
                        "net": net(price, disc),
                        "days": days,
                        "compliant": flags == 0,
                        "value": (qty[n] * net(price, disc)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                    }
                )
            if lines:
                quotes.enter_quote(
                    db,
                    rfq_no,
                    quotes.QuoteInput(supplier_code=s, valid_until=valid, lines=lines),
                    buyer,
                )
        comp = quotes.comparison(db, rfq_no, lib.TODAY)
        got = {ln.rfq_line_no: ln for ln in comp.lines}
        recommended: dict[int, str | None] = {}
        for n, offs in offers.items():
            lines_checked += 1
            good = sorted(
                (o for o in offs if o["compliant"]),
                key=lambda o: (o["net"], o["days"], o["supplier"]),
            )
            want = good[0]["supplier"] if good else None
            recommended[n] = want
            g = got[n]
            rec_ok += g.recommended == want
            flags_ok += {o.supplier_code: o.compliant for o in g.offers} == {
                o["supplier"]: o["compliant"] for o in offs
            }
            # the ranking shows the qualifying offers first, cheapest first; the dropped ones follow
            order_ok += [o.supplier_code for o in g.offers][: len(good)] == [o["supplier"] for o in good] and {
                o.supplier_code for o in g.offers
            } == {o["supplier"] for o in offs}
            if g.recommended != want:
                bad.append(f"{rfq_no} line {n}: platform recommends {g.recommended}, oracle {want}")
            for o in g.offers:
                mine = next(x for x in offs if x["supplier"] == o.supplier_code)
                if o.net_price != mine["net"] or o.value != mine["value"]:
                    bad.append(
                        f"{rfq_no} line {n} {o.supplier_code}: net/value {o.net_price}/{o.value}, oracle {mine['net']}/{mine['value']}"
                    )
        # the award rule: pick a random supplier for each line that has an offer
        picks = {
            n: rng.choice([o["supplier"] for o in offs] + ([recommended[n]] * 3 if recommended[n] else []))
            for n, offs in offers.items()
            if offs
        }
        if not picks:
            continue
        # a reason: a pick that is not the cheapest qualifying offer, or a picked line with too few qualifying quotes
        needs_reason = any(
            picks[n] != recommended[n] or sum(o["compliant"] for o in offers[n]) < min_quotes for n in picks
        )
        award_cases += 1
        try:
            quotes.award(db, rfq_no, picks, buyer, as_of=lib.TODAY)
            refused = False
        except quotes.QuoteError as exc:
            # block 6 (spec 5.2 rule 1, OP-9): an expired quote is refused even with a reason; it is never the
            # recommendation, so the oracle always asks for a reason there
            refused = "reason is required" in str(exc) or "ask for a renewed price" in str(exc)
            db.rollback()
        if refused == needs_reason:
            award_ok += 1
        else:
            bad.append(
                f"{rfq_no} award {picks}: platform {'asked for' if refused else 'did not ask for'} a reason, oracle says {'needed' if needs_reason else 'not needed'}"
            )
    out.metrics = {
        "scenarios": scenarios,
        "lines compared": lines_checked,
        "recommendation correct %": pct(rec_ok, lines_checked),
        "compliance flags correct %": pct(flags_ok, lines_checked),
        "offer order correct %": pct(order_ok, lines_checked),
        "award rule correct %": pct(award_ok, award_cases),
    }
    if bad:
        out.find("high", f"{len(bad)} disagreement(s) with the oracle; first: {bad[0]}")
    return out
