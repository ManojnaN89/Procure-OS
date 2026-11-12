"""Combined requisitions (P1-31): shortfalls of several production orders, and the restock of the same item, needed at
one delivery place within the combining window are bought on one requisition line; each source keeps its own quantity,
so cancelling one order takes its part off the line and replenishment counts only what is bought for stock. Worked on
the main demo company, Apex Pumps."""

import csv
import uuid
from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_material_requests import Apex, _lines, _set_stock

from p2p.formulas.calendar import business_today
from p2p.formulas.combining import combined_quantity, within_window
from p2p.main import app
from p2p.platform import demo, reference, routing, tenants
from p2p.platform.sod import user
from p2p.requests import demand, material, replenishment

ROOT = Path(__file__).resolve().parents[1]
APEX = ROOT / "data" / "datasets" / "apex-v1"
D = Decimal
client = TestClient(app)
FIRST = date(2026, 12, 5)


@pytest.fixture(scope="module")
def company(control_db: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Apex]:
    slug = "cb" + uuid.uuid4().hex[:8]
    demo.reset_company(demo.company("apex"), APEX, credentials=None, slug=slug)
    with Session(routing.engine_for(slug)) as session:
        session.execute(
            text("insert into location (code, name, loc_short) values ('WH-PUN', 'Warehouse, Pune', 'PUN')")
        )
        session.commit()
        yield Apex(slug, session, tmp_path_factory.mktemp("erp"))
    routing.clear_engine_cache()
    tenants.drop_tenant(slug)


@pytest.fixture
def apex(company: Apex) -> Apex:
    """Apex as the demo reset leaves it, without its own demand, with the default combining window."""
    db = company.db
    db.rollback()
    for sql in (
        "delete from material_request_buy",
        "delete from material_request_line",
        "delete from material_request",
        "delete from material_requirement",
        "delete from production_order",
        "delete from product_demand",
        "delete from demand_import",
        "delete from approval_step",
        "delete from request",
        "delete from stock_movement where source = 'platform'",
        "delete from number_counter where document_type in ('request', 'material_request')",
        "delete from org_setting where key = 'combine_window_days'",
    ):
        db.execute(text(sql))
    db.commit()
    return company


def _orders(apex: Apex, orders: dict[str, tuple[date, list[tuple[str, str]], str]]) -> dict[str, str]:
    """Import production orders {order_no: (need_by, [(item, qty)], location)}; returns order -> material request."""
    folder = apex.folder / uuid.uuid4().hex[:6]
    folder.mkdir(parents=True)
    with (folder / "production_order.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["order_no", "product_code", "qty", "need_by", "location_code"])
        w.writerows([no, "FG-01", "50", need.isoformat(), loc] for no, (need, _, loc) in orders.items())
    with (folder / "production_order_line.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["order_no", "item_code", "qty"])
        w.writerows([no, item, qty] for no, (_, parts, _) in orders.items() for item, qty in parts)
    report = demand.import_demand(apex.db, folder)
    assert report.rejected == []
    return dict(apex.db.execute(text("select order_no, no from material_request")).all())


def _check(apex: Apex, *, submit: bool = True) -> material.CheckResult:
    return material.store_check(apex.db, user(apex.uid("store")), tenant=apex.slug, submit=submit)


def _request_lines(apex: Apex, item: str) -> list[tuple[str, Decimal, str]]:
    """(request, quantity, status) of every request line for the item."""
    rows = apex.db.execute(
        text("select request_no, qty, status from request_line where item_code = :i order by request_no, line_no"),
        {"i": item},
    )
    return [(r[0], r[1], r[2]) for r in rows]


def _bought_for(apex: Apex, no: str, item: str) -> dict[str, Decimal]:
    """Material request -> quantity linked to the request's line for the item."""
    rows = apex.db.execute(
        text(
            "select m.mr_no, sum(b.qty) from material_request_buy b join material_request_line m on m.id = b.mr_line_id "
            "where b.request_no = :n and m.item_code = :i group by m.mr_no"
        ),
        {"n": no, "i": item},
    )
    return {r[0]: r[1] for r in rows}


# ---- the formulas at their edges ----------------------------------------------------------------


@pytest.mark.parametrize(("apart", "combines"), [(0, True), (7, True), (8, False)])
def test_lines_needed_seven_days_apart_combine_and_eight_do_not(apart: int, combines: bool) -> None:
    assert within_window([FIRST, FIRST + timedelta(days=apart)], 7) is combines
    assert within_window([FIRST + timedelta(days=apart), FIRST], 7) is combines  # either order


def test_a_combined_line_is_rounded_up_once_for_all_its_sources() -> None:
    # two needs of 3 and 4 against a minimum order of 10: one line of 10, not two of 10
    assert combined_quantity([D(3), D(4)], base_per_purchase_unit=D(1), moq=D(10)) == D(10)
    # bought in packs of 12: 7 + 6 = 13 pieces is two packs
    assert combined_quantity([D(7), D(6)], base_per_purchase_unit=D(12), moq=D(0)) == D(2)
    assert combined_quantity([D(0), D(0)], base_per_purchase_unit=D(1), moq=D(10)) == D(0)


# ---- combining in the store check ---------------------------------------------------------------


def test_two_orders_bearing_shortfalls_make_one_requisition_line(apex: Apex) -> None:
    # given two orders needing bearings a week apart, with 40 bearings on the shelf
    mrs = _orders(
        apex,
        {
            "MO-A": (FIRST, [("RM-06", "100"), ("RM-01", "10")], "PLT-CKN"),
            "MO-B": (FIRST + timedelta(days=7), [("RM-06", "50")], "PLT-CKN"),
        },
    )

    # when the store checks
    result = _check(apex)

    # then MO-A gets the 40 and lacks 60, MO-B lacks 50, and one line buys the 110 for both
    ((no, qty, status),) = _request_lines(apex, "RM-06")
    assert (qty, status) == (D(110), "open")
    assert _bought_for(apex, no, "RM-06") == {mrs["MO-A"]: D(60), mrs["MO-B"]: D(50)}
    assert _lines(apex, mrs["MO-A"])["RM-06"][1:] == (D(40), D(0), D(60))
    assert _lines(apex, mrs["MO-B"])["RM-06"][1:] == (D(0), D(0), D(50))

    # and MO-B needs no request of its own: its bearing joined MO-A's request, which went for approval at its new value
    first, second = sorted(result.requests, key=lambda c: c.order_no)
    assert first.request is not None and first.request.no == no
    assert second.request is None and second.combined == {"RM-06": no}
    head = apex.db.execute(text("select total_value, need_by, status from request where no = :n"), {"n": no}).one()
    assert head.total_value == D("10680.00")  # 110 bearings at 38 and 10 castings at 650
    assert head.need_by == FIRST
    assert first.request.total_value == D("10680.00") and first.request.outcome == "submitted"
    reason = apex.db.execute(
        text("select reason from request_line where request_no = :n and item_code = 'RM-06'"), {"n": no}
    ).scalar_one()
    assert f"combined with MO-B ({mrs['MO-B']}) short 50" in reason


@pytest.mark.parametrize(("second_department", "lines"), [("PRD", 1), ("STR", 2)])
def test_shortfalls_combine_only_within_one_department(apex: Apex, second_department: str, lines: int) -> None:
    # given two material requests short of bearings within the window, the first for Production, the second for
    # Production too or for Stores (as joiners of two departments are)
    mrs = _orders(
        apex,
        {
            "MO-A": (FIRST, [("RM-06", "100")], "PLT-CKN"),
            "MO-B": (FIRST + timedelta(days=3), [("RM-06", "50")], "PLT-CKN"),
        },
    )
    apex.db.execute(text("update material_request set department_code = 'PRD' where no = :n"), {"n": mrs["MO-A"]})
    apex.db.execute(
        text("update material_request set department_code = :d where no = :n"),
        {"d": second_department, "n": mrs["MO-B"]},
    )
    apex.db.commit()

    # when the store checks
    _check(apex, submit=False)

    # then one department's shortfalls share a line; another department's get their own line and request
    got = _request_lines(apex, "RM-06")
    assert len(got) == lines
    assert sorted(q for _, q, _ in got) == ([D(110)] if lines == 1 else [D(50), D(60)])
    departments = apex.db.execute(
        text("select distinct r.department_code from request r join request_line l on l.request_no = r.no "
             "where l.item_code = 'RM-06' order by 1")
    ).scalars().all()  # fmt: skip
    assert departments == sorted({"PRD", second_department})


def test_orders_eight_days_apart_are_bought_on_separate_lines(apex: Apex) -> None:
    _orders(
        apex,
        {
            "MO-A": (FIRST, [("RM-06", "100")], "PLT-CKN"),
            "MO-B": (FIRST + timedelta(days=8), [("RM-06", "50")], "PLT-CKN"),
        },
    )

    _check(apex)

    assert sorted(q for _, q, _ in _request_lines(apex, "RM-06")) == [D(50), D(60)]


def test_the_window_is_a_company_setting(apex: Apex) -> None:
    apex.db.execute(text("insert into org_setting (key, value) values ('combine_window_days', '10')"))
    apex.db.commit()
    _orders(
        apex,
        {
            "MO-A": (FIRST, [("RM-06", "100")], "PLT-CKN"),
            "MO-B": (FIRST + timedelta(days=8), [("RM-06", "50")], "PLT-CKN"),
        },
    )

    _check(apex)

    assert [q for _, q, _ in _request_lines(apex, "RM-06")] == [D(110)]


def test_the_same_item_for_another_delivery_place_is_not_combined(apex: Apex) -> None:
    _orders(
        apex,
        {
            "MO-A": (FIRST, [("RM-06", "100")], "PLT-CKN"),
            "MO-P": (FIRST + timedelta(days=1), [("RM-06", "50")], "WH-PUN"),  # nothing on the shelf there
        },
    )

    _check(apex)

    places = apex.db.execute(
        text("select delivery_location, qty from request_line where item_code = 'RM-06' order by 1")
    ).all()
    assert [tuple(p) for p in places] == [("PLT-CKN", D(60)), ("WH-PUN", D(50))]


def test_a_request_already_submitted_takes_nothing_more(apex: Apex) -> None:
    # given MO-A's shortfall checked and sent for approval
    _orders(apex, {"MO-A": (FIRST, [("RM-06", "100")], "PLT-CKN")})
    _check(apex)

    # when MO-B, needed two days later, is loaded and checked
    _orders(apex, {"MO-B": (FIRST + timedelta(days=2), [("RM-06", "50")], "PLT-CKN")})
    _check(apex)

    # then it gets its own line: the approver approved what they saw
    assert sorted(q for _, q, _ in _request_lines(apex, "RM-06")) == [D(50), D(60)]


# ---- cancelling one source, and the restock ------------------------------------------------------


def test_cancelling_one_order_takes_its_part_off_the_combined_line(apex: Apex) -> None:
    # given MO-A and MO-B combined on one line of 110 bearings
    mrs = _orders(
        apex,
        {
            "MO-A": (FIRST, [("RM-06", "100")], "PLT-CKN"),
            "MO-B": (FIRST + timedelta(days=3), [("RM-06", "50")], "PLT-CKN"),
        },
    )
    _check(apex)
    ((no, _, _),) = _request_lines(apex, "RM-06")

    # when MO-B is called off
    left = material.cancel(apex.db, apex.uid("store"), mrs["MO-B"], "customer cancelled")

    # then the line buys MO-A's 60 only, the request's value follows, and nothing is left for the buyer to sort out
    assert left == []
    assert _request_lines(apex, "RM-06") == [(no, D(60), "open")]
    assert apex.db.execute(text("select total_value from request where no = :n"), {"n": no}).scalar_one() == D(
        "2280.00"
    )
    assert _lines(apex, mrs["MO-A"])["RM-06"][3] == D(60)
    reduced = apex.db.execute(
        text(
            "select before->>'qty', after->>'qty', reason from audit_event where action = 'request.line_reduce' "
            "order by id desc limit 1"
        )
    ).one()
    assert (D(reduced[0]), D(reduced[1])) == (D(110), D(60))
    assert reduced[2].startswith(f"{mrs['MO-B']} cancelled")

    # and cancelling MO-A too leaves the request for the buyer (it buys for nobody else)
    assert material.cancel(apex.db, apex.uid("store"), mrs["MO-A"], "plant shut") == [no]


def test_a_restock_joins_a_draft_shortfall_and_replenishment_counts_only_the_stock_part(apex: Apex) -> None:
    # given V-belts (min-max 5 to 15) with 12 on the shelf, and an order needing 20 when a restock would arrive
    _set_stock(apex, "SP-01", "12")
    today = business_today()
    restock_by = reference.calendar_for(apex.db).add_working_days(today, 5)
    mrs = _orders(apex, {"MO-V": (restock_by + timedelta(days=2), [("SP-01", "20")], "PLT-CKN")})

    # when the store checks without sending for approval: 12 set aside, 8 on a draft line
    _check(apex, submit=False)
    ((no, qty, _),) = _request_lines(apex, "SP-01")
    assert qty == D(8)

    # then the draft line buys only for the order, so it does not stop the restock, which joins it for stock
    result = replenishment.run(apex.db, as_of=today)
    assert result.combined == {"SP-01@PLT-CKN": no}
    assert "SP-01" not in {ln.item_code for r in result.requests for ln in r.lines}
    line = apex.db.execute(
        text("select qty, stock_qty from request_line where request_no = :n and item_code = 'SP-01'"), {"n": no}
    ).one()
    assert (line.qty, line.stock_qty) == (D(23), D(15))  # 8 for the order and 15 for the shelf

    # and the next restock run sees the 15 already asked for stock
    _, skipped = replenishment.plan(apex.db, today)
    assert any(s.item_code == "SP-01" and s.reason.startswith("Already requested") for s in skipped)

    # when the order is called off, then only the stock part is left on the line
    material.cancel(apex.db, apex.uid("store"), mrs["MO-V"], "order moved to next month")
    assert _request_lines(apex, "SP-01") == [(no, D(15), "open")]


# ---- the audit trail and the screen --------------------------------------------------------------


def test_combining_is_audited_and_the_request_screen_shows_each_source(apex: Apex) -> None:
    mrs = _orders(
        apex,
        {
            "MO-A": (FIRST, [("RM-06", "100")], "PLT-CKN"),
            "MO-B": (FIRST + timedelta(days=5), [("RM-06", "50")], "PLT-CKN"),
        },
    )
    _check(apex)
    ((no, _, _),) = _request_lines(apex, "RM-06")

    combined = apex.db.execute(
        text(
            "select actor_type, after from audit_event where action = 'request.combine' and entity_id = :n "
            "order by id desc limit 1"
        ),
        {"n": no},
    ).one()
    assert combined[0] == "user"
    assert (D(combined[1]["qty"]), D(combined[1]["for_material_request"])) == (D(110), D(50))

    shown = client.get(f"/api/requisitions/{no}", headers=apex.headers("buyer", "Buyer")).json()
    (line,) = shown["lines"]
    assert [(s["order_no"], s["qty"]) for s in line["bought_for"]] == [("MO-A", "60"), ("MO-B", "50")]
    assert {s["material_request"] for s in line["bought_for"]} == {mrs["MO-A"], mrs["MO-B"]}

    # and the budget was checked for the plant as well as for the department
    levels = {b["level"]: b for b in shown["approval"]["budget"]}
    assert set(levels) == {"department", "location"}
    assert (levels["location"]["location"], levels["location"]["period"]) == (
        "PLT-CKN",
        "Apr 2026 to Nov 2026 (FY 2026-27)",
    )
