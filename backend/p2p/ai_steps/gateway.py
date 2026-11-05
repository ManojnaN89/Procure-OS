"""Model gateway: one interface, complete() and extract(), over several providers.

The model for each AI step comes from configuration, never from the step's code. A call is retried with backoff on
rate limits and provider errors, then sent to the next model in the step's route list. Every call is logged with
tokens, cost, time and outcome; identical prompts at temperature 0 are answered from a cache. A refusal is final.
"""

import hashlib
import json
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, ValidationError
from sqlalchemy import text
from sqlalchemy.orm import Session

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
from p2p.config import get_settings

OPENAI_COMPATIBLE = {
    "groq": ("https://api.groq.com/openai/v1", "groq_api_key"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/", "gemini_api_key"),
    "openrouter": ("https://openrouter.ai/api/v1", "openrouter_api_key"),
    "ollama": ("http://localhost:11434/v1", ""),
}

# Model names change often. Defaults only; override with MODEL_ROUTES in .env (see .env.example).
DEFAULT_ROUTES: dict[str, list[tuple[str, str]]] = {
    "default": [("groq", "openai/gpt-oss-120b"), ("gemini", "gemini-2.5-flash")],
}

# US dollars per million tokens (input, output). Free tiers cost nothing; add paid models here.
PRICE_PER_MTOK: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}


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


def load_routes() -> dict[str, list[tuple[str, str]]]:
    raw = get_settings().model_routes
    if not raw:
        return DEFAULT_ROUTES
    return {step: [(p, m) for p, m in routes] for step, routes in json.loads(raw).items()}


class Gateway:
    def __init__(
        self,
        adapters: dict[str, Adapter] | None = None,
        routes: dict[str, list[tuple[str, str]]] | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
    ) -> None:
        self.adapters = adapters if adapters is not None else build_adapters()
        self.routes = routes if routes is not None else load_routes()
        self.sleep = sleep
        self.max_retries = max_retries

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
    ) -> Completion:
        route = self.routes.get(step) or self.routes["default"]
        started = time.monotonic()
        cache_key = self._hash(route, messages, temperature, max_tokens, json_output)
        if db is not None and use_cache and temperature == 0:
            hit = db.execute(
                text("select response from model_cache where request_hash = :h"), {"h": cache_key}
            ).scalar()
            if hit:
                done = Completion(**{**hit, "cost_usd": Decimal(0), "cached": True, "ms": 0})
                self._log(db, step, prompt_version, done, "cache_hit", None, cache_key, cost=Decimal(0))
                return done
        last_error: ModelError | None = None
        attempts = 0
        for provider, model in route:
            adapter = self.adapters.get(provider)
            if adapter is None:
                last_error = BadRequest(f"provider '{provider}' is not configured (no API key)")
                continue
            for attempt in range(self.max_retries + 1):
                attempts += 1
                try:
                    raw = adapter.chat(
                        model, messages, temperature=temperature, max_tokens=max_tokens, json_output=json_output
                    )
                except RateLimited as exc:
                    last_error = exc
                    self.sleep(exc.retry_after if exc.retry_after is not None else self._backoff(attempt))
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
                    )
                    raise ModelRefusal(f"{provider}/{model} declined to answer")
                return self._success(
                    db, step, prompt_version, provider, model, raw, attempts, started, cache_key, temperature, use_cache
                )
        self._fail(db, step, prompt_version, None, None, attempts, started, "error", str(last_error), cache_key)
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
    ) -> Extraction[T]:
        """Ask for JSON matching a Pydantic model. One repair attempt if the answer is not valid."""
        instruction = "Reply with a single JSON object that matches this JSON schema and nothing else:\n" + json.dumps(
            schema.model_json_schema()
        )
        convo: Messages = [{"role": "system", "content": instruction}, *messages]
        completion = self.complete(
            step, convo, db=db, prompt_version=prompt_version, max_tokens=max_tokens, json_output=True
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
        body = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
        return schema.model_validate_json(body)

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(30.0, 2.0**attempt) + random.uniform(0, 0.5)  # noqa: S311 - jitter, not security

    @staticmethod
    def _hash(
        route: list[tuple[str, str]], messages: Messages, temperature: float, max_tokens: int, json_output: bool
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
    ) -> Completion:
        price = PRICE_PER_MTOK.get((provider, model))
        cost = (
            (Decimal(raw.tokens_in) * price[0] + Decimal(raw.tokens_out) * price[1]) / Decimal(1_000_000)
            if price
            else Decimal(0)
        )
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
            self._log(db, step, prompt_version, done, "ok", None, cache_key)
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
    ) -> None:
        if db is None:
            return
        db.execute(
            text(
                "insert into model_call (step, provider, model, prompt_version, attempts, ms, outcome, error, request_hash) "
                "values (:s, :p, :m, :pv, :a, :ms, :o, :e, :h)"
            ),
            {
                "s": step,
                "p": provider,
                "m": model,
                "pv": prompt_version,
                "a": attempts,
                "ms": int((time.monotonic() - started) * 1000),
                "o": outcome,
                "e": error[:500],
                "h": cache_key,
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
    ) -> None:
        db.execute(
            text(
                "insert into model_call (step, provider, model, prompt_version, tokens_in, tokens_out, cost_usd, ms, attempts, "
                "outcome, error, request_hash) values (:s, :p, :m, :pv, :ti, :to, :c, :ms, :a, :o, :e, :h)"
            ),
            {
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
            },
        )
        db.commit()
