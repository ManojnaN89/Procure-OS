import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import tenants
from p2p.platform.routing import tenant_session
from p2p.platform.tokens import ALGORITHM, create_access_token

app = FastAPI()


@app.get("/notes")
def notes(db: Annotated[Session, Depends(tenant_session)]) -> list[str]:
    return [r[0] for r in db.execute(text("select body from note order by id"))]


def _tenant_with_note(body: str) -> str:
    slug = "t" + uuid.uuid4().hex[:8]
    tenants.create_tenant(slug, load_reference=False)
    owner = tenants.owner_engine(slug)  # only the owner creates a table; the app reads it by its default privileges
    with owner.begin() as c:
        c.execute(text("create table note (id serial primary key, body text)"))
    owner.dispose()
    engine = tenants.tenant_engine(slug)
    with engine.begin() as c:
        c.execute(text("insert into note (body) values (:b)"), {"b": body})
    engine.dispose()
    return slug


def _get(token: str | None = None) -> tuple[int, object]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = TestClient(app).get("/notes", headers=headers)
    return r.status_code, r.json()


def test_a_request_for_tenant_a_never_reads_tenant_b_data(control_db: str) -> None:
    a = _tenant_with_note("secret of A")
    b = _tenant_with_note("secret of B")

    code_a, body_a = _get(create_access_token(1, a, "Buyer"))
    code_b, body_b = _get(create_access_token(1, b, "Buyer"))

    assert (code_a, body_a) == (200, ["secret of A"])
    assert (code_b, body_b) == (200, ["secret of B"])


def test_no_token_means_no_database_session(control_db: str) -> None:
    assert _get()[0] == 401
    assert _get("not-a-token")[0] == 401


def test_a_forged_or_expired_token_is_refused(control_db: str) -> None:
    slug = _tenant_with_note("x")
    forged = jwt.encode(
        {"sub": "1", "tenant": slug, "role": "Admin", "exp": datetime.now(UTC) + timedelta(minutes=5)},
        "some-other-secret-that-is-long-enough",
        algorithm=ALGORITHM,
    )
    expired = create_access_token(1, slug, "Buyer", minutes=-1)

    assert _get(forged)[0] == 401
    assert _get(expired)[0] == 401


def test_a_token_for_an_unknown_tenant_is_refused(control_db: str) -> None:
    assert _get(create_access_token(1, "doesnotexist", "Buyer"))[0] == 403
