"""D-25: the audit log and the setup history are hash-chained and insert only (migration 0059)."""

import threading
import uuid
from collections.abc import Iterator

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from p2p.platform import audit, routing, tenants


@pytest.fixture
def slug(control_db: str) -> str:
    name = "t" + uuid.uuid4().hex[:8]
    tenants.create_tenant(name, load_reference=False)
    return name


@pytest.fixture
def db(slug: str) -> Iterator[Session]:
    with Session(routing.engine_for(slug)) as session:
        yield session


def _write(db: Session, n: int, entity: str = "x") -> None:
    for i in range(n):
        audit.record(db, audit.SYSTEM, f"{entity}.create", entity, i, after={"n": i})
    db.commit()


def _setup_change(db: Session, key: str) -> None:
    db.execute(
        text(
            "insert into setup_change (by_user, area, key, new_value, reason) values ('1', 'policy', :k, '\"1\"', 'r')"
        ),
        {"k": key},
    )


def _owner_edit(db: Session, statement: str) -> None:
    """What only the database owner can do: switch the triggers off and change a written row."""
    db.execute(text("set local session_replication_role = replica"))
    db.execute(text(statement))
    db.commit()


def test_each_audit_row_links_to_the_one_before_and_the_chain_verifies(db: Session) -> None:
    # given rows written through the one audit service
    _write(db, 3)
    rows = db.execute(text("select prev_hash, row_hash from audit_event order by id")).all()
    # then the first links to the start, every next one to the row before, and the whole chain checks out
    assert rows[0].prev_hash == "0" * 64
    assert [r.prev_hash for r in rows[1:]] == [r.row_hash for r in rows[:-1]]
    assert len({r.row_hash for r in rows}) == len(rows)
    assert audit.verify_chain(db) is None


def test_a_row_changed_behind_the_app_is_found_by_its_id(db: Session) -> None:
    _write(db, 4)
    ids = db.scalars(text("select id from audit_event order by id")).all()
    # when the owner turns the triggers off and changes the third row
    _owner_edit(db, f"update audit_event set reason = 'edited' where id = {ids[2]}")
    # then the check names that row
    broken = audit.verify_chain(db)
    assert broken is not None and (broken.id, broken.reason) == (ids[2], "the row was changed after it was written")


def test_a_row_taken_out_is_found_at_the_row_after_it(db: Session) -> None:
    _write(db, 4)
    ids = db.scalars(text("select id from audit_event order by id")).all()
    _owner_edit(db, f"delete from audit_event where id = {ids[1]}")
    broken = audit.verify_chain(db)
    assert broken is not None and (broken.id, broken.reason) == (ids[2], "the row before it is missing or was changed")


def test_writers_at_the_same_time_never_fork_the_chain(slug: str, db: Session) -> None:
    # given eight transactions writing audit rows at the same time, each with several rows
    start = threading.Barrier(8)
    errors: list[BaseException] = []

    def writer(n: int) -> None:
        try:
            with Session(routing.engine_for(slug)) as own:
                start.wait()
                for i in range(5):
                    audit.record(own, audit.SYSTEM, "w.create", f"w{n}", i)
                    own.flush()
                own.commit()
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert errors == []
    # then all 40 rows are one chain: no two rows link to the same row, and it verifies
    prevs = db.scalars(text("select prev_hash from audit_event")).all()
    assert len(prevs) == 40 and len(set(prevs)) == 40
    assert audit.verify_chain(db) is None


def test_a_rolled_back_write_leaves_the_chain_whole(db: Session) -> None:
    _write(db, 2)
    audit.record(db, audit.SYSTEM, "x.create", "x", "gone")
    db.rollback()
    _write(db, 2)
    assert audit.verify_chain(db) is None


@pytest.mark.parametrize(
    "statement",
    ["update setup_change set reason = 'changed'", "delete from setup_change", "truncate setup_change"],
)
def test_the_setup_history_refuses_update_delete_and_truncate(db: Session, statement: str) -> None:
    _setup_change(db, "budget_policy")
    db.commit()
    with pytest.raises(DBAPIError, match="setup_change is insert only"):
        db.execute(text(statement))
    db.rollback()
    assert db.scalar(text("select count(*) from setup_change")) == 1


def test_the_setup_history_is_chained_too(db: Session) -> None:
    for key in ("a", "b", "c"):
        _setup_change(db, key)
    db.commit()
    assert audit.verify_chain(db, "setup_change") is None
    first = db.scalar(text("select min(id) from setup_change"))
    _owner_edit(db, f"update setup_change set reason = 'other' where id = {first}")
    broken = audit.verify_chain(db, "setup_change")
    assert broken is not None and broken.id == first


def test_only_chained_tables_can_be_verified(db: Session) -> None:
    with pytest.raises(ValueError, match="no hash chain"):
        audit.verify_chain(db, "po")


def test_rows_written_before_the_migration_are_linked_by_it(slug: str) -> None:
    # given a company database one step before the chain, with audit and setup rows in it
    url = routing.engine_for(slug).url.render_as_string(hide_password=False)
    config = tenants._config("tenant", url)
    command.downgrade(config, "-1")
    engine = routing.engine_for(slug)
    with engine.begin() as conn:
        for i in range(3):
            conn.execute(
                text(
                    "insert into audit_event (actor_type, actor_id, action, entity, entity_id) values ('service', 's', 'x', 'x', :i)"
                ),
                {"i": str(i)},
            )
            conn.execute(
                text("insert into setup_change (by_user, area, key, reason) values ('1', 'policy', :k, 'r')"),
                {"k": str(i)},
            )
    # when it is migrated
    command.upgrade(config, "head")
    # then the old rows form the start of the chain and new rows carry on from them
    with Session(engine) as db:
        _write(db, 1)
        assert audit.verify_chain(db) is None
        assert audit.verify_chain(db, "setup_change") is None
        assert db.scalar(text("select count(*) from audit_event where row_hash is null")) == 0
