"""Provider adapters. Each turns one chat request into a RawResponse and our own error types.

OpenAI-compatible adapter: Groq, Gemini, OpenRouter, local Ollama. Anthropic adapter: the Claude API.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic
import httpx2
import openai


class ModelError(Exception):
    """Base class for everything the gateway raises."""


class RateLimited(ModelError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ServerError(ModelError):
    """The provider failed or could not be reached; worth retrying."""


class BadRequest(ModelError):
    """The provider refused the request itself (bad key, bad model, too long). Retrying will not help."""


class ModelRefusal(ModelError):
    """The model declined to answer. Not retried and not sent to another model."""


class InvalidOutput(ModelError):
    """The model answered but not in the shape asked for."""


@dataclass(frozen=True)
class RawResponse:
    text: str
    tokens_in: int
    tokens_out: int
    finish_reason: str | None = None
    refusal: str | None = None
    headers: dict[str, str] = field(default_factory=dict)  # response headers, lower case (rate-limit numbers)


Messages = list[dict[str, str]]


class Adapter(Protocol):
    def chat(
        self, model: str, messages: Messages, *, temperature: float, max_tokens: int, json_output: bool
    ) -> RawResponse:
        """Providers that accept provider-specific settings also take an optional `extra` dict."""
        ...


def _retry_after(headers: Any) -> float | None:
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


class OpenAICompatAdapter:
    def __init__(
        self, base_url: str, api_key: str, http_client: httpx2.Client | None = None, timeout: float = 120.0
    ) -> None:
        self.client = openai.OpenAI(
            base_url=base_url, api_key=api_key or "none", max_retries=0, http_client=http_client, timeout=timeout
        )

    def chat(
        self,
        model: str,
        messages: Messages,
        *,
        temperature: float,
        max_tokens: int,
        json_output: bool,
        extra: dict[str, Any] | None = None,
    ) -> RawResponse:
        kwargs: dict[str, Any] = {}
        if json_output:
            kwargs["response_format"] = {"type": "json_object"}
        if extra:
            kwargs["extra_body"] = extra  # provider-specific settings, for example reasoning_effort
        try:
            raw = self.client.chat.completions.with_raw_response.create(
                model=model,
                messages=messages,  # type: ignore[arg-type]
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )
        except openai.RateLimitError as exc:
            raise RateLimited(str(exc)[:200], _retry_after(exc.response.headers)) from None
        except openai.APIStatusError as exc:
            if exc.status_code >= 500:
                raise ServerError(f"{exc.status_code}: {str(exc)[:200]}") from None
            raise BadRequest(f"{exc.status_code}: {str(exc)[:200]}") from None
        except (openai.APIConnectionError, openai.APITimeoutError) as exc:
            raise ServerError(f"connection problem: {str(exc)[:200]}") from None
        r = raw.parse()
        choice = r.choices[0]
        usage = r.usage
        return RawResponse(
            text=choice.message.content or "",
            tokens_in=usage.prompt_tokens if usage else 0,
            tokens_out=usage.completion_tokens if usage else 0,
            finish_reason=choice.finish_reason,
            refusal=getattr(choice.message, "refusal", None),
            headers={k.lower(): v for k, v in raw.headers.items()},
        )


class AnthropicAdapter:
    def __init__(self, api_key: str, http_client: httpx2.Client | None = None) -> None:
        self.client = anthropic.Anthropic(api_key=api_key or "none", max_retries=0, http_client=http_client)

    def chat(
        self,
        model: str,
        messages: Messages,
        *,
        temperature: float,
        max_tokens: int,
        json_output: bool,
        extra: dict[str, Any] | None = None,
    ) -> RawResponse:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        if json_output:
            system += "\n\nReply with a single JSON object and nothing else."
        turns = [m for m in messages if m["role"] != "system"]
        try:
            r = self.client.messages.create(
                model=model,
                system=system or anthropic.omit,
                messages=turns,  # type: ignore[arg-type]
                max_tokens=max_tokens,
            )
        except anthropic.RateLimitError as exc:
            raise RateLimited(str(exc)[:200], _retry_after(exc.response.headers)) from None
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500 or exc.status_code == 529:
                raise ServerError(f"{exc.status_code}: {str(exc)[:200]}") from None
            raise BadRequest(f"{exc.status_code}: {str(exc)[:200]}") from None
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            raise ServerError(f"connection problem: {str(exc)[:200]}") from None
        text = "".join(b.text for b in r.content if b.type == "text")
        refused = r.stop_reason == "refusal"
        return RawResponse(
            text=text,
            tokens_in=r.usage.input_tokens,
            tokens_out=r.usage.output_tokens,
            finish_reason=r.stop_reason,
            refusal="refused" if refused else None,
        )
