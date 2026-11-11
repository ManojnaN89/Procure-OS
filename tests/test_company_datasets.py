"""The demo companies described by a profile file (Apex Pumps, Brightwave Technologies): their master data has the flow
company's shape, passes its checks, and loads into a new company with nothing left out; their ERP input files add up;
their twelve months of history end at the profile's stock, with every planted problem in the answer keys."""

import csv
import filecmp
import importlib.util
import shutil
import sys
import uuid
from collections import Counter
from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from p2p.approvals import rules as approval_rules
from p2p.masters.importer import import_masters
from p2p.platform import demo, routing, tenants
from p2p.receiving import stock

ROOT = Path(__file__).resolve().parents[1]
GENERATOR = ROOT / "datagen" / "generator"
_spec = importlib.util.spec_from_file_location("company_profile", GENERATOR / "company_profile.py")
assert _spec and _spec.loader
gen: Any = importlib.util.module_from_spec(_spec)
sys.modules["company_profile"] = gen
_spec.loader.exec_module(gen)
COMPANIES = ("apex", "brightwave")
TODAY = date(2026, 11, 6)


@pytest.fixture(scope="module")
def datasets(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    folder = tmp_path_factory.mktemp("companies")
    for name in COMPANIES:
        gen.build(gen.CompanyProfile.named(name), folder / name)
    demo._generator().build(folder / "flow")
    return {name: folder / name for name in (*COMPANIES, "flow")}


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _header(path: Path) -> str:
    with path.open(encoding="utf-8") as f:
        return f.readline()


@pytest.mark.parametrize("name", COMPANIES)
def test_each_company_has_the_flow_company_master_files_with_the_same_headers(
    datasets: dict[str, Path], name: str
) -> None:
    flow, mine = datasets["flow"] / "master", datasets[name] / "master"

    assert sorted(p.name for p in mine.glob("*.csv")) == sorted(p.name for p in flow.glob("*.csv"))
    for path in flow.glob("*.csv"):
        assert _header(mine / path.name) == _header(path), path.name


@pytest.mark.parametrize("name", COMPANIES)
def test_each_dataset_is_as_designed(datasets: dict[str, Path], name: str) -> None:
    problems = gen.check(datasets[name], gen.CompanyProfile.named(name))

    assert problems == []


def test_apex_production_order_lines_are_the_bill_of_materials_times_the_pumps_to_make(
    datasets: dict[str, Path],
) -> None:
    lines = {
        (r["order_no"], r["item_code"]): (r["qty"], r["unit"])
        for r in _rows(datasets["apex"] / "inputs" / "production_order_line.csv")
    }

    assert len(lines) == 20  # ten materials for each of two pumps
    assert lines[("MO-1", "RM-08")] == ("90.000", "KGS")  # 1.8 kg x 50 pumps
    assert lines[("MO-1", "RM-11")] == ("10.000", "LTR")  # 0.2 L x 50
    assert lines[("MO-2", "RM-11")] == ("15.000", "LTR")  # 0.15 L x 100
    assert lines[("MO-2", "RM-06")] == ("200.000", "NOS")  # two bearings x 100
    orders = {r["order_no"]: r for r in _rows(datasets["apex"] / "inputs" / "production_order.csv")}
    assert (orders["MO-1"]["qty"], orders["MO-1"]["reason"]) == ("50", "SO-1001")  # 60 ordered, 10 pumps in stock


def test_brightwave_joiners_are_planned_by_kit_and_have_no_production(datasets: dict[str, Path]) -> None:
    inputs = sorted(p.name for p in (datasets["brightwave"] / "inputs").glob("*.csv"))
    kits = _rows(datasets["brightwave"] / "inputs" / "joiner_kit.csv")

    assert inputs == ["joiner_kit.csv", "joiner_plan.csv"]
    assert {r["item_code"] for r in kits if r["kit"] == "developer"} == {
        "LP-02",
        "MN-01",
        "DK-01",
        "HS-01",
        "KB-01",
        "SW-01",
    }
    assert {r["item_code"] for r in kits if r["kit"] == "standard"} - {"LP-01"} == {
        "MN-01",
        "DK-01",
        "HS-01",
        "KB-01",
        "SW-01",
    }


@pytest.mark.parametrize("name", COMPANIES)
def test_requester_approver_receiver_ap_and_payer_are_different_people(datasets: dict[str, Path], name: str) -> None:
    held: dict[str, set[str]] = {}
    for r in _rows(datasets[name] / "master" / "user_role.csv"):
        held.setdefault(r["user_code"], set()).add(r["role"])

    jobs = {code: demo.login_role(roles) for code, roles in held.items()}

    assert {"Requester", "Approver", "Store", "AP", "Finance"} <= set(jobs.values())
    for one, two in (("Requester", "Approver"), ("Store", "AP"), ("AP", "Finance"), ("Store", "Finance")):
        assert not {c for c, j in jobs.items() if j == one} & {c for c, j in jobs.items() if j == two}


def _broken(
    source: Path, target: Path, table: str, change: dict[str, str], line: int = 0, folder: str = "master"
) -> Path:
    shutil.copytree(source, target)
    path = target / folder / f"{table}.csv"
    rows = _rows(path)
    rows[line].update(change)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    return target


@pytest.mark.parametrize(
    ("table", "change", "line", "found"),
    [
        ("supplier", {"pan": "DECCD1234C"}, 0, "does not carry 0000"),
        ("user", {"email": "production@apexpumps.com"}, 0, "does not end in .example"),
        ("approval_rule", {"min_value": "10000.02"}, 1, "leave a gap or overlap"),  # one paisa missing between bands
        ("approval_rule", {"max_value": "100000.01"}, 1, "leave a gap or overlap"),  # overlaps the next band by a paisa
        ("user_role", {"role": "approver"}, 0, "nobody is only a requester"),  # the requester approves too
        ("price_record", {"uom": "KGS"}, 0, "not per NOS"),
        ("budget", {"amount": "1450000.01"}, 0, "different budget in different months"),
        ("item", {"base_uom": "PIECE"}, 0, "not a GST unit code"),
    ],
)
def test_the_checks_catch_a_dataset_that_breaks_a_rule(
    datasets: dict[str, Path], tmp_path: Path, table: str, change: dict[str, str], line: int, found: str
) -> None:
    broken = _broken(datasets["apex"], tmp_path / "apex", table, change, line)

    problems = gen.check(broken, gen.CompanyProfile.named("apex"))

    assert any(found in p for p in problems), problems


@pytest.mark.parametrize(
    ("table", "change", "found"),
    [
        ("po_line", {"cgst": "1.00", "sgst": "1.00"}, "GST 1.00/1.00"),  # tax that is not rate times value
        ("invoice", {"total": "1.00"}, "totals"),
        ("stock_movement", {"qty": "9999.000"}, "below zero"),  # an issue of more than is on the shelf
        ("request", {"requester": "U02"}, "one person holds two"),  # the storekeeper asks for what he receives
        ("payment", {"paid_on": "2026-11-05"}, "outside the history"),
    ],
)
def test_the_history_checks_catch_a_history_that_breaks_a_rule(
    datasets: dict[str, Path], tmp_path: Path, table: str, change: dict[str, str], found: str
) -> None:
    rows = _rows(datasets["apex"] / "history" / f"{table}.csv")
    line = next(i for i, r in enumerate(rows) if table != "stock_movement" or r["type"] == "issue")
    broken = _broken(datasets["apex"], tmp_path / "apex", table, change, line, folder="history")

    problems = gen.check(broken, gen.CompanyProfile.named("apex"))

    assert any(found in p for p in problems), problems


def test_the_same_seed_gives_the_same_files(datasets: dict[str, Path], tmp_path: Path) -> None:
    gen.build(gen.CompanyProfile.named("apex"), tmp_path / "again")

    for path in sorted(datasets["apex"].rglob("*.*")):
        assert filecmp.cmp(path, tmp_path / "again" / path.relative_to(datasets["apex"]), shallow=False), path.name


@pytest.mark.parametrize("name", COMPANIES)
def test_the_history_ends_at_the_profile_stock_and_starts_where_the_movements_say(
    datasets: dict[str, Path], name: str
) -> None:
    profile = gen.CompanyProfile.named(name)
    level = {r["item_code"]: Decimal(r["qty"]) for r in _rows(datasets[name] / "master" / "stock_opening.csv")}
    for m in _rows(datasets[name] / "history" / "stock_movement.csv"):
        level[m["item_code"]] += Decimal(m["qty"]) * (1 if m["type"] == "receipt" else -1)

    assert {r["as_of"] for r in _rows(datasets[name] / "master" / "stock_opening.csv")} == {"2025-11-01"}
    assert level == {it["code"]: Decimal(str(it["on_hand"])) for it in profile["items"] if "on_hand" in it}


def test_apex_has_nothing_open_at_the_cutoff_for_the_materials_of_mo_1(datasets: dict[str, Path]) -> None:
    history = datasets["apex"] / "history"
    mo_1 = {
        r["item_code"]
        for r in _rows(datasets["apex"] / "inputs" / "production_order_line.csv")
        if r["order_no"] == "MO-1"
    }

    assert {r["status"] for r in _rows(history / "request_line.csv") if r["item_code"] in mo_1} == {"ordered"}
    assert {r["status"] for r in _rows(history / "po_line.csv") if r["item_code"] in mo_1} == {"received"}
    assert {r["status"] for r in _rows(history / "po.csv")} >= {"sent", "closed"}  # other work is still open
    assert [r["status"] for r in _rows(history / "invoice.csv")].count("blocked") == 1


@pytest.mark.parametrize("name", COMPANIES)
def test_every_planted_problem_is_in_the_answer_key_as_often_as_planned(datasets: dict[str, Path], name: str) -> None:
    history = gen.CompanyProfile.named(name)["history"]
    cases = Counter(r["type"] for r in _rows(datasets[name] / "truth" / "truth_case.csv"))
    blocked = Counter(r["reason_codes"] for r in _rows(datasets[name] / "truth" / "truth_match_expected.csv"))

    assert {kind: cases[kind] for kind in history["planted"]} == history["planted"]
    assert cases["split_order"] == len(history["split_orders"])
    assert cases["unmatched_bank_line"] == 12  # a bank charge at each month end
    assert (blocked["price_variance"], blocked["qty_over_received"]) == (
        history["planted"]["price_mismatch"],
        history["planted"]["qty_mismatch"],
    )
    assert cases["part_delivery"] > 0 and cases["supplier_date_change"] > 0


def test_apex_buys_castings_single_source_until_its_contract_starts_and_bearings_from_late_s2_by_rfq(
    datasets: dict[str, Path],
) -> None:
    po = {r["no"]: r for r in _rows(datasets["apex"] / "history" / "po.csv")}
    routes = [
        (po[r["po_no"]]["created_at"][:10], r["route"])
        for r in _rows(datasets["apex"] / "truth" / "truth_combined_lines.csv")
        if r["item_code"] == "RM-01"
    ]
    asked = {r["item_code"] for r in _rows(datasets["apex"] / "truth" / "truth_supplier_selection.csv")}

    assert {route for day, route in routes if day < "2026-04-01"} == {"single_source"}
    assert {route for day, route in routes if day >= "2026-04-01"} == {"contract"}
    assert "RM-06" in asked  # bearings: S2 is too often late for a safe reorder


def test_a_production_order_line_that_does_not_match_the_bill_of_materials_is_caught(
    datasets: dict[str, Path], tmp_path: Path
) -> None:
    target = tmp_path / "apex"
    shutil.copytree(datasets["apex"], target)
    path = target / "inputs" / "production_order_line.csv"
    path.write_text(
        path.read_text(encoding="utf-8").replace("MO-1,RM-08,90.000", "MO-1,RM-08,89.999"), encoding="utf-8"
    )

    assert any("bill of materials" in p for p in gen.check(target))


@pytest.fixture(scope="module", params=COMPANIES)
def company(
    request: pytest.FixtureRequest, control_db: str, datasets: dict[str, Path], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[str, Session, Any]]:
    name = request.param
    slug = name[:5] + uuid.uuid4().hex[:8]
    made = demo.reset_company(
        demo.company(name), datasets[name], credentials=tmp_path_factory.mktemp(name) / "credentials", slug=slug
    )
    with Session(routing.engine_for(slug)) as session:
        yield name, session, made
    routing.clear_engine_cache()
    tenants.drop_tenant(slug)


def test_the_masters_load_into_a_new_company_with_nothing_left_out_and_one_login_each(
    company: tuple[str, Session, Any], datasets: dict[str, Path]
) -> None:
    name, db, made = company

    report = import_masters(db, datasets[name], dry_run=True)

    assert made.report.rejected == [] and report.rejected == [] and report.warnings == []
    assert len(made.logins) == 9
    assert all(email.endswith(gen.CompanyProfile.named(name)["company"]["email_domain"]) for email, _, _ in made.logins)


def test_the_approval_rules_send_each_amount_to_the_right_person(company: tuple[str, Session, Any]) -> None:
    name, db, _ = company

    def route(document: str, value: str, category: str | None = None) -> list[str]:
        rule = approval_rules.find_rule(db, document, Decimal(value), TODAY, category=category)
        steps = approval_rules.resolve_chain(
            db, rule.roles, requester="U01", department="ENG" if name == "brightwave" else "PRD", on_date=TODAY
        )
        return [s.approver for s in steps]

    if name == "apex":  # plant head U07, purchase manager U08, director U09
        assert route("request", "10000.00") == [] and route("request", "10000.01") == ["U07"]
        assert route("request", "100000.00") == ["U07"] and route("request", "100000.01") == ["U09"]
        assert route("po", "100000.00") == [] and route("po", "100000.01") == ["U08"]
        assert route("po", "500000.00") == ["U08"] and route("po", "500000.01") == ["U09"]
        assert route("price_variance", "5000") == ["U08"] and route("payment_run", "5000") == ["U09"]
    else:  # head of engineering U07, IT head U08, CFO U09
        assert route("request", "10000.00") == [] and route("request", "10000.01") == ["U07"]
        assert route("request", "100000.01") == ["U09"]
        assert route("request", "5000", "ITH") == ["U08"]  # IT hardware always needs the IT head
        assert route("request", "50000", "SWS") == ["U07", "U08"]
        assert route("request", "150000", "ITH") == ["U08", "U09"]
        assert route("po", "100000.00") == [] and route("po", "100000.01") == ["U09"]


def test_reset_loads_the_stock_history_and_on_hand_is_the_profile_stock(company: tuple[str, Session, Any]) -> None:
    name, db, made = company

    on_hand = {line.item_code: line.on_hand for line in stock.stock_levels(db)}

    assert made.stock is not None and made.stock.rejected == [] and made.stock.rows_loaded > 500
    profile = gen.CompanyProfile.named(name)
    assert on_hand == {it["code"]: Decimal(str(it["on_hand"])) for it in profile["items"] if "on_hand" in it}


def test_the_stock_at_or_below_its_reorder_point_is_the_planned_spares_and_supplies(
    company: tuple[str, Session, Any],
) -> None:
    name, db, _ = company

    low = sorted(line.item_code for line in stock.stock_levels(db) if line.status == "reorder")

    assert low == (["SP-01"] if name == "apex" else ["OS-01", "OS-02"])
