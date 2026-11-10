"""Approving from an email: a signed link that works once.

The email to an approver carries a link to a page that shows the request and asks for the decision. The link is signed
(it cannot be made up or altered), expires, and names one person at one step of one request. Opening it changes nothing,
so a mail scanner that fetches links does no harm; the decision is a separate button. It works once because the step stops
waiting after the decision, and a link for a step that is no longer waiting for that person is refused. The decision is the
same as on the screen: same turn, same role, same separation of duties, and the audit row says it came by an emailed link.
"""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.approvals import workflow
from p2p.config import get_settings
from p2p.platform import permissions

EXPIRES_DAYS = 7
VIA = "emailed link"


class LinkError(Exception):
    """The link cannot be used: altered, expired, or the request is no longer waiting for this person."""


@dataclass(frozen=True)
class Link:
    tenant: str
    request_no: str
    round_no: int
    step: int
    user_id: int
    expires: int


def _key() -> bytes:
    secret = get_settings().jwt_secret
    if not secret:
        raise LinkError("approval links are not available: no signing secret is set")
    return hashlib.sha256(b"approval-link:" + secret.encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text_: str) -> bytes:
    return base64.urlsafe_b64decode(text_ + "=" * (-len(text_) % 4))


def make(tenant: str, request_no: str, round_no: int, step: int, user_id: int, *, now: float | None = None) -> str:
    expires = int((time.time() if now is None else now) + EXPIRES_DAYS * 86400)
    body = _b64(json.dumps([tenant, request_no, round_no, step, user_id, expires], separators=(",", ":")).encode())
    return f"{body}.{_b64(hmac.new(_key(), body.encode(), hashlib.sha256).digest())}"


def read(token: str, *, now: float | None = None) -> Link:
    """The link a token stands for. Raises LinkError for anything altered, malformed or expired."""
    try:
        body, signature = token.split(".")
        good = hmac.compare_digest(_unb64(signature), hmac.new(_key(), body.encode(), hashlib.sha256).digest())
        tenant, no, round_no, step, user_id, expires = json.loads(_unb64(body))
    except LinkError:
        raise
    except Exception:  # noqa: BLE001 - whatever is wrong with the text, it is not a link we made
        raise LinkError("this link is not valid") from None
    if not good:
        raise LinkError("this link is not valid")
    if (time.time() if now is None else now) > int(expires):
        raise LinkError("this link has expired: open the request in the app")
    return Link(str(tenant), str(no), int(round_no), int(step), int(user_id), int(expires))


def _waiting(db: Session, link: Link) -> dict[str, Any]:
    """The request as the approver should see it, if the step is still waiting for this person."""
    row = (
        db.execute(
            text(
                "select r.no, r.status, r.requester, r.total_value, r.justification, r.approval_round, r.rule_code, r.rule_version, "
                "r.decision_reason, s.status as step_status, s.approver, s.delegate from request r "
                "join approval_step s on s.request_no = r.no and s.round = :r and s.step = :s where r.no = :n"
            ),
            {"n": link.request_no, "r": link.round_no, "s": link.step},
        )
        .mappings()
        .first()
    )
    me = workflow.org_code_of(db, link.user_id)
    if (
        row is None
        or row["status"] != "submitted"
        or row["approval_round"] != link.round_no
        or row["step_status"] != "pending"
        or me is None
        or me not in (row["approver"], row["delegate"])
    ):
        raise LinkError("this request is no longer waiting for your decision (it may already be decided)")
    return dict(row)


def summary(db: Session, link: Link) -> dict[str, Any]:
    """What the page shows. Changes nothing."""
    row = _waiting(db, link)
    lines = [
        {"item": r[0], "qty": str(r[1].normalize()), "uom": r[2] or "", "price": None if r[3] is None else str(r[3])}
        for r in db.execute(
            text(
                "select coalesce(i.name, l.free_text), l.qty, l.uom, l.est_price from request_line l "
                "left join item i on i.code = l.item_code where l.request_no = :n and l.status <> 'cancelled' order by l.line_no"
            ),
            {"n": link.request_no},
        )
    ]
    return {
        "no": row["no"],
        "requester": workflow.name_of(db, row["requester"]),
        "total": str(Decimal(row["total_value"]).quantize(Decimal("0.01"))),
        "why": row["justification"] or "",
        "rule": f"{row['rule_code']} v{row['rule_version']}",
        "lines": lines,
    }


def decide(db: Session, link: Link, action: str, reason: str | None) -> str:
    """Approve or reject for the person the link names. Everything the screen checks is checked here too."""
    _waiting(db, link)
    try:
        if action == "approve":
            return workflow.approve(db, link.tenant, link.request_no, link.user_id, reason, via=VIA)
        if action == "reject":
            return workflow.reject(db, link.request_no, link.user_id, reason, tenant=link.tenant, via=VIA)
    except (workflow.ApprovalError, permissions.PermissionDenied) as exc:
        db.rollback()
        raise LinkError(str(exc)) from None
    raise LinkError("the decision must be approve or reject")


def url(tenant: str, request_no: str, round_no: int, step: int, user_id: int) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/approve/{make(tenant, request_no, round_no, step, user_id)}"
