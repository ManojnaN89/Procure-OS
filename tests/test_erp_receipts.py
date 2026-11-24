"""Receipts from the ERP with their own keys (task 4.5, D-20): loaded once, no stock, a reverses row."""

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session
from test_orders import shop  # noqa: F401 - fixtures
from test_receiving import _line, _on_hand, _sent, store  # noqa: F401 - fixtures
from test_sourcing import Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.receiving import erp_receipts

HEADER = (
    "erp_receipt_no,erp_line,po_no,po_line_no,received_on,qty_received,qty_rejected,reject_reason,challan,"
    "received_by,serials,reverses\n"
)


def _file(tmp_path: Path, rows: list[str]) -> Path:
    path = tmp_path / "goods_receipt.csv"
    path.write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_given_erp_rows_when_they_are_imported_then_order_and_budget_move_but_stock_does_not(
    store: Session, outbox: Outbox, tmp_path: Path
) -> None:  # noqa: F811
    po_no, _ = _sent(store, qty="100")
    path = _file(
        tmp_path,
        [
            f"ERP-GR-7001,1,{po_no},1,2026-11-06,40,,,,DC/1,,,",
            f"ERP-GR-7002,1,{po_no},1,2026-11-06,70,,,,DC/2,,,",
            "ERP-GR-7003,1,PO-NOPE,1,2026-11-06,10,,,,DC/3,,,",
            f"ERP-GR-7004,1,{po_no},1,2026-11-06,60,,,,DC/4,,,",
        ],
    )

    report = erp_receipts.import_receipts(store, store.info["slug"], path, user_id=1)

    assert report.loaded["goods_receipt"] == 2 and report.already == 0
    assert len(report.rejected) == 2
    reasons = " ".join(p.reason for p in report.rejected)
    assert "more than the" in reasons and "does not exist" in reasons
    assert store.execute(text("select received_qty from po_line where po_no = :p"), {"p": po_no}).scalar_one() == 100
    assert store.execute(text("select count(*) from stock_movement")).scalar_one() == 0
    assert _on_hand(store) == 0
    budget = store.execute(text("select coalesce(sum(amount), 0) from budget_actual where po_no = :p"), {"p": po_no}).scalar_one()
    assert budget == 10000.00
    assert store.execute(text("select source, external_ref from goods_receipt order by no")).all() == [
        ("erp", "ERP-GR-7001"),
        ("erp", "ERP-GR-7004"),
    ]


def test_a_second_import_loads_nothing_and_a_changed_row_is_listed_not_applied(
    store: Session, outbox: Outbox, tmp_path: Path
) -> None:  # noqa: F811
    po_no, _ = _sent(store, qty="100")
    first = _file(tmp_path, [f"ERP-GR-7001,1,{po_no},1,2026-11-06,40,,,,DC/1,,,"])
    erp_receipts.import_receipts(store, store.info["slug"], first, user_id=1)

    again = erp_receipts.import_receipts(store, store.info["slug"], first, user_id=1)
    assert again.loaded["goods_receipt"] == 0 and again.already == 1

    changed = _file(tmp_path, [f"ERP-GR-7001,1,{po_no},1,2026-11-06,55,,,,DC/1,,,"])
    report = erp_receipts.import_receipts(store, store.info["slug"], changed, user_id=1)
    assert report.loaded["goods_receipt"] == 0
    assert report.changed_rows == [
        "already loaded; changed: qty_received 40 -> 55 (ERP-GR-7001 line 1)"
    ]
    assert store.execute(text("select received_qty from po_line where po_no = :p"), {"p": po_no}).scalar_one() == 40


def test_a_reverses_row_reverses_that_erp_receipt_and_the_app_refuses_its_own(
    store: Session, outbox: Outbox, tmp_path: Path
) -> None:  # noqa: F811
    po_no, _ = _sent(store, qty="100")
    erp_receipts.import_receipts(
        store,
        store.info["slug"],
        _file(tmp_path, [f"ERP-GR-7004,1,{po_no},1,2026-11-06,60,,,,DC/4,,,"]),
        user_id=1,
    )
    report = erp_receipts.import_receipts(
        store,
        store.info["slug"],
        _file(
            tmp_path,
            [",".join(["ERP-GR-7005", "1", "", "", "", "", "", "", "", "", "", "ERP-GR-7004"])],
        ),
        user_id=1,
    )

    assert report.reversed == 1
    assert store.execute(text("select received_qty from po_line where po_no = :p"), {"p": po_no}).scalar_one() == 0
    assert store.execute(
        text("select sum(amount) from budget_actual where po_no = :p and source = 'receipt_reversal'"), {"p": po_no}
    ).scalar_one() == -6000.00
    assert store.execute(text("select status from goods_receipt where external_ref = 'ERP-GR-7004'")).scalar_one() == "reversed"


def test_a_reverses_row_for_a_receipt_never_loaded_is_left_out(
    store: Session, outbox: Outbox, tmp_path: Path
) -> None:  # noqa: F811
    _sent(store, qty="10")
    report = erp_receipts.import_receipts(
        store, store.info["slug"], _file(tmp_path, [",".join(["ERP-GR-7006", "1", "", "", "", "", "", "", "", "", "", "ERP-GR-4999"])]), user_id=1
    )
    assert report.rejected[0].reason == "ERP-GR-4999 has not been loaded"


def test_a_dry_run_writes_nothing(store: Session, outbox: Outbox, tmp_path: Path) -> None:  # noqa: F811
    po_no, _ = _sent(store, qty="100")
    path = _file(tmp_path, [f"ERP-GR-7001,1,{po_no},1,2026-11-06,40,,,,DC/1,,,"])

    report = erp_receipts.import_receipts(store, store.info["slug"], path, user_id=1, dry_run=True)

    assert report.loaded["goods_receipt"] == 1
    assert store.execute(text("select count(*) from goods_receipt")).scalar_one() == 0
    assert _line(store) == (0, "open")
