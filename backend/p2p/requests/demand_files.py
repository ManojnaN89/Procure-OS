"""The ERP input files, one class per kind: our template's columns, the checks a row must pass, the row's key in the ERP and
the record it becomes. The loading itself (mapping, duplicates, changes, writing) is the same for all kinds and lives in
`requests/demand.py`."""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.masters.schema import Table

QTY_PLACES = Decimal("0.0001")  # quantities are kept to four decimals
_PERIOD = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


@dataclass
class OrderHead:
    """A production order the lines of this import can hang on (already loaded, or accepted earlier in this import)."""

    id: int | None
    need_by: date
    location_code: str


@dataclass
class Reference:
    """What the rows are checked against: the company's masters and the production orders already known."""

    items: dict[str, tuple[bool, str]]  # code -> (active, base unit)
    factors: dict[tuple[str, str], Decimal]  # (item, unit) -> how many base units one of that unit is
    locations: set[str]
    default_location: str | None  # the company's only stock location, used when a file names none
    orders: dict[str, OrderHead] = field(default_factory=dict)
    departments: set[str] = field(default_factory=set)
    kits: dict[str, list[tuple[str, Decimal]]] = field(default_factory=dict)  # kit -> (item, base quantity per joiner)

    @classmethod
    def load(cls, db: Session) -> "Reference":
        items = {r[0]: (bool(r[1]), r[2]) for r in db.execute(text("select code, active, base_uom from item"))}
        factors = {
            (r[0], r[1]): Decimal(r[3])
            for r in db.execute(text("select item_code, from_uom, to_uom, factor from uom_conversion"))
            if r[0] in items and r[2] == items[r[0]][1] and r[3]
        }
        locations = {r[0]: bool(r[1]) for r in db.execute(text("select code, is_stock_location from location"))}
        stock = [c for c, is_stock in locations.items() if is_stock]
        orders = {
            r[0]: OrderHead(r[1], r[2], r[3])
            for r in db.execute(text("select order_no, id, need_by, location_code from production_order"))
        }
        departments = {r[0] for r in db.execute(text("select code from department"))}
        kits: dict[str, list[tuple[str, Decimal]]] = {}
        for kit, item, base in db.execute(text("select kit, item_code, base_qty from joiner_kit order by kit, id")):
            kits.setdefault(kit, []).append((item, Decimal(base)))
        return cls(items, factors, set(locations), stock[0] if len(stock) == 1 else None, orders, departments, kits)

    def location(self, row: dict[str, Any]) -> str | None:
        """The row's location: the one it names, or the company's only stock location."""
        return row.get("location_code") or self.default_location

    def check_location(self, row: dict[str, Any]) -> str | None:
        code = row.get("location_code")
        if code and code not in self.locations:
            return f"location_code '{code}' is not a location of the company"
        if not code and self.default_location is None:
            return "location_code is required: the company has more than one stock location"
        return None

    def check_material(self, row: dict[str, Any]) -> str | None:
        """An item line: a known, active item in its own unit or one with a conversion."""
        code = row["item_code"]
        if code not in self.items:
            return f"item_code '{code}' is not in the item master"
        active, base = self.items[code]
        if not active:
            return f"item {code} is inactive"
        unit = row.get("unit") or base
        if unit != base and (code, unit) not in self.factors:
            return f"unit '{unit}' is not {code}'s unit ({base}) and no conversion from it is set"
        return None

    def base_qty(self, item: str, unit: str, qty: Decimal) -> Decimal:
        base = self.items[item][1]
        return qty if unit == base else (qty * self.factors[(item, unit)]).quantize(QTY_PLACES)


def check_qty(qty: Decimal, column: str = "qty") -> str | None:
    if qty <= 0:
        return f"{column} {qty} must be above zero"
    if qty != qty.quantize(QTY_PLACES):
        return f"{column} {qty} has more than four decimals"
    return None


class DemandFile:
    """One kind of ERP input file. A subclass names its columns and says how a row is checked and stored."""

    kind: ClassVar[str]  # the file's name (kind.csv or kind.xlsx) and the template's
    title: ClassVar[str]
    table: ClassVar[str]  # where the rows are kept
    columns: ClassVar[tuple[tuple[str, str], ...]]  # our template: (column, sql type)
    required: ClassVar[tuple[str, ...]]
    optional: ClassVar[frozenset[str]] = frozenset()  # columns the file may leave out altogether
    key: ClassVar[tuple[str, ...]]  # columns of `table` that identify the row in the ERP
    qty_column: ClassVar[str] = "qty"  # the quantity every row must have above zero
    compared: ClassVar[tuple[str, ...]]  # columns of `table` whose change on a second import is reported
    example: ClassVar[tuple[str, ...]]  # an example row for the template

    @classmethod
    def spec(cls) -> Table:
        return Table(cls.kind, cls.kind, cls.columns, None, ())  # named after the file, as problems are reported

    def check(self, row: dict[str, Any], ref: Reference) -> str | None:
        """A reason to leave the row out, or None. Required columns and the quantity are checked before this."""
        return None

    def record(self, row: dict[str, Any], ref: Reference) -> dict[str, Any]:
        """The row as it is stored (without the import it came with)."""
        raise NotImplementedError

    def records(self, row: dict[str, Any], ref: Reference) -> list[dict[str, Any]]:
        """What the row becomes: one record, unless the kind spreads a row over several (a joiner plan over its kit)."""
        return [self.record(row, ref)]

    def accepted(self, rec: dict[str, Any], ref: Reference) -> None:
        """Remember an accepted record for the kinds read after this one."""


class ProductionOrders(DemandFile):
    kind = "production_order"
    title = "Production orders (one row per order)"
    table = "production_order"
    columns = (
        ("order_no", "text"),
        ("product_code", "text"),
        ("qty", "numeric"),
        ("need_by", "date"),
        ("reason", "text"),
        ("location_code", "text"),
    )
    required = ("order_no", "product_code", "qty", "need_by")
    optional = frozenset({"reason", "location_code"})
    key = ("order_no",)
    compared = ("product_code", "qty", "need_by", "location_code")
    example = ("MO-1", "FG-01", "50", "2026-12-05", "SO-1001", "")

    def check(self, row: dict[str, Any], ref: Reference) -> str | None:
        return ref.check_location(row)

    def record(self, row: dict[str, Any], ref: Reference) -> dict[str, Any]:
        return {
            "order_no": row["order_no"],
            "product_code": row["product_code"],
            "qty": row["qty"],
            "need_by": row["need_by"],
            "reason": row.get("reason"),
            "location_code": ref.location(row),
        }

    def accepted(self, rec: dict[str, Any], ref: Reference) -> None:
        ref.orders[rec["order_no"]] = OrderHead(rec.get("id"), rec["need_by"], rec["location_code"])


class ProductionOrderLines(DemandFile):
    """The materials of each production order (the ERP has exploded the bill of materials). One line per item and order."""

    kind = "production_order_line"
    title = "Production order components (one row per material of an order)"
    table = "material_requirement"
    columns = (
        ("order_no", "text"),
        ("item_code", "text"),
        ("qty", "numeric"),
        ("unit", "text"),
        ("need_by", "date"),
    )
    required = ("order_no", "item_code", "qty")
    optional = frozenset({"unit", "need_by"})
    key = ("source_kind", "source_doc_no", "line_key")
    compared = ("item_code", "qty", "unit", "need_by")
    example = ("MO-1", "RM-08", "90", "KGS", "2026-12-05")

    def check(self, row: dict[str, Any], ref: Reference) -> str | None:
        if row["order_no"] not in ref.orders:
            return f"production order {row['order_no']} has no header: it is not loaded and not in production_order"
        return ref.check_material(row)

    def record(self, row: dict[str, Any], ref: Reference) -> dict[str, Any]:
        head = ref.orders[row["order_no"]]
        unit = row.get("unit") or ref.items[row["item_code"]][1]
        return {
            "source_kind": "production_order",
            "source_doc_no": row["order_no"],
            "line_key": row["item_code"],
            "production_order_id": head.id,
            "item_code": row["item_code"],
            "qty": row["qty"],
            "unit": unit,
            "base_qty": ref.base_qty(row["item_code"], unit, row["qty"]),
            "need_by": row.get("need_by") or head.need_by,
            "location_code": head.location_code,
        }


class MrpResults(DemandFile):
    """What the ERP's material planning run says to buy: an item, a quantity and a date, per plan line."""

    kind = "mrp_result"
    title = "MRP results (one row per planned material line)"
    table = "material_requirement"
    columns = (
        ("plan_no", "text"),
        ("line_no", "text"),
        ("item_code", "text"),
        ("qty", "numeric"),
        ("unit", "text"),
        ("need_by", "date"),
        ("location_code", "text"),
    )
    required = ("plan_no", "line_no", "item_code", "qty", "need_by")
    optional = frozenset({"unit", "location_code"})
    key = ("source_kind", "source_doc_no", "line_key")
    compared = ("item_code", "qty", "unit", "need_by", "location_code")
    example = ("MRP-2026-12", "1", "RM-06", "200", "NOS", "2026-12-10", "")

    def check(self, row: dict[str, Any], ref: Reference) -> str | None:
        return ref.check_location(row) or ref.check_material(row)

    def record(self, row: dict[str, Any], ref: Reference) -> dict[str, Any]:
        unit = row.get("unit") or ref.items[row["item_code"]][1]
        return {
            "source_kind": "mrp",
            "source_doc_no": row["plan_no"],
            "line_key": row["line_no"],
            "production_order_id": None,
            "item_code": row["item_code"],
            "qty": row["qty"],
            "unit": unit,
            "base_qty": ref.base_qty(row["item_code"], unit, row["qty"]),
            "need_by": row["need_by"],
            "location_code": ref.location(row),
        }


class SalesOrders(DemandFile):
    """Customer orders for finished products: why a production order exists. Kept for reference, not bought from."""

    kind = "sales_order"
    title = "Sales orders (one row per product of an order)"
    table = "product_demand"
    columns = (
        ("order_no", "text"),
        ("customer", "text"),
        ("product_code", "text"),
        ("qty", "numeric"),
        ("need_date", "date"),
    )
    required = ("order_no", "product_code", "qty", "need_date")
    optional = frozenset({"customer"})
    key = ("kind", "doc_no", "product_code")
    compared = ("customer", "qty", "need_by")
    example = ("SO-1001", "Sahyadri Pump Distributors", "FG-01", "60", "2026-12-10")

    def record(self, row: dict[str, Any], ref: Reference) -> dict[str, Any]:
        return {
            "kind": "sales_order",
            "doc_no": row["order_no"],
            "product_code": row["product_code"],
            "customer": row.get("customer"),
            "qty": row["qty"],
            "need_by": row["need_date"],
            "period": None,
            "status": None,
        }


class DemandPlans(DemandFile):
    """The demand plan: how many of a product a month is expected to need."""

    kind = "demand_plan"
    title = "Demand plan (one row per product and month)"
    table = "product_demand"
    columns = (
        ("plan_no", "text"),
        ("product_code", "text"),
        ("qty", "numeric"),
        ("period", "text"),
        ("status", "text"),
    )
    required = ("plan_no", "product_code", "qty", "period")
    optional = frozenset({"status"})
    key = ("kind", "doc_no", "product_code")
    compared = ("qty", "period", "status")
    example = ("DP-2026-12", "FG-02", "100", "2026-12", "approved")

    def check(self, row: dict[str, Any], ref: Reference) -> str | None:
        if not _PERIOD.fullmatch(row["period"]):
            return f"period '{row['period']}' is not a month like 2026-12"
        return None

    def record(self, row: dict[str, Any], ref: Reference) -> dict[str, Any]:
        return {
            "kind": "demand_plan",
            "doc_no": row["plan_no"],
            "product_code": row["product_code"],
            "customer": None,
            "qty": row["qty"],
            "need_by": None,
            "period": row["period"],
            "status": row.get("status"),
        }


class JoinerKits(DemandFile):
    """An office's "materials per joiner": what each kind of new joiner gets (a laptop, a monitor, a licence)."""

    kind = "joiner_kit"
    title = "Joiner kits (one row per item of a kit)"
    table = "joiner_kit"
    columns = (("kit", "text"), ("item_code", "text"), ("qty", "numeric"), ("unit", "text"))
    required = ("kit", "item_code", "qty")
    optional = frozenset({"unit"})
    key = ("kit", "item_code")
    compared = ("qty", "unit")
    example = ("machinist", "SP-02", "10", "")

    def check(self, row: dict[str, Any], ref: Reference) -> str | None:
        return ref.check_material(row)

    def record(self, row: dict[str, Any], ref: Reference) -> dict[str, Any]:
        unit = row.get("unit") or ref.items[row["item_code"]][1]
        return {
            "kit": row["kit"],
            "item_code": row["item_code"],
            "qty": row["qty"],
            "unit": unit,
            "base_qty": ref.base_qty(row["item_code"], unit, row["qty"]),
        }

    def accepted(self, rec: dict[str, Any], ref: Reference) -> None:
        ref.kits.setdefault(rec["kit"], []).append((rec["item_code"], rec["base_qty"]))


class JoinerPlans(DemandFile):
    """New joiners: how many join a department with which kit from when. Each row becomes the items (and licences) of its
    kit times the number of joiners, needed by the start date and charged to the joiner's department; a plan's rows for
    one department become one material request."""

    kind = "joiner_plan"
    title = "Joiner plan (one row per department, kit and start date)"
    table = "material_requirement"
    columns = (
        ("plan_no", "text"),
        ("department", "text"),
        ("kit", "text"),
        ("count", "numeric"),
        ("start_date", "date"),
        ("location_code", "text"),
    )
    required = ("plan_no", "department", "kit", "count", "start_date")
    optional = frozenset({"location_code"})
    key = ("source_kind", "source_doc_no", "line_key")
    compared = ("item_code", "qty", "need_by", "location_code", "department_code")
    qty_column = "count"
    example = ("JP-2026-12", "PRD", "machinist", "2", "2026-12-01", "")

    def check(self, row: dict[str, Any], ref: Reference) -> str | None:
        if row["count"] != row["count"].to_integral_value():
            return f"count {row['count']} must be a whole number of people"
        if row["department"] not in ref.departments:
            return f"department '{row['department']}' is not a department of the company"
        if row["kit"] not in ref.kits:
            return f"kit '{row['kit']}' is not in joiner_kit: load the kits first"
        return ref.check_location(row)

    def records(self, row: dict[str, Any], ref: Reference) -> list[dict[str, Any]]:
        return [
            {
                "source_kind": "joiner",
                "source_doc_no": f"{row['plan_no']}/{row['department']}",
                "line_key": f"{row['kit']}/{row['start_date'].isoformat()}/{item}",
                "production_order_id": None,
                "item_code": item,
                "qty": per_joiner * row["count"],
                "unit": ref.items[item][1],
                "base_qty": per_joiner * row["count"],
                "need_by": row["start_date"],
                "location_code": ref.location(row),
                "department_code": row["department"],
            }
            for item, per_joiner in ref.kits[row["kit"]]
        ]


# in the order they are read: production order headers before their lines, kits before the joiners who get them
KINDS: dict[str, DemandFile] = {
    k.kind: k
    for k in (
        ProductionOrders(), ProductionOrderLines(), MrpResults(), SalesOrders(), DemandPlans(), JoinerKits(),
        JoinerPlans(),
    )
}  # fmt: skip
