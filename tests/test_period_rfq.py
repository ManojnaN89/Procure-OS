"""Period RFQs (task 12.1, D40 route B): prices asked for a period with no purchase request, on Apex Pumps as
`demo reset` leaves it (business date Monday 23 November 2026). Who is asked and how many quotes are wanted are the
operational rules; the suggested quantity is the engine's; nothing is committed; sending waits for a buyer (D42)."""

import csv
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from period_support import P1_DAY, ROOT, Co, clear, make
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from p2p.formulas.calendar import pin_today
from p2p.sourcing import price_review

D = Decimal
TRUTH = ROOT / "data" / "datasets" / "apex-v1" / "truth" / "truth_period_sourcing.csv"


def truth(scenario: str) -> list[dict[str, str]]:
    with TRUTH.open(encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if r["scenario"] == scenario]


@pytest.fixture(scope="module")
def apex_co(control_db: str) -> Iterator[Co]:
    yield from make(control_db, "apex", "apexpumps.example")


@pytest.fixture
def apex(apex_co: Co) -> Co:
    pin_today(P1_DAY)
    clear(apex_co)
    return apex_co


def test_p1_a_period_rfq_has_no_request_its_line_points_to_a_period_line_and_waits_for_a_buyer(apex: Co) -> None:
    budget_before = apex.all("select * from budget_actual order by 1")

    made = apex.period_rfq("PLT-CKN", "2027-01-01", "2027-03-31", [("RM-08", "900")])

    assert made.status_code == 201, made.text
    got = made.json()
    rfq = got["rfq_no"]
    (p1,) = truth("P1")
    assert got["status"] == "draft" and got["invited"] == ["SUP-003", "SUP-005", "SUP-001"] and got["min_quotes"] == 3
    assert got["lines"] == [{"line_no": 1, "item_code": "RM-08", "qty": "900", "suggested_qty": p1["suggested_qty"]}]
    kind, request, start, end, status, rule = apex.one(
        "select kind, request_no, period_from, period_to, status, min_quotes_rule from rfq where no = :n", n=rfq
    )
    assert (kind, request, start, end, status) == ("period", None, date(2027, 1, 1), date(2027, 3, 31), "draft")
    assert rule.startswith("buying_route")  # the band above Rs 2,00,000 asks three quotes
    line = apex.one(
        "select l.request_no, l.request_line_no, d.qty, d.uom, d.suggested_qty, d.status, l.need_by from rfq_line l "
        "join period_demand_line d on d.id = l.period_line_id where l.rfq_no = :n",
        n=rfq,
    )
    assert tuple(line) == (None, None, D("900.0000"), "KGS", D(p1["suggested_qty"]), "on_rfq", date(2027, 1, 1))
    assert (
        apex.mails == [] and apex.one("select status from proposal where id = :i", i=got["proposal_id"])[0] == "pending"
    )
    assert apex.all("select * from budget_actual order by 1") == budget_before
    words = apex.one("select suggestion from period_demand_line")[0]
    assert (
        words
        == "Ordered 3009.5 KGS in the last 12 months (752.375 KGS for 3 months); 216 KGS of open requirements in the period"
    )


def test_p1_the_buyer_confirms_and_the_mail_names_the_period_and_the_place_not_a_request(apex: Co) -> None:
    rfq = apex.period_rfq("PLT-CKN", "2027-01-01", "2027-03-31", [("RM-08", "900")]).json()["rfq_no"]

    sent = apex.confirm(rfq)

    assert sent.json() == {"results": [{"rfq_no": rfq, "status": "sent", "sent": 3}]}
    assert len(apex.mails) == 3
    body = apex.mails[0]["body"]
    assert (
        "Prices are asked for the period 01 Jan 2027 to 31 Mar 2027, for orders placed during it. The quantities are "
        "our estimate for the period, not a commitment to buy." in body
    )
    assert "Deliver to: Plant, Chakan (Pune)" in body and "REQ-" not in body
    who, via = apex.one("select confirmed_by, confirmed_via from rfq where no = :n", n=rfq)
    assert (who, via) == (apex.uid("buyer"), "bulk")


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"period_from": "2026-11-23"}, "the period must start after today"),
        ({"period_to": "2028-01-01"}, "a period is at most 12 months"),
        ({"lines": []}, "at least one item line"),
        ({"lines": [{"item_code": "", "qty": "1"}]}, "has no item"),
        ({"lines": [{"item_code": "RM-08", "qty": "0"}]}, "above zero"),
        ({"lines": [{"item_code": "NOPE", "qty": "1"}]}, "there is no item NOPE"),
        (
            {"lines": [{"item_code": "RM-08", "qty": "1"}, {"item_code": "PK-01", "qty": "1"}]},
            "one period RFQ per category",
        ),
        ({"location_code": "NOWHERE"}, "there is no location NOWHERE"),
    ],
)
def test_a_period_rfq_that_makes_no_sense_is_refused_and_nothing_is_written(apex: Co, body: dict, message: str) -> None:
    payload = {"location_code": "PLT-CKN", "period_from": "2027-01-01", "period_to": "2027-03-31",
               "lines": [{"item_code": "RM-08", "qty": "900"}], **body}  # fmt: skip

    r = apex.call("buyer", "POST", "/api/rfqs/period", payload)

    assert r.status_code == 422 and message in r.text, r.text
    assert apex.one("select count(*) from rfq where kind = 'period'")[0] == 0
    assert apex.one("select count(*) from period_demand_line")[0] == 0


def test_a_capital_item_is_bought_on_a_request_not_a_period_rfq(apex: Co) -> None:
    apex.db.execute(text("update item set capex = true where code = 'RM-07'"))
    apex.db.commit()
    try:
        r = apex.period_rfq("PLT-CKN", "2027-01-01", "2027-03-31", [("RM-07", "10")])
        assert r.status_code == 422 and "capital item" in r.text
    finally:
        apex.db.execute(text("update item set capex = false where code = 'RM-07'"))
        apex.db.commit()


def test_an_open_period_rfq_for_the_same_item_and_place_on_a_shared_day_refuses_a_second(apex: Co) -> None:
    first = apex.period_rfq("PLT-CKN", "2027-01-01", "2027-03-31", [("RM-08", "900")]).json()["rfq_no"]

    clash = apex.period_rfq("PLT-CKN", "2027-03-31", "2027-05-31", [("RM-08", "100")])
    after = apex.period_rfq("PLT-CKN", "2027-04-01", "2027-05-31", [("RM-08", "100")])  # no shared day
    elsewhere = apex.period_rfq("PLT-SND", "2027-01-01", "2027-03-31", [("RM-08", "100")])

    assert clash.status_code == 422
    assert clash.json()["detail"] == f"{first} already asks for RM-08 at PLT-CKN for 01 Jan 2027 to 31 Mar 2027"
    assert after.status_code == 201 and elsewhere.status_code == 201


def test_an_rfq_line_points_to_a_request_line_or_a_period_line_never_neither_never_both(apex: Co) -> None:
    rfq = apex.period_rfq("PLT-CKN", "2027-01-01", "2027-03-31", [("RM-08", "900")]).json()["rfq_no"]
    pid = apex.one("select period_line_id from rfq_line where rfq_no = :n", n=rfq)[0]

    for request, line, period_line in ((None, None, None), ("REQ-X", 1, pid)):
        with pytest.raises(IntegrityError, match="rfq_line_one_source"):
            apex.db.execute(
                text(
                    "insert into rfq_line (rfq_no, line_no, request_no, request_line_no, period_line_id, qty) "
                    "values (:r, 9, :q, :l, :p, 1)"
                ),
                {"r": rfq, "q": request, "l": line, "p": period_line},
            )
        apex.db.rollback()


def test_the_engine_suggests_a_quantity_and_says_both_figures(apex: Co) -> None:
    r = apex.call(
        "buyer", "GET",
        "/api/rfqs/period/suggest?item_code=RM-08&location_code=PLT-CKN&period_from=2027-01-01&period_to=2027-03-31",
    )  # fmt: skip

    assert r.status_code == 200
    assert r.json() == {
        "suggested_qty": "753", "open_need": "216", "ordered_12m": "3009.5", "months": 3, "uom": "KGS",
        "suggestion": "Ordered 3009.5 KGS in the last 12 months (752.375 KGS for 3 months); 216 KGS of open "
        "requirements in the period",
    }  # fmt: skip
    bad = apex.call("buyer", "GET", "/api/rfqs/period/suggest?item_code=RM-08&location_code=PLT-CKN&period_from=x")
    assert bad.status_code == 422
    assert apex.call("production", "GET", "/api/rfqs/period/suggest").status_code == 403
    today = apex.call(
        "buyer", "GET",
        "/api/rfqs/period/suggest?item_code=RM-08&location_code=PLT-CKN&period_from=2026-11-23&period_to=2027-01-31",
    )  # fmt: skip
    assert today.status_code == 422 and "the period must start after today" in today.text
    # what was ordered is the old system's order history loaded with the stock history (no receipt counts)
    assert apex.one("select sum(qty) from order_history where item_code = 'RM-08'")[0] == D("3009.5000")


def test_cancelling_a_period_rfq_withdraws_its_sending_and_mails_nobody(apex: Co) -> None:
    got = apex.period_rfq("PLT-CKN", "2027-01-01", "2027-03-31", [("RM-08", "900")]).json()

    assert apex.call("buyer", "POST", f"/api/rfqs/{got['rfq_no']}/cancel", {"reason": ""}).status_code == 422
    done = apex.call("buyer", "POST", f"/api/rfqs/{got['rfq_no']}/cancel", {"reason": "Plan changed"})

    assert done.status_code == 200 and done.json() == {"status": "cancelled"}
    assert apex.one("select status from rfq where no = :n", n=got["rfq_no"])[0] == "cancelled"
    assert apex.one("select status from period_demand_line")[0] == "cancelled"
    assert apex.one("select status from proposal where id = :i", i=got["proposal_id"])[0] == "expired"
    assert apex.mails == []


def test_an_awarded_period_rfq_cannot_be_cancelled(apex: Co) -> None:
    from period_support import p1_to_proposal

    rfq = p1_to_proposal(apex)

    r = apex.call("buyer", "POST", f"/api/rfqs/{rfq}/cancel", {"reason": "x"})

    assert r.status_code == 409 and "award proposed" in r.text


def test_p6_an_expiry_review_prefills_and_starts_a_period_rfq_and_is_closed_by_it(apex: Co) -> None:
    pin_today(date(2027, 2, 1))
    price_review.run(apex.db, apex.slug, date(2027, 2, 1))
    review = apex.one(
        "select id from task where kind = 'strategic_review' and status = 'open' and payload->>'trigger' = 'expiry' "
        "and payload->>'contract' = 'CNT-0001'"
    )
    assert review is not None
    task_id = review[0]
    refused = apex.call("buyer", "POST", f"/api/reviews/{task_id}/close", {"outcome": "rfq_started", "reason": "x"})
    assert refused.status_code == 422 and "start the period RFQ from the review" in refused.text

    pre = apex.call("buyer", "GET", f"/api/reviews/{task_id}/period-rfq").json()

    rows = truth("P6")
    assert pre["locations"] == ["PLT-CKN"]
    assert (pre["period_from"], pre["period_to"]) == ("2027-04-01", "2028-03-31")
    assert pre["lines"] == [{"item_code": r["item"], "suggested_qty": r["suggested_qty"]} for r in rows]
    lines = [(ln["item_code"], ln["suggested_qty"]) for ln in pre["lines"]]
    made = apex.period_rfq("PLT-CKN", pre["period_from"], pre["period_to"], lines, review_task_id=task_id)
    assert made.status_code == 201, made.text
    rfq = made.json()["rfq_no"]
    status, payload = apex.one("select status, payload from task where id = :i", i=task_id)
    assert status == "done" and payload["outcome"] == "rfq_started" and payload["rfq_no"] == rfq
    assert apex.one("select review_task_id from rfq where no = :n", n=rfq)[0] == task_id
    clash = apex.period_rfq("PLT-CKN", "2027-06-01", "2027-06-30", [("RM-01", "10")])
    assert clash.status_code == 422 and "already asks for RM-01 at PLT-CKN" in clash.text
    assert apex.call("buyer", "GET", f"/api/reviews/{task_id}/period-rfq").status_code == 409  # closed


def test_the_rfq_screens_show_the_kind_the_period_and_the_suggestion(apex: Co) -> None:
    rfq = apex.period_rfq("PLT-CKN", "2027-01-01", "2027-03-31", [("RM-08", "900")]).json()["rfq_no"]

    listed = apex.call("buyer", "GET", "/api/rfqs?kind=period").json()
    detail = apex.call("buyer", "GET", f"/api/rfqs/{rfq}").json()

    assert [r["id"] for r in listed] == [rfq] and listed[0]["kind"] == "period" and listed[0]["waiting_to_send"]
    assert listed[0]["title"] == "Raw materials: 1 item for 01 Jan 2027 to 31 Mar 2027"
    assert apex.call("buyer", "GET", "/api/rfqs?kind=operational").json() == []
    assert (detail["kind"], detail["period_from"], detail["confirmed_at"]) == ("period", "2027-01-01", None)
    assert detail["lines"][0]["suggested_qty"] == "753" and detail["award_decision"] is None
