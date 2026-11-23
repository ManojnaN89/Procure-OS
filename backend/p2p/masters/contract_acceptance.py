"""The supplier accepts (or declines) a rate contract from an award (task 12.3, D53): signed link plus one-time code.

The award letter carries a signed link (its own key, 14 days, works once, the company in the token). The page shows the
contract's terms and sends a one-time code only to the address the letter went to; accepting or declining needs that
code, so a forwarded link decides nothing. Accepting records who accepted and when and asks a second person, neither the
contract's maker nor the award's approver, to activate it; declining ends the contract and tells the buyer (nothing is
awarded to anyone else, OP-15). The contract buys nothing until a person activates it (D48).
"""

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals.workflow import org_code_of
from p2p.config import get_settings
from p2p.platform import audit, notifications, otp, tasks
from p2p.platform.signing import SignatureError, Signer
from p2p.platform.sod import Actor

PURPOSE = "accept"
EXPIRES = timedelta(days=14)  # OP-21: suppliers review terms; the code still guards the decision
SIGNER = Signer("contract-link")
CODES = otp.OneTimeCodes("contract_accept")
TASK = "contract_activation"


class LinkError(Exception):
    """The link cannot be used: altered, expired, used, or the contract is no longer waiting for the supplier."""


@dataclass(frozen=True)
class Link:
    tenant: str
    id: int
    contract: str
    supplier: str
    expires: int


def issue_link(db: Session, tenant: str, contract: str, address: str, *, now: datetime | None = None) -> str:
    """A link for the contract's supplier, sent to `address`; returns its token. The caller commits."""
    now = now or datetime.now(UTC)
    supplier = db.execute(text("select supplier_code from contract where code = :c"), {"c": contract}).scalar_one()
    expires = now + EXPIRES
    link_id = db.execute(
        text(
            "insert into contract_link (contract_code, supplier_code, purpose, sent_to, issued_at, expires_at) "
            "values (:c, :s, :p, :to, :n, :e) returning id"
        ),
        {"c": contract, "s": supplier, "p": PURPOSE, "to": address, "n": now, "e": expires},
    ).scalar_one()
    return SIGNER.sign([tenant, int(link_id), contract, str(supplier), int(expires.timestamp())])


def url(token: str) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/contract/{token}"


def read(token: str, *, now: float | None = None) -> Link:
    """The link a token stands for. Raises LinkError for anything altered, malformed or expired."""
    try:
        tenant, link_id, contract, supplier, expires = SIGNER.read(token)
    except SignatureError as exc:
        raise LinkError(str(exc)) from None
    except ValueError:
        raise LinkError("this link is not valid") from None
    if (time.time() if now is None else now) > int(expires):
        raise LinkError("this link has expired: please reply to the award email instead")
    return Link(str(tenant), int(link_id), str(contract), str(supplier), int(expires))


def check(db: Session, link: Link, *, lock: bool = False) -> dict[str, Any]:
    """The link's row and its contract, if the link may still be used."""
    sql = (
        "select l.contract_code, l.supplier_code, l.sent_to, l.used_at, c.status, c.supplier_code as contract_supplier, "  # noqa: S608
        "c.supplier_accepted_at, c.supplier_declined_at from contract_link l join contract c on c.code = l.contract_code "
        "where l.id = :i" + (" for update of l, c" if lock else "")
    )
    row = db.execute(text(sql), {"i": link.id}).mappings().first()
    if row is None or (row["contract_code"], row["supplier_code"]) != (link.contract, link.supplier):
        raise LinkError("this link is not valid")
    if row["contract_supplier"] != link.supplier:
        raise LinkError(f"contract {link.contract} is not with you")
    if row["used_at"] is not None:
        raise LinkError("this link has already been used: it works once")
    if row["supplier_accepted_at"] is not None or row["supplier_declined_at"] is not None or row["status"] != "draft":
        raise LinkError(f"contract {link.contract} has been answered already")
    return dict(row)


def terms(db: Session, contract: str) -> dict[str, Any]:
    """What the supplier is asked to accept: the contract and its prices."""
    c = dict(
        db.execute(
            text(
                "select c.*, coalesce(s.trade_name, s.legal_name) as supplier_name, t.name as terms_name, "
                "(select coalesce(short_name, legal_name) from company limit 1) as company from contract c "
                "join supplier s on s.code = c.supplier_code left join payment_terms t on t.code = c.payment_terms_code "
                "where c.code = :c"
            ),
            {"c": contract},
        ).mappings().one()
    )  # fmt: skip
    c["prices"] = [
        dict(r)
        for r in db.execute(
            text(
                "select p.item_code, i.name, p.uom, p.price, p.lead_days, l.name as place from price_record p "
                "left join item i on i.code = p.item_code left join location l on l.code = p.location_code "
                "where p.contract_code = :c and p.status = 'draft' order by p.item_code"
            ),
            {"c": contract},
        ).mappings()
    ]
    return c


def send_code(db: Session, link: Link) -> str:
    """Email a one-time code to the address the letter went to (never another). Returns that address."""
    row = check(db, link)
    db.rollback()
    return CODES.issue_to(db, link.contract, str(row["sent_to"]), subject=f"Your code for contract {link.contract}")


def _activator(db: Session, tenant: str, contract: str) -> int | None:
    """The first holder of contract.activate (by organisation code) who neither made the contract nor approved its award."""
    row = db.execute(
        text(
            "select c.created_by, d.decided_by from contract c left join award_decision d on d.id = c.award_decision_id "
            "where c.code = :c"
        ),
        {"c": contract},
    ).one()
    never = {str(x) for x in row if x is not None}
    holders = [
        int(u)
        for u in db.execute(
            text(
                "select distinct ur.user_id from user_role ur join role_permission rp on rp.role = ur.role "
                "where rp.action = 'contract.activate'"
            )
        ).scalars()
        if str(u) not in never
    ]
    coded = sorted((org_code_of(db, u) or f"~{u}", u) for u in holders)
    return coded[0][1] if coded else None


def accept(db: Session, tenant: str, link: Link, code: str | None) -> None:
    """The supplier accepts the contract's terms with the code sent to the letter's address."""
    row = check(db, link, lock=True)
    CODES.verify_address(db, link.contract, str(row["sent_to"]), code)
    check(db, link, lock=True)  # the code commits its own refusal; the link is read again under the lock
    db.execute(
        text("update contract set supplier_accepted_at = now(), supplier_accepted_by = :a where code = :c"),
        {"a": row["sent_to"], "c": link.contract},
    )
    _use(db, link, "accept")
    audit.record(db, Actor("service", f"contract-link:{row['sent_to']}"), "contract.supplier_accept", "contract", link.contract,
                 after={"by": row["sent_to"]})  # fmt: skip
    db.commit()
    buyer = db.execute(text("select created_by from contract where code = :c"), {"c": link.contract}).scalar()
    if buyer and str(buyer).isdigit():
        notifications.notify(
            db, tenant, int(buyer), "contract", f"{link.supplier} accepted contract {link.contract}",
            "It is activated by a second person.", link=f"#/contracts/{link.contract}",
        )  # fmt: skip
    who = _activator(db, tenant, link.contract)
    if who is not None:
        tasks.create_task(
            db, tenant, kind=TASK, title=f"Activate contract {link.contract}: the supplier accepted its terms",
            assignee=who, sla_hours=24, payload={"contract": link.contract, "link": f"#/contracts/{link.contract}"},
        )  # fmt: skip


def decline(db: Session, tenant: str, link: Link, code: str | None, reason: str | None) -> None:
    """The supplier declines the contract (a reason is needed): it ends, its prices are withdrawn, the buyer is told."""
    why = (reason or "").strip()
    if not why:
        raise LinkError("say why you decline")
    row = check(db, link, lock=True)
    CODES.verify_address(db, link.contract, str(row["sent_to"]), code)
    check(db, link, lock=True)
    db.execute(
        text(
            "update contract set status = 'declined', supplier_declined_at = now(), decline_reason = :r where code = :c"
        ),
        {"r": why, "c": link.contract},
    )
    db.execute(
        text("update price_record set status = 'withdrawn' where contract_code = :c and status = 'draft'"),
        {"c": link.contract},
    )
    _use(db, link, "decline")
    audit.record(db, Actor("service", f"contract-link:{row['sent_to']}"), "contract.supplier_decline", "contract", link.contract,
                 after={"by": row["sent_to"]}, reason=why)  # fmt: skip
    db.commit()
    buyer = db.execute(text("select created_by from contract where code = :c"), {"c": link.contract}).scalar()
    name = db.execute(
        text("select coalesce(trade_name, legal_name) from supplier where code = :c"), {"c": link.supplier}
    ).scalar()
    if buyer and str(buyer).isdigit():
        tasks.create_task(
            db, tenant, kind="contract_declined", title=f"{name or link.supplier} declined {link.contract}: {why}",
            assignee=int(buyer), sla_hours=24, payload={"contract": link.contract, "link": f"#/contracts/{link.contract}"},
        )  # fmt: skip


def _use(db: Session, link: Link, what: str) -> None:
    db.execute(text("update contract_link set used_at = now(), used_for = :w where id = :i"), {"w": what, "i": link.id})
