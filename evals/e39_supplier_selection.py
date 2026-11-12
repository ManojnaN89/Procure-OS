"""E39: supplier selection (plan step 6, P1-34), against an oracle written from the plan on random sets of quotes.

The oracle never calls the platform's comparison. From the quotes it planted it drops those that fail a must-have
(delivered after the need-by date, expired, supplier not active or not approved, no GSTIN), works out each total cost in
whole paise (price times quantity plus freight, GST added only when the input credit is not claimed), ranks by cost then
earlier delivery, finds the best split by trying every whole-unit allocation, and says when a written reason is needed.
Then the selections are made and the orders checked: one per supplier, for its part. The printable RFQ is read back.
"""

import itertools
import random
from datetime import timedelta
from decimal import Decimal

import lib
from lib import D, Outcome, pct, rows
from sqlalchemy import text

ID = "E39"
TITLE = "Supplier selection: must-haves, total cost, tie-break, split only when it saves, reasons, orders per supplier"
REQUIREMENT = (
    "Plan step 6 / P1-34: drop quotes failing a must-have; compare total cost (GST not counted with input credit); "
    "ties by earlier delivery; split only when the saving beats the extra freight; a reason for other than the cheapest "
    "qualifying quote or too few quotes; one PO per supplier; printable RFQ per supplier"
)
CATEGORY = "E39C"
ITEM = "E39-ITEM"
GOOD = ["E39-A", "E39-B", "E39-C", "E39-D"]
BAD = {"E39-X": "blocked", "E39-N": "not approved", "E39-G": "no GSTIN"}
MIN_QUOTES = 2


def tax(paise: int) -> int:
    """18% GST in paise, halves up (the way invoices round)."""
    return (paise * 18 + 50) // 100


class Offer:
    def __init__(self, supplier: str, price: int, days: int, valid_in: int, freight: int, cap: int | None) -> None:
        self.supplier, self.price, self.days, self.valid_in, self.freight, self.cap = (
            supplier,
            price,  # paise per unit
            days,
            valid_in,  # days from today the quote is valid; negative is expired
            freight,  # paise
            cap,  # the most offered, when less than asked
        )

    def dropped(self, need_in: int) -> bool:
        return self.days > need_in or self.valid_in < 0 or self.supplier in BAD

    def goods(self, qty: int, credit: bool) -> int:
        value = qty * self.price
        return value if credit else value + tax(value)

    def fixed(self, credit: bool) -> int:
        return self.freight if credit else self.freight + tax(self.freight)


def oracle(offers: list[Offer], qty: int, need_in: int, credit: bool) -> dict:
    good = [o for o in offers if not o.dropped(need_in)]
    whole = [o for o in good if o.cap is None]

    def cost(o: Offer) -> int:  # the comparison's total: value plus freight, GST on both together when counted
        base = qty * o.price + o.freight
        return base if credit else base + tax(base)

    ranked = sorted(whole, key=lambda o: (cost(o), o.days, o.supplier))
    single = min((o.goods(qty, credit) + o.fixed(credit) for o in whole), default=None)
    best_split = None
    for alloc in itertools.product(*[range(0, (o.cap if o.cap is not None else qty) + 1) for o in good]):
        if sum(alloc) != qty or sum(1 for q in alloc if q) < 2:
            continue
        total = sum(o.goods(q, credit) + o.fixed(credit) for o, q in zip(good, alloc, strict=True) if q)
        best_split = total if best_split is None else min(best_split, total)
    worthwhile = best_split is not None and (single is None or best_split < single)
    return {
        "qualifying": {o.supplier for o in good},
        "ranked": [o.supplier for o in ranked],
        "costs": {o.supplier: cost(o) for o in offers},
        "split_total": best_split,
        "worthwhile": worthwhile,
        "recommended": None if worthwhile or not ranked else ranked[0].supplier,
        "cheapest": ranked[0].supplier if ranked else None,
    }


def setup(db) -> None:
    db.execute(text("insert into item_category (code, name, min_quotes) values (:c, 'E39 goods', :m)"),
               {"c": CATEGORY, "m": MIN_QUOTES})  # fmt: skip
    db.execute(
        text(
            "insert into item (code, name, category_code, base_uom, purchase_uom, active, stocked, gst_rate_before, "
            "gst_rate_after) values (:i, 'E39 widget', :c, 'NOS', 'NOS', true, true, 18, 18)"
        ),
        {"i": ITEM, "c": CATEGORY},
    )
    for n, code in enumerate([*GOOD, *BAD], start=1):
        letter = "ABCDEFGHIJ"[n - 1]
        db.execute(
            text(
                "insert into supplier (code, legal_name, status, gst_reg_type, gstin, state_code) "
                "values (:c, :c, :s, 'regular', :g, '27')"
            ),
            {
                "c": code,
                "s": "blocked" if BAD.get(code) == "blocked" else "active",
                "g": None if BAD.get(code) == "no GSTIN" else f"27EVAL{letter}0000{letter}1Z5",
            },
        )
        if BAD.get(code) != "not approved":
            db.execute(
                text(
                    "insert into supplier_category (supplier_code, category_code, approved_from) "
                    "values (:s, :c, '2024-01-01')"
                ),
                {"s": code, "c": CATEGORY},
            )
    db.commit()


def plant(db, i: int, qty: int, need_in: int, offers: list[Offer], buyer) -> tuple[str, str]:
    from p2p.orders import quotes

    req_no, rfq_no = f"E39-REQ-{i}", f"E39-RFQ-{i}"
    need_by = lib.TODAY + timedelta(days=need_in)
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
            "values (:r, 1, :i, :q, 'NOS', :c, :nb, 'sourcing')"
        ),
        {"r": req_no, "i": ITEM, "q": qty, "c": CATEGORY, "nb": need_by},
    )
    db.execute(
        text(
            "insert into rfq (no, request_no, category_code, due_date, status, min_quotes) "
            "values (:r, :q, :c, :d, 'sent', :m)"
        ),
        {"r": rfq_no, "q": req_no, "c": CATEGORY, "d": lib.TODAY + timedelta(days=5), "m": MIN_QUOTES},
    )
    db.execute(
        text(
            "insert into rfq_line (rfq_no, line_no, request_no, request_line_no, item_code, description, qty, uom, "
            "need_by) values (:r, 1, :q, 1, :i, 'E39 widget', :qty, 'NOS', :nb)"
        ),
        {"r": rfq_no, "q": req_no, "i": ITEM, "qty": qty, "nb": need_by},
    )
    db.execute(
        text(
            "insert into sourcing_decision (request_no, line_no, method, rfq_no, reason) values (:q, 1, 'rfq', :r, 'e39')"
        ),
        {"q": req_no, "r": rfq_no},
    )
    for o in offers:
        db.execute(
            text("insert into rfq_supplier (rfq_no, supplier_code, email, invited_at) values (:r, :s, :e, now())"),
            {"r": rfq_no, "s": o.supplier, "e": f"{o.supplier.lower()}@eval.example"},
        )
    db.commit()
    for o in offers:
        quotes.enter_quote(
            db,
            rfq_no,
            quotes.QuoteInput(
                supplier_code=o.supplier,
                valid_until=lib.TODAY + timedelta(days=o.valid_in),
                freight=Decimal(o.freight) / 100,
                lines=[
                    quotes.QuoteLineInput(
                        1, Decimal(o.price) / 100, Decimal(18), delivery_days=o.days,
                        qty=Decimal(o.cap) if o.cap is not None else None,
                    )
                ],
            ),
            buyer,
        )  # fmt: skip
    return req_no, rfq_no


def fixed_cases() -> list[tuple[str, int, int, list[Offer]]]:
    """The boundaries, written out: (name, quantity, days until needed, offers)."""
    return [
        ("on the need-by date qualifies, a day late does not", 10, 10,
         [Offer("E39-A", 10000, 10, 30, 0, None), Offer("E39-B", 9000, 11, 30, 0, None)]),
        ("a quote expiring today qualifies", 10, 10,
         [Offer("E39-A", 10000, 5, 0, 0, None), Offer("E39-B", 9000, 5, -1, 0, None)]),
        ("equal totals go to the earlier delivery", 10, 10,
         [Offer("E39-A", 10000, 7, 30, 0, None), Offer("E39-B", 10000, 4, 30, 0, None)]),
        ("a split saving exactly the extra freight does not split", 5, 10,
         [Offer("E39-A", 10000, 5, 30, 6000, 3), Offer("E39-B", 12000, 5, 30, 0, None)]),
        ("a split saving one paisa more than the freight splits", 5, 10,
         [Offer("E39-A", 10000, 5, 30, 5999, 3), Offer("E39-B", 12000, 5, 30, 0, None)]),
        ("a blocked, an unapproved and a GSTIN-less supplier are dropped", 10, 10,
         [Offer("E39-X", 5000, 5, 30, 0, None), Offer("E39-N", 5000, 5, 30, 0, None),
          Offer("E39-G", 5000, 5, 30, 0, None), Offer("E39-A", 10000, 5, 30, 0, None)]),
    ]  # fmt: skip


def random_cases(rng: random.Random, n: int) -> list[tuple[str, int, int, list[Offer]]]:
    out = []
    for i in range(n):
        qty, need_in = rng.randint(4, 12), rng.randint(5, 20)
        who = rng.sample(GOOD, k=rng.randint(2, 4)) + ([rng.choice(list(BAD))] if rng.random() < 0.2 else [])
        offers = [
            Offer(
                s,
                rng.randint(8000, 12000) if rng.random() > 0.2 else 10000,  # some equal prices
                need_in + rng.choice([-3, -1, 0, 0, 1]),
                rng.choice([-1, 0, 1, 30, 30]),
                rng.choice([0, 0, 0, 5000, 12000]),
                rng.randint(1, qty - 1) if rng.random() < 0.3 else None,
            )
            for s in who
        ]
        out.append((f"random {i}", qty, need_in, offers))
    return out


def run(ctx) -> Outcome:
    from p2p.orders import quotes, selection_rule, service
    from p2p.platform import sod
    from p2p.sourcing import rfq_document

    out = Outcome(True)
    world = lib.build_world("e39", with_stock=False)
    db = world.db
    buyer = sod.user(world.logins["Buyer"])
    setup(db)
    registered = bool(next(r for r in rows("master", "location") if r["code"] == lib.location()).get("gstin"))
    rng = random.Random(39)
    bad: list[str] = []
    checked = rank_ok = drop_ok = cost_ok = split_ok = rec_ok = 0
    selections = reason_ok = split_orders = orders_ok = 0
    cases = fixed_cases() + random_cases(rng, 30 if ctx.quick else 120)
    half = len(cases) // 2
    for i, (name, qty, need_in, offers) in enumerate(cases):
        if i == half:  # the second half: a rule version that does not claim the input credit
            selection_rule.save_rule(db, sod.user(1), effective_from=lib.TODAY, input_credit=False, note="E39")
        credit = registered and i < half
        req_no, rfq_no = plant(db, i, qty, need_in, offers, buyer)
        want = oracle(offers, qty, need_in, credit)
        (line,) = quotes.comparison(db, rfq_no, lib.TODAY).lines
        checked += 1
        got_qualifying = {o.supplier_code for o in line.offers if o.compliant}
        drop_ok += got_qualifying == want["qualifying"]
        got_ranked = [o.supplier_code for o in line.offers if o.rank is not None]
        rank_ok += got_ranked == want["ranked"]
        cost_ok += all(int(o.cost * 100) == want["costs"][o.supplier_code] for o in line.offers)
        got_split = line.split.total if line.split else None
        split_match = (line.split_recommended == want["worthwhile"]) and (
            not want["worthwhile"] or int(D(got_split) * 100) == want["split_total"]
        )
        split_ok += split_match
        rec_ok += line.recommended == want["recommended"]
        for label, ok in (
            ("qualifying", got_qualifying == want["qualifying"]),
            ("ranking", got_ranked == want["ranked"]),
            ("split", split_match),
            ("recommendation", line.recommended == want["recommended"]),
        ):
            if not ok:
                bad.append(f"{name} ({rfq_no}): {label} differs; oracle {want}, platform ranked {got_ranked}, "
                           f"recommended {line.recommended}, split {line.split}")  # fmt: skip

        # select: the recommended split, else a random whole offer (the recommendation more often than not)
        if want["worthwhile"] and line.split:
            parts = [(p.supplier_code, p.qty) for p in line.split.parts]
            needs = len(want["qualifying"]) < MIN_QUOTES
            pick: str | None = None
        else:
            wholes = [o.supplier for o in offers if o.cap is None]
            if not wholes:
                continue
            pick = rng.choice(wholes + ([want["recommended"]] * 2 if want["recommended"] else []))
            parts = []
            needs = pick != want["recommended"] or pick != want["cheapest"] or len(want["qualifying"]) < MIN_QUOTES
        selections += 1
        try:
            if pick is None:
                quotes.award(db, rfq_no, {}, buyer, as_of=lib.TODAY, splits={1: parts})
            else:
                quotes.award(db, rfq_no, {1: pick}, buyer, as_of=lib.TODAY)
            refused = False
        except quotes.QuoteError as exc:
            refused = "reason is required" in str(exc)
            db.rollback()
        reason_ok += refused == needs
        if refused != needs:
            bad.append(f"{name}: platform {'asked for' if refused else 'did not ask for'} a reason, oracle "
                       f"{'needs' if needs else 'does not need'} one (pick {pick or parts})")  # fmt: skip
        if refused and pick is None:
            quotes.award(db, rfq_no, {}, buyer, reason="E39: fewer quotes", as_of=lib.TODAY, splits={1: parts})
            refused = False
        if pick is None and not refused:
            split_orders += 1
            made = service.order_request(db, world.slug, req_no, as_of=lib.TODAY)
            got = sorted(
                (r[0], D(r[1]))
                for r in db.execute(
                    text(
                        "select p.supplier_code, l.qty from po p join po_line l on l.po_no = p.no where p.request_no = :n"
                    ),
                    {"n": req_no},
                )
            )
            ok = len(made.pos) == len(parts) and got == sorted((s, D(q)) for s, q in parts)
            orders_ok += ok
            if not ok:
                bad.append(f"{name}: orders {got} for the split {parts} ({made.skipped})")

    awards = db.execute(
        text("select count(*), count(*) filter (where rule_version like 'supplier_selection v%') from audit_event "
             "where action = 'rfq.award'")
    ).one()  # fmt: skip
    if awards[0] != awards[1] or awards[0] == 0:
        bad.append(f"audit: {awards[1]} of {awards[0]} selections name the rule version")

    # the printable RFQ, read back
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument(rfq_document.rfq_document(db, "E39-RFQ-0", "E39-A"))
    try:
        page = "\n".join(pdf[k].get_textpage().get_text_range() for k in range(len(pdf)))
    finally:
        pdf.close()
    due = lib.TODAY + timedelta(days=5)
    need = lib.TODAY + timedelta(days=10)
    for expected in ("REQUEST FOR QUOTATION E39-RFQ-0", "E39 widget", f"{need:%d %b %Y}", f"Quote by: {due:%d %b %Y}",
                     "Deliver to", "Delivery terms:", "Payment terms:"):  # fmt: skip
        if expected not in page:
            bad.append(f"printable RFQ: '{expected}' is missing")

    out.metrics = {
        "quote sets compared": checked,
        "must-haves correct %": pct(drop_ok, checked),
        "total costs correct %": pct(cost_ok, checked),
        "ranking correct %": pct(rank_ok, checked),
        "split decision correct %": pct(split_ok, checked),
        "recommendation correct %": pct(rec_ok, checked),
        "reason rule correct %": pct(reason_ok, selections),
        "split selections ordered per supplier": f"{orders_ok} of {split_orders}",
        "input credit claimed in the first half": registered,
    }
    if bad:
        out.find("high", f"{len(bad)} disagreement(s) with the oracle; first: {bad[0]}")
    if split_orders == 0:
        out.find("medium", "no quote set made a split worth selecting: the split path was not exercised")
    return out
