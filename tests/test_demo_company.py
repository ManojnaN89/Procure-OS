"""The main demo company (Apex Pumps, `apex`): made from nothing by one command, with its logins, and the first action
works. The shape of its master data and history is checked in test_company_datasets.py."""

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

from p2p.formulas.calendar import business_today
from p2p.main import app
from p2p.platform import demo, routing, tenants, users
from p2p.requests import replenishment
from p2p.sourcing import documents

client = TestClient(app)
TODAY = date(2026, 11, 6)
LOGINS = [
    "admin",
    "admin2",
    "ap",
    "buyer",
    "director",
    "finance",
    "planthead",
    "production",
    "purchasemanager",
    "store",
]


@dataclass
class Apex:
    slug: str
    db: Session
    credentials: Path
    dataset: Path


@pytest.fixture(scope="module")
def apex(control_db: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Apex]:
    folder = tmp_path_factory.mktemp("apex")
    slug = "apext" + uuid.uuid4().hex[:8]
    credentials = folder / "credentials"
    dataset = folder / "dataset"
    demo.reset_company(demo.company("apex"), dataset, credentials=credentials, slug=slug)
    with Session(routing.engine_for(slug)) as session:
        yield Apex(slug, session, credentials, dataset)
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


def test_the_main_demo_company_is_apex() -> None:
    assert demo.MAIN == "apex" and demo.profiled_companies() == ["apex", "brightwave"]
    with pytest.raises(demo.DemoError, match="no demo company 'flow'"):
        demo.company("flow")


def test_each_of_the_ten_people_signs_in_with_the_role_the_script_names(apex: Apex) -> None:
    logins = _logins(apex.credentials)

    assert sorted(email.split("@")[0] for email in logins) == LOGINS
    assert all(email.endswith("@apexpumps.example") for email in logins)
    roles = sorted(role for _, role in logins.values())
    assert roles == [
        "AP",
        "Admin",
        "Admin",
        "Approver",
        "Approver",
        "Approver",
        "Buyer",
        "Finance",
        "Requester",
        "Store",
    ]
    for email, (password, role) in logins.items():
        signed_in = client.post("/api/auth/login", json={"email": email, "password": password})
        assert signed_in.status_code == 200, email
        me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {signed_in.json()['access_token']}"}).json()
        assert me["role"] == role


def test_every_login_has_the_same_throwaway_development_password_and_it_passes_the_platform_rules(apex: Apex) -> None:
    logins = _logins(apex.credentials)

    assert {password for password, _ in logins.values()} == {demo.DEV_PASSWORD}
    assert len(demo.DEV_PASSWORD) >= users.MIN_PASSWORD_LENGTH


def test_six_suppliers_can_all_be_used_one_is_a_micro_enterprise_and_one_has_the_castings_contract(apex: Apex) -> None:
    db = apex.db

    codes = [r[0] for r in db.execute(text("select code from supplier order by code"))]
    assert codes == [f"SUP-00{n}" for n in range(1, 7)]
    for code in codes:
        assert documents.check_supplier(db, code, TODAY).ok, code
    micro = db.execute(
        text(
            "select msme_category, msme_agreement, agreed_payment_days, state_code from supplier where code = 'SUP-002'"
        )
    ).one()
    assert tuple(micro) == ("micro", True, 45, "24")  # in Gujarat: IGST
    contracts = db.execute(text("select code, supplier_code, status from contract")).all()
    assert [tuple(c) for c in contracts] == [("CNT-0001", "SUP-001", "active")]
    by_status = dict(
        db.execute(
            text("select status, count(*) from price_record where contract_code = 'CNT-0001' group by status")
        ).all()
    )
    assert by_status == {"active": 5, "draft": 2}  # the castings' revised rates from 1 Jan wait for a second person


def test_nothing_is_open_yet_and_the_settings_are_in_place(apex: Apex) -> None:
    db = apex.db

    for table in ("request", "rfq", "po", "invoice", "payment_run", "goods_receipt"):
        assert db.execute(text(f"select count(*) from {table}")).scalar_one() == 0, table  # noqa: S608
    assert (
        db.execute(text("select value from app_setting where key = 'replenishment_department'")).scalar_one() == "PRD"
    )
    assert {r[0] for r in db.execute(text("select role from approver_role"))} == {
        "plant_head",
        "purchase_manager",
        "director",
    }
    # only touchless orders act alone (the profile's `autonomy`); everything else waits for a person
    auto = db.execute(text("select decision_type from autonomy_setting where level = 'auto'")).scalars().all()
    assert auto == ["po.issue"]


def test_check_stock_levels_makes_one_request_for_the_v_belts_and_it_approves_itself(apex: Apex) -> None:
    result = replenishment.run(apex.db, as_of=business_today(), tenant=apex.slug, submit=True)

    assert [(r.category_code, r.outcome) for r in result.requests] == [("MRO", "approved")]
    (line,) = result.requests[0].lines
    assert (line.item_code, line.qty, result.requests[0].total_value) == ("SP-01", Decimal("11"), Decimal("2310.00"))
    assert not any(r.submit_error for r in result.requests)


def test_the_credentials_file_keeps_other_companies_and_replaces_only_its_own(tmp_path: Path) -> None:
    path = tmp_path / "credentials"
    path.write_text("# local logins for tenant 'other' (git-ignored)\na@other.example  pw1\n\n", encoding="utf-8")

    demo._write_credentials(path, "apex", [("x@apexpumps.example", "secret-1", "Buyer", "Buyer")])
    demo._write_credentials(path, "apex", [("x@apexpumps.example", "secret-2", "Buyer", "Buyer")])

    content = path.read_text(encoding="utf-8")
    assert "a@other.example  pw1" in content and "secret-1" not in content and content.count("tenant 'apex'") == 1
    assert "x@apexpumps.example  secret-2" in content


def test_making_the_company_twice_leaves_one_company_and_one_set_of_logins(apex: Apex) -> None:
    apex.db.close()  # its database is about to be dropped and made again
    again = demo.reset_company(demo.company("apex"), apex.dataset, credentials=apex.credentials, slug=apex.slug)

    assert len(again.logins) == 10 and again.report is not None and again.report.rejected == []
    assert len(_logins(apex.credentials)) == 10
    with Session(routing.engine_for(apex.slug)) as fresh:
        assert fresh.execute(text("select count(*) from request")).scalar_one() == 0  # the earlier requests are gone
    with Session(tenants.control_engine()) as control:
        assert control.execute(text("select count(*) from tenant where slug = :s"), {"s": apex.slug}).scalar_one() == 1


@pytest.mark.parametrize("host", ["db.example.com", "10.0.0.5", "192.168.1.20", "prod-db"])
def test_the_company_is_not_made_on_a_database_that_is_not_on_this_machine(
    host: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(demo.tenants, "control_url", lambda: make_url(f"postgresql+psycopg://u:p@{host}:5432/control"))

    with pytest.raises(demo.DemoError, match="only made on this machine"):
        demo.reset_company(
            demo.company("apex"), tmp_path / "dataset", credentials=tmp_path / "credentials", slug="apexremote"
        )

    assert not (tmp_path / "credentials").exists() and not (tmp_path / "dataset").exists()  # nothing was touched


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_a_local_database_is_accepted(host: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo.tenants, "control_url", lambda: make_url(f"postgresql+psycopg://u:p@{host}:5440/control"))

    demo.require_local_database()
