"""Company states (task 7.1, D-04): setup, cutover, live, suspended; who may sign in; what runs; go-live by two
administrators. The demo company is made in setup and walked through every state."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import email_links
from p2p.main import app
from p2p.platform import company_state, cutover, demo, ratelimit, routing, tenants, users, worker
from p2p.platform.company_state import StateError, operator
from p2p.platform.tokens import create_access_token

client = TestClient(app)
DOMAIN = "apexpumps.example"
OPS = operator("Test Operator")


@dataclass
class Apex:
    slug: str
    dataset: Path

    def uid(self, login: str) -> int:
        found = users.user_id_by_email(self.slug, f"{login}@{DOMAIN}")
        assert found is not None
        return found

    def auth(self, login: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {create_access_token(self.uid(login), self.slug, 'x')}"}

    def sign_in(self, login: str) -> tuple[int, dict[str, str]]:
        ratelimit.reset()
        r = client.post("/api/auth/login", json={"email": f"{login}@{DOMAIN}", "password": demo.DEV_PASSWORD})
        return r.status_code, r.json()


@pytest.fixture(scope="module")
def apex(control_db: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Apex]:
    slug = "st" + uuid.uuid4().hex[:8]
    dataset = tmp_path_factory.mktemp("apex") / "dataset"
    demo.reset_company(demo.company("apex"), dataset, credentials=None, slug=slug, state="setup")
    yield Apex(slug, dataset)
    routing.clear_engine_cache()
    tenants.drop_tenant(slug)


def _moves(slug: str) -> list[tuple[str, str]]:
    with tenants.control_session() as s:
        return [
            (str(r[0]), str(r[1]))
            for r in s.execute(
                text(
                    "select c.from_state, c.to_state from tenant_state_change c join tenant t on t.id = c.tenant_id "
                    "where t.slug = :s order by c.id"
                ),
                {"s": slug},
            )
        ]


def test_a_new_company_starts_in_setup_and_refuses_moves_the_table_does_not_allow(control_db: str) -> None:
    slug = "sn" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug, load_reference=False).ok
    try:
        assert company_state.state_of(slug) == "setup"
        with pytest.raises(StateError, match="setup -> setup is not allowed"):
            company_state.move(slug, "setup", actor=OPS, reason="again")
        with pytest.raises(StateError, match="setup -> live is not allowed"):
            company_state.move(slug, "live", actor=OPS, reason="skip the cutover")
        with pytest.raises(StateError, match="a reason is needed"):
            company_state.move(slug, "suspended", actor=OPS, reason=" ", kind="security")
        with pytest.raises(StateError, match="commercial or security"):
            company_state.move(slug, "suspended", actor=OPS, reason="unpaid")
        with pytest.raises(StateError, match="not suspended"):
            company_state.resume(slug, actor=OPS, reason="nothing to resume")
        assert _moves(slug) == []

        company_state.move(slug, "suspended", actor=OPS, reason="unpaid", kind="commercial")
        with tenants.control_session() as s:
            row = s.execute(
                text("select state, state_before_suspension from tenant where slug = :s"), {"s": slug}
            ).one()
        assert tuple(row) == ("suspended", "setup")
        with pytest.raises(StateError, match="suspended -> cutover is not allowed"):
            company_state.move(slug, "cutover", actor=OPS, reason="not where it was")
        company_state.resume(slug, actor=OPS, reason="paid")
        assert company_state.state_of(slug) == "setup"
        assert _moves(slug) == [("setup", "suspended"), ("suspended", "setup")]
        with Session(routing.engine_for(slug)) as db:
            trail = db.execute(
                text(
                    "select before->>'state', after->>'state', reason from audit_event where action = 'company.state' order by id"
                )
            ).all()
        assert [tuple(t) for t in trail] == [("setup", "suspended", "unpaid"), ("suspended", "setup", "paid")]
    finally:
        tenants.drop_tenant(slug)


def test_a_live_company_never_goes_back(control_db: str) -> None:
    slug = "sl" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug, load_reference=False).ok
    try:
        company_state.move(slug, "live", actor=OPS, reason="a demo company", path="demo")
        for to in ("setup", "cutover"):
            with pytest.raises(StateError, match="a live company does not go back"):
                company_state.move(slug, to, actor=OPS, reason="undo")
        assert company_state.state_of(slug) == "live"
    finally:
        tenants.drop_tenant(slug)


def test_setup_lets_everyone_in_and_cutover_only_those_who_load_or_administer(apex: Apex) -> None:
    for login in ("store", "production", "admin", "finance"):
        assert apex.sign_in(login)[0] == 200
    me = client.get("/api/auth/me", headers=apex.auth("store"))
    assert me.json()["company_state"] == "setup"
    store_token = apex.auth("store")
    early = client.post("/api/company/go-live", json={"reason": "too early"}, headers=apex.auth("admin"))
    assert early.status_code == 409 and "only a company in cutover goes live" in early.json()["detail"]

    done = cutover.archive_and_rebuild(apex.slug, reason="rehearsal", operator="Test Operator")
    assert done.archive and company_state.state_of(apex.slug) == "cutover"

    for login, status in (("store", 403), ("production", 403), ("admin", 200), ("admin2", 200), ("finance", 200)):
        code, body = apex.sign_in(login)
        assert code == status, (login, body)
        if code == 403:
            assert body["detail"] == company_state.CUTOVER_SIGN_IN
    refused = client.get("/api/auth/me", headers=store_token)
    assert refused.status_code == 403  # a token from setup stops at its next request

    job = worker.enqueue(apex.slug, "test.state_probe", {})
    worker.run_once(tenant=apex.slug)
    with tenants.control_session() as s:
        assert s.execute(text("select status from job where id = :i"), {"i": job}).scalar() == "queued"


def test_go_live_needs_a_second_administrator_and_readiness(apex: Apex) -> None:
    assert company_state.state_of(apex.slug) == "cutover"
    folder = apex.dataset / "cutover"
    from p2p.platform import cutover_import

    with Session(routing.engine_for(apex.slug)) as db:
        report = cutover_import.import_cutover(db, folder, user_id=apex.uid("finance"), dry_run=False)
    assert not report.rejected and report.control_ok

    assert client.post("/api/company/go-live", json={"reason": ""}, headers=apex.auth("admin")).status_code == 422
    asked = client.post("/api/company/go-live", json={"reason": "ready"}, headers=apex.auth("admin"))
    assert asked.status_code == 202, asked.text
    body = asked.json()
    assert (body["status"], body["old"], body["new"]) == ("waiting", "cutover", "live")
    again = client.post("/api/company/go-live", json={"reason": "me too"}, headers=apex.auth("admin2"))
    assert again.status_code == 409
    pid = body["proposal_id"]
    assert client.post(f"/api/admin/proposals/{pid}/approve", json={}, headers=apex.auth("admin")).status_code == 403
    assert company_state.state_of(apex.slug) == "cutover"
    done = client.post(f"/api/admin/proposals/{pid}/approve", json={}, headers=apex.auth("admin2"))
    assert done.status_code == 200 and done.json()["execution"] == "ok", done.text
    assert company_state.state_of(apex.slug) == "live"
    with tenants.control_session() as s:
        row = s.execute(
            text(
                "select c.by_kind, c.by_id, c.approved_by from tenant_state_change c join tenant t on t.id = c.tenant_id "
                "where t.slug = :s and c.to_state = 'live'"
            ),
            {"s": apex.slug},
        ).one()
    assert tuple(row) == ("user", str(apex.uid("admin")), str(apex.uid("admin2")))
    with Session(routing.engine_for(apex.slug)) as db:
        history = db.execute(text("select area, key, approved_by from setup_change where area = 'company'")).all()
        assert [tuple(h) for h in history] == [("company", "state", str(apex.uid("admin2")))]
        assert db.execute(text("select count(*) from audit_event where action = 'company.readiness'")).scalar() == 1
    assert apex.sign_in("store")[0] == 200
    worker.run_once(tenant=apex.slug)  # the job left from cutover runs now


def test_a_suspended_company_signs_nobody_in_and_its_links_and_jobs_wait(apex: Apex) -> None:
    assert company_state.state_of(apex.slug) == "live"
    token = apex.auth("admin")
    other = "so" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other, load_reference=False).ok
    try:
        company_state.move(apex.slug, "suspended", actor=OPS, reason="a leaked password", kind="security")
        for login in ("admin", "store"):
            code, body = apex.sign_in(login)
            assert code == 403 and body["detail"] == company_state.SUSPENDED
        assert client.get("/api/auth/me", headers=token).status_code == 403
        link = email_links.make(apex.slug, "REQ-X", 1, 1, apex.uid("director"))
        page = client.get(f"/approve/{link}")
        assert page.status_code == 403 and company_state.NOT_TAKING_ACTIONS in page.text
        job = worker.enqueue(apex.slug, "test.state_probe", {})
        worker.run_once(tenant=apex.slug)
        with tenants.control_session() as s:
            assert s.execute(text("select status from job where id = :i"), {"i": job}).scalar() == "queued"
        assert company_state.state_of(other) == "setup"  # another company is not touched
        company_state.resume(apex.slug, actor=OPS, reason="password changed")
        assert company_state.state_of(apex.slug) == "live"
        assert client.get("/api/auth/me", headers=token).status_code == 200
    finally:
        tenants.drop_tenant(other)
    assert _moves(apex.slug) == [
        ("setup", "cutover"),
        ("cutover", "live"),
        ("live", "suspended"),
        ("suspended", "live"),
    ]


@worker.register("test.state_probe")
def _probe(ctx: worker.JobContext) -> None:
    return None
