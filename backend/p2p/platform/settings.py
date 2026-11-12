"""Reading a company setting: the organisation's policy value, else the system switch, else the default in code.

The values are changed on the company setup screen, which keeps every change (who, when, old and new value, why) in the
setup history, so the value used by a decision at any time can be read back.
"""

from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.orm import Session


def value(db: Session, key: str, default: str) -> str:
    for table in ("org_setting", "app_setting"):
        found = db.execute(text(f"select value from {table} where key = :k"), {"k": key}).scalar()  # noqa: S608
        if found not in (None, ""):
            return str(found)
    return default


def number(db: Session, key: str, default: str) -> Decimal:
    """A numeric setting; a value that is not a number falls back to the default."""
    try:
        return Decimal(value(db, key, default))
    except InvalidOperation:
        return Decimal(default)
