"""A throw-away control database for one test or eval run: made at the start, dropped at the end by the run that made it.

Two runs on one Postgres must use different names (`P2P_TEST_CONTROL_DB`, `P2P_EVAL_CONTROL_DB`). A run that starts on a
name another run is still connected to stops with a message instead of dropping that database under it, and a run drops
at the end only the database it made itself (marked with a comment), never one a later run made again under that name.
"""

import os
import uuid

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url


class ScratchDatabaseInUse(RuntimeError):
    pass


class ScratchControlDb:
    def __init__(self, server_url: str, name: str) -> None:
        self.name = name
        self.url = make_url(server_url).set(database=name).render_as_string(hide_password=False)
        self._admin = create_engine(make_url(server_url).set(database="postgres"), isolation_level="AUTOCOMMIT")
        self._mark = f"p2p scratch run {os.getpid()}-{uuid.uuid4().hex[:12]}"

    def create(self) -> None:
        """Drop what a killed run left under this name (its companies too), then make the database afresh."""
        with self._admin.connect() as c:
            if self._exists(c):
                others = c.execute(
                    text("select count(*) from pg_stat_activity where datname = :n and pid <> pg_backend_pid()"),
                    {"n": self.name},
                ).scalar_one()
                if others:
                    raise ScratchDatabaseInUse(
                        f"database {self.name} is in use by another run ({others} connections). Give this run its own "
                        "names: P2P_TEST_CONTROL_DB (or P2P_EVAL_CONTROL_DB) and P2P_TENANT_DB_PREFIX."
                    )
                self._drop_all(c)
            c.execute(text(f'create database "{self.name}"'))
            c.execute(text(f"comment on database \"{self.name}\" is '{self._mark}'"))  # our own text, no quotes in it

    def exists(self) -> bool:
        with self._admin.connect() as c:
            return self._exists(c)

    def drop(self) -> None:
        """Drop the database and its companies, if it is still the one this run made."""
        with self._admin.connect() as c:
            mark = c.execute(
                text("select shobj_description(oid, 'pg_database') from pg_database where datname = :n"),
                {"n": self.name},
            ).scalar()
            if mark == self._mark:
                self._drop_all(c)
        self._admin.dispose()

    def _exists(self, c: Connection) -> bool:
        return c.execute(text("select 1 from pg_database where datname = :n"), {"n": self.name}).first() is not None

    def _drop_all(self, c: Connection) -> None:
        engine = create_engine(self.url)
        try:
            with engine.connect() as own:
                names = [r[0] for r in own.execute(text("select database_name from tenant_database"))]
        except Exception:  # noqa: BLE001 - never migrated: it lists no companies
            names = []
        finally:
            engine.dispose()
        for n in names:
            c.execute(text(f'drop database if exists "{n}" with (force)'))
        c.execute(text(f'drop database if exists "{self.name}" with (force)'))
