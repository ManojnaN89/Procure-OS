"""Which approval rule applies to a request, and who the people in its chain are.

The rules are data (the approval_rule table, loaded from the company's master data): a value band, a document type, and
optionally a category, department and spend type, each with a version and the dates it is in force. Looking a rule up is
a plain query, so a decision can always be repeated: the request keeps the rule code and version it was decided under.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


class ApprovalRuleError(Exception):
    """No rule fits, or the rule cannot be turned into people. The request stays where it is until someone fixes the data."""


@dataclass(frozen=True)
class ApprovalRule:
    code: str
    version: int
    roles: tuple[str, ...]  # empty: approved automatically
    min_value: Decimal
    max_value: Decimal

    @property
    def is_automatic(self) -> bool:
        return not self.roles


@dataclass(frozen=True)
class Step:
    role: str
    approver: str  # code of a person in the organisation
    delegate: str | None = None  # who stands in while the approver is away


def find_rule(
    db: Session,
    document: str,
    value: Decimal,
    on_date: date,
    *,
    spend_type: str = "ANY",
    category: str | None = None,
    department: str | None = None,
) -> ApprovalRule:
    """The most specific rule in force on the date whose band contains the value (both ends included).

    A rule naming a category, department or spend type beats one that says ANY, and a category counts for more than a
    department, which counts for more than a spend type. Two different rules that fit equally well are a mistake in
    the data, and are refused rather than guessed between.
    """
    rows = (
        db.execute(
            text(
                "select rule_code, version, approver_chain, min_value, max_value, "
                "(category_code <> 'ANY')::int * 4 + (department_code <> 'ANY')::int * 2 + (spend_type <> 'ANY')::int as specificity "
                "from approval_rule where document = :doc and :v between min_value and max_value "
                "and effective_from <= :d and (effective_to is null or effective_to >= :d) "
                "and category_code in ('ANY', coalesce(:cat, 'ANY')) "
                "and department_code in ('ANY', coalesce(:dep, 'ANY')) "
                "and spend_type in ('ANY', :spend) "
                "order by specificity desc, version desc"
            ),
            {"doc": document, "v": value, "d": on_date, "cat": category, "dep": department, "spend": spend_type},
        )
        .mappings()
        .all()
    )
    if not rows:
        raise ApprovalRuleError(f"no approval rule for a {document} of {value} on {on_date}")
    best = [r for r in rows if r["specificity"] == rows[0]["specificity"]]
    if len({r["rule_code"] for r in best}) > 1:
        codes = ", ".join(sorted({r["rule_code"] for r in best}))
        raise ApprovalRuleError(f"rules {codes} all fit a {document} of {value}; the rule table is ambiguous")
    r = best[0]  # the highest version of the one rule
    chain = [part.strip() for part in str(r["approver_chain"]).split(">") if part.strip()]
    roles = () if chain == ["auto"] else tuple(chain)
    return ApprovalRule(r["rule_code"], int(r["version"]), roles, r["min_value"], r["max_value"])


def _one(db: Session, sql: str, **params: Any) -> str | None:
    row = db.execute(text(sql), params).first()
    return str(row[0]) if row and row[0] is not None else None


def _person_for_role(db: Session, role: str, requester: str | None, department: str | None) -> str:
    row = db.execute(text("select source, value from approver_role where role = :r"), {"r": role}).first()
    if row is None:
        raise ApprovalRuleError(f"the role '{role}' used by the approval rule is not defined in approver_role")
    source, value = row
    person: str | None = None
    if source == "requester_manager":
        if requester:
            person = _one(db, "select manager_code from org_user where code = :c", c=requester)
        elif department:  # nobody raised it (a planning request): the department's head is the nearest manager
            person = _one(db, "select head_user from department where code = :c", c=department)
    elif source == "department_head":
        person = _one(db, "select head_user from department where code = :c", c=department) if department else None
    elif source == "department_head_of":
        person = _one(db, "select head_user from department where code = :c", c=value)
    elif source == "designation":
        person = _one(db, "select code from org_user where designation = :d and active order by code limit 1", d=value)
    if person is None:
        raise ApprovalRuleError(f"cannot find who '{role}' is for this request ({source}); check the organisation data")
    return person


def resolve_chain(
    db: Session, roles: tuple[str, ...], *, requester: str | None, department: str | None, on_date: date
) -> list[Step]:
    """Turn the roles of a chain into people, in order.

    The same person twice in a row is one step. The requester never approves their own request. A person on leave on the
    date (a delegation) is stood in for by their delegate.
    """
    steps: list[Step] = []
    for role in roles:
        person = _person_for_role(db, role, requester, department)
        if person == requester or (steps and steps[-1].approver == person):
            continue
        delegate = _one(
            db,
            "select to_user from delegation where from_user = :p and start_date <= :d "
            "and (end_date is null or end_date >= :d) order by start_date desc limit 1",
            p=person,
            d=on_date,
        )
        steps.append(Step(role, person, delegate if delegate != requester else None))
    if roles and not steps:
        raise ApprovalRuleError("the only approver in the chain is the requester; someone else has to approve")
    return steps
