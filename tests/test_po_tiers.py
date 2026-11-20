"""Purchase order tiers, the delivery location on the order, and touchless orders (plan step 7, P1-35, D35, D36).

The tiers are the 'po' rows of the approval rule table: a band with no approver up to Rs 1,00,000, the purchase manager
(here the procurement head) up to Rs 5,00,000, the director above. In the band with no approver only a touchless order is
approved, by the rule: made of contract and safe-reorder lines, it is issued and sent with no buyer when every touchless
test passes. Any other order there goes up to the purchase manager: the buyer who made an order never approves it (v1.1).
"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_buying_route import _apex, _ask
from test_orders import _headers, _logins, shop  # noqa: F401 - fixtures
from test_sourcing import TODAY, Outbox, _price, _source, db, outbox  # noqa: F401 - fixtures

from p2p.agents import autonomy
from p2p.formulas import touchless as tl
from p2p.main import app
from p2p.orders import service as orders
from p2p.orders import touchless, workflow
from p2p.platform import audit, demo, sod

D = Decimal
client = TestClient(app)
TIERS = [
    ("PO-01", "0.00", "100000.00", "auto"),
    ("PO-02", "100000.01", "500000.00", "procurement_head"),
    ("PO-03", "500000.01", "999999999.00", "director"),
]


@pytest.fixture
def tiers(shop: Session) -> Session:
    """The order world with Apex's tiers, the Apex items, and an item free of GST so a total is exactly its price."""
    shop.execute(text("delete from approval_rule where document = 'po'"))
    for code, low, high, chain in TIERS:
        shop.execute(
            text(
                "insert into approval_rule (rule_code, document, min_value, max_value, category_code, department_code, "
                "spend_type, approver_chain, version, effective_from) values (:c, 'po', :lo, :hi, 'ANY', 'ANY', 'ANY', "
                ":ch, 1, '2024-11-01')"
            ),
            {"c": code, "lo": low, "hi": high, "ch": chain},
        )
    shop.execute(
        text(
            "insert into item (code, name, category_code, base_uom, purchase_uom, active, stocked, standard_price, "
            "gst_rate_before, gst_rate_after) values ('Z', 'Zero-rated part', 'C1', 'NOS', 'NOS', true, true, 1, 0, 0)"
        )
    )
    shop.commit()
    _apex(shop)
    return shop


def _gate(db: Session, level: str = "auto") -> None:
    autonomy.configure(db, sod.user(1), "po.issue", level=level)


def _order(
    db: Session, no: str, item: str, qty: str, price: str, est: str | None = None, *, contract: bool = True
) -> str:
    """One line bought at a price on file (on the contract by default), and the order made from it. The casting
    RM-01 is on the contract at 650 already."""
    if item != "RM-01":
        _price(db, "S1", item, price, contract="K-ACTIVE" if contract else None)
    request = _ask(db, no, [(item, qty, est or price)])
    _source(db, request, dispatch=False)
    (po,) = orders.order_request(db, db.info["slug"], request, as_of=TODAY).pos
    return po


def _po(db: Session, no: str) -> dict:
    return dict(db.execute(text("select * from po where no = :n"), {"n": no}).mappings().one())


# ---- the tests, as plain arithmetic ----------------------------------------------------------------


def _facts(**kw: object) -> tl.Facts:
    base: dict[str, object] = {
        "total": D("38350.00"),
        "buyer_tier": True,
        "buyer_tier_max": D("100000.00"),
        "supplier": "S1",
        "supplier_status": "active",
        "lines": (tl.OrderLine("casting", "contract", D(650), D(650)),),
        "contracts": (tl.ContractCeiling("K", D("2500000"), D("32500")),),
        "budget_over": None,
        "gate_allowed": True,
        "gate_reason": "allowed by the automation settings",
    }
    return tl.Facts(**{**base, **kw})  # type: ignore[arg-type]


RULE = tl.TouchlessRule(1, None, D(2))


@pytest.mark.parametrize(
    ("route", "touchless_"),
    [
        ("contract", True),
        ("reorder", True),
        ("price_list", False),
        ("single_source", False),
        ("emergency", False),
        ("quotation", False),
    ],
)
def test_only_contract_and_safe_reorder_lines_can_be_touchless(route: str, touchless_: bool) -> None:
    found = tl.checks(_facts(lines=(tl.OrderLine("x", route, D(10), D(10)),)), RULE)

    assert all(c.passed for c in found) is touchless_


@pytest.mark.parametrize(("advance", "passed"), [(0, True), (1, False), (30, False)])
def test_an_advance_in_the_payment_terms_stops_touchless(advance: int, passed: bool) -> None:
    found = tl.checks(_facts(advance_pct=advance), RULE)

    (check,) = [c for c in found if c.test == "touchless.advance"]
    assert check.passed is passed and all(c.passed for c in found) is passed
    if not passed:
        assert f"an advance of {advance}% is agreed" in tl.reason(found)


@pytest.mark.parametrize(
    ("total", "rule", "passed"),
    [
        ("100000.00", RULE, True),  # the buyer's tier is the limit
        ("50000.00", tl.TouchlessRule(2, D("50000"), D(2)), True),  # a limit of its own, exactly reached
        ("50000.01", tl.TouchlessRule(2, D("50000"), D(2)), False),
    ],
)
def test_the_touchless_limit_includes_its_edge(total: str, rule: tl.TouchlessRule, passed: bool) -> None:
    (value,) = [c for c in tl.checks(_facts(total=D(total)), rule) if c.test == "touchless.value"]

    assert value.passed is passed


@pytest.mark.parametrize(("used", "passed"), [("2500000.00", True), ("2500000.01", False), ("0", True)])
def test_a_contract_used_up_exactly_still_has_room_and_a_paisa_more_has_not(used: str, passed: bool) -> None:
    ceiling = tl.ContractCeiling("K", D("2500000.00"), D(used))  # what is ordered, this order included

    (check,) = [c for c in tl.checks(_facts(contracts=(ceiling,)), RULE) if c.test == "touchless.contract"]

    assert check.passed is passed


def test_with_no_buyer_tier_and_no_limit_nothing_is_touchless() -> None:
    found = tl.checks(_facts(buyer_tier=False, buyer_tier_max=None), RULE)

    assert {c.test for c in found if not c.passed} == {"touchless.tier", "touchless.value"}


# ---- the tiers ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "tier", "status"),
    [
        ("100000.00", "procurement_head", "submitted"),  # not touchless: a person approves, never the buyer
        ("100000.01", "procurement_head", "submitted"),
        ("500000.00", "procurement_head", "submitted"),
        ("500000.01", "director", "submitted"),
    ],
)
def test_each_tier_starts_one_paisa_above_the_one_below(tiers: Session, value: str, tier: str, status: str) -> None:
    po = _order(tiers, "R-T", "Z", "1", value)

    row = _po(tiers, po)
    assert (row["total"], row["approval_tier"], row["status"]) == (D(value), tier, status)
    approver = tiers.execute(text("select role from po_approval_step order by step desc limit 1")).scalar_one()
    assert approver == tier and row["touchless"] is False
    if value == "100000.00":
        assert "Not touchless, so a person approves it, never the buyer who made it" in row["decision_reason"]
    else:
        assert "needs an approver" in row["touchless_note"]


# ---- touchless ---------------------------------------------------------------------------------------


def test_the_apex_casting_on_contract_is_issued_and_sent_with_no_buyer(tiers: Session, outbox: Outbox) -> None:
    _gate(tiers)

    po = _order(tiers, "R-1", "RM-01", "50", "650")

    row = _po(tiers, po)
    # 50 x 650 = 32,500.00; S1 is in state 27 and the store in 29: IGST 18% = 5,850.00
    assert (row["taxable"], row["igst"], row["total"]) == (D("32500.00"), D("5850.00"), D("38350.00"))
    assert (row["status"], row["touchless"], row["approval_tier"], row["touchless_rule"]) == (
        "sent",
        True,
        "buyer",
        "touchless_po v1",
    )
    assert row["touchless_note"].startswith("Touchless: issued and sent without a buyer")
    assert [c["test"] for c in row["touchless_checks"]] == [
        "touchless.route",
        "touchless.tier",
        "touchless.value",
        "touchless.supplier",
        "touchless.contract",
        "touchless.price",
        "touchless.budget",
        "touchless.advance",
        "touchless.gate",
    ]
    (mail,) = [m for m in outbox.sent if po in m["subject"]]
    assert "Deliver to: Bengaluru store (WH-BLR)" in mail["body"]


def test_a_safe_reorder_from_the_known_supplier_is_touchless(tiers: Session, outbox: Outbox) -> None:
    _gate(tiers)
    request = _ask(tiers, "R-2", [("RM-06", "100", "38")])  # bearings, last bought from S2 17 days ago
    _source(tiers, request, dispatch=False)

    (po,) = orders.order_request(tiers, tiers.info["slug"], request, as_of=TODAY).pos

    row = _po(tiers, po)
    assert (row["supplier_code"], row["status"], row["touchless"]) == ("S2", "sent", True)


@pytest.mark.parametrize(
    ("limit", "value", "touchless_"),
    [
        (None, "100000.00", True),  # the default limit is the buyer's tier, edge included
        ("50000", "50000.00", True),
        ("50000", "50000.01", False),
    ],
)
def test_the_touchless_limit_exactly_and_one_paisa_over(
    tiers: Session, outbox: Outbox, limit: str | None, value: str, touchless_: bool
) -> None:
    _gate(tiers)
    if limit:
        touchless.save_rule(tiers, sod.user(1), effective_from=TODAY, value_limit=D(limit), price_pct=D(2), note="t")

    row = _po(tiers, _order(tiers, "R-3", "Z", "1", value))

    assert (row["touchless"], row["status"]) == ((True, "sent") if touchless_ else (False, "submitted"))
    if limit:
        assert row["touchless_rule"] == "touchless_po v2"
    if not touchless_:
        assert "against the touchless limit ₹ 50,000.00" in row["touchless_note"]


@pytest.mark.parametrize(("price", "touchless_"), [("102", True), ("102.01", False)])
def test_a_price_two_percent_above_the_estimate_is_touchless_and_a_paisa_more_is_not(
    tiers: Session, outbox: Outbox, price: str, touchless_: bool
) -> None:
    _gate(tiers)

    row = _po(tiers, _order(tiers, "R-4", "Z", "1", price, est="100"))

    assert row["touchless"] is touchless_
    if not touchless_:
        assert "is 2.01% against the estimate" in row["touchless_note"]


@pytest.mark.parametrize(("ceiling", "touchless_"), [("1000.00", True), ("999.99", False)])
def test_a_contract_used_up_exactly_is_touchless_and_one_paisa_over_is_held(
    tiers: Session, outbox: Outbox, ceiling: str, touchless_: bool
) -> None:
    _gate(tiers)
    _price(tiers, "S1", "Z", "100")
    request = _ask(tiers, "R-5", [("Z", "10", "100")])  # 1,000.00 before tax
    _source(tiers, request, dispatch=False)  # on the contract, which has no ceiling yet
    tiers.execute(text("update contract set max_value = :m where code = 'K-ACTIVE'"), {"m": ceiling})
    tiers.commit()  # the ceiling is set before the order is made

    (po,) = orders.order_request(tiers, tiers.info["slug"], request, as_of=TODAY).pos

    row = _po(tiers, po)
    if touchless_:
        assert (row["status"], row["touchless"]) == ("sent", True)
        assert "₹ 1,000.00 of ₹ 1,000.00 used" in row["touchless_note"]
    else:  # over the ceiling: held for a person, never issued by itself
        assert (row["status"], row["touchless"]) == ("draft", False) and "ceiling" in row["hold_reason"]


@pytest.mark.parametrize("how", ["propose", "kill switch"])
def test_with_the_autonomy_gate_off_the_order_goes_to_a_person_and_says_why(
    tiers: Session, outbox: Outbox, how: str
) -> None:
    _gate(tiers, "auto" if how == "kill switch" else "propose")
    if how == "kill switch":
        autonomy.set_kill_switch(tiers, sod.user(1), True, "stop")

    row = _po(tiers, _order(tiers, "R-6", "RM-01", "50", "650"))

    assert (row["status"], row["touchless"]) == ("submitted", False)  # to the purchase manager, not the buyer
    why = "the kill switch is on" if how == "kill switch" else "a person confirms it first (automation setting"
    assert f"automatic issue: {why}" in row["touchless_note"]
    assert "po.issue" not in row["touchless_note"]
    assert [m for m in outbox.sent if "Purchase order" in m["subject"]] == []


def test_a_single_source_line_is_never_touchless(tiers: Session, outbox: Outbox) -> None:
    _gate(tiers)
    tiers.execute(text("insert into item_single_source (item_code, supplier_code, reason) values ('Z', 'S1', 'only')"))
    tiers.commit()

    row = _po(tiers, _order(tiers, "R-7", "Z", "10", "100", contract=False))

    assert tiers.execute(text("select source_ref from po_line where po_no = :n"), {"n": row["no"]}).scalar_one() == (
        "single_source"
    )
    assert (row["status"], row["touchless"]) == ("submitted", False)
    assert "Zero-rated part is single source: never touchless" in row["touchless_note"]


# ---- audit and segregation of duties --------------------------------------------------------------------


def test_a_touchless_order_is_audited_as_the_system_and_its_rule_with_the_facts_checked(
    tiers: Session, outbox: Outbox
) -> None:
    _gate(tiers)

    po = _order(tiers, "R-8", "RM-01", "50", "650")

    rows = {
        r["action"]: r
        for r in tiers.execute(
            text("select * from audit_event where entity = 'po' and entity_id = :n order by id"), {"n": po}
        ).mappings()
    }
    check = rows["po.touchless_check"]
    assert (check["actor_type"], check["actor_id"], check["rule_version"]) == (
        "rule",
        "touchless_po v1",
        "touchless_po v1; PO-01:v1",
    )
    assert check["after"]["touchless"] is True and len(check["after"]["checks"]) == 9
    assert (rows["po.send"]["actor_type"], rows["po.send"]["actor_id"]) == ("rule", "touchless_po v1")
    outcome = tiers.execute(text("select auto, ref from autonomy_outcome where decision_type = 'po.issue'")).one()
    assert tuple(outcome) == (True, po)


def test_no_person_holds_a_duty_on_a_touchless_order_and_the_buyer_still_cannot_approve_above_their_tier(
    tiers: Session, outbox: Outbox
) -> None:
    logins = _logins(tiers)
    _gate(tiers)
    small = _order(tiers, "R-9", "RM-01", "50", "650")
    actors = tiers.execute(
        text("select distinct actor_type from audit_event where entity = 'po' and entity_id = :n"), {"n": small}
    ).scalars()
    assert set(actors) <= {"service", "rule"}  # nobody approved or sent it, so nobody's duties change

    big = _order(tiers, "R-10", "Z", "1", "200000")
    tiers.execute(text("update po set buyer = 'U5' where no = :n"), {"n": big})  # the approver bought it himself
    tiers.commit()
    with pytest.raises(workflow.ApprovalError, match="segregation of duties"):
        workflow.approve(tiers, tiers.info["slug"], big, logins["procurement"])


def _resubmit(db: Session, po: str, buyer: str) -> list[tuple[str, str, str | None]]:
    """Put a submitted order back to draft with another buyer, submit it again, and return its new chain."""
    db.execute(text("delete from po_approval_step where po_no = :n"), {"n": po})
    db.execute(text("update po set status = 'draft', buyer = :b where no = :n"), {"b": buyer, "n": po})
    db.commit()
    assert workflow.submit(db, db.info["slug"], po, sod.user(1), today=TODAY) == "submitted"
    return [
        (r[0], r[1], r[2])
        for r in db.execute(
            text("select role, approver, delegate from po_approval_step where po_no = :n order by step"), {"n": po}
        )
    ]


def test_a_buyer_who_holds_the_approving_role_is_skipped_and_the_order_goes_up_a_tier(
    tiers: Session, outbox: Outbox
) -> None:
    _gate(tiers, "propose")  # not touchless: a person approves even a small order
    po = _order(tiers, "R-12", "RM-01", "10", "650")
    assert [r[0] for r in tiers.execute(text("select role from po_approval_step where po_no = :n"), {"n": po})] == [
        "procurement_head"
    ]

    chain = _resubmit(tiers, po, "U5")  # the procurement head made this order himself

    assert [(role, who) for role, who, _ in chain] == [("director", chain[0][1])] and chain[0][1] != "U5"
    assert "U5" in workflow.makers(tiers, _po(tiers, po))


def test_a_deputy_who_made_the_order_does_not_stand_in_for_the_approver(tiers: Session, outbox: Outbox) -> None:
    _gate(tiers, "propose")
    po = _order(tiers, "R-13", "RM-01", "10", "650")
    head = tiers.execute(text("select approver from po_approval_step where po_no = :n"), {"n": po}).scalar_one()
    buyer = tiers.execute(
        text("select code from org_user where code <> :h order by code limit 1"), {"h": head}
    ).scalar_one()
    tiers.execute(
        text("insert into delegation (from_user, to_user, start_date, reason) values (:f, :t, '2024-01-01', 'leave')"),
        {"f": head, "t": buyer},
    )
    tiers.commit()

    chain = _resubmit(tiers, po, buyer)

    assert chain == [("procurement_head", head, None)]  # no deputy rather than the buyer as deputy


def test_the_buyer_cannot_accept_a_price_variance_on_their_own_order(tiers: Session, outbox: Outbox) -> None:
    logins = _logins(tiers)
    _gate(tiers, "propose")
    po = _order(tiers, "R-14", "RM-01", "10", "650")
    tiers.execute(text("update po set buyer = 'U5', status = 'draft' where no = :n"), {"n": po})
    tiers.commit()

    with pytest.raises(workflow.ApprovalError, match="segregation of duties"):
        orders._accept_variance(tiers, {"po_no": po, "tenant": tiers.info["slug"]}, sod.user(logins["procurement"]))


# ---- the delivery location ----------------------------------------------------------------------------


def test_lines_for_two_locations_become_two_orders_each_with_its_place_and_tax(tiers: Session, outbox: Outbox) -> None:
    _gate(tiers)
    tiers.execute(
        text(
            "insert into location (code, name, address, city, pincode, gstin, state_code) values "
            "('WH-MUM', 'Mumbai store', 'Dock 4', 'Mumbai', '400001', '27BBBBB0000B1Z5', '27')"
        )
    )
    tiers.commit()
    request = _ask(tiers, "R-11", [("RM-01", "10", "650"), ("RM-01", "20", "650")])
    tiers.execute(
        text("update request_line set delivery_location = 'WH-MUM' where request_no = :r and line_no = 2"),
        {"r": request},
    )
    tiers.commit()
    _source(tiers, request, dispatch=False)

    made = orders.order_request(tiers, tiers.info["slug"], request, as_of=TODAY)

    by_place = {
        r["ship_to_location"]: r
        for r in tiers.execute(text("select * from po where request_no = :r order by no"), {"r": request}).mappings()
    }
    assert sorted(made.pos) == sorted(r["no"] for r in by_place.values()) and len(made.pos) == 2
    blr, mum = by_place["WH-BLR"], by_place["WH-MUM"]
    assert (blr["taxable"], blr["igst"], blr["cgst"]) == (D("6500.00"), D("1170.00"), D("0.00"))  # 27 -> 29
    assert (mum["taxable"], mum["igst"], mum["cgst"], mum["sgst"]) == (
        D("13000.00"),
        D("0.00"),
        D("1170.00"),
        D("1170.00"),
    )
    assert "-MUM-" in mum["no"] and mum["buyer_gstin"] == "27BBBBB0000B1Z5"
    (mail,) = [m for m in outbox.sent if mum["no"] in m["subject"]]
    assert "Deliver to: Mumbai store (WH-MUM), Dock 4, Mumbai, 400001" in mail["body"]


# ---- the screens ------------------------------------------------------------------------------------------


def test_the_order_list_and_page_show_the_tier_approver_place_and_touchless_badge(
    tiers: Session, outbox: Outbox
) -> None:
    logins = _logins(tiers)
    _gate(tiers)
    po = _order(tiers, "R-12", "RM-01", "50", "650")
    headers = _headers(tiers.info["slug"], logins["buyer"], "Buyer")

    (row,) = [r for r in client.get("/api/purchase-orders", headers=headers).json() if r["id"] == po]
    page = client.get(f"/api/purchase-orders/{po}", headers=headers).json()

    assert (row["tier"], row["approver"], row["ship_to_name"], row["touchless"]) == (
        "buyer",
        "the system (touchless)",
        "Bengaluru store",
        True,
    )
    assert page["touchless_note"].startswith("Touchless") and len(page["touchless_checks"]) == 9
    assert page["touchless_rule"] == "touchless_po v1" and page["ship_to"] == "WH-BLR"


def test_touchless_orders_are_on_for_apex_and_off_for_brightwave() -> None:
    assert demo.company("apex").autonomy == {"po.issue": "auto"}
    assert demo.company("brightwave").autonomy == {"po.issue": "propose"}


def _maker_trace(db: Session, po: str, how: str, user_id: int) -> None:
    """Leave in the audit log (or the revisions) the mark of one way of shaping the order, done by this login."""
    line = db.execute(text("select request_no, request_line_no from po_line where po_no = :n"), {"n": po}).one()
    if how == "award":
        db.execute(
            text("insert into rfq (no, due_date, type, status) values ('RFQ-C1', :d, 'commercial', 'awarded')"),
            {"d": TODAY},
        )
        db.execute(
            text(
                "insert into rfq_line (rfq_no, line_no, request_no, request_line_no, qty) values ('RFQ-C1', 1, :r, :l, 1)"
            ),
            {"r": line[0], "l": line[1]},
        )
        audit.record(db, sod.user(user_id), "rfq.award", "rfq", "RFQ-C1")
    elif how == "route":
        audit.record(
            db, sod.user(user_id), "sourcing.route_change_proposed", "request", f"{line[0]}/{line[1]}",
            after={"route": "direct"},
        )  # fmt: skip
    else:
        db.execute(
            text(
                "insert into po_revision (po_no, version, changed_by, reason, changes, total_before, total_after, "
                "reapproval) values (:n, 2, :u, 'more', '[]', 1, 2, true)"
            ),
            {"n": po, "u": str(user_id)},
        )
    db.commit()


@pytest.mark.parametrize("how", ["award", "route", "revise"])
def test_who_awarded_rerouted_or_revised_the_order_is_one_of_its_makers_and_never_approves_it(
    tiers: Session, outbox: Outbox, how: str
) -> None:
    logins = _logins(tiers)
    _gate(tiers, "propose")
    po = _order(tiers, f"R-2{len(how)}", "RM-01", "10", "650")
    head = tiers.execute(text("select approver from po_approval_step where po_no = :n"), {"n": po}).scalar_one()
    assert workflow.org_code_of(tiers, logins["procurement"]) == head

    _maker_trace(tiers, po, how, logins["procurement"])

    assert head in workflow.makers(tiers, _po(tiers, po))
    with pytest.raises(workflow.ApprovalError, match="segregation of duties"):
        workflow.approve(tiers, tiers.info["slug"], po, logins["procurement"])
    chain = _resubmit(tiers, po, _po(tiers, po)["buyer"])
    assert chain and head not in [who for _, who, _ in chain]


def test_a_revision_made_by_accepting_a_suppliers_price_does_not_make_the_approver_a_maker(
    tiers: Session, outbox: Outbox
) -> None:
    logins = _logins(tiers)
    _gate(tiers, "propose")
    po = _order(tiers, "R-30", "RM-01", "10", "650")
    head = workflow.org_code_of(tiers, logins["procurement"])
    _maker_trace(tiers, po, "revise", logins["procurement"])
    tiers.execute(
        text(
            "insert into po_change (po_no, po_line_no, kind, old_value, new_value, source, status, proposal_id, "
            "version_made) values (:n, 1, 'price', '650', '660', 'link', 'accepted', 1, 2)"
        ),
        {"n": po},
    )
    tiers.commit()

    assert head not in workflow.makers(tiers, _po(tiers, po))


def test_when_every_approver_made_the_order_it_is_refused_with_a_clear_reason_not_approved(
    tiers: Session, outbox: Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gate(tiers, "propose")
    po = _order(tiers, "R-31", "RM-01", "10", "650")
    tiers.execute(text("delete from po_approval_step where po_no = :n"), {"n": po})
    tiers.execute(text("update po set status = 'draft' where no = :n"), {"n": po})
    tiers.commit()
    everyone = frozenset(str(r[0]) for r in tiers.execute(text("select code from org_user")))
    monkeypatch.setattr(workflow, "makers", lambda db, order: everyone)  # all of them shaped the order

    held = workflow.submit(tiers, tiers.info["slug"], po, sod.user(1), today=TODAY)

    row = _po(tiers, po)
    assert held == "held" and row["status"] == "draft"  # never approved by a maker, and the reason is on the order
    assert row["hold_reason"].startswith("Nobody may approve it:") and "add an approver" in row["hold_reason"]
    from p2p.formulas.calendar import BusinessCalendar
    from p2p.platform import exceptions

    stuck = [s for s in exceptions.collect(tiers, TODAY, BusinessCalendar(frozenset())) if s.kind == "order"]
    assert [s.ref for s in stuck] == [po]
    monkeypatch.undo()  # the rules now name someone who did not make it: submitting again goes on
    assert workflow.submit(tiers, tiers.info["slug"], po, sod.user(1), today=TODAY) == "submitted"
    assert _po(tiers, po)["hold_reason"] is None


def test_an_approver_who_revises_an_order_waiting_for_them_hands_its_approval_to_the_next_person(
    tiers: Session, outbox: Outbox
) -> None:
    from p2p.orders import changes

    logins = _logins(tiers)
    _gate(tiers, "propose")
    po = _order(tiers, "R-40", "RM-01", "200", "650")  # Rs 1.5 lakh: the procurement head's band
    head = workflow.org_code_of(tiers, logins["procurement"])
    assert tiers.execute(text("select approver from po_approval_step where po_no = :n"), {"n": po}).scalar_one() == head

    done = changes.revise(
        tiers,
        tiers.info["slug"],
        po,
        logins["procurement"],
        "Fewer castings",
        [changes.LineChange(1, qty=Decimal("190"))],
    )

    assert done["status"] == "submitted" and done["reapproval"] is True  # approval restarts on the new version
    steps = tiers.execute(
        text(
            "select approver, status from po_approval_step where po_no = :n and round = "
            "(select approval_round from po where no = :n) order by step"
        ),
        {"n": po},
    ).all()
    assert steps and all(a != head for a, _ in steps)  # the reviser is now one of its makers
    with pytest.raises(workflow.ApprovalError, match="segregation of duties|not your turn"):
        workflow.approve(tiers, tiers.info["slug"], po, logins["procurement"])


def test_an_order_waiting_for_approval_is_not_issued_once_its_supplier_is_blocked(
    tiers: Session, outbox: Outbox
) -> None:
    logins = _logins(tiers)
    po = _order(tiers, "R-BLK", "Z", "1", "200000")
    tiers.execute(
        text("update supplier set status = 'blocked' where code = :c"), {"c": _po(tiers, po)["supplier_code"]}
    )
    tiers.commit()

    with pytest.raises(workflow.ApprovalError, match="not active"):
        workflow.approve(tiers, tiers.info["slug"], po, logins["procurement"])

    assert _po(tiers, po)["status"] == "submitted"
    refused = tiers.execute(
        text("select count(*) from audit_event where action = 'po.issue_refused' and entity_id = :n"), {"n": po}
    ).scalar_one()
    assert refused == 1


def test_an_order_to_a_supplier_blocked_after_sourcing_is_made_but_never_touchless(
    tiers: Session, outbox: Outbox
) -> None:
    _gate(tiers)
    _price(tiers, "S1", "Z", "500", contract="K-ACTIVE")
    request = _ask(tiers, "R-BLK2", [("Z", "1", "500")])
    _source(tiers, request, dispatch=False)
    tiers.execute(text("update supplier set status = 'blocked' where code = 'S1'"))
    tiers.commit()

    (po,) = orders.order_request(tiers, tiers.info["slug"], request, as_of=TODAY).pos

    row = _po(tiers, po)
    assert row["touchless"] is False and row["status"] == "submitted"  # waits for a person, who cannot approve it
