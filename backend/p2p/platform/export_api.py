"""Export a list as a CSV file: the same rows the screen shows, as text, ready for a spreadsheet.

Each export is a read of one of the lists the screens use, so what a person may export is what they may see. An export is
written to the audit log (who, which list, how many rows). A cell that a spreadsheet would run as a formula is defanged.
"""

import csv
import io
import json
import re
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from p2p.formulas.calendar import business_today
from p2p.platform import audit, invoice_api, payment_api, routing, ui_api
from p2p.platform.sod import user

router = APIRouter(prefix="/api", tags=["export"])
Db = Annotated[Session, Depends(routing.tenant_session)]
Ctx = Annotated[routing.TenantContext, Depends(routing.tenant_context)]

Reader = Callable[[Session, routing.TenantContext], list[dict[str, Any]]]
LISTS: dict[str, Reader] = {
    "requests": lambda db, ctx: ui_api.list_requisitions(db, ctx),
    "quotations": lambda db, ctx: ui_api.list_rfqs(db, ctx),
    "orders": lambda db, ctx: ui_api.list_purchase_orders(db, ctx),
    "receipts": lambda db, ctx: ui_api.list_receipts(db, ctx),
    "invoices": lambda db, ctx: invoice_api.list_invoices(db, ctx, None),
    "payment-runs": lambda db, ctx: payment_api.list_runs(db, ctx),
    "vendors": lambda db, ctx: ui_api.list_vendors(db, ctx),
    "contracts": lambda db, ctx: ui_api.list_contracts(db, ctx),
    "stock": lambda db, ctx: ui_api.inventory(db, ctx)["lines"],
}
_NUMBER = re.compile(r"-?\d+(\.\d+)?")


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, dict | list):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    # a spreadsheet runs a cell that starts with = + - @ as a formula; a plain number such as -5.00 is left alone
    if text[:1] in ("=", "+", "-", "@", "\t", "\r") and not _NUMBER.fullmatch(text):
        return "'" + text
    return text


def to_csv(rows: list[dict[str, Any]]) -> str:
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_cell(row.get(c)) for c in columns])
    return out.getvalue()


@router.get("/export/{name}")
def export_list(name: str, db: Db, ctx: Ctx) -> Response:
    """The list as a CSV file (UTF-8 with a byte-order mark, so a spreadsheet shows rupee signs and accents right)."""
    reader = LISTS.get(name)
    if reader is None:
        raise HTTPException(404, f"There is no list called {name}. Choose one of: {', '.join(sorted(LISTS))}.")
    rows = reader(db, ctx)
    audit.record(db, user(ctx.user_id), "export.list", "list", name, after={"rows": len(rows)})
    db.commit()
    body = "﻿" + to_csv(rows)
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}-{business_today().isoformat()}.csv"'},
    )
