"""The generator's own answer for the block 2 stock scenarios (held stock, late supply by rule Q17, safety stock, the
store check after events, restock of what is bought for production): the numbers of the spec, worked out by
datagen/generator/stock_scenarios.py from the raw files alone. No database: the datasets are built into a temporary
folder."""

import csv
import importlib.util
import json
import shutil
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "datagen" / "generator"
_spec = importlib.util.spec_from_file_location("company_profile", GENERATOR / "company_profile.py")
assert _spec and _spec.loader
gen: Any = sys.modules.get("company_profile") or importlib.util.module_from_spec(_spec)
if "company_profile" not in sys.modules:
    sys.modules["company_profile"] = gen
    _spec.loader.exec_module(gen)
D = Decimal


@pytest.fixture(scope="module")
def data(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    folder = tmp_path_factory.mktemp("stock")
    for name in ("apex", "brightwave"):
        gen.build(gen.CompanyProfile.named(name), folder / name)
    return {name: folder / name for name in ("apex", "brightwave")}


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


class Truth:
    def __init__(self, folder: Path) -> None:
        t = folder / "truth"
        self.check_rows = _rows(t / "truth_stock_check.csv")
        self.positions = _rows(t / "truth_stock_position.csv")
        self.requests = _rows(t / "truth_stock_requests.csv")
        self.scenarios = {s["name"]: s for s in json.loads((t / "stock_scenarios.json").read_text(encoding="utf-8"))}

    def line(self, scenario: str, step: str, order: str, item: str) -> dict[str, str]:
        found = [
            r
            for r in self.check_rows
            if (r["scenario"], r["step"], r["mr_order_no"], r["item_code"]) == (scenario, step, order, item)
        ]
        assert len(found) == 1, (scenario, step, order, item)
        return found[0]

    def figures(self, scenario: str, step: str, order: str, item: str) -> tuple[D, ...]:
        r = self.line(scenario, step, order, item)
        return tuple(D(r[k]) for k in ("reserved", "timely", "late", "at_risk", "short", "open_request_qty"))

    def position(self, scenario: str, step: str, item: str) -> dict[str, D]:
        r = next(r for r in self.positions if (r["scenario"], r["step"], r["item_code"]) == (scenario, step, item))
        return {k: D(v) for k, v in r.items() if k not in ("scenario", "step", "item_code", "location_code")}

    def made(self, scenario: str, step: str) -> list[dict[str, str]]:
        return [r for r in self.requests if (r["scenario"], r["step"]) == (scenario, step)]

    def expect(self, scenario: str, label: str) -> dict[str, Any]:
        step = next(s for s in self.scenarios[scenario]["steps"] if s["label"] == label)
        return dict(step.get("expect") or {})


@pytest.fixture(scope="module")
def apex(data: dict[str, Path]) -> Truth:
    return Truth(data["apex"])


@pytest.fixture(scope="module")
def bw(data: dict[str, Path]) -> Truth:
    return Truth(data["brightwave"])


def _value(rows: list[dict[str, str]], order: str) -> D:
    return D(next(r["value"] for r in rows if r["order_no"] == order))


# ---- the files and their checks -----------------------------------------------------------------------------


def test_held_stock_and_safety_stock_are_in_the_files(data: dict[str, Path]) -> None:
    held = _rows(data["apex"] / "history" / "stock_held.csv")
    assert [(h["id"], h["item_code"], h["quality"], h["qty"]) for h in held] == [
        ("H-APX-01", "RM-07", "quarantine", "20"),
        ("H-APX-02", "RM-06", "blocked", "8"),
    ]
    plan = {r["item_code"]: r["safety_stock"] for r in _rows(data["apex"] / "master" / "item_location.csv")}
    assert (plan["SP-01"], plan["SP-02"], plan["SP-03"], plan["RM-03"]) == ("2.000", "5.000", "10.000", "")
    plan = {r["item_code"]: r["safety_stock"] for r in _rows(data["brightwave"] / "master" / "item_location.csv")}
    assert (plan["OS-02"], plan["OS-04"], plan["OS-01"]) == ("1.000", "10.000", "0.000")
    assert [h["id"] for h in _rows(data["brightwave"] / "history" / "stock_held.csv")] == ["H-BWT-01", "H-BWT-02"]


def test_the_check_catches_bad_held_stock_and_safety_above_the_point(data: dict[str, Path], tmp_path: Path) -> None:
    copy = tmp_path / "apex"
    shutil.copytree(data["apex"], copy)
    held = copy / "history" / "stock_held.csv"
    held.write_text(held.read_text(encoding="utf-8") + "H-X,SV-01,PLT-CKN,2026-10-01,damaged,0,,x\n", encoding="utf-8")
    plan = copy / "master" / "item_location.csv"
    plan.write_text(
        plan.read_text(encoding="utf-8").replace(
            "SP-01,PLT-CKN,minmax,5.000,2.000", "SP-01,PLT-CKN,minmax,5.000,9.000"
        ),
        encoding="utf-8",
    )
    problems = " | ".join(gen.check(copy))
    for words in (
        "not stocked at PLT-CKN",
        "not quarantine or blocked",
        "not above zero",
        "no reason",
        "safety stock 9.000",
    ):
        assert words in problems, words


def test_every_scenario_is_spelled_out_from_the_reset(apex: Truth, bw: Truth) -> None:
    assert set(apex.scenarios) == {
        "A0", "Q1-seals", "Q3-hold", "T1-late", "T1b-on-time", "T2-count-and-arrival", "T3-buffer", "T4-overdue",
        "S1-breakdown", "S2-impeller-safety", "D1-restock", "E1-short-close", "E2-rejected",
    }  # fmt: skip
    assert set(bw.scenarios) == {"B0", "Q2-monitors", "BT1-late", "BT2-buffer"}
    for sc in [*apex.scenarios.values(), *bw.scenarios.values()]:
        assert sc["steps"][0]["do"] == "reset", sc["name"]
        assert all(s["on"] and s["do"] for s in sc["steps"])
    t2 = [s["do"] for s in apex.scenarios["T2-count-and-arrival"]["steps"]]
    assert t2 == ["reset", "store_check", "approve_request", "source", "acknowledge", "count", "receive"]


# ---- base and held stock (4.1) ---------------------------------------------------------------------------------


def test_a0_is_the_plans_worked_example_and_held_lots_change_nothing(apex: Truth, bw: Truth) -> None:
    base = apex.made("A0", "base")
    assert _value(base, "MO-1") == D("72930.00") and _value(base, "MO-2") == D("209700.00")
    assert next(r for r in base if r["order_no"] == "MO-1")["lines"] == "RM-01:50;RM-06:60;RM-08:40;PK-01:30"
    assert apex.figures("A0", "base", "MO-1", "RM-06")[0] == 40  # the 8 blocked bearings are never set aside
    assert apex.line("A0", "base", "MO-1", "RM-06")["causes"] == "held_quality;on_order"
    assert _value(bw.made("B0", "base"), "JP-2026-12/ENG") == D("440200.00")


def test_q1_seals_passed_go_to_mo2_and_cut_its_request(apex: Truth) -> None:
    pos = apex.position("Q1-seals", "after_decision", "RM-07")
    assert (pos["usable"], pos["quarantine"], pos["blocked"]) == (74, 0, 6)
    r = apex.figures("Q1-seals", "after_decision", "MO-2", "RM-07")
    assert (r[0], r[5]) == (24, 76)
    assert _value(apex.made("Q1-seals", "after_decision"), "MO-2") == D("208370.00")
    assert apex.figures("Q1-seals", "after_decision", "MO-1", "RM-06")[0] == 40


def test_q2_monitors_passed_burn_in(bw: Truth) -> None:
    r = bw.figures("Q2-monitors", "after_decision", "JP-2026-12/ENG", "MN-01")
    assert (r[0], r[5]) == (2, 2)
    assert _value(bw.made("Q2-monitors", "after_decision"), "JP-2026-12/ENG") == D("415200.00")
    assert bw.position("Q2-monitors", "after_decision", "LP-01")["blocked"] == 1
    assert bw.figures("Q2-monitors", "after_decision", "JP-2026-12/ADM", "LP-01")[0] == 2


def test_q3_crushed_boxes_give_back_the_latest_need_and_buy_it_again(apex: Truth) -> None:
    pos = apex.position("Q3-hold", "after_hold", "RM-10")
    assert (pos["usable"], pos["blocked"], pos["reserved"]) == (190, 10, 190)
    assert apex.figures("Q3-hold", "after_hold", "MRP-2027-01/PLT-CKN", "RM-10")[0] == 40
    assert apex.expect("Q3-hold", "after_hold")["released"] == {"MRP-2027-01/PLT-CKN": "10"}
    made = apex.made("Q3-hold", "after_hold")
    assert [(r["lines"], r["value"], r["change"]) for r in made] == [("RM-10:10", "600.00", "new")]


# ---- late supply (Q17) ------------------------------------------------------------------------------------------


def test_t1_a_late_promise_is_at_risk_not_bought_again_and_one_case(apex: Truth) -> None:
    assert apex.figures("T1-late", "after_ack", "MO-1", "RM-01") == (0, 0, 50, 50, 0, 50)
    assert apex.made("T1-late", "after_ack") == []
    cases = apex.expect("T1-late", "after_ack")["open_cases"]
    assert len(cases) == 1
    case = cases[0]
    # 24 Nov 2026 (Guru Nanak Jayanti) is a holiday at Chakan: two working days from Monday 23 Nov end on 26 Nov
    assert (case["owner"], case["escalate_to"], case["due"], case["buffer_days"]) == ("U03", "U08", "2026-11-26", 0)
    for item in ("RM-03", "RM-06", "RM-08", "PK-01"):  # the rest of A0 as it was
        assert apex.figures("T1-late", "after_ack", "MO-1", item) == apex.figures("A0", "base", "MO-1", item)


def test_t1b_on_the_day_is_in_time(apex: Truth) -> None:
    assert apex.figures("T1b-on-time", "after_ack", "MO-1", "RM-01") == (0, 50, 0, 0, 0, 50)
    assert "open_cases" not in apex.expect("T1b-on-time", "after_ack")


def test_t2_count_goes_to_the_line_at_risk_and_the_late_lot_frees_the_mrp(apex: Truth) -> None:
    assert apex.figures("T2-count-and-arrival", "after_count", "MO-1", "RM-01")[:4] == (20, 0, 50, 30)
    mrp = apex.figures("T2-count-and-arrival", "after_count", "MRP-2027-01/PLT-CKN", "RM-01")
    assert (mrp[0], mrp[5]) == (0, 120)
    assert apex.figures("T2-count-and-arrival", "after_receipt", "MO-1", "RM-01")[0] == 50
    mrp = apex.figures("T2-count-and-arrival", "after_receipt", "MRP-2027-01/PLT-CKN", "RM-01")
    assert (mrp[0], mrp[5]) == (20, 100)
    made = apex.made("T2-count-and-arrival", "after_receipt")
    assert _value(made, "MRP-2027-01/PLT-CKN") == D("356490.00")
    exp = apex.expect("T2-count-and-arrival", "after_receipt")
    assert exp["open_cases"] == [] and [c["outcome"] for c in exp["resolved_cases"]] == ["resolved"]


def test_t3_one_working_day_of_buffer(apex: Truth) -> None:
    assert apex.figures("T3-buffer", "after_buffer", "MO-1", "RM-01")[3] == 50
    case = apex.expect("T3-buffer", "after_buffer")["open_cases"][0]
    assert case["buffer_days"] == 1 and case["lines"][0]["latest_arrival"] == "2026-12-04"


def test_t4_an_overdue_promise_counts_as_today(apex: Truth) -> None:
    assert apex.figures("T4-overdue", "after_job_4dec", "MO-1", "RM-01")[1:4] == (50, 0, 0)
    assert apex.figures("T4-overdue", "after_job_7dec", "MO-1", "RM-01")[1:4] == (0, 50, 50)
    case = apex.expect("T4-overdue", "after_job_7dec")["open_cases"][0]
    assert case["lines"][0]["promised"] == "2026-12-03" and case["due"] == "2026-12-09"


def test_bt1_laptops_and_screens_late_then_the_monitors_passed(bw: Truth) -> None:
    for item, risk in (("LP-02", 3), ("MN-01", 4), ("DK-01", 4)):
        assert bw.figures("BT1-late", "after_ack", "JP-2026-12/ENG", item)[3] == risk
    case = bw.expect("BT1-late", "after_ack")["open_cases"][0]
    # 24 Nov is a central holiday in Bengaluru too
    assert (case["owner"], case["escalate_to"], case["due"]) == ("U03", "U09", "2026-11-26")
    assert bw.figures("BT1-late", "after_decision", "JP-2026-12/ENG", "MN-01")[0] == 2
    assert bw.figures("BT1-late", "after_decision", "JP-2026-12/ENG", "MN-01")[3] == 2


def test_bt2_a_saturday_delivery_is_late_only_with_the_two_day_buffer(bw: Truth) -> None:
    for item in ("LP-02", "MN-01", "DK-01"):
        assert bw.figures("BT2-buffer", "after_ack", "JP-2026-12/ENG", item)[3] == 0
    for item, risk in (("LP-02", 3), ("MN-01", 4), ("DK-01", 4)):
        assert bw.figures("BT2-buffer", "after_buffer", "JP-2026-12/ENG", item)[3] == risk
    case = bw.expect("BT2-buffer", "after_buffer")["open_cases"][0]
    # 1 Dec back two working days on the Mon-Fri office calendar, with Kanakadasa Jayanthi (Fri 27 Nov) closed
    assert case["lines"][0]["latest_arrival"] == "2026-11-26" and case["buffer_days"] == 2


# ---- safety stock (C4) ------------------------------------------------------------------------------------------


def test_s1_breakdown_needs_the_override_then_restock(apex: Truth) -> None:
    refused = apex.expect("S1-breakdown", "issue_refused")["refused"]
    assert (refused["available"], refused["safety_stock"]) == ("2", "2")
    assert apex.expect("S1-breakdown", "override_asked")["approver"] == "U07"
    assert apex.position("S1-breakdown", "after_override", "SP-01")["usable"] == 1
    made = apex.made("S1-breakdown", "after_replenish")
    assert [(r["kind"], r["lines"], r["value"], r["priority"]) for r in made] == [
        ("restock", "SP-01:14", "2940.00", "urgent")
    ]


def test_s2_impeller_safety_stock_then_the_override_for_mo1(apex: Truth) -> None:
    mo1 = apex.figures("S2-impeller-safety", "after_check", "MO-1", "RM-03")
    assert (mo1[0], mo1[5]) == (40, 10)
    mrp = apex.figures("S2-impeller-safety", "after_check", "MRP-2027-01/PLT-CKN", "RM-03")
    assert (mrp[0], mrp[5]) == (0, 120)
    made = apex.made("S2-impeller-safety", "after_check")
    assert _value(made, "MO-1") == D("75130.00") and _value(made, "MRP-2027-01/PLT-CKN") == D("371690.00")
    mo1 = apex.figures("S2-impeller-safety", "after_override", "MO-1", "RM-03")
    assert (mo1[0], mo1[5]) == (50, 0)
    assert _value(apex.made("S2-impeller-safety", "after_override"), "MO-1") == D("72930.00")
    assert apex.position("S2-impeller-safety", "after_override", "RM-03")["available"] == -10


# ---- restock and events -------------------------------------------------------------------------------------


def test_d1_a_po_bought_for_production_does_not_stop_the_restock(apex: Truth) -> None:
    made = apex.made("D1-restock", "after_replenish")
    rm01 = next(r for r in made if r["lines"].startswith("RM-01"))
    assert (rm01["lines"], rm01["value"], rm01["priority"], rm01["approver_code"]) == (
        "RM-01:60", "39000.00", "urgent", "U07",
    )  # fmt: skip
    assert apex.made("D1-restock", "after_second_run") == []


def test_e1_the_short_closed_balance_is_bought_again_by_the_event(apex: Truth) -> None:
    assert apex.figures("E1-short-close", "after_receipt", "MO-1", "RM-01")[0] == 30
    made = apex.made("E1-short-close", "after_close")
    assert [(r["lines"], r["value"], r["approver_code"]) for r in made] == [("RM-01:20", "13000.00", "U07")]


def test_e2_a_rejected_request_is_bought_again_only_by_the_button(apex: Truth) -> None:
    assert apex.made("E2-rejected", "after_reject") == [] and apex.made("E2-rejected", "after_job") == []
    assert apex.expect("E2-rejected", "after_reject")["last_rejected"]
    assert _value(apex.made("E2-rejected", "after_button"), "MO-2") == D("209700.00")
