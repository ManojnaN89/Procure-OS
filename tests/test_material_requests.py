"""Material requests (step 2 of the flow): each imported production order gets one; the store check sets free stock aside
earliest need-by first and never more than is free; what is short becomes one purchase request at the estimate that goes
into approval; issue at production start takes the reservation with it; cancelling releases it; replenishment and the
repeat check know about all of it. Worked on the main demo company, Apex Pumps."""

import csv
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import workflow
from p2p.formulas.calendar import business_today
from p2p.formulas.estimate import Estimate, estimated_price
from p2p.formulas.material import earmarked, still_short, to_reserve
from p2p.main import app
from p2p.platform import audit, demo, numbering, routing, tenants, users
from p2p.platform.sod import user
from p2p.platform.tokens import create_access_token
from p2p.receiving import goods
from p2p.receiving.stock import StockError, post_count, post_issue, stock_levels
from p2p.requests import demand, duplicates, material, replenishment, reservation

ROOT = Path(__file__).resolve().parents[1]
APEX = ROOT / "data" / "datasets" / "apex-v1"
D = Decimal
client = TestClient(app)

# the plan's example: 50 pumps of 1 HP by 5 December, five materials per pump
MO1 = [("RM-01", "50"), ("RM-03", "50"), ("RM-06", "100"), ("RM-08", "90"), ("PK-01", "50")]


@dataclass
class Apex:
    slug: str
    db: Session
    folder: Path

    def uid(self, login: str) -> int:
        found = users.user_id_by_email(self.slug, f"{login}@apexpumps.example")
        assert found is not None
        return found

    def headers(self, login: str, role: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(self.uid(login), self.slug, role)}"}


@pytest.fixture(scope="module")
def company(control_db: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Apex]:
    slug = "mr" + uuid.uuid4().hex[:8]
    demo.reset_company(demo.company("apex"), APEX, credentials=None, slug=slug)
    with Session(routing.engine_for(slug)) as session:
        yield Apex(slug, session, tmp_path_factory.mktemp("erp"))
    routing.clear_engine_cache()
    tenants.drop_tenant(slug)


@pytest.fixture
def apex(company: Apex) -> Apex:
    """Apex as the demo reset leaves it, with its own demand taken out so each scenario loads the orders it names."""
    db = company.db
    db.rollback()
    for sql in (
        "delete from demand_change_case",  # what a cancel or an ERP change did (block 1)
        "delete from demand_change",
        "delete from material_request_buy",
        "delete from material_request_line",
        "delete from material_request",
        "delete from material_requirement",
        "delete from production_order",
        "delete from product_demand",
        "delete from demand_import",
        "delete from request",
        "delete from stock_movement where source = 'platform'",
        "delete from number_counter where document_type in ('request', 'material_request')",
    ):
        db.execute(text(sql))
    db.commit()
    return company


def _write(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _orders(apex: Apex, orders: dict[str, tuple[str, list[tuple[str, str]]]]) -> demand.DemandReport:
    """Import production orders {order_no: (need_by, [(item, qty)])} as the ERP would send them."""
    folder = apex.folder / uuid.uuid4().hex[:6]
    _write(
        folder / "production_order.csv",
        ["order_no", "product_code", "qty", "need_by"],
        [[no, "FG-01", "50", need_by] for no, (need_by, _) in orders.items()],
    )
    _write(
        folder / "production_order_line.csv",
        ["order_no", "item_code", "qty"],
        [[no, item, qty] for no, (_, parts) in orders.items() for item, qty in parts],
    )
    report = demand.import_demand(apex.db, folder)
    assert report.rejected == []
    return report


def _on_hand(apex: Apex, item: str) -> Decimal:
    return D(apex.db.execute(text("select on_hand from stock_level where item_code = :i"), {"i": item}).scalar_one())


def _set_stock(apex: Apex, item: str, qty: str) -> None:
    """Make the books say `qty` for an item (a count by the store)."""
    post_count(apex.db, apex.uid("store"), item, "PLT-CKN", D(qty), "test set-up")


def _lines(apex: Apex, mr_no: str) -> dict[str, tuple[Decimal, Decimal, Decimal, Decimal]]:
    """item -> (need, reserved, issued, requested)"""
    rows = apex.db.execute(
        text(
            "select item_code, need_qty, reserved_qty, issued_qty, requested_qty from material_request_cover "
            "where mr_no = :n"
        ),
        {"n": mr_no},
    )
    return {r[0]: (r[1], r[2], r[3], r[4]) for r in rows}


def _check(apex: Apex) -> material.CheckResult:
    return material.store_check(apex.db, user(apex.uid("store")), tenant=apex.slug)


# ---- the formulas at their edges ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("want", "free", "expected"),
    [
        ("50", "60", "50"),  # enough on the shelf
        ("50", "50", "50"),  # exactly enough
        ("50", "40", "40"),  # only what is free
        ("50", "0", "0"),  # nothing free
        ("50", "-5", "0"),  # the books are below what is set aside: nothing more, never a negative reservation
        ("0", "60", "0"),  # nothing wanted
        ("0.0001", "60", "0.0001"),
    ],
)
def test_a_line_takes_only_what_is_free(want: str, free: str, expected: str) -> None:
    assert to_reserve(D(want), D(free)) == D(expected)


@pytest.mark.parametrize(
    ("need", "issued", "reserved", "requested", "short", "coming"),
    [
        ("100", "0", "40", "0", "60", "0"),
        ("100", "0", "40", "60", "0", "60"),  # already asked for
        ("100", "0", "100", "0", "0", "0"),  # all set aside
        ("100", "30", "20", "50", "0", "50"),  # part issued
        ("100", "0", "40", "80", "0", "60"),  # more requested than lacking (a pack size): only the lack is earmarked
        ("100", "100", "0", "60", "0", "0"),  # all issued: nothing on order is held for it
    ],
)
def test_what_is_short_and_what_is_on_its_way(
    need: str, issued: str, reserved: str, requested: str, short: str, coming: str
) -> None:
    args = (D(need), D(issued), D(reserved), D(requested))
    assert still_short(*args) == D(short)
    assert earmarked(*args) == D(coming)


def test_the_estimate_is_the_contract_then_the_last_price_paid_then_the_standard_price() -> None:
    contract, paid = (D(650), "CNT-0001"), (D(640), "PO-1")
    assert estimated_price(contract, paid, D(600)) == Estimate(D(650), "contract", "CNT-0001")
    assert estimated_price(None, (D(38), "PO-7"), D(40)) == Estimate(D(38), "last price paid", "PO-7")
    assert estimated_price(None, None, D(45)) == Estimate(D(45), "standard price")
    assert estimated_price((D(0), "CNT-0002"), None, D(45)) == Estimate(D(45), "standard price")  # zero is no price
    assert estimated_price(None, (D(0), "PO-8"), D(45)) == Estimate(D(45), "standard price")
    assert estimated_price(None, None, D(0)) is None
    assert estimated_price(None, None, None) is None
    # the words kept with the request line
    assert Estimate(D(650), "contract", "CNT-0001").source == "contract CNT-0001"
    assert Estimate(D(38), "last price paid", "PO-7").source == "last price paid on PO-7"
    assert Estimate(D(45), "standard price").source == "standard price"


# ---- the worked example -------------------------------------------------------------------------


def test_mo1_reserves_the_impellers_and_asks_to_buy_four_items_for_72930(apex: Apex) -> None:
    # given MO-1 imported, and the store holding casting 0, impeller 60, bearing 40, copper wire 50 kg, carton 20
    report = _orders(apex, {"MO-1": ("2026-12-05", MO1)})
    assert [_on_hand(apex, i) for i, _ in MO1] == [D(0), D(60), D(40), D(50), D(20)]

    # then the import made its material request, one line per component, nothing set aside yet
    assert len(report.material_requests) == 1
    mr = report.material_requests[0]
    assert mr.startswith("MR-CKN-2627-")
    assert {k: v[1] for k, v in _lines(apex, mr).items()} == {i: D(0) for i, _ in MO1}

    # when the store runs the check
    result = _check(apex)

    # then the impellers are all set aside and four items are short
    lines = _lines(apex, mr)
    assert {k: v[1] for k, v in lines.items()} == {
        "RM-01": D(0), "RM-03": D(50), "RM-06": D(40), "RM-08": D(50), "PK-01": D(20),
    }  # fmt: skip
    (checked,) = result.requests
    assert {ln.item_code: ln.short for ln in checked.lines if ln.short} == {
        "RM-01": D(50), "RM-06": D(60), "RM-08": D(40), "PK-01": D(30),
    }  # fmt: skip

    # and one purchase request asks for them at Rs 72,930, the casting at its contract price
    req = checked.request
    assert req is not None and req.no is not None
    assert req.total_value == D("72930.00")
    priced = {ln.item_code: (ln.qty, ln.est_price) for ln in req.lines}
    assert priced == {
        "RM-01": (D(50), D(650)), "RM-06": (D(60), D(38)), "RM-08": (D(40), D(920)), "PK-01": (D(30), D(45)),
    }  # fmt: skip
    head = apex.db.execute(
        text("select source_ref, channel, requester, department_code, status, total_value from request where no = :n"),
        {"n": req.no},
    ).one()
    assert (head.source_ref, head.channel, head.requester, head.department_code) == (mr, "planning", None, "PRD")
    assert head.total_value == D("72930.00")

    # and it went into approval: the plant head approves requests up to Rs 1,00,000
    assert req.outcome == "submitted", req.submit_error
    approver = apex.db.execute(
        text("select approver from approval_step where request_no = :n and status = 'pending'"), {"n": req.no}
    ).scalar_one()
    assert approver == "U07"
    assert {k: v[3] for k, v in _lines(apex, mr).items() if v[3]} == {
        "RM-01": D(50), "RM-06": D(60), "RM-08": D(40), "PK-01": D(30),
    }  # fmt: skip


def test_every_step_writes_its_audit_row(apex: Apex) -> None:
    # given MO-1, checked, part issued and then cancelled
    mr = _orders(apex, {"MO-1": ("2026-12-05", MO1)}).material_requests[0]
    no = _check(apex).purchase_requests[0].no
    material.issue(apex.db, apex.uid("store"), mr, {2: D(10)})
    material.cancel(apex.db, apex.uid("store"), mr, "order called off")

    # then each of them is in the log
    actions = {
        r[0]
        for r in apex.db.execute(
            text(
                "select action from audit_event where entity_id in (:m, :r) or (entity = 'stock' and action = 'stock.issue')"
            ),
            {"m": mr, "r": no},
        )
    }
    assert {
        "material_request.create",
        "material_request.check",
        "material_request.issue",
        "material_request.cancel",
        "request.create",
        "request.submit",
        "stock.issue",
    } <= actions


# ---- boundaries of the store check ---------------------------------------------------------------


def test_stock_exactly_equal_to_the_need_is_all_set_aside_and_nothing_is_bought(apex: Apex) -> None:
    # given an order for exactly the 60 impellers on the shelf
    mr = _orders(apex, {"MO-7": ("2026-12-05", [("RM-03", "60")])}).material_requests[0]

    # when checked
    result = _check(apex)

    # then all 60 are set aside, none are free, and nothing is bought
    assert _lines(apex, mr)["RM-03"][1] == D(60)
    assert result.purchase_requests == []
    (level,) = stock_levels(apex.db, item="RM-03")
    assert (level.on_hand, level.reserved, level.free) == (D(60), D(60), D(0))


def test_an_item_with_no_stock_is_all_bought(apex: Apex) -> None:
    mr = _orders(apex, {"MO-8": ("2026-12-05", [("RM-01", "20")])}).material_requests[0]

    req = _check(apex).purchase_requests[0]

    assert _lines(apex, mr)["RM-01"][1] == D(0)
    assert [(ln.item_code, ln.qty) for ln in req.lines] == [("RM-01", D(20))]


def test_the_earliest_need_by_gets_the_stock_first(apex: Apex) -> None:
    # given two orders for impellers, the later-needed one loaded first, and 60 on the shelf
    later = _orders(apex, {"MO-20": ("2026-12-20", [("RM-03", "50")])}).material_requests[0]
    sooner = _orders(apex, {"MO-21": ("2026-12-01", [("RM-03", "50")])}).material_requests[0]

    # when the store checks
    _check(apex)

    # then the order needed sooner gets its 50, the later one the 10 left, and buys the rest
    assert _lines(apex, sooner)["RM-03"][1:] == (D(50), D(0), D(0))
    assert _lines(apex, later)["RM-03"][1:] == (D(10), D(0), D(40))
    (level,) = stock_levels(apex.db, item="RM-03")
    assert level.free == 0


def test_a_second_check_sets_nothing_aside_twice_and_buys_nothing_twice(apex: Apex) -> None:
    mr = _orders(apex, {"MO-1": ("2026-12-05", MO1)}).material_requests[0]
    _check(apex)
    before = _lines(apex, mr)

    again = _check(apex)

    assert _lines(apex, mr) == before
    assert again.purchase_requests == []
    assert apex.db.execute(text("select count(*) from request")).scalar_one() == 1


def test_a_rejected_purchase_request_lets_the_next_check_ask_again(apex: Apex) -> None:
    # given MO-1's shortfall request, rejected by the plant head
    _orders(apex, {"MO-1": ("2026-12-05", MO1)})
    first = _check(apex).purchase_requests[0]
    assert first.no is not None
    workflow.reject(apex.db, first.no, apex.uid("planthead"), "buy in one lot next week", tenant=apex.slug)

    # when the store checks again, then what is short is asked for again
    second = _check(apex).purchase_requests
    assert len(second) == 1 and second[0].total_value == D("72930.00")


# ---- issue at production start -------------------------------------------------------------------


def test_a_part_issue_leaves_the_rest_set_aside_and_free_stock_is_not_touched(apex: Apex) -> None:
    # given MO-7 for 50 impellers, checked: 50 of the 60 set aside
    mr = _orders(apex, {"MO-7": ("2026-12-05", [("RM-03", "50")])}).material_requests[0]
    _check(apex)
    store = apex.uid("store")

    # when 30 are issued at production start
    assert material.issue(apex.db, store, mr, {1: D(30)}) == {"RM-03": D(30)}

    # then 20 stay set aside, 30 left the shelf, the request is still open
    assert _lines(apex, mr)["RM-03"][:3] == (D(50), D(20), D(30))
    assert _on_hand(apex, "RM-03") == D(30)
    assert apex.db.execute(text("select status from material_request where no = :n"), {"n": mr}).scalar_one() == "open"

    # and nobody can take the set-aside stock for something else: only the 10 free can be issued
    with pytest.raises(StockError, match="only 10 is free"):
        post_issue(apex.db, store, "RM-03", "PLT-CKN", D(11))
    with pytest.raises(material.MaterialError, match="only 20 is set aside"):
        material.issue(apex.db, store, mr, {1: D(21)})

    # when the rest is issued, then the line and the request are done
    assert material.issue(apex.db, store, mr) == {"RM-03": D(20)}
    assert _lines(apex, mr)["RM-03"][:3] == (D(50), D(0), D(50))
    assert (
        apex.db.execute(text("select status from material_request where no = :n"), {"n": mr}).scalar_one() == "issued"
    )
    assert _on_hand(apex, "RM-03") == D(10)
    with pytest.raises(material.MaterialError, match="is issued"):
        material.issue(apex.db, store, mr)


def test_nothing_can_be_issued_before_the_store_check(apex: Apex) -> None:
    mr = _orders(apex, {"MO-7": ("2026-12-05", [("RM-03", "50")])}).material_requests[0]

    with pytest.raises(material.MaterialError, match="run the store check first"):
        material.issue(apex.db, apex.uid("store"), mr)
    assert _on_hand(apex, "RM-03") == D(60)


# ---- cancelling ----------------------------------------------------------------------------------


def test_a_cancelled_order_releases_its_stock_for_the_next_one(apex: Apex) -> None:
    # given MO-7 holding 50 impellers and MO-9 (needed later) short of 40
    first = _orders(apex, {"MO-7": ("2026-12-05", [("RM-03", "50")])}).material_requests[0]
    _check(apex)
    second = _orders(apex, {"MO-9": ("2026-12-09", [("RM-03", "10")])}).material_requests[0]
    _check(apex)
    assert _lines(apex, second)["RM-03"][1] == D(10)

    # when MO-7 is called off
    left = material.cancel(apex.db, apex.uid("store"), first, "customer cancelled SO-1001")

    # then its 50 are free again, the order is no longer demand, and a new order can have them
    assert left["purchase_requests_left"] == [] and left["cases"] == []
    assert _lines(apex, first)["RM-03"][1] == D(0)
    assert stock_levels(apex.db, item="RM-03")[0].free == D(50)
    statuses = apex.db.execute(
        text(
            "select m.status, p.status, (select string_agg(distinct r.status, ',') from material_requirement r "
            "where r.production_order_id = p.id) from material_request m join production_order p on p.id = "
            "m.production_order_id where m.no = :n"
        ),
        {"n": first},
    ).one()
    assert tuple(statuses) == ("cancelled", "cancelled", "cancelled")
    third = _orders(apex, {"MO-10": ("2026-12-10", [("RM-03", "50")])}).material_requests[0]
    _check(apex)
    assert _lines(apex, third)["RM-03"][1] == D(50)
    with pytest.raises(material.MaterialError, match="say why"):
        material.cancel(apex.db, apex.uid("store"), third, " ")


def test_a_cancel_cuts_a_request_not_with_the_buyer_and_opens_a_case_for_one_that_is(apex: Apex) -> None:
    # Q21 (decided 20 Nov): a cancel cuts purchase requests not yet with the buyer; before, they stayed for the buyer
    mr = _orders(apex, {"MO-1": ("2026-12-05", MO1)}).material_requests[0]
    no = _check(apex).purchase_requests[0].no

    done = material.cancel(apex.db, apex.uid("store"), mr, "order called off")
    assert done["requests_cancelled"] == [no] and done["purchase_requests_left"] == [] and done["cases"] == []
    assert apex.db.execute(text("select status from request where no = :n"), {"n": no}).scalar_one() == "cancelled"

    # when the buyer already has the request, it stays and the buyer gets one case for it
    mr = _orders(apex, {"MO-2": ("2026-12-05", MO1)}).material_requests[0]
    no = _check(apex).purchase_requests[0].no
    apex.db.execute(text("update request_line set status = 'sourcing' where request_no = :n"), {"n": no})
    apex.db.commit()
    done = material.cancel(apex.db, apex.uid("store"), mr, "order called off", tenant=apex.slug)
    assert done["purchase_requests_left"] == [no] and done["requests_cut"] == []
    assert [(c["doc_type"], c["doc_no"], c["owner_kind"]) for c in done["cases"]] == [("request", no, "buyer")]


# ---- replenishment and the repeat check ----------------------------------------------------------


def test_replenishment_counts_stock_set_aside_and_restocks_beside_a_shortfall_bought_for_an_order(apex: Apex) -> None:
    # given V-belts planned min/max (reorder at 5, up to 15) with 12 on the shelf: nothing to order (no safety stock
    # here: block 2 keeps it out of reach, test_stock_block2.py)
    _set_stock(apex, "SP-01", "12")
    apex.db.execute(text("update item_location set safety_stock = null where item_code = 'SP-01'"))
    apex.db.commit()
    planned, _ = replenishment.plan(apex.db, business_today())
    assert "SP-01" not in {ln.item_code for r in planned for ln in r.lines}

    # when an order sets 10 of them aside
    _orders(apex, {"MO-30": ("2026-12-05", [("SP-01", "10")])})
    _check(apex)

    # then only 2 are free, below the reorder point: replenishment buys up to the maximum from the free stock
    planned, _ = replenishment.plan(apex.db, business_today())
    (line,) = [ln for r in planned for ln in r.lines if ln.item_code == "SP-01"]
    assert line.qty == D(13)
    assert "2 NOS (10 of 12 set aside)" in line.reason

    # when a second order needs 20 more: 2 set aside, 18 bought for it
    mr = _orders(apex, {"MO-31": ("2026-12-06", [("SP-01", "20")])}).material_requests[0]
    _check(apex)
    assert _lines(apex, mr)["SP-01"][1:] == (D(2), D(0), D(18))

    # then the 18 on order are held for the order, not counted as stock on the way
    (level,) = stock_levels(apex.db, item="SP-01")
    assert (level.on_order, level.for_requests, level.free_on_order, level.free) == (D(18), D(18), D(0), D(0))

    # and the order's request line does not stop the restock (it buys nothing for stock): the shelf is refilled to 15,
    # and the 18 are not bought again
    result = replenishment.run(apex.db, as_of=business_today())
    (line,) = [ln for r in result.requests for ln in r.lines if ln.item_code == "SP-01"]
    assert (line.qty, line.stock_qty) == (D(15), D(15))
    planned, skipped = replenishment.plan(apex.db, business_today())
    assert "SP-01" not in {ln.item_code for r in planned for ln in r.lines}
    assert any(s.item_code == "SP-01" and s.reason.startswith("Already requested") for s in skipped)
    assert _lines(apex, mr)["SP-01"][3] == D(18)


def test_requests_for_different_orders_are_not_repeats_but_a_hand_made_one_still_is(apex: Apex) -> None:
    # given two orders for the same castings, checked one after the other
    _orders(apex, {"MO-40": ("2026-12-05", [("RM-01", "10")])})
    first = _check(apex).purchase_requests[0].no
    _orders(apex, {"MO-41": ("2026-12-06", [("RM-01", "10")])})
    second = _check(apex).purchase_requests[0].no
    assert first and second

    # then the second is not a repeat of the first
    assert duplicates.find_duplicates(apex.db, second) == []

    # but a request typed in by hand for the same castings is flagged against them
    apex.db.execute(
        text(
            "insert into request (no, requester, ship_to_location, type, channel, status, need_by) "
            "values ('REQ-HAND-1', 'U01', 'PLT-CKN', 'goods', 'form', 'draft', '2026-12-05')"
        )
    )
    apex.db.execute(
        text(
            "insert into request_line (request_no, line_no, item_code, qty, uom, need_by, delivery_location) "
            "values ('REQ-HAND-1', 1, 'RM-01', 10, 'NOS', '2026-12-05', 'PLT-CKN')"
        )
    )
    apex.db.commit()
    assert {d.other_request for d in duplicates.find_duplicates(apex.db, "REQ-HAND-1")} == {first, second}


# ---- separation of duties, the screen's data, permissions ----------------------------------------


def test_the_storekeeper_cannot_approve_the_shortfall_and_the_plant_head_can(apex: Apex) -> None:
    _orders(apex, {"MO-1": ("2026-12-05", MO1)})
    no = _check(apex).purchase_requests[0].no
    assert no is not None

    with pytest.raises(workflow.ApprovalError):
        workflow.approve(apex.db, apex.slug, no, apex.uid("store"))
    apex.db.rollback()
    assert workflow.approve(apex.db, apex.slug, no, apex.uid("planthead")) == "approved"


def test_the_store_works_its_material_requests_on_the_screen(apex: Apex) -> None:
    store = apex.headers("store", "Store")
    _orders(apex, {"MO-1": ("2026-12-05", MO1)})

    listed = client.get("/api/material-requests", headers=store).json()
    assert listed["can_manage"] is True
    (mr,) = listed["requests"]
    assert (mr["order_no"], mr["status"], len(mr["lines"])) == ("MO-1", "open", 5)

    checked = client.post("/api/material-requests/check", json={}, headers=store).json()["checked"]
    assert checked[0]["purchase_request"]["total_value"] == "72930.00"
    assert checked[0]["short"] == {"RM-01": "50", "RM-06": "60", "RM-08": "40", "PK-01": "30"}

    lines = {
        ln["item_code"]: ln for ln in client.get("/api/material-requests", headers=store).json()["requests"][0]["lines"]
    }
    # short is what nothing covers (rule Q17, block 2): the 60 lacking are all on the purchase request
    assert (
        lines["RM-06"]["reserved_qty"],
        lines["RM-06"]["lacking_qty"],
        lines["RM-06"]["short"],
        lines["RM-06"]["requested_qty"],
    ) == ("40", "60", "0", "60")
    assert lines["RM-06"]["requests"] == [checked[0]["purchase_request"]["no"]]

    issued = client.post(f"/api/material-requests/{mr['no']}/issue", json={"lines": {"2": "50"}}, headers=store)
    assert issued.status_code == 200 and issued.json() == {"issued": {"RM-03": "50"}}
    too_much = client.post(f"/api/material-requests/{mr['no']}/issue", json={"lines": {"3": "41"}}, headers=store)
    assert too_much.status_code == 409
    assert client.post("/api/material-requests/NOPE/issue", json={}, headers=store).status_code == 404


def test_only_the_store_runs_the_check_and_another_company_sees_nothing(apex: Apex, control_db: str) -> None:
    _orders(apex, {"MO-1": ("2026-12-05", MO1)})
    requester = apex.headers("production", "Requester")
    buyer = apex.headers("buyer", "Buyer")

    assert client.post("/api/material-requests/check", json={}, headers=requester).status_code == 403
    assert client.post("/api/material-requests/check", json={}, headers=buyer).status_code == 403
    assert client.get("/api/material-requests", headers=requester).status_code == 403

    other = "mo" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other, load_reference=False).ok
    try:
        uid = users.create_user(other, "store@other.example", "a-long-password", "Store", "Store")
        seen = client.get(
            "/api/material-requests", headers={"Authorization": f"Bearer {create_access_token(uid, other, 'Store')}"}
        )
        assert seen.status_code == 200 and seen.json()["requests"] == []
    finally:
        routing.clear_engine_cache()
        tenants.drop_tenant(other)


# ---- making them -----------------------------------------------------------------------------------


def test_components_loaded_later_join_the_orders_open_request_and_nothing_is_made_twice(apex: Apex) -> None:
    mr = _orders(apex, {"MO-50": ("2026-12-05", [("RM-03", "5")])}).material_requests[0]
    folder = apex.folder / "later"
    _write(folder / "production_order_line.csv", ["order_no", "item_code", "qty"], [["MO-50", "RM-05", "5"]])

    report = demand.import_demand(apex.db, folder)

    assert report.material_requests == [mr]
    assert sorted(_lines(apex, mr)) == ["RM-03", "RM-05"]
    assert material.make_requests(apex.db) == []


def test_material_requests_are_numbered_by_the_default_series_until_the_company_has_its_own(apex: Apex) -> None:
    assert numbering.prefix(apex.db, "material_request") == "MR"
    assert numbering.prefix(apex.db, "no_such_document") is None


# ---- stock that arrives is set aside at once; freed stock never leads to buying twice --------------


def _cover(apex: Apex, mr: str, item: str) -> tuple[Decimal, Decimal]:
    """(set aside, still coming on live requests) for a material request line, never more than it needs."""
    apex.db.rollback()
    need, reserved, _, requested = _lines(apex, mr)[item]
    assert reserved + requested <= need
    return reserved, requested


def _held_and_short(apex: Apex, later_need: str) -> tuple[str, str, str]:
    """MO-7 holds 50 of the 60 impellers; MO-9 (needed later) holds the 10 left and its shortfall is on a purchase
    request (submitted for approval). Returns MO-7's and MO-9's material requests and that purchase request."""
    first = _orders(apex, {"MO-7": ("2026-12-05", [("RM-03", "50")])}).material_requests[0]
    _check(apex)
    second = _orders(apex, {"MO-9": ("2026-12-09", [("RM-03", later_need)])}).material_requests[0]
    (req,) = _check(apex).purchase_requests
    assert req.no is not None and req.outcome == "submitted"
    assert _cover(apex, second, "RM-03") == (D(10), D(later_need) - 10)
    return first, second, req.no


@pytest.mark.parametrize(
    ("later_need", "reserved", "coming", "request_qty", "request_status"),
    [
        ("60", "60", "0", None, "cancelled"),  # the 50 freed are exactly what was requested: the request goes
        ("61", "60", "1", "1", "submitted"),  # a unit over: the request keeps the one unit stock cannot give
        ("90", "60", "30", "30", "submitted"),  # partly: the request keeps the rest
    ],
)
def test_freed_stock_takes_over_a_purchase_request_never_both(
    apex: Apex, later_need: str, reserved: str, coming: str, request_qty: str | None, request_status: str
) -> None:
    # given MO-9's shortfall on a purchase request not yet with the buyer
    first, second, no = _held_and_short(apex, later_need)
    start = apex.db.execute(text("select max(id) from audit_event")).scalar_one()

    # when MO-7 is called off, its 50 impellers come free
    material.cancel(apex.db, apex.uid("store"), first, "order called off", tenant=apex.slug)

    # then they are set aside for MO-9 at once and its request is cut by as much: never stocked and bought twice
    assert _cover(apex, second, "RM-03") == (D(reserved), D(coming))
    status, qty = apex.db.execute(
        text(
            "select r.status, (select qty from request_line where request_no = r.no and status <> 'cancelled') "
            "from request r where r.no = :n"
        ),
        {"n": no},
    ).one()
    assert (status, qty) == (request_status, D(request_qty) if request_qty else None)
    cut = apex.db.execute(
        text("select count(*) from audit_event where action = 'request.line_reduce' and entity_id = :n and id > :s"),
        {"n": no, "s": start},
    ).scalar_one()
    assert cut == 1
    by = apex.db.execute(
        text(
            "select actor_type from audit_event where action = 'material_request.reserve' and entity_id = :m "
            "and id > :s"
        ),
        {"m": second, "s": start},
    ).scalars()
    assert list(by) == [audit.SYSTEM.kind]

    # and a store check afterwards changes nothing and buys nothing
    assert _check(apex).purchase_requests == []
    assert _cover(apex, second, "RM-03") == (D(reserved), D(coming))


def test_a_request_already_with_the_buyer_keeps_its_cover_and_the_stock_stays_free(apex: Apex) -> None:
    # given MO-9's shortfall already out for quotation
    first, second, no = _held_and_short(apex, "60")
    apex.db.execute(text("update request_line set status = 'sourcing' where request_no = :n"), {"n": no})
    apex.db.commit()

    # when MO-7 is called off
    material.cancel(apex.db, apex.uid("store"), first, "order called off", tenant=apex.slug)

    # then MO-9 keeps relying on what is being bought, and the 50 stay free
    assert _cover(apex, second, "RM-03") == (D(10), D(50))
    assert stock_levels(apex.db, item="RM-03")[0].free == D(50)


def test_freed_stock_goes_first_to_a_shortage_nobody_is_buying_for(apex: Apex) -> None:
    # given MO-9 (needed 9 Dec) covered by a request, and MO-12 (needed later, loaded after the check) covered by nothing
    first, second, _ = _held_and_short(apex, "60")
    third = _orders(apex, {"MO-12": ("2026-12-12", [("RM-03", "30")])}).material_requests[0]

    # when MO-7's 50 come free
    material.cancel(apex.db, apex.uid("store"), first, "order called off", tenant=apex.slug)

    # then MO-12 gets its 30 though it is needed later, and MO-9 the 20 left, its request cut to the 30 still lacking
    assert _cover(apex, third, "RM-03") == (D(30), D(0))
    assert _cover(apex, second, "RM-03") == (D(30), D(30))


def test_a_count_that_finds_more_is_set_aside_at_once_and_the_store_is_told(apex: Apex) -> None:
    # given MO-1 checked: 40 bearings set aside, 60 on a purchase request
    mr = _orders(apex, {"MO-1": ("2026-12-05", MO1)}).material_requests[0]
    _check(apex)

    # when the store counts 60 bearings where the books say 40
    resp = client.post(
        "/api/inventory/count",
        json={"item_code": "RM-06", "location": "PLT-CKN", "counted_qty": "60", "reason": "found a box"},
        headers=apex.headers("store", "Store"),
    )

    # then the 20 are set aside for MO-1 by the system, its request is cut by 20, and the store hears of it
    assert resp.status_code == 200, resp.text
    assert _cover(apex, mr, "RM-06") == (D(60), D(40))
    told = apex.db.execute(
        text("select body from notification where user_id = :u and kind = 'material' order by id desc limit 1"),
        {"u": apex.uid("store")},
    ).scalar_one()
    assert "MO-1" in told and "20 RM-06" in told

    # and doing it again sets nothing more aside
    assert reservation.stock_rose(apex.db, apex.slug, [("RM-06", "PLT-CKN")], why="again") == {}


def test_goods_accepted_on_a_receipt_are_set_aside_for_the_order_waiting(apex: Apex) -> None:
    # given MO-8 short of 20 castings (none on the shelf), its request not yet with the buyer
    mr = _orders(apex, {"MO-8": ("2026-12-05", [("RM-01", "20")])}).material_requests[0]
    _check(apex)
    assert _cover(apex, mr, "RM-01") == (D(0), D(20))

    # when 12 castings are accepted on a receipt
    apex.db.execute(
        text(
            "insert into stock_movement (id, item_code, location_code, date, type, qty, ref_doc, source) "
            "values ('SM-GR-T1-1', 'RM-01', 'PLT-CKN', :d, 'receipt', 12, 'GR-T1', 'platform')"
        ),
        {"d": business_today()},
    )
    apex.db.commit()
    goods.reserve_arrived(apex.db, apex.slug, "GR-T1")

    # then they are set aside for MO-8 at once, and its request now buys only the 8 left
    assert _cover(apex, mr, "RM-01") == (D(12), D(8))
