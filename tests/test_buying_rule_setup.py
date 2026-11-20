"""The buying rules on the company setup screen: the buying route, the supplier selection rule, the touchless order rule
and the single-source items. Only an Admin changes them, every change needs a reason and is a new version from a date
(never an edit of an old one), nonsense is refused, the change is in the audit log and the setup history, the platform
then decides by the new version while earlier decisions keep the one they used, and one company never sees another's."""

import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from setup_changes import change
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_buying_route import _ask, _decisions, apex  # noqa: F401 - fixtures
from test_orders import _comp, _quote, _sent_rfq, shop  # noqa: F401 - fixtures
from test_po_tiers import _gate, _order, _po, tiers  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, TODAY, Outbox, _source, db, outbox  # noqa: F401 - fixtures

from p2p.main import app
from p2p.orders import quotes, selection_rule
from p2p.platform import routing, sod, tenants, users
from p2p.platform.tokens import create_access_token
from p2p.sourcing import route

client = TestClient(app)
D = Decimal
TOMORROW = (TODAY + timedelta(days=1)).isoformat()


_CHECKERS: dict[str, dict[str, str]] = {}  # an administrator's token -> a second administrator of the same company


def _login(db: Session, role: str, *, checker: bool = True) -> dict[str, str]:
    slug = db.info["slug"]
    uid = users.create_user(slug, f"{role.lower()}{uuid.uuid4().hex[:6]}@x.example", PASSWORD, f"A {role}", role)
    headers = {"Authorization": f"Bearer {create_access_token(uid, slug, role)}"}
    if role == "Admin" and checker:
        _CHECKERS[headers["Authorization"]] = _login(db, "Admin", checker=False)
    return headers


def _apply(verb: str, path: str, headers: dict[str, str], **kw: Any) -> Any:
    """A rule change by this administrator, approved by a second one when it waits for approval (D-22)."""
    checker = _CHECKERS.get(headers["Authorization"])
    if checker is None:
        return getattr(client, verb)(path, headers=headers, **kw)
    return change(client, verb, path, headers, checker, **kw)


def _changes(db: Session, area: str) -> list[dict[str, Any]]:
    db.expire_all()
    rows = db.execute(
        text("select key, old_value, new_value, reason from setup_change where area = :a order by id"), {"a": area}
    ).mappings()
    return [dict(r) for r in rows]


ROUTE = {"values": {"reorder_below": "3000"}, "effective_from": TODAY.isoformat(), "reason": "Smaller safe reorders"}
SELECTION = {
    "method": "weighted",
    "weights": {"price": "0", "delivery": "0", "quality": "0", "payment_terms": "100"},
    "tie_break": ["vendor_score", "delivery"],
    "input_credit": True,
    "effective_from": TODAY.isoformat(),
    "reason": "Credit matters most this year",
}
TOUCHLESS = {"value_limit": "50000", "price_pct": "2", "effective_from": TODAY.isoformat(), "reason": "Start small"}
WRITES = [
    ("post", "/api/setup/buying-route", ROUTE),
    ("post", "/api/setup/supplier-selection", SELECTION),
    ("post", "/api/setup/touchless", TOUCHLESS),
    ("put", "/api/setup/single-source/A", {"supplier_code": "S2", "reason": "Only Bravo makes it"}),
]


# ---- who may see and change them -----------------------------------------------------------------


@pytest.mark.parametrize("path", ["buying-route", "supplier-selection", "touchless", "single-source"])
def test_the_rule_screens_are_for_an_admin_only(db: Session, path: str) -> None:  # noqa: F811
    assert client.get(f"/api/setup/{path}").status_code == 401
    for role in ("Buyer", "Approver", "Requester"):
        assert client.get(f"/api/setup/{path}", headers=_login(db, role)).status_code == 403, role
    assert client.get(f"/api/setup/{path}", headers=_login(db, "Admin")).status_code == 200


def test_no_rule_changes_without_the_admin_role_or_without_a_reason(db: Session) -> None:  # noqa: F811
    buyer, admin = _login(db, "Buyer"), _login(db, "Admin")
    for verb, path, body in WRITES:
        assert getattr(client, verb)(path, json=body, headers=buyer).status_code == 403, path
        no_reason = {k: v for k, v in body.items() if k != "reason"}
        assert getattr(client, verb)(path, json=no_reason, headers=admin).status_code == 422, path
    assert _apply("delete", "/api/setup/single-source/A", headers=admin).status_code == 422
    assert db.execute(text("select count(*) from setup_change")).scalar_one() == 0
    assert db.execute(text("select count(*) from buying_route_rule")).scalar_one() == 1


def test_one_company_never_sees_or_changes_anothers_rules(db: Session) -> None:  # noqa: F811
    other = "b" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other, load_reference=False).ok
    uid = users.create_user(other, f"admin{uuid.uuid4().hex[:6]}@x.example", PASSWORD, "Other Admin", "Admin")
    uid2 = users.create_user(other, f"admin{uuid.uuid4().hex[:6]}@x.example", PASSWORD, "Their Checker", "Admin")
    theirs = {"Authorization": f"Bearer {create_access_token(uid, other, 'Admin')}"}
    checker = {"Authorization": f"Bearer {create_access_token(uid2, other, 'Admin')}"}

    assert change(client, "post", "/api/setup/touchless", theirs, checker, json=TOUCHLESS).status_code == 201

    mine = client.get("/api/setup/touchless", headers=_login(db, "Admin")).json()
    assert [v["version"] for v in mine["versions"]] == [1]
    with Session(routing.engine_for(other)) as s:
        assert s.execute(text("select max(version) from touchless_rule")).scalar_one() == 2


# ---- the buying route ------------------------------------------------------------------------------


def test_the_buying_route_shows_the_version_in_force_with_its_thresholds_and_who_set_it(db: Session) -> None:  # noqa: F811
    data = client.get("/api/setup/buying-route", headers=_login(db, "Admin")).json()

    assert [f["key"] for f in data["fields"]][:2] == ["two_quotes_from", "three_quotes_above"]
    assert {f["kind"] for f in data["fields"]} == {"inr", "days", "pct"}
    (v1,) = data["versions"]
    assert v1["in_force"] and v1["category"] is None and v1["by"] == "Set up with the company"
    assert v1["values"]["two_quotes_from"] == "25000" and v1["values"]["reorder_on_time_pct"] == "80"


def test_a_new_route_version_decides_the_next_line_and_the_earlier_decision_keeps_version_one(apex: Session) -> None:
    first = _ask(apex, "R-1", [("RM-06", "100", None)])
    _source(apex, first, dispatch=False)  # ₹ 3,800 of bearings: a safe reorder under version 1
    admin = _login(apex, "Admin")

    r = _apply("post", "/api/setup/buying-route", json=ROUTE, headers=admin)

    assert r.status_code == 201 and r.json() == {"version": 2}
    (line,) = _source(apex, _ask(apex, "R-2", [("RM-06", "10", None)]), dispatch=False).lines
    assert line.route == "rfq" and line.route_rule == "buying_route v2" and "(under ₹ 3,000.00)" in line.reason
    assert _decisions(apex, first)[1]["route_rule"] == "buying_route v1"
    apex.expire_all()
    old = apex.execute(text("select reorder_below, effective_to from buying_route_rule where version = 1")).one()
    assert old == (D("25000.00"), TODAY - timedelta(days=1))  # closed, not edited
    (change,) = _changes(apex, "buying_route_rule")
    assert change["old_value"]["reorder_below"] == "25000" and change["new_value"] == {"reorder_below": "3000"}
    assert change["reason"] == "Smaller safe reorders"
    audit_row = apex.execute(
        text("select actor_type, rule_version, reason from audit_event where action = 'buying_route.change'")
    ).one()
    assert tuple(audit_row) == ("user", "buying_route company v2", "Smaller safe reorders")
    versions = client.get("/api/setup/buying-route", headers=admin).json()["versions"]
    assert [(v["version"], v["in_force"]) for v in versions] == [(2, True), (1, False)]
    assert versions[0]["by"] == "A Admin" and versions[0]["note"] == "Smaller safe reorders"


def test_a_category_version_tightens_only_its_items_and_names_only_its_thresholds(apex: Session) -> None:
    admin = _login(apex, "Admin")
    body = {**ROUTE, "category": "C1", "reason": "Raw material reorders smaller"}

    r = _apply("post", "/api/setup/buying-route", json=body, headers=admin)

    assert r.status_code == 201 and r.json() == {"version": 1}
    (line,) = _source(apex, _ask(apex, "R-1", [("RM-06", "100", None)]), dispatch=False).lines
    assert line.route == "rfq" and line.route_rule == "buying_route v1 + C1 v1"
    apex.expire_all()
    row = apex.execute(
        text("select reorder_below, agree_above from buying_route_rule where category_code = 'C1'")
    ).one()
    assert row == (D("3000.00"), None)  # the company's other thresholds still apply
    (change,) = _changes(apex, "buying_route_rule")
    assert (change["key"], change["old_value"], change["new_value"]) == ("C1", None, {"reorder_below": "3000"})
    shown = client.get("/api/setup/buying-route", headers=admin).json()
    assert "C1" in [c["code"] for c in shown["categories"]]
    assert [(v["category"], v["version"]) for v in shown["versions"]] == [(None, 1), ("C1", 1)]
    for bad, says in (({"category": "NOPE"}, "no item category NOPE"), ({"values": {"agree_above": "-5"}}, "negative")):
        r = _apply("post", "/api/setup/buying-route", json={**body, **bad}, headers=admin)
        assert r.status_code == 422 and says in r.json()["detail"]


@pytest.mark.parametrize(
    ("values", "says"),
    [
        ({"reorder_below": "-1"}, "cannot be negative"),
        ({"two_quotes_from": "200000", "three_quotes_above": "200000"}, "must be higher than the value for two"),
        ({"three_quotes_above": "20000"}, "must be higher than the value for two"),
        ({"reorder_on_time_pct": "100.01"}, "cannot be above 100"),
        ({"window_days": "0"}, "whole number of at least 1"),
        ({"window_days": "7.5"}, "whole number of at least 1"),
        ({"budget": "1"}, "not a threshold"),
    ],
)
def test_route_thresholds_that_make_no_sense_are_refused(db: Session, values: dict, says: str) -> None:  # noqa: F811
    r = _apply("post", "/api/setup/buying-route", json={**ROUTE, "values": values}, headers=_login(db, "Admin"))

    assert r.status_code == 422 and says in r.json()["detail"]
    assert db.execute(text("select count(*) from buying_route_rule")).scalar_one() == 1
    assert _changes(db, "buying_route_rule") == []


def test_a_version_cannot_start_in_the_past_or_before_the_latest_one(db: Session) -> None:  # noqa: F811
    admin = _login(db, "Admin")
    past = {**ROUTE, "effective_from": (TODAY - timedelta(days=1)).isoformat()}
    assert (
        "starts today or later" in _apply("post", "/api/setup/buying-route", json=past, headers=admin).json()["detail"]
    )
    later = {**TOUCHLESS, "effective_from": (TODAY + timedelta(days=10)).isoformat()}
    assert _apply("post", "/api/setup/touchless", json=later, headers=admin).status_code == 201

    r = _apply("post", "/api/setup/touchless", json={**TOUCHLESS, "effective_from": TOMORROW}, headers=admin)

    assert r.status_code == 422 and "cannot start before it" in r.json()["detail"]


# ---- the supplier selection rule -------------------------------------------------------------------


def test_a_new_selection_rule_ranks_the_next_rfq_and_the_earlier_award_keeps_version_one(
    shop: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    _, first = _sent_rfq(shop, outbox, lines=[("B", "5", None, "C1")])
    _quote(shop, first, "S1", "100")
    _quote(shop, first, "S2", "110")
    quotes.award(shop, first, {1: "S1"}, sod.user(7), as_of=TODAY)

    r = _apply("post", "/api/setup/supplier-selection", json=SELECTION, headers=_login(shop, "Admin"))

    assert r.status_code == 201 and r.json() == {"version": 2}
    _, second = _sent_rfq(shop, outbox, no="R-2", lines=[("B", "5", None, "C1")])
    comp = _comp(shop, second)
    assert comp.rule == "supplier_selection v2"
    assert shop.execute(text("select selection_rule from award where rfq_no = :r"), {"r": first}).scalar_one() == (
        "supplier_selection v1"
    )
    rule = selection_rule.rule_for(shop, TODAY)
    assert rule.tie_break == ("vendor_score", "delivery") and rule.weights.payment_terms == 100
    (change,) = _changes(shop, "supplier_selection_rule")
    assert change["old_value"]["method"] == "total_cost" and change["new_value"]["weights"]["payment_terms"] == "100"


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"weights": {"price": "40", "delivery": "30", "quality": "20", "payment_terms": "5"}}, "add up to 100"),
        ({"weights": {"price": "110", "delivery": "-10", "quality": "0", "payment_terms": "0"}}, "not be negative"),
        ({"weights": {"price": "100"}}, "the four weights"),
        ({"method": "cheapest"}, "lowest total cost or weighted score"),
        ({"tie_break": ["delivery", "delivery"]}, "a tie is broken"),
    ],
)
def test_a_selection_rule_that_makes_no_sense_is_refused(db: Session, change: dict, says: str) -> None:  # noqa: F811
    r = _apply("post", "/api/setup/supplier-selection", json={**SELECTION, **change}, headers=_login(db, "Admin"))

    assert r.status_code == 422 and says in r.json()["detail"]
    assert db.execute(text("select count(*) from supplier_selection_rule")).scalar_one() == 1


# ---- the touchless order rule ----------------------------------------------------------------------


def test_a_lower_touchless_limit_stops_the_next_order_and_the_earlier_one_keeps_version_one(
    tiers: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    _gate(tiers)
    first = _po(tiers, _order(tiers, "R-1", "Z", "1", "60000"))
    assert (first["touchless"], first["touchless_rule"]) == (True, "touchless_po v1")
    admin = _login(tiers, "Admin")
    shown = client.get("/api/setup/touchless", headers=admin).json()
    assert shown["buyer_tier_max"] == "100000.00" and shown["versions"][0]["value_limit"] is None

    r = _apply("post", "/api/setup/touchless", json=TOUCHLESS, headers=admin)

    assert r.status_code == 201 and r.json() == {"version": 2}
    second = _po(tiers, _order(tiers, "R-2", "Z", "1", "60000"))
    assert (second["touchless"], second["touchless_rule"]) == (False, "touchless_po v2")
    assert "against the touchless limit ₹ 50,000.00" in second["touchless_note"]
    assert _po(tiers, first["no"])["touchless_rule"] == "touchless_po v1"
    (change,) = _changes(tiers, "touchless_rule")
    assert change["old_value"] == {"value_limit": None, "price_pct": "2"}
    assert change["new_value"] == {"value_limit": "50000", "price_pct": "2"}


@pytest.mark.parametrize(
    ("change", "says"),
    [({"value_limit": "-1"}, "cannot be negative"), ({"price_pct": "-0.5"}, "cannot be negative"),
     ({"price_pct": "101"}, "above 100")],
)  # fmt: skip
def test_a_touchless_rule_that_makes_no_sense_is_refused(db: Session, change: dict, says: str) -> None:  # noqa: F811
    r = _apply("post", "/api/setup/touchless", json={**TOUCHLESS, **change}, headers=_login(db, "Admin"))

    assert r.status_code == 422 and says in r.json()["detail"]
    assert db.execute(text("select count(*) from touchless_rule")).scalar_one() == 1


# ---- single-source items ---------------------------------------------------------------------------


def test_an_item_is_marked_single_source_listed_and_cleared_with_reasons(db: Session) -> None:  # noqa: F811
    admin = _login(db, "Admin")

    assert _apply("put", "/api/setup/single-source/A", json=WRITES[3][2], headers=admin).status_code == 200

    (mark,) = client.get("/api/setup/single-source", headers=admin).json()["marks"]
    assert (mark["item_code"], mark["supplier_code"], mark["reason"], mark["marked_by"]) == (
        "A",
        "S2",
        "Only Bravo makes it",
        "A Admin",
    )
    assert route.Router(db, TODAY)._single_source("A") == "S2"
    r = _apply("delete", "/api/setup/single-source/A?reason=Second%20maker%20found", headers=admin)
    assert r.status_code == 200 and client.get("/api/setup/single-source", headers=admin).json()["marks"] == []
    assert [(c["old_value"], c["new_value"]) for c in _changes(db, "item_single_source")] == [
        (None, "S2"),
        ("S2", None),
    ]
    assert db.execute(text("select count(*) from audit_event where action = 'item.single_source'")).scalar_one() == 2


def test_a_single_source_needs_a_known_item_and_an_active_supplier(db: Session) -> None:  # noqa: F811
    admin = _login(db, "Admin")
    db.execute(text("update supplier set status = 'blocked' where code = 'S3'"))
    db.commit()
    body = {"reason": "x"}

    assert (
        _apply("put", "/api/setup/single-source/NOPE", json={**body, "supplier_code": "S2"}, headers=admin).status_code
        == 404
    )
    r = _apply("put", "/api/setup/single-source/A", json={**body, "supplier_code": "S3"}, headers=admin)
    assert r.status_code == 422 and "only an active supplier" in r.json()["detail"]
    assert (
        _apply("put", "/api/setup/single-source/A", json={**body, "supplier_code": "ZZ"}, headers=admin).status_code
        == 422
    )
    assert _apply("delete", "/api/setup/single-source/A?reason=x", headers=admin).status_code == 404
