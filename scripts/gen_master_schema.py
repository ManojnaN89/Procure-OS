"""Generate the master-data schema from a generated dataset.

    python scripts/gen_master_schema.py data/datasets/apex-v1

Writes backend/p2p/masters/schema.py (the typed description the importer uses) and the tenant migration that creates the
tables, with the SQL written out in full so the migration never changes meaning when this script changes. Run it again
only when the data dictionary changes, and then add a new migration instead of editing an old one.
"""

import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# csv file -> (table name, primary key columns; None means a generated id), in the order tables are created.
TABLES: list[tuple[str, str, tuple[str, ...] | None]] = [
    ("company", "company", ("code",)),
    ("bank", "bank", ("code",)),
    ("department", "department", ("code",)),
    ("location", "location", ("code",)),
    ("payment_terms", "payment_terms", ("code",)),
    ("gl_account", "gl_account", ("code",)),
    ("category", "item_category", ("code",)),
    ("user", "org_user", ("code",)),
    ("user_role", "org_user_role", None),
    ("delegation", "delegation", None),
    ("cost_centre", "cost_centre", ("code",)),
    ("project", "project", ("code",)),
    ("budget", "budget", None),
    ("company_bank_account", "company_bank_account", ("code",)),
    ("gst_registration", "gst_registration", ("gstin",)),
    ("number_series", "number_series", None),
    ("setting", "org_setting", ("key",)),
    ("tolerance", "tolerance", None),
    ("approval_rule", "approval_rule", None),
    ("item", "item", ("code",)),
    ("item_location", "item_location", ("item_code", "location_code")),
    ("item_substitute", "item_substitute", ("item_code", "substitute_code")),
    ("uom_conversion", "uom_conversion", ("item_code", "from_uom", "to_uom")),
    ("stock_opening", "stock_opening", ("item_code", "location_code")),
    ("supplier", "supplier", ("code",)),
    ("supplier_address", "supplier_address", None),
    ("supplier_bank_account", "supplier_bank_account", None),
    ("supplier_category", "supplier_category", ("supplier_code", "category_code")),
    ("supplier_contact", "supplier_contact", None),
    ("supplier_document", "supplier_document", None),
    ("supplier_status_history", "supplier_status_history", None),
    ("contract", "contract", ("code",)),
    ("price_record", "price_record", None),
    ("sanction_entry", "sanction_entry", None),
]

# column -> (referenced table, referenced column); checked when loading and enforced (deferred) in the database
FOREIGN_KEYS: dict[str, list[tuple[str, str, str]]] = {
    "cost_centre": [("department_code", "department", "code"), ("location_code", "location", "code")],
    "org_user": [
        ("department_code", "department", "code"),
        ("location_code", "location", "code"),
        ("manager_code", "org_user", "code"),
    ],
    "org_user_role": [("user_code", "org_user", "code")],
    "delegation": [("from_user", "org_user", "code"), ("to_user", "org_user", "code")],
    "project": [("cost_centre_code", "cost_centre", "code")],
    "budget": [
        ("cost_centre_code", "cost_centre", "code"),
        ("gl_code", "gl_account", "code"),
        ("location_code", "location", "code"),
    ],
    "item": [("category_code", "item_category", "code")],
    "item_location": [("item_code", "item", "code"), ("location_code", "location", "code")],
    "item_substitute": [("item_code", "item", "code"), ("substitute_code", "item", "code")],
    "uom_conversion": [("item_code", "item", "code")],
    "stock_opening": [("item_code", "item", "code"), ("location_code", "location", "code")],
    "supplier": [("payment_terms_code", "payment_terms", "code")],
    "supplier_address": [("supplier_code", "supplier", "code")],
    "supplier_bank_account": [("supplier_code", "supplier", "code")],
    "supplier_category": [("supplier_code", "supplier", "code"), ("category_code", "item_category", "code")],
    "supplier_contact": [("supplier_code", "supplier", "code")],
    "supplier_document": [("supplier_code", "supplier", "code")],
    "supplier_status_history": [("supplier_code", "supplier", "code")],
    "contract": [("supplier_code", "supplier", "code")],
    "price_record": [
        ("supplier_code", "supplier", "code"),
        ("item_code", "item", "code"),
        ("contract_code", "contract", "code"),
    ],
}

# columns that are empty in the sample data but have a known type
OVERRIDES = {("item_location", "lead_days_override"): "integer"}

TEXT_NAME = re.compile(
    r"(code|_no|gstin|pincode|ifsc|phone|pan|cin|^key$|^value$|hsn|account|prefix|format|period|number|email|uom|^name$)"
)
BOOL = {"true", "false"}


def infer(name: str, values: list[str]) -> str:
    present = [v for v in values if v != ""]
    if not present:
        return "text"
    if all(v.lower() in BOOL for v in present):
        return "boolean"
    if all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in present):
        return "date"
    if all(re.fullmatch(r"\d{4}-\d{2}-\d{2}T[\d:.+\-Z]+", v) for v in present):
        return "timestamptz"
    if TEXT_NAME.search(name) and not name.endswith(("_pct", "_days", "_qty", "_months")):
        return "text"
    if all(re.fullmatch(r"-?\d+", v) and not (len(v) > 1 and v.lstrip("-").startswith("0")) for v in present):
        return "bigint" if any(len(v) > 9 for v in present) else "integer"
    if all(re.fullmatch(r"-?\d+(\.\d+)?", v) for v in present):
        return "numeric(18,4)"
    return "text"


def main(dataset: Path) -> None:
    specs = []
    for csv_name, table, pk in TABLES:
        with (dataset / "master" / f"{csv_name}.csv").open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        columns = [(c, OVERRIDES.get((table, c)) or infer(c, [r[c] for r in rows])) for c in rows[0]]
        specs.append((csv_name, table, pk, columns, FOREIGN_KEYS.get(table, [])))

    # schema.py
    lines = [
        '"""Master-data tables: written by scripts/gen_master_schema.py from the data dictionary. Do not edit by hand."""',
        "",
        "from dataclasses import dataclass",
        "",
        "",
        "@dataclass(frozen=True)",
        "class Table:",
        "    csv: str  # file name in the dataset's master folder",
        "    name: str  # table in the tenant database",
        "    columns: tuple[tuple[str, str], ...]  # (column, sql type)",
        "    pk: tuple[str, ...] | None  # None: the table has a generated id and no natural key",
        "    foreign_keys: tuple[tuple[str, str, str], ...]  # (column, referenced table, referenced column)",
        "",
        "",
        "TABLES: tuple[Table, ...] = (",
    ]
    for csv_name, table, pk, columns, fks in specs:
        lines.append(f'    Table("{csv_name}", "{table}", (')
        lines += [f'        ("{c}", "{t}"),' for c, t in columns]
        lines.append(f"    ), {pk!r}, {tuple(fks)!r}),")
    lines += [")", "", "BY_NAME = {t.name: t for t in TABLES}", ""]
    (ROOT / "backend/p2p/masters").mkdir(parents=True, exist_ok=True)
    (ROOT / "backend/p2p/masters/__init__.py").touch()
    (ROOT / "backend/p2p/masters/schema.py").write_text("\n".join(lines), encoding="utf-8", newline="\n")

    # migration with the SQL written out
    ddl = []
    for _csv, table, pk, columns, fks in specs:
        cols = [f"{c} {t}" for c, t in columns]
        if pk is None:
            cols.insert(0, "id bigserial primary key")
        else:
            cols.append(f"primary key ({', '.join(pk)})")
        for col, ref_table, ref_col in fks:
            cols.append(f"foreign key ({col}) references {ref_table} ({ref_col}) deferrable initially deferred")
        ddl.append((table, ",\n            ".join(cols)))
    out = [
        '"""Master data: organisation, items, suppliers, contracts, prices, budgets (generated by scripts/gen_master_schema.py)',
        "",
        "Revision ID: 0014",
        "Revises: 0013",
        '"""',
        "",
        "from alembic import op",
        "",
        'revision = "0014"',
        'down_revision = "0013"',
        "branch_labels = None",
        "depends_on = None",
        "",
        "",
        "def upgrade() -> None:",
    ]
    for table, body in ddl:
        out.append(
            f'    op.execute(\n        """\n        create table {table} (\n            {body}\n        )\n        """\n    )'
        )
    out += ["", "", "def downgrade() -> None:"]
    out += [f'    op.execute("drop table {table} cascade")' for table, _ in reversed(ddl)]
    path = ROOT / "backend/p2p/platform/migrations/tenant/versions/0014_masters.py"
    path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(specs)} tables written to schema.py and {path.name}")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
