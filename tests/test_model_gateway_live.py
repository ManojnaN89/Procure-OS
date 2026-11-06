"""Live checks against real providers. They run only when the API keys are in .env, and use synthetic text only."""

import pytest
from pydantic import BaseModel

from p2p.ai_steps.gateway import Gateway, build_adapters, load_routes
from p2p.config import get_settings

SETTINGS = get_settings()
TEXT = "Quote Q-77 from Shree Steel Traders: 40 bearings at Rs 125 each, total Rs 5000, delivery in 10 days."


class Quote(BaseModel):
    supplier: str
    total: int


def _one_provider(name: str) -> Gateway:
    adapters = build_adapters()
    entry = {e[0]: e for e in load_routes()["default"]}.get(name)
    if name not in adapters or entry is None:
        pytest.skip(f"no key or route for {name}")
    return Gateway({name: adapters[name]}, {"live": [entry]})


@pytest.mark.skipif(not SETTINGS.groq_api_key, reason="GROQ_API_KEY is not set")
def test_an_extraction_runs_on_groq() -> None:
    got = _one_provider("groq").extract("live", [{"role": "user", "content": TEXT}], Quote)
    assert got.value.total == 5000 and "Shree" in got.value.supplier


@pytest.mark.skipif(not SETTINGS.gemini_api_key, reason="GEMINI_API_KEY is not set")
def test_the_same_extraction_runs_on_gemini_through_the_same_interface() -> None:
    got = _one_provider("gemini").extract("live", [{"role": "user", "content": TEXT}], Quote)
    assert got.value.total == 5000 and "Shree" in got.value.supplier


@pytest.mark.skipif(not SETTINGS.nvidia_api_key, reason="NVIDIA_API_KEY is not set")
def test_the_same_extraction_runs_on_nvidia_through_the_same_interface() -> None:
    got = _one_provider("nvidia").extract("live", [{"role": "user", "content": TEXT}], Quote)
    assert got.value.total == 5000 and "Shree" in got.value.supplier
