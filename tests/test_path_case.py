"""Path A or B (D40 steps 3-5, D43, block 6, OP-17 a): a contract line whose price is above the market beyond every
threshold, and not urgent, waits for the buyer. Path B (ask for quotes) is recommended; path A (buy on the contract)
needs a reason, and its order is never touchless and is made by the buyer who chose it."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import TODAY, _logins, shop  # noqa: F401 - fixtures
from test_sourcing import (  # noqa: F401 - fixtures
    PASSWORD,
    Outbox,
    _headers,
    _price,
    _request,
    _source,
    db,
    outbox,
)

from p2p.formulas import path, price_gap
from p2p.main import app
from p2p.orders import workflow
from p2p.platform import users
from p2p.sourcing import benchmarks, notes, service

D = Decimal
client = TestClient(app)
RULE = price_gap.ThresholdRule("price_threshold v1", D(5), D(10), D(25000), 45, 1, 60)


def _test(
    contract: str,
    market: str,
    qty: str,
    *,
    priority: str | None = None,
    need_by: date | None = None,
) -> bool:
    return path.test(
        D(contract), [D(market)], D(qty), RULE, priority=priority, need_by=need_by or date(2027, 1, 15),
        on=date(2026, 11, 23), lead_days=10, rfq_days=5,
    ).case  # fmt: skip


def test_a_gap_exactly_at_a_threshold_is_not_above_it() -> None:
    assert _test("650", "600", "600") is True  # 7.6923%, 50 per unit, 30,000
    assert _test("200", "190", "5000") is False  # 10.00 per unit: not above 10
    assert _test("1000", "950", "500.01") is False  # 5.0000%: not above 5
    assert _test("650", "600", "500") is False  # saving 25,000.00: not above
    assert _test("650", "600", "500.02") is True


def test_an_urgent_line_stays_on_the_contract_by_priority_or_by_date() -> None:
    assert _test("650", "600", "600", priority="urgent") is False
    assert _test("650", "600", "600", priority="emergency") is False
    assert _test("650", "600", "600", need_by=date(2026, 12, 7)) is False  # 14 days < 10 lead + 5 RFQ days
    assert _test("650", "600", "600", need_by=date(2026, 12, 8)) is True  # 15 days


def test_no_counting_benchmark_is_no_case() -> None:
    assert path.test(D(650), [], D(600), RULE, priority=None, need_by=None, on=date(2026, 11, 23), lead_days=0,
                     rfq_days=5).case is False  # fmt: skip


# ---- a company ------------------------------------------------------------------------------------------------------


def _market(db: Session, price: str = "85") -> None:
    """Item A on contract K-ACTIVE at 100.00 with S1, and a verified national benchmark."""
    slug, sfx = db.info["slug"], db.info["sfx"]
    enter = users.create_user(slug, f"be{sfx}@x.example", PASSWORD, "Bench Enterer", "Buyer")
    check = users.create_user(slug, f"bv{sfx}@x.example", PASSWORD, "Bench Checker", "Approver")
    _price(db, "S1", "A", "100")
    data = {"item_code": "A", "price": D(price), "uom": "NOS", "effective_date": date(2026, 11, 1),
            "region_code": None, "source_kind": "price_list", "source_name": "Steel index"}  # fmt: skip
    bid = benchmarks.add(db, data, enter, today=TODAY)
    benchmarks.verify(db, bid, check, "checked")


def _case(db: Session, qty: str = "2000", need_by: str = "2027-01-15") -> str:
    no = _request(db, "R-1", [("A", qty, None, "C1")])
    db.execute(
        text("update request_line set need_by = :d where request_no = :n"),
        {"d": need_by, "n": no},
    )
    db.commit()
    _source(db, no)
    return no


def _decision(db: Session) -> tuple[str | None, str]:
    return db.execute(text("select path_case, method from sourcing_decision")).one()  # type: ignore[return-value]


def test_a_contract_line_above_the_market_waits_for_the_buyer_with_path_b_recommended(
    shop: Session,
) -> None:  # noqa: F811
    _market(shop)

    no = _case(shop)

    assert _decision(shop) == ("waiting", "contract")
    assert shop.execute(text("select count(*) from po")).scalar_one() == 0
    rec = shop.execute(
        text("select summary, engine_figures, status from recommendation where kind = 'path_case'")
    ).one()
    assert rec[0] == (
        "Line 1, Bearing: contract K-ACTIVE with Alpha Traders at ₹ 100.00 per NOS fits, but the market is lower: "
        "₹ 85.00 per NOS (Steel index, 01 Nov 2026), a gap of ₹ 15.00 (15.00%), ₹ 30,000.00 on 2000 NOS. Recommended: "
        "path B, ask for quotes. Path A buys on the contract; the contract stays valid either way."
    )
    f = rec[1]
    assert (f["contract_price"], f["market"], f["gap"], f["gap_pct"], f["saving"]) == (
        "100.0000", "85.0000", "15.0000", "15.0000", "30000.00",
    )  # fmt: skip
    task = shop.execute(text("select title from task where kind = 'path_case'")).scalar_one()
    assert task == f"Choose path A or B for {no} line 1 (A)"


def test_an_urgent_line_is_ordered_on_the_contract_as_before(shop: Session) -> None:  # noqa: F811
    _market(shop)

    _case(shop, need_by="2026-11-20")

    assert _decision(shop) == (None, "contract")
    assert shop.execute(text("select count(*) from recommendation where kind = 'path_case'")).scalar_one() == 0


def test_path_a_needs_a_reason_and_its_order_is_never_touchless_and_made_by_the_buyer(
    shop: Session,
) -> None:  # noqa: F811
    _market(shop)
    no = _case(shop)
    people = _logins(shop)

    with pytest.raises(service.PathError, match="a reason is needed") as err:
        service.decide_path(
            shop,
            shop.info["slug"],
            no,
            1,
            "A",
            people["buyer"],
            reason=" ",
            as_of=TODAY,
        )
    assert err.value.status == 422

    got = service.decide_path(shop, shop.info["slug"], no, 1, "A", people["buyer"], reason="supply is assured",
                              as_of=TODAY)  # fmt: skip

    (po_no,) = got["po_nos"]
    po = shop.execute(
        text("select taxable, touchless, touchless_note from po where no = :n"),
        {"n": po_no},
    ).one()
    assert po[0] == D("200000.00") and po[1] is False and "a person chose path A (D43)" in po[2]
    assert shop.execute(text("select path_case, path_confirmed_by from sourcing_decision")).one() == (
        "chosen_a", people["buyer"],
    )  # fmt: skip
    made_by = {str(r[0]) for r in shop.execute(text(workflow._MAKERS_SQL), {"n": po_no})}
    assert str(people["buyer"]) in made_by  # the buyer who chose path A is one of the order's makers
    rec = shop.execute(text("select status, decision_note from recommendation where kind = 'path_case'")).one()
    assert rec == ("rejected", "supply is assured")
    assert shop.execute(text("select status from task where kind = 'path_case'")).scalar_one() == "done"
    assert shop.execute(text("select count(*) from audit_event where action = 'sourcing.path'")).scalar_one() == 1


def test_path_b_makes_an_rfq_draft_that_waits_for_the_send_to_be_confirmed(
    shop: Session,
) -> None:  # noqa: F811
    _market(shop)
    no = _case(shop)
    people = _logins(shop)

    got = service.decide_path(shop, shop.info["slug"], no, 1, "B", people["buyer"], reason=None, as_of=TODAY)

    assert got["path"] == "B" and got["rfq_no"]
    assert shop.execute(text("select status from rfq where no = :n"), {"n": got["rfq_no"]}).scalar_one() == "draft"
    assert (
        shop.execute(
            text("select count(*) from proposal where action = 'rfq.send' and status = 'pending'")
        ).scalar_one()
        == 1
    )
    assert shop.execute(text("select count(*) from recommendation where kind = 'rfq_draft'")).scalar_one() == 1
    assert shop.execute(text("select path_case from sourcing_decision")).scalar_one() == "chosen_b"
    assert shop.execute(text("select count(*) from po")).scalar_one() == 0


def test_a_case_that_changed_is_refused_and_tested_again(shop: Session) -> None:  # noqa: F811
    _market(shop)
    no = _case(shop)
    people = _logins(shop)
    shop.execute(text("update request set priority = 'urgent' where no = :n"), {"n": no})
    shop.commit()

    with pytest.raises(service.PathError, match="the case changed: .*no case any more"):
        service.decide_path(
            shop,
            shop.info["slug"],
            no,
            1,
            "A",
            people["buyer"],
            reason="x",
            as_of=TODAY,
        )

    assert shop.execute(text("select path_case from sourcing_decision")).scalar_one() is None
    assert shop.execute(text("select status from recommendation where kind = 'path_case'")).scalar_one() == "stale"
    assert notes  # the line is ordered on the contract by the engine (the order job is queued)


def test_another_company_cannot_decide_this_case(shop: Session) -> None:  # noqa: F811
    _market(shop)
    no = _case(shop)
    other = "q" + shop.info["sfx"]
    from p2p.platform import tenants

    assert tenants.create_tenant(other, load_reference=False).ok
    stranger = users.create_user(other, f"st{shop.info['sfx']}@x.example", PASSWORD, "Stranger", "Buyer")
    res = client.post(
        f"/api/requests/{no}/lines/1/path",
        json={"path": "B"},
        headers=_headers(other, stranger, "Buyer"),
    )

    assert res.status_code in (404, 409)
    assert shop.execute(text("select path_case from sourcing_decision")).scalar_one() == "waiting"
