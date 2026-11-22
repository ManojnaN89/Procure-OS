"""A later masters import updates by key (task 0.6, DF-16, D-22): it adds new rows, changes only what the table's policy
allows, never deletes, and leaves what a second person decides (a supplier's status and bank accounts, policies, rules,
roles, versions) as it is, or lets new rows arrive as drafts or unverified, each with a warning."""

import csv
import os
import shutil
import subprocess
import sys
import uuid
from collections.abc import Callable, Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_masters_import import dataset  # noqa: F401 - the fixture

from p2p.formulas.gstin import gstin_check_char
from p2p.masters import supplier_status, suppliers
from p2p.masters.importer import import_masters
from p2p.masters.report import ImportReport
from p2p.masters.schema import TABLES
from p2p.platform import proposals, routing, settings, tenants, users
from p2p.platform.sod import user

BACKEND = Path(__file__).resolve().parents[1] / "backend"
PASSWORD = "correct horse battery"
Change = Callable[[list[dict[str, str]]], None]


@pytest.fixture
def db(control_db: str, dataset: Path) -> Iterator[Session]:  # noqa: F811
    """A company set up by its first import of the Apex dataset."""
    slug = "u" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug).ok
    with Session(routing.engine_for(slug)) as session:
        session.info["slug"] = slug
        first = import_masters(session, dataset)
        assert first.mode == "first" and first.rejected == []
        yield session


def _variant(dataset: Path, tmp: Path, **changes: Change) -> Path:  # noqa: F811
    """A copy of the dataset in which some files are changed: name=change(rows)."""
    target = tmp / uuid.uuid4().hex[:6]
    shutil.copytree(dataset / "master", target / "master")
    for name, change in changes.items():
        path = target / "master" / f"{name}.csv"
        with path.open(encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            fields, rows = list(reader.fieldnames or []), list(reader)
        change(rows)
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
    return target


def _set(code: str, **values: str) -> Change:
    """Change the row whose first key column is `code`."""

    def change(rows: list[dict[str, str]]) -> None:
        row = next(r for r in rows if next(iter(r.values())) == code)
        row.update(values)

    return change


def _add(**row: str) -> Change:
    def change(rows: list[dict[str, str]]) -> None:
        rows.append({k: row.get(k, "") for k in rows[0]})

    return change


def _one(db: Session, sql: str, **params: Any) -> Any:
    db.expire_all()
    return db.execute(text(sql), params).scalar()


def _counts(db: Session) -> dict[str, int]:
    return {s.name: int(_one(db, f"select count(*) from {s.name}")) for s in TABLES}  # noqa: S608


def _audit(db: Session, action: str) -> list[tuple[str, str, Any, Any]]:
    db.expire_all()
    rows = db.execute(
        text("select entity, entity_id, before, after from audit_event where action = :a order by id"), {"a": action}
    )
    return [tuple(r) for r in rows]  # type: ignore[misc]


def _warned(report: ImportReport) -> list[str]:
    return [w.reason for w in report.warnings]


def _people(db: Session) -> tuple[int, int]:
    slug, sfx = db.info["slug"], uuid.uuid4().hex[:5]
    buyer = users.create_user(slug, f"b{sfx}@x.example", PASSWORD, "A Buyer", "Buyer")
    approver = users.create_user(slug, f"a{sfx}@x.example", PASSWORD, "An Approver", "Approver")
    return buyer, approver


# ---- the mode ---------------------------------------------------------------------------------


def test_the_same_dataset_imported_twice_changes_nothing(db: Session, dataset: Path) -> None:  # noqa: F811
    ids = db.execute(text("select id from price_record order by id")).scalars().all()
    before = _counts(db)

    again = import_masters(db, dataset)

    assert again.mode == "update" and again.rejected == []
    assert sum(again.added.values()) == sum(again.changed.values()) == sum(again.not_in_file.values()) == 0
    assert again.warnings == [] and again.changes == []
    assert again.rows_loaded == sum(again.unchanged.values()) == sum(before.values())
    assert _counts(db) == before
    assert db.execute(text("select id from price_record order by id")).scalars().all() == ids
    assert _audit(db, "masters.row_add") == _audit(db, "masters.row_change") == []
    assert [a[3]["mode"] for a in _audit(db, "masters.import")] == ["first", "update"]


def test_a_company_set_up_on_the_screens_is_never_replaced(control_db: str, dataset: Path) -> None:  # noqa: F811
    # given a company with a supplier made on the screens and no import yet
    slug = "u" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(slug).ok
    with Session(routing.engine_for(slug)) as db:
        db.execute(
            text("insert into supplier (code, legal_name, status) values ('SCR-1', 'Screen Supplier', 'active')")
        )
        db.commit()
        # when the dataset is imported
        report = import_masters(db, dataset)
        # then it is an update: the screen's supplier stays, the file's suppliers arrive as drafts
        assert report.mode == "update"
        assert _one(db, "select status from supplier where code = 'SCR-1'") == "active"
        assert report.not_in_file["supplier"] == 1
        assert _one(db, "select status from supplier where code = 'SUP-001'") == "draft"


# ---- suppliers: status, bank accounts, identity -----------------------------------------------


def test_a_blocked_supplier_and_its_changed_bank_account_stay_as_the_proposals_left_them(
    db: Session,
    dataset: Path,
    tmp_path: Path,  # noqa: F811
) -> None:
    # given SUP-001 blocked and SUP-002's account changed, each through its proposal and another person's approval
    buyer, approver = _people(db)
    pid = supplier_status.request_change(db, "SUP-001", "blocked", user(buyer), "Quality", today=date(2026, 11, 6))
    proposals.approve(db, pid, approver)
    asked = suppliers.change_bank(
        db, "SUP-002", {"account_no": "000155555555", "ifsc": "ICIC0004321"}, user(buyer), "Moved"
    )
    proposals.approve(db, asked["proposal"], approver)
    bank = db.execute(
        text("select account_no, verified, submitted_by from supplier_bank_account where supplier_code = 'SUP-002'")
    ).all()
    # when the original files are imported again (SUP-001 active, SUP-002's old account verified)
    report = import_masters(db, dataset)
    # then neither changes, and the report says so
    assert _one(db, "select status from supplier where code = 'SUP-001'") == "blocked"
    db.expire_all()
    assert (
        db.execute(
            text("select account_no, verified, submitted_by from supplier_bank_account where supplier_code = 'SUP-002'")
        ).all()
        == bank
    )
    assert "SUP-001: status stays blocked; an import does not change it" in _warned(report)
    assert "SUP-002: bank details kept; change them through a bank change" in _warned(report)
    assert report.changed.get("supplier_bank_account", 0) == report.added.get("supplier_bank_account", 0) == 0


def test_a_new_supplier_arrives_as_a_draft_with_an_unverified_account(
    db: Session,
    dataset: Path,
    tmp_path: Path,  # noqa: F811
) -> None:
    gstin = "27NEWSU0000N1Z"
    gstin += gstin_check_char(gstin)
    folder = _variant(
        dataset,
        tmp_path,
        supplier=_add(
            code="SUP-090",
            legal_name="New Supplier",
            pan="NEWSU0000N",
            gstin=gstin,
            state_code="27",
            status="active",
            payment_terms_code="NET30",
        ),  # fmt: skip
        supplier_bank_account=_add(
            supplier_code="SUP-090",
            bank_code="SBI",
            account_no="000190909090",
            ifsc="SBIN0001234",
            verified="true",
            verified_on="2026-01-01",
        ),  # fmt: skip
    )

    report = import_masters(db, folder)

    assert report.rejected == []
    assert _one(db, "select status from supplier where code = 'SUP-090'") == "draft"
    db.expire_all()
    account = db.execute(
        text("select verified, verified_on, submitted_by from supplier_bank_account where supplier_code = 'SUP-090'")
    ).one()
    assert tuple(account) == (False, None, "system")
    assert "SUP-090: added as a draft; a second person activates it" in _warned(report)
    assert (
        "SUP-090/****9090/SBIN0001234: bank account added unverified; a second person verifies it before any payment"
        in _warned(report)
    )
    added = {(e, k) for e, k, _, _ in _audit(db, "masters.row_add")}
    assert added == {("supplier", "SUP-090"), ("supplier_bank_account", "SUP-090/****9090/SBIN0001234")}
    assert all("000190909090" not in str(a) for a in _audit(db, "masters.row_add"))  # account numbers masked


def test_a_new_supplier_with_a_known_pan_is_left_out_with_its_children(
    db: Session,
    dataset: Path,
    tmp_path: Path,  # noqa: F811
) -> None:
    gstin = "29DECCD0000C1Z"
    gstin += gstin_check_char(gstin)
    folder = _variant(
        dataset,
        tmp_path,
        supplier=_add(
            code="SUP-091", legal_name="Same Person", pan="DECCD0000C", gstin=gstin, state_code="29", status="active"
        ),  # fmt: skip
        supplier_contact=_add(supplier_code="SUP-091", name="Desk", email="d@x.example"),
        supplier_bank_account=_add(
            supplier_code="SUP-091", bank_code="SBI", account_no="000191919191", ifsc="SBIN0001234", verified="false"
        ),  # fmt: skip
    )
    before = _counts(db)

    report = import_masters(db, folder)

    left = [(p.table, p.reason) for p in report.rejected]
    assert ("supplier", "possible duplicate: SUP-001 already has this PAN") in left
    assert {t for t, _ in left} == {"supplier", "supplier_contact", "supplier_bank_account"}
    assert _counts(db) == before


def test_an_active_suppliers_identity_stays_and_a_drafts_changes(
    db: Session,
    dataset: Path,
    tmp_path: Path,  # noqa: F811
) -> None:
    db.execute(text("update supplier set status = 'draft' where code = 'SUP-006'"))
    db.commit()
    g4, g6 = "27WESCW0000P2Z", "27WESPW0000S2Z"
    g4, g6 = g4 + gstin_check_char(g4), g6 + gstin_check_char(g6)
    folder = _variant(
        dataset, tmp_path, supplier=lambda rows: (_set("SUP-004", gstin=g4)(rows), _set("SUP-006", gstin=g6)(rows))
    )

    report = import_masters(db, folder)

    assert _one(db, "select gstin from supplier where code = 'SUP-004'") == "27WESCW0000P1Z5"
    assert (
        "SUP-004: gstin stays 27WESCW0000P1Z5 while the supplier is active; put it under review to change it "
        "(another person approves)" in _warned(report)
    )
    assert _one(db, "select gstin from supplier where code = 'SUP-006'") == g6
    (change,) = [a for a in _audit(db, "masters.row_change") if a[1] == "SUP-006"]
    assert change[2] == {"gstin": "27WESPW0000S1Z8"} and change[3] == {"gstin": g6}


def test_a_trade_name_and_a_contact_email_change_and_are_audited(
    db: Session,
    dataset: Path,
    tmp_path: Path,  # noqa: F811
) -> None:
    folder = _variant(
        dataset,
        tmp_path,
        supplier=_set("SUP-003", trade_name="Shree Wires"),
        supplier_contact=_set("SUP-003", email="orders@shreewires.example"),
    )

    report = import_masters(db, folder)

    assert _one(db, "select trade_name from supplier where code = 'SUP-003'") == "Shree Wires"
    assert _one(db, "select email from supplier_contact where supplier_code = 'SUP-003'") == "orders@shreewires.example"
    assert report.changed["supplier"] == report.changed["supplier_contact"] == 1
    changes = {(e, k): (b, a) for e, k, b, a in _audit(db, "masters.row_change")}
    assert changes[("supplier", "SUP-003")] == (
        {"trade_name": "Shree Wires and Electricals Pvt Ltd"},
        {"trade_name": "Shree Wires"},
    )
    assert changes[("supplier_contact", "SUP-003/Sales Desk")][1] == {"email": "orders@shreewires.example"}


def test_a_supplier_missing_from_the_file_stays_with_everything_it_has(
    db: Session,
    dataset: Path,
    tmp_path: Path,  # noqa: F811
) -> None:
    def without(rows: list[dict[str, str]]) -> None:
        rows[:] = [r for r in rows if "SUP-004" not in r.values()]

    files = ("supplier", "supplier_address", "supplier_bank_account", "supplier_contact", "supplier_document",
             "supplier_status_history", "supplier_category", "supplier_category_scope", "price_record", "contract")  # fmt: skip
    before = _counts(db)
    report = import_masters(db, _variant(dataset, tmp_path, **dict.fromkeys(files, without)))

    assert report.rejected == [] and _counts(db) == before
    assert report.not_in_file["supplier"] == 1 and report.not_in_file["supplier_bank_account"] == 1
    assert _one(db, "select count(*) from supplier_document where supplier_code = 'SUP-004'") > 0


# ---- controls: policies, rules, approvers, opening stock --------------------------------------


def test_policies_and_approval_rules_are_never_changed_by_a_later_import(
    db: Session,
    dataset: Path,
    tmp_path: Path,  # noqa: F811
) -> None:
    def rules(rows: list[dict[str, str]]) -> None:
        rows[0]["max_value"] = "20000.00"  # REQ-01 changed
        rows.append({**rows[1], "version": "2", "effective_from": "2026-12-01"})  # a new version of REQ-02

    folder = _variant(
        dataset,
        tmp_path,
        setting=lambda rows: (_set("budget_policy", value="warn")(rows), _add(key="new_policy", value="1")(rows)),
        approval_rule=rules,
    )

    report = import_masters(db, folder)

    assert settings.value(db, "budget_policy", "x") == "block"
    assert _one(db, "select count(*) from org_setting where key = 'new_policy'") == 0
    assert _one(db, "select max_value from approval_rule where rule_code = 'REQ-01'") == Decimal("10000.00")
    assert _one(db, "select count(*) from approval_rule where rule_code = 'REQ-02'") == 1
    setup = "after the first import (another administrator approves)"
    assert set(_warned(report)) == {
        f"budget_policy: not changed: policies are changed on the setup screen {setup}",
        f"new_policy: not added: policies are changed on the setup screen {setup}",
        f"REQ-01/1: not changed: approval rules are changed on the setup screen {setup}",
        f"REQ-02/2: not added: approval rules are changed on the setup screen {setup}",
    }


def test_a_department_head_stays_while_its_name_changes(db: Session, dataset: Path, tmp_path: Path) -> None:  # noqa: F811
    report = import_masters(
        db, _variant(dataset, tmp_path, department=_set("PRD", name="Production Shop", head_user="U02"))
    )

    db.expire_all()
    assert tuple(db.execute(text("select name, head_user from department where code = 'PRD'")).one()) == (
        "Production Shop",
        "U01",
    )
    assert _warned(report) == [
        "PRD: head_user stays U01; who approves is changed on the setup screen (another administrator approves)"
    ]


def test_opening_stock_is_loaded_once(db: Session, dataset: Path, tmp_path: Path) -> None:  # noqa: F811
    report = import_masters(db, _variant(dataset, tmp_path, stock_opening=_set("RM-01", qty="99")))

    assert _one(db, "select qty from stock_opening where item_code = 'RM-01'") == Decimal("21.000")
    assert _warned(report) == [
        "RM-01/PLT-CKN: kept as it is; opening stock is loaded once: correct stock with a stock count"
    ]


# ---- versions and second-person states --------------------------------------------------------


def test_a_new_contract_price_version_and_a_new_contract_arrive_as_drafts(
    db: Session,
    dataset: Path,
    tmp_path: Path,  # noqa: F811
) -> None:
    def next_version(rows: list[dict[str, str]]) -> None:
        rows.append({**rows[0], "version": "3", "price": "700.00", "valid_from": "2027-02-01"})

    folder = _variant(
        dataset,
        tmp_path,
        price_record=next_version,
        contract=_add(
            code="CNT-0090",
            supplier_code="SUP-002",
            type="rate_contract",
            valid_from="2026-12-01",
            valid_to="2027-11-30",
            status="active",
        ),  # fmt: skip
    )
    in_force = _one(db, "select price from price_record where contract_code = 'CNT-0001' and item_code = 'RM-01'")

    report = import_masters(db, folder)

    assert _one(db, "select status from price_record where contract_code = 'CNT-0001' and version = 3") == "draft"
    assert _one(db, "select status from contract where code = 'CNT-0090'") == "draft"
    assert (
        _one(db, "select price from price_record where item_code = 'RM-01' and status = 'active' and version = 1")
        == in_force
    )
    assert set(_warned(report)) == {
        "SUP-001/RM-01/CNT-0001/NOS/1/-/MH/3: added as a draft version; a second person activates it on the contract page",
        "CNT-0090: added as a draft; a second person activates it on the contract page",
    }


def test_a_changed_price_version_is_kept_and_its_row_keeps_its_id(
    db: Session,
    dataset: Path,
    tmp_path: Path,  # noqa: F811
) -> None:
    ids = db.execute(text("select id from price_record order by id")).scalars().all()
    report = import_masters(db, _variant(dataset, tmp_path, price_record=_set("SUP-001", price="999.00")))

    assert db.execute(text("select id from price_record order by id")).scalars().all() == ids
    assert _one(db, "select price from price_record where item_code = 'RM-01'") == Decimal("650.00")
    assert _warned(report) == [
        "SUP-001/RM-01/CNT-0001/NOS/1/-/MH/1: kept as it is; a price version does not change: add the next version"
    ]


def test_documents_benchmarks_history_and_flags_follow_their_rules(
    db: Session,
    dataset: Path,
    tmp_path: Path,  # noqa: F811
) -> None:
    def benchmark(rows: list[dict[str, str]]) -> None:
        rows.append({**rows[0], "effective_date": "2026-11-05", "source_ref": "new"})

    folder = _variant(
        dataset,
        tmp_path,
        supplier_document=_add(supplier_code="SUP-001", doc_type="iso_cert", number="ISO-1", verified="true"),
        market_benchmark=benchmark,
        supplier_status_history=_add(
            supplier_code="SUP-001",
            at="2026-01-01T10:00:00+05:30",
            from_status="active",
            to_status="active",
            reason="x",
            by_user="U03",
        ),  # fmt: skip
        supplier_flag=_add(
            supplier_code="SUP-001",
            kind="same_contact",
            other_ref="SUP-002",
            detail="d",
            raised_at="2026-11-01T10:00:00+05:30",
        ),  # fmt: skip
    )

    report = import_masters(db, folder)
    again = import_masters(db, folder)

    assert _one(db, "select verified from supplier_document where number = 'ISO-1'") is False
    db.expire_all()
    new = db.execute(text("select status, verified_by from market_benchmark where source_ref = 'new'")).one()
    assert tuple(new) == ("unverified", None)
    assert _one(db, "select count(*) from supplier_status_history where reason = 'x'") == 0
    assert _one(db, "select count(*) from supplier_flag where detail = 'd'") == 1  # added once
    assert report.added == {**dict.fromkeys(report.added, 0), "supplier_document": 1, "market_benchmark": 1,
                            "supplier_flag": 1}  # fmt: skip
    assert sum(again.added.values()) == 0
    reasons = _warned(report)
    assert "SUP-001/iso_cert/ISO-1: added unverified; verify it on the supplier page" in reasons
    assert any(r.endswith(": added unverified; verify it on the benchmarks page") for r in reasons)
    assert "SUP-001: not added: the status history of an existing supplier is written by its status changes" in reasons


def test_a_key_the_company_holds_twice_is_left_out(db: Session, dataset: Path, tmp_path: Path) -> None:  # noqa: F811
    db.execute(
        text(
            "insert into supplier_contact (supplier_code, name, role, email) "
            "values ('SUP-001', 'Sales Desk', 'sales', 'other@x.example')"
        )
    )
    db.commit()

    report = import_masters(db, dataset)

    assert [(p.table, p.reason) for p in report.rejected] == [
        (
            "supplier_contact",
            "SUP-001/Sales Desk: the company has 2 rows with this key; correct them on the screen first",
        )
    ]


def test_rows_the_orders_and_receipts_point_at_never_stop_a_later_import(db: Session, dataset: Path) -> None:  # noqa: F811
    # given a PO line bound to a contract price, an RFQ with a delivery location and a vendor statement
    price = _one(db, "select id from price_record where item_code = 'RM-01'")
    db.execute(text("insert into rfq (no, due_date, type, status, delivery_location) values ('RFQ-X', '2026-12-01', 'commercial', 'draft', 'PLT-CKN')"))  # fmt: skip
    db.execute(
        text(
            "insert into vendor_statement (supplier_code, period_from, period_to, file_hash, lines, matched, "
            "amount_differs, only_theirs, only_ours, imported_by) values ('SUP-001', '2026-10-01', '2026-10-31', 'h', "
            "0, 0, 0, 0, 0, 'x')"
        )
    )
    db.execute(
        text(
            "insert into po (no, po_type, supplier_code, ship_to_location, taxable, cgst, sgst, igst, total, status) "
            "values ('PO-X', 'standard', 'SUP-001', 'PLT-CKN', 0, 0, 0, 0, 0, 'draft')"
        )
    )
    db.execute(
        text(
            "insert into po_line (po_no, line_no, request_no, request_line_no, item_code, qty, unit_price, gst_rate, "
            "taxable, cgst, sgst, igst, source, status, price_record_id) values ('PO-X', 1, 'R', 1, 'RM-01', 1, 650, "
            "18, 650, 0, 0, 0, 'contract', 'open', :p)"
        ),
        {"p": price},
    )
    db.commit()

    report = import_masters(db, dataset)

    assert report.rejected == [] and report.mode == "update"
    assert _one(db, "select price_record_id from po_line where po_no = 'PO-X'") == price


def test_a_dry_run_reports_the_same_and_writes_nothing(db: Session, dataset: Path, tmp_path: Path) -> None:  # noqa: F811
    folder = _variant(
        dataset, tmp_path, supplier=_set("SUP-003", trade_name="Shree Wires"), department=_set("PRD", head_user="U02")
    )
    before, audited = _counts(db), _one(db, "select count(*) from audit_event")

    dry = import_masters(db, folder, dry_run=True)

    assert _counts(db) == before and _one(db, "select count(*) from audit_event") == audited
    assert _one(db, "select trade_name from supplier where code = 'SUP-003'") != "Shree Wires"
    real = import_masters(db, folder)
    assert (dry.changed, dry.added, dry.unchanged, dry.not_in_file) == (
        real.changed,
        real.added,
        real.unchanged,
        real.not_in_file,
    )
    assert _warned(dry) == _warned(real) and len(dry.changes) == len(real.changes) == 1


def test_an_import_into_one_company_changes_nothing_in_another(
    db: Session,
    dataset: Path,
    tmp_path: Path,  # noqa: F811
) -> None:
    other = "u" + uuid.uuid4().hex[:8]
    assert tenants.create_tenant(other).ok
    with Session(routing.engine_for(other)) as b:
        import_masters(b, dataset)
        counts, audited = _counts(b), _one(b, "select count(*) from audit_event")

        import_masters(db, _variant(dataset, tmp_path, supplier=_set("SUP-003", trade_name="Shree Wires")))

        assert _counts(b) == counts and _one(b, "select count(*) from audit_event") == audited
        assert _one(b, "select trade_name from supplier where code = 'SUP-003'") != "Shree Wires"


# ---- the command line -------------------------------------------------------------------------


def _cli(slug: str, folder: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "p2p.cli", "import", "masters", slug, str(folder), *extra],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        env={**os.environ, "CONTROL_DATABASE_URL": os.environ["CONTROL_DATABASE_URL"]},
        check=False,
    )


def test_the_command_line_says_what_an_update_did(db: Session, dataset: Path, tmp_path: Path) -> None:  # noqa: F811
    folder = _variant(dataset, tmp_path, supplier=_set("SUP-003", trade_name="Shree Wires"))

    dry = _cli(db.info["slug"], folder, "--dry-run")
    real = _cli(db.info["slug"], folder)

    assert dry.returncode == real.returncode == 0, dry.stderr
    assert dry.stdout.startswith("update by key (dry run, nothing written): would add 0, would change 1, unchanged ")
    lines = real.stdout.splitlines()
    assert lines[0].startswith("update by key: added 0, changed 1, unchanged ")
    assert "  supplier: 0 added, 1 changed, 8 unchanged, 0 not in the file" in lines
    assert "  changed   supplier SUP-003: trade_name 'Shree Wires and Electricals Pvt Ltd' -> 'Shree Wires'" in lines
    spoiled = _cli(db.info["slug"], _variant(dataset, tmp_path, supplier=_set("SUP-003", pan="BAD")))
    assert spoiled.returncode == 1 and "  left out  supplier line 4: pan 'BAD' is not a valid PAN" in spoiled.stdout
