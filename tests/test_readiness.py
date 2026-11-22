"""Readiness to cut over and go live (task 7.3): every check passing on the demo company and failing on a planted gap,
separation coverage per maker, the five duties as a matching, and the grep tests that keep the checks in step with
the code."""

import re
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.main import app
from p2p.platform import demo, readiness, routing, tenants, users
from p2p.platform.tokens import create_access_token

client = TestClient(app)
BACKEND = Path(readiness.__file__).resolve().parents[1]  # backend/p2p


@pytest.fixture(scope="module")
def apex(control_db: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    slug = "rd" + uuid.uuid4().hex[:8]
    demo.reset_company(
        demo.company("apex"), tmp_path_factory.mktemp("apex") / "d", credentials=None, slug=slug, state="setup"
    )
    yield slug
    routing.clear_engine_cache()
    tenants.drop_tenant(slug)


def _failed(result: readiness.Readiness, severity: str = "blocker") -> set[str]:
    return {c.code for c in result.checks if not c.ok and c.severity == severity}


def _problems(result: readiness.Readiness, code: str) -> list[str]:
    return [p for c in result.checks if c.code == code for p in c.problems]


def test_the_demo_company_is_ready_to_cut_over_with_warnings_only(apex: str) -> None:
    with Session(routing.engine_for(apex)) as db:
        before = db.execute(text("select count(*) from audit_event")).scalar()
        setup = readiness.check(db, apex, group="setup")
        every = readiness.check(db, apex)
        after = db.execute(text("select count(*) from audit_event")).scalar()
    assert setup.ready and not _failed(setup)
    assert _failed(every) == {"cutover_import"} and not every.ready
    assert before == after  # reading is not audited
    banks = _problems(every, "supplier_bank")
    assert "SUP-001 ****0001 verified by a file, not by a person (D-01)" in banks
    codes = [c.code for c in every.checks]
    assert {"masters", "admins", "approval_bands", "approvers", "separation", "duties", "pending_changes"} <= set(codes)


def test_the_api_answers_administrators_and_auditors_only(apex: str) -> None:
    admin = users.user_id_by_email(apex, "admin@apexpumps.example")
    store = users.user_id_by_email(apex, "store@apexpumps.example")
    assert admin and store
    as_admin = {"Authorization": f"Bearer {create_access_token(admin, apex, 'Admin')}"}
    r = client.get("/api/company/readiness", params={"group": "setup"}, headers=as_admin)
    assert r.status_code == 200 and r.json()["ready"] is True and r.json()["state"] == "setup"
    assert client.get("/api/company/readiness", params={"group": "nope"}, headers=as_admin).status_code == 422
    as_store = {"Authorization": f"Bearer {create_access_token(store, apex, 'Store')}"}
    assert client.get("/api/company/readiness", headers=as_store).status_code == 403


def test_a_maker_who_is_their_own_only_checker_fails_separation(apex: str) -> None:
    """Finance proposes payment runs; with the payment run rule naming Finance too, nobody else may approve them."""
    with Session(routing.engine_for(apex)) as db:
        db.execute(text("update approver_role set value = 'Finance Manager' where role = 'director'"))
        db.execute(
            text(
                "insert into role_permission (role, action) values ('Finance', 'payment.approve') on conflict do nothing"
            )
        )
        found = readiness.check(db, apex, group="setup")
        db.rollback()
    sep = _problems(found, "separation")
    assert any(p.startswith("Anil Mehta could not get a payment run approved done (SEP-14)") for p in sep), sep
    assert "separation" in _failed(found)


def test_nobody_to_check_a_control_and_a_control_not_in_use(apex: str) -> None:
    with Session(routing.engine_for(apex)) as db:
        db.execute(text("delete from role_permission where action = 'supplier.verify_bank'"))
        db.execute(text("delete from role_permission where action = 'benchmark.enter'"))
        found = readiness.check(db, apex, group="setup")
        db.rollback()
    sep = _problems(found, "separation")
    assert "nobody may check a supplier's bank account verified (SEP-06)" in sep
    assert not any("SEP-10" in p for p in sep)  # nobody enters benchmarks: not in use


def test_one_administrator_is_not_enough_even_with_the_role_twice(control_db: str) -> None:
    slug = "ra" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug, load_reference=False).ok
    try:
        uid = users.create_user(slug, f"solo.{slug}@example.com", "correct horse battery", "Solo", "Admin")
        with Session(routing.engine_for(slug)) as db:
            db.execute(
                text("insert into user_role (user_id, role, scope_location) values (:u, 'Admin', 'ANY')"), {"u": uid}
            )
            found = readiness.check(db, slug, group="setup")
        assert {"admins", "masters", "company_bank", "duties"} <= _failed(found)
        assert any("only Solo; two administrators are needed" in p for p in _problems(found, "admins"))
    finally:
        tenants.drop_tenant(slug)


def test_the_five_duties_are_a_matching_not_five_counts() -> None:
    assert readiness._matched([{1}, {2}, {3}, {4}, {5}])
    assert readiness._matched([{1, 2}, {1}, {2, 3}, {3, 4}, {4, 5}])
    assert not readiness._matched([{1, 2}, {1, 2}, {3}, {4}, {3, 4}])  # five permissions held by four people
    assert not readiness._matched([set(), {1}, {2}, {3}, {4}])


def _python_files() -> list[Path]:
    return [p for p in BACKEND.rglob("*.py") if "migrations" not in p.parts]


def test_every_numbered_document_type_is_checked_by_readiness() -> None:
    found = set()
    for path in _python_files():
        found |= set(re.findall(r"next_number\(\s*\w+,\s*\"(\w+)\"", path.read_text(encoding="utf-8")))
    assert found == set(readiness.DOCUMENT_TYPES)


def test_every_segregation_refusal_is_named_by_a_readiness_rule() -> None:
    named = {m for rule in readiness.SEPARATION for m in rule.enforced_in} | set(readiness.COVERED_ELSEWHERE)
    refusing = {
        path.relative_to(BACKEND).as_posix()
        for path in _python_files()
        if "segregation of duties:" in path.read_text(encoding="utf-8")
    }
    assert refusing <= named, sorted(refusing - named)
    assert all((BACKEND / m).is_file() for m in named)
