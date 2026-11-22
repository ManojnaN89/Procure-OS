"""Stock check and reservation (block 2) on the demo companies: every stock scenario of the datasets, run step by step
through the real services, agrees at each labelled step with the truth the generator worked out on its own
(truth_stock_check, truth_stock_position, truth_stock_requests and the expected cases of stock_scenarios.json).

Given a fresh demo reset (and the base store check), when the scenario's steps run on their business dates, then every
open material request line has the truth's need, set aside, timely, late, at risk, short, open cover and causes; every
place the truth lists has its usable, held, set aside, safety and available stock; each request the step makes or
changes has the truth's lines and value; the late-supply cases are opened, updated and resolved as expected."""

from collections.abc import Iterator
from decimal import Decimal

import pytest
from sqlalchemy import text
from stock_scenarios_support import Run, drop, scenarios, truth

ALL = [(name, sc["name"]) for name in ("apex", "brightwave") for sc in scenarios(name)]


@pytest.fixture
def run(control_db: str) -> Iterator[list[Run]]:
    made: list[Run] = []
    yield made
    for r in made:
        drop(r)


def _check_lines(r: Run, scenario: str, label: str) -> None:
    want = [t for t in truth(r.name, "check") if t["scenario"] == scenario and t["step"] == label]
    got = r.lines()
    assert want, (scenario, label)
    for t in want:
        key = (t["mr_order_no"], t["item_code"], t["location_code"])
        assert key in got, (label, key)
        expected = {k: t[k] for k in got[key]}
        assert got[key] == expected, (scenario, label, key)


def _check_positions(r: Run, scenario: str, label: str) -> None:
    want = [t for t in truth(r.name, "position") if t["scenario"] == scenario and t["step"] == label]
    got = r.positions(sorted({(t["item_code"], t["location_code"]) for t in want}))
    for t in want:
        key = (t["item_code"], t["location_code"])
        assert got[key] == {k: t[k] for k in got[key]}, (scenario, label, key)


def _check_requests(r: Run, scenario: str, label: str, before: dict[str, dict[str, object]]) -> None:
    want = [t for t in truth(r.name, "requests") if t["scenario"] == scenario and t["step"] == label]
    now = r.requests()
    for t in want:
        lines = sorted(t["lines"].split(";"))
        found = [
            no for no, x in now.items()
            if x["order_no"] == t["order_no"] and x["lines"] == lines and Decimal(str(x["total_value"])) == Decimal(t["value"])
        ]  # fmt: skip
        assert found, (scenario, label, t, {no: x for no, x in now.items() if no not in before or x != before[no]})
        if t["change"] == "new":
            assert any(no not in before for no in found), (scenario, label, t)


def _check_cases(r: Run, st: dict[str, object]) -> None:
    expect = st.get("expect") or {}
    assert isinstance(expect, dict)
    if "open_cases" not in expect:
        return
    rows = (
        r.s.execute(
            text(
                "select s.po_no, s.mr_no, s.lines, s.buffer_days, s.outcome, t.assignee, t.due_at, t.escalation_chain "
                "from supply_risk s join task t on t.id = s.task_id order by s.task_id"
            )
        )
        .mappings()
        .all()
    )
    open_ = [x for x in rows if x["outcome"] is None]
    assert len(open_) == len(expect["open_cases"]), (st["label"], rows)
    for want, got in zip(expect["open_cases"], open_, strict=True):
        assert got["mr_no"] == r.mr_of(want["mr_order_no"])
        assert got["assignee"] == r.uid(want["owner"])
        assert got["escalation_chain"] == [r.uid(want["escalate_to"])]
        assert (
            got["due_at"].astimezone().date().isoformat() == want["due"]
            or got["due_at"].date().isoformat() == want["due"]
        )
        assert got["buffer_days"] == want["buffer_days"]
        assert [(x["item_code"], x["at_risk"], x["promised"], x["latest_arrival"]) for x in got["lines"]] == [
            (x["item_code"], x["at_risk"], x["promised"], x["latest_arrival"]) for x in want["lines"]
        ]
    resolved = [x for x in rows if x["outcome"] == "resolved"]
    assert len(resolved) == len(expect["resolved_cases"]), (st["label"], rows)


@pytest.mark.parametrize(("name", "scenario"), ALL, ids=[s for _, s in ALL])
def test_each_stock_scenario_agrees_with_the_truth_at_every_step(run: list[Run], name: str, scenario: str) -> None:
    sc = next(s for s in scenarios(name) if s["name"] == scenario)
    r = Run(name)
    run.append(r)
    for st in sc["steps"]:
        before = r.requests() if r.db is not None else {}
        r.step(st)
        label = st["label"]
        if not label:
            continue
        expect = st.get("expect") or {}
        if "refused" in expect:
            refused = r.refused
            assert refused is not None and getattr(refused, "available", None) is not None
            assert f"{refused.available.normalize():f}" == expect["refused"]["available"]  # type: ignore[attr-defined]
            assert f"{refused.safety_stock.normalize():f}" == expect["refused"]["safety_stock"]  # type: ignore[attr-defined]
        _check_lines(r, scenario, label)
        _check_positions(r, scenario, label)
        _check_requests(r, scenario, label, before)
        _check_cases(r, st)
