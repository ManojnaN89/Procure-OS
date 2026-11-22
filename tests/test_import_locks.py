"""Imports by company state (task 7.6, D-02): which import runs in which state, and the two locks of a live company
(opening stock and bank accounts are not added or changed by a masters import)."""

import csv
import shutil
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.masters.importer import import_masters
from p2p.masters.upsert import LIVE_LOCKS
from p2p.platform import company_state, cutover, cutover_import, demo, routing, tenants, users
from p2p.platform.company_state import StateError, operator
from p2p.receiving.stock import import_movements
from p2p.requests.demand import import_demand

OPS = operator("Test Operator")


@dataclass
class Apex:
    slug: str
    dataset: Path
    finance: int

    def attempt(self, kind: str) -> str:
        """'ok', or the refusal."""
        with Session(routing.engine_for(self.slug)) as db:
            try:
                if kind == "masters":
                    import_masters(db, self.dataset, dry_run=True)
                elif kind == "stock":
                    import_movements(db, self.dataset, dry_run=True)
                elif kind == "demand":
                    import_demand(db, self.dataset, dry_run=True, tenant=self.slug)
                else:
                    cutover_import.import_cutover(db, self.dataset / "cutover", user_id=self.finance, dry_run=True)
            except StateError as exc:
                return str(exc)
        return "ok"

    def table(self) -> dict[str, str]:
        return {kind: self.attempt(kind) for kind in ("masters", "stock", "demand", "cutover")}


@pytest.fixture(scope="module")
def apex(control_db: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Apex]:
    slug = "il" + uuid.uuid4().hex[:8]
    dataset = tmp_path_factory.mktemp("apex") / "dataset"
    demo.reset_company(demo.company("apex"), dataset, credentials=None, slug=slug, state="setup")
    finance = users.user_id_by_email(slug, "finance@apexpumps.example")
    assert finance
    yield Apex(slug, dataset, finance)
    routing.clear_engine_cache()
    tenants.drop_tenant(slug)


def test_imports_follow_the_company_state(apex: Apex) -> None:
    setup = apex.table()
    assert {k: v for k, v in setup.items() if k != "cutover"} == {"masters": "ok", "stock": "ok", "demand": "ok"}
    assert setup["cutover"] == "cutover import is only for a company in cutover; it is setup"

    cutover.archive_and_rebuild(apex.slug, reason="import locks test", operator="Test Operator")
    assert apex.table() == dict.fromkeys(("masters", "stock", "demand", "cutover"), "ok")

    company_state.move(apex.slug, "live", actor=OPS, reason="live for the test")
    live = apex.table()
    assert live["masters"] == "ok" and live["demand"] == "ok"
    assert live["stock"] == (
        "stock import is not allowed while the company is live: opening stock and its history are locked after "
        "go-live; correct stock with a count"
    )
    assert live["cutover"] == "cutover import is only for a company in cutover; it is live"

    company_state.move(apex.slug, "suspended", actor=OPS, reason="unpaid", kind="commercial")
    try:
        assert apex.table() == {
            "masters": "masters import is not allowed while the company is suspended",
            "stock": "stock import is not allowed while the company is suspended",
            "demand": "demand import is not allowed while the company is suspended",
            "cutover": "cutover import is only for a company in cutover; it is suspended",
        }
    finally:
        company_state.resume(apex.slug, actor=OPS, reason="paid")


def _rewrite(path: Path, change: object) -> None:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    header = list(rows[0])
    rows = change(rows)  # type: ignore[operator]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)


def test_a_live_company_keeps_its_opening_stock_and_bank_accounts(apex: Apex, tmp_path: Path) -> None:
    assert company_state.state_of(apex.slug) == "live"
    copy = tmp_path / "masters"
    shutil.copytree(apex.dataset / "master", copy / "master")
    with Session(routing.engine_for(apex.slug)) as db:
        held = {r[0] for r in db.execute(text("select item_code from stock_opening"))}
        opening = sorted(map(str, db.execute(text("select * from stock_opening")).all()))
        banks = sorted(map(str, db.execute(text("select * from supplier_bank_account")).all()))
        free = sorted(r[0] for r in db.execute(text("select code from item")) if r[0] not in held)[0]

    def more_stock(rows: list[dict[str, str]]) -> list[dict[str, str]]:
        return [{**rows[0], "qty": "999.000"}, *rows[1:], {**rows[0], "item_code": free, "qty": "7.000"}]

    def new_account(rows: list[dict[str, str]]) -> list[dict[str, str]]:
        sample = next(r for r in rows if r["supplier_code"] == "SUP-003")
        return [*rows, {**sample, "account_no": "000988000003", "ifsc": "HDFC0009803", "verified": "true"}]

    _rewrite(copy / "master" / "stock_opening.csv", more_stock)
    _rewrite(copy / "master" / "supplier_bank_account.csv", new_account)
    with Session(routing.engine_for(apex.slug)) as db:
        report = import_masters(db, copy)
        assert report.mode == "update" and not report.rejected
        assert sorted(map(str, db.execute(text("select * from stock_opening")).all())) == opening
        assert sorted(map(str, db.execute(text("select * from supplier_bank_account")).all())) == banks
    said = [p.reason for p in report.warnings]
    assert any(free in w and LIVE_LOCKS["stock_opening"] in w for w in said)
    assert any("opening stock is loaded once" in w for w in said)  # the changed quantity: kept, as in update mode
    assert any(w.startswith("SUP-003/") and LIVE_LOCKS["supplier_bank_account"] in w for w in said)
