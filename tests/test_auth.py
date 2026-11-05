import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from p2p.main import app
from p2p.platform import tenants, users

client = TestClient(app)
PASSWORD = "correct horse battery"


def _user(role: str = "Buyer") -> tuple[str, str]:
    slug = "t" + uuid.uuid4().hex[:8]
    tenants.create_tenant(slug)
    email = f"{uuid.uuid4().hex[:8]}@example.com"
    users.create_user(slug, email, PASSWORD, "Test Person", role)
    return slug, email


def _login(email: str, password: str = PASSWORD):  # type: ignore[no-untyped-def]
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_user_logs_in_and_sees_only_their_tenant(control_db: str) -> None:
    slug, email = _user("Approver")

    r = _login(email)
    assert r.status_code == 200
    me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"})

    assert me.status_code == 200
    assert me.json() == {
        "user_id": me.json()["user_id"],
        "tenant": slug,
        "role": "Approver",
        "display_name": "Test Person",
    }


def test_wrong_email_and_wrong_password_look_the_same(control_db: str) -> None:
    _, email = _user()
    a = _login("nobody@example.com")
    b = _login(email, "wrong password!")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_five_wrong_passwords_lock_the_account(control_db: str) -> None:
    _, email = _user()
    for _ in range(5):
        assert _login(email, "wrong password!").status_code == 401

    assert _login(email, "wrong password!").status_code == 423
    assert _login(email).status_code == 423  # even the right password is refused while locked


def test_a_good_login_resets_the_failure_count(control_db: str) -> None:
    _, email = _user()
    for _ in range(4):
        _login(email, "wrong password!")
    assert _login(email).status_code == 200
    for _ in range(4):
        assert _login(email, "wrong password!").status_code == 401

    assert _login(email).status_code == 200


def test_refresh_rotates_and_a_reused_token_ends_the_session(control_db: str) -> None:
    _, email = _user()
    first = _login(email).json()

    second = client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert second.status_code == 200
    assert second.json()["refresh_token"] != first["refresh_token"]

    # the old token comes back: refused, and the new one is cancelled too
    assert client.post("/api/auth/refresh", json={"refresh_token": first["refresh_token"]}).status_code == 401
    assert (
        client.post("/api/auth/refresh", json={"refresh_token": second.json()["refresh_token"]}).status_code
        == 401
    )


def test_logout_cancels_the_refresh_token(control_db: str) -> None:
    _, email = _user()
    tokens = _login(email).json()

    assert client.post("/api/auth/logout", json={"refresh_token": tokens["refresh_token"]}).status_code == 204
    assert (
        client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    )


def test_weak_password_duplicate_email_and_bad_role_are_refused(control_db: str) -> None:
    slug, email = _user()
    with pytest.raises(ValueError, match="at least"):
        users.create_user(slug, "new@example.com", "short", "X")
    with pytest.raises(ValueError, match="already exists"):
        users.create_user(slug, email, PASSWORD, "X")
    with pytest.raises(ValueError, match="role"):
        users.create_user(slug, "other@example.com", PASSWORD, "X", "Wizard")


def test_the_lock_ends_after_fifteen_minutes(control_db: str) -> None:
    _, email = _user()
    for _ in range(5):
        _login(email, "wrong password!")
    assert _login(email).status_code == 423

    engine = tenants.control_engine()
    with engine.begin() as c:
        c.execute(
            text("update app_user set locked_until = now() - interval '1 minute' where email = :e"),
            {"e": email},
        )
    engine.dispose()

    assert _login(email).status_code == 200
