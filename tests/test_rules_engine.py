import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.platform import routing, tenants
from p2p.platform.sod import Actor
from p2p.rules import engine
from p2p.rules.engine import RuleError

ADMIN = Actor("user", "1")


def approval_table(first_limit: int = 50000, second_limit: int = 500000) -> dict[str, Any]:
    """Value band to approver role; the last row catches everything above."""
    rules = [
        {"_id": "r1", "i1": f"<= {first_limit}", "o1": '"manager"'},
        {"_id": "r2", "i1": f"<= {second_limit}", "o1": '"dept_head"'},
        {"_id": "r3", "i1": "", "o1": '"cfo"'},
    ]
    return {
        "contentType": "application/vnd.gorules.decision",
        "nodes": [
            {"id": "in", "type": "inputNode", "name": "request", "content": {}},
            {
                "id": "t",
                "type": "decisionTableNode",
                "name": "approval",
                "content": {
                    "hitPolicy": "first",
                    "inputs": [{"id": "i1", "name": "Value", "field": "value"}],
                    "outputs": [{"id": "o1", "name": "Approver", "field": "approver"}],
                    "rules": rules,
                },
            },
            {"id": "out", "type": "outputNode", "name": "response", "content": {}},
        ],
        "edges": [
            {"id": "e1", "sourceId": "in", "targetId": "t", "type": "edge"},
            {"id": "e2", "sourceId": "t", "targetId": "out", "type": "edge"},
        ],
    }


@pytest.fixture
def db(control_db: str) -> Session:
    slug = "t" + uuid.uuid4().hex[:8]
    tenants.create_tenant(slug, load_reference=False)
    with Session(routing.engine_for(slug)) as session:
        yield session


def _approver(db: Session, value: Any) -> str:
    return str(engine.evaluate(db, "approval", {"value": value}, ADMIN).result["approver"])


def test_a_published_approval_table_picks_the_right_approver(db: Session) -> None:
    engine.save_draft(db, "approval", approval_table(), ADMIN)
    engine.publish(db, "approval", ADMIN)

    assert _approver(db, 10000) == "manager"
    assert _approver(db, 50000) == "manager"  # the band limit itself belongs to the lower band
    assert _approver(db, 50001) == "dept_head"
    assert _approver(db, Decimal("500000.00")) == "dept_head"
    assert _approver(db, 500001) == "cfo"


def test_publishing_version_2_leaves_version_1_reproducible(db: Session) -> None:
    engine.save_draft(db, "approval", approval_table(), ADMIN)
    assert engine.publish(db, "approval", ADMIN) == 1
    old = engine.evaluate(db, "approval", {"value": 60000}, ADMIN)
    assert (old.version, old.result["approver"]) == (1, "dept_head")

    engine.save_draft(db, "approval", approval_table(first_limit=100000), ADMIN, note="manager limit raised")
    assert engine.publish(db, "approval", ADMIN) == 2

    new = engine.evaluate(db, "approval", {"value": 60000}, ADMIN)
    assert (new.version, new.result["approver"]) == (2, "manager")  # the live table changed
    assert engine.reproduce(db, old.id) == {"approver": "dept_head"}  # the old decision still reproduces
    statuses = dict(db.execute(text("select version, status from rule_table where key = 'approval'")).all())
    assert statuses == {1: "archived", 2: "published"}


def test_every_evaluation_stores_its_input_output_and_trace(db: Session) -> None:
    engine.save_draft(db, "approval", approval_table(), ADMIN)
    engine.publish(db, "approval", ADMIN)
    ev = engine.evaluate(db, "approval", {"value": 70000}, Actor("agent", "intake"), case_ref="REQ-7")

    row = db.execute(
        text(
            "select input->>'value', output->>'approver', trace is not null, actor, case_ref from rule_evaluation where id = :i"
        ),
        {"i": ev.id},
    ).one()
    assert tuple(row) == ("70000", "dept_head", True, "agent:intake", "REQ-7")


def test_a_draft_is_not_used_until_it_is_published(db: Session) -> None:
    engine.save_draft(db, "approval", approval_table(), ADMIN)
    with pytest.raises(RuleError, match="published"):
        engine.evaluate(db, "approval", {"value": 1}, ADMIN)

    engine.publish(db, "approval", ADMIN)
    engine.save_draft(db, "approval", approval_table(first_limit=1), ADMIN)  # a new draft sits beside the live version
    assert _approver(db, 10000) == "manager"


def test_saving_a_draft_twice_replaces_it_instead_of_creating_versions(db: Session) -> None:
    assert engine.save_draft(db, "approval", approval_table(), ADMIN) == 1
    assert engine.save_draft(db, "approval", approval_table(first_limit=10), ADMIN) == 1
    assert db.execute(text("select count(*) from rule_table")).scalar_one() == 1


def test_a_broken_table_and_a_missing_draft_are_refused(db: Session) -> None:
    with pytest.raises(RuleError, match="valid"):
        engine.save_draft(db, "bad", {"nodes": "nonsense"}, ADMIN)
    with pytest.raises(RuleError, match="no draft"):
        engine.publish(db, "approval", ADMIN)
    with pytest.raises(RuleError, match="published"):
        engine.evaluate(db, "approval", {"value": 1}, ADMIN)


def test_saving_and_publishing_a_table_are_audited_with_the_rule_version(db: Session) -> None:
    engine.save_draft(db, "approval", approval_table(), ADMIN, note="first table")
    engine.publish(db, "approval", ADMIN, reason="go live")

    rows = db.execute(text("select action, entity_id, rule_version, reason from audit_event order by id")).all()
    assert [tuple(r) for r in rows] == [
        ("rule.draft_create", "approval@1", None, "first table"),
        ("rule.publish", "approval@1", "approval@1", "go live"),
    ]
