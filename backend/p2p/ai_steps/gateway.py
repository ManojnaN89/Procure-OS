"""Model gateway: one interface, complete() and extract(), over several providers.

The model for each AI step comes from configuration, never from the step's code. A call is retried with backoff on
rate limits and provider errors, then sent to the next model in the step's route list. Every call is logged with
tokens, cost, time and outcome; identical prompts at temperature 0 are answered from a cache. A refusal is final.
Before anything else, every message is masked (ai_steps/masking.py): bank accounts, IFSC codes, PANs and Aadhaar numbers
never reach a provider, and the call's log row counts what was masked (never the values).
"""

import hashlib
import json
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents import trace
from p2p.ai_steps import costs
from p2p.ai_steps.adapters import (
    Adapter,
    AnthropicAdapter,
    BadRequest,
    InvalidOutput,
    Messages,
    ModelError,
    ModelRefusal,
    OpenAICompatAdapter,
    RateLimited,
    RawResponse,
    ServerError,
)
from p2p.ai_steps.limits import Limits, QuotaExhausted, RateLimiter, estimate_tokens
from p2p.ai_steps.masking import mask_messages
from p2p.config import get_settings

OPENAI_COMPATIBLE = {
    "groq": ("https://api.groq.com/openai/v1", "groq_api_key"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini_api_key"),
    "openrouter": ("https://openrouter.ai/api/v1", "openrouter_api_key"),
    "nvidia": ("https://integrate.api.nvidia.com/v1", "nvidia_api_key"),  # free trial: development and testing only
    "ollama": ("http://localhost:11434/v1", ""),
}

# Model names change often. These are the defaults chosen from the evaluation in docs/model-selection.md (6 Nov 2026).
# A step's own route is looked up by its name; "<name>.strong" is where a failed check is retried (see ai_steps/runner.py).
# Override any of it with MODEL_ROUTES in .env. The optional third item holds provider settings.
_LOW = {
    "reasoning_effort": "low"
}  # gpt-oss on Groq: short thinking keeps answers fast and inside the minute's token limit
_MEDIUM = {"reasoning_effort": "medium"}
_G120 = ("groq", "openai/gpt-oss-120b")
_G20 = ("groq", "openai/gpt-oss-20b")
_LAGUNA = ("nvidia", "poolside/laguna-xs-2.1")  # NVIDIA: development and testing only
_ULTRA = ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b")  # slowest and strongest: second opinion

DEFAULT_ROUTES: dict[str, list[tuple[Any, ...]]] = {
    # S01 to S05: reading and classifying. Small outputs, so the fast small model first.
    "quote_reading": [(*_G20, _LOW), (*_G120, _LOW), _LAGUNA],
    "quote_reading.strong": [(*_G120, _MEDIUM), _ULTRA],
    "invoice_reading": [(*_G120, _LOW), (*_G20, _LOW), _LAGUNA],
    "invoice_reading.strong": [(*_G120, _MEDIUM), _ULTRA],
    "reply_classification": [(*_G20, _LOW), _LAGUNA, (*_G120, _LOW)],
    "reply_classification.strong": [(*_G120, _MEDIUM), _ULTRA],
    "item_mapping": [(*_G20, _LOW), _LAGUNA, (*_G120, _LOW)],
    "request_reading": [(*_G20, _LOW), (*_G120, _LOW), _LAGUNA],
    "request_reading.strong": [(*_G120, _MEDIUM), _ULTRA],
    "item_mapping.strong": [(*_G120, _MEDIUM), _ULTRA],
    "supplier_message": [(*_G20, _LOW), (*_G120, _LOW), _LAGUNA],
    "supplier_message.strong": [(*_G120, _MEDIUM), _ULTRA],
    "forward_buy_note": [(*_G20, _LOW), (*_G120, _LOW), _LAGUNA],
    "certificate_reading": [(*_G20, _LOW), (*_G120, _LOW), _LAGUNA],
    "certificate_reading.strong": [(*_G120, _MEDIUM), _ULTRA],
    # AG01, AG02, AG04: tool choice each turn.
    "agent": [(*_G120, _LOW), _LAGUNA, (*_G20, _LOW)],
    # AG03: the invoice-exception investigator gets the most thinking.
    "agent_invoice_exceptions": [(*_G120, _MEDIUM), _ULTRA, _LAGUNA],
    "default": [(*_G120, _LOW), _LAGUNA],
}

# What each free tier allows, measured on this key on 6 Nov 2026 (see docs/model-selection.md). Override with MODEL_LIMITS.
DEFAULT_LIMITS: dict[str, Limits] = {
    # Groq limits each model separately (checked from the response headers of this key)
    "groq/openai/gpt-oss-120b": Limits(rpm=30, tpm=8000, rpd=1000),
    "groq/openai/gpt-oss-20b": Limits(rpm=30, tpm=8000, rpd=1000),
    "groq/qwen/qwen3.8-27b": Limits(rpm=30, tpm=8000, rpd=1000),
    "groq/allam-2-7b": Limits(rpm=30, tpm=6000, rpd=7000),
    # NVIDIA publishes nothing and sends no headers; forum reports say 30 to 40 a minute for the whole key,
    # so every NVIDIA model shares one bucket.
    "nvidia": Limits(rpm=30),
}


def _traced() -> dict[str, Any]:
    """What the call belongs to (agents/trace.py): the run, capability, person, workflow and document."""
    t = trace.current()
    return {
        "run": t.run_id, "cap": t.capability, "uid": t.user_id, "wf": t.workflow_id, "ent": t.entity,
        "eid": t.entity_id,
    }  # fmt: skip


@dataclass(frozen=True)
class Completion:
    text: str
    provider: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    ms: int
    attempts: int
    cached: bool = False


@dataclass(frozen=True)
class Extraction[T: BaseModel]:
    value: T
    completion: Completion


def build_adapters() -> dict[str, Adapter]:
    s = get_settings()
    adapters: dict[str, Adapter] = {}
    for name, (url, key_attr) in OPENAI_COMPATIBLE.items():
        key = getattr(s, key_attr) if key_attr else "ollama"
        if key:
            adapters[name] = OpenAICompatAdapter(url, key)
    if s.anthropic_api_key:
        adapters["anthropic"] = AnthropicAdapter(s.anthropic_api_key)
    return adapters


def load_routes() -> dict[str, list[tuple[Any, ...]]]:
    raw = get_settings().model_routes
    if not raw:
        return DEFAULT_ROUTES
    return {step: [tuple(entry) for entry in routes] for step, routes in json.loads(raw).items()}


def load_limits() -> dict[str, Limits]:
    """Limits per 'provider' or 'provider/model'; the most specific entry wins."""
    raw = get_settings().model_limits
    limits = dict(DEFAULT_LIMITS)
    if raw:
        limits.update({key: Limits(**vals) for key, vals in json.loads(raw).items()})
    return limits


class Gateway:
    def __init__(
        self,
        adapters: dict[str, Adapter] | None = None,
        routes: dict[str, list[tuple[Any, ...]]] | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
        limits: dict[str, Limits] | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_wait: float = 45.0,
    ) -> None:
        self.adapters = adapters if adapters is not None else build_adapters()
        self.routes = routes if routes is not None else load_routes()
        self.sleep = sleep
        self.max_retries = max_retries
        self.limits = limits if limits is not None else load_limits()
        self.clock = clock
        self.max_wait = max_wait  # longest the gateway waits for room under a limit before using another model
        self._limiters: dict[str, RateLimiter] = {}

    def _limiter(self, provider: str, model: str) -> RateLimiter | None:
        """A limit named for the exact model is that model's own bucket; a provider-wide limit is shared by its models."""
        exact = f"{provider}/{model}"
        key = exact if exact in self.limits else provider
        if key not in self.limits:
            return None
        if key not in self._limiters:
            self._limiters[key] = RateLimiter(
                self.limits[key], clock=self.clock, sleep=self.sleep, max_wait=self.max_wait
            )
        return self._limiters[key]

    # ---- public interface --------------------------------------------------------------------

    def complete(
        self,
        step: str,
        messages: Messages,
        *,
        db: Session | None = None,
        prompt_version: str = "",
        temperature: float = 0.0,
        max_tokens: int = 1024,
        json_output: bool = False,
        use_cache: bool = True,
        max_wait: float | None = None,
    ) -> Completion:
        messages, masked = mask_messages(messages)  # the one place every prompt passes: nothing sensitive goes out
        route = self.routes.get(step) or self.routes["default"]
        started = time.monotonic()
        cache_key = self._hash(route, messages, temperature, max_tokens, json_output)
        if db is not None and use_cache and temperature == 0:
            hit = db.execute(
                text("select response from model_cache where request_hash = :h"), {"h": cache_key}
            ).scalar()
            if hit:
                done = Completion(**{**hit, "cost_usd": Decimal(0), "cached": True, "ms": 0})
                self._log(db, step, prompt_version, done, "cache_hit", None, cache_key, cost=Decimal(0), masked=masked)
                return done
        last_error: ModelError | None = None
        attempts = 0
        capped: tuple[str | None, str | None] = (None, None)  # the model the budget refused, for the log row
        for entry in route:
            provider, model = entry[0], entry[1]
            extra = entry[2] if len(entry) > 2 else None
            adapter = self.adapters.get(provider)
            if adapter is None:
                last_error = BadRequest(f"provider '{provider}' is not configured (no API key)")
                continue
            limiter = self._limiter(provider, model)
            estimate = estimate_tokens(" ".join(m["content"] for m in messages)) + max_tokens
            if db is not None:
                try:
                    costs.check_cap(db, provider, model, estimate)  # before the call: the month's budget (DF-32)
                except costs.CostCapReached as exc:
                    last_error, capped = exc, (provider, model)
                    continue  # the next model of the route may be free
            for attempt in range(self.max_retries + 1):
                attempts += 1
                if limiter is not None:
                    try:
                        # waits for room under the provider's limits, or gives up on this model
                        limiter.acquire(estimate, max_wait)
                    except QuotaExhausted as exc:
                        last_error = exc
                        break
                kwargs: dict[str, Any] = {"extra": extra} if extra else {}
                try:
                    raw = adapter.chat(
                        model,
                        messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        json_output=json_output,
                        **kwargs,
                    )
                except RateLimited as exc:
                    last_error = exc
                    pause = exc.retry_after if exc.retry_after is not None else self._backoff(attempt)
                    if limiter is not None:
                        limiter.penalise(pause)
                    else:
                        self.sleep(pause)
                    continue
                except ServerError as exc:
                    last_error = exc
                    self.sleep(self._backoff(attempt))
                    continue
                except BadRequest as exc:
                    last_error = exc
                    break  # try the next model; retrying this one will not help
                if raw.refusal or raw.finish_reason == "content_filter":
                    self._fail(
                        db,
                        step,
                        prompt_version,
                        provider,
                        model,
                        attempts,
                        started,
                        "refusal",
                        "model refused",
                        cache_key,
                        masked,
                    )
                    raise ModelRefusal(f"{provider}/{model} declined to answer")
                if limiter is not None:
                    limiter.observe(raw.headers)
                    if raw.tokens_in + raw.tokens_out:
                        limiter.settle(estimate, raw.tokens_in + raw.tokens_out)
                return self._success(
                    db,
                    step,
                    prompt_version,
                    provider,
                    model,
                    raw,
                    attempts,
                    started,
                    cache_key,
                    temperature,
                    use_cache,
                    masked,
                )
        self._fail(db, step, prompt_version, *capped, attempts, started, "error", str(last_error), cache_key, masked)
        raise last_error or ModelError("no model configured for this step")

    def extract[T: BaseModel](
        self,
        step: str,
        messages: Messages,
        schema: type[T],
        *,
        db: Session | None = None,
        prompt_version: str = "",
        max_tokens: int = 2048,
        max_wait: float | None = None,
    ) -> Extraction[T]:
        """Ask for JSON matching a Pydantic model. One repair attempt if the answer is not valid."""
        instruction = "Reply with a single JSON object that matches this JSON schema and nothing else:\n" + json.dumps(
            schema.model_json_schema()
        )
        convo: Messages = [{"role": "system", "content": instruction}, *messages]
        completion = self.complete(
            step,
            convo,
            db=db,
            prompt_version=prompt_version,
            max_tokens=max_tokens,
            json_output=True,
            max_wait=max_wait,
        )
        try:
            return Extraction(self._parse(completion.text, schema), completion)
        except (ValueError, ValidationError) as first:
            repair = [
                *convo,
                {"role": "assistant", "content": completion.text},
                {
                    "role": "user",
                    "content": f"That was not valid ({str(first)[:300]}). Reply again with only the JSON object.",
                },
            ]
            second = self.complete(
                step,
                repair,
                db=db,
                prompt_version=prompt_version,
                max_tokens=max_tokens,
                json_output=True,
                use_cache=False,
                max_wait=max_wait,
            )
            try:
                return Extraction(self._parse(second.text, schema), second)
            except (ValueError, ValidationError) as exc:
                if db is not None:
                    self._log(db, step, prompt_version, second, "invalid", str(exc)[:300], None)
                raise InvalidOutput(f"model answer does not match the schema: {str(exc)[:200]}") from None

    # ---- internals ---------------------------------------------------------------------------

    @staticmethod
    def _parse[T: BaseModel](raw: str, schema: type[T]) -> T:
        raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL)  # some reasoning models show their thinking
        body = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        return schema.model_validate_json(body)

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(30.0, 2.0**attempt) + random.uniform(0, 0.5)  # noqa: S311 - jitter, not security

    @staticmethod
    def _hash(
        route: list[tuple[Any, ...]], messages: Messages, temperature: float, max_tokens: int, json_output: bool
    ) -> str:
        blob = json.dumps([route, messages, temperature, max_tokens, json_output], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _success(
        self,
        db: Session | None,
        step: str,
        prompt_version: str,
        provider: str,
        model: str,
        raw: RawResponse,
        attempts: int,
        started: float,
        cache_key: str,
        temperature: float,
        use_cache: bool,
        masked: dict[str, int],
    ) -> Completion:
        cost = costs.cost(costs.price(provider, model, datetime.now(UTC).date()), raw.tokens_in, raw.tokens_out)
        done = Completion(
            raw.text,
            provider,
            model,
            raw.tokens_in,
            raw.tokens_out,
            cost,
            int((time.monotonic() - started) * 1000),
            attempts,
        )
        if db is not None:
            self._log(db, step, prompt_version, done, "ok", None, cache_key, masked=masked)
            if cost > 0:
                costs.warn_if_passed(db)
            if use_cache and temperature == 0:
                payload = {**done.__dict__, "cost_usd": str(done.cost_usd)}
                payload.pop("cached", None)
                db.execute(
                    text(
                        "insert into model_cache (request_hash, response) values (:h, cast(:r as jsonb)) on conflict do nothing"
                    ),
                    {"h": cache_key, "r": json.dumps(payload)},
                )
                db.commit()
        return done

    def _fail(
        self,
        db: Session | None,
        step: str,
        prompt_version: str,
        provider: str | None,
        model: str | None,
        attempts: int,
        started: float,
        outcome: str,
        error: str,
        cache_key: str,
        masked: dict[str, int],
    ) -> None:
        if db is None:
            return
        db.execute(
            text(
                "insert into model_call (step, provider, model, prompt_version, attempts, ms, outcome, error, request_hash, "
                "masked, run_id, capability, user_id, workflow_id, entity, entity_id) values (:s, :p, :m, :pv, :a, :ms, :o, "
                ":e, :h, cast(:mk as jsonb), :run, :cap, :uid, :wf, :ent, :eid)"
            ),
            {
                **_traced(),
                "s": step,
                "p": provider,
                "m": model,
                "pv": prompt_version,
                "a": attempts,
                "ms": int((time.monotonic() - started) * 1000),
                "o": outcome,
                "e": error[:500],
                "h": cache_key,
                "mk": json.dumps(masked) if masked else None,
            },
        )
        db.commit()

    def _log(
        self,
        db: Session,
        step: str,
        prompt_version: str,
        done: Completion,
        outcome: str,
        error: str | None,
        cache_key: str | None,
        *,
        cost: Decimal | None = None,
        masked: dict[str, int] | None = None,
    ) -> None:
        db.execute(
            text(
                "insert into model_call (step, provider, model, prompt_version, tokens_in, tokens_out, cost_usd, ms, attempts, "
                "outcome, error, request_hash, masked, run_id, capability, user_id, workflow_id, entity, entity_id) values "
                "(:s, :p, :m, :pv, :ti, :to, :c, :ms, :a, :o, :e, :h, cast(:mk as jsonb), :run, :cap, :uid, :wf, :ent, :eid)"
            ),
            {
                **_traced(),
                "s": step,
                "p": done.provider,
                "m": done.model,
                "pv": prompt_version,
                "ti": done.tokens_in,
                "to": done.tokens_out,
                "c": cost if cost is not None else done.cost_usd,
                "ms": done.ms,
                "a": done.attempts,
                "o": outcome,
                "e": error,
                "h": cache_key,
                "mk": json.dumps(masked) if masked else None,
            },
        )
        db.commit()
