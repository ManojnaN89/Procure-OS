"""One small helper shared by the API routers and agents: run a query and get the rows as plain dictionaries."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def rows(db: Session, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in db.execute(text(sql), params)]
