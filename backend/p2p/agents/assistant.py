"""The assistant: answers questions about requests, quotations, orders and stock, from the data.

It is an agent in the sense of the runtime (a model chooses tools, tools return facts, the model writes the answer) but
every tool here only reads. Nothing the assistant says can change anything, and a figure in its answer has to be one the
tools returned: a number that is not in any tool result is not trusted, and the answer is replaced by the plain facts
(rule 2: a model never computes a number a person relies on).
"""

import re
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents.decider import GatewayDecider
from p2p.agents.runtime import AgentRuntime
from p2p.agents.tools import Tool, ToolRegistry
from p2p.ai_steps.adapters import ModelError
from p2p.ai_steps.evidence import numbers_in
from p2p.approvals import workflow as approvals
from p2p.formulas.money import format_qty
from p2p.platform import audit, permissions
from p2p.platform.sod import Actor
from p2p.receiving.stock import stock_levels

SYSTEM = (
    "You are the procurement assistant of a company. Answer the user's question using only what the tools return. "
    "Call a tool to look things up; never answer from memory. Copy numbers, amounts and document numbers exactly as the "
    "tools give them. Never add up, subtract, average or estimate: if the user needs a total or a difference that the "
    "tools did not give, say that you cannot work it out and show the figures you have. If the tools find nothing, say so. "
    "Keep answers short and plain."
)
FALLBACK_INTRO = "I could not check part of my answer against the data, so here is what I found instead:"
UNAVAILABLE = "I could not get an answer from the language model just now. The pages of the application show the same information."
MAX_ROWS = 15
MAX_STEPS = 6


class NoParams(BaseModel):
    pass


class StatusFilter(BaseModel):
    status: str | None = Field(
        default=None, description="draft, pending, approved, rejected, returned or closed; leave empty for all"
    )


class ByNumber(BaseModel):
    no: str = Field(
        description="the document number, for example REQ-VAD-2627-00001, RFQ-2627-00001 or PO-BLR-2627-00001"
    )


class ItemQuery(BaseModel):
    query: str = Field(description="part of an item name or code")


def _rows(db: Session, sql: str, **params: Any) -> list[dict[str, Any]]:
    return [dict(r._mapping) for r in db.execute(text(sql), params)]


def _s(value: Any) -> Any:
    """JSON-safe, keeping money and quantities exact as text."""
    if isinstance(value, Decimal):
        return format_qty(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _clean(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{k: _s(v) for k, v in r.items()} for r in rows]


def build_registry(db: Session, user_id: int) -> ToolRegistry:
    """The read-only tools, for one signed-in person."""
    registry = ToolRegistry()

    def my_pending_work(db: Session, p: NoParams, actor: Actor) -> dict[str, Any]:
        me = approvals.org_code_of(db, user_id)
        out: dict[str, Any] = {"person": me or "not linked to a person in the organisation"}
        if me:
            out["requests_waiting_for_my_approval"] = _clean(
                _rows(
                    db,
                    "select r.no, r.total_value as amount, r.justification as title from approval_step s join request r "
                    "on r.no = s.request_no and r.approval_round = s.round where s.status = 'pending' "
                    "and (s.approver = :me or s.delegate = :me) and r.status = 'submitted' order by r.no limit :n",
                    me=me,
                    n=MAX_ROWS,
                )
            )
            out["orders_waiting_for_my_approval"] = _clean(
                _rows(
                    db,
                    "select p.no, p.total as amount, p.supplier_code from po_approval_step s join po p "
                    "on p.no = s.po_no and p.approval_round = s.round where s.status = 'pending' "
                    "and (s.approver = :me or s.delegate = :me) and p.status = 'submitted' order by p.no limit :n",
                    me=me,
                    n=MAX_ROWS,
                )
            )
        if permissions.can_anywhere(db, user_id, "rfq.create"):
            out["quotation_requests_with_quotes_to_award"] = _clean(
                _rows(
                    db,
                    "select r.no, count(distinct q.supplier_code) as quotes from rfq r join quote q on q.rfq_no = r.no "
                    "where r.status = 'sent' group by r.no order by r.no limit :n",
                    n=MAX_ROWS,
                )
            )
            out["supplier_replies_to_review"] = db.execute(
                text("select count(*) from quote_draft where status = 'pending'")
            ).scalar_one()
        return out

    def find_requests(db: Session, p: StatusFilter, actor: Actor) -> dict[str, Any]:
        wanted = {"pending": "submitted"}.get((p.status or "").lower(), (p.status or "").lower() or None)
        rows = _clean(
            _rows(
                db,
                "select no, status, total_value as amount, priority, justification as title from request "
                "where (cast(:s as text) is null or status = cast(:s as text)) order by created_at desc, no desc limit :n",
                s=wanted,
                n=MAX_ROWS,
            )
        )
        total = db.execute(
            text("select count(*) from request where (cast(:s as text) is null or status = cast(:s as text))"),
            {"s": wanted},
        ).scalar_one()
        return {"count_matching": total, "shown": len(rows), "requests": rows}

    def get_request(db: Session, p: ByNumber, actor: Actor) -> dict[str, Any]:
        head = _rows(
            db,
            "select no, status, total_value as amount, priority, ship_to_location, rule_code, decision_reason from request where no = :n",
            n=p.no,
        )
        if not head:
            return {"error": f"no request called {p.no}"}
        lines = _clean(
            _rows(
                db,
                "select rl.line_no, coalesce(i.name, rl.free_text) as item, rl.qty, rl.uom, rl.est_price, rl.status, d.method as sourced_by, "
                "d.rfq_no, d.contract_code from request_line rl left join item i on i.code = rl.item_code "
                "left join sourcing_decision d on d.request_no = rl.request_no and d.line_no = rl.line_no "
                "where rl.request_no = :n order by rl.line_no limit :m",
                n=p.no,
                m=MAX_ROWS,
            )
        )
        orders = _clean(_rows(db, "select no, status, total from po where request_no = :n order by no", n=p.no))
        return {**_clean(head)[0], "lines": lines, "orders": orders}

    def get_rfq(db: Session, p: ByNumber, actor: Actor) -> dict[str, Any]:
        head = _rows(db, "select no, status, request_no, due_date from rfq where no = :n", n=p.no)
        if not head:
            return {"error": f"no quotation request called {p.no}"}
        suppliers = _clean(
            _rows(
                db,
                "select s.supplier_code, coalesce(v.trade_name, v.legal_name) as name, s.invited_at is not null as invited, "
                "s.responded_at is not null as replied, s.declined from rfq_supplier s join supplier v on v.code = s.supplier_code "
                "where s.rfq_no = :n order by s.supplier_code",
                n=p.no,
            )
        )
        offers = _clean(
            _rows(
                db,
                "select l.description as item, q.supplier_code, ql.unit_price, ql.discount_pct, ql.gst_rate, ql.delivery_days "
                "from quote_line ql join quote q on q.no = ql.quote_no join rfq_line l on l.rfq_no = q.rfq_no and l.line_no = ql.rfq_line_no "
                "where q.rfq_no = :n and q.revision = (select max(revision) from quote x where x.rfq_no = q.rfq_no "
                "and x.supplier_code = q.supplier_code) order by l.line_no, ql.unit_price limit :m",
                n=p.no,
                m=40,
            )
        )
        awards = _clean(
            _rows(
                db, "select rfq_line_no, supplier_code, price from award where rfq_no = :n order by rfq_line_no", n=p.no
            )
        )
        return {**_clean(head)[0], "suppliers": suppliers, "quotes": offers, "awards": awards}

    def list_orders(db: Session, p: StatusFilter, actor: Actor) -> dict[str, Any]:
        wanted = {"pending": "submitted"}.get((p.status or "").lower(), (p.status or "").lower() or None)
        rows = _clean(
            _rows(
                db,
                "select no, status, supplier_code, total, request_no from po where (cast(:s as text) is null or status = cast(:s as text)) "
                "order by created_at desc, no desc limit :n",
                s=wanted,
                n=MAX_ROWS,
            )
        )
        total = db.execute(
            text("select count(*) from po where (cast(:s as text) is null or status = cast(:s as text))"), {"s": wanted}
        ).scalar_one()
        return {"count_matching": total, "shown": len(rows), "orders": rows}

    def get_order(db: Session, p: ByNumber, actor: Actor) -> dict[str, Any]:
        head = _rows(
            db,
            "select p.no, p.status, p.supplier_code, coalesce(s.trade_name, s.legal_name) as supplier, p.taxable, p.cgst, p.sgst, p.igst, "
            "p.total, p.request_no, p.rfq_no, p.contract_code, p.hold_reason, p.decision_reason from po p "
            "join supplier s on s.code = p.supplier_code where p.no = :n",
            n=p.no,
        )
        if not head:
            return {"error": f"no purchase order called {p.no}"}
        lines = _clean(
            _rows(
                db,
                "select line_no, description as item, qty, uom, unit_price, gst_rate, taxable from po_line where po_no = :n order by line_no limit :m",
                n=p.no,
                m=MAX_ROWS,
            )
        )
        tax = head[0]["cgst"] + head[0]["sgst"] + head[0]["igst"]  # worked out here so the model never has to add
        return {**_clean(head)[0], "total_tax": format_qty(tax), "lines": lines}

    def stock_status(db: Session, p: ItemQuery, actor: Actor) -> dict[str, Any]:
        like = f"%{p.query.strip()}%"
        codes = [
            r[0]
            for r in db.execute(
                text("select code from item where name ilike :q or code ilike :q order by code limit 8"), {"q": like}
            )
        ]
        found = []
        for code in codes:
            for line in stock_levels(db, item=code):
                found.append(
                    {
                        "item": line.item_name,
                        "code": line.item_code,
                        "location": line.location_code,
                        "on_hand": format_qty(line.on_hand),
                        "on_order": format_qty(line.on_order),
                        "reorder_point": format_qty(line.reorder_point) if line.reorder_point is not None else None,
                        "status": line.status,
                        "uom": line.uom,
                    }
                )
        return (
            {"count": len(found), "stock": found[:MAX_ROWS]}
            if found
            else {"error": f"no stocked item matches '{p.query}'"}
        )

    for name, description, params, fn in (
        (
            "my_pending_work",
            "What is waiting for the person asking: requests and orders to approve, quotes to award, supplier replies to review",
            NoParams,
            my_pending_work,
        ),
        (
            "find_requests",
            "List purchase requests, newest first, optionally only those with a given status",
            StatusFilter,
            find_requests,
        ),
        (
            "get_request",
            "Everything about one purchase request: its status, lines, how each line is sourced, and its orders",
            ByNumber,
            get_request,
        ),
        (
            "get_rfq",
            "One quotation request: the suppliers asked, who replied, the quotes received and any award",
            ByNumber,
            get_rfq,
        ),
        (
            "list_orders",
            "List purchase orders, newest first, optionally only those with a given status",
            StatusFilter,
            list_orders,
        ),
        ("get_order", "One purchase order with its supplier, lines, GST split and total", ByNumber, get_order),
        (
            "stock_status",
            "Stock on hand, on order and its status for items whose name or code contains the text",
            ItemQuery,
            stock_status,
        ),
    ):
        registry.register(Tool(name, description, params, fn))
    return registry


# ---- the answer, checked ----------------------------------------------------------------------

_DATEISH = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{4}\b")


def ungrounded_numbers(answer: str, tool_text: str) -> list[Decimal]:
    """Numbers in the answer that no tool result contains. Small numbers (under 10, such as 'two suppliers') and years pass."""
    known = numbers_in(tool_text)
    cleaned = _DATEISH.sub(" ", answer)
    bad = []
    for n in sorted(numbers_in(cleaned)):
        if n < 10 or Decimal(2000) <= n <= Decimal(2100) or n in known:
            continue
        bad.append(n)
    return bad


def _render(value: Any, indent: int = 0) -> list[str]:
    pad = "  " * indent
    if isinstance(value, dict):
        out: list[str] = []
        for key, item in value.items():
            label = str(key).replace("_", " ")
            if isinstance(item, (dict, list)):
                out.append(f"{pad}{label}:")
                out.extend(_render(item, indent + 1))
            elif item is not None:
                out.append(f"{pad}{label}: {item}")
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, dict):
                out.append(
                    pad + "- " + ", ".join(f"{str(k).replace('_', ' ')} {v}" for k, v in item.items() if v is not None)
                )
            else:
                out.append(f"{pad}- {item}")
        return out
    return [f"{pad}{value}"]


def _plain_facts(tool_results: list[tuple[str, Any]]) -> str:
    """What the tools returned, as plain lines a person can read."""
    lines: list[str] = []
    for _, result in tool_results:
        lines.extend(_render(result))
    return "\n".join(lines)[:2500]


@dataclass
class Answer:
    text: str
    grounded: bool
    run_id: int | None
    tools_used: list[str]


def ask(db: Session, tenant: str, user_id: int, question: str, gateway: Any) -> Answer:
    """Answer one question. Never raises for a model problem: the person gets a plain message instead."""
    registry = build_registry(db, user_id)
    runtime = AgentRuntime(
        tenant,
        db,
        registry,
        GatewayDecider(gateway, "agent", db, max_wait=20.0),
        agent="assistant",
        max_steps=MAX_STEPS,
        system=SYSTEM,
    )
    try:
        outcome = runtime.start(question.strip()[:2000], thread_id=f"assistant-{uuid.uuid4().hex}")
    except ModelError:
        db.rollback()
        return Answer(UNAVAILABLE, True, None, [])
    steps = _tool_steps(db, outcome.run_id)
    used = [name for name, _ in steps]
    answer = (outcome.result or "").strip()
    if outcome.status == "failed":  # the model could not be reached, or kept giving unusable replies
        return Answer(UNAVAILABLE, True, outcome.run_id, used)
    if outcome.status != "done" or not answer:
        return Answer(
            "I could not work that out. Try asking about a request, quotation, order or item by name or number.",
            True,
            outcome.run_id,
            used,
        )
    facts = _plain_facts(steps)
    bad = ungrounded_numbers(answer, facts)
    if bad:
        audit.record(
            db,
            Actor("agent", "assistant"),
            "assistant.ungrounded",
            "agent_run",
            outcome.run_id,
            after={"numbers": [str(n) for n in bad], "answer": answer[:500]},
        )
        db.commit()
        return Answer(f"{FALLBACK_INTRO}\n{facts}" if facts else UNAVAILABLE, False, outcome.run_id, used)
    return Answer(answer, True, outcome.run_id, used)


def _tool_steps(db: Session, run_id: int) -> list[tuple[str, Any]]:
    out: list[tuple[str, Any]] = []
    for name, detail in db.execute(
        text("select name, detail from agent_step where run_id = :r and kind = 'tool' order by id"), {"r": run_id}
    ):
        result = (detail or {}).get("result") if isinstance(detail, dict) else None
        out.append((str(name), result))
    return out
