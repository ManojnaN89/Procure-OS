"""Live checks against real providers. They run only when the API keys are in .env, and use synthetic text only."""

import threading
from typing import Any

import pytest
from pydantic import BaseModel

from p2p.ai_steps.gateway import Gateway, build_adapters, load_routes
from p2p.config import get_settings

SETTINGS = get_settings()
LIMIT_SECONDS = 120
TEXT = "Quote Q-77 from Shree Steel Traders: 40 bearings at Rs 125 each, total Rs 5000, delivery in 10 days."


class Quote(BaseModel):
    supplier: str
    total: int


def _one_provider(name: str) -> Gateway:
    adapters = build_adapters()
    entry = {e[0]: e for e in load_routes()["default"]}.get(name)
    if name not in adapters or entry is None:
        pytest.skip(f"no key or route for {name}")
    return Gateway({name: adapters[name]}, {"live": [entry]}, max_retries=1, max_wait=20)


def _extract(name: str) -> Quote:
    """One extraction, or a failure after LIMIT_SECONDS: a provider that stops answering must not hang the test run."""
    gateway = _one_provider(name)
    got: dict[str, Any] = {}

    def call() -> None:
        try:
            got["value"] = gateway.extract("live", [{"role": "user", "content": TEXT}], Quote).value
        except Exception as exc:  # noqa: BLE001 - raised again in the test below
            got["error"] = exc

    worker = threading.Thread(target=call, daemon=True)
    worker.start()
    worker.join(LIMIT_SECONDS)
    if worker.is_alive():
        pytest.fail(f"{name} gave no answer within {LIMIT_SECONDS} seconds")
    if "error" in got:
        raise got["error"]
    return got["value"]


@pytest.mark.skipif(not SETTINGS.groq_api_key, reason="GROQ_API_KEY is not set")
def test_an_extraction_runs_on_groq() -> None:
    got = _extract("groq")
    assert got.total == 5000 and "Shree" in got.supplier


@pytest.mark.skipif(not SETTINGS.gemini_api_key, reason="GEMINI_API_KEY is not set")
def test_the_same_extraction_runs_on_gemini_through_the_same_interface() -> None:
    got = _extract("gemini")
    assert got.total == 5000 and "Shree" in got.supplier


@pytest.mark.skipif(not SETTINGS.nvidia_api_key, reason="NVIDIA_API_KEY is not set")
def test_the_same_extraction_runs_on_nvidia_through_the_same_interface() -> None:
    got = _extract("nvidia")
    assert got.total == 5000 and "Shree" in got.supplier
