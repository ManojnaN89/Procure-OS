"""A user is a login (control database) and a profile (the company's): written so that a failure never leaves a usable
login without a profile (DF-36), with a repair for a process that died half way; and only the system makes an Admin."""

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.main import app
from p2p.platform import audit, ratelimit, routing, tenants, users
from p2p.platform.sod import Actor
from p2p.platform.tokens import create_access_token

client = TestClient(app)
PASSWORD = "correct horse battery"


@pytest.fixture
def slug(control_db: str) -> Iterator[str]:
    name = "uc" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(name, load_reference=False).ok
    yield name
    routing.clear_engine_cache()
    tenants.drop_tenant(name)


def _logins(slug: str) -> dict[str, Any]:
    with tenants.control_session() as s:
        return {
            str(r[0]): r[1]
            for r in s.execute(
                text(
                    "select u.email, u.provisioned_at from app_user u join tenant t on t.id = u.tenant_id where t.slug = :s"
                ),
                {"s": slug},
            )
        }


def _sign_in(email: str, password: str = PASSWORD) -> Any:
    ratelimit.reset()
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_a_new_user_signs_in_once_both_databases_hold_it(slug: str) -> None:
    uid = users.create_user(slug, "made@example.com", PASSWORD, "Made", "Buyer")
    assert _logins(slug)["made@example.com"] is not None
    with Session(routing.engine_for(slug)) as db:
        assert db.execute(text("select role from user_profile where user_id = :u"), {"u": uid}).scalar() == "Buyer"
    assert _sign_in("made@example.com").status_code == 200


def test_when_the_company_step_fails_no_login_and_no_profile_are_left(
    slug: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = audit.record

    def failing(db: Session, actor: Actor, action: str, *a: Any, **k: Any) -> None:
        if action == "user.create":
            raise RuntimeError("the company database is gone")
        real(db, actor, action, *a, **k)

    monkeypatch.setattr(audit, "record", failing)
    with pytest.raises(RuntimeError):
        users.create_user(slug, "lost@example.com", PASSWORD, "Lost", "Requester")
    assert "lost@example.com" not in _logins(slug)
    with Session(routing.engine_for(slug)) as db:
        assert db.execute(text("select count(*) from user_profile where display_name = 'Lost'")).scalar() == 0


def test_a_login_whose_last_step_never_ran_is_refused_like_a_wrong_password_until_repaired(slug: str) -> None:
    uid = users.create_user(slug, "half@example.com", PASSWORD, "Half", "Requester")
    with tenants.control_engine().begin() as c:  # the process died before the third step
        c.execute(text("update app_user set provisioned_at = null where id = :u"), {"u": uid})
    wrong = _sign_in("half@example.com", "not the password")
    half = _sign_in("half@example.com")
    assert half.status_code == 401 and half.json() == wrong.json()

    with tenants.control_engine().begin() as c:  # logins without a profile, one old and one being made right now
        tid = c.execute(text("select id from tenant where slug = :s"), {"s": slug}).scalar_one()
        for name, minutes in (("old", 11), ("young", 2)):
            c.execute(
                text(
                    "insert into app_user (tenant_id, email, password_hash, created_at) "
                    "values (:t, :e, 'x', now() - make_interval(mins => :m))"
                ),
                {"t": tid, "e": f"{name}@example.com", "m": minutes},
            )
    fixes = {f.email: f.fixed for f in users.repair(slug)}
    assert fixes == {"half@example.com": "provisioned", "old@example.com": "login_removed"}
    assert _sign_in("half@example.com").status_code == 200
    left = _logins(slug)
    assert "old@example.com" not in left and "young@example.com" in left
    with Session(routing.engine_for(slug)) as db:
        done = db.execute(
            text("select after->>'fixed' from audit_event where action = 'user.repair' order by id")
        ).all()
    assert sorted(r[0] for r in done) == ["login_removed", "provisioned"]
    assert users.repair(slug) == []  # nothing left to repair


def test_only_the_system_makes_an_administrator(slug: str) -> None:
    admin = users.create_user(slug, "first@example.com", PASSWORD, "First", "Admin")  # the system (CLI, demo)
    with pytest.raises(ValueError, match="another administrator approves it"):
        users.create_user(slug, "second@example.com", PASSWORD, "Second", "Admin", actor=Actor("user", str(admin)))
    headers = {"Authorization": f"Bearer {create_access_token(admin, slug, 'Admin')}"}
    r = client.post(
        "/api/admin/users",
        json={"email": "twin@example.com", "password": PASSWORD, "display_name": "Twin", "role": "Admin"},
        headers=headers,
    )
    assert r.status_code in (403, 422)  # block 0c answers 403 before the service is asked
    assert "twin@example.com" not in _logins(slug)
