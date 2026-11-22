"""Model prices and the monthly AI budget (task 11a.6, DF-32): costs in Decimal from the price rows, the cap checked
before the call, a model with no price refused while a cap is set, one warning a month."""

import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.ai_steps import costs
from p2p.ai_steps.adapters import BadRequest, RawResponse
from p2p.ai_steps.gateway import Gateway
from p2p.platform import routing, tenants, users

PAID = costs.Price("scripted", "paid-1", date(2026, 1, 1), Decimal("1.00"), Decimal("2.00"))
DEARER = costs.Price("scripted", "paid-1", date(2027, 6, 1), Decimal("3.00"), Decimal("4.00"))
MSG = [{"role": "user", "content": "hello"}]


class Model:
    def __init__(self) -> None:
        self.calls = 0

    def chat(self, model: str, messages: Any, **kw: Any) -> RawResponse:
        self.calls += 1
        return RawResponse("ok", 1000, 500, "stop")


@pytest.fixture
def db(control_db: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Session]:
    monkeypatch.setattr(costs, "EXTRA", [PAID, DEARER])
    slug = "t" + uuid.uuid4().hex[:8]
    tenants.create_tenant(slug, load_reference=False)
    with Session(routing.engine_for(slug)) as session:
        session.info["slug"] = slug
        yield session
    routing.clear_engine_cache()


def _cap(db: Session, value: str) -> None:
    db.execute(
        text("insert into org_setting (key, value) values (:k, :v) on conflict (key) do update set value = :v"),
        {"k": costs.CAP_KEY, "v": value},
    )
    db.commit()


def test_the_price_in_force_on_the_day_is_used() -> None:
    costs.EXTRA[:] = [PAID, DEARER]
    try:
        assert costs.price("scripted", "paid-1", date(2027, 5, 31)) == PAID
        assert costs.price("scripted", "paid-1", date(2027, 6, 1)) == DEARER
        assert costs.price("scripted", "paid-1", date(2025, 12, 31)) is None
        assert costs.price("groq", "openai/gpt-oss-20b", date(2026, 11, 23)) is not None  # the free tier from the file
    finally:
        costs.EXTRA.clear()


@pytest.mark.parametrize(
    ("tokens_in", "tokens_out", "expected"),
    [
        (0, 0, "0.000000"),
        (1, 0, "0.000001"),
        (0, 1, "0.000002"),
        (1000, 500, "0.002000"),
        (1_000_000, 1_000_000, "3.000000"),
    ],
)
def test_a_call_costs_its_tokens_at_the_price_in_decimal(tokens_in: int, tokens_out: int, expected: str) -> None:
    got = costs.cost(PAID, tokens_in, tokens_out)
    assert isinstance(got, Decimal) and got == Decimal(expected) and str(got) == expected


def test_calls_stop_before_the_budget_is_passed_and_no_model_is_asked(db: Session) -> None:
    model = Model()
    gw = Gateway({"scripted": model}, {"default": [("scripted", "paid-1")]})
    _cap(db, "0.005")
    gw.complete("default", MSG, db=db, use_cache=False, max_tokens=500)
    gw.complete("default", MSG, db=db, use_cache=False, max_tokens=500)
    assert costs.month_spend(db) == Decimal("0.004000") and model.calls == 2

    with pytest.raises(costs.CostCapReached):
        gw.complete("default", MSG, db=db, use_cache=False, max_tokens=500)
    assert model.calls == 2
    last = db.execute(text("select outcome, error from model_call order by id desc limit 1")).one()
    assert last[0] == "error" and "budget" in last[1]
    assert issubclass(costs.CostCapReached, BadRequest)


def test_the_budget_edges_spend_equal_refused_and_just_below_allowed(db: Session) -> None:
    db.execute(
        text(
            "insert into model_call (step, provider, model, cost_usd, outcome) values ('x', 'scripted', 'paid-1', 1, 'ok')"
        )
    )
    _cap(db, "1")
    with pytest.raises(costs.CostCapReached):
        costs.check_cap(db, "scripted", "paid-1", 0)  # spent equals the cap
    _cap(db, "1.000001")
    costs.check_cap(db, "scripted", "paid-1", 0)  # a millionth of a dollar left, a call that cannot cost anything
    with pytest.raises(costs.CostCapReached):
        costs.check_cap(db, "scripted", "paid-1", 1)  # its worst case (one token at the output price) does not fit


def test_a_model_with_no_price_is_refused_only_while_a_budget_is_set(db: Session) -> None:
    costs.check_cap(db, "scripted", "unknown", 100)
    _cap(db, "10")
    with pytest.raises(costs.CostCapReached, match="no price"):
        costs.check_cap(db, "scripted", "unknown", 100)


def test_the_administrators_are_warned_once_a_month(db: Session) -> None:
    slug = db.info["slug"]
    admin = users.create_user(slug, f"a{uuid.uuid4().hex[:6]}@example.com", "correct horse battery", "Ada", "Admin")
    _cap(db, "0.0025")
    gw = Gateway({"scripted": Model()}, {"default": [("scripted", "paid-1")]})
    gw.complete("default", MSG, db=db, use_cache=False, max_tokens=1)  # 0.002 of 0.0025: 80 %

    assert db.execute(text("select count(*) from notification where kind = 'ai_cost'")).scalar() == 1
    assert costs.warn_if_passed(db) is False
    assert db.execute(text("select user_id from notification where kind = 'ai_cost'")).scalar() == admin
