"""Write docs/database-schema.md from a migrated database: every table and column, control and tenant.

    cd backend && .venv/Scripts/python ../scripts/gen_schema_doc.py [tenant-slug]   # default: apex

Run it after adding a migration, against a tenant that is at the latest revision (`python -m p2p.cli tenant migrate-all`
first). A test (tests/test_schema_doc.py) fails when the file is out of date.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from p2p.platform import schema_doc, tenants  # noqa: E402


def main() -> int:
    slug = sys.argv[1] if len(sys.argv) > 1 else "apex"
    text = schema_doc.render(tenants.control_engine(), tenants.tenant_engine(slug))
    (ROOT / "docs" / "database-schema.md").write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote docs/database-schema.md from tenant {slug}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
