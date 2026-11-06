"""Runs the synthetic emails through the real reading pipeline (replies.read_reply) and scores the drafts."""

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import lib
from ai_cases import Case, Item
from lib import D, World, rows
from p2p.ai_steps.adapters import RawResponse
from p2p.ai_steps.gateway import Gateway
from p2p.ai_steps.quote_reading import QuoteReading, ReadLine
from p2p.sourcing import replies
from sqlalchemy import text


class Scripted:
    """A fake provider that answers whatever it was last told to."""

    def __init__(self) -> None:
        self.answer = "{}"
        self.calls = 0

    def chat(
        self,
        model: str,
        messages: Any,
        *,
        temperature: float,
        max_tokens: int,
        json_output: bool,
        **kw: Any,
    ) -> RawResponse:
        self.calls += 1
        return RawResponse(self.answer, 100, 50, "stop")


def scripted_gateway() -> tuple[Gateway, Scripted]:
    provider = Scripted()
    gw = Gateway(
        {"fake": provider},
        {"quote_reading": [("fake", "m")], "quote_reading.strong": [("fake", "m2")]},
        sleep=lambda s: None,
    )
    return gw, provider


def supplier_for_emails() -> tuple[str, str]:
    """A real supplier of the dataset with an email address, and that address."""
    cats = {
        r["supplier_code"]
        for r in rows("master", "supplier_category")
        if r["category_code"] == "MRO"
    }
    for c in rows("master", "supplier_contact"):
        if c["supplier_code"] in cats and c["email"]:
            return c["supplier_code"], c["email"]
    raise RuntimeError("no supplier with an email")


def setup_rfq(world: World, tag: str, items: list[Item]) -> tuple[str, str, str]:
    """An approved request with an RFQ out to one supplier, one line per item. Returns (rfq, supplier, supplier email)."""
    db = world.db
    supplier, email = supplier_for_emails()
    req, rfq = f"EV-R-{tag}", f"EV-Q-{tag}"
    db.execute(
        text(
            "insert into request (no, ship_to_location, type, channel, status, total_value) values (:n, 'WH-BLR', 'goods', 'planning', 'approved', 1)"
        ),
        {"n": req},
    )
    db.execute(
        text(
            "insert into rfq (no, request_no, category_code, due_date, status) values (:r, :q, 'MRO', now(), 'sent')"
        ),
        {"r": rfq, "q": req},
    )
    for it in items:
        db.execute(
            text(
                "insert into request_line (request_no, line_no, item_code, qty, uom, category_code, est_price, need_by, status) values (:r, :n, :i, :q, 'NOS', 'MRO', :e, '2026-12-01', 'sourcing')"
            ),
            {"r": req, "n": it.line_no, "i": it.code, "q": it.qty, "e": it.est},
        )
        db.execute(
            text(
                "insert into rfq_line (rfq_no, line_no, request_no, request_line_no, item_code, description, qty, uom, need_by) values (:r, :n, :q, :n, :i, :d, :qty, 'NOS', '2026-12-01')"
            ),
            {
                "r": rfq,
                "n": it.line_no,
                "q": req,
                "i": it.code,
                "d": it.name,
                "qty": it.qty,
            },
        )
    db.execute(
        text(
            "insert into rfq_supplier (rfq_no, supplier_code, email, invited_at) values (:r, :s, :e, now()) on conflict do nothing"
        ),
        {"r": rfq, "s": supplier, "e": email},
    )
    db.commit()
    return rfq, supplier, email


def deliver(
    world: World,
    rfq: str,
    from_addr: str,
    body: str,
    subject: str = "Re: Request for quotation",
) -> int:
    db = world.db
    email_id = int(
        db.execute(
            text(
                "insert into email_message (message_id, direction, from_addr, to_addr, subject, body_text, sent_at, plus_tag, linked_type, linked_id) "
                "values (:m, 'in', :f, :t, :s, :b, now(), :tag, 'rfq', :r) returning id"
            ),
            {
                "m": f"<{rfq}-{time.monotonic_ns()}@eval>",
                "f": from_addr,
                "t": f"rfq+{rfq}@cbcpl.example",
                "s": subject,
                "b": body,
                "tag": rfq,
                "r": rfq,
            },
        ).scalar_one()
    )
    db.commit()
    return email_id


def perfect_reading(case: Case) -> QuoteReading:
    """What a careful model writes for a case: only what the email says, nothing computed."""
    t = case.truth
    if t.intent == "decline":
        reason = (
            case.body.split("because ")[-1].split(".")[0]
            if "because " in case.body
            else None
        )
        return QuoteReading(intent="decline", decline_reason=reason, confidence=0.95)
    if t.intent == "question":
        return QuoteReading(intent="question", question=t.question, confidence=0.95)
    lines = []
    by_line = {it.line_no: it for it in case.items}
    for n, v in t.lines.items():
        it = by_line[n]
        written = v.get("written", v["price"])
        lines.append(
            ReadLine(
                item=it.code if case.style == "by-code" else it.name,
                unit_price=written,
                discount_pct=v["discount"],
                gst_rate=v["gst"],
                delivery_days=v["days"],
                price_includes_gst=True if v.get("incl") else None,
            )
        )
    return QuoteReading(
        intent="quote",
        lines=lines,
        valid_until=t.valid_until,  # type: ignore[arg-type]
        freight=t.freight,
        packing_forwarding=t.packing,
        confidence=0.92,
    )


@dataclass
class Scored:
    case: Case
    status: str
    intent: str | None
    correct: bool
    clean: bool  # looks ready to confirm: pending, no problems, no assumptions
    silent_error: bool
    problems: list[str]
    detail: str
    seconds: float
    model_calls: int


def score(case: Case, draft: dict[str, Any], seconds: float, calls: int) -> Scored:
    t = case.truth
    status = draft["status"]
    problems = list(draft["problems"] or [])
    assumptions = list(draft["assumptions"] or [])
    clean = status == "pending" and not problems and not assumptions
    intent = draft["intent"]
    detail = ""
    if t.intent != "quote":
        correct = intent == t.intent and status == "pending"
        if t.intent == "decline" and not correct:
            detail = f"read as {intent}, status {status}"
    else:
        correct = intent == "quote" and status == "pending"
        proposal = draft["proposal"] or {}
        got = {ln["rfq_line_no"]: ln for ln in proposal.get("lines", [])}
        if status == "needs_person" and not t.lines:
            correct = True  # nothing safe to enter: abstaining is the right answer
        elif correct:
            if set(got) != set(t.lines):
                correct, detail = (
                    False,
                    f"lines read {sorted(got)}, expected {sorted(t.lines)}",
                )
            else:
                by_item = {it.line_no: it for it in case.items}
                for n, v in t.lines.items():
                    g = got[n]
                    want_gst = v["gst"] if v["gst"] is not None else by_item[n].gst
                    checks = {
                        "price": D(g["unit_price"]) == v["price"],
                        "discount": D(g["discount_pct"])
                        == (v["discount"] or Decimal(0)),
                        "gst": D(g["gst_rate"]) == want_gst,
                        "days": g["delivery_days"] == v["days"],
                    }
                    bad = [k for k, ok in checks.items() if not ok]
                    if bad:
                        correct = False
                        detail = f"line {n}: {', '.join(bad)} wrong (got {g['unit_price']}/{g['discount_pct']}/{g['gst_rate']}/{g['delivery_days']})"
                        break
                if correct:
                    freight, packing = (
                        D(proposal.get("freight")),
                        D(proposal.get("packing_forwarding")),
                    )
                    if freight != (t.freight or Decimal(0)) or packing != (
                        t.packing or Decimal(0)
                    ):
                        correct, detail = (
                            False,
                            f"charges {freight}/{packing}, expected {t.freight}/{t.packing}",
                        )
                    if (proposal.get("valid_until") or None) != t.valid_until:
                        correct, detail = (
                            False,
                            f"valid_until {proposal.get('valid_until')}, expected {t.valid_until}",
                        )
        elif not detail:
            detail = f"status {status}: {problems[:1]}"
    return Scored(
        case,
        status,
        intent,
        correct,
        clean,
        clean and not correct,
        problems,
        detail,
        seconds,
        calls,
    )


def run_case(
    world: World,
    case: Case,
    gateway: Gateway,
    provider: Scripted | None,
    answer: QuoteReading | None,
) -> Scored:
    rfq, supplier, email = setup_rfq(world, case.id, case.items)
    email_id = deliver(world, rfq, email, case.body)
    calls_before = provider.calls if provider else 0
    if provider is not None and answer is not None:
        provider.answer = answer.model_dump_json()
    started = time.monotonic()
    draft_id = replies.read_reply(
        world.db, world.slug, email_id, gateway, as_of=lib.TODAY
    )
    seconds = time.monotonic() - started
    draft = dict(
        world.db.execute(
            text("select * from quote_draft where id = :i"), {"i": draft_id}
        )
        .mappings()
        .one()
    )
    calls = (
        (provider.calls - calls_before)
        if provider
        else int(
            world.db.execute(
                text("select count(*) from model_call where step like 'quote_reading%'")
            ).scalar_one()
        )
    )
    return score(case, draft, seconds, calls)


def summarise(scored: list[Scored]) -> dict[str, Any]:
    n = len(scored)
    exact = sum(s.correct for s in scored)
    safe = sum(s.correct or s.status == "needs_person" for s in scored)
    silent = [s for s in scored if s.silent_error]
    intent_ok = sum(
        1
        for s in scored
        if s.intent == s.case.truth.intent
        or (s.status == "needs_person" and s.intent is None)
    )
    by_style: dict[str, list[bool]] = {}
    for s in scored:
        by_style.setdefault(s.case.style, []).append(s.correct)
    return {
        "cases": n,
        "exact %": round(100 * exact / n, 1),
        "exact or safely refused %": round(100 * safe / n, 1),
        "silent errors": len(silent),
        "sent to a person": sum(s.status == "needs_person" for s in scored),
        "intent right %": round(100 * intent_ok / n, 1),
        "by style": {k: f"{sum(v)}/{len(v)}" for k, v in by_style.items()},
        "median seconds": round(sorted(s.seconds for s in scored)[n // 2], 2),
        "silent error cases": [f"{s.case.id}: {s.detail}" for s in silent][:5],
        "misses": [
            f"{s.case.id}: {s.detail or s.status}"
            for s in scored
            if not s.correct and not s.silent_error
        ][:6],
    }
