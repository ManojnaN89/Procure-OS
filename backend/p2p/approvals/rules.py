"""Which approval rule applies to a request, and who the people in its chain are.

The rules are data (the approval_rule table, loaded from the company's master data): a value band, a document type, and
optionally a category, department, location and spend type, each with a version and the dates it is in force. Looking a
rule up is a plain query, so a decision can always be repeated: the request keeps the rule code and version it was decided
under, and each approval step keeps the rule row that put it there.

How rows combine (D36):
1. Only rows in force on the date, whose band holds the value (both ends included), and whose category, department,
   location and spend type are ANY or the request's own, are looked at.
2. A row whose chain does not start with '+' replaces: the most specific of them gives the chain. Specificity follows the
   setting `approval_precedence` (default category > department > location > spend type): a row naming a dimension earlier
   in that order beats any row that names only later ones. Two different rules that fit equally well are refused.
3. A row whose chain starts with '+' adds: its roles join the end of the chain (every such row that fits, most specific
   first). Added to an automatic band, the chain becomes just the added roles.
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.money import format_inr
from p2p.platform import settings

# dimension -> column of approval_rule
DIMENSIONS = {
    "category": "category_code",
    "department": "department_code",
    "location": "location_code",
    "spend_type": "spend_type",
}
DEFAULT_PRECEDENCE = "category>department>location>spend_type"


class ApprovalRuleError(Exception):
    """No rule fits, or the rule cannot be turned into people. The request stays where it is until someone fixes the data."""


@dataclass(frozen=True)
class Source:
    """The rule row a role of the chain comes from, and in plain words why it applies."""

    rule_id: int
    code: str
    version: int
    why: str


@dataclass(frozen=True)
class ApprovalRule:
    code: str
    version: int
    roles: tuple[str, ...]  # empty: approved automatically
    min_value: Decimal
    max_value: Decimal
    sources: tuple[Source, ...] = ()  # one per role, in the same order

    @property
    def is_automatic(self) -> bool:
        return not self.roles


@dataclass(frozen=True)
class Step:
    role: str
    approver: str  # code of a person in the organisation
    delegate: str | None = None  # who stands in while the approver is away
    source: Source | None = None


def precedence(db: Session) -> tuple[str, ...]:
    """The order in which a named dimension makes a row more specific (the setting approval_precedence)."""
    order = tuple(p.strip() for p in settings.value(db, "approval_precedence", DEFAULT_PRECEDENCE).split(">"))
    if sorted(order) != sorted(DIMENSIONS):
        raise ApprovalRuleError(
            "the approval precedence setting must list category, department, location and spend type once each, "
            "with '>' between them"
        )
    return order


def specificity(row: dict[str, Any], order: tuple[str, ...]) -> int:
    """Each named dimension weighs more than all the later ones together, so the order decides."""
    return sum(2 ** (len(order) - 1 - i) for i, dim in enumerate(order) if row[DIMENSIONS[dim]] not in (None, "ANY"))


def chain_roles(chain: str) -> tuple[bool, tuple[str, ...]]:
    """(adds, roles) of an approver_chain: '+role>role' adds, 'auto' is no roles."""
    raw = chain.strip()
    adds = raw.startswith("+")
    roles = tuple(p.strip() for p in raw.lstrip("+").split(">") if p.strip())
    return adds, () if roles == ("auto",) else roles


def _why(r: dict[str, Any], value: Decimal, adds: bool) -> str:
    scope = ", ".join(
        f"{dim.replace('_', ' ')} {r[col]}" for dim, col in DIMENSIONS.items() if r[col] not in (None, "ANY")
    )
    band = f"{format_inr(r['min_value'])} to {format_inr(r['max_value'])}"
    head = f"rule {r['rule_code']} v{r['version']}" + (" adds an approver" if adds else "")
    return f"{head}: {format_inr(value)} is in the band {band}" + (f" for {scope}" if scope else "")


def find_rule(
    db: Session,
    document: str,
    value: Decimal,
    on_date: date,
    *,
    spend_type: str = "ANY",
    category: str | None = None,
    department: str | None = None,
    location: str | None = None,
) -> ApprovalRule:
    """The chain for a document of this value on the date, from the rows that fit (see the module's note for the order)."""
    rows = [
        dict(r)
        for r in db.execute(
            text(
                "select id, rule_code, version, approver_chain, min_value, max_value, category_code, department_code, "
                "coalesce(location_code, 'ANY') as location_code, spend_type from approval_rule "
                "where document = :doc and :v between min_value and max_value "
                "and effective_from <= :d and (effective_to is null or effective_to >= :d) "
                "and category_code in ('ANY', coalesce(:cat, 'ANY')) "
                "and department_code in ('ANY', coalesce(:dep, 'ANY')) "
                "and coalesce(location_code, 'ANY') in ('ANY', coalesce(:loc, 'ANY')) "
                "and spend_type in ('ANY', :spend) "
                "order by version desc, id"
            ),
            {
                "doc": document,
                "v": value,
                "d": on_date,
                "cat": category,
                "dep": department,
                "loc": location,
                "spend": spend_type,
            },
        ).mappings()
    ]
    order = precedence(db)
    replacing = [r for r in rows if not chain_roles(str(r["approver_chain"]))[0]]
    if not replacing:
        raise ApprovalRuleError(f"no approval rule for a {document} of {format_inr(value)} on {on_date:%d %b %Y}")
    top = max(specificity(r, order) for r in replacing)
    best = [r for r in replacing if specificity(r, order) == top]
    if len({r["rule_code"] for r in best}) > 1:
        codes = ", ".join(sorted({r["rule_code"] for r in best}))
        raise ApprovalRuleError(f"rules {codes} all fit a {document} of {value}; the rule table is ambiguous")
    base = best[0]  # the highest version of the one rule
    roles = list(chain_roles(str(base["approver_chain"]))[1])
    sources = [Source(int(base["id"]), base["rule_code"], int(base["version"]), _why(base, value, False))] * len(roles)
    seen: set[str] = set()
    for r in sorted(
        (r for r in rows if r not in replacing), key=lambda r: (-specificity(r, order), r["rule_code"], -r["version"])
    ):
        if r["rule_code"] in seen:
            continue  # one version of each adding rule
        seen.add(r["rule_code"])
        for role in chain_roles(str(r["approver_chain"]))[1]:
            if role not in roles:
                roles.append(role)
                sources.append(Source(int(r["id"]), r["rule_code"], int(r["version"]), _why(r, value, True)))
    return ApprovalRule(
        base["rule_code"], int(base["version"]), tuple(roles), base["min_value"], base["max_value"], tuple(sources)
    )


def _one(db: Session, sql: str, **params: Any) -> str | None:
    row = db.execute(text(sql), params).first()
    return str(row[0]) if row and row[0] is not None else None


def _person_for_role(
    db: Session, role: str, requester: str | None, department: str | None, location: str | None
) -> str:
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
    elif source == "location_head":
        person = _one(db, "select head_user from location where code = :c", c=location) if location else None
    elif source == "designation":
        person = _one(db, "select code from org_user where designation = :d and active order by code limit 1", d=value)
    if person is None:
        raise ApprovalRuleError(f"cannot find who '{role}' is for this request ({source}); check the organisation data")
    return person


def deputy_on(db: Session, person: str, on_date: date) -> str | None:
    """Who stands in for a person on a date (a delegation in force), if anyone."""
    return _one(
        db,
        "select to_user from delegation where from_user = :p and start_date <= :d "
        "and (end_date is null or end_date >= :d) order by start_date desc, id desc limit 1",
        p=person,
        d=on_date,
    )


def resolve_chain(
    db: Session,
    roles: tuple[str, ...],
    *,
    requester: str | None,
    department: str | None,
    on_date: date,
    location: str | None = None,
    sources: tuple[Source, ...] = (),
    exclude: frozenset[str] = frozenset(),
) -> list[Step]:
    """Turn the roles of a chain into people, in order.

    The same person twice in a row is one step. The requester never approves their own request, nor does anyone in
    `exclude` (the people who made a purchase order). A person on leave on the date (a delegation) is stood in for by
    their delegate, unless the delegate is one of them.
    """
    never = exclude | ({requester} if requester else set())
    steps: list[Step] = []
    for n, role in enumerate(roles):
        person = _person_for_role(db, role, requester, department, location)
        if person in never or (steps and steps[-1].approver == person):
            continue
        delegate = deputy_on(db, person, on_date)
        source = sources[n] if n < len(sources) else None
        steps.append(Step(role, person, delegate if delegate not in never else None, source))
    if roles and not steps:
        raise ApprovalRuleError("the only approver in the chain is the requester; someone else has to approve")
    return steps
