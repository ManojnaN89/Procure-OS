"""Scenario tests: P2P behaviour of SAP S/4HANA and Dynamics 365 that this platform copies (plan sheet 'SAP-D365 Benchmark').

Each test is named after a scenario and written as given / when / then. A scenario whose feature is not built yet is an
expected failure that fails only because the feature is missing (ImportError or AttributeError). It is strict: the day
the feature exists the test starts to pass, pytest reports it as a failure, and the marker must be removed. Until then
the test is the written specification of the feature. Scenarios 3, 5, 6 and 13 already run for real.
"""

import uuid
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import IST
from p2p.formulas.receipt import ReceiptStatus, receipt_status
from p2p.platform import permissions, routing, sod, tasks, tenants, users, worker
from p2p.platform.sod import Actor
from p2p.rules import engine as rules

pytestmark = pytest.mark.scenario


def not_built(step: str, feature: str) -> pytest.MarkDecorator:
    return pytest.mark.xfail(raises=(ImportError, AttributeError), strict=True, reason=f"{feature} is built in {step}")


@pytest.fixture
def db(control_db: str) -> Iterator[Session]:
    slug = "t" + uuid.uuid4().hex[:8]
    tenants.create_tenant(slug, load_reference=False)
    with Session(routing.engine_for(slug)) as session:
        session.info["slug"] = slug
        yield session
    routing.clear_engine_cache()


# 1. Planning ---------------------------------------------------------------------------------


@not_built("P3", "the planning engine (FR02 to FR09)")
def test_scenario_01_reorder_point_creates_a_requisition_when_stock_falls() -> None:
    from p2p.planning.reorder import reorder_quantity  # given an item with reorder point 30 and max 100

    # when stock plus open orders is 20, then a request for 80 is suggested (replenish to max, FR08)
    assert reorder_quantity(position=Decimal(20), minimum=Decimal(30), maximum=Decimal(100)) == Decimal(80)
    # and nothing is suggested while the position is above the reorder point
    assert reorder_quantity(position=Decimal(31), minimum=Decimal(30), maximum=Decimal(100)) == Decimal(0)


# 2. Requisition ------------------------------------------------------------------------------


@not_built("P1", "netting at request entry (FR01)")
def test_scenario_02_request_is_reduced_by_stock_and_open_orders() -> None:
    from p2p.requests.netting import suggested_quantity

    # given 20 ordered, 10 already received and 20 requested for the same item and location,
    # when the request is entered, then 10 is suggested (the other 10 is still on its way)
    assert suggested_quantity(
        requested=Decimal(20), stock=Decimal(10), open_po_balance=Decimal(10), open_requests=Decimal(0)
    ) == Decimal(10)


# 3. Approval ---------------------------------------------------------------------------------


def test_scenario_03_approval_is_routed_by_value_band_from_a_decision_table(db: Session) -> None:
    # given a published approval table: up to 50,000 manager, up to 5 lakh department head, above that the CFO
    from test_rules_engine import approval_table

    actor = Actor("user", "1")
    rules.save_draft(db, "approval", approval_table(), actor)
    rules.publish(db, "approval", actor)

    # when requests of 40,000, 60,000 and 6 lakh are evaluated
    routes = [
        rules.evaluate(db, "approval", {"value": v}, actor, case_ref="REQ-1").result["approver"]
        for v in (40000, 60000, 600000)
    ]

    # then each goes to the right approver, and the rule version that decided is on record
    assert routes == ["manager", "dept_head", "cfo"]
    assert db.execute(text("select count(*) from rule_evaluation where version = 1")).scalar_one() == 3


@not_built("P1", "auto-approval (FR29)")
def test_scenario_04_auto_approval_needs_every_condition_to_hold() -> None:
    from p2p.approvals.auto import can_auto_approve

    # given a catalogue item of 8,000 within the 10,000 cap with budget available and no flags,
    base = dict(
        value=Decimal(8000),
        cap=Decimal(10000),
        budget_available=True,
        catalogue_item=True,
        policy_flag=False,
        split_flag=False,
        watch_list=False,
    )
    assert can_auto_approve(**base)
    # when any one condition fails, then a person must approve
    for failing in (
        {"value": Decimal(10001)},
        {"budget_available": False},
        {"catalogue_item": False},
        {"policy_flag": True},
        {"split_flag": True},
        {"watch_list": True},
    ):
        assert not can_auto_approve(**{**base, **failing})


def test_scenario_05_a_user_cannot_approve_what_they_requested(db: Session) -> None:
    # given a requester who also has approval rights and a limit
    slug = db.info["slug"]
    uid = users.create_user(slug, f"a{uuid.uuid4().hex[:6]}@example.com", "correct horse battery", "Both", "Approver")
    users.set_limit(slug, uid, "request", Decimal("1000000"))

    # when they try to approve their own request, then it is refused
    with pytest.raises(permissions.PermissionDenied, match="requester"):
        permissions.authorize_approval(
            db,
            uid,
            action="request.approve",
            document="request",
            value=Decimal(5000),
            duty="approver",
            case_actors={"requester": sod.user(uid)},
        )


def test_scenario_06_an_approval_not_done_in_time_escalates_to_the_next_person(db: Session) -> None:
    # given a task for the first approver with an 8 working-hour SLA, raised Friday 5 pm, escalating to a second person
    slug = db.info["slug"]
    first, second = (
        users.create_user(slug, f"s{i}{uuid.uuid4().hex[:5]}@example.com", "correct horse battery", f"A{i}", "Approver")
        for i in (1, 2)
    )
    tid = tasks.create_task(
        db,
        slug,
        kind="approve",
        title="Approve REQ-1",
        assignee=first,
        sla_hours=8,
        escalation_chain=[second],
        now=datetime(2026, 11, 6, 17, 0, tzinfo=IST),
    )

    # when Monday 4 pm has passed without a decision
    worker.run_once(now=datetime(2026, 11, 9, 16, 1, tzinfo=IST))

    # then the task is with the second person
    db.expire_all()
    assert db.execute(text("select assignee from task where id = :i"), {"i": tid}).scalar_one() == second


# 4. Find a source ----------------------------------------------------------------------------


@not_built("P1", "source determination (FR11)")
def test_scenario_07_a_valid_contract_beats_a_price_record_which_beats_an_rfq() -> None:
    from p2p.sourcing.determination import determine_source

    # given an item with a valid contract, an approved price record and neither
    # when a source is determined, then the contract is used first
    assert determine_source(contract="CNT-1", price_record="PR-1").kind == "contract"
    assert determine_source(contract=None, price_record="PR-1").kind == "price_record"
    # and with neither, an RFQ is needed
    assert determine_source(contract=None, price_record=None).kind == "rfq"
    # and a blocked supplier or an expired licence is skipped
    assert (
        determine_source(contract="CNT-1", price_record="PR-1", contract_supplier_blocked=True).kind == "price_record"
    )


@not_built("P1", "price determination (FR10)")
def test_scenario_08_the_price_follows_the_quantity_break_and_ignores_expired_prices() -> None:
    from p2p.sourcing.pricing import net_price

    tiers = [(Decimal(1), Decimal(100)), (Decimal(100), Decimal(90)), (Decimal(500), Decimal(80))]
    # given quantity breaks at 1, 100 and 500, when 100 are ordered, then the second tier applies at its boundary
    assert net_price(tiers, quantity=Decimal(100)) == Decimal(90)
    assert net_price(tiers, quantity=Decimal(99)) == Decimal(100)
    assert net_price(tiers, quantity=Decimal(500)) == Decimal(80)


# 5. RFQ and comparison -----------------------------------------------------------------------


@not_built("P1", "landed cost and quote comparison (FR13)")
def test_scenario_09_quotes_are_compared_on_landed_cost_not_unit_price() -> None:
    from p2p.sourcing.landed_cost import landed_cost_per_unit

    # given supplier A at 100 a unit with 800 freight for 40 units, and supplier B at 105 with freight included
    a = landed_cost_per_unit(price=Decimal(100), qty=Decimal(40), freight=Decimal(800))
    b = landed_cost_per_unit(price=Decimal(105), qty=Decimal(40), freight=Decimal(0))
    # when compared, then B is cheaper even though its unit price is higher
    assert a == Decimal(120) and b == Decimal(105) and b < a


@not_built("P2", "minimum quotes (FR31)")
def test_scenario_10_an_award_above_the_threshold_needs_three_quotes_or_a_reason() -> None:
    from p2p.sourcing.policy import award_allowed

    # given a threshold of 2 lakh and a minimum of 3 quotes
    # when two quotes exist for an estimate of 3 lakh, then award is blocked unless a justification is recorded
    assert not award_allowed(
        estimate=Decimal(300000), quotes=2, threshold=Decimal(200000), minimum=3, justification=None
    )
    assert award_allowed(
        estimate=Decimal(300000), quotes=2, threshold=Decimal(200000), minimum=3, justification="sole source"
    )
    assert award_allowed(estimate=Decimal(150000), quotes=1, threshold=Decimal(200000), minimum=3, justification=None)


# 6. Create PO --------------------------------------------------------------------------------


@not_built("P1", "PO consolidation (FR43)")
def test_scenario_11_approved_lines_are_grouped_into_one_po_per_vendor_and_ship_to() -> None:
    from p2p.orders.consolidation import consolidate

    lines = [
        {
            "vendor": "V1",
            "ship_to": "PLT-VAD",
            "currency": "INR",
            "terms": "NET30",
            "need_by": "2026-11-20",
            "item": "A",
        },
        {
            "vendor": "V1",
            "ship_to": "PLT-VAD",
            "currency": "INR",
            "terms": "NET30",
            "need_by": "2026-11-22",
            "item": "B",
        },
        {
            "vendor": "V1",
            "ship_to": "WH-BLR",
            "currency": "INR",
            "terms": "NET30",
            "need_by": "2026-11-20",
            "item": "C",
        },
    ]
    # when consolidated, then lines for the same vendor and ship-to share a PO, and a different ship-to gets its own
    groups = consolidate(lines, window_days=7)
    assert sorted(len(g) for g in groups) == [1, 2]


# 7. Confirmation and changes -----------------------------------------------------------------


@not_built("P1", "supplier change tolerance (FR19)")
def test_scenario_12_a_supplier_change_inside_the_tolerance_is_accepted_automatically() -> None:
    from p2p.orders.changes import within_tolerance

    tolerance = dict(price_pct=Decimal(2), qty_pct=Decimal(5), slip_days=3)
    # given tolerances of 2% price, 5% quantity and 3 days, when a supplier asks for exactly those limits, then it is accepted
    assert within_tolerance(
        price_change_pct=Decimal(2), qty_change_pct=Decimal(5), slip_days=3, new_date_before_need_by=True, **tolerance
    )
    # and one step beyond any limit goes to the buyer
    assert not within_tolerance(
        price_change_pct=Decimal("2.01"),
        qty_change_pct=Decimal(0),
        slip_days=0,
        new_date_before_need_by=True,
        **tolerance,
    )
    assert not within_tolerance(
        price_change_pct=Decimal(0), qty_change_pct=Decimal(0), slip_days=3, new_date_before_need_by=False, **tolerance
    )


# 8. Receipt ----------------------------------------------------------------------------------


def test_scenario_13_receiving_more_than_ordered_beyond_the_tolerance_is_blocked() -> None:
    # given an order of 100 with a 5% over-delivery tolerance
    # when 105 have been received in total, then it is accepted; one more unit beyond is blocked
    assert receipt_status(100, 105, over_pct=5) is ReceiptStatus.COMPLETE
    assert receipt_status(100, 106, over_pct=5) is ReceiptStatus.OVER_BLOCKED


# 9. Invoice and match ------------------------------------------------------------------------


@not_built("P1", "three-way match (FR21)")
def test_scenario_14_an_invoice_price_outside_the_tolerance_blocks_payment() -> None:
    from p2p.invoices.matching import three_way_match

    # given a PO price of 100 with a 2% price tolerance and 40 received
    # when the invoice says 103 for 40, then it is blocked; at 102 it is within tolerance; at 100 it matches
    line = dict(
        po_price=Decimal(100), received_qty=Decimal(40), invoiced_before=Decimal(0), price_tolerance_pct=Decimal(2)
    )
    assert three_way_match(invoice_price=Decimal(103), invoice_qty=Decimal(40), **line).result == "blocked"
    assert three_way_match(invoice_price=Decimal(102), invoice_qty=Decimal(40), **line).result == "within_tolerance"
    assert three_way_match(invoice_price=Decimal(100), invoice_qty=Decimal(40), **line).result == "matched"


# 10. Payment ---------------------------------------------------------------------------------


@not_built("P1", "due date with the MSME cap (FR23)")
def test_scenario_15_a_micro_or_small_supplier_is_paid_within_45_days_of_acceptance() -> None:
    from datetime import date

    from p2p.payments.due_date import due_date

    # given net 90 terms with a written agreement of 60 days for a small supplier, accepted on 1 Oct 2026
    # when the due date is worked out, then the 45-day statutory cap wins over both
    assert due_date(accepted_on=date(2026, 10, 1), term_days=90, msme=True, agreed_days=60) == date(2026, 11, 15)
    # and without a written agreement the due date is 15 days after acceptance
    assert due_date(accepted_on=date(2026, 10, 1), term_days=90, msme=True, agreed_days=None) == date(2026, 10, 16)
    # while a large supplier keeps its terms
    assert due_date(accepted_on=date(2026, 10, 1), term_days=90, msme=False, agreed_days=None) == date(2026, 12, 30)
