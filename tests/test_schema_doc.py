"""docs/database-schema.md is generated from the migrations: a new table or column must show up in it."""

import difflib
import uuid
from pathlib import Path

from p2p.platform import schema_doc, tenants

REFERENCE = Path(__file__).resolve().parents[1] / "docs" / "database-schema.md"


def test_the_schema_reference_lists_every_table_and_column_the_migrations_make(control_db: str) -> None:
    slug = "s" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug, load_reference=False).ok

    engine = tenants.tenant_engine(slug)
    try:
        made = schema_doc.render(tenants.control_engine(), engine)
    finally:
        engine.dispose()

    written = REFERENCE.read_text(encoding="utf-8")
    changes = "\n".join(
        list(difflib.unified_diff(written.splitlines(), made.splitlines(), "docs", "migrations", lineterm=""))[:30]
    )
    assert made == written, (
        "docs/database-schema.md is out of date: run `cd backend && .venv/Scripts/python ../scripts/gen_schema_doc.py`\n"
        + changes
    )
