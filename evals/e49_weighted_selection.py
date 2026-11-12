"""E49: the weighted score of supplier selection (plan step 6, "or on a weighted score if the company prefers"), and the
dataset's own answer key for the cheapest supplier replayed through the platform.

E39 and E05 only ever compare on total cost. Here the versioned selection rule is switched to a weighted score with
weights chosen per case, and an oracle in exact fractions scores every qualifying quote: price as the lowest total cost
over this one, payment terms as these days over the longest among the qualifying quotes, delivery and quality from the
vendor score (a supplier with no history in the company counts as 0.7 on each), the weighted average as 0 to 100 rounded
half up to one decimal; ranked by score, then earlier delivery, then supplier code. Boundaries: one quote only, equal
scores, the best score dropped for a must-have, terms outweighing price, a score ending in .x5, weights that do not add up
to 100, and a dropped quote with longer terms (it must not set the scale). Then every RFQ in
`truth/truth_supplier_selection.csv` is planted with its quotes on the real items and suppliers and compared on total cost
(the key's split selections are left out: it keeps no quantity per part to replay).
"""

import random
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

import lib
from e39_supplier_selection import BAD, GOOD, Offer, plant, setup
from lib import D, Outcome, pct, rows
from sqlalchemy import text

ID = "E49"
TITLE = "Supplier selection by weighted score, and the answer key's cheapest supplier, match an independent oracle"
REQUIREMENT = (
    "Plan step 6 / P1-34: quotes failing a must-have are dropped; the rest are compared on total cost or, when the "
    "company's versioned rule says so, on a weighted score of price, delivery, quality and payment terms; ties by earlier "
    "delivery; the rule version is recorded with each selection; the dataset's RFQs select the supplier its answer key "
    "names"
)
NEUTRAL = Fraction(7, 10)
TERMS = {"IMM": 0, "NET15": 15, "NET30": 30, "NET45": 45, "NET60": 60}


class Quote(Offer):
    def __init__(self, supplier: str, price: int, days: int, valid_in: int, freight: int, terms: str) -> None:
        super().__init__(supplier, price, days, valid_in, freight, None)
        self.terms = terms


def half_up_tenth(x: Fraction) -> Decimal:
    return Decimal((x * 10 + Fraction(1, 2)).__floor__()) / Decimal(10)


def oracle(
    quotes: list[Quote], qty: int, need_in: int, w: tuple[int, int, int, int]
) -> tuple[dict[str, Decimal], list[str]]:
    """Each qualifying supplier's score, and the ranking (input credit claimed: GST is not part of the cost)."""
    good = [q for q in quotes if not q.dropped(need_in)]
    if not good:
        return {}, []
    cost = {q.supplier: qty * q.price + q.freight for q in good}
    best = min(cost.values())
    longest = max(TERMS[q.terms] for q in good)
    total = sum(w)
    scores = {}
    for q in good:
        terms = Fraction(1) if longest == 0 else Fraction(TERMS[q.terms], longest)
        raw = (w[0] * Fraction(best, cost[q.supplier]) + w[1] * NEUTRAL + w[2] * NEUTRAL + w[3] * terms) / total * 100
        scores[q.supplier] = half_up_tenth(raw)
    ranked = sorted(good, key=lambda q: (-scores[q.supplier], q.days, q.supplier))
    return scores, [q.supplier for q in ranked]


def fixed_cases() -> list[tuple[str, int, int, tuple[int, int, int, int], list[Quote]]]:
    """(name, quantity, days until needed, weights price/delivery/quality/terms, quotes); prices in paise."""
    return [
        ("one quote only", 10, 10, (40, 30, 20, 10), [Quote("E39-A", 10000, 5, 30, 0, "NET30")]),
        ("equal scores go to the earlier delivery", 10, 10, (40, 30, 20, 10),
         [Quote("E39-A", 10000, 7, 30, 0, "NET30"), Quote("E39-B", 10000, 4, 30, 0, "NET30")]),
        ("the best score is dropped when it delivers late", 10, 10, (40, 30, 20, 10),
         [Quote("E39-A", 9000, 11, 30, 0, "NET60"), Quote("E39-B", 10000, 5, 30, 0, "NET30"),
          Quote("E39-C", 10500, 5, 30, 0, "NET45")]),
        ("payment terms outweigh price", 10, 10, (10, 0, 0, 90),
         [Quote("E39-A", 10000, 5, 30, 0, "IMM"), Quote("E39-B", 12000, 5, 30, 0, "NET60")]),
        ("a score of 81.25 rounds half up to 81.3", 10, 10, (100, 0, 0, 0),
         [Quote("E39-A", 8125, 5, 30, 0, "NET30"), Quote("E39-B", 10000, 5, 30, 0, "NET30")]),
        ("weights that add up to 4, not 100", 10, 10, (3, 1, 0, 0),
         [Quote("E39-A", 10000, 5, 30, 0, "NET30"), Quote("E39-B", 9700, 5, 30, 0, "NET15")]),
        ("an expired quote with longer terms does not set the scale", 10, 10, (40, 30, 20, 10),
         [Quote("E39-A", 10000, 5, 30, 0, "NET30"), Quote("E39-B", 9000, 5, -1, 0, "NET60"),
          Quote("E39-C", 11000, 5, 30, 0, "NET30")]),
        ("freight counts in the price part", 10, 10, (70, 0, 0, 30),
         [Quote("E39-A", 10000, 5, 30, 20000, "NET45"), Quote("E39-B", 11000, 5, 30, 0, "NET30")]),
    ]  # fmt: skip


def random_cases(rng: random.Random, n: int) -> list[tuple[str, int, int, tuple[int, int, int, int], list[Quote]]]:
    out = []
    for i in range(n):
        qty, need_in = rng.randint(2, 20), rng.randint(5, 20)
        w = tuple(rng.choice([0, 5, 10, 20, 30, 40, 55]) for _ in range(4))
        if not any(w):
            w = (40, 30, 20, 10)
        who = rng.sample(GOOD, k=rng.randint(1, 4)) + ([rng.choice(list(BAD))] if rng.random() < 0.2 else [])
        quotes = [
            Quote(
                s,
                rng.randint(8000, 12000) if rng.random() > 0.2 else 10000,
                need_in + rng.choice([-3, -1, 0, 0, 1]),
                rng.choice([-1, 0, 30, 30]),
                rng.choice([0, 0, 0, 2500, 9000]),
                rng.choice(list(TERMS)),
            )
            for s in who
        ]
        out.append((f"random {i}", qty, need_in, w, quotes))  # type: ignore[arg-type]
    return out


def invite_and_quote(db, rfq_no: str, quotes: list[Quote], gst: Decimal, buyer) -> None:  # type: ignore[no-untyped-def]
    from p2p.orders import quotes as service

    for q in quotes:
        db.execute(
            text("insert into rfq_supplier (rfq_no, supplier_code, email, invited_at) values (:r, :s, :e, now())"),
            {"r": rfq_no, "s": q.supplier, "e": f"{q.supplier.lower()}@eval.example"},
        )
    db.commit()
    for q in quotes:
        service.enter_quote(
            db,
            rfq_no,
            service.QuoteInput(
                supplier_code=q.supplier,
                valid_until=lib.TODAY + timedelta(days=q.valid_in),
                payment_terms_code=q.terms,
                freight=Decimal(q.freight) / 100,
                lines=[service.QuoteLineInput(1, Decimal(q.price) / 100, gst, delivery_days=q.days)],
            ),
            buyer,
        )


def run(ctx) -> Outcome:  # noqa: C901 - two parts, each step by step
    from p2p.formulas import selection as sel
    from p2p.orders import quotes, selection_rule
    from p2p.platform import sod

    out = Outcome(True)
    world = lib.build_world("e49", with_stock=False)
    db = world.db
    buyer = sod.user(world.logins["Buyer"])
    setup(db)
    rng = random.Random(49)  # noqa: S311 - a repeatable sample, not a secret
    cases = fixed_cases() + random_cases(rng, 20 if ctx.quick else 80)
    bad: list[str] = []
    checked = score_ok = rank_ok = rec_ok = version_ok = selected = 0
    for i, (name, qty, need_in, w, offered) in enumerate(cases):
        version = selection_rule.save_rule(
            db, sod.user(1), effective_from=lib.TODAY, method="weighted",
            weights=sel.Weights(*(Decimal(x) for x in w)), note=f"E49 {name}",
        )  # fmt: skip
        _, rfq_no = plant(db, 1000 + i, qty, need_in, [], buyer)
        invite_and_quote(db, rfq_no, offered, Decimal(18), buyer)
        want_scores, want_rank = oracle(offered, qty, need_in, w)
        comp = quotes.comparison(db, rfq_no, lib.TODAY)
        (line,) = comp.lines
        checked += 1
        got_scores = {o.supplier_code: o.score for o in line.offers if o.rank is not None}
        got_rank = [o.supplier_code for o in line.offers if o.rank is not None]
        want_rec = want_rank[0] if want_rank else None
        score_ok += got_scores == want_scores
        rank_ok += got_rank == want_rank
        rec_ok += line.recommended == want_rec
        if got_scores != want_scores or got_rank != want_rank or line.recommended != want_rec:
            bad.append(
                f"{name} ({rfq_no}, weights {w}): scores {got_scores} ranked {got_rank} recommended {line.recommended}; "
                f"oracle {want_scores} ranked {want_rank} recommends {want_rec}"
            )
        if comp.rule != f"supplier_selection v{version}":
            bad.append(f"{name}: the comparison used {comp.rule!r}, the rule in force is v{version}")
        if want_rec:
            quotes.award(db, rfq_no, {1: want_rec}, buyer, reason="E49: the weighted recommendation", as_of=lib.TODAY)
            selected += 1
            recorded = db.execute(text("select selection_rule from award where rfq_no = :r"), {"r": rfq_no}).scalar()
            version_ok += recorded == f"supplier_selection v{version}"
            if recorded != f"supplier_selection v{version}":
                bad.append(f"{name}: the selection recorded rule {recorded!r}, expected supplier_selection v{version}")
    if bad:
        out.find("high", f"{len(bad)} weighted comparison(s) differ from the oracle; first: {bad[0]}")

    # ---- part 2: the dataset's answer key, on total cost ----
    selection_rule.save_rule(db, sod.user(1), effective_from=lib.TODAY, method="total_cost", note="E49 answer key")
    from p2p.formulas.orders import current_gst_rate

    items = {r["code"]: r for r in rows("master", "item")}
    approved = {(r["supplier_code"], r["category_code"]) for r in rows("master", "supplier_category")}
    key = rows("truth", "truth_supplier_selection")
    if ctx.quick:
        key = key[:30]
    splits = [r for r in key if r["reason"].startswith("Split")]  # the key keeps no quantity per part to replay
    key = [r for r in key if not r["reason"].startswith("Split")]
    replayed = matches = own_agrees = 0
    key_bad: list[str] = []
    key_notes: list[str] = []
    for n, row in enumerate(key):
        it = items[row["item_code"]]
        change = it["gst_rate_change_date"]
        gst = current_gst_rate(
            D(it["gst_rate_before"]), D(it["gst_rate_after"]),
            date.fromisoformat(change) if change else None, lib.TODAY,
        ) or Decimal(18)  # fmt: skip
        options = []
        for opt in row["options"].split(";"):
            supplier, price, days = opt.split(":")
            options.append((supplier, D(price), int(days.rstrip("d"))))
        rfq_no = _plant_real(db, n, row, it)
        for supplier, _, _ in options:
            db.execute(
                text("insert into rfq_supplier (rfq_no, supplier_code, email, invited_at) values (:r, :s, :e, now())"),
                {"r": rfq_no, "s": supplier, "e": f"{supplier.lower()}@eval.example"},
            )
        db.commit()
        for supplier, price, days in options:
            quotes.enter_quote(
                db, rfq_no,
                quotes.QuoteInput(supplier_code=supplier, valid_until=lib.TODAY + timedelta(days=30),
                                  lines=[quotes.QuoteLineInput(1, price, gst, delivery_days=days)]),
                buyer,
            )  # fmt: skip
        # the oracle: approved suppliers only, the lowest price (the quantity is the same for all), then earlier delivery
        fit = [o for o in options if (o[0], it["category_code"]) in approved]
        own = min(fit, key=lambda o: (o[1], o[2], o[0]))[0] if fit else None
        (line,) = quotes.comparison(db, rfq_no, lib.TODAY).lines
        replayed += 1
        matches += line.recommended == row["selected"]
        own_agrees += own == row["selected"]
        if line.recommended != row["selected"]:
            key_bad.append(
                f"{row['rfq_no']} ({row['item_code']}, {row['options']}): platform recommends {line.recommended}, the "
                f"answer key selected {row['selected']} ({row['reason']}); the oracle says {own}"
            )
        elif own != row["selected"]:
            key_notes.append(
                f"{row['rfq_no']}: the answer key's {row['selected']} is not the lowest approved price ({own})"
            )
    if key_bad:
        out.find("high", f"{len(key_bad)} of {replayed} answer-key RFQs select another supplier; first: {key_bad[0]}")
    out.notes += key_notes[:5]

    out.metrics = {
        "weighted quote sets compared": checked,
        "scores correct %": pct(score_ok, checked),
        "ranking correct %": pct(rank_ok, checked),
        "recommendation correct %": pct(rec_ok, checked),
        "selections recording their rule version": f"{version_ok} of {selected}",
        "answer-key RFQs replayed (splits left out: the key has no part quantities)": f"{replayed} ({len(splits)} left out)",
        "answer key matched %": pct(matches, replayed),
        "oracle agrees with the answer key %": pct(own_agrees, replayed),
    }
    return out


def _plant_real(db, n: int, row: dict[str, str], item: dict[str, str]) -> str:  # type: ignore[no-untyped-def]
    """An approved request and a sent RFQ for one line of a real item, needed in a month (planted like E39's)."""
    req_no, rfq_no = f"E49-KEY-{n}", f"E49-KEYRFQ-{n}"
    need_by = lib.TODAY + timedelta(days=30)
    uom = item["purchase_uom"] or item["base_uom"]
    db.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value) "
            "values (:n, :loc, 'goods', 'planning', 'approved', 1)"
        ),
        {"n": req_no, "loc": lib.location()},
    )
    db.execute(
        text(
            "insert into request_line (request_no, line_no, item_code, qty, uom, category_code, need_by, status) "
            "values (:r, 1, :i, :q, :u, :c, :nb, 'sourcing')"
        ),
        {"r": req_no, "i": row["item_code"], "q": D(row["qty"]), "u": uom, "c": item["category_code"], "nb": need_by},
    )
    db.execute(
        text(
            "insert into rfq (no, request_no, category_code, due_date, status, min_quotes) "
            "values (:r, :q, :c, :d, 'sent', :m)"
        ),
        {"r": rfq_no, "q": req_no, "c": item["category_code"], "d": lib.TODAY + timedelta(days=5),
         "m": int(row["quotes_required"] or 1)},
    )  # fmt: skip
    db.execute(
        text(
            "insert into rfq_line (rfq_no, line_no, request_no, request_line_no, item_code, description, qty, uom, "
            "need_by) values (:r, 1, :q, 1, :i, :d, :qty, :u, :nb)"
        ),
        {
            "r": rfq_no,
            "q": req_no,
            "i": row["item_code"],
            "d": item["name"],
            "qty": D(row["qty"]),
            "u": uom,
            "nb": need_by,
        },
    )
    db.execute(
        text(
            "insert into sourcing_decision (request_no, line_no, method, rfq_no, reason) values (:q, 1, 'rfq', :r, 'e49')"
        ),
        {"q": req_no, "r": rfq_no},
    )
    db.commit()
    return rfq_no
