"""One live invoice per key and IRN (block 10, task 3.3): the FR33 key, cancelled copies, the IRN, the affordable race
and the migration's recompute. Given a bill, when it is entered again, then it is a cancelled copy or refused.
"""

import importlib.util
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session
from test_non_po_invoices import _enter, _row, bills  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.invoices import service as invoices
from p2p.platform import routing

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "migration_0170",
    ROOT / "backend" / "p2p" / "platform" / "migrations" / "tenant" / "versions" / "0170_invoices_without_order.py",
)
assert _spec is not None and _spec.loader is not None
migration = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migration)
D = Decimal
TODAY = date(2026, 11, 23)


def test_d1_the_key_normalises_and_a_cancelled_original_is_no_twin(bills: Session) -> None:  # noqa: F811
    original = _enter(bills, number="SNL/2627/0458", on=TODAY, not_duplicate_reason="first")

    spaced = _enter(bills, number="snl 2627 0458", on=TODAY)
    assert (spaced.status, spaced.duplicate_of) == ("cancelled", original.no)

    next_year = _enter(bills, number="SNL/2627/0458", on=date(2027, 4, 5))
    assert next_year.status == "awaiting_authorisation" and _row(bills, next_year.no)["fy"] == "2027-28"
    credit = invoices.enter_invoice(
        bills,
        invoices.InvoiceIn(
            supplier_code="S1", supplier_invoice_no="SNL/2627/0458", invoice_date=TODAY, type="credit_note",
            ref_invoice_no=original.no, lines=[],
        ),
        bills.info["bills"]["ap"], as_of=TODAY,
    )  # fmt: skip
    assert credit.status == "approved" and credit.duplicate_of is None


def test_d2_two_entries_at_once_leave_one_live_and_one_cancelled_copy(bills: Session) -> None:  # noqa: F811
    slug = bills.info["slug"]
    document = bills.execute(
        text(
            "insert into document (doc_type, filename, content_type, size, sha256, storage_key) values "
            "('invoice_evidence', 'race.pdf', 'application/pdf', 1, 'race', 'race') returning id"
        )
    ).scalar_one()
    bills.commit()
    user = bills.info["bills"]["ap"]

    def enter_once(_: int) -> str:
        with Session(routing.engine_for(slug)) as s:
            try:
                done = invoices.enter_invoice(
                    s,
                    invoices.InvoiceIn(
                        supplier_code="S1",
                        supplier_invoice_no="RACE/2627/1",
                        invoice_date=TODAY,
                        kind="non_po",
                        category_code="UTL",
                        cost_centre_code="CC-ADM",
                        documents=[{"id": int(document), "kind": "copy"}],
                        lines=[invoices.InvLine(None, None, D("1"), D("100"), gst_rate=D("18"), description="Line")],
                    ),
                    user,
                    as_of=TODAY,
                )
                return done.status
            except invoices.InvoiceError:
                return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(enter_once, range(2)))

    live = bills.execute(text("select count(*) from invoice where dedupe_key = 'RACE26271' and status <> 'cancelled'")).scalar_one()  # fmt: skip
    assert live == 1
    assert sorted(outcomes) in (["awaiting_authorisation", "cancelled"], ["awaiting_authorisation", "refused"])


def test_d3_the_same_irn_under_another_number_is_a_cancelled_copy(bills: Session) -> None:  # noqa: F811
    irn = "A" * 64
    first = _enter(bills, number="SNL/2627/0501", on=TODAY, irn=irn)
    second = _enter(bills, number="SNL/2627/0502", on=TODAY, irn=irn)

    assert (second.status, second.duplicate_of) == ("cancelled", first.no)
    assert "same IRN" in _row(bills, second.no)["block_reason"]


def test_d5_the_migration_recomputes_every_key_and_lists_a_live_clash(bills: Session, owner_session: Any) -> None:  # noqa: F811
    assert migration._key("PIS/2425/0036") == "PIS242536"
    assert migration._key("INV-001") == migration._key("inv 1") == "INV1"
    assert migration._key("INV-000") == "INV0"
    assert migration._key("0") == "0"

    # the data before 0170: the old key (leading zeros kept), one cancelled and one live
    with owner_session(bills.info["slug"]) as o:
        o.execute(text("drop index invoice_live_key"))
        o.execute(
            text(
                "insert into invoice (no, type, supplier_code, supplier_invoice_no, dedupe_key, invoice_date, fy, "
                "received_on, taxable, total, status) values "
                "('OLD-1', 'invoice', 'S1', 'INV-001', 'INV001', '2026-11-01', '2026-27', '2026-11-01', 100, 118, 'cancelled'), "
                "('OLD-2', 'invoice', 'S1', 'INV-1', 'INV1', '2026-11-02', '2026-26', '2026-11-02', 100, 118, 'awaiting_authorisation')"
            )
        )
        o.commit()
        migration._recompute(o.connection(), migration._key)
        keys = dict(o.execute(text("select no, dedupe_key from invoice where no like 'OLD-%'")).all())
        assert keys == {"OLD-1": "INV1", "OLD-2": "INV1"}
        assert migration._clashes(o.connection()) == []  # one is cancelled
        o.execute(
            text(
                "insert into invoice (no, type, supplier_code, supplier_invoice_no, dedupe_key, invoice_date, fy, "
                "received_on, taxable, total, status) values "
                "('OLD-3', 'invoice', 'S1', 'inv 0001', 'INV1', '2026-11-03', '2026-26', '2026-11-03', 100, 118, 'awaiting_authorisation')"
            )
        )
        o.commit()
        clashes = migration._clashes(o.connection())
        assert len(clashes) == 1 and "OLD-2, OLD-3" in clashes[0]
