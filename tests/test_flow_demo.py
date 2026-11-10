"""The small demo company (`flow`): made from nothing by one command, the right shape, and the first action works."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from p2p.approvals import rules as approval_rules
from p2p.formulas.calendar import business_today
from p2p.main import app
from p2p.masters.importer import import_masters
from p2p.platform import demo, routing, tenants, users
from p2p.receiving import stock
from p2p.requests import replenishment
from p2p.sourcing import documents

client = TestClient(app)
TODAY = date(2026, 11, 6)


@dataclass
class Flow:
    slug: str
    db: Session
    credentials: Path
    dataset: Path


@pytest.fixture(scope="module")
def flow(control_db: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Flow]:
    folder = tmp_path_factory.mktemp("flow")
    slug = "flowt" + uuid.uuid4().hex[:8]
    credentials = folder / "credentials"
    dataset = folder / "dataset"
    demo.reset_flow(dataset, credentials=credentials, slug=slug)
    with Session(routing.engine_for(slug)) as session:
        yield Flow(slug, session, credentials, dataset)
    routing.clear_engine_cache()
    tenants.drop_tenant(slug)


def _logins(path: Path) -> dict[str, tuple[str, str]]:
    """email -> (password, role) from the credentials file."""
    found: dict[str, tuple[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        email, password, _, rest = line.split(None, 3)
        found[email] = (password, rest[rest.rindex("(") + 1 : rest.rindex(")")])
    return found


def test_the_company_has_eight_people_each_with_one_login_and_the_roles_the_script_names(flow: Flow) -> None:
    logins = _logins(flow.credentials)

    assert flow.db.execute(text("select count(*) from org_user")).scalar_one() == 8
    assert {role for _, role in logins.values()} == {
        "Approver",
        "Requester",
        "Store",
        "Buyer",
        "AP",
        "Finance",
        "Admin",
    }
    assert (
        sorted(role for _, role in logins.values()).count("Approver") == 2
    )  # the maintenance manager and the plant head
    assert len(logins) == 8 and all(email.endswith("@flow.example") for email in logins)
    for email, (password, role) in logins.items():
        signed_in = client.post("/api/auth/login", json={"email": email, "password": password})
        assert signed_in.status_code == 200, email
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {signed_in.json()['access_token']}"}).json()
        assert me["role"] == role


def test_twelve_items_and_four_of_them_are_at_or_below_their_reorder_point(flow: Flow) -> None:
    levels = stock.stock_levels(flow.db)

    assert len(levels) == 12
    assert sorted(line.item_code for line in levels if line.status == "reorder") == [
        "ITM-001",
        "ITM-004",
        "ITM-006",
        "ITM-011",
    ]
    assert sum(1 for line in levels if line.status == "ok") == 8  # six healthy and two just above the point
    assert {
        line.on_hand - (line.reorder_point or 0) for line in levels if line.item_code in ("ITM-008", "ITM-012")
    } == {1}


def test_the_approval_rules_send_each_amount_to_the_right_person(flow: Flow) -> None:
    db = flow.db

    def route(value: str) -> tuple[str, ...]:
        return approval_rules.find_rule(db, "request", Decimal(value), TODAY).roles

    assert route("10000.00") == () and route("10000.01") == ("manager",) and route("100000.00") == ("manager",)
    assert route("100000.01") == ("plant_head",)
    for document in ("po", "payment_run", "price_variance"):
        assert approval_rules.find_rule(db, document, Decimal("250000"), TODAY).roles == ("plant_head",)
    # the maintenance engineer's manager is the maintenance manager; the plant head is found by designation
    chain = approval_rules.resolve_chain(
        db, ("manager", "plant_head"), requester="U03", department="MNT", on_date=TODAY
    )
    assert [(step.role, step.approver) for step in chain] == [("manager", "U02"), ("plant_head", "U01")]
    assert {r[0] for r in db.execute(text("select role from approver_role"))} == {"manager", "dept_head", "plant_head"}


def test_four_suppliers_can_all_be_used_one_is_a_micro_enterprise_and_one_has_a_rate_contract(flow: Flow) -> None:
    db = flow.db

    codes = [r[0] for r in db.execute(text("select code from supplier order by code"))]
    assert codes == ["SUP-001", "SUP-002", "SUP-003", "SUP-004"]
    for code in codes:
        assert documents.check_supplier(db, code, TODAY).ok, code
    micro = db.execute(
        text(
            "select msme_category, msme_agreement, agreed_payment_days, payment_terms_code from supplier where code = 'SUP-001'"
        )
    ).one()
    assert tuple(micro) == ("micro", True, 45, "NET60")  # the 45-day rule shows against the 60-day terms
    contracts = db.execute(text("select code, supplier_code, status from contract")).all()
    assert [tuple(c) for c in contracts] == [("CNT-0001", "SUP-002", "active")]
    assert db.execute(text("select count(*) from price_record where contract_code = 'CNT-0001'")).scalar_one() == 3
    assert (
        db.execute(text("select count(*) from supplier where state_code <> '27'")).scalar_one() == 1
    )  # one interstate supplier


def test_nothing_has_happened_yet_and_the_budget_and_settings_are_in_place(flow: Flow) -> None:
    db = flow.db

    for table in ("request", "rfq", "po", "invoice", "payment_run", "goods_receipt"):
        assert db.execute(text(f"select count(*) from {table}")).scalar_one() == 0, table  # noqa: S608
    assert (
        db.execute(text("select value from app_setting where key = 'replenishment_department'")).scalar_one() == "MNT"
    )
    assert db.execute(text("select count(*) from budget")).scalar_one() == 24
    assert (
        db.execute(text("select count(*) from autonomy_setting where level = 'auto'")).scalar_one() == 0
    )  # a buyer approves sending


def test_the_dataset_loads_into_a_company_with_nothing_left_out(flow: Flow, control_db: str) -> None:
    report = import_masters(flow.db, flow.dataset, dry_run=True)

    assert report.rejected == [] and report.rows_loaded > 200


def test_check_stock_levels_makes_three_requests_one_approves_itself_and_two_wait_for_the_maintenance_manager(
    flow: Flow,
) -> None:
    result = replenishment.run(flow.db, as_of=business_today(), tenant=flow.slug, submit=True)

    by_category = {r.category_code: r for r in result.requests}
    assert sorted(by_category) == ["ELE", "MRO", "PPE"]
    assert (by_category["PPE"].total_value, by_category["PPE"].outcome) == (Decimal("9200.00"), "approved")
    assert (by_category["MRO"].total_value, by_category["MRO"].outcome) == (Decimal("55000.00"), "submitted")
    assert (by_category["ELE"].total_value, by_category["ELE"].outcome) == (Decimal("12400.00"), "submitted")
    waiting = flow.db.execute(
        text("select approver, status from approval_step where status = 'pending' order by request_no")
    ).all()
    assert [tuple(w) for w in waiting] == [("U02", "pending"), ("U02", "pending")]
    assert not any(r.submit_error for r in result.requests)


def test_the_credentials_file_keeps_other_companies_and_replaces_only_its_own(tmp_path: Path) -> None:
    path = tmp_path / "credentials"
    path.write_text("# local demo logins for tenant 'demo' (git-ignored)\na@demo.example  pw1\n\n", encoding="utf-8")

    demo._write_credentials(path, "flow", [("x@flow.example", "secret-1", "Buyer", "Buyer")])
    demo._write_credentials(path, "flow", [("x@flow.example", "secret-2", "Buyer", "Buyer")])

    content = path.read_text(encoding="utf-8")
    assert "a@demo.example  pw1" in content and "secret-1" not in content and content.count("tenant 'flow'") == 1
    assert "x@flow.example  secret-2" in content


def test_every_login_has_the_same_throwaway_development_password_and_it_passes_the_platform_rules(flow: Flow) -> None:
    logins = _logins(flow.credentials)

    assert {password for password, _ in logins.values()} == {demo.DEV_PASSWORD}
    assert len(demo.DEV_PASSWORD) >= users.MIN_PASSWORD_LENGTH
    assert sorted(email.split("@")[0] for email in logins) == [
        "admin", "ap", "approver", "buyer", "finance", "manager", "requester", "store",
    ]  # fmt: skip
    names = {r[0] for r in flow.db.execute(text("select name from org_user"))}
    assert names == {
        "Plant Head",
        "Maintenance Manager",
        "Requester",
        "Storekeeper",
        "Buyer",
        "Accounts Payable",
        "Finance Manager",
        "Admin",
    }  # no personal names


def test_making_the_company_twice_leaves_one_company_and_one_set_of_logins(flow: Flow, tmp_path: Path) -> None:
    flow.db.close()  # its database is about to be dropped and made again
    again = demo.reset_flow(flow.dataset, credentials=flow.credentials, slug=flow.slug)

    assert len(again.logins) == 8 and again.report is not None and again.report.rejected == []
    assert len(_logins(flow.credentials)) == 8
    with Session(routing.engine_for(flow.slug)) as fresh:
        assert (
            fresh.execute(text("select count(*) from request")).scalar_one() == 0
        )  # the earlier run's requests are gone
    with Session(tenants.control_engine()) as control:
        assert control.execute(text("select count(*) from tenant where slug = :s"), {"s": flow.slug}).scalar_one() == 1


@pytest.mark.parametrize("host", ["db.example.com", "10.0.0.5", "192.168.1.20", "prod-db"])
def test_the_company_is_not_made_on_a_database_that_is_not_on_this_machine(
    host: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demo.tenants, "control_url", lambda: make_url(f"postgresql+psycopg://u:p@{host}:5432/control"))

    with pytest.raises(demo.DemoError, match="only made on this machine"):
        demo.reset_flow(tmp_path / "dataset", credentials=tmp_path / "credentials", slug="flowremote")

    assert not (tmp_path / "credentials").exists() and not (tmp_path / "dataset").exists()  # nothing was touched


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_a_local_database_is_accepted(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo.tenants, "control_url", lambda: make_url(f"postgresql+psycopg://u:p@{host}:5440/control"))

    demo.require_local_database()
