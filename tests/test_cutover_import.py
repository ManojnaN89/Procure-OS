"""The cutover import of open orders, receipts, invoices and advances (task 7.4): every check with its reason, dry runs
that write nothing, a second load that loads nothing, receipts that post no stock, advances recovered by the next
invoice, the year to date for tax at source, and number counters raised above the loaded numbers."""

import csv
import shutil
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.invoices import payment, reconciliation, service
from p2p.main import app
from p2p.platform import cutover, cutover_import, demo, numbering, ratelimit, routing, tenants, users
from p2p.platform.company_state import StateError
from p2p.platform.tokens import create_access_token

client = TestClient(app)
DOMAIN = "apexpumps.example"


@dataclass
class Apex:
    slug: str
    dataset: Path
    finance: int

    def db(self) -> Session:
        return Session(routing.engine_for(self.slug))

    def load(self, folder: Path, *, dry_run: bool = False) -> cutover_import.CutoverReport:
        with self.db() as db:
            return cutover_import.import_cutover(db, folder, user_id=self.finance, dry_run=dry_run)

    def count(self, table: str) -> int:
        with self.db() as db:
            return int(db.execute(text(f"select count(*) from {table}")).scalar_one())  # noqa: S608 - fixed names


@pytest.fixture(scope="module")
def apex(control_db: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Apex]:
    slug = "ci" + uuid.uuid4().hex[:8]
    dataset = tmp_path_factory.mktemp("apex") / "dataset"
    demo.reset_company(demo.company("apex"), dataset, credentials=None, slug=slug, state="setup")
    finance = users.user_id_by_email(slug, f"finance@{DOMAIN}")
    assert finance
    yield Apex(slug, dataset, finance)
    routing.clear_engine_cache()
    tenants.drop_tenant(slug)


def _truth(dataset: Path) -> list[dict[str, str]]:
    with (dataset / "truth" / "truth_cutover.csv").open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_the_import_is_only_for_a_company_in_cutover(apex: Apex) -> None:
    with (
        apex.db() as db,
        pytest.raises(StateError, match="cutover import is only for a company in cutover; it is setup"),
    ):
        cutover_import.import_cutover(db, apex.dataset / "cutover", user_id=apex.finance, dry_run=True)
    headers = {"Authorization": f"Bearer {create_access_token(apex.finance, apex.slug, 'Finance')}"}
    ratelimit.reset()
    files = [("files", ("po.csv", (apex.dataset / "cutover" / "po.csv").read_bytes(), "text/csv"))]
    r = client.post("/api/company/cutover/import", files=files, data={"dry_run": "true"}, headers=headers)
    assert r.status_code == 409 and "it is setup" in r.json()["detail"]
    cutover.archive_and_rebuild(apex.slug, reason="cutover import test", operator="Test Operator")


def test_every_planted_row_is_left_out_with_its_reason_and_a_dry_run_writes_nothing(apex: Apex) -> None:
    bad = apex.dataset / "cutover-bad"
    counts = {t: apex.count(t) for t in ("po", "invoice", "audit_event", "stock_movement", "cutover_import")}
    dry = apex.load(bad, dry_run=True)
    assert {t: apex.count(t) for t in counts} == counts
    reasons = [f"{r.file}:{r.line} {r.reason}" for r in dry.rejected]
    for t in _truth(apex.dataset):
        if t["expected"] == "left_out":
            assert any(t["key"] in r and t["reason_contains"] in r for r in reasons), (t, reasons)
    assert not dry.control_ok and dry.control["invoice"]["ok"] is False

    real = apex.load(bad)
    assert len(real.rejected) == len(dry.rejected) and not real.control_ok
    with apex.db() as db:
        row = db.execute(text("select rejected, control_ok from cutover_import order by id desc limit 1")).one()
        assert row[0] == len(real.rejected) and row[1] is False
        assert (
            db.execute(text("select count(*) from po where no in ('PO-CKN-2627-00203', 'PO-CKN-2627-00204')")).scalar()
            == 0
        )
    assert apex.count("stock_movement") == counts["stock_movement"]


def test_the_good_files_load_the_rest_and_a_second_load_loads_nothing(apex: Apex) -> None:
    good = apex.dataset / "cutover"
    moves = apex.count("stock_movement")
    first = apex.load(good)
    assert not first.rejected and first.control_ok
    assert first.loaded["po"] == 1 and first.loaded["goods_receipt"] == 1  # what the bad files left out
    second = apex.load(good)
    assert not any(second.loaded.values()) and second.already["po"] == 31 and second.already["invoice"] == 44
    assert apex.count("stock_movement") == moves  # cutover receipts post no stock
    with apex.db() as db:
        grni = reconciliation.received_vs_invoiced(db, date(2026, 10, 31))
        assert grni["not_invoiced"] == Decimal("3600.00")
        blocked = db.execute(text("select no from invoice where status = 'blocked'")).scalars().all()
        assert blocked == ["PI-2627-000247"]
        carriers = db.execute(
            text("select count(*) from request where channel = 'cutover' and no like 'CUT-%'")
        ).scalar()
        assert carriers == 31
        assert db.execute(text("select distinct source from po_line")).scalars().all() == ["cutover"]
        # each order brought over is its version 1, made by the cutover; an acknowledged one is acknowledged at 1
        kept = db.execute(
            text("select count(*), count(*) filter (where made_by = 'cutover' and version = 1) from po_version")
        ).one()
        assert tuple(kept) == (31, 31)
        assert not db.execute(
            text(
                "select no from po where acknowledged_at is not null and acknowledged_version is distinct from 1 "
                "union all select no from po where acknowledged_at is null and acknowledged_version is not null"
            )
        ).all()
        assert not [a for a in payment.due_advances(db) if a["no"] == "PO-CKN-2627-00202"]
        assert db.execute(text("select count(*) from audit_event where action = 'cutover.invoice'")).scalar() == 44


def test_a_changed_document_is_left_out_as_already_loaded_and_changed(apex: Apex, tmp_path: Path) -> None:
    folder = tmp_path / "changed"
    shutil.copytree(apex.dataset / "cutover", folder)
    path = folder / "invoice.csv"
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    rows[0]["total"] = str(Decimal(rows[0]["total"]) + 1)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    report = apex.load(folder, dry_run=True)
    assert [r.reason for r in report.rejected] == [
        f"{rows[0]['no']}: already loaded; changed: total {Decimal(rows[0]['total']) - 1} -> {rows[0]['total']}"
    ]


def test_numbers_continue_above_the_loaded_ones(apex: Apex) -> None:
    with apex.db() as db:
        po = numbering.next_number(db, "po", date(2026, 11, 6), location_code="PLT-CKN")
        invoice = numbering.next_number(db, "invoice_booking", date(2026, 11, 6))
        db.rollback()
    assert po == "PO-CKN-2627-00204" and invoice == "PI-2627-000281"


def test_the_next_invoice_takes_the_advance_off_and_tax_counts_the_year_so_far(apex: Apex) -> None:
    with apex.db() as db:
        ytd = db.execute(text("select taxable from cutover_supplier_ytd where supplier_code = 'SUP-005'")).scalar_one()
        on_file = payment._cumulative(db, "SUP-005", date(2026, 11, 6))
        assert on_file >= ytd > 0
        sup = db.execute(text("select gstin from supplier where code = 'SUP-006'")).scalar_one()
        data = service.InvoiceIn(
            supplier_code="SUP-006",
            supplier_invoice_no="TEST-ADV-1",
            invoice_date=date(2026, 11, 6),
            lines=[service.InvLine("PO-CKN-2627-00202", 1, Decimal(1), Decimal("12000.00"))],
            supplier_gstin=sup,
        )
        entered = service.enter_invoice(db, data, apex.finance)
        if db.execute(text("select booked_at from invoice where no = :n"), {"n": entered.no}).scalar() is None:
            payment.book_invoice(db, entered.no)
            db.commit()
        used = db.execute(text("select used_gross from advance where payment_no = 'PAY-2627-000281'")).scalar_one()
    assert used == Decimal("4248.00")
