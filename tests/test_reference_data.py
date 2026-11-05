import uuid
from datetime import date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import IST, sla_due
from p2p.platform import reference, routing, tenants
from p2p.platform.reference import parse_hsn_prefixes


@pytest.mark.parametrize(
    ("raw", "prefixes", "review"),
    [
        ("0101 21 00, 0101 29", ["01012100", "010129"], False),
        ("5004 to 5006", ["5004", "5005", "5006"], False),
        ("1701 or1702", ["1701", "1702"], False),
        ("0507 (Except 050790)", ["0507"], True),
        ("0910 [other than 0910 11 10, 0910 30 10]", ["0910"], True),
        ("Any chapter", [], True),
        ("2515 [Except 2515 12 20, 2515 12 90] or 6802", ["2515", "6802"], True),
    ],
)
def test_hsn_text_from_the_notifications_becomes_code_prefixes(raw: str, prefixes: list[str], review: bool) -> None:
    assert parse_hsn_prefixes(raw) == (prefixes, review)


@pytest.fixture(scope="module")
def tenant_db(control_db: str) -> Session:
    slug = "t" + uuid.uuid4().hex[:8]
    result = tenants.create_tenant(slug)  # loads the reference data as part of creating the tenant
    assert result.ok
    db = Session(routing.engine_for(slug))
    yield db
    db.close()


def _count(db: Session, table: str) -> int:
    return int(db.execute(text(f"select count(*) from {table}")).scalar_one())


def test_a_new_tenant_comes_with_the_reference_data_loaded(tenant_db: Session) -> None:
    assert _count(tenant_db, "ref_gst_state") >= 37
    assert _count(tenant_db, "ref_hsn_sac") > 20000
    assert _count(tenant_db, "ref_gst_rate") > 1000
    assert _count(tenant_db, "ref_tds_section") >= 20
    assert _count(tenant_db, "ref_holiday") > 200
    assert _count(tenant_db, "ref_load") == 1


def test_loading_again_replaces_the_data_instead_of_doubling_it(tenant_db: Session) -> None:
    before = _count(tenant_db, "ref_gst_rate")
    reference.load_reference(tenant_db)
    assert _count(tenant_db, "ref_gst_rate") == before


def test_the_gst_rate_depends_on_the_date(tenant_db: Session) -> None:
    # HSN 2807 (sulphuric acid): 18% until 21 Sep 2025, 5% from 22 Sep 2025 (rate cut in the catalogue)
    assert reference.gst_rate_for(tenant_db, "28070010", date(2025, 9, 21)).igst == Decimal("18")  # type: ignore[union-attr]
    after = reference.gst_rate_for(tenant_db, "28070010", date(2025, 9, 22))
    assert after is not None and after.igst == Decimal("5") and after.cgst == Decimal("2.5")


def test_the_longest_matching_code_wins(tenant_db: Session) -> None:
    short = reference.gst_rate_for(tenant_db, "271019", date(2026, 1, 1))
    assert short is not None and len(short.code_prefix) >= 4
    assert reference.gst_rate_for(tenant_db, "0000", date(2026, 1, 1)) is None


def test_a_service_rate_is_found_by_sac(tenant_db: Session) -> None:
    gta = reference.gst_rate_for(tenant_db, "996511", date(2026, 1, 1), kind="services")
    assert gta is not None and gta.reverse_charge is not None


def test_central_and_state_holidays_feed_the_business_calendar(tenant_db: Session) -> None:
    cal = reference.calendar_for(tenant_db, "24")  # Gujarat
    assert not cal.is_working_day(date(2026, 1, 26))  # Republic Day
    assert cal.is_working_day(date(2026, 1, 27))
    # an SLA that would end on Republic Day moves to the next working day
    due = sla_due(datetime(2026, 1, 23, 17, tzinfo=IST), 8, cal)  # Friday evening; Monday 26th is a holiday
    assert due.date() == date(2026, 1, 27)


def test_tds_sections_are_loaded_with_their_rates(tenant_db: Session) -> None:
    row = tenant_db.execute(
        text("select rate_pct, rate_no_pan_pct, threshold_annual from ref_tds_section where section = '194Q' limit 1")
    ).one()
    assert (row[0], row[1], row[2]) == (Decimal("0.100"), Decimal("5.000"), Decimal("5000000.00"))
