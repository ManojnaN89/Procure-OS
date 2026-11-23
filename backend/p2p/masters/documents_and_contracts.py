"""Supplier documents (added, then verified by someone else) and contracts (stored as a draft, priced, then activated).

A supplier document counts only once a person has checked it against the original, and that person is not the one who
added it. A contract is stored as a draft with its price list; making it active is the approver's decision, and only for
a supplier that can be used.

Contract prices are versions (D48). A price is entered as a draft; it is used only once someone who neither stored the
contract nor entered a price of that version activates it: with the contract itself (version 1), or, on an active
contract, through a proposal for the next version. Two prices of one supplier for the same item, unit and quantity break
never apply on the same day; a new version replaces the one in force from its first day, which then ends the day before.

A rate contract can also come from an approved award of a period RFQ (task 12.3, `from_award`): a draft made in the
approval's transaction in the proposing buyer's name, which the supplier accepts by link and code
(masters/contract_acceptance.py) before anyone may activate it, and never the person who approved the award (D48, D53).
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.contracts import periods_overlap
from p2p.formulas.scope import Scope, intersects
from p2p.formulas.scope import label as scope_label
from p2p.formulas.suppliers import LABELS
from p2p.formulas.words import count
from p2p.masters import regions
from p2p.platform import audit, proposals
from p2p.platform.sod import Actor, user
from p2p.sourcing import documents

D = Decimal


class MasterError(Exception):
    pass


# ---- supplier documents -----------------------------------------------------------------------


def add_document(
    db: Session,
    supplier: str,
    doc_type: str,
    number: str | None,
    issued_on: date | None,
    expires_on: date | None,
    user_id: int,
) -> int:
    if doc_type not in LABELS:
        raise MasterError(f"the document type must be one of {', '.join(sorted(LABELS))}")
    if db.execute(text("select 1 from supplier where code = :c"), {"c": supplier}).first() is None:
        raise MasterError(f"supplier {supplier} does not exist")
    if issued_on and expires_on and expires_on < issued_on:
        raise MasterError("a document cannot expire before it was issued")
    doc_id = int(
        db.execute(
            text(
                "insert into supplier_document (supplier_code, doc_type, number, issued_on, expires_on, verified, added_by) "
                "values (:s, :t, :n, :i, :e, false, :u) returning id"
            ),
            {"s": supplier, "t": doc_type, "n": number, "i": issued_on, "e": expires_on, "u": str(user_id)},
        ).scalar_one()
    )
    audit.record(
        db,
        user(user_id),
        "supplier.document_add",
        "supplier",
        supplier,
        after={"type": doc_type, "number": number, "id": doc_id},
    )
    db.commit()
    return doc_id


def set_verified(db: Session, supplier: str, doc_id: int, verified: bool, user_id: int) -> None:
    row = db.execute(
        text("select added_by, verified from supplier_document where id = :i and supplier_code = :s for update"),
        {"i": doc_id, "s": supplier},
    ).first()
    if row is None:
        raise MasterError(f"document {doc_id} of {supplier} does not exist")
    if verified and row[0] == str(user_id):
        raise MasterError("segregation of duties: the person who added a document cannot verify it")
    db.execute(
        text("update supplier_document set verified = :v, verified_by = :u, verified_at = now() where id = :i"),
        {"v": verified, "u": str(user_id), "i": doc_id},
    )
    audit.record(
        db, user(user_id), "supplier.document_verify" if verified else "supplier.document_unverify", "supplier", supplier,
        before={"verified": bool(row[1])}, after={"verified": verified, "id": doc_id},
    )  # fmt: skip
    db.commit()


# ---- contracts --------------------------------------------------------------------------------

TYPES = ("rate_contract", "service_contract", "framework")


def create_contract(
    db: Session, data: dict[str, Any], user_id: int, *, created_by: int | None = None, commit: bool = True
) -> str:
    """Store a contract as a draft. It is used for sourcing only after an approver activates it. `created_by` names the
    maker when it is not the caller (a contract from an award is the proposing buyer's); `commit=False` leaves the
    caller's transaction open."""
    maker = created_by if created_by is not None else user_id
    supplier = str(data["supplier_code"])
    if db.execute(text("select 1 from supplier where code = :c"), {"c": supplier}).first() is None:
        raise MasterError(f"supplier {supplier} does not exist")
    kind = str(data.get("type") or "rate_contract")
    if kind not in TYPES:
        raise MasterError(f"the type must be one of {', '.join(TYPES)}")
    start, end = data["valid_from"], data.get("valid_to")
    if end is not None and end < start:
        raise MasterError("a contract cannot end before it starts")
    ceiling = data.get("max_value")
    if ceiling is not None and Decimal(ceiling) <= 0:
        raise MasterError("the ceiling must be more than zero (leave it empty for none)")
    last = db.execute(text("select max(code) from contract where code ~ '^CNT-[0-9]+$'")).scalar()
    code = f"CNT-{int(str(last)[4:]) + 1:04d}" if last else "CNT-0001"
    db.execute(
        text(
            "insert into contract (code, supplier_code, type, valid_from, valid_to, max_value, payment_terms_code, delivery_terms, "
            "freight_terms, warranty_months, sla_text, status, created_by) "
            "values (:c, :s, :t, :f, :e, :m, :p, :d, :fr, :w, :sla, 'draft', :by)"
        ),
        {
            "c": code, "s": supplier, "t": kind, "f": start, "e": end, "m": ceiling, "p": data.get("payment_terms_code"),
            "d": data.get("delivery_terms"), "fr": data.get("freight_terms"), "w": data.get("warranty_months"),
            "sla": data.get("sla_text"), "by": str(maker),
        },
    )  # fmt: skip
    if data.get("source_rfq"):
        db.execute(
            text("update contract set source_rfq = :r, award_decision_id = :d where code = :c"),
            {"r": data["source_rfq"], "d": data.get("award_decision_id"), "c": code},
        )
    audit.record(
        db,
        user(user_id),
        "contract.create",
        "contract",
        code,
        after={"supplier": supplier, "type": kind, "ceiling": ceiling, "created_by": maker,
               "source_rfq": data.get("source_rfq")},
    )  # fmt: skip
    if commit:
        db.commit()
    return code


VERSION_ACTION = "contract.price_version"


def _pending_proposal(db: Session, contract: str) -> int | None:
    found = db.execute(
        text("select id from proposal where action = :a and status = 'pending' and payload->>'contract' = :c"),
        {"a": VERSION_ACTION, "c": contract},
    ).scalar()
    return None if found is None else int(found)


def _in_force_before(db: Session, contract: str, key: dict[str, Any], start: date) -> int | None:
    """The active price of the same contract, item, unit and break that a new version starting on `start` replaces."""
    found = db.execute(
        text(
            "select id from price_record where contract_code = :c and item_code = :i and uom is not distinct from :u "
            "and coalesce(min_qty, 1) = :m and status = 'active' and valid_from < :f "
            "and (valid_to is null or valid_to >= :f) and location_code is not distinct from :loc "
            "and region_code is not distinct from :reg"
        ),
        {
            "c": contract,
            "i": key["item"],
            "u": key["uom"],
            "m": key["min_qty"],
            "f": start,
            "loc": key.get("location"),
            "reg": key.get("region"),
        },  # fmt: skip
    ).scalar()
    return None if found is None else int(found)


def _overlap(
    db: Session, supplier: str, key: dict[str, Any], start: date, end: date | None, ignore: set[int]
) -> str | None:
    """A contract price of the supplier for the same item, unit and break, draft or active, valid on a day of the period
    in a place both prices cover (everywhere covers every place; a region covers its locations, D44)."""
    mine = Scope.of([key.get("location")], [key.get("region")])
    region_of = regions.region_of(db)
    rows = db.execute(
        text(
            "select id, contract_code, version, status, valid_from, valid_to, location_code, region_code from price_record "
            "where supplier_code = :s and item_code = :i and uom is not distinct from :u and coalesce(min_qty, 1) = :m "
            "and contract_code is not null and status in ('draft', 'active') order by valid_from, id"
        ),
        {"s": supplier, "i": key["item"], "u": key["uom"], "m": key["min_qty"]},
    ).all()
    for r in rows:
        theirs = Scope.of([r[6]], [r[7]])
        if r[0] not in ignore and periods_overlap(start, end, r[4], r[5]) and intersects(mine, theirs, region_of):
            until = f"{r[5]:%d %b %Y}" if r[5] else "no end"
            return (
                f"the price would overlap the {r[3]} price of {key['item']} on {r[1]} (version {r[2]}, "
                f"{r[4]:%d %b %Y} to {until}, {scope_label(theirs, regions.names(db))}); prices of one supplier for an "
                "item may not apply on the same day in the same place"
            )
    return None


def add_price(
    db: Session,
    contract: str,
    data: dict[str, Any],
    user_id: int,
    *,
    created_by: int | None = None,
    commit: bool = True,
) -> int:
    """Enter a price as a draft of the contract's next version. Nothing is bought at it until it is activated."""
    maker = created_by if created_by is not None else user_id
    c = db.execute(
        text("select supplier_code, status, valid_from, valid_to from contract where code = :c for update"),
        {"c": contract},
    ).first()
    if c is None:
        raise MasterError(f"contract {contract} does not exist")
    if c[1] in ("expired", "declined"):
        raise MasterError(f"a contract that is {c[1]} takes no new prices")
    if _pending_proposal(db, contract) is not None:
        raise MasterError(
            "a price version of this contract is waiting to be activated; it takes no prices until decided"
        )
    item = str(data["item_code"])
    if db.execute(text("select 1 from item where code = :i"), {"i": item}).first() is None:
        raise MasterError(f"item {item} does not exist")
    price = Decimal(data["price"])
    if price <= 0:
        raise MasterError("the price must be more than zero")
    start = data.get("valid_from") or c[2]
    end = data.get("valid_to") or c[3]
    if end is not None and end < start:
        raise MasterError("a price cannot end before it starts")
    if start < c[2] or (c[3] is not None and (end is None or end > c[3])):
        raise MasterError("a price must fall within the contract's dates")
    key = {
        "item": item,
        "uom": data.get("uom"),
        "min_qty": int(data.get("min_qty") or 1),
        "location": data.get("location_code") or None,
        "region": data.get("region_code") or None,
    }
    if not key["location"] and not key["region"]:  # a new version of a price keeps the place of the one it replaces
        key["location"], key["region"] = _scope_in_force(db, contract, key, start)
    _check_price_scope(db, key)
    replaced = _in_force_before(db, contract, key, start) if c[1] == "active" else None
    clash = _overlap(db, c[0], key, start, end, {replaced} if replaced else set())
    if clash:
        raise MasterError(clash)
    version = int(
        db.execute(
            text(
                "select coalesce((select max(version) from price_record where contract_code = :c and status = 'draft'), "
                "(select coalesce(max(version), 0) + 1 from price_record where contract_code = :c))"
            ),
            {"c": contract},
        ).scalar_one()
    )
    rid = int(
        db.execute(
            text(
                "insert into price_record (supplier_code, item_code, contract_code, uom, price, min_qty, valid_from, valid_to, "
                "lead_days, version, status, created_by, location_code, region_code) "
                "values (:s, :i, :c, :u, :p, :m, :f, :e, :l, :v, 'draft', :by, :loc, :reg) returning id"
            ),
            {
                "s": c[0], "i": item, "c": contract, "u": key["uom"], "p": price, "m": key["min_qty"],
                "f": start, "e": end, "l": data.get("lead_days"), "v": version, "by": str(maker),
                "loc": key["location"], "reg": key["region"],
            },
        ).scalar_one()
    )  # fmt: skip
    audit.record(
        db, user(user_id), "contract.price_add", "contract", contract,
        after={
            "item": item, "price": price, "id": rid, "version": version, "status": "draft",
            "location": key["location"], "region": key["region"],
        },
    )  # fmt: skip
    if commit:
        db.commit()
    return rid


def _scope_in_force(db: Session, contract: str, key: dict[str, Any], start: date) -> tuple[str | None, str | None]:
    """The place of the contract's active price for the same item, unit and break on the start date, when there is one
    (one place only); else everywhere."""
    rows = db.execute(
        text(
            "select distinct location_code, region_code from price_record where contract_code = :c and item_code = :i "
            "and uom is not distinct from :u and coalesce(min_qty, 1) = :m and status = 'active' and valid_from <= :f "
            "and (valid_to is null or valid_to >= :f)"
        ),
        {"c": contract, "i": key["item"], "u": key["uom"], "m": key["min_qty"], "f": start},
    ).all()
    return (rows[0][0], rows[0][1]) if len(rows) == 1 else (None, None)


def _check_price_scope(db: Session, key: dict[str, Any]) -> None:
    """A price applies everywhere, at one location or in one region (D44)."""
    if key["location"] and key["region"]:
        raise MasterError("a price applies at one location or in one region, not both")
    if (
        key["location"]
        and db.execute(text("select 1 from location where code = :c"), {"c": key["location"]}).first() is None
    ):
        raise MasterError(f"there is no location {key['location']}")
    if key["region"] and db.execute(text("select 1 from region where code = :c"), {"c": key["region"]}).first() is None:
        raise MasterError(f"there is no region {key['region']}")


def _refuse_maker(db: Session, contract: str, version: int, user_id: int) -> None:
    """Who stored the contract or entered a price of the version never activates it (D48)."""
    makers = {
        str(r[0])
        for r in db.execute(
            text(
                "select created_by from contract where code = :c union "
                "select created_by from price_record where contract_code = :c and version = :v"
            ),
            {"c": contract, "v": version},
        )
        if r[0] is not None
    }
    if str(user_id) in makers:
        raise MasterError(
            "segregation of duties: whoever stored the contract or entered its prices cannot activate them"
        )


def _activate_rows(db: Session, contract: str, supplier: str, version: int, user_id: int) -> dict[str, Any]:
    """Make the draft prices of a version active. A price it replaces ends the day before the new one starts."""
    rows = db.execute(
        text(
            "select id, item_code, uom, coalesce(min_qty, 1), valid_from, valid_to, location_code, region_code "
            "from price_record where contract_code = :c and version = :v and status = 'draft' order by id for update"
        ),
        {"c": contract, "v": version},
    ).all()
    if not rows:
        raise MasterError(f"version {version} of {contract} has no draft prices")
    ended: list[int] = []
    for rid, item, uom, min_qty, start, end, loc, reg in rows:
        key = {"item": item, "uom": uom, "min_qty": min_qty, "location": loc, "region": reg}
        replaced = _in_force_before(db, contract, key, start)
        clash = _overlap(db, supplier, key, start, end, {rid} | ({replaced} if replaced else set()))
        if clash:
            raise MasterError(clash)
        if replaced is not None:
            db.execute(
                text("update price_record set valid_to = :t where id = :i"),
                {"t": start - timedelta(days=1), "i": replaced},
            )
            ended.append(replaced)
    db.execute(
        text(
            "update price_record set status = 'active', activated_by = :u, activated_at = now() "
            "where contract_code = :c and version = :v and status = 'draft'"
        ),
        {"u": str(user_id), "c": contract, "v": version},
    )
    return {"version": version, "prices": [r[0] for r in rows], "ended": ended}


def activate(db: Session, contract: str, user_id: int, *, today: date) -> None:
    """Make a draft contract and its first price version active, by someone who did not store or price it."""
    c = db.execute(
        text("select supplier_code, status, valid_to from contract where code = :c for update"), {"c": contract}
    ).first()
    if c is None:
        raise MasterError(f"contract {contract} does not exist")
    if c[1] != "draft":
        raise MasterError(f"{contract} is {c[1]}; only a draft is activated")
    if c[2] is not None and c[2] < today:
        raise MasterError("the contract has already ended")
    status = db.execute(text("select status from supplier where code = :c"), {"c": c[0]}).scalar()
    if status != "active":
        raise MasterError(f"the supplier {c[0]} is {status}, so a contract with it cannot be activated")
    check = documents.check_supplier(db, c[0], today)
    if not check.ok:
        raise MasterError("the supplier cannot be used: " + "; ".join(check.blocking))
    draft = db.execute(
        text("select max(version) from price_record where contract_code = :c and status = 'draft'"), {"c": contract}
    ).scalar()
    if draft is None:
        raise MasterError("a contract with no prices cannot be used: add its price list first")
    _refuse_maker(db, contract, int(draft), user_id)
    refuse_award_steps(db, contract, user_id)
    done = _activate_rows(db, contract, c[0], int(draft), user_id)
    db.execute(text("update contract set status = 'active' where code = :c"), {"c": contract})
    audit.record(
        db,
        user(user_id),
        "contract.activate",
        "contract",
        contract,
        before={"status": "draft"},
        after={"status": "active", **done},
    )
    db.commit()


class AwardActivationRefused(MasterError):
    """Activating a contract from an award before the supplier accepted it (409) or by its award's approver (403)."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def refuse_award_steps(db: Session, contract: str, user_id: int) -> None:
    """A contract from an award is activated only once its supplier accepted the terms, and never by whoever approved
    the award (OP-13: four separate acts)."""
    row = db.execute(
        text(
            "select c.award_decision_id, c.supplier_accepted_at, d.decided_by from contract c "
            "left join award_decision d on d.id = c.award_decision_id where c.code = :c"
        ),
        {"c": contract},
    ).first()
    if row is None or row[0] is None:
        return
    if row[2] is not None and str(row[2]) == str(user_id):
        raise AwardActivationRefused("whoever approved the award does not activate its contract", 403)
    if row[1] is None:
        raise AwardActivationRefused(f"{contract} waits for the supplier to accept its terms", 409)


# ---- a contract from an approved award (task 12.3) ----------------------------------------------------------


def _award_terms(db: Session, supplier: str, parts: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Payment terms and delivery terms from the quote, else the supplier's own."""
    terms = next((p["payment_terms_code"] for p in parts if p["payment_terms_code"]), None)
    terms = (
        terms or db.execute(text("select payment_terms_code from supplier where code = :c"), {"c": supplier}).scalar()
    )
    return terms, next((p["delivery_terms"] for p in parts if p["delivery_terms"]), None)


def _award_key(rfq: dict[str, Any], part: dict[str, Any]) -> dict[str, Any]:
    return {"item": part["item_code"], "uom": part["uom"], "min_qty": 1, "location": rfq["delivery_location"],
            "region": None}  # fmt: skip


def award_clash(db: Session, supplier: str, parts: list[dict[str, Any]], rfq: dict[str, Any]) -> str | None:
    """Why the award's contract prices could not be stored (an overlap with a price of the supplier), else None."""
    for p in parts:
        clash = _overlap(db, supplier, _award_key(rfq, p), rfq["period_from"], rfq["period_to"], set())
        if clash:
            return clash
    return None


def from_award(
    db: Session, rfq: dict[str, Any], decision: dict[str, Any], supplier: str, parts: list[dict[str, Any]], by: int
) -> str:
    """The draft rate contract of one awarded supplier, in the approval's transaction (does not commit): the period,
    a ceiling of the awarded value before tax, the quote's terms, one price per awarded line at the RFQ's location
    (OP-5). Its maker is the proposing buyer, so the award's approver is never recorded as the maker."""
    from p2p.formulas.money import round_money  # noqa: PLC0415 - the one money helper this function needs

    buyer = int(decision["proposed_by"])
    terms, delivery = _award_terms(db, supplier, parts)
    ceiling = sum((round_money(p["qty"] * p["price"]) for p in parts), Decimal(0))
    data = {
        "supplier_code": supplier, "type": "rate_contract", "valid_from": rfq["period_from"],
        "valid_to": rfq["period_to"], "max_value": ceiling, "payment_terms_code": terms, "delivery_terms": delivery,
        "source_rfq": rfq["no"], "award_decision_id": decision["id"],
    }  # fmt: skip
    code = create_contract(db, data, by, created_by=buyer, commit=False)
    for p in parts:
        add_price(
            db, code,
            {"item_code": p["item_code"], "price": p["price"], "uom": p["uom"], "min_qty": 1,
             "lead_days": p["delivery_days"], "location_code": rfq["delivery_location"]},
            by, created_by=buyer, commit=False,
        )  # fmt: skip
    return code


# ---- a new price version of an active contract ------------------------------------------------


def _draft_version(db: Session, contract: str) -> tuple[int, list[int]]:
    rows = db.execute(
        text("select version, id from price_record where contract_code = :c and status = 'draft' order by id"),
        {"c": contract},
    ).all()
    if not rows:
        raise MasterError(f"{contract} has no draft prices")
    return int(rows[0][0]), [int(r[1]) for r in rows]


def _status(db: Session, contract: str) -> str:
    status = db.execute(text("select status from contract where code = :c"), {"c": contract}).scalar()
    if status is None:
        raise MasterError(f"contract {contract} does not exist")
    return str(status)


def submit_version(db: Session, contract: str, user_id: int) -> int:
    """Ask for the draft prices of an active contract to be activated: a proposal someone else decides. Returns its id."""
    status = _status(db, contract)
    if status != "active":
        raise MasterError(f"{contract} is {status}; the prices of a draft contract are activated with the contract")
    if _pending_proposal(db, contract) is not None:
        raise MasterError("this price version is already waiting to be activated")
    version, ids = _draft_version(db, contract)
    return proposals.propose(
        db,
        user(user_id),
        action=VERSION_ACTION,
        summary=f"Activate price version {version} of {contract} ({count(len(ids), 'price')})",
        payload={"contract": contract, "version": version, "prices": ids},
        required_permission="contract.activate",
    )


@proposals.guard(VERSION_ACTION)
def _may_activate(db: Session, payload: dict[str, Any], actor: Actor) -> None:
    if actor.kind == "user":
        _refuse_maker(db, str(payload["contract"]), int(payload["version"]), int(actor.id))


@proposals.executor(VERSION_ACTION)
def _activate_version(db: Session, payload: dict[str, Any], actor: Actor) -> dict[str, Any]:
    contract, version = str(payload["contract"]), int(payload["version"])
    _may_activate(db, payload, actor)
    c = db.execute(text("select supplier_code, status from contract where code = :c for update"), {"c": contract}).one()
    if c[1] != "active":
        raise MasterError(f"{contract} is {c[1]}")
    if _draft_version(db, contract) != (version, [int(i) for i in payload["prices"]]):
        raise MasterError("the draft prices changed after the version was sent for activation")
    done = _activate_rows(db, contract, c[0], version, int(actor.id))
    audit.record(db, actor, "contract.price_version_activate", "contract", contract, after=done)
    db.commit()
    return done


def activate_version(db: Session, contract: str, user_id: int, note: str | None = None) -> dict[str, Any]:
    """The approver activates the version waiting for them (through its proposal)."""
    _status(db, contract)
    pid = _pending_proposal(db, contract)
    if pid is None:
        raise MasterError(f"no price version of {contract} is waiting to be activated")
    try:
        proposals.approve(db, pid, user_id, note=note)
    except proposals.ProposalError as exc:
        raise MasterError(str(exc)) from None
    execution, result = db.execute(text("select execution, result from proposal where id = :i"), {"i": pid}).one()
    if execution != "ok":
        raise MasterError(f"the version was not activated: {(result or {}).get('error', 'see the log')}")
    return dict(result or {})


def withdraw_version(db: Session, contract: str, user_id: int, reason: str, *, rejected: bool = False) -> int:
    """Drop the draft prices of a contract: the maker withdraws them, or the approver rejects the version waiting.
    Returns how many prices were dropped."""
    _status(db, contract)
    why = reason.strip()
    if not why:
        raise MasterError("a reason is needed")
    version, ids = _draft_version(db, contract)
    pid = _pending_proposal(db, contract)
    try:
        if rejected:
            if pid is None:
                raise MasterError(f"no price version of {contract} is waiting to be activated")
            proposals.reject(db, pid, user_id, why)
        elif pid is not None:
            proposals.withdraw(db, pid, user_id, why)
    except proposals.ProposalError as exc:
        raise MasterError(str(exc)) from None
    db.execute(
        text("update price_record set status = 'withdrawn' where contract_code = :c and status = 'draft'"),
        {"c": contract},
    )
    action = "contract.price_version_reject" if rejected else "contract.price_version_withdraw"
    audit.record(db, user(user_id), action, "contract", contract, after={"version": version, "prices": ids}, reason=why)
    db.commit()
    return len(ids)
