"""Approving from an email: a signed link that works once.

The email to an approver carries a link to a page that shows the request and asks for the decision. The link is signed
(it cannot be made up or altered), expires, and names one person at one step of one request. Opening it changes nothing,
so a mail scanner that fetches links does no harm; the decision is a separate button. It works once because the step stops
waiting after the decision, and a link for a step that is no longer waiting for that person is refused. The decision is the
same as on the screen: same turn, same role, same separation of duties, and the audit row says it came by an emailed link.
Every decision from a link (approve, reject, return) needs a one-time code emailed to the login the link names
(platform/otp.py), whatever the amount, so a forwarded link decides nothing (D-07). Instead of a code the approver can
open the request in the app and sign in. Rejecting or returning also needs a reason.
"""

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
from p2p.platform.signing import SignatureError, Signer

EXPIRES_DAYS = 7
VIA = "emailed link"
CODES = otp.OneTimeCodes("approval")
ACTIONS = ("approve", "reject", "return")
SIGNER = Signer("approval-link")


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


def make(tenant: str, request_no: str, round_no: int, step: int, user_id: int, *, now: float | None = None) -> str:
    expires = int((time.time() if now is None else now) + EXPIRES_DAYS * 86400)
    try:
        return SIGNER.sign([tenant, request_no, round_no, step, user_id, expires])
    except SignatureError as exc:
        raise LinkError(str(exc)) from None


def read(token: str, *, now: float | None = None) -> Link:
    """The link a token stands for. Raises LinkError for anything altered, malformed or expired."""
    try:
        tenant, no, round_no, step, user_id, expires = SIGNER.read(token)
    except SignatureError as exc:
        raise LinkError(str(exc)) from None
    except ValueError:
        raise LinkError("this link is not valid") from None
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
        "otp": True,  # every decision from an email link needs a code (D-07)
        "app_url": app_url(req["no"]),
    }


def summary(db: Session, link: Link) -> dict[str, Any]:
    """What the page shows. Changes nothing."""
    _waiting(db, link)
    return facts(db, link.request_no, link.round_no, link.step, workflow.org_code_of(db, link.user_id))


def send_code(db: Session, link: Link) -> str:
    """Email the approver a one-time code for this step. Returns the address it went to."""
    _waiting(db, link)
    try:
        return CODES.issue(db, link.ref, link.user_id, subject=f"Your code to decide request {link.request_no}")
    except otp.CodeError as exc:
        raise LinkError(str(exc)) from None


def decide(db: Session, link: Link, action: str, reason: str | None, code: str | None = None) -> str:
    """Approve, reject or return for the person the link names. Everything the screen checks is checked here too, and
    every decision needs the person's one-time code first (D-07; it is used up only when the decision goes through)."""
    _waiting(db, link)
    if action not in ACTIONS:
        raise LinkError("the decision must be approve, reject or return")
    if not (code or "").strip():
        raise LinkError("this needs a one-time code: ask for one (a code works once)")
    try:
        CODES.verify(db, link.ref, link.user_id, code)
        if action == "approve":
            return workflow.approve(db, link.tenant, link.request_no, link.user_id, reason, via=VIA)
        if action == "reject":
            return workflow.reject(db, link.request_no, link.user_id, reason, tenant=link.tenant, via=VIA)
        return workflow.return_for_changes(db, link.request_no, link.user_id, reason, tenant=link.tenant, via=VIA)
    except (workflow.ApprovalError, permissions.PermissionDenied, otp.CodeError) as exc:
        db.rollback()
        raise LinkError(str(exc)) from None


def app_url(request_no: str) -> str:
    """The request in the app, where the approver signs in and decides without a code."""
    return f"{get_settings().public_base_url.rstrip('/')}/#/requisitions/{request_no}"


def url(tenant: str, request_no: str, round_no: int, step: int, user_id: int) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/approve/{make(tenant, request_no, round_no, step, user_id)}"
