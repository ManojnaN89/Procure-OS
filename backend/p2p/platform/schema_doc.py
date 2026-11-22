"""The database as built, written as markdown: every table and column of the control database and of a tenant database.

The hand-written data dictionary describes the data the platform is designed around; this is what the migrations actually
make. `scripts/gen_schema_doc.py` writes `docs/database-schema.md`, and a test fails when the file is out of date.
"""

from sqlalchemy import Engine, text

_COLUMNS = text(
    "select c.table_name, c.column_name, c.data_type, c.is_nullable from information_schema.columns c "
    "join information_schema.tables t on t.table_schema = c.table_schema and t.table_name = c.table_name "
    "and t.table_type = 'BASE TABLE' "
    "where c.table_schema = 'public' and c.table_name <> 'alembic_version' order by c.table_name, c.ordinal_position"
)
# from the catalogue: information_schema hides the keys of a table the reader may only select from (the app login)
_KEYS = text(
    "select c.relname, a.attname from pg_index i join pg_class c on c.oid = i.indrelid "
    "join pg_namespace n on n.oid = c.relnamespace join pg_attribute a on a.attrelid = c.oid and a.attnum = any(i.indkey) "
    "where i.indisprimary and n.nspname = 'public'"
)
_TYPES = {
    "character varying": "text",
    "integer": "int",
    "numeric": "num",
    "timestamp with time zone": "timestamptz",
    "timestamp without time zone": "timestamp",
    "boolean": "bool",
}

HEADER = """# Database schema (generated)

Every table and column the migrations make, one line per table. **Do not edit by hand:** run `python scripts/gen_schema_doc.py`
after a migration (a test fails when this file is out of date). The meaning of the data is in `data-dictionary.md`.

Reading a line: `name*` is part of the primary key, `name?` may be empty, then the type (`num` is an exact decimal, never a float).
"""


def _tables(engine: Engine) -> dict[str, list[str]]:
    with engine.connect() as conn:
        keys = {(t, c) for t, c in conn.execute(_KEYS)}
        out: dict[str, list[str]] = {}
        for table, column, kind, nullable in conn.execute(_COLUMNS):
            mark = ("*" if (table, column) in keys else "") + ("?" if nullable == "YES" else "")
            out.setdefault(table, []).append(f"{column}{mark} {_TYPES.get(kind, kind)}")
        return out


def render(control: Engine, tenant: Engine) -> str:
    parts = [HEADER]
    for title, engine in (
        ("Control database (one for the platform)", control),
        ("Tenant database (one per company)", tenant),
    ):
        tables = _tables(engine)
        parts.append(f"\n## {title}\n\n{len(tables)} tables.\n")
        parts += [f"**{name}** · {', '.join(cols)}\n" for name, cols in sorted(tables.items())]
    return "\n".join(parts)
