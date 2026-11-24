"""One company never reads another's close records (block 13, TN1): GSTR-2B, price measures and a closed month are
rows of that company's own database."""

import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.invoices import gstr2b
from p2p.platform import routing, tenants


@pytest.fixture
def two(control_db: str) -> Iterator[tuple[str, str, Session, Session]]:
    made = []
    for _ in range(2):
        slug = "t" + uuid.uuid4().hex[:8]
        assert tenants.create_tenant(slug, load_reference=False).ok
        made.append(slug)
    a, b = made
    try:
        with Session(routing.engine_for(a)) as one, Session(routing.engine_for(b)) as two:
            yield a, b, one, two
    finally:
        routing.clear_engine_cache()


def test_given_two_companies_when_each_keeps_a_close_record_then_neither_sees_the_other(
    two: tuple[str, str, Session, Session],
) -> None:
    _a, _b, one, _two = two
    one.execute(
        text(
            "insert into gstr2b_import (gstin, period, file_hash, imported_by) values ('29AAAAA0000A1Z5', '2026-10', 'h1', '1')"
        )
    )
    one.execute(
        text("insert into price_measure (measure, part, source_type, source_ref, on_date, period, reference, amount) "
             "values ('ppv', 'invoice', 'invoice_line', 'PI-1', '2026-10-05', '2026-10', 'standard v1', 100)")
    )  # fmt: skip
    one.execute(text("insert into period_close (period, closed_by, reason) values ('2026-10', 'U05', 'month end')"))
    one.commit()

    assert len(gstr2b.current(one)) == 1
    assert gstr2b.current(_two) == []
    assert one.execute(text("select count(*) from price_measure")).scalar_one() == 1
    assert _two.execute(text("select count(*) from price_measure")).scalar_one() == 0
    assert _two.execute(text("select count(*) from period_close")).scalar_one() == 0
    assert _two.execute(text("select count(*) from gstr2b_import")).scalar_one() == 0
