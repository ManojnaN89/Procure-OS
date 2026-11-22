"""E66: a later masters import keeps every second-person control (task 0.6, DF-16, D-22, rule 8), written without the
implementation.

Independence: written from the block 0c spec (sections 3.1-3.5, 5, 6 and the decisions of 22 Nov 2026), the plan and
the dataset only; the platform's import code was never read. The oracle reads the CSV files of the dataset and of the
changed copy, and the company database straight (an owner connection, for reading only), and works out by itself what a
later import must add, change, keep and leave out. It never trusts the importer's report alone: every count the report
or the audit log gives is compared with the oracle's own difference of the database before and after.

Scenario. The demo company Apex Pumps is made (its first import: mode first, every row of every file loaded). Then, as
the demo logins: a purchase order from the CNT-0001 contract price (so a purchase order line points at a price row);
SUP-001 blocked through a status proposal the director approves; SUP-002's bank account changed through a bank change
proposal the director approves. A copy of the dataset then says: SUP-001 active; SUP-002 its old account, verified;
SUP-003 a new trade name and contact email; SUP-005 (active) a new GSTIN; a new supplier SUP-090, active, with a
verified account; a new supplier SUP-091 with SUP-001's PAN; SUP-004 and everything of it left out of the files; a
policy changed and a new policy key; an approval rule row changed and a new approval rule version; a new active
CNT-0001 price version; a new active contract; an item renamed; an opening stock quantity changed; a department's head
and name changed. `p2p.cli import masters` runs as a dry run, for real, and again with the same copy. A second company
must see none of it.

Also here (decisions of 22 Nov 2026, D-22): through the older admin API an administrator neither makes an Admin login
alone (POST /api/admin/users) nor gives a role or a limit without a second administrator
(/api/admin/users/{id}/roles and /limits).

Mutations (spec section 6): the import applies supplier.status from the file; a new supplier's bank account keeps
verified=true; the later import deletes and re-inserts (a supplier left out of the file disappears, price_record ids
change). Each is put into the database by hand in a transaction that is rolled back, and the same checks that passed
on the real result must fail on it.
"""

import csv
import json
import re
import shutil
import tempfile
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import block0c as z
import env
import lib
from block1 import cli
from lib import D, Outcome
from sqlalchemy import column, func, select, table, text
from sqlalchemy.engine import Connection

ID = "E66"
TITLE = "A later masters import keeps second-person controls: status, bank, policies, versions, history, nothing deleted"
REQUIREMENT = (
    "Task 0.6, DF-16, D-22, rule 8 (block 0c spec 3.1-3.5): the first import loads everything; any later import matches "
    "rows by key, never deletes, never changes a supplier's status or an existing supplier's bank details, adds new "
    "suppliers as drafts with unverified accounts, leaves out possible duplicates, never applies policies, approval "
    "rules or opening stock, adds price versions and contracts as drafts, keeps approvers; every added or changed row "
    "audited; a dry run writes nothing; a repeat changes nothing; tenant isolation. Decisions of 22 Nov: the admin API "
    "makes no Admin login and gives no role or limit without a second administrator"
)
PREFIX = "e66"
DOMAIN = "apexpumps.example"
BLOCKED, BANK, RENAMED, GONE, FROZEN, NEW, TWIN = "SUP-001", "SUP-002", "SUP-003", "SUP-004", "SUP-005", "SUP-090", "SUP-091"
CONTRACT, NEW_CONTRACT, CONTRACT_ITEM = "CNT-0001", "CNT-0090", "RM-01"
NEW_POLICY = "e66_new_policy"

# keys of the tables without a natural key (spec 3.2); a table with a key in the data dictionary uses it
ALL_COLUMNS = {"org_user_role", "delegation", "supplier_category_scope", "supplier_status_history", "supplier_flag"}
KEYS: dict[str, tuple[str, ...]] = {
    "budget": ("cost_centre_code", "location_code", "gl_code", "project_code", "period", "version"),
    "number_series": ("document_type", "scope"),
    "tolerance": ("key", "category_code", "version"),
    "approval_rule": ("rule_code", "version"),
    "supplier_address": ("supplier_code", "type"),
    "supplier_bank_account": ("supplier_code", "account_no", "ifsc"),
    "supplier_contact": ("supplier_code", "name"),
    "supplier_document": ("supplier_code", "doc_type", "number"),
    "price_record": ("supplier_code", "item_code", "contract_code", "uom", "min_qty", "location_code", "region_code",
                     "version"),
    "sanction_entry": ("name", "country", "list_name"),
    "price_threshold_rule": ("category_code", "version"),
    "market_benchmark": ("item_code", "region_code", "source_name", "effective_date"),
}  # fmt: skip
TAIL = "after the first import (another administrator approves)"

# the CLI's lines (spec 3.4)
UPDATE = re.compile(
    r"update by key: added (\d+), changed (\d+), unchanged (\d+), not in the file (\d+) \(kept\), in [\d.]+ ?s; "
    r"(\d+) left out, (\d+) warnings?"
)
DRY = re.compile(
    r"update by key \(dry run, nothing written\): would add (\d+), would change (\d+), unchanged (\d+), not in the file "
    r"(\d+) \(kept\), in [\d.]+ ?s; (\d+) left out, (\d+) warnings?"
)


# ---- the files ----------------------------------------------------------------------------------------------------


def read(folder: Path, name: str) -> tuple[list[str], list[dict[str, str]]]:
    with (folder / "master" / f"{name}.csv").open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def write(folder: Path, name: str, fields: list[str], rows: list[dict[str, str]]) -> None:
    with (folder / "master" / f"{name}.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def cast(value: str | None, sql_type: str) -> Any:
    """A cell as the typed value the database holds (the spec compares typed values)."""
    value = (value or "").strip()
    if value == "":
        return None
    base = sql_type.split("(")[0]
    if base == "boolean":
        return value.lower() == "true"
    if base == "integer":
        return int(value)
    if base == "numeric":
        return Decimal(value)
    if base == "date":
        return date.fromisoformat(value)
    if base == "timestamptz":
        return datetime.fromisoformat(value)
    return value


def gstin(first14: str) -> str:
    """A GSTIN with its check character (the published mod-36 rule), so the changed files stay valid."""
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    total = 0
    for i, ch in enumerate(first14):
        product = alphabet.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return first14 + alphabet[(36 - total % 36) % 36]


def key_text(values: tuple[Any, ...]) -> str:
    """A key as the report shows it: values joined with '/', null as '-' (spec 3.2)."""
    return "/".join("-" if v is None else str(v) for v in values)


class Schema:
    """The master tables of the data dictionary (file name, table, columns, key)."""

    def __init__(self) -> None:
        # generated from docs/data-dictionary.md: the shape of the tables, not the import rules
        from p2p.masters.schema import TABLES

        self.tables = TABLES
        self.fks = {(t.name, col): (ref, rc) for t in TABLES for col, ref, rc in t.foreign_keys}

    def key(self, spec: Any) -> tuple[str, ...]:
        if spec.name in ALL_COLUMNS:
            return tuple(c for c, _ in spec.columns)
        return tuple(spec.pk or KEYS.get(spec.name) or tuple(c for c, _ in spec.columns))

    def file_rows(self, folder: Path) -> dict[str, list[dict[str, Any]]]:
        """Every table whose file is in the folder, as typed rows of the columns the file carries."""
        out: dict[str, list[dict[str, Any]]] = {}
        for spec in self.tables:
            path = folder / "master" / f"{spec.csv}.csv"
            if not path.is_file():
                continue
            fields, rows = read(folder, spec.csv)
            types = dict(spec.columns)
            out[spec.name] = [{c: cast(r.get(c), types[c]) for c in fields if c in types} for r in rows]
            if spec.name == "price_record":  # a price list without versions is version 1, in force (data dictionary)
                for r in out[spec.name]:
                    r["version"] = r.get("version") or 1
                    r["status"] = r.get("status") or "active"
        return out

    def snapshot(self, conn: Connection) -> dict[str, dict[tuple[Any, ...], list[dict[str, Any]]]]:
        """Every master table of the company by key (all columns, ids included)."""
        conn.rollback()
        out: dict[str, dict[tuple[Any, ...], list[dict[str, Any]]]] = {}
        for spec in self.tables:
            cols = self.key(spec)
            by_key: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
            for r in conn.execute(select(text("*")).select_from(table(spec.name))).mappings():
                by_key.setdefault(tuple(r[c] for c in cols), []).append(dict(r))
            out[spec.name] = by_key
        conn.rollback()
        return out

    def columns(self, table: str) -> list[str]:
        return [c for c, _ in next(t for t in self.tables if t.name == table).columns]


def delta(schema: Schema, before: dict, after: dict) -> dict[str, dict[str, list[tuple[Any, ...]]]]:
    """What changed between two snapshots, per table: keys added, removed, changed (columns of the data dictionary)."""
    out: dict[str, dict[str, list[tuple[Any, ...]]]] = {}
    for spec in schema.tables:
        b, a = before[spec.name], after[spec.name]
        cols = schema.columns(spec.name)
        changed = [
            k for k in b.keys() & a.keys()
            if sorted(tuple(str(r[c]) for c in cols) for r in b[k]) != sorted(tuple(str(r[c]) for c in cols) for r in a[k])
        ]  # fmt: skip
        out[spec.name] = {
            "added": sorted(a.keys() - b.keys(), key=str),
            "removed": sorted(b.keys() - a.keys(), key=str),
            "changed": sorted(changed, key=str),
        }
    return out


def count_rows(conn: Connection, name: str, **where: Any) -> int:
    """Rows of a table (named by the schema), optionally where columns equal values."""
    stmt = select(func.count()).select_from(table(name))
    for col, value in where.items():
        stmt = stmt.where(column(col) == value)
    found = int(conn.execute(stmt).scalar_one())
    conn.rollback()
    return found


def per_table(d: dict[str, dict[str, list[Any]]], kind: str) -> dict[str, int]:
    return {t: len(v[kind]) for t, v in d.items() if v[kind]}


def nonzero(raw: Any) -> dict[str, int]:
    return {str(k): int(v) for k, v in (raw or {}).items() if int(v)}


# ---- the changed copy -----------------------------------------------------------------------------------------------


class Copy:
    """The dataset with the scenario's changes, and what the oracle expects of each change."""

    def __init__(self, base: Path, folder: Path, schema: Schema) -> None:
        shutil.copytree(base, folder)
        self.folder, self.schema = folder, schema
        self.expect_added: dict[str, set[tuple[Any, ...]]] = {}
        self.expect_changed: dict[str, set[tuple[Any, ...]]] = {}
        self.expect_kept: dict[str, set[tuple[Any, ...]]] = {}  # rows the file changes that must not change
        self.warnings: list[tuple[str, re.Pattern[str], str]] = []  # (table, the warning's reason, in words)
        self.twin_rows = 0  # rows the possible duplicate takes with it
        self.not_added = 0  # new rows of control tables the import must not add
        self.facts: dict[str, Any] = {}

    def expect(self, kind: str, table: str, key: tuple[Any, ...]) -> None:
        getattr(self, f"expect_{kind}").setdefault(table, set()).add(key)

    def warn(self, table: str, reason: str, *, key_pattern: str | None = None) -> None:
        if key_pattern is None:
            self.warnings.append((table, re.compile("^" + re.escape(reason) + "$"), reason))
        else:
            self.warnings.append((table, re.compile("^" + key_pattern + re.escape(reason) + "$"), f"<key>: {reason}"))

    def build(self) -> None:
        f = self.folder
        s_fields, sup = read(f, "supplier")
        by = {r["code"]: r for r in sup}
        missing = [c for c in (BLOCKED, BANK, RENAMED, GONE, FROZEN) if by.get(c, {}).get("status") != "active"]
        if missing or NEW in by or TWIN in by:
            raise AssertionError(f"the dataset's suppliers are not as the eval expects: {missing}, {sorted(by)}")
        b_fields, bank = read(f, "supplier_bank_account")
        c_fields, contact = read(f, "supplier_contact")
        a_fields, address = read(f, "supplier_address")
        bank_of = {r["supplier_code"]: r for r in bank}
        contact_of = {r["supplier_code"]: r for r in contact}
        address_of = {r["supplier_code"]: r for r in address if r.get("type") == "registered"}

        # SUP-001 active in the file (blocked in the company); SUP-002 its old account, verified
        by[BLOCKED]["status"] = "active"
        self.expect("kept", "supplier", (BLOCKED,))
        self.warn("supplier", f"{BLOCKED}: status stays blocked; an import does not change it")
        bank_of[BANK]["verified"] = "true"
        self.warn("supplier_bank_account", "bank details kept; change them through a bank change",
                  key_pattern=re.escape(BANK) + r"(/[^:]*)?: ")  # fmt: skip
        # SUP-003: a new trade name and contact email (applied, audited)
        self.facts["old_trade"] = by[RENAMED]["trade_name"]
        by[RENAMED]["trade_name"] = "Shree Wires E66"
        self.facts["new_trade"] = by[RENAMED]["trade_name"]
        self.expect("changed", "supplier", (RENAMED,))
        old_mail = contact_of[RENAMED]["email"]
        contact_of[RENAMED]["email"] = "orders@" + old_mail.partition("@")[2]
        self.facts.update(old_mail=old_mail, new_mail=contact_of[RENAMED]["email"], contact=contact_of[RENAMED]["name"])
        self.expect("changed", "supplier_contact", (RENAMED, contact_of[RENAMED]["name"]))
        # SUP-005 (active): a new GSTIN stays out
        old = by[FROZEN]["gstin"]
        self.facts["old_gstin"] = old
        self.expect("kept", "supplier", (FROZEN,))
        by[FROZEN]["gstin"] = gstin(old[:12] + ("2" if old[12] != "2" else "3") + "Z")
        self.warn("supplier", f"{FROZEN}: gstin stays {old} while the supplier is active; put it under review to "
                              "change it (another person approves)")  # fmt: skip
        # SUP-090: new, active, a verified account: added as a draft, the account unverified
        pan = "EFSPC0000N"
        new = {**by[RENAMED], "code": NEW, "legal_name": "E66 Fresh Supplies Pvt Ltd",
               "trade_name": "E66 Fresh Supplies Pvt Ltd", "pan": pan, "status": "active"}  # fmt: skip
        new["gstin"] = gstin(by[RENAMED]["state_code"] + pan + "1Z")
        sup.append(new)
        self.facts.update(new_account="000100000090")
        bank.append({**bank_of[RENAMED], "supplier_code": NEW, "account_no": "000100000090",
                     "ifsc": bank_of[RENAMED]["ifsc"][:4] + "0900090", "verified": "true", "verified_on": "2024-09-15"})  # fmt: skip
        contact.append({**contact_of[RENAMED], "supplier_code": NEW, "email": "sales@e66fresh.example",
                        "phone": "+91 00000 90090"})  # fmt: skip
        address.append({**address_of[RENAMED], "supplier_code": NEW, "gstin": new["gstin"]})
        self.expect("added", "supplier", (NEW,))
        self.expect("added", "supplier_bank_account", (NEW, "000100000090", bank[-1]["ifsc"]))
        self.expect("added", "supplier_contact", (NEW, contact[-1]["name"]))
        self.expect("added", "supplier_address", (NEW, address[-1]["type"]))
        self.warn("supplier", f"{NEW}: added as a draft; a second person activates it")
        self.warn("supplier_bank_account", "bank account added unverified; a second person verifies it before any payment",
                  key_pattern=re.escape(NEW) + r"(/[^:]*)?: ")  # fmt: skip
        # SUP-091: SUP-001's PAN under another GSTIN: left out with its children
        twin_pan = by[BLOCKED]["pan"]
        twin = {**by[BLOCKED], "code": TWIN, "legal_name": "E66 Twin Castings", "trade_name": "E66 Twin Castings",
                "pan": twin_pan, "gstin": gstin("29" + twin_pan + "1Z"), "state_code": "29", "status": "active"}  # fmt: skip
        sup.append(twin)
        bank.append({**bank_of[BLOCKED], "supplier_code": TWIN, "account_no": "000100000091",
                     "ifsc": bank_of[BLOCKED]["ifsc"][:4] + "0900091", "verified": "true"})  # fmt: skip
        contact.append({**contact_of[BLOCKED], "supplier_code": TWIN, "email": "sales@e66twin.example"})
        address.append({**address_of[BLOCKED], "supplier_code": TWIN, "gstin": twin["gstin"], "state_code": "29",
                        "city": "Bengaluru", "pincode": "560058"})  # fmt: skip
        self.twin_rows = 4
        write(f, "supplier", s_fields, sup)
        write(f, "supplier_bank_account", b_fields, bank)
        write(f, "supplier_contact", c_fields, contact)
        write(f, "supplier_address", a_fields, address)
        self._leave_out(GONE)
        self._controls()
        self._versions()
        self._others()

    def _leave_out(self, code: str) -> None:
        """A supplier and every row of it (contracts and their prices too) left out of the files."""
        gone_contracts: set[str] = set()
        for spec in self.schema.tables:
            path = self.folder / "master" / f"{spec.csv}.csv"
            refs = [col for col, ref, _ in spec.foreign_keys if ref == "supplier"]
            if not path.is_file() or not (refs or spec.name == "supplier"):
                continue
            fields, rows = read(self.folder, spec.csv)
            keep = [r for r in rows if r.get("code" if spec.name == "supplier" else refs[0]) != code]
            if spec.name == "contract":
                gone_contracts |= {r["code"] for r in rows if r["supplier_code"] == code}
            write(self.folder, spec.csv, fields, keep)
        for spec in self.schema.tables:
            refs = [col for col, ref, _ in spec.foreign_keys if ref == "contract"]
            if refs and gone_contracts and (self.folder / "master" / f"{spec.csv}.csv").is_file():
                fields, rows = read(self.folder, spec.csv)
                write(self.folder, spec.csv, fields, [r for r in rows if r.get(refs[0]) not in gone_contracts])

    def _controls(self) -> None:
        f = self.folder
        fields, rows = read(f, "setting")
        policy = next(r for r in rows if r["key"] == "budget_policy")
        self.facts["policy"] = ("budget_policy", policy["value"])
        policy["value"] = "warn" if policy["value"] != "warn" else "block"
        self.expect("kept", "org_setting", ("budget_policy",))
        rows.append({"key": NEW_POLICY, "value": "1", "description": "E66 probe"})
        self.not_added += 1
        write(f, "setting", fields, rows)
        self.warn("org_setting", f"budget_policy: not changed: policies are changed on the setup screen {TAIL}")
        self.warn("org_setting", f"{NEW_POLICY}: not added: policies are changed on the setup screen {TAIL}")

        fields, rows = read(f, "approval_rule")
        req02 = next(r for r in rows if r["rule_code"] == "REQ-02" and (r.get("version") or "1") == "1")
        self.facts["req02"] = req02["approver_chain"]
        req02["approver_chain"] = "purchase_manager" if req02["approver_chain"] != "purchase_manager" else "director"
        self.expect("kept", "approval_rule", ("REQ-02", 1))
        req03 = max((r for r in rows if r["rule_code"] == "REQ-03"), key=lambda r: int(r.get("version") or 1))
        version = int(req03.get("version") or 1) + 1
        rows.append({**req03, "version": str(version), "effective_from": "2027-04-01", "effective_to": ""})
        self.facts["req03_new"] = version
        self.not_added += 1
        write(f, "approval_rule", fields, rows)
        self.warn("approval_rule", f"REQ-02/1: not changed: approval rules are changed on the setup screen {TAIL}")
        self.warn("approval_rule", f"REQ-03/{version}: not added: approval rules are changed on the setup screen {TAIL}")

    def _versions(self) -> None:
        f = self.folder
        fields, rows = read(f, "price_record")
        for col, default in (("version", "1"), ("status", "active")):
            if col not in fields:
                fields.append(col)
            for r in rows:
                r[col] = r.get(col) or default  # the same meaning the file had without the column
        mine = [r for r in rows if r["contract_code"] == CONTRACT and r["item_code"] == CONTRACT_ITEM]
        first = min(mine, key=lambda r: int(r["version"]))
        top = max(int(r["version"]) for r in mine)
        new = {**first, "version": str(top + 1), "status": "active", "price": f"{D(first['price']) + 50:.2f}",
               "valid_from": "2026-11-01"}  # fmt: skip
        rows.append(new)
        write(f, "price_record", fields, rows)
        spec = next(t for t in self.schema.tables if t.name == "price_record")
        types = dict(spec.columns)
        key = tuple(cast(new.get(c), types[c]) for c in KEYS["price_record"])
        self.facts.update(price_key=key, in_force_price=D(first["price"]), new_price=D(new["price"]))
        self.expect("added", "price_record", key)
        self.warn("price_record", f"{key_text(key)}: added as a draft version; a second person activates it on the "
                                  "contract page")  # fmt: skip

        fields, rows = read(f, "contract")
        base = next(r for r in rows if r["code"] == CONTRACT)
        rows.append({**base, "code": NEW_CONTRACT, "supplier_code": RENAMED, "valid_from": "2026-11-01",
                     "valid_to": "2027-10-31", "max_value": "100000.00", "status": "active"})  # fmt: skip
        write(f, "contract", fields, rows)
        self.expect("added", "contract", (NEW_CONTRACT,))
        self.warn("contract", f"{NEW_CONTRACT}: added as a draft; a second person activates it on the contract page")

    def _others(self) -> None:
        f = self.folder
        fields, rows = read(f, "item")
        item = next((r for r in rows if r["code"] == "RM-05"), rows[1])
        self.facts["item"] = (item["code"], item["name"] + " (E66)")
        item["name"] += " (E66)"
        write(f, "item", fields, rows)
        self.expect("changed", "item", (item["code"],))

        fields, rows = read(f, "stock_opening")
        opening = rows[0]
        self.facts["opening"] = ((opening["item_code"], opening["location_code"]), D(opening["qty"]))
        opening["qty"] = str(D(opening["qty"]) + 99)
        self.expect("kept", "stock_opening", (opening["item_code"], opening["location_code"]))
        write(f, "stock_opening", fields, rows)
        self.warn("stock_opening", f"{opening['item_code']}/{opening['location_code']}: kept as it is; opening stock is "
                                   "loaded once: correct stock with a stock count")  # fmt: skip

        _, people = read(f, "user")
        fields, rows = read(f, "department")
        dept = next((r for r in rows if r["code"] == "PRD"), rows[0])
        old_head = dept["head_user"]
        dept["head_user"] = next(p["code"] for p in people if p["code"] != old_head)
        self.facts["dept"] = (dept["code"], old_head, dept["name"] + " E66")
        dept["name"] += " E66"
        write(f, "department", fields, rows)
        self.expect("changed", "department", (dept["code"],))
        self.warn("department", f"{dept['code']}: head_user stays {old_head}; who approves is changed on the setup "
                                "screen (another administrator approves)")  # fmt: skip


# ---- the controls a broken import would lose (used on the real result and on each mutation) --------------------------


def controls(conn: Connection, facts: dict[str, Any]) -> dict[str, Any]:
    """What the scenario's controls look like in the company, read straight from its tables."""

    def one(sql: str, **p: Any) -> Any:
        return conn.execute(text(sql), p).first()

    prices = {
        tuple(r[:8]): r[8]
        for r in conn.execute(
            text(
                "select supplier_code, item_code, contract_code, uom, min_qty, location_code, region_code, version, id "
                "from price_record"
            )
        )
    }
    po_link = one("select l.price_record_id, p.price from po_line l left join price_record p on p.id = l.price_record_id "
                  "where l.po_no = :n order by l.line_no limit 1", n=facts.get("po_no", ""))  # fmt: skip
    return {
        "blocked status": (one("select status from supplier where code = :c", c=BLOCKED) or [None])[0],
        "new supplier status": (one("select status from supplier where code = :c", c=NEW) or [None])[0],
        "new supplier accounts verified": [
            bool(r[0]) for r in conn.execute(
                text("select coalesce(verified, false) from supplier_bank_account where supplier_code = :c"), {"c": NEW}
            )
        ],
        "left-out supplier": one("select count(*) from supplier where code = :c", c=GONE)[0],
        "left-out supplier accounts": one("select count(*) from supplier_bank_account where supplier_code = :c",
                                            c=GONE)[0],
        "price ids": prices,
        "po price link": tuple(po_link) if po_link else None,
    }


def judge(obs: dict[str, Any], facts: dict[str, Any]) -> list[str]:
    """Every way the controls differ from what the spec demands."""
    wrong = []
    if obs["blocked status"] != "blocked":
        wrong.append(f"{BLOCKED} is {obs['blocked status']} after the import; blocked through a proposal, it must stay")
    if obs["new supplier status"] != "draft":
        wrong.append(f"the new supplier {NEW} is {obs['new supplier status']}, expected draft")
    if obs["new supplier accounts verified"] != [False]:
        wrong.append(f"the new supplier's account verified flags are {obs['new supplier accounts verified']}, "
                     "expected one unverified account")  # fmt: skip
    # eval bug, review of 22 Nov: the message read a key `controls` does not write (left-out supplier accounts)
    if obs["left-out supplier"] != 1 or obs["left-out supplier accounts"] != facts["gone_accounts"]:
        wrong.append(f"{GONE}, left out of the file, now has {obs['left-out supplier']} row and "
                     f"{obs['left-out supplier accounts']} accounts (before: 1 and {facts['gone_accounts']})")  # fmt: skip
    moved = {k: (v, obs["price ids"].get(k)) for k, v in facts["price_ids"].items() if obs["price ids"].get(k) != v}
    if moved:
        wrong.append(f"price_record ids changed for {len(moved)} rows, e.g. {list(moved.items())[:2]}")
    link = obs["po price link"]
    if link is None or link[0] != facts["po_link"][0] or link[1] is None or D(link[1]) != D(facts["po_link"][1]):
        wrong.append(f"the order's price link is {link}, before the import {facts['po_link']}")
    return wrong


MUTATIONS: dict[str, list[tuple[str, dict[str, str]]]] = {
    "the import applies supplier.status from the file": [
        ("update supplier set status = 'active' where code = :c", {"c": BLOCKED})
    ],
    "a new supplier's bank account keeps verified=true": [
        ("update supplier_bank_account set verified = true where supplier_code = :c", {"c": NEW})
    ],
    "the later import deletes and re-inserts": [
        ("set local session_replication_role = replica", {}),  # what delete and re-insert leaves, without the keys
        ("delete from supplier_bank_account where supplier_code = :c", {"c": GONE}),
        ("delete from supplier where code = :c", {"c": GONE}),
        ("update price_record set id = id + 1000000", {}),
    ],
}


# ---- the eval -------------------------------------------------------------------------------------------------------


def run(ctx: lib.Ctx) -> Outcome:
    env.forget_tenants(PREFIX)  # left by an earlier run that stopped half way (the demo logins are unique)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def _run(ctx: lib.Ctx) -> Outcome:
    from fastapi.testclient import TestClient
    from p2p.main import app
    from p2p.platform import demo, users
    from p2p.platform.tokens import create_access_token

    out = Outcome(True)
    check = z.checker(out)
    lib.capture_mail()
    slug = env.slug(PREFIX)
    folder = Path(tempfile.mkdtemp(prefix="e66_"))
    # in setup: a live company also locks opening stock and bank accounts (block 0d, D-02), which E82 covers
    demo.reset_company(demo.company("apex"), folder / "dataset", credentials=None, slug=slug, state="setup")
    who = {"admin": "Admin", "admin2": "Admin", "director": "Approver", "buyer": "Buyer", "planthead": "Approver",
           "production": "Requester"}  # fmt: skip
    uid = {k: int(users.user_id_by_email(slug, f"{k}@{DOMAIN}") or 0) for k in who}
    h = {k: {"Authorization": f"Bearer {create_access_token(uid[k], slug, r)}"} for k, r in who.items()}
    if not check(all(uid.values()), f"the demo company lacks a login the eval needs: {uid}"):
        return out
    with z.connect(z.owner_url(z.tenant_database(slug))) as conn:  # the owner, for reading only
        _scenario(out, check, slug, folder, TestClient(app), uid, h, conn)
    return out


def _scenario(
    out: Outcome, check: Callable[..., bool], slug: str, folder: Path, client: Any, uid: dict[str, int],
    h: dict[str, dict[str, str]], conn: Connection,
) -> None:  # fmt: skip
    from p2p.agents import intake
    from p2p.approvals import workflow as approvals
    from p2p.masters.importer import import_masters
    from p2p.orders import service as orders
    from p2p.platform import routing, settings, sod, tenants, users
    from p2p.sourcing import service as sourcing
    from sqlalchemy.orm import Session

    schema = Schema()
    base = folder / "dataset"
    db = Session(routing.engine_for(slug))

    def q(sql: str, **p: Any) -> list[Any]:
        conn.rollback()
        found = list(conn.execute(text(sql), p))
        conn.rollback()
        return found

    def audit_rows(after_id: int, *actions: str) -> list[dict[str, Any]]:
        conn.rollback()
        found = conn.execute(
            text("select id, action, entity, entity_id, before, after from audit_event where id > :i and "
                 "action = any(:a) order by id"), {"i": after_id, "a": list(actions)},
        ).mappings()  # fmt: skip
        rows = [dict(r) for r in found]
        conn.rollback()
        return rows

    def last_audit() -> int:
        return int(q("select coalesce(max(id), 0) from audit_event")[0][0])

    # ---- 1. the first import: mode first, every row of every file ---------------------------------------------------
    first = audit_rows(0, "masters.import")
    out.metrics["first import: audit rows / mode"] = f"{len(first)} / {[r['after'].get('mode') for r in first]}"
    check(len(first) == 1 and (first[0]["after"] or {}).get("mode") == "first",
          f"the demo company's first import is audited as {[r['after'] for r in first]}, expected one with mode first")  # fmt: skip
    counts_off = {}
    for spec in schema.tables:
        path = base / "master" / f"{spec.csv}.csv"
        if path.is_file():
            want = len(read(base, spec.csv)[1])
            got = count_rows(conn, spec.name)
            if got != want:
                counts_off[spec.name] = f"{got} rows, file {want}"
    out.metrics["first import: tables whose rows differ from the file"] = counts_off or "none"
    check(not counts_off, f"the first import did not load every row of every file: {counts_off}")

    # a second company, for isolation
    other = env.slug(PREFIX)
    assert tenants.create_tenant(other, load_reference=False).ok
    with Session(routing.engine_for(other)) as odb:
        check(not import_masters(odb, base).rejected, "the second company's first import left rows out", "medium")
    with z.connect(z.owner_url(z.tenant_database(other))) as oc:
        other_before = _fingerprint(schema, oc)

    # ---- 2. the controls people set after the first import ----------------------------------------------------------
    facts: dict[str, Any] = {}
    made = intake.create_from_form(db, uid["production"], [(CONTRACT_ITEM, D(2))], date(2026, 11, 30), None, "E66",
                                   today=lib.TODAY)  # fmt: skip
    no = made.request_nos[0]
    sub = approvals.submit(db, slug, no, sod.user(uid["production"]), today=lib.TODAY)
    po_no = None
    if sub.status == "approved":
        sourcing.source_request(db, slug, no, as_of=lib.TODAY, dispatch=False)
        result = orders.order_request(db, slug, no, as_of=lib.TODAY)
        po_no = result.pos[0] if result.pos else None
    facts["po_no"] = po_no or ""
    link = q("select l.price_record_id, p.price from po_line l left join price_record p on p.id = l.price_record_id "
             "where l.po_no = :n order by l.line_no limit 1", n=facts["po_no"])  # fmt: skip
    facts["po_link"] = tuple(link[0]) if link else (None, None)
    out.metrics["order bound to a contract price (po, price_record id)"] = f"{po_no}, {facts['po_link'][0]}"
    check(po_no is not None and facts["po_link"][0] is not None,
          f"could not set up an order on a {CONTRACT} price (request {no}: {sub.status}); the price link is not tested")  # fmt: skip

    def proposal_of(r: Any) -> int | None:
        if r.status_code not in (200, 201, 202):
            return None
        j = r.json()
        pid = j.get("proposal_id") or j.get("proposal")
        return int(pid) if pid else None

    def approve(pid: int | None, person: str) -> Any:
        return client.post(f"/api/admin/proposals/{pid}/approve", json={}, headers=h[person])

    r = client.post(f"/api/vendors/{BLOCKED}/status", json={"to": "blocked", "reason": "E66 quality hold"},
                    headers=h["buyer"])  # fmt: skip
    done = approve(proposal_of(r), "director")
    status = q("select status from supplier where code = :c", c=BLOCKED)[0][0]
    check(status == "blocked", f"blocking {BLOCKED}: {r.status_code} {r.text[:150]}, approval {done.status_code}, now "
                               f"{status}; the status check is not tested")  # fmt: skip
    old_bank = q("select account_no, ifsc from supplier_bank_account where supplier_code = :c", c=BANK)[0]
    r = client.post(f"/api/vendors/{BANK}/bank", json={"account_no": "000100000902", "ifsc": old_bank[1][:4] +
                    "0900902", "reason": "E66 moved bank"}, headers=h["buyer"])  # fmt: skip
    done = approve(proposal_of(r), "director")
    bank_sql = ("select account_no, ifsc, coalesce(verified, false), verified_on, submitted_by from supplier_bank_account "
                "where supplier_code = :c order by account_no")  # fmt: skip
    bank_after_change = [tuple(x) for x in q(bank_sql, c=BANK)]
    check([b[0] for b in bank_after_change] == ["000100000902"],
          f"changing {BANK}'s account: {r.status_code} {r.text[:150]}, approval {done.status_code}, accounts "
          f"{bank_after_change}; the bank check is not tested")  # fmt: skip

    # ---- 3. the changed copy and the oracle's expectations ------------------------------------------------------------
    copy = Copy(base, folder / "changed", schema)
    try:
        copy.build()
    except (AssertionError, KeyError, StopIteration) as exc:
        check(False, f"the dataset does not have what the scenario changes: {exc!r}")
        return
    facts.update(copy.facts)
    before = schema.snapshot(conn)
    facts["gone_accounts"] = sum(len(v) for k, v in before["supplier_bank_account"].items() if k[0] == GONE)
    facts["price_ids"] = {k: v[0]["id"] for k, v in before["price_record"].items() if len(v) == 1}
    gone_rows = {t: sorted(k for k in before[t] if k and k[0] == GONE) for t in ("supplier_document",
                 "supplier_status_history", "supplier_contact", "supplier_address")}  # fmt: skip
    files = schema.file_rows(copy.folder)
    file_keys = {t: {tuple(r.get(c) for c in schema.key(next(s for s in schema.tables if s.name == t))) for r in rows}
                 for t, rows in files.items()}  # fmt: skip
    want_not_in_file = {t: len(before[t].keys() - keys) for t, keys in file_keys.items() if before[t].keys() - keys}
    drift = {}  # rows of the copy that differ from the company although the scenario did not touch them
    for t, rows in files.items():
        spec = next(s for s in schema.tables if s.name == t)
        cols = schema.key(spec)
        for r in rows:
            k = tuple(r.get(c) for c in cols)
            have = before[t].get(k)
            differs = have and len(have) == 1 and any(r[c] != have[0].get(c) for c in r if c in have[0])
            if differs and k not in copy.expect_changed.get(t, set()) | copy.expect_kept.get(t, set()):
                drift.setdefault(t, set()).add(k)
    out.metrics["rows the company holds differently from the file, untouched by the scenario"] = (
        {t: len(v) for t, v in drift.items()} or "none"
    )

    # ---- 4. dry run: the same report, nothing written -----------------------------------------------------------------
    audit0 = last_audit()
    code, printed = cli("import", "masters", slug, str(copy.folder), "--dry-run")
    dry = DRY.search(printed)
    out.metrics["dry run: exit code / first line"] = f"{code} / {dry.group(0) if dry else printed.strip()[:200]}"
    check(code == 1, f"the dry run exited {code}; a row left out (the possible duplicate) means 1", "medium")
    check(dry is not None, f"the dry run printed no 'update by key (dry run, nothing written)' line: {printed[:300]}")
    check(schema.snapshot(conn) == before, "the dry run changed the company's master tables")
    check(last_audit() == audit0, "the dry run wrote audit rows")

    # ---- 5. the real import --------------------------------------------------------------------------------------------
    code, printed = cli("import", "masters", slug, str(copy.folder))
    after = schema.snapshot(conn)
    got = UPDATE.search(printed)
    nums = tuple(int(x) for x in got.groups()) if got else None
    out.metrics["import: exit code / first line"] = f"{code} / {got.group(0) if got else printed.strip()[:200]}"
    check(got is not None, f"the later import printed no 'update by key: ...' line: {printed[:400]}")
    d = delta(schema, before, after)
    added, changed, removed = per_table(d, "added"), per_table(d, "changed"), per_table(d, "removed")
    want_added = {t: len(v) for t, v in copy.expect_added.items()}
    out.metrics["oracle delta: added / changed / removed"] = f"{added} / {changed} / {removed or 'none'}"
    check(not removed, f"the later import removed rows: {removed}")
    check(added == want_added and all(set(d[t]["added"]) == v for t, v in copy.expect_added.items()),
          f"rows added {({t: d[t]['added'] for t in added})}, the oracle expects {copy.expect_added}")  # fmt: skip
    for t, keys in copy.expect_changed.items():
        check(keys <= set(d[t]["changed"]), f"{t}: rows {sorted(keys - set(d[t]['changed']), key=str)} were not "
                                            "changed by the file")  # fmt: skip
    extra = {t: sorted(set(v["changed"]) - copy.expect_changed.get(t, set()) - drift.get(t, set()), key=str)
             for t, v in d.items()}  # fmt: skip
    extra = {t: v for t, v in extra.items() if v}
    check(not extra, f"rows changed that the file did not change: {extra}")
    if drift:
        out.notes.append(f"rows the company held differently from the file before the import: "
                         f"{ {t: sorted(v, key=str)[:3] for t, v in drift.items()} }")  # fmt: skip
    if nums:
        a, c, u, k, j, w = nums
        check(code == 1, f"the import exited {code}; a row left out (the possible duplicate) means 1")
        check((a, c) == (sum(added.values()), sum(changed.values())),
              f"the CLI says added {a}, changed {c}; the database says {sum(added.values())}, {sum(changed.values())}")  # fmt: skip
        check(k == sum(want_not_in_file.values()), f"the CLI says {k} not in the file; the oracle counts "
                                                   f"{sum(want_not_in_file.values())} ({want_not_in_file})", "medium")  # fmt: skip
        check(j == copy.twin_rows, f"the CLI says {j} left out; the oracle: {TWIN} and its {copy.twin_rows - 1} rows",
              "medium")  # fmt: skip
        accepted = sum(len(v) for v in files.values()) - j
        check(accepted - a - c - copy.not_added <= u <= accepted - a - c,
              f"the CLI says {u} unchanged; accepted rows {accepted} minus added and changed is {accepted - a - c}", "low")  # fmt: skip
        if dry:
            check(tuple(int(x) for x in dry.groups()) == nums, f"the dry run said {dry.groups()}, the import {nums}",
                  "medium")  # fmt: skip
    lines = [ln.rstrip() for ln in printed.splitlines()]
    sup_line = next((ln for ln in lines if ln.startswith("  supplier: ")), None)
    want_sup = (f"  supplier: {added.get('supplier', 0)} added, {changed.get('supplier', 0)} changed, "
                f"\\d+ unchanged, {want_not_in_file.get('supplier', 0)} not in the file")  # fmt: skip
    check(sup_line is not None and re.fullmatch(want_sup, sup_line),
          f"the supplier line is {sup_line!r}, expected {want_sup!r}", "medium")  # fmt: skip
    want_change_line = f"  changed   supplier {RENAMED}: trade_name '{facts['old_trade']}' -> '{facts['new_trade']}'"
    check(want_change_line in lines, f"no line {want_change_line!r}", "medium")
    check(f"  added     supplier {NEW}" in lines, f"no line '  added     supplier {NEW}'", "medium")
    left = [ln for ln in lines if ln.startswith("  left out  supplier line ")]
    check(any(ln.endswith(f": possible duplicate: {BLOCKED} already has this PAN") for ln in left),
          f"the possible duplicate is not left out with the spec's words: {left}", "medium")  # fmt: skip
    warned = [ln[len("  warning   "):] for ln in lines if ln.startswith("  warning   ")]
    missing_w = []
    for name, pattern, words in copy.warnings:
        if not any(w.startswith(f"{name}: ") and pattern.match(w[len(name) + 2:]) for w in warned):
            missing_w.append(f"{name}: {words}")
    out.metrics["warnings expected / printed / missing"] = f"{len(copy.warnings)} / {len(warned)} / {len(missing_w)}"
    for m in missing_w:
        out.find("high", f"warning missing or not in the spec's words: {m}")

    # what the company holds now
    obs = controls(conn, facts)
    conn.rollback()
    wrong = judge(obs, facts)
    for w in wrong:
        out.find("high", w)
    out.metrics["controls after the import (spec 3.5)"] = "kept" if not wrong else f"{len(wrong)} broken"
    check(not q("select 1 from supplier where code = :c", c=TWIN), f"{TWIN} (SUP-001's PAN) was added")
    for t in ("supplier_bank_account", "supplier_contact", "supplier_address"):
        check(not count_rows(conn, t, supplier_code=TWIN), f"{TWIN}'s {t} row was added")
    check([tuple(x) for x in q(bank_sql, c=BANK)] == bank_after_change,
          f"{BANK}'s account after the import: {q(bank_sql, c=BANK)}; as the approved change left it: {bank_after_change}")  # fmt: skip
    new_bank = q("select verified_on from supplier_bank_account where supplier_code = :c", c=NEW)
    check(new_bank and new_bank[0][0] is None, f"{NEW}'s account keeps a verified_on date: {new_bank}", "medium")
    trade = q("select trade_name from supplier where code = :c", c=RENAMED)[0][0]
    mail = q("select email from supplier_contact where supplier_code = :c and name = :n", c=RENAMED, n=facts["contact"])
    check(trade == facts["new_trade"] and mail and mail[0][0] == facts["new_mail"],
          f"{RENAMED}: trade name {trade!r}, contact email {mail}; the file says {facts['new_trade']!r}, {facts['new_mail']!r}")  # fmt: skip
    gst = q("select gstin from supplier where code = :c", c=FROZEN)[0][0]
    check(gst == facts["old_gstin"], f"{FROZEN} (active) took the file's GSTIN {gst}; it must stay {facts['old_gstin']}")
    for t, keys in gone_rows.items():
        check(sorted(k for k in after[t] if k and k[0] == GONE) == keys, f"{GONE}'s {t} rows changed: the file left "
                                                                         "them out, the company keeps them")  # fmt: skip
    key, old_value = facts["policy"]
    db.rollback()
    check(settings.value(db, key, "") == old_value, f"policy {key} is {settings.value(db, key, '')}, expected {old_value}")
    check(not q("select 1 from org_setting where key = :k", k=NEW_POLICY), f"the new policy key {NEW_POLICY} was added")
    chain = q("select approver_chain from approval_rule where rule_code = 'REQ-02' and version = 1")
    check(chain and chain[0][0] == facts["req02"], f"approval rule REQ-02 is {chain}, expected {facts['req02']}")
    check(not q("select 1 from approval_rule where rule_code = 'REQ-03' and version = :v", v=facts["req03_new"]),
          "the new approval rule version was added")  # fmt: skip
    pk = facts["price_key"]
    new_price = q("select status, price from price_record where supplier_code = :s and item_code = :i and "
                  "contract_code = :c and version = :v", s=pk[0], i=pk[1], c=pk[2], v=pk[7])  # fmt: skip
    check([r[0] for r in new_price] == ["draft"], f"the new {CONTRACT} price version is {new_price}, expected one draft")
    # eval bug, review of 22 Nov: this scenario blocks CNT-0001's supplier, and sourcing never uses a blocked supplier's
    # contract; the question is which price version is in force, so the supplier is active again inside this rolled
    # back transaction only
    db.execute(text("update supplier set status = 'active' where code = :c"), {"c": BLOCKED})
    used = [D(o.price) for o in sourcing.contract_options(db, CONTRACT_ITEM, "NOS", lib.TODAY) if o.contract_code == CONTRACT]
    db.rollback()
    check(used == [facts["in_force_price"]], f"sourcing now uses {used} for {CONTRACT_ITEM}, the version in force is "
                                             f"{facts['in_force_price']}", "medium")  # fmt: skip
    contract = q("select status from contract where code = :c", c=NEW_CONTRACT)
    check([r[0] for r in contract] == ["draft"], f"the new contract {NEW_CONTRACT} is {contract}, expected draft")
    item_code, item_name = facts["item"]
    check(q("select name from item where code = :c", c=item_code)[0][0] == item_name, f"{item_code} was not renamed")
    (oi, ol), qty = facts["opening"]
    got_qty = q("select qty from stock_opening where item_code = :i and location_code = :l", i=oi, l=ol)
    check(got_qty and D(got_qty[0][0]) == qty, f"opening stock of {oi} is {got_qty}, expected {qty} (loaded once)")
    dept, head, name = facts["dept"]
    got_dept = q("select head_user, name from department where code = :c", c=dept)
    check(got_dept and tuple(got_dept[0]) == (head, name), f"department {dept} is {got_dept}; expected head {head} "
                                                           f"kept and name {name!r} applied")  # fmt: skip

    # the audit log
    summary = audit_rows(audit0, "masters.import")
    rows_audit = audit_rows(audit0, "masters.row_add", "masters.row_change")
    adds = [r for r in rows_audit if r["action"] == "masters.row_add"]
    changes = [r for r in rows_audit if r["action"] == "masters.row_change"]
    out.metrics["audit: imports / row_add / row_change"] = f"{len(summary)} / {len(adds)} / {len(changes)}"
    if check(len(summary) == 1, f"the import wrote {len(summary)} masters.import rows, expected 1"):
        s = summary[0]["after"] or {}
        check(s.get("mode") == "update", f"masters.import after.mode is {s.get('mode')!r}, expected 'update'")
        check(nonzero(s.get("added")) == added and nonzero(s.get("changed")) == changed,
              f"masters.import says added {s.get('added')}, changed {s.get('changed')}; the database delta is "
              f"{added}, {changed}")  # fmt: skip
        check(nonzero(s.get("not_in_file")) == want_not_in_file,
              f"masters.import says not in the file {s.get('not_in_file')}; the oracle {want_not_in_file}", "medium")  # fmt: skip
        check(s.get("rejected") == copy.twin_rows, f"masters.import says {s.get('rejected')} rejected, the oracle "
                                                   f"{copy.twin_rows}", "medium")  # fmt: skip
    check(len(adds) == sum(added.values()) and len(changes) == sum(changed.values()),
          f"{len(adds)} masters.row_add and {len(changes)} masters.row_change rows for {sum(added.values())} added and "
          f"{sum(changed.values())} changed rows")  # fmt: skip
    want_audit = [
        ("supplier", RENAMED, {"trade_name": facts["old_trade"]}, {"trade_name": facts["new_trade"]}),
        ("supplier_contact", f"{RENAMED}/{facts['contact']}", {"email": facts["old_mail"]}, {"email": facts["new_mail"]}),
    ]
    for entity, eid, b, a in want_audit:
        found = [(r["before"], r["after"]) for r in changes if r["entity"] == entity and r["entity_id"] == eid]
        check(found == [(b, a)], f"masters.row_change for {entity} {eid}: {found}, expected before {b} after {a}")
    check(not any(facts["new_account"] in json.dumps(r["after"], default=str) for r in rows_audit),
          f"the audit log shows {NEW}'s whole account number (spec 3.4: masked)", "medium")  # fmt: skip

    # ---- 6. the same copy again: nothing added or changed -------------------------------------------------------------
    audit1 = last_audit()
    _, printed3 = cli("import", "masters", slug, str(copy.folder))
    again = UPDATE.search(printed3)
    out.metrics["the same copy again: first line"] = again.group(0) if again else printed3.strip()[:200]
    check(again is not None and again.group(1) == "0" and again.group(2) == "0",
          f"the same copy imported again: {again.group(0) if again else printed3[:300]}, expected added 0, changed 0")  # fmt: skip
    check(not audit_rows(audit1, "masters.row_add", "masters.row_change"), "the repeat wrote masters.row_* audit rows")
    check(schema.snapshot(conn) == after, "the repeat changed the company's master tables")

    # ---- 7. the other company saw none of it ---------------------------------------------------------------------------
    with z.connect(z.owner_url(z.tenant_database(other))) as oc:
        check(_fingerprint(schema, oc) == other_before, "the other company's masters or audit changed")

    # ---- 8. the admin API (decisions of 22 Nov): no Admin login, role or limit without a second administrator ----------
    _admin_api(out, check, slug, client, uid, h, q, proposal_of, approve, users)

    # ---- 9. mutations: each broken rule, put into the database by hand and rolled back, must be caught -----------------
    caught = {}
    for name, sqls in MUTATIONS.items():
        conn.rollback()
        tx = conn.begin()
        how = "in the database"
        try:
            for sql, params in sqls:
                conn.execute(text(sql), params)
            mutated = controls(conn, facts)
        except Exception as exc:  # noqa: BLE001 - not allowed here (no superuser): the same change on the observation
            tx.rollback()
            tx = None
            how = f"on the observation ({type(exc).__name__})"
            mutated = dict(obs)
            if "status" in name:
                mutated["blocked status"] = "active"
            elif "verified" in name:
                mutated["new supplier accounts verified"] = [True]
            else:
                mutated.update({"left-out supplier": 0, "left-out supplier accounts": 0,
                                "price ids": {k: v + 1000000 for k, v in obs["price ids"].items()}})  # fmt: skip
        finally:
            if tx is not None:
                tx.rollback()
        told = [t for t in judge(mutated, facts) if t not in wrong]  # a finding the real result does not have
        caught[name] = f"caught {how}" if told else f"NOT caught ({how})"
        check(told, f"mutation '{name}' was not caught: the eval cannot tell this rule is broken", "medium")
    out.metrics["mutation checks (rule broken on purpose: caught?)"] = caught
    db.close()


def _fingerprint(schema: Schema, conn: Connection) -> dict[str, Any]:
    """Row counts of every master table and the audit log, and the suppliers' statuses."""
    conn.rollback()
    found: dict[str, Any] = {
        t.name: count_rows(conn, t.name) for t in schema.tables
    }
    found["audit_event"] = conn.execute(text("select count(*) from audit_event")).scalar()
    found["statuses"] = sorted(tuple(r) for r in conn.execute(text("select code, status from supplier")))
    conn.rollback()
    return found


def _admin_api(
    out: Outcome, check: Callable[..., bool], slug: str, client: Any, uid: dict[str, int], h: dict[str, Any],
    q: Callable[..., list[Any]], proposal_of: Callable[[Any], int | None], approve: Callable[..., Any], users: Any,
) -> None:  # fmt: skip
    """The older admin API (decisions of 22 Nov 2026, points 1 and 2). The spec leaves the shape open: a refusal (403)
    or a D-22 proposal another administrator approves, for the Admin login; a proposal for roles and limits."""
    results: dict[str, str] = {}

    # 1. an Admin login made by one administrator
    email = f"e66.admin.{slug}@eval.example"
    body = {"email": email, "password": lib.PASSWORD, "display_name": "E66 Admin", "role": "Admin", "reason": "E66"}
    r = client.post("/api/admin/users", json=body, headers=h["admin"])
    made = users.user_id_by_email(slug, email)
    pid = proposal_of(r)
    if made:
        results["Admin login"] = f"made at once ({r.status_code})"
        check(False, f"POST /api/admin/users made an Admin login with one administrator ({r.status_code})")
    elif r.status_code == 403:
        results["Admin login"] = "refused (403)"
    elif pid is not None:
        own, other = approve(pid, "admin").status_code, approve(pid, "admin2").status_code
        made = users.user_id_by_email(slug, email)
        results["Admin login"] = f"proposed; maker approves {own}, other admin {other}, login made {bool(made)}"
        check(own == 403 and other == 200 and made, f"the Admin login proposal: maker {own}, other admin {other}, "
                                                    f"made {bool(made)}")  # fmt: skip
    else:
        results["Admin login"] = f"{r.status_code} {r.text[:120]}"
        check(False, f"POST /api/admin/users with role Admin answered {r.status_code}: neither 403 nor a proposal",
              "medium")  # fmt: skip
    plain = f"e66.req.{slug}@eval.example"
    r = client.post("/api/admin/users", json={**body, "email": plain, "role": "Requester"}, headers=h["admin"])
    check(r.status_code == 201 and users.user_id_by_email(slug, plain),
          f"POST /api/admin/users with role Requester answered {r.status_code}: a plain login must still work", "low")  # fmt: skip

    # 2. a role and a limit for someone else, and for oneself
    def role_count() -> int:
        return int(q("select count(*) from user_role where user_id = :u and role = 'Store'", u=uid["buyer"])[0][0])

    def limit() -> Any:
        found = q("select max_value from user_limit where user_id = :u and document = 'po'", u=uid["planthead"])
        return D(found[0][0]) if found and found[0][0] is not None else None

    cases = [
        ("role", f"/api/admin/users/{uid['buyer']}/roles", {"role": "Store", "reason": "E66"}, role_count, 1),
        ("limit", f"/api/admin/users/{uid['planthead']}/limits",
         {"document": "po", "max_value": "50000.00", "reason": "E66"}, limit, D("50000.00")),
    ]  # fmt: skip
    for name, path, payload, read_value, want in cases:
        before = read_value()
        r = client.post(path, json=payload, headers=h["admin"])
        now = read_value()
        pid = proposal_of(r)
        if now != before:
            results[name] = f"applied at once ({r.status_code})"
            check(False, f"POST {path} applied the {name} with one administrator ({before} -> {now})")
            continue
        if pid is None:
            results[name] = f"not proposed: {r.status_code} {r.text[:120]}"
            check(False, f"POST {path} answered {r.status_code} without a proposal; the decision of 22 Nov says it goes "
                         "through the second-administrator change", "medium")  # fmt: skip
            continue
        own = approve(pid, "admin").status_code
        mid = read_value()
        other = approve(pid, "admin2").status_code
        final = read_value()
        results[name] = f"proposed; maker approves {own}, other admin {other}; {before} -> {final}"
        check(own == 403 and mid == before, f"the {name} proposal: its maker approved it ({own}), value {mid}")
        check(other == 200 and final == want, f"the {name} proposal approved by the other admin: {other}, value {final}, "
                                              f"expected {want}")  # fmt: skip
    own_cases = {
        "role": (f"/api/admin/users/{uid['admin']}/roles", {"role": "Store", "reason": "E66"}),
        "limit": (f"/api/admin/users/{uid['admin']}/limits", {"document": "po", "max_value": "1", "reason": "E66"}),
    }
    for name, (path, payload) in own_cases.items():
        r = client.post(path, json=payload, headers=h["admin"])
        held = q("select count(*) from user_role where user_id = :u and role = 'Store'", u=uid["admin"])[0][0]
        results[f"own {name}"] = str(r.status_code)
        check(r.status_code in (403, 409) and proposal_of(r) is None and not (name == "role" and held),
              f"an administrator's own {name} through {path}: {r.status_code} {r.text[:120]}")  # fmt: skip
    out.metrics["admin API (decisions of 22 Nov)"] = results
