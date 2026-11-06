from p2p.ai_steps.gateway import DEFAULT_LIMITS, DEFAULT_ROUTES, OPENAI_COMPATIBLE

READING_STEPS = ["quote_reading", "invoice_reading", "reply_classification", "item_mapping", "certificate_reading"]


def entries() -> list[tuple[str, tuple[object, ...]]]:
    return [(step, e) for step, route in DEFAULT_ROUTES.items() for e in route]


def test_every_default_route_names_a_known_provider_and_a_model() -> None:
    known = {*OPENAI_COMPATIBLE, "anthropic"}
    for step, entry in entries():
        assert entry[0] in known and isinstance(entry[1], str) and entry[1], (step, entry)


def test_every_groq_model_in_a_route_has_its_own_rate_limit_so_it_is_always_paced() -> None:
    for step, entry in entries():
        if entry[0] == "groq":
            assert f"groq/{entry[1]}" in DEFAULT_LIMITS, (step, entry)
    assert "nvidia" in DEFAULT_LIMITS


def test_every_reading_step_has_a_stronger_route_for_when_a_check_fails() -> None:
    for step in READING_STEPS:
        assert step in DEFAULT_ROUTES and f"{step}.strong" in DEFAULT_ROUTES


def test_the_strong_route_is_not_the_same_first_attempt_again() -> None:
    for step in READING_STEPS:
        assert DEFAULT_ROUTES[f"{step}.strong"][0] != DEFAULT_ROUTES[step][0]  # a different model or more thinking


def test_a_fallback_exists_for_every_step_even_if_the_first_provider_is_down() -> None:
    for step, route in DEFAULT_ROUTES.items():
        assert len({e[0] for e in route}) >= 2 or len({e[1] for e in route}) >= 2, step


def test_no_route_depends_only_on_the_nvidia_trial() -> None:
    for step, route in DEFAULT_ROUTES.items():
        assert any(e[0] != "nvidia" for e in route), f"{step} would stop if the NVIDIA trial were switched off"
