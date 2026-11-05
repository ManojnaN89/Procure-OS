import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from p2p.platform import audit, routing, tenants, users
from p2p.platform.sod import Actor


def _tenant() -> str:
    slug = "t" + uuid.uuid4().hex[:8]
    tenants.create_tenant(slug, load_reference=False)
    return slug


def _rows(slug: str, entity: str) -> list[tuple[str, ...]]:
    with Session(routing.engine_for(slug)) as db:
        result = db.execute(
            text("select actor_type, actor_id, action, entity_id from audit_event where entity = :e order by id"),
            {"e": entity},
        )
        return [tuple(r) for r in result]


def test_creating_a_user_a_role_and_a_limit_each_write_an_audit_row(control_db: str) -> None:
    slug = _tenant()
    admin = Actor("user", "1")
    uid = users.create_user(slug, "a@example.com", "correct horse battery", "A", "Approver", actor=admin)
    users.grant_role(slug, uid, "Store", location="PLT-VAD", actor=admin)
    users.set_limit(slug, uid, "po", Decimal("100000"), actor=admin)

    assert _rows(slug, "user") == [
        ("user", "1", "user.create", str(uid)),
        ("user", "1", "role.grant", str(uid)),
        ("user", "1", "limit.set", str(uid)),
    ]


def test_the_audit_row_holds_before_and_after_values_and_the_reason(control_db: str) -> None:
    slug = _tenant()
    with Session(routing.engine_for(slug)) as db:
        audit.record(
            db,
            Actor("agent", "intake"),
            "request.update",
            "request",
            "REQ-1",
            before={"qty": Decimal("5")},
            after={"qty": Decimal("7")},
            reason="supplier MOQ",
            rule_version="approval@3",
            model="gpt-oss-120b",
            prompt_version="p1",
        )
        db.commit()
        row = db.execute(
            text("select before->>'qty', after->>'qty', reason, rule_version, model, prompt_version from audit_event")
        ).one()
    assert tuple(row) == ("5", "7", "supplier MOQ", "approval@3", "gpt-oss-120b", "p1")


def test_audit_rows_roll_back_with_the_write_they_describe(control_db: str) -> None:
    slug = _tenant()
    with Session(routing.engine_for(slug)) as db:
        audit.record(db, audit.SYSTEM, "x.create", "x", "1")
        db.rollback()
    assert _rows(slug, "x") == []


@pytest.mark.parametrize(
    "statement",
    ["update audit_event set reason = 'changed'", "delete from audit_event", "truncate audit_event"],
)
def test_update_delete_and_truncate_are_refused(control_db: str, statement: str) -> None:
    slug = _tenant()
    with Session(routing.engine_for(slug)) as db:
        audit.record(db, audit.SYSTEM, "x.create", "x", "1")
        db.commit()
        with pytest.raises(DBAPIError, match="insert only"):
            db.execute(text(statement))
        db.rollback()
    assert len(_rows(slug, "x")) == 1
