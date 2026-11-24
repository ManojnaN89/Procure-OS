"""The plain rules of a bill without a purchase order (block 10, D-14).

A bill may be entered only for a configured category, within its limit, from an active supplier approved for the category,
charged to a live cost centre and an expense account, and with its evidence. The figures come from the rule row and the
invoice; nothing here reads or writes a database, and a model computes none of it.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from p2p.formulas.matching import normalize_invoice_no
from p2p.formulas.money import format_inr

GST_SLABS = frozenset(Decimal(x) for x in ("0", "0.25", "3", "5", "12", "18", "28"))
GL_TYPES = frozenset({"expense"})


@dataclass(frozen=True)
class Rule:
    """The `non_po_rule` version in force for a category."""

    id: int
    category_code: str
    category_name: str
    version: int
    max_value: Decimal
    supporting_doc: str | None = None
    gl_code: str | None = None
    stocked: bool = False


@dataclass(frozen=True)
class Problem:
    """A refusal: the code (for the audit trail), the words a person reads, and the HTTP status it is refused with."""

    code: str
    message: str
    status: int = 409


def money(value: Decimal | str) -> str:
    return f"Rs {format_inr(value, symbol=False)}"


def rule_problems(
    *,
    rule: Rule | None,
    category_name: str | None,
    total: Decimal,
    supplier_name: str,
    supplier_status: str,
    supplier_approved: bool,
    cost_centre_ok: bool,
    gl_code: str | None,
    gl_ok: bool,
    has_copy: bool,
    has_supporting: bool,
) -> list[Problem]:
    """Everything that stops a bill being entered, in the order the spec gives (10.1). Empty when it may be entered."""
    if rule is None or rule.stocked:
        name = rule.category_name if rule else (category_name or "this category")
        return [Problem("category", f"{name} cannot be billed without a purchase order")]
    out: list[Problem] = []
    if total > rule.max_value:
        out.append(
            Problem(
                "limit",
                f"a {rule.category_name.lower()} bill without an order may be at most {money(rule.max_value)}; "
                f"this one is {money(total)}: raise a purchase order",
            )
        )
    if supplier_status != "active":
        out.append(
            Problem(
                "supplier_status",
                f"{supplier_name} is {supplier_status}: only an active supplier's bill can be entered without an order",
            )
        )
    if not supplier_approved:
        out.append(Problem("supplier_category", f"{supplier_name} is not approved for {rule.category_name.lower()}"))
    if not cost_centre_ok:
        out.append(Problem("cost_centre", "the cost centre is not an active cost centre of this company"))
    if not gl_ok:
        out.append(Problem("gl", f"GL {gl_code or ''} cannot take a bill without an order".replace("GL  ", "GL ")))
    if rule.supporting_doc and not has_supporting:
        out.append(
            Problem(
                "supporting_doc",
                f"a {rule.category_name.lower()} bill needs the {rule.supporting_doc} attached",
                422,
            )
        )
    if not has_copy:
        out.append(Problem("copy", "a bill without an order needs the bill copy attached", 422))
    return out


@dataclass(frozen=True)
class Possible:
    """A live bill that may be the same one: the platform asks before entering a second."""

    no: str
    number: str
    invoice_date: date
    total: Decimal


def possible_duplicate(candidates: Sequence[Possible], on: date, window_days: int) -> Possible | None:
    """The first live bill of the same supplier, category and total whose date is within the window, both ends
    included, in either direction. None when there is none."""
    window = abs(int(window_days))
    for c in candidates:
        if normalize_invoice_no(c.number) and abs((c.invoice_date - on).days) <= window:
            return c
    return None
