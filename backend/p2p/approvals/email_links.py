"""Approving from an email: a signed link that works once.

The email to an approver carries a link to a page that shows the request and asks for the decision. The link is signed
(it cannot be made up or altered), expires, and names one person at one step of one request. Opening it changes nothing,
so a mail scanner that fetches links does no harm; the decision is a separate button. It works once because the step stops
waiting after the decision, and a link for a step that is no longer waiting for that person is refused. The decision is the
same as on the screen: same turn, same role, same separation of duties, and the audit row says it came by an emailed link.
Approving a request above the setting `approval_otp_above` also needs a one-time code emailed to the approver
(platform/otp.py); rejecting or returning needs a reason, never a code.
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

from p2p.approvals import budget, workflow
from p2p.config import get_settings
from p2p.formulas.calendar import business_today
from p2p.formulas.money import format_inr, format_qty
from p2p.platform import otp, permissions

EXPIRES_DAYS = 7
VIA = "emailed link"
CODES = otp.OneTimeCodes("approval")
ACTIONS = ("approve", "reject", "return")


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

    @property
    def ref(self) -> str:
        """What a one-time code for this link is tied to: one step of one round of one request."""
        return f"{self.request_no}:{self.round_no}:{self.step}"


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
                "select r.no, r.status, r.approval_round, s.status as step_status, s.approver, s.delegate from request r "
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


def facts(db: Session, no: str, round_no: int, step_no: int, me: str | None) -> dict[str, Any]:
    """What the approver is shown, in the email and on the page: the request, its lines, the estimated total, the budget
    now, the rule and why the step came to this person. A plain template: no model writes any of it."""
    req = (
        db.execute(
            text(
                "select r.no, r.requester, r.total_value, r.justification, r.rule_code, r.rule_version, r.ship_to_location, "
                "r.department_code, d.name as department, l.name as location, s.role, s.approver, s.delegate, "
                "s.route_reason, (select count(*) from approval_step x where x.request_no = r.no and x.round = s.round "
                "and x.status <> 'cancelled') as steps from request r join approval_step s on s.request_no = r.no "
                "and s.round = :r and s.step = :s left join department d on d.code = r.department_code "
                "left join location l on l.code = r.ship_to_location where r.no = :n"
            ),
            {"n": no, "r": round_no, "s": step_no},
        )
        .mappings()
        .one()
    )
    lines = [
        {
            "item": str(r[0] or ""),
            "qty": format_qty(r[1]),
            "uom": r[2] or "",
            "price": "" if r[3] is None else format_inr(r[3]),
            "amount": "" if r[3] is None else format_inr(Decimal(r[1]) * Decimal(r[3])),
            "need_by": "" if r[4] is None else f"{r[4]:%d %b %Y}",
        }
        for r in db.execute(
            text(
                "select coalesce(i.name, l.free_text), l.qty, l.uom, l.est_price, l.need_by from request_line l "
                "left join item i on i.code = l.item_code where l.request_no = :n and l.status <> 'cancelled' order by l.line_no"
            ),
            {"n": no},
        )
    ]
    total = Decimal(req["total_value"]).quantize(Decimal("0.01"))
    requester = workflow.name_of(db, req["requester"]) if req["requester"] else "A stock-level check"
    why = f"It came to you as {str(req['role']).replace('_', ' ')} (step {step_no} of {req['steps']})"
    if me and me != req["approver"]:
        why += f", standing in for {workflow.name_of(db, req['approver'])}"
    route = str(req["route_reason"] or f"rule {req['rule_code']} v{req['rule_version']}").rstrip(".")
    why += f". {route[:1].upper()}{route[1:]}."
    where = req["location"] or req["ship_to_location"]
    dept = f" ({req['department']})" if req["department"] else ""
    plural = "s" if len(lines) != 1 else ""
    summary = f"{requester}{dept} asks for {len(lines)} line{plural} worth {format_inr(total)} in all, for delivery at {where}."
    return {
        "no": req["no"],
        "requester": requester,
        "total": str(total),
        "total_inr": format_inr(total),
        "justification": req["justification"] or "",
        "rule": f"{req['rule_code']} v{req['rule_version']}",
        "lines": lines,
        "budget": budget.summary(budget.check_request(db, no, business_today())),
        "why": why,
        "summary": summary,
        "otp": otp.needed(db, total),
    }


def summary(db: Session, link: Link) -> dict[str, Any]:
    """What the page shows. Changes nothing."""
    _waiting(db, link)
    return facts(db, link.request_no, link.round_no, link.step, workflow.org_code_of(db, link.user_id))


def send_code(db: Session, link: Link) -> str:
    """Email the approver a one-time code for this step. Returns the address it went to."""
    _waiting(db, link)
    try:
        return CODES.issue(db, link.ref, link.user_id, subject=f"Your code to approve request {link.request_no}")
    except otp.CodeError as exc:
        raise LinkError(str(exc)) from None


def decide(db: Session, link: Link, action: str, reason: str | None, code: str | None = None) -> str:
    """Approve, reject or return for the person the link names. Everything the screen checks is checked here too, and an
    approval above the code limit needs the code first (it is used up only when the approval goes through)."""
    _waiting(db, link)
    if action not in ACTIONS:
        raise LinkError("the decision must be approve, reject or return")
    try:
        if action == "approve":
            total = db.execute(
                text("select total_value from request where no = :n"), {"n": link.request_no}
            ).scalar_one()
            if otp.needed(db, Decimal(total)):
                CODES.verify(db, link.ref, link.user_id, code)
            return workflow.approve(db, link.tenant, link.request_no, link.user_id, reason, via=VIA)
        if action == "reject":
            return workflow.reject(db, link.request_no, link.user_id, reason, tenant=link.tenant, via=VIA)
        return workflow.return_for_changes(db, link.request_no, link.user_id, reason, tenant=link.tenant, via=VIA)
    except (workflow.ApprovalError, permissions.PermissionDenied, otp.CodeError) as exc:
        db.rollback()
        raise LinkError(str(exc)) from None


def url(tenant: str, request_no: str, round_no: int, step: int, user_id: int) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/approve/{make(tenant, request_no, round_no, step, user_id)}"
