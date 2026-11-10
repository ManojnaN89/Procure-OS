"""A request line gets its GL account from the item, or from the item's category when the item has none, however the request is made;
a line without a cost centre is held against the cost centres of its delivery place (see test_budget)."""

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from test_intake_agent import _asker, world  # noqa: F401 - fixtures
from test_orders import shop  # noqa: F401 - fixtures
from test_replenishment import _company, _lines
from test_sourcing import db  # noqa: F401 - fixtures

from p2p.agents import intake
from p2p.requests import replenishment

D = Decimal
FRIDAY = date(2026, 11, 6)


def _gl(db, no: str) -> dict[str, str | None]:  # type: ignore[no-untyped-def]
    return {
        r[0]: r[1]
        for r in db.execute(text("select item_code, gl_code from request_line where request_no = :n"), {"n": no})
    }


def test_a_request_from_the_form_takes_the_items_gl_account_else_its_categorys(world) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    world.execute(
        text("insert into gl_account (code, name) values (5100, 'Spares'), (5200, 'Special spares'), (5300, 'Tools')")
    )
    world.execute(text("update item_category set default_gl = 5100 where code = 'C1'"))
    world.execute(text("update item_category set default_gl = 5300 where code = 'C2'"))
    world.execute(text("update item set default_gl = 5200 where code = 'B2'"))  # this item has its own account
    world.commit()

    made = intake.create_from_form(
        world, _asker(world), [("B1", D(1)), ("B2", D(1)), ("F1", D(1))], date(2026, 11, 20), None, "x", today=FRIDAY
    )

    gl: dict[str, str | None] = {}
    for no in made.request_nos:
        gl.update(_gl(world, no))
    assert gl == {"B1": "5100", "B2": "5200", "F1": "5300"}


def test_a_request_from_a_restock_run_takes_the_same_defaults(control_db: str) -> None:
    _, db = _company()
    db.execute(
        text(
            "insert into gl_account (code, name) values (5100, 'Spares'), (5200, 'Special spares'), (5400, 'Packaging')"
        )
    )
    db.execute(text("update item_category set default_gl = 5100 where code = 'C1'"))
    db.execute(text("update item_category set default_gl = 5400 where code = 'C2'"))
    db.execute(text("update item set default_gl = 5200 where code = 'D'"))
    db.commit()

    result = replenishment.run(db, as_of=FRIDAY)

    gl: dict[str, str | None] = {}
    for made in result.requests:
        gl.update(_gl(db, made.no))
    assert gl == {"A": "5100", "D": "5200", "B": "5400"}
    assert _lines(db, result.requests[0].no)["A"]["gl_code"] == "5100"
    db.close()
