"""Supplier documents (added, then verified by someone else) and contracts (stored as a draft, priced, then activated).

A supplier document counts only once a person has checked it against the original, and that person is not the one who
added it. A contract is stored as a draft with its price list; making it active is the approver's decision, and only for
a supplier that can be used.

Contract prices are versions (D48). A price is entered as a draft; it is used only once someone who neither stored the
contract nor entered a price of that version activates it: with the contract itself (version 1), or, on an active
contract, through a proposal for the next version. Two prices of one supplier for the same item, unit and quantity break
never apply on the same day; a new version replaces the one in force from its first day, which then ends the day before.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.contracts import periods_overlap
from p2p.formulas.suppliers import LABELS
from p2p.formulas.words import count
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


def create_contract(db: Session, data: dict[str, Any], user_id: int) -> str:
    """Store a contract as a draft. It is used for sourcing only after an approver activates it."""
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
            "sla": data.get("sla_text"), "by": str(user_id),
        },
    )  # fmt: skip
    audit.record(
        db,
        user(user_id),
        "contract.create",
        "contract",
        code,
        after={"supplier": supplier, "type": kind, "ceiling": ceiling},
    )
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
            "and (valid_to is null or valid_to >= :f)"
        ),
        {"c": contract, "i": key["item"], "u": key["uom"], "m": key["min_qty"], "f": start},
    ).scalar()
    return None if found is None else int(found)


def _overlap(
    db: Session, supplier: str, key: dict[str, Any], start: date, end: date | None, ignore: set[int]
) -> str | None:
    """A contract price of the supplier for the same item, unit and break, draft or active, valid on a day of the period."""
    rows = db.execute(
        text(
            "select id, contract_code, version, status, valid_from, valid_to from price_record "
            "where supplier_code = :s and item_code = :i and uom is not distinct from :u and coalesce(min_qty, 1) = :m "
            "and contract_code is not null and status in ('draft', 'active') order by valid_from, id"
        ),
        {"s": supplier, "i": key["item"], "u": key["uom"], "m": key["min_qty"]},
    ).all()
    for r in rows:
        if r[0] not in ignore and periods_overlap(start, end, r[4], r[5]):
            until = f"{r[5]:%d %b %Y}" if r[5] else "no end"
            return (
                f"the price would overlap the {r[3]} price of {key['item']} on {r[1]} (version {r[2]}, "
                f"{r[4]:%d %b %Y} to {until}); prices of one supplier for an item may not apply on the same day"
            )
    return None


def add_price(db: Session, contract: str, data: dict[str, Any], user_id: int) -> int:
    """Enter a price as a draft of the contract's next version. Nothing is bought at it until it is activated."""
    c = db.execute(
        text("select supplier_code, status, valid_from, valid_to from contract where code = :c for update"),
        {"c": contract},
    ).first()
    if c is None:
        raise MasterError(f"contract {contract} does not exist")
    if c[1] == "expired":
        raise MasterError("an expired contract takes no new prices")
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
    key = {"item": item, "uom": data.get("uom"), "min_qty": int(data.get("min_qty") or 1)}
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
                "lead_days, version, status, created_by) values (:s, :i, :c, :u, :p, :m, :f, :e, :l, :v, 'draft', :by) "
                "returning id"
            ),
            {
                "s": c[0], "i": item, "c": contract, "u": key["uom"], "p": price, "m": key["min_qty"],
                "f": start, "e": end, "l": data.get("lead_days"), "v": version, "by": str(user_id),
            },
        ).scalar_one()
    )  # fmt: skip
    audit.record(
        db, user(user_id), "contract.price_add", "contract", contract,
        after={"item": item, "price": price, "id": rid, "version": version, "status": "draft"},
    )  # fmt: skip
    db.commit()
    return rid


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
            "select id, item_code, uom, coalesce(min_qty, 1), valid_from, valid_to from price_record "
            "where contract_code = :c and version = :v and status = 'draft' order by id for update"
        ),
        {"c": contract, "v": version},
    ).all()
    if not rows:
        raise MasterError(f"version {version} of {contract} has no draft prices")
    ended: list[int] = []
    for rid, item, uom, min_qty, start, end in rows:
        key = {"item": item, "uom": uom, "min_qty": min_qty}
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
