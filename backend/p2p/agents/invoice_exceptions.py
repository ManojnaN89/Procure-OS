"""The invoice exceptions agent (AG03): why is this invoice blocked, and what should be done about it.

Two layers, so that the answer is both useful and safe:

* `diagnose` works the cause out from the data with plain rules: the match result, the goods received and rejected, what
  other invoices already took, the supplier's registration. It also drafts the vendor's query email from those facts.
  It is always available and always right about the numbers (rule 2).
* `explain` lets a model investigate with read-only tools and write the explanation in plain words. The model's
  explanation is used only when it names the same cause the rules found and every number in it came from a tool; if not,
  the rules' explanation is shown instead.

Nothing here changes an invoice. A payment block stays until a person releases it (rule 5); this only advises.
"""

import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.agents.assistant import _clean, _plain_facts, _tool_steps, ungrounded_numbers
from p2p.agents.decider import GatewayDecider
from p2p.agents.runtime import AgentRuntime
from p2p.agents.tools import Tool, ToolRegistry
from p2p.ai_steps.adapters import ModelError
from p2p.formulas.money import format_qty
from p2p.platform import audit
from p2p.platform.rows import rows as _rows
from p2p.platform.sod import Actor

AGENT = Actor("agent", "invoice_exceptions")
MAX_STEPS = 7

# the causes, named as the dataset names the problems it plants
CAUSES = (
    "price_mismatch", "qty_mismatch", "rejected_qty_invoiced", "goods_not_received", "wrong_gstin",
    "composition_tax", "e_invoice_missing", "arithmetic",
)  # fmt: skip
NEXT_ACTION = {
    "credit_note": "Ask the supplier for a credit note",
    "corrected_invoice": "Ask the supplier for a corrected invoice",
    "wait_for_receipt": "Wait for the goods receipt, then match again",
    "request_release": "Ask for a release",
}  # what a person does next, in the words the Invoices screen uses
BY_REASON = {
    "tax_not_allowed": "composition_tax", "irn_missing": "e_invoice_missing", "gstin_mismatch": "wrong_gstin",
    "arithmetic_mismatch": "arithmetic", "price_variance": "price_mismatch",
}  # fmt: skip

SYSTEM = (
    "You are an accounts assistant who investigates why a supplier invoice is blocked from payment. Use the tools to look "
    "at the invoice, its match results, the purchase order, the goods receipts, the other invoices on the same order and "
    "the supplier's recent emails. Then write the explanation for a person in accounts: two to four plain sentences saying "
    "what is wrong and why, naming the order and the receipts involved. Copy every number and document number exactly as "
    "the tools give them; never add up, subtract or estimate. Do not say the invoice is released or approved: only a person "
    "can do that. End with one last line in exactly this form: 'Cause: <one of {causes}>'."
).format(causes=", ".join(CAUSES))


class InvoiceNo(BaseModel):
    no: str = Field(description="our invoice number, for example PI-2627-000011")


class OrderNo(BaseModel):
    no: str = Field(description="the purchase order number, for example PO-BLR-2627-00001")


class SupplierCode(BaseModel):
    code: str = Field(description="the supplier code")


CAPABILITY = "invoice_ap"


@dataclass
class Diagnosis:
    invoice_no: str
    cause: str
    fix: str
    explanation: str
    vendor_subject: str
    vendor_body: str
    source: str = "rules"  # agent when the model's explanation was used
    run_id: int | None = None
    grounded: bool = True
    facts: dict[str, Any] = field(default_factory=dict)


# ---- the facts --------------------------------------------------------------------------------


def gather(db: Session, invoice_no: str) -> dict[str, Any] | None:
    """Everything the diagnosis rests on, read from the data. None if there is no such invoice."""
    head = _rows(
        db,
        "select i.*, coalesce(s.trade_name, s.legal_name) as supplier, s.gst_reg_type, s.e_invoice_applicable, s.gstin as master_gstin "
        "from invoice i join supplier s on s.code = i.supplier_code where i.no = :n",
        n=invoice_no,
    )
    if not head:
        return None
    inv = head[0]
    results = _rows(
        db, "select * from match_result where invoice_no = :n and resolved_at is null order by id", n=invoice_no
    )
    lines = []
    for ln in _rows(db, "select * from invoice_line where invoice_no = :n order by line_no", n=invoice_no):
        po = _rows(db, "select * from po_line where po_no = :p and line_no = :l", p=ln["po_no"], l=ln["po_line_no"])[0]
        got = _rows(
            db,
            "select coalesce(sum(qty_received), 0) as gross, coalesce(sum(qty_accepted), 0) as accepted, "
            "coalesce(sum(qty_rejected), 0) as rejected, count(*) as receipts from gr_line where po_no = :p and po_line_no = :l",
            p=ln["po_no"], l=ln["po_line_no"],
        )[0]  # fmt: skip
        others = _rows(
            db,
            "select coalesce(sum(l.qty), 0) as qty from invoice_line l join invoice i on i.no = l.invoice_no "
            "where l.po_no = :p and l.po_line_no = :l and i.no <> :n and i.type = 'invoice' "
            "and i.status in ('approved', 'part_paid', 'paid')",
            p=ln["po_no"], l=ln["po_line_no"], n=invoice_no,
        )[0]["qty"]  # fmt: skip
        match = next((r for r in results if r["invoice_line_no"] == ln["line_no"] and r["result"] == "blocked"), None)
        lines.append(
            {
                "line_no": ln["line_no"], "po_no": ln["po_no"], "po_line_no": ln["po_line_no"], "item": ln["description"],
                "uom": ln["uom"], "invoice_qty": Decimal(ln["qty"]), "invoice_price": Decimal(ln["unit_price"]),
                "order_qty": Decimal(po["qty"]), "order_price": Decimal(po["unit_price"]),
                "received_gross": Decimal(got["gross"]), "accepted": Decimal(got["accepted"]),
                "rejected": Decimal(got["rejected"]), "receipts": int(got["receipts"]),
                "invoiced_by_others": Decimal(others), "result": match["reason_codes"] if match else None,
                "qty_var": Decimal(match["qty_var"] or 0) if match else Decimal(0),
                "price_var": Decimal(match["price_var"] or 0) if match else Decimal(0),
            }
        )  # fmt: skip
    order = (
        _rows(db, "select no, buyer_gstin, supplier_gstin, status from po where no = :p", p=lines[0]["po_no"])
        if lines
        else []
    )
    contact = _rows(
        db,
        "select name, email from supplier_contact where supplier_code = :c and email is not null "
        "order by is_primary desc nulls last, id limit 1",
        c=inv["supplier_code"],
    )
    return {
        "invoice": inv, "results": results, "lines": lines, "order": order[0] if order else {},
        "contact": contact[0] if contact else {},
    }  # fmt: skip


# ---- the rules --------------------------------------------------------------------------------


def _header_reason(facts: dict[str, Any]) -> str | None:
    for r in facts["results"]:
        if r["invoice_line_no"] is None and r["reason_codes"]:
            return str(r["reason_codes"])
    return None


def _line_cause(ln: dict[str, Any]) -> str:
    if ln["result"] == "price_variance":
        return "price_mismatch"
    if ln["receipts"] == 0:
        return "goods_not_received"
    overage = ln["qty_var"]
    if ln["rejected"] > 0 and ln["rejected"] >= overage:
        return "rejected_qty_invoiced"
    return "qty_mismatch"


def classify(facts: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """The cause, and the invoice line it is about (None for a problem with the invoice as a whole)."""
    header = _header_reason(facts)
    if header and header in BY_REASON:
        return BY_REASON[header], None
    for ln in facts["lines"]:
        if ln["result"] in ("price_variance", "qty_over_received"):
            return _line_cause(ln), ln
    return "arithmetic", None


def _fix(cause: str, ln: dict[str, Any] | None) -> str:
    if cause == "rejected_qty_invoiced":
        return "credit_note"
    if cause == "goods_not_received":
        return "wait_for_receipt"
    if cause == "qty_mismatch" and ln is not None and ln["invoice_qty"] <= ln["order_qty"] - ln["invoiced_by_others"]:
        # the quantity billed is still within what was ordered and not yet invoiced: the rest of the goods may simply not have come yet
        return "wait_for_receipt"
    return "corrected_invoice"


def _n(value: Decimal) -> str:
    return format_qty(value)


def diagnose(db: Session, invoice_no: str) -> Diagnosis | None:
    """Work the cause out from the data. Returns None for an invoice that is not blocked."""
    facts = gather(db, invoice_no)
    if facts is None or facts["invoice"]["status"] != "blocked":
        return None
    inv = facts["invoice"]
    cause, ln = classify(facts)
    fix = _fix(cause, ln)
    ref = f"your invoice {inv['supplier_invoice_no']} dated {inv['invoice_date']:%d %b %Y} (our reference {inv['no']})"
    where = f"order {ln['po_no']} line {ln['po_line_no']} ({ln['item']})" if ln else "the order"
    if cause == "price_mismatch" and ln:
        why = (
            f"The invoice charges {_n(ln['invoice_price'])} per {ln['uom']} for {ln['item']} on {where}, but the order price is "
            f"{_n(ln['order_price'])}: {_n(ln['price_var'])} more per unit, outside the tolerance."
        )
        ask = (
            f"it charges {_n(ln['invoice_price'])} per {ln['uom']} for {ln['item']}, while order {ln['po_no']} says "
            f"{_n(ln['order_price'])}. Please send a corrected invoice at the order price, or a credit note for the difference."
        )
    elif cause == "rejected_qty_invoiced" and ln:
        why = (
            f"The invoice bills {_n(ln['invoice_qty'])} {ln['uom']} of {ln['item']} on {where}. Goods receipts show "
            f"{_n(ln['received_gross'])} received, of which {_n(ln['rejected'])} were rejected, so only {_n(ln['accepted'])} were accepted."
        )
        ask = (
            f"it bills {_n(ln['invoice_qty'])} {ln['uom']} of {ln['item']} for order {ln['po_no']}, but {_n(ln['rejected'])} "
            f"were rejected at goods receipt and only {_n(ln['accepted'])} accepted. Please send a credit note for the rejected quantity."
        )
    elif cause == "goods_not_received" and ln:
        why = f"The invoice bills {_n(ln['invoice_qty'])} {ln['uom']} of {ln['item']} on {where}, but no goods receipt has been recorded for it."
        ask = (
            f"it bills {_n(ln['invoice_qty'])} {ln['uom']} of {ln['item']} for order {ln['po_no']}, but we have no receipt of those goods yet. "
            "Please confirm the dispatch and delivery details."
        )
    elif cause == "qty_mismatch" and ln:
        why = (
            f"The invoice bills {_n(ln['invoice_qty'])} {ln['uom']} of {ln['item']} on {where}, but only {_n(ln['accepted'])} have been "
            f"received and accepted ({_n(ln['qty_var'])} more than can be invoiced)."
        )
        ask = (
            f"it bills {_n(ln['invoice_qty'])} {ln['uom']} of {ln['item']} for order {ln['po_no']}, but our goods receipts show "
            f"{_n(ln['accepted'])} accepted. Please send a corrected invoice for the quantity delivered, or tell us when the rest will arrive."
        )
    elif cause == "wrong_gstin":
        mine = facts["order"].get("buyer_gstin")
        which = (
            f"our GSTIN on the invoice is {inv['buyer_gstin']} but the order was placed with {mine}"
            if mine and inv["buyer_gstin"] != mine
            else f"the supplier GSTIN on the invoice is {inv['supplier_gstin']}, which is not one registered for the supplier"
        )
        why = f"The GSTIN on the invoice does not match: {which}."
        ask = f"the GSTIN is wrong: {which}. Please send a corrected invoice with the right GSTIN."
    elif cause == "composition_tax":
        why = "The supplier is registered under the composition scheme (or not registered for GST), so it cannot charge GST, yet the invoice charges tax."
        ask = "you are registered under the composition scheme, so GST cannot be charged. Please send a bill of supply without GST."
    elif cause == "e_invoice_missing":
        why = "The supplier must issue e-invoices, but this invoice carries no IRN (invoice reference number)."
        ask = "it must be an e-invoice but carries no IRN. Please send the e-invoice with its IRN and QR code."
    else:
        detail = next(
            (r["detail"] for r in facts["results"] if r["invoice_line_no"] is None and r["detail"]),
            "the amounts do not add up",
        )
        why = f"The invoice does not add up: {detail}."
        ask = f"the amounts do not add up ({detail}). Please check and send a corrected invoice."
    contact = facts["contact"].get("name") or "Sir or Madam"
    body = f"Dear {contact},\n\nWe cannot pay {ref} yet: {ask}\n\nRegards,\nAccounts\n"
    return Diagnosis(
        invoice_no, cause, fix, why, f"Query on invoice {inv['supplier_invoice_no']}", body, facts={"cause": cause, "line": ln},
    )  # fmt: skip


# ---- the read-only tools ----------------------------------------------------------------------


def build_registry(db: Session) -> ToolRegistry:
    registry = ToolRegistry()

    def get_invoice(db: Session, p: InvoiceNo, actor: Actor) -> dict[str, Any]:
        facts = gather(db, p.no)
        if facts is None:
            return {"error": f"no invoice called {p.no}"}
        inv = facts["invoice"]
        return {
            **_clean([{k: inv[k] for k in ("no", "type", "supplier", "supplier_invoice_no", "invoice_date", "status", "payment_block", "block_reason", "total", "supplier_gstin", "buyer_gstin", "irn")}])[0],
            "lines": _clean([
                {"line_no": x["line_no"], "order": x["po_no"], "order_line": x["po_line_no"], "item": x["item"], "invoice_qty": x["invoice_qty"], "invoice_price": x["invoice_price"]}
                for x in facts["lines"]
            ]),
            "match_results": _clean([
                {"line": r["invoice_line_no"], "result": r["result"], "reason": r["reason_codes"], "price_difference_per_unit": r["price_var"], "quantity_over": r["qty_var"], "detail": r["detail"]}
                for r in facts["results"]
            ]),
        }  # fmt: skip

    def get_order(db: Session, p: OrderNo, actor: Actor) -> dict[str, Any]:
        head = _rows(db, "select no, status, supplier_code, buyer_gstin, supplier_gstin from po where no = :n", n=p.no)
        if not head:
            return {"error": f"no purchase order called {p.no}"}
        lines = _rows(
            db,
            "select line_no, description as item, qty as ordered, uom, unit_price as order_price, received_qty as accepted_so_far, "
            "invoiced_qty as invoiced_so_far, status from po_line where po_no = :n order by line_no limit 20",
            n=p.no,
        )
        return {**_clean(head)[0], "lines": _clean(lines)}

    def get_receipts(db: Session, p: OrderNo, actor: Actor) -> dict[str, Any]:
        rows = _rows(
            db,
            "select g.no as receipt, g.gate_entry_at as received_on, g.inspection_status, l.po_line_no as order_line, "
            "l.qty_received as received, l.qty_accepted as accepted, l.qty_rejected as rejected, l.reject_reason "
            "from goods_receipt g join gr_line l on l.gr_no = g.no where g.po_no = :n order by g.no, l.po_line_no limit 30",
            n=p.no,
        )
        return (
            {"receipts": _clean(rows)}
            if rows
            else {"receipts": [], "note": f"no goods have been received against {p.no}"}
        )

    def invoices_on_order(db: Session, p: OrderNo, actor: Actor) -> dict[str, Any]:
        rows = _rows(
            db,
            "select i.no as invoice, i.supplier_invoice_no, i.type, i.status, l.po_line_no as order_line, l.qty "
            "from invoice i join invoice_line l on l.invoice_no = i.no where l.po_no = :n order by i.no, l.po_line_no limit 30",
            n=p.no,
        )
        return {"invoices": _clean(rows)}

    def supplier_emails(db: Session, p: SupplierCode, actor: Actor) -> dict[str, Any]:
        rows = _rows(
            db,
            "select m.direction, m.subject, m.sent_at, left(coalesce(m.body_text, ''), 300) as start from email_message m "
            "where exists (select 1 from supplier_contact c where c.supplier_code = :c and c.email is not null "
            "and (lower(m.from_addr) like '%' || lower(c.email) || '%' or lower(m.to_addr) like '%' || lower(c.email) || '%')) "
            "order by m.sent_at desc nulls last limit 5",
            c=p.code,
        )
        return {"emails": _clean(rows)} if rows else {"emails": [], "note": "no emails with this supplier are on file"}

    def supplier_profile(db: Session, p: SupplierCode, actor: Actor) -> dict[str, Any]:
        head = _rows(
            db,
            "select code, coalesce(trade_name, legal_name) as name, gst_reg_type, e_invoice_applicable, gstin from supplier where code = :c",
            c=p.code,
        )
        if not head:
            return {"error": f"no supplier called {p.code}"}
        gstins = [
            r["gstin"]
            for r in _rows(
                db, "select gstin from supplier_address where supplier_code = :c and gstin is not null", c=p.code
            )
        ]
        return {**_clean(head)[0], "registered_gstins": gstins}

    for name, description, params, fn in (
        (
            "get_invoice",
            "The blocked invoice: its lines, status, block reason and what the match found per line",
            InvoiceNo,
            get_invoice,
        ),
        (
            "get_order",
            "A purchase order with its lines: ordered, price, accepted so far and invoiced so far",
            OrderNo,
            get_order,
        ),
        (
            "get_receipts",
            "The goods receipts of an order: received, accepted and rejected per line",
            OrderNo,
            get_receipts,
        ),
        (
            "invoices_on_order",
            "All invoices and credit notes entered against an order, with their status",
            OrderNo,
            invoices_on_order,
        ),
        ("supplier_emails", "The latest emails with a supplier", SupplierCode, supplier_emails),
        (
            "supplier_profile",
            "A supplier's GST registration type, e-invoice duty and registered GSTINs",
            SupplierCode,
            supplier_profile,
        ),
    ):
        registry.register(Tool(name, description, params, fn))
    return registry


# ---- the agent --------------------------------------------------------------------------------

# The last line names one cause. Models dress it in markdown or end it with a full stop, so those are tolerated.
_CAUSE_LINE = re.compile(r"^[\W_]*cause[\W_]{0,8}(" + "|".join(CAUSES) + r")[\W_]*$", re.I | re.M)
# A claim that something was done: only a person releases or approves, so text that says it happened is not used.
_CLAIM = re.compile(
    r"\b(?:was|were|been|now|already|has|have|i|i've)\s+(?:\w+\s+)?(?:released|unblocked|cleared|paid|approved)\b", re.I
)
_LINK = re.compile(r"https?://|www\.|[\w.+-]+@[\w-]+\.[a-z]{2,}", re.I)
# What a supplier wrote is untrusted: a number or a link that only appears in an email is not a fact about the invoice.
UNTRUSTED_TOOLS = ("supplier_emails",)


def explain(db: Session, tenant: str, user_id: int, invoice_no: str, gateway: Any) -> Diagnosis | None:
    """Investigate a blocked invoice. The rules' diagnosis is the floor: the model's wording replaces it only if checked."""
    from p2p.agents import capabilities  # noqa: PLC0415 - the switch imports the agents

    base = diagnose(db, invoice_no)
    if base is None:
        return None
    if not capabilities.is_on(db, CAPABILITY):  # switched off: the rules' diagnosis only, no model
        _store(db, base, user_id)
        return base
    runtime = AgentRuntime(
        tenant, db, build_registry(db), GatewayDecider(gateway, "agent_invoice_exceptions", db, max_wait=30.0),
        agent="invoice_exceptions", max_steps=MAX_STEPS, system=SYSTEM, capability=CAPABILITY, user_id=user_id,
        started_by=f"user:{user_id}",
    )  # fmt: skip
    answer = ""
    run_id: int | None = None
    steps: list[tuple[str, Any]] = []
    try:
        outcome = runtime.start(
            f"Why is invoice {invoice_no} blocked, and what should we do? "
            f"Your answer must end with a last line in exactly this form: 'Cause: <one of {', '.join(CAUSES)}>'.",
            thread_id=f"invoice-exc-{invoice_no}-{uuid.uuid4().hex}",
        )
        run_id = outcome.run_id
        if outcome.status == "done":
            answer = (outcome.result or "").strip()
            steps = _tool_steps(db, outcome.run_id)
    except ModelError:
        db.rollback()
    if answer:
        said = next(iter(reversed(list(_CAUSE_LINE.finditer(answer)))), None)
        body = _CAUSE_LINE.sub("", answer).strip()
        facts_text = _plain_facts([s for s in steps if s[0] not in UNTRUSTED_TOOLS])
        bad = ungrounded_numbers(body, facts_text)
        links = [m for m in _LINK.findall(body) if m.lower() not in facts_text.lower()]
        claims = [m.group(0) for m in _CLAIM.finditer(body)]
        if said and said.group(1).lower() == base.cause and body and not bad and not links and not claims:
            base.explanation, base.source, base.run_id = body, "agent", run_id
        else:
            base.run_id = run_id
            base.grounded = not bad and not links and not claims
            audit.record(
                db, AGENT, "invoice.explain_rejected", "invoice", invoice_no,
                after={"said": said.group(1) if said else None, "rules": base.cause, "ungrounded": [str(n) for n in bad], "links": links, "claims": claims, "tail": answer[-120:]},
            )  # fmt: skip
    _store(db, base, user_id)
    return base


def _store(db: Session, d: Diagnosis, user_id: int) -> None:
    db.execute(
        text(
            "insert into invoice_diagnosis (invoice_no, cause, fix, explanation, vendor_subject, vendor_body, source, run_id, created_by) "
            "values (:i, :c, :f, :e, :s, :b, :src, :r, :u)"
        ),
        {"i": d.invoice_no, "c": d.cause, "f": d.fix, "e": d.explanation, "s": d.vendor_subject, "b": d.vendor_body, "src": d.source, "r": d.run_id, "u": user_id},
    )  # fmt: skip
    audit.record(
        db, AGENT, "invoice.diagnose", "invoice", d.invoice_no,
        after={"cause": d.cause, "fix": d.fix, "source": d.source}, reason=f"asked by user {user_id}",
    )  # fmt: skip
    db.commit()


def latest(db: Session, invoice_no: str) -> dict[str, Any] | None:
    rows = _rows(
        db,
        "select cause, fix, explanation, vendor_subject, vendor_body, source, created_at from invoice_diagnosis "
        "where invoice_no = :n order by id desc limit 1",
        n=invoice_no,
    )
    if not rows:
        return None
    r = rows[0]
    r["created_at"] = r["created_at"].isoformat()
    return r
