import json
import uuid
from collections.abc import Callable, Iterator
from datetime import date
from decimal import Decimal
from typing import Any

import httpx2
import pytest
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.ai_steps import costs
from p2p.ai_steps.adapters import (
    AnthropicAdapter,
    BadRequest,
    InvalidOutput,
    ModelRefusal,
    OpenAICompatAdapter,
)
from p2p.ai_steps.gateway import Gateway
from p2p.platform import routing, tenants

Handler = Callable[[httpx2.Request], httpx2.Response]


class Quote(BaseModel):
    supplier: str
    amount: int


def chat_ok(content: str, tokens: tuple[int, int] = (10, 5), finish: str = "stop") -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "x",
            "object": "chat.completion",
            "created": 0,
            "model": "m",
            "choices": [{"index": 0, "finish_reason": finish, "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1], "total_tokens": sum(tokens)},
        },
    )


def adapter(handler: Handler) -> OpenAICompatAdapter:
    return OpenAICompatAdapter(
        "http://mock/v1", "key", http_client=httpx2.Client(transport=httpx2.MockTransport(handler))
    )


def gateway(adapters: dict[str, Any], sleeps: list[float] | None = None) -> Gateway:
    return Gateway(
        adapters,
        {"default": [("a", "model-a"), ("b", "model-b")], "extract": [("a", "model-a")]},
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)),
        max_retries=2,
    )


MSG = [{"role": "user", "content": "hello"}]


@pytest.fixture
def db(control_db: str) -> Iterator[Session]:
    slug = "t" + uuid.uuid4().hex[:8]
    tenants.create_tenant(slug, load_reference=False)
    with Session(routing.engine_for(slug)) as session:
        yield session
    routing.clear_engine_cache()


def calls(db: Session) -> list[tuple[Any, ...]]:
    rows = db.execute(
        text("select provider, model, outcome, tokens_in, tokens_out, attempts from model_call order by id")
    )
    return [tuple(r) for r in rows]


# ---- basic call, logging, cost ---------------------------------------------------------------


def test_a_call_returns_the_text_and_is_logged_with_tokens_and_outcome(db: Session) -> None:
    gw = gateway({"a": adapter(lambda r: chat_ok("hi there", (12, 4)))})
    out = gw.complete("default", MSG, db=db, prompt_version="p1")

    assert (out.text, out.provider, out.model, out.tokens_in, out.tokens_out) == ("hi there", "a", "model-a", 12, 4)
    assert calls(db) == [("a", "model-a", "ok", 12, 4, 1)]
    assert db.execute(text("select prompt_version, ms >= 0 from model_call")).one() == ("p1", True)


def test_cost_comes_from_the_price_table_and_is_zero_for_free_tiers(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    price = costs.Price("a", "model-a", date(2026, 1, 1), Decimal("3"), Decimal("15"))
    monkeypatch.setattr(costs, "EXTRA", [price])  # the price file of ai_steps/costs.py, with a test row
    out = gateway({"a": adapter(lambda r: chat_ok("x", (1_000_000, 100_000)))}).complete("default", MSG, db=db)
    assert out.cost_usd == Decimal("4.5")  # 1M in x 3 + 0.1M out x 15, per million
    free = gateway({"b": adapter(lambda r: chat_ok("x", (1_000_000, 1)))}, None)
    assert free.complete("default", MSG, db=db, use_cache=False).cost_usd == 0


# ---- retries, rate limits, fallback ----------------------------------------------------------


def test_a_rate_limit_is_waited_out_using_retry_after_then_succeeds() -> None:
    seen: list[int] = []

    def handler(r: httpx2.Request) -> httpx2.Response:
        seen.append(1)
        if len(seen) < 3:
            return httpx2.Response(429, headers={"retry-after": "7"}, json={"error": {"message": "slow down"}})
        return chat_ok("finally")

    sleeps: list[float] = []
    out = gateway({"a": adapter(handler)}, sleeps).complete("default", MSG)
    assert out.text == "finally" and out.attempts == 3
    assert sleeps == [7.0, 7.0]


def test_a_server_error_is_retried_with_growing_pauses() -> None:
    n: list[int] = []

    def handler(r: httpx2.Request) -> httpx2.Response:
        n.append(1)
        return httpx2.Response(503, json={"error": {"message": "down"}}) if len(n) < 3 else chat_ok("up again")

    sleeps: list[float] = []
    assert gateway({"a": adapter(handler)}, sleeps).complete("default", MSG).text == "up again"
    assert len(sleeps) == 2 and sleeps[1] > sleeps[0]


def test_when_the_first_model_keeps_failing_the_next_one_answers(db: Session) -> None:
    broken = adapter(lambda r: httpx2.Response(500, json={"error": {"message": "boom"}}))
    working = adapter(lambda r: chat_ok("from b"))
    out = gateway({"a": broken, "b": working}).complete("default", MSG, db=db)

    assert (out.provider, out.text) == ("b", "from b")
    assert out.attempts == 4  # 3 tries on a, 1 on b
    assert calls(db)[-1][:3] == ("b", "model-b", "ok")


def test_a_bad_request_is_not_retried_and_goes_straight_to_the_next_model() -> None:
    hits: list[str] = []

    def bad(r: httpx2.Request) -> httpx2.Response:
        hits.append("a")
        return httpx2.Response(401, json={"error": {"message": "bad key"}})

    out = gateway({"a": adapter(bad), "b": adapter(lambda r: chat_ok("b ok"))}).complete("default", MSG)
    assert hits == ["a"] and out.provider == "b"


def test_when_every_model_fails_the_error_is_raised_and_logged(db: Session) -> None:
    down = adapter(lambda r: httpx2.Response(401, json={"error": {"message": "bad key"}}))
    with pytest.raises(BadRequest):
        gateway({"a": down, "b": down}).complete("default", MSG, db=db)
    assert db.execute(text("select outcome, provider from model_call")).one() == ("error", None)


def test_a_provider_without_a_key_is_skipped() -> None:
    out = gateway({"b": adapter(lambda r: chat_ok("only b"))}).complete("default", MSG)
    assert out.provider == "b"


# ---- refusal ---------------------------------------------------------------------------------


def test_a_refusal_is_final_and_is_not_sent_to_another_model(db: Session) -> None:
    hits: list[str] = []

    def refusing(r: httpx2.Request) -> httpx2.Response:
        hits.append("a")
        return chat_ok("", finish="content_filter")

    with pytest.raises(ModelRefusal):
        gateway({"a": adapter(refusing), "b": adapter(lambda r: chat_ok("should not be asked"))}).complete(
            "default", MSG, db=db
        )
    assert hits == ["a"]
    assert db.execute(text("select outcome from model_call")).scalar_one() == "refusal"


# ---- cache -----------------------------------------------------------------------------------


def test_an_identical_prompt_is_answered_from_the_cache_without_calling_the_model(db: Session) -> None:
    n: list[int] = []

    def handler(r: httpx2.Request) -> httpx2.Response:
        n.append(1)
        return chat_ok("cached answer", (20, 8))

    gw = gateway({"a": adapter(handler)})
    first = gw.complete("default", MSG, db=db)
    second = gw.complete("default", MSG, db=db)

    assert len(n) == 1 and second.text == "cached answer" and second.cached and not first.cached
    assert [c[2] for c in calls(db)] == ["ok", "cache_hit"]
    assert db.execute(text("select cost_usd from model_call where outcome = 'cache_hit'")).scalar_one() == 0
    assert gw.complete("default", MSG, db=db, use_cache=False).cached is False and len(n) == 2


# ---- extract ---------------------------------------------------------------------------------


def test_extract_returns_a_validated_object(db: Session) -> None:
    gw = gateway({"a": adapter(lambda r: chat_ok(json.dumps({"supplier": "Shree Steel", "amount": 125000})))})
    got = gw.extract("extract", MSG, Quote, db=db)
    assert got.value == Quote(supplier="Shree Steel", amount=125000)


def test_extract_accepts_json_wrapped_in_a_code_fence() -> None:
    gw = gateway({"a": adapter(lambda r: chat_ok('```json\n{"supplier": "X", "amount": 5}\n```'))})
    assert gw.extract("extract", MSG, Quote).value.amount == 5


def test_extract_repairs_once_when_the_first_answer_is_not_valid() -> None:
    answers = iter(['{"supplier": "X"}', '{"supplier": "X", "amount": 9}'])
    gw = gateway({"a": adapter(lambda r: chat_ok(next(answers)))})
    assert gw.extract("extract", MSG, Quote).value == Quote(supplier="X", amount=9)


def test_extract_gives_up_after_one_repair_and_logs_it(db: Session) -> None:
    gw = gateway({"a": adapter(lambda r: chat_ok("not json at all"))})
    with pytest.raises(InvalidOutput):
        gw.extract("extract", MSG, Quote, db=db)
    assert "invalid" in [c[2] for c in calls(db)]


def test_the_request_asks_for_json_and_carries_the_schema() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(r: httpx2.Request) -> httpx2.Response:
        bodies.append(json.loads(r.content))
        return chat_ok('{"supplier": "X", "amount": 1}')

    gateway({"a": adapter(handler)}).extract("extract", MSG, Quote)
    assert bodies[0]["response_format"] == {"type": "json_object"}
    assert "amount" in bodies[0]["messages"][0]["content"] and bodies[0]["messages"][0]["role"] == "system"


# ---- second provider through the same interface ----------------------------------------------


def test_the_anthropic_adapter_works_through_the_same_gateway(db: Session) -> None:
    def handler(r: httpx2.Request) -> httpx2.Response:
        body = json.loads(r.content)
        assert body["system"].startswith("Reply with a single JSON object")  # the schema instruction moved to `system`
        return httpx2.Response(
            200,
            json={
                "id": "m",
                "type": "message",
                "role": "assistant",
                "model": "claude",
                "content": [{"type": "text", "text": '{"supplier": "Claude Co", "amount": 7}'}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 30, "output_tokens": 9},
            },
        )

    claude = AnthropicAdapter("key", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    gw = Gateway({"anthropic": claude}, {"extract": [("anthropic", "claude-sonnet-5-5")]}, sleep=lambda s: None)
    got = gw.extract("extract", MSG, Quote, db=db)

    assert got.value == Quote(supplier="Claude Co", amount=7)
    assert calls(db) == [("anthropic", "claude-sonnet-5-5", "ok", 30, 9, 1)]


def test_the_anthropic_adapter_reports_a_refusal() -> None:
    def handler(r: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "id": "m",
                "type": "message",
                "role": "assistant",
                "model": "claude",
                "content": [],
                "stop_reason": "refusal",
                "stop_sequence": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        )

    claude = AnthropicAdapter("key", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    with pytest.raises(ModelRefusal):
        Gateway({"anthropic": claude}, {"default": [("anthropic", "claude-sonnet-5-5")]}).complete("default", MSG)
