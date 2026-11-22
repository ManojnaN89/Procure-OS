"""Setup archived, a clean live database (task 7.2, D-05): the configuration is carried row for row, test documents and
the platform's own stock movements stay in the archive, numbers restart, a new audit chain names the archive's last
hashes, and the app login cannot open the archive."""

import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from p2p.formulas.calendar import business_today
from p2p.platform import audit, company_state, cutover, db_logins, demo, numbering, routing, tenants

Owner = Callable[[str], AbstractContextManager[Session]]


@dataclass
class Cut:
    slug: str
    database: str
    archive: str
    before: dict[str, list[tuple[object, ...]]]


def _rows(db: Session, table: str) -> list[tuple[object, ...]]:
    return sorted(
        (tuple(map(str, r)) for r in db.execute(text(f"select * from {table}{cutover._where(table)}"))), key=str
    )  # noqa: S608


@pytest.fixture(scope="module")
def cut(control_db: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Cut]:
    slug = "cr" + uuid.uuid4().hex[:8]
    demo.reset_company(
        demo.company("apex"), tmp_path_factory.mktemp("apex") / "d", credentials=None, slug=slug, state="setup"
    )
    with Session(routing.engine_for(slug)) as db:  # test data made in setup
        no = numbering.next_number(db, "request", business_today(), location_code="PLT-CKN")
        db.execute(
            text("insert into request (no, ship_to_location, type, channel) values (:n, 'PLT-CKN', 'goods', 'form')"),
            {"n": no},
        )
        db.execute(
            text(
                "insert into stock_movement (id, item_code, location_code, date, type, qty, source) "
                "values ('SM-SETUP-1', 'SP-03', 'PLT-CKN', :d, 'issue', 1, 'platform')"
            ),
            {"d": business_today()},
        )
        audit.record(db, audit.SYSTEM, "test.setup", "request", no)
        db.commit()
        before = {t: _rows(db, t) for t in cutover.CARRIED}
    database = tenants.tenant_db_name(slug)
    done = cutover.archive_and_rebuild(slug, reason="cutover test", operator="Test Operator")
    yield Cut(slug, database, done.archive, before)
    routing.clear_engine_cache()
    tenants.drop_tenant(slug)


@pytest.fixture
def on(control_db: str) -> Callable[[str], AbstractContextManager[Session]]:
    from contextlib import contextmanager

    @contextmanager
    def session(database: str) -> Iterator[Session]:
        engine = create_engine(db_logins.owner_url(tenants.control_url().set(database=database)))
        try:
            with Session(engine) as s:
                yield s
        finally:
            engine.dispose()

    return session


def test_every_carried_table_is_the_same_row_for_row(cut: Cut, on: Owner) -> None:
    with on(cut.database) as live, on(cut.archive) as arch:
        for table, rows in cut.before.items():
            assert _rows(live, table) == rows, table
            assert _rows(arch, table) == rows, table


def test_test_documents_and_platform_stock_stay_in_the_archive(cut: Cut, on: Owner) -> None:
    with on(cut.database) as live, on(cut.archive) as arch:
        assert live.execute(text("select count(*) from request")).scalar() == 0
        assert arch.execute(text("select count(*) from request")).scalar() == 1
        assert live.execute(text("select distinct source from stock_movement")).scalars().all() == ["import"]
        assert arch.execute(text("select 1 from stock_movement where id = 'SM-SETUP-1'")).first() is not None
        assert live.execute(text("select count(*) from number_counter")).scalar() == 0
    assert company_state.state_of(cut.slug) == "cutover"


def test_numbers_restart_and_ids_continue(cut: Cut) -> None:
    with Session(routing.engine_for(cut.slug)) as db:
        no = numbering.next_number(db, "request", business_today(), location_code="PLT-CKN")
        top = db.execute(text("select max(id) from org_user_role")).scalar()
        new_id = db.execute(
            text("insert into org_user_role (user_code, role) values ('U01', 'requester') returning id")
        ).scalar()
        db.rollback()
    assert no.endswith("00001")
    assert new_id > top


def test_a_new_audit_chain_names_the_archive_and_both_chains_hold(cut: Cut, on: Owner) -> None:
    with on(cut.database) as live, on(cut.archive) as arch:
        first = live.execute(text("select action, after from audit_event order by id limit 1")).one()
        last = arch.execute(text("select row_hash from audit_event order by id desc limit 1")).scalar()
        rows = arch.execute(text("select count(*) from audit_event")).scalar()
        archived = arch.execute(
            text("select after->>'archive' from audit_event where action = 'company.archived'")
        ).scalar()
        for s in (live, arch):
            assert audit.verify_chain(s, "audit_event") is None
            assert audit.verify_chain(s, "setup_change") is None
    assert first[0] == "company.cutover"
    assert first[1]["archive"] == cut.archive == archived
    assert (first[1]["archive_audit_last_hash"], first[1]["archive_audit_rows"]) == (last, rows)
    with tenants.control_session() as s:
        kept = s.execute(
            text("select audit_rows, audit_last_hash from tenant_archive where database_name = :n"), {"n": cut.archive}
        ).one()
    assert tuple(kept) == (rows, last)


def test_the_app_login_cannot_open_the_archive(cut: Cut) -> None:
    engine = create_engine(db_logins.app_url(tenants.control_url().set(database=cut.archive)))
    try:
        with pytest.raises(OperationalError, match="permission denied"), engine.connect():
            pass
    finally:
        engine.dispose()


def test_a_dry_run_changes_nothing_and_a_failed_copy_leaves_setup(
    control_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    slug = "cf" + uuid.uuid4().hex[:8]
    demo.reset_company(demo.company("brightwave"), None, credentials=None, slug=slug, state="setup")
    try:
        name = tenants.tenant_db_name(slug)

        def databases() -> set[str]:
            with tenants._admin_connection() as c:
                return {
                    str(r[0])
                    for r in c.execute(text("select datname from pg_database where datname like :p"), {"p": name + "%"})
                }

        before = databases()
        done = cutover.archive_and_rebuild(slug, reason="rehearsal", operator="Test Operator", dry_run=True)
        assert done.dry_run and done.carried["supplier"] > 0
        assert databases() == before and company_state.state_of(slug) == "setup"

        def broken(db_name: str, new: str) -> None:
            raise RuntimeError("the copy broke")

        monkeypatch.setattr(cutover, "_copy", broken)
        with pytest.raises(RuntimeError, match="the copy broke"):
            cutover.archive_and_rebuild(slug, reason="rehearsal", operator="Test Operator")
        assert databases() == before and company_state.state_of(slug) == "setup"
    finally:
        tenants.drop_tenant(slug)


def test_every_company_table_is_carried_or_left_in_the_archive(cut: Cut, on: Owner) -> None:
    with on(cut.database) as live:
        tables = {str(r[0]) for r in live.execute(text("select tablename from pg_tables where schemaname = 'public'"))}
    tables.discard("alembic_version")
    assert not set(cutover.CARRIED) & set(cutover.NOT_CARRIED)
    assert tables == set(cutover.CARRIED) | set(cutover.NOT_CARRIED)
