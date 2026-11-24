"""Receipts from the client's ERP (task 4.5, D-20): its own receipt numbers as the source key.

The file (CSV or Excel, our template or the client's columns through `erp_column_map`) has one row per receipt line, keyed
by `(erp_receipt_no, erp_line)`. Rows are read in file order and one platform receipt is made per ERP receipt number and
order, posted through the ordinary goods receipt with `source = 'erp'` and the ERP's own reference. The ERP's inspection
is taken as done (accepted = received - rejected); no stock moves, because the ERP's stock arrives through
`import stock` (OP-17), and a quantity above the room is left out rather than held (OP-18). A second import loads nothing;
a changed row is listed and not applied. A `reverses` row reverses the platform receipt of that ERP number through the
reversal service, without the window.
"""

import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_qty
from p2p.formulas.words import count
from p2p.masters.importer import ImportReport, Problem, read_table
from p2p.masters.schema import Table
from p2p.platform import audit
from p2p.platform.sod import Actor
from p2p.receiving import goods, reversal
from p2p.requests import demand
from p2p.requests.demand_files import ERP_RECEIPT

KIND = "goods_receipt"


@dataclass
class ReceiptReport(ImportReport):
    already: int = 0  # rows loaded before and unchanged
    changed_rows: list[str] = field(default_factory=list)  # "already loaded; changed: ..."
    reversed: int = 0
    mapping_version: int = 0

    @property
    def left_out(self) -> list[Problem]:
        return self.rejected


def spec() -> Table:
    return ERP_RECEIPT.spec()


def template_csv() -> str:
    """Kept out: `requests/demand.template_csv('goods_receipt')` gives this file's template (block 9 adds it there)."""
    from p2p.requests import demand  # noqa: PLC0415 - the one template writer

    return demand.template_csv(KIND)


def _file(source: Path) -> Path | None:
    if source.is_file():
        return source
    folder = source / "inputs" if (source / "inputs").is_dir() else source
    for ext in (".csv", ".xlsx"):
        found = folder / f"{KIND}{ext}"
        if found.is_file():
            return found
    return None


def _user_for_receiver(db: Session, tenant: str | None, name: str | None) -> tuple[int | None, str | None]:
    """The login a row's ERP user maps to (an org code or an email), else the label recorded instead (OP-19)."""
    if not name or not name.strip():
        return None, "erp:unknown"
    value = name.strip()
    row = db.execute(
        text("select code, email from org_user where lower(code) = lower(:v) or lower(email) = lower(:v)"),
        {"v": value},
    ).first()
    if row is None:
        return None, f"erp:{value}"
    from p2p.platform import notifications  # noqa: PLC0415 - a login is found by the person's email

    uid = notifications.login_of(db, tenant, str(row[0])) if tenant else None
    return (uid, None) if uid is not None else (None, f"erp:{value}")


def _existing(db: Session, erp_no: str, erp_line: str) -> dict[str, Any] | None:
    row = (
        db.execute(
            text(
                "select g.no, g.po_no, g.delivery_challan_no, g.received_by, l.qty_received, l.qty_rejected, "
                "l.reject_reason from goods_receipt g join gr_line l on l.gr_no = g.no "
                "where g.external_ref = :e and l.external_line = :l order by g.no limit 1"
            ),
            {"e": erp_no, "l": erp_line},
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


def _changes(old: dict[str, Any], row: dict[str, Any]) -> list[str]:
    out = []
    for column, key in (
        ("qty_received", "qty_received"),
        ("qty_rejected", "qty_rejected"),
        ("reject_reason", "reject_reason"),
    ):
        before, after = old.get(column), row.get(key)
        if column in ("qty_received", "qty_rejected"):
            before, after = Decimal(before or 0), Decimal(after or 0)
        if (before or None) != (after or None):
            if column in ("qty_received", "qty_rejected"):
                out.append(f"{column} {format_qty(before)} -> {format_qty(after)}")
            else:
                shown_before = "-" if before is None else str(before)
                shown_after = "-" if after is None else str(after)
                out.append(f"{column} {shown_before} -> {shown_after}")
    return out


def import_receipts(
    db: Session,
    tenant: str | None,
    source: Path,
    *,
    actor: Actor = audit.SYSTEM,
    dry_run: bool = False,
    user_id: int = 0,
) -> ReceiptReport:
    """Read the ERP's receipt file (or a dataset folder's inputs/) and post what is new. Commits unless dry."""
    started = time.monotonic()
    report = ReceiptReport(dry_run=dry_run)
    path = _file(source)
    if path is None:
        report.rejected.append(Problem(KIND, 0, f"no receipt file in {source} (looked for {KIND}.csv or .xlsx)"))
        return report
    version, mapping = demand.current_mapping(db, KIND)
    report.mapping_version = version
    rows = read_table(path, spec(), report, rename=mapping)
    plan: dict[tuple[str, str], list[dict[str, Any]]] = {}
    order: list[tuple[str, str]] = []
    loaded = 0
    for line, row in rows:
        reason = None
        if not row.get("erp_receipt_no") or not row.get("erp_line"):
            reason = "erp_receipt_no and erp_line are required"
        elif row.get("reverses"):
            reason = None
        elif row.get("po_no") is None or row.get("received_on") is None:
            reason = "po_no and received_on are required"
        elif row.get("qty_received") is None or row["qty_received"] <= 0:
            reason = "qty_received must be above zero"
        if reason:
            report.rejected.append(Problem(KIND, line, reason))
            continue
        if row.get("reverses"):
            target = db.execute(
                text("select no from goods_receipt where external_ref = :e order by created_at desc, no desc limit 1"),
                {"e": row["reverses"]},
            ).scalar()
            if target is None:
                report.rejected.append(Problem(KIND, line, f"{row['reverses']} has not been loaded"))
                continue
            if dry_run:
                report.reversed += 1
                continue
            try:
                reversal.reverse_receipt(
                    db,
                    tenant,
                    str(target),
                    user_id,
                    f"Reversed by the ERP ({row['erp_receipt_no']})",
                    as_of=row.get("received_on") or business_today(),
                    force=True,
                    erp=True,
                )
            except reversal.ReversalError as exc:
                db.rollback()
                report.rejected.append(Problem(KIND, line, str(exc)))
                continue
            report.reversed += 1
            continue
        old = _existing(db, str(row["erp_receipt_no"]), str(row["erp_line"]))
        if old is not None:
            changed = _changes(old, row)
            if changed:
                report.changed_rows.append(
                    f"already loaded; changed: {', '.join(changed)} ({row['erp_receipt_no']} line {row['erp_line']})"
                )
            else:
                report.already += 1
            continue
        reason = _check_row(db, row)
        if reason:
            report.rejected.append(Problem(KIND, line, reason))
            continue
        key = (str(row["erp_receipt_no"]), str(row["po_no"]))
        if key not in plan:
            plan[key] = []
            order.append(key)
        plan[key].append(row)
    for key in order:
        group = plan[key]
        if dry_run:
            loaded += len(group)
            continue
        first = group[0]
        receiver, label = _user_for_receiver(db, tenant, first.get("received_by"))
        try:
            goods.post_receipt(
                db,
                str(first["po_no"]),
                [
                    goods.LineIn(
                        int(r["po_line_no"]),
                        Decimal(r["qty_received"]),
                        Decimal(r["qty_rejected"]) if r.get("qty_rejected") is not None else None,
                        r.get("reject_reason"),
                        [s.strip() for s in str(r.get("serials") or "").split(";") if s.strip()] or None,
                    )
                    for r in group
                ],
                goods.Header(challan=first.get("challan"), remarks=f"From the ERP ({first['erp_receipt_no']})"),
                receiver or user_id,
                as_of=first["received_on"],
                tenant=tenant,
                source="erp",
                external_ref=str(first["erp_receipt_no"]),
                external_lines={int(r["po_line_no"]): str(r["erp_line"]) for r in group},
                erp=True,
                received_by=label,
                skip_conflict=receiver is None,
                move_stock=False,
            )
        except goods.ReceiptError as exc:
            db.rollback()
            for _ in group:
                report.rejected.append(Problem(KIND, 0, str(exc)))
            continue
        loaded += len(group)
    report.loaded[KIND] = loaded
    if not dry_run:
        audit.record(
            db,
            actor,
            "erp_receipt.import",
            "dataset",
            path.name,
            after={
                "loaded": loaded,
                "already": report.already,
                "changed": len(report.changed_rows),
                "left_out": len(report.rejected),
                "reversed": report.reversed,
                "mapping_version": version,
            },
        )
        db.commit()
    else:
        db.rollback()
    report.seconds = round(time.monotonic() - started, 2)
    return report


def _check_row(db: Session, row: dict[str, Any]) -> str | None:
    """Why one ERP row is left out, before any receipt is made for it."""
    found = db.execute(text("select * from po where no = :n"), {"n": row["po_no"]}).mappings().first()
    if found is None:
        return f"order {row['po_no']} does not exist"
    if found["status"] not in goods.RECEIVABLE:
        return f"{row['po_no']} is {found['status']}; goods can only be received against an order that was sent"
    line = (
        db.execute(
            text("select * from po_line where po_no = :p and line_no = :l"),
            {"p": row["po_no"], "l": row["po_line_no"]},
        )
        .mappings()
        .first()
    )
    if line is None:
        return f"line {row['po_line_no']} is not on {row['po_no']}"
    if line["status"] not in ("open", "part_received"):
        return f"line {row['po_line_no']} is not open"
    from p2p.receiving.kinds import service_lines  # noqa: PLC0415 - a service is confirmed in the app

    if int(row["po_line_no"]) in service_lines(db, str(row["po_no"])):
        return f"line {row['po_line_no']} is a service: confirm services in the app"
    room = goods.room_for(
        db, str(row["po_no"]), int(row["po_line_no"]), line["item_code"], Decimal(line["qty"]), row["received_on"]
    )
    if Decimal(row["qty_received"]) > room:
        return f"line {row['po_line_no']}: {row['qty_received']} is more than the {room} that may still be received"
    item = db.execute(text("select serialised from item where code = :c"), {"c": line["item_code"]}).first()
    if item is not None and item[0]:
        numbers = [s.strip() for s in str(row.get("serials") or "").split(";") if s.strip()]
        if len(numbers) != int(Decimal(row["qty_received"])):
            return f"line {row['po_line_no']}: {count(int(Decimal(row['qty_received'])), 'serial number')} needed, one per unit"
    return None
