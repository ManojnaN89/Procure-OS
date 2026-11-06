from typing import Any

import pytest

from p2p.ai_steps.adapters import RateLimited, RawResponse
from p2p.ai_steps.gateway import Gateway
from p2p.ai_steps.limits import Limits, QuotaExhausted, RateLimiter, _seconds


class FakeTime:
    """A clock the test moves by hand; sleeping just moves it forward."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def limiter(t: FakeTime, **limits: Any) -> RateLimiter:
    return RateLimiter(Limits(**limits), clock=t.clock, sleep=t.sleep, max_wait=120)


def test_requests_per_minute_are_spaced_out_instead_of_hitting_the_limit() -> None:
    t = FakeTime()
    lim = limiter(t, rpm=10)  # 90% of 10 = 9 calls a minute
    for _ in range(9):
        assert lim.acquire(100) == 0
    waited = lim.acquire(100)  # the tenth call has to wait for the first to leave the window

    assert 59 <= waited <= 61 and t.now - 1000 >= 60


def test_tokens_per_minute_count_input_and_output_together() -> None:
    t = FakeTime()
    lim = limiter(t, tpm=8000)  # 7,200 usable
    assert lim.acquire(3000) == 0 and lim.acquire(3000) == 0
    waited = lim.acquire(3000)  # 9,000 would be over: wait for the first call to leave the window

    assert waited >= 59


def test_a_single_call_bigger_than_the_whole_minute_is_refused_so_another_model_is_used() -> None:
    with pytest.raises(QuotaExhausted, match="needs about 9000 tokens"):
        limiter(FakeTime(), tpm=8000).acquire(9000)


def test_the_daily_quota_is_never_waited_out() -> None:
    t = FakeTime()
    lim = limiter(t, rpd=100)  # 90 usable
    for _ in range(90):
        lim.acquire(10)
    with pytest.raises(QuotaExhausted, match="rate limit"):
        lim.acquire(10)
    t.now += 86_401  # a day later the quota is back
    assert lim.acquire(10) == 0


def test_a_long_wait_is_refused_instead_of_blocking_the_caller() -> None:
    t = FakeTime()
    lim = RateLimiter(Limits(tpm=1000), clock=t.clock, sleep=t.sleep, max_wait=10)
    lim.acquire(800)
    with pytest.raises(QuotaExhausted):
        lim.acquire(800)  # would need about 60 seconds, more than max_wait


def test_the_real_token_count_replaces_the_estimate() -> None:
    t = FakeTime()
    lim = limiter(t, tpm=8000)
    lim.acquire(7000)  # estimated high (it reserves the maximum answer length)
    lim.settle(7000, 500)  # the model used only 500
    assert lim.acquire(6000) == 0


def test_a_429_stops_every_caller_for_the_time_the_provider_asked() -> None:
    t = FakeTime()
    lim = limiter(t, rpm=100)
    lim.penalise(20)
    assert lim.acquire(10) == pytest.approx(20, abs=1)


def test_the_providers_own_numbers_in_the_headers_are_trusted() -> None:
    t = FakeTime()
    lim = limiter(t, tpm=8000)
    lim.observe(
        {"x-ratelimit-remaining-tokens": "100", "x-ratelimit-reset-tokens": "7.5s"}
    )  # nearly empty, resets in 7.5 s
    assert lim.acquire(10) == pytest.approx(7.5, abs=1)

    lim2 = limiter(FakeTime(), tpm=8000)
    lim2.observe({"x-ratelimit-remaining-tokens": "7000", "x-ratelimit-reset-tokens": "7.5s"})  # plenty left
    assert lim2.acquire(10) == 0


@pytest.mark.parametrize(
    ("text", "seconds"), [("952ms", 0.952), ("7.66s", 7.66), ("1m26.4s", 86.4), ("2h3m", 7380), ("", 1.0), ("45", 45.0)]
)
def test_reset_times_in_headers_are_read(text: str, seconds: float) -> None:
    assert _seconds(text) == pytest.approx(seconds)


# ---- inside the gateway ----------------------------------------------------------------------


class Counting:
    def __init__(self, headers: dict[str, str] | None = None, tokens: tuple[int, int] = (100, 20)) -> None:
        self.calls: list[str] = []
        self.headers = headers or {}
        self.tokens = tokens

    def chat(
        self, model: str, messages: Any, *, temperature: float, max_tokens: int, json_output: bool, **kw: Any
    ) -> RawResponse:
        self.calls.append(model)
        return RawResponse("ok", *self.tokens, "stop", headers=self.headers)


MSG = [{"role": "user", "content": "hello"}]


def test_the_gateway_waits_for_room_instead_of_sending_too_fast() -> None:
    t = FakeTime()
    provider = Counting()
    gw = Gateway(
        {"p": provider},
        {"default": [("p", "m")]},
        limits={"p": Limits(rpm=10)},
        sleep=t.sleep,
        clock=t.clock,
        max_wait=120,
    )
    for i in range(12):
        gw.complete("default", [{"role": "user", "content": f"q{i}"}])

    assert len(provider.calls) == 12 and t.now - 1000 >= 60  # the 10th call had to wait a minute


def test_a_call_that_can_never_fit_goes_to_the_next_model_without_being_sent() -> None:
    small, big = Counting(), Counting()
    gw = Gateway(
        {"small": small, "big": big},
        {"default": [("small", "m"), ("big", "m")]},
        limits={"small": Limits(tpm=500)},
        sleep=lambda s: None,
    )
    out = gw.complete("default", [{"role": "user", "content": "x" * 3000}], max_tokens=400)  # about 1,400 tokens

    assert out.provider == "big" and small.calls == []


def test_models_of_one_provider_share_a_limit_when_the_limit_is_for_the_provider() -> None:
    t = FakeTime()
    provider = Counting()
    gw = Gateway(
        {"nv": provider},
        {"default": [("nv", "m1")], "other": [("nv", "m2")]},
        limits={"nv": Limits(rpm=10)},
        sleep=t.sleep,
        clock=t.clock,
        max_wait=120,
    )
    for i in range(5):
        gw.complete("default", [{"role": "user", "content": f"a{i}"}])
        gw.complete("other", [{"role": "user", "content": f"b{i}"}])  # ten calls over two models: the 10th waits

    assert t.now - 1000 >= 60


def test_models_with_their_own_limit_do_not_slow_each_other() -> None:
    t = FakeTime()
    provider = Counting()
    limits = {"g/m1": Limits(rpm=10), "g/m2": Limits(rpm=10)}
    gw = Gateway(
        {"g": provider}, {"default": [("g", "m1")], "other": [("g", "m2")]}, limits=limits, sleep=t.sleep, clock=t.clock
    )
    for i in range(8):
        gw.complete("default", [{"role": "user", "content": f"a{i}"}])
        gw.complete("other", [{"role": "user", "content": f"b{i}"}])  # 8 each: within 9 a minute per model

    assert t.slept == []


def test_a_429_makes_the_next_call_wait_and_is_then_retried() -> None:
    t = FakeTime()
    answers: list[Any] = [RateLimited("slow down", 12.0), RawResponse("fine", 10, 5, "stop")]

    class Flaky:
        def chat(self, model: str, messages: Any, **kw: Any) -> RawResponse:
            a = answers.pop(0)
            if isinstance(a, Exception):
                raise a
            return a  # type: ignore[no-any-return]

    gw = Gateway(
        {"p": Flaky()},
        {"default": [("p", "m")]},
        limits={"p": Limits(rpm=100)},
        sleep=t.sleep,
        clock=t.clock,
        max_wait=120,
    )
    assert gw.complete("default", MSG).text == "fine"
    assert t.slept and 11 <= sum(t.slept) <= 14


def test_when_the_provider_says_the_minute_is_nearly_used_up_the_next_call_waits() -> None:
    t = FakeTime()
    provider = Counting({"x-ratelimit-remaining-tokens": "50", "x-ratelimit-reset-tokens": "5s"})
    gw = Gateway(
        {"p": provider}, {"default": [("p", "m")]}, limits={"p": Limits(tpm=8000)}, sleep=t.sleep, clock=t.clock
    )
    gw.complete("default", [{"role": "user", "content": "one"}])
    gw.complete("default", [{"role": "user", "content": "two"}])

    assert any(4.5 <= s <= 6 for s in t.slept)


def test_a_patient_call_waits_where_the_default_would_give_up() -> None:
    t = FakeTime()
    provider = Counting(tokens=(600, 100))  # each call really uses 700 of the 900 usable tokens a minute
    limits = {"p": Limits(tpm=1000)}
    default = Gateway(
        {"p": provider}, {"default": [("p", "m")]}, limits=limits, sleep=t.sleep, clock=t.clock, max_wait=10
    )
    default.complete("default", [{"role": "user", "content": "a" * 600}], max_tokens=100)  # about 300 tokens
    with pytest.raises(QuotaExhausted):
        default.complete("default", [{"role": "user", "content": "b" * 1200}], max_tokens=300)  # would need a minute

    patient = Gateway(
        {"p": provider}, {"default": [("p", "m")]}, limits=limits, sleep=t.sleep, clock=t.clock, max_wait=10
    )
    patient.complete("default", [{"role": "user", "content": "c" * 600}], max_tokens=100)
    out = patient.complete("default", [{"role": "user", "content": "d" * 1200}], max_tokens=300, max_wait=120)
    assert out.text == "ok" and t.now - 1000 >= 50
