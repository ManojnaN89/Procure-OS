"""One estimate rule for every request (plan step 3): the contract price in force for the quantity, else the last price
paid on an order that is not a draft and not cancelled, else the item's standard price; the request line keeps which one
it used. Worked on the main demo company, Apex Pumps, for every way a request is raised: the form, the plain-words
intake, replenishment and the store check."""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import text
from test_intake_agent import gateway, reading
from test_material_requests import MO1, Apex, _check, _orders, _set_stock, apex, company  # noqa: F401 - fixtures

from p2p.agents import intake
from p2p.formulas.calendar import business_today
from p2p.orders.quotes import last_paid
from p2p.requests import estimates, replenishment

D = Decimal


def _estimate(apex: Apex, item: str, qty: str = "10") -> tuple[Decimal, str] | None:
    standard = apex.db.execute(text("select standard_price from item where code = :c"), {"c": item}).scalar_one()
    got = estimates.for_item(apex.db, item, "NOS", D(qty), business_today(), standard)
    return (got.price, got.source) if got else None


def _line_source(apex: Apex, no: str, item: str) -> tuple[Decimal, str | None]:
    row = apex.db.execute(
        text("select est_price, est_source from request_line where request_no = :n and item_code = :i"),
        {"n": no, "i": item},
    ).one()
    return D(row[0]), row[1]


# ---- the chain at its edges -----------------------------------------------------------------------


def test_a_contract_in_force_today_is_the_estimate_and_one_that_ended_yesterday_is_not(apex: Apex) -> None:
    # given the castings on contract CNT-0001 at Rs 650
    assert _estimate(apex, "RM-01") == (D(650), "contract CNT-0001")
    today = business_today()
    paid = last_paid(apex.db, ["RM-01"]).get("RM-01")

    # when the price record ended yesterday, then the contract no longer counts
    apex.db.execute(
        text("update price_record set valid_to = :d where item_code = 'RM-01'"), {"d": today - timedelta(1)}
    )
    fallback = _estimate(apex, "RM-01")
    assert fallback == ((paid[0], f"last price paid on {paid[1]}") if paid else (D(650), "standard price"))

    # when it starts today, then it counts again
    apex.db.execute(
        text("update price_record set valid_from = :d, valid_to = null where item_code = 'RM-01'"), {"d": today}
    )
    assert _estimate(apex, "RM-01") == (D(650), "contract CNT-0001")
    apex.db.rollback()


def test_an_item_never_ordered_and_off_contract_is_estimated_at_its_standard_price(apex: Apex) -> None:
    # given an item with no contract price and no order line at all
    item = apex.db.execute(
        text(
            "select i.code from item i where i.standard_price > 0 and i.base_uom = 'NOS' "
            "and not exists (select 1 from po_line l where l.item_code = i.code) "
            "and not exists (select 1 from price_record p where p.item_code = i.code and p.contract_code is not null) "
            "order by i.code limit 1"
        )
    ).scalar_one()
    standard = apex.db.execute(text("select standard_price from item where code = :c"), {"c": item}).scalar_one()

    assert _estimate(apex, item) == (D(standard), "standard price")


def _paid(apex: Apex, item: str, price: str, po_no: str, *, status: str = "sent", days_ago: int = 0) -> None:
    """An order (and the request it came from) that paid `price` for the item, made `days_ago` days ago. Not committed:
    the test rolls it back."""
    x = apex.db.execute
    x(
        text(
            "insert into request (no, ship_to_location, type, channel, status, need_by) "
            "values (:r, 'PLT-CKN', 'goods', 'form', 'approved', current_date)"
        ),
        {"r": f"R-{po_no}"},
    )
    x(
        text(
            "insert into request_line (request_no, line_no, item_code, qty, uom, need_by, delivery_location) "
            "values (:r, 1, :i, 1, 'NOS', current_date, 'PLT-CKN')"
        ),
        {"r": f"R-{po_no}", "i": item},
    )
    x(
        text(
            "insert into po (no, supplier_code, ship_to_location, status, created_at) "
            "values (:p, 'SUP-002', 'PLT-CKN', :s, now() - make_interval(days => :d))"
        ),
        {"p": po_no, "s": status, "d": days_ago},
    )
    x(
        text(
            "insert into po_line (po_no, line_no, request_no, request_line_no, item_code, qty, uom, unit_price, gst_rate, "
            "taxable, source) values (:p, 1, :r, 1, :i, 1, 'NOS', :price, 18, :price, 'award')"
        ),
        {"p": po_no, "r": f"R-{po_no}", "i": item, "price": D(price)},
    )


def test_the_last_price_paid_is_the_latest_order_and_never_a_cancelled_or_draft_one(apex: Apex) -> None:
    # given no order yet: the bearings (no contract) are estimated at their standard price
    assert _estimate(apex, "RM-06") == (D(38), "standard price")

    # when they were bought three times, then the latest order counts, not the lowest or the first
    _paid(apex, "RM-06", "41.00", "PO-T1", days_ago=30)
    _paid(apex, "RM-06", "37.75", "PO-T2", days_ago=20)
    _paid(apex, "RM-06", "39.50", "PO-T3", days_ago=10)
    assert _estimate(apex, "RM-06") == (D("39.50"), "last price paid on PO-T3")

    # and a later order that was cancelled, or is still a draft, is not a price paid
    _paid(apex, "RM-06", "45.00", "PO-T4", status="cancelled", days_ago=1)
    _paid(apex, "RM-06", "46.00", "PO-T5", status="draft")
    assert _estimate(apex, "RM-06") == (D("39.50"), "last price paid on PO-T3")

    # and a contract in force still comes first
    _paid(apex, "RM-01", "640.00", "PO-T6")
    assert _estimate(apex, "RM-01") == (D(650), "contract CNT-0001")
    apex.db.rollback()


# ---- every source uses it and keeps where it came from --------------------------------------------


def test_the_store_check_keeps_the_source_of_each_estimate(apex: Apex) -> None:
    _orders(apex, {"MO-1": ("2026-12-05", MO1)})

    req = _check(apex).purchase_requests[0]

    assert req.no is not None
    assert _line_source(apex, req.no, "RM-01") == (D(650), "contract CNT-0001")
    assert _line_source(apex, req.no, "RM-06") == (D(38), "standard price")  # never bought: no last price paid


def test_a_request_from_the_form_is_estimated_at_the_contract_price(apex: Apex) -> None:
    # given the casting's standard price is set apart from its contract price
    apex.db.execute(text("update item set standard_price = 700 where code = 'RM-01'"))
    apex.db.commit()
    try:
        got = intake.create_from_form(
            apex.db, apex.uid("production"), [("RM-01", D(5))], business_today() + timedelta(days=20)
        )
        (no,) = got.request_nos
        assert _line_source(apex, no, "RM-01") == (D(650), "contract CNT-0001")
        value = apex.db.execute(text("select total_value from request where no = :n"), {"n": no}).scalar_one()
        assert value == D("3250.00")
    finally:
        apex.db.execute(text("update item set standard_price = 650 where code = 'RM-01'"))
        apex.db.commit()


def test_a_request_in_plain_words_is_estimated_the_same_way(apex: Apex) -> None:
    gw, _ = gateway(reading([("RM-01", 4)], need_by="2026-12-20"))

    got = intake.handle(apex.db, apex.slug, apex.uid("production"), "need 4 RM-01 by 20 Dec 2026", gw)

    assert got.status == "drafted", got.message
    assert _line_source(apex, got.request_nos[0], "RM-01") == (D(650), "contract CNT-0001")


def test_replenishment_estimates_at_the_contract_price_and_says_so(apex: Apex) -> None:
    # given the impellers (on contract at Rs 220) planned min/max and down at the reorder point
    apex.db.execute(
        text(
            "update item_location set reorder_policy = 'minmax', reorder_point = 20, min_qty = 20, max_qty = 60 "
            "where item_code = 'RM-03'"
        )
    )
    apex.db.execute(text("update item set standard_price = 250 where code = 'RM-03'"))
    apex.db.commit()
    _set_stock(apex, "RM-03", "20")
    try:
        planned, _ = replenishment.plan(apex.db, business_today())
        (line,) = [ln for r in planned for ln in r.lines if ln.item_code == "RM-03"]
        assert (line.est_price, line.est_source, line.value) == (D(220), "contract CNT-0001", D(220) * line.qty)
    finally:
        apex.db.execute(text("update item set standard_price = 220 where code = 'RM-03'"))
        apex.db.commit()
