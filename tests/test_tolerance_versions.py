"""Tolerance versions (task 3.2, D-17): the tolerance set is versioned as a whole, an invoice is judged under the version
in force on the day it was received, a match and a receipt keep the version they used, and a re-match gives the same
result after a later version exists. A change is a new version from a date, approved by a second administrator."""

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_invoices import _goods, _invoice, ledger  # noqa: F401 - fixtures
from test_orders import _headers, shop  # noqa: F401 - fixtures
from test_receiving import store  # noqa: F401 - fixtures
from test_sourcing import PASSWORD, Outbox, db, outbox  # noqa: F401 - fixtures

from p2p.formulas.calendar import business_today
from p2p.invoices import service as invoices
from p2p.invoices import tolerances
from p2p.main import app
from p2p.platform import users
from p2p.platform.sod import user

D = Decimal
client = TestClient(app)
EARLY = date(2026, 11, 6)  # 'today' for the versions made directly through the service


def _version(db_: Session, key: str, value: str, start: date, category: str = "ANY") -> int:
    v = tolerances.save_version(
        db_, user(1), key=key, category=category, value=value, effective_from=start, today=EARLY, note="test"
    )
    db_.commit()
    return v


def _versions(db_: Session) -> dict[int, tuple[date, date | None]]:
    db_.expire_all()
    return {
        r[0]: (r[1], r[2])
        for r in db_.execute(text("select version, effective_from, effective_to from tolerance_version"))
    }


def test_version_one_is_the_company_set_and_a_change_copies_it_with_one_value_changed(ledger: Session) -> None:  # noqa: F811
    v = _version(ledger, "price_abs", "300", date(2026, 11, 10))

    assert v == 2
    one, two = tolerances.of_version(ledger, 1), tolerances.of_version(ledger, 2)
    assert (one.value("price_abs"), two.value("price_abs")) == ("500", "300")
    assert (one.value("price_pct"), two.value("price_pct")) == ("2", "2")  # the rest copied
    assert _versions(ledger) == {1: (date(2000, 1, 1), date(2026, 11, 9)), 2: (date(2026, 11, 10), None)}
    assert tolerances.in_force(ledger, date(2026, 11, 9)).version == 1  # the day before
    assert tolerances.in_force(ledger, date(2026, 11, 10)).version == 2  # its start date
    audit = ledger.execute(text("select rule_version from audit_event where action = 'tolerance.change'")).scalar_one()
    assert audit == "tolerance v2"


def test_a_category_value_overrides_the_company_one_within_its_version(ledger: Session) -> None:  # noqa: F811
    _version(ledger, "price_abs", "1000", date(2026, 11, 10), category="C1")

    v2 = tolerances.in_force(ledger, date(2026, 11, 10))
    assert (v2.decimal("price_abs", "0", "C1"), v2.decimal("price_abs", "0", "C2")) == (D(1000), D(500))
    assert tolerances.in_force(ledger, date(2026, 11, 9)).decimal("price_abs", "0", "C1") == D(500)


def test_a_new_version_starts_today_or_later_and_never_before_the_latest(ledger: Session) -> None:  # noqa: F811
    with pytest.raises(tolerances.ToleranceError, match="today or later"):
        _version(ledger, "price_abs", "300", date(2026, 11, 5))
    _version(ledger, "price_abs", "300", date(2026, 11, 20))
    with pytest.raises(tolerances.ToleranceError, match="cannot start before it"):
        _version(ledger, "price_abs", "250", date(2026, 11, 19))


def test_a_version_replaced_on_its_start_day_is_never_used(ledger: Session) -> None:  # noqa: F811
    _version(ledger, "price_abs", "300", date(2026, 11, 20))
    _version(ledger, "price_abs", "250", date(2026, 11, 20))

    start, end = _versions(ledger)[2]
    assert end is not None and end < start  # never used
    assert tolerances.in_force(ledger, date(2026, 11, 20)).version == 3


def test_an_invoice_is_judged_under_the_version_of_the_day_it_was_received_and_keeps_it(
    ledger: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    _version(ledger, "price_abs", "300", date(2026, 11, 10))  # the rupee tolerance cut from the 10th
    before, logins = _goods(ledger, qty="10", price="1000")
    after, _ = _goods(ledger, qty="11", price="1000", logins=logins)

    # Rs 40 a unit is 4% (above the 2%), and Rs 400 on the line: inside 500, outside 300
    old = _invoice(ledger, before, "10", "1040", number="A", on=date(2026, 11, 9))
    new = _invoice(ledger, after, "10", "1040", number="B", on=date(2026, 11, 10))

    assert (old.status, old.result) == ("approved", "within_tolerance")
    assert (new.status, new.result, new.reasons) == ("blocked", "blocked", ["price_variance"])
    kept = dict(ledger.execute(text("select no, tolerance_version from invoice")).all())
    assert kept == {old.no: 1, new.no: 2}
    per_line = ledger.execute(text("select invoice_no, tolerance_version from match_result order by id")).all()
    assert per_line == [(old.no, 1), (new.no, 2)]
    audit = ledger.execute(
        text("select entity_id, rule_version from audit_event where action = 'invoice.match' order by id")
    ).all()
    assert audit == [(old.no, "tolerance v1"), (new.no, "tolerance v2")]


def test_matching_again_after_a_later_version_gives_the_same_result_and_version(
    ledger: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    _version(ledger, "price_abs", "300", date(2026, 11, 10))
    po, _ = _goods(ledger, qty="10", price="1000")
    blocked = _invoice(ledger, po, "10", "1040", number="B", on=date(2026, 11, 10))
    _version(ledger, "price_abs", "1000", date(2026, 11, 12))  # a looser version later

    again = invoices.match_invoice(ledger, blocked.no, user(1))

    assert (again.status, again.reasons) == ("blocked", ["price_variance"])
    assert ledger.execute(text("select tolerance_version from invoice")).scalar_one() == 2


def test_a_goods_receipt_keeps_the_version_of_its_day(ledger: Session, outbox: Outbox) -> None:  # noqa: F811
    _goods(ledger, qty="10", price="100")
    assert ledger.execute(text("select tolerance_version from goods_receipt")).scalar_one() == 1


def test_a_tolerance_change_waits_for_a_second_administrator_and_shows_the_versions(ledger: Session) -> None:  # noqa: F811
    slug, sfx = ledger.info["slug"], ledger.info["sfx"]
    maker = users.create_user(slug, f"ta{sfx}@x.example", PASSWORD, "Admin One", "Admin")
    checker = users.create_user(slug, f"tb{sfx}@x.example", PASSWORD, "Admin Two", "Admin")
    hm, hc = _headers(slug, maker, "Admin"), _headers(slug, checker, "Admin")
    start = business_today().isoformat()

    past = client.put(
        "/api/setup/tolerances/price_abs",
        json={"value": "300", "reason": "audit", "effective_from": "2026-01-01"},
        headers=hm,
    )
    asked = client.put(
        "/api/setup/tolerances/supplier_date_days",
        json={"value": "5", "reason": "suppliers asked", "effective_from": start},
        headers=hm,
    )

    assert past.status_code == 422 and "today or later" in past.text
    assert asked.status_code == 202, asked.text
    assert tolerances.in_force(ledger, business_today()).version == 1  # nothing changed yet
    own = client.post(f"/api/admin/proposals/{asked.json()['proposal_id']}/approve", json={}, headers=hm)
    assert own.status_code == 403
    done = client.post(f"/api/admin/proposals/{asked.json()['proposal_id']}/approve", json={}, headers=hc)
    assert done.status_code == 200 and done.json()["execution"] == "ok", done.text
    listed = client.get("/api/setup/tolerances", headers=hc).json()
    assert listed["in_force"] == 2
    assert [v["version"] for v in listed["versions"]] == [2, 1]
    assert {"key": "supplier_date_days", "category": "ANY", "value": "5"} in listed["versions"][0]["values"]
    buyer = users.create_user(slug, f"tc{sfx}@x.example", PASSWORD, "A Buyer", "Buyer")
    assert client.get("/api/setup/tolerances", headers=_headers(slug, buyer, "Buyer")).status_code == 403
