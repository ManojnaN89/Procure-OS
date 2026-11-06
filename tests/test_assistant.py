"""The assistant: read-only tools over the real data, answers that must be grounded in what the tools returned, and the
screen's chat endpoint. The model is a scripted fake; what is tested is everything around it."""

import json
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_ai_steps import Scripted
from test_approvals import _request as approval_request
from test_approvals import _submit
from test_orders import _headers, _logins, _ordered, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.agents import assistant
from p2p.ai_steps.gateway import Gateway
from p2p.main import app
from p2p.platform.sod import Actor

D = Decimal
client = TestClient(app)
ACTOR = Actor("agent", "assistant")


def decision(**kw: Any) -> str:
    return json.dumps({"confidence": 0.9, **kw})


def gateway(*turns: str) -> tuple[Gateway, Scripted]:
    provider = Scripted({"m": list(turns)})
    return Gateway(
        {"fake": provider}, {"agent": [("fake", "m")], "default": [("fake", "m")]}, sleep=lambda s: None
    ), provider


# ---- the tools only read ----------------------------------------------------------------------


def test_every_assistant_tool_is_read_only(shop: Session) -> None:  # noqa: F811
    registry = assistant.build_registry(shop, 1)

    assert registry.names() == [
        "find_requests",
        "get_order",
        "get_request",
        "get_rfq",
        "list_orders",
        "my_pending_work",
        "stock_status",
    ]
    assert all(not registry.get(name).writes for name in registry.names())  # type: ignore[union-attr]


def test_running_every_tool_changes_nothing_in_the_database(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _ordered(shop, outbox)
    tables = [
        "request",
        "request_line",
        "po",
        "po_line",
        "rfq",
        "quote",
        "award",
        "audit_event",
        "approval_step",
        "stock_movement",
    ]
    before = {t: shop.execute(text(f"select count(*) from {t}")).scalar_one() for t in tables}  # noqa: S608
    registry = assistant.build_registry(shop, 1)

    registry.get("find_requests").fn(shop, assistant.StatusFilter(), ACTOR)  # type: ignore[union-attr]
    registry.get("get_request").fn(shop, assistant.ByNumber(no="R-1"), ACTOR)  # type: ignore[union-attr]
    registry.get("get_rfq").fn(shop, assistant.ByNumber(no="RFQ-2627-00001"), ACTOR)  # type: ignore[union-attr]
    registry.get("list_orders").fn(shop, assistant.StatusFilter(), ACTOR)  # type: ignore[union-attr]
    registry.get("get_order").fn(shop, assistant.ByNumber(no="PO-BLR-2627-00001"), ACTOR)  # type: ignore[union-attr]
    registry.get("stock_status").fn(shop, assistant.ItemQuery(query="bearing"), ACTOR)  # type: ignore[union-attr]
    registry.get("my_pending_work").fn(shop, assistant.NoParams(), ACTOR)  # type: ignore[union-attr]
    shop.rollback()

    assert {t: shop.execute(text(f"select count(*) from {t}")).scalar_one() for t in tables} == before  # noqa: S608


def test_the_order_tool_returns_the_exact_amounts_as_text(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _ordered(shop, outbox)

    got = assistant.build_registry(shop, 1).get("get_order").fn(shop, assistant.ByNumber(no="PO-BLR-2627-00001"), ACTOR)  # type: ignore[union-attr]

    assert (got["total"], got["igst"], got["taxable"], got["supplier"], got["total_tax"]) == (
        "2331.09",
        "355.59",
        "1975.5",
        "Alpha Traders",
        "355.59",  # worked out in code, so the model never has to add the tax parts itself
    )
    assert [(ln["item"], ln["qty"], ln["unit_price"]) for ln in got["lines"]] == [
        ("Bearing", "10", "100"),
        ("Seal", "5", "180"),
    ]


def test_an_unknown_number_gets_a_plain_error_not_a_crash(shop: Session) -> None:  # noqa: F811
    registry = assistant.build_registry(shop, 1)

    assert registry.get("get_order").fn(shop, assistant.ByNumber(no="PO-NOPE"), ACTOR) == {
        "error": "no purchase order called PO-NOPE"
    }  # type: ignore[union-attr]
    assert "error" in registry.get("get_request").fn(shop, assistant.ByNumber(no="x"), ACTOR)  # type: ignore[union-attr]
    assert "error" in registry.get("stock_status").fn(shop, assistant.ItemQuery(query="zzz"), ACTOR)  # type: ignore[union-attr]


def test_what_is_waiting_for_me_shows_only_my_own_approvals(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    logins = _logins(shop)
    _submit(shop, approval_request(shop, "R-A", "50000.00"))  # waits for the stores head
    _ordered(shop, outbox)  # and an order waits for the procurement head

    def pending(user: int) -> dict[str, Any]:
        return assistant.build_registry(shop, user).get("my_pending_work").fn(shop, assistant.NoParams(), ACTOR)  # type: ignore[union-attr, no-any-return]

    stores, procurement, director = (
        pending(logins["stores"]),
        pending(logins["procurement"]),
        pending(logins["director"]),
    )
    assert [r["no"] for r in stores["requests_waiting_for_my_approval"]] == ["R-A"]
    assert [o["no"] for o in procurement["orders_waiting_for_my_approval"]] == ["PO-BLR-2627-00001"]
    assert director["requests_waiting_for_my_approval"] == [] and director["orders_waiting_for_my_approval"] == []
    assert "quotation_requests_with_quotes_to_award" not in stores  # an approver is not a buyer
    assert "quotation_requests_with_quotes_to_award" in pending(logins["buyer"])


# ---- the answer must be grounded in the tools --------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "facts", "bad"),
    [
        ("The order total is 2331.09.", '{"total": "2331.09"}', []),
        ("The total is ₹ 2,331.09", '{"total": "2331.09"}', []),
        ("Cost is 1,25,000 rupees", '{"amount": "125000.00"}', []),  # Indian digit grouping
        ("Worth 5 lakh", '{"amount": "500000"}', []),
        ("There are 3 orders", "{}", []),  # a small count is fine
        ("In 2026 we ordered twice", "{}", []),  # a year is fine
        ("Due on 2026-11-13", "{}", []),  # a date is fine
        ("The total is 4500", '{"a": "2331.09", "b": "2169.00"}', [D(4500)]),  # added up by the model: not trusted
        ("About 90% done", "{}", [D(90)]),
        ("It is 120 and 85", '{"x": "120"}', [D(85)]),
    ],
)
def test_a_number_that_no_tool_returned_is_not_trusted(answer: str, facts: str, bad: list[Decimal]) -> None:
    assert assistant.ungrounded_numbers(answer, facts) == bad


def test_a_question_is_answered_from_a_tool_and_the_answer_is_kept_when_grounded(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _ordered(shop, outbox)
    gw, provider = gateway(
        decision(tool="get_order", args={"no": "PO-BLR-2627-00001"}),
        decision(final="PO-BLR-2627-00001 is for Alpha Traders and totals 2331.09, with IGST of 355.59."),
    )

    got = assistant.ask(shop, shop.info["slug"], 1, "How much is PO-BLR-2627-00001?", gw)

    assert got.grounded is True and got.tools_used == ["get_order"]
    assert got.text == "PO-BLR-2627-00001 is for Alpha Traders and totals 2331.09, with IGST of 355.59."
    assert len(provider.calls) == 2  # one turn to pick the tool, one to answer


def test_an_answer_with_a_figure_the_model_made_up_is_replaced_by_the_plain_facts(
    shop: Session, outbox: Outbox
) -> None:  # noqa: F811
    _ordered(shop, outbox)
    gw, _ = gateway(
        decision(tool="get_order", args={"no": "PO-BLR-2627-00001"}),
        decision(final="The order totals 2500.00 including tax."),  # the real total is 2331.09
    )

    got = assistant.ask(shop, shop.info["slug"], 1, "How much is PO-BLR-2627-00001?", gw)

    assert got.grounded is False
    assert got.text.startswith(assistant.FALLBACK_INTRO) and "total: 2331.09" in got.text and "2500" not in got.text
    assert "{" not in got.text  # plain lines a person can read, not JSON
    (event,) = shop.execute(text("select after from audit_event where action = 'assistant.ungrounded'")).scalars().all()
    assert event["numbers"] == ["2500.00"] or event["numbers"] == ["2500"]


def test_a_total_the_model_adds_up_itself_is_not_passed_on(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _ordered(shop, outbox)
    gw, _ = gateway(
        decision(tool="list_orders", args={}),
        decision(final="Together your orders come to 9999.50."),
    )

    got = assistant.ask(shop, shop.info["slug"], 1, "What do my orders come to in total?", gw)

    assert got.grounded is False and "9999.50" not in got.text.replace(assistant.FALLBACK_INTRO, "")


def test_when_the_model_cannot_be_reached_the_person_gets_a_plain_message(shop: Session) -> None:  # noqa: F811
    no_models = Gateway({}, {"agent": [("none", "m")], "default": [("none", "m")]}, sleep=lambda s: None)

    got = assistant.ask(shop, shop.info["slug"], 1, "hello", no_models)

    assert got.text == assistant.UNAVAILABLE


def test_a_model_that_asks_for_a_tool_that_does_not_exist_gets_nowhere_but_does_no_harm(shop: Session) -> None:  # noqa: F811
    gw, _ = gateway(
        decision(tool="delete_everything", args={}),
        decision(final="I cannot do that."),
    )

    got = assistant.ask(shop, shop.info["slug"], 1, "delete all the orders", gw)

    assert got.tools_used == [] and got.text == "I cannot do that."
    assert shop.execute(text("select count(*) from request")).scalar_one() == 0


# ---- the chat endpoint ------------------------------------------------------------------------


def test_the_chat_endpoint_streams_the_assistants_answer(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    from p2p.platform import ui_api, users

    slug = shop.info["slug"]
    user = users.create_user(slug, f"chat{shop.info['sfx']}@x.example", "correct horse battery", "Chatter", "Buyer")
    _ordered(shop, outbox)
    gw, _ = gateway(
        decision(tool="list_orders", args={"status": "pending"}),
        decision(final="One order is waiting for approval."),
    )
    shop.commit()  # the model's first run builds its checkpoint tables, which waits for every open transaction to end
    ui_api.use_gateway(gw)
    try:
        r = client.post(
            "/api/chat", json={"message": "what orders are pending?"}, headers=_headers(slug, user, "Buyer")
        )
    finally:
        ui_api.use_gateway(None)

    events = [json.loads(line[5:]) for line in r.text.splitlines() if line.startswith("data:")]
    assert [e["type"] for e in events] == ["session", "token", "suggestions", "done"]
    assert events[1]["content"] == "One order is waiting for approval."
    assert "What is waiting for me?" in events[2]["suggestions"]
