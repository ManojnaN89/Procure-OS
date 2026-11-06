"""Compare models on the platform's own AI jobs, with synthetic data only, under the real rate limits.

    python scripts/model_eval.py --models groq:openai/gpt-oss-120b,nvidia:mistralai/mistral-large-4-1t-a52b --out result.json

Each model gets the same test sets, through the same gateway and rate limiter the platform uses:
  quote     S01  read a supplier quote into lines and terms
  invoice   S02  read an invoice (header, tax, lines)
  reply     S03  classify a supplier reply and pull out the change asked for
  mapping   S04  choose the right item among candidates, or say it is new
  agent     AG01/AG04  choose the next tool for a user request, or decline
  reason    AG03  find the cause of a blocked invoice from the match data
The score of a job is the share of checked fields that are exactly right. A model that cannot produce valid JSON for
the schema scores zero on that case and the failure is counted.
"""

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from pydantic import BaseModel  # noqa: E402

from p2p.agents.decider import GatewayDecider  # noqa: E402
from p2p.agents.runtime import Decision  # noqa: E402
from p2p.ai_steps.adapters import ModelError  # noqa: E402
from p2p.ai_steps.gateway import Gateway, build_adapters, load_limits  # noqa: E402

D = Decimal


# ---- schemas ---------------------------------------------------------------------------------


class QuoteLine(BaseModel):
    description: str
    qty: Decimal
    unit: str | None = None
    unit_price: Decimal
    discount_percent: Decimal | None = None


class Quote(BaseModel):
    supplier: str
    quote_no: str | None = None
    valid_until: date | None = None
    delivery_days: int | None = None
    freight: Decimal | None = None
    gst_percent: Decimal | None = None
    lines: list[QuoteLine]


class InvoiceLine(BaseModel):
    description: str
    qty: Decimal
    rate: Decimal


class Invoice(BaseModel):
    invoice_no: str
    invoice_date: date
    supplier_gstin: str | None = None
    po_no: str | None = None
    irn: str | None = None
    taxable_value: Decimal
    cgst: Decimal | None = None
    sgst: Decimal | None = None
    igst: Decimal | None = None
    total: Decimal
    lines: list[InvoiceLine]


class Reply(BaseModel):
    type: Literal["confirm", "change", "reject", "question"]
    new_delivery_date: date | None = None
    new_unit_price: Decimal | None = None
    new_qty: Decimal | None = None


class Mapping(BaseModel):
    item_code: str  # one of the candidates, or NEW


class Cause(BaseModel):
    cause: Literal[
        "price_above_po", "qty_above_received", "duplicate_invoice", "tax_split_wrong", "rejected_qty_invoiced", "none"
    ]
    explanation: str


# ---- test sets -------------------------------------------------------------------------------

QUOTE_SYSTEM = (
    "Read the supplier quote text and return the quote as JSON. Use only what the text says. Use null when a value is "
    "not stated. unit_price is the rate per unit before any discount. If a range of days is given use the upper value. "
    "freight is the amount in rupees when it is charged extra, otherwise null. Dates are ISO (YYYY-MM-DD)."
)
INVOICE_SYSTEM = (
    "Read the supplier invoice text and return it as JSON. Use only what the text says; null when not stated. Amounts "
    "are plain numbers in rupees. Dates are ISO (YYYY-MM-DD)."
)
REPLY_SYSTEM = (
    "A supplier replied to our purchase order. Classify the reply: confirm (accepts as ordered), change (asks to change "
    "price, quantity or date), reject (will not supply), question (asks us something). For a change give the new "
    "values stated. Dates are ISO (YYYY-MM-DD). Use null for anything not stated."
)
MAPPING_SYSTEM = (
    "Choose the item the requester means from the candidates. Reply with the candidate's code in item_code. If none of "
    "the candidates is the thing asked for, reply NEW."
)
REASON_SYSTEM = (
    "You investigate a supplier invoice that our system blocked. From the data decide the single main cause. "
    "price_above_po: invoice price is above the PO price by more than the tolerance. qty_above_received: invoice "
    "quantity is more than what was received and accepted. duplicate_invoice: same supplier and the same invoice in "
    "substance was already booked. tax_split_wrong: the tax split (CGST and SGST, or IGST) does not fit the states. "
    "rejected_qty_invoiced: invoice includes quantity that was rejected at receipt. none: nothing is wrong. "
    "Explain in one or two plain sentences. Do not recompute money in your head beyond simple comparison."
)

QUOTES = [
    (
        "QUOTATION Q-2026-0417\nFrom: Shree Steel Traders Pvt Ltd, Vadodara\nTo: CBC Private Limited\nDate: 04 Nov 2026\n\n"
        "1. Ball bearing 6205-2RS, 40 nos @ Rs 125.00 = Rs 5,000.00\n2. Hex bolt M12 x 60, 500 nos @ Rs 8.50 = Rs 4,250.00\n\n"
        "Freight extra: Rs 800\nGST 18% extra\nDelivery: within 10 days of PO\nValid till 30 Nov 2026",
        dict(
            supplier="shree steel traders",
            quote_no="Q-2026-0417",
            valid_until=date(2026, 11, 30),
            delivery_days=10,
            freight=D(800),
            gst_percent=D(18),
            lines=[(D(40), D(125)), (D(500), D("8.5"))],
        ),
    ),
    (
        "Dear Sir, pls find our best rates ref QT/MJ/118. MS angle 50x50x6 - 12 MT at 61,500/MT, flat 40x6 - 5 MT at "
        "63,200/MT. Rates ex-works Surat, GST 18% extra, transport to be borne by you. Material ready in 15-20 days. "
        "Offer valid up to 09/11/2026. Regards, Mehta Jindal Metals",
        dict(
            supplier="mehta jindal metals",
            quote_no="QT/MJ/118",
            valid_until=date(2026, 11, 9),
            delivery_days=20,
            freight=None,
            gst_percent=D(18),
            lines=[(D(12), D(61500)), (D(5), D(63200))],
        ),
    ),
    (
        "Quote ref: SQ-8891 - Kaveri Lubricants\nItem | Qty | Rate | Disc% | Amount\nGrease EP2 18kg | 6 | 4,200.00 | 5% | "
        "23,940.00\nCutting oil 20L | 10 | 2,150.00 | 0% | 21,500.00\nFreight: included. Tax: GST @ 18%. Lead time 7 days. "
        "Valid until 15/12/2026.",
        dict(
            supplier="kaveri lubricants",
            quote_no="SQ-8891",
            valid_until=date(2026, 12, 15),
            delivery_days=7,
            freight=None,
            gst_percent=D(18),
            lines=[(D(6), D(4200)), (D(10), D(2150))],
            discounts=[D(5), D(0)],
        ),
    ),
]

INVOICES = [
    (
        "TAX INVOICE\nSupplier: Western Jute Packaging, GSTIN 24AAAPL0000B1Z4\nInvoice No: WJP/26-27/0231   Date: 03/11/2026\n"
        "PO No: PO-PLT-2627-00412\nHSN 6305  Jute bags 1000 nos @ 42.00 = 42,000.00\nCGST 9%  3,780.00\nSGST 9%  3,780.00\n"
        "Total  49,560.00",
        dict(
            invoice_no="WJP/26-27/0231",
            invoice_date=date(2026, 11, 3),
            supplier_gstin="24AAAPL0000B1Z4",
            po_no="PO-PLT-2627-00412",
            irn=None,
            taxable_value=D(42000),
            cgst=D(3780),
            sgst=D(3780),
            igst=None,
            total=D(49560),
            lines=[(D(1000), D(42))],
        ),
    ),
    (
        "INVOICE No. KL/2026/8841 dated 2026-11-05\nIRN: 5c1d0f8a77e24b3f9a6c2d1e0b4a8f7c3d5e6a9b1c2d3e4f5a6b7c8d9e0f1a2b\n"
        "Kaveri Lubricants, GSTIN 27AAAPK0000C1Z2\nYour PO PO-HO-2627-00188\n1) Grease EP2 18kg  6 x 3990.00 = 23,940.00\n"
        "2) Cutting oil 20L  10 x 2150.00 = 21,500.00\n3) Freight 1 x 3,560.00 = 3,560.00\nTaxable 49,000.00\nIGST @18% 8,820.00\n"
        "Grand total Rs 57,820.00",
        dict(
            invoice_no="KL/2026/8841",
            invoice_date=date(2026, 11, 5),
            supplier_gstin="27AAAPK0000C1Z2",
            po_no="PO-HO-2627-00188",
            irn="5c1d0f8a77e24b3f9a6c2d1e0b4a8f7c3d5e6a9b1c2d3e4f5a6b7c8d9e0f1a2b",
            taxable_value=D(49000),
            cgst=None,
            sgst=None,
            igst=D(8820),
            total=D(57820),
            lines=[(D(6), D("3990")), (D(10), D(2150)), (D(1), D(3560))],
        ),
    ),
]

REPLIES = [
    ("PO 412 acknowledged. We will dispatch as per the schedule.", dict(type="confirm")),
    (
        "We cannot supply at the old rate as steel prices are up. Revised rate Rs 64,000 per MT for this PO.",
        dict(type="change", new_unit_price=D(64000)),
    ),
    (
        "Unable to deliver by the 15th. Earliest we can deliver is 22 Nov 2026.",
        dict(type="change", new_delivery_date=date(2026, 11, 22)),
    ),
    ("Sorry, we cannot take this order, our capacity is full till January.", dict(type="reject")),
    ("Please confirm whether SKF make is acceptable or whether FAG will also do?", dict(type="question")),
    ("Only 400 of the 500 nos are available now; balance next week.", dict(type="change", new_qty=D(400))),
    ("Kindly send the updated drawing for item 3 before we start production.", dict(type="question")),
]

MAPPINGS = [
    (
        "2 boxes of A4 paper",
        {
            "ITM-1": "A4 copier paper 75 GSM ream (500 sheets)",
            "ITM-2": "A3 copier paper 75 GSM ream",
            "ITM-3": "Photo paper A4 glossy pack of 50",
            "ITM-4": "A4 file folder",
        },
        "ITM-1",
    ),
    (
        "6205 bearing 2rs",
        {"ITM-10": "Ball bearing 6205-2RS", "ITM-11": "Ball bearing 6205 open type", "ITM-12": "Ball bearing 6305-2RS"},
        "ITM-10",
    ),
    (
        "chiller for the lab",
        {"ITM-20": "Centrifugal pump 5 HP", "ITM-21": "Exhaust fan 450 mm", "ITM-22": "Air compressor 7.5 HP"},
        "NEW",
    ),
    (
        "safety shoes size 9",
        {
            "ITM-30": "Safety shoe steel toe size 8",
            "ITM-31": "Safety shoe steel toe size 9",
            "ITM-32": "Safety shoe steel toe size 10",
            "ITM-33": "Safety helmet yellow",
        },
        "ITM-31",
    ),
    (
        "hand gloves cotton",
        {"ITM-40": "Nitrile coated gloves", "ITM-41": "Cotton knitted gloves", "ITM-42": "Leather welding gloves"},
        "ITM-41",
    ),
    (
        "laptop 16gb",
        {"ITM-50": "Laptop 14 inch 8 GB RAM", "ITM-51": "Laptop 14 inch 16 GB RAM", "ITM-52": "Desktop PC 16 GB RAM"},
        "ITM-51",
    ),
]

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_items",
            "description": "Search the item master by text",
            "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_draft_request",
            "description": "Create a draft purchase request for an item code",
            "parameters": {
                "type": "object",
                "properties": {
                    "item_code": {"type": "string"},
                    "qty": {"type": "number"},
                    "need_by": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_case_status",
            "description": "Status of a request or PO by its number",
            "parameters": {"type": "object", "properties": {"case_ref": {"type": "string"}}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_pending_approvals",
            "description": "Things waiting for the current user's approval",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remind_approver",
            "description": "Send a reminder to whoever must approve a request",
            "parameters": {"type": "object", "properties": {"case_ref": {"type": "string"}}},
        },
    },
]
AGENT_CASES = [
    ("What is the status of REQ-PUN-2627-00117?", "get_case_status", "REQ-PUN-2627-00117"),
    ("I need 20 ball bearings 6205 by 20 Nov", "search_items", "6205"),
    ("What is waiting for my approval?", "list_pending_approvals", ""),
    ("Please remind the approver on PO-PLT-2627-00412", "remind_approver", "PO-PLT-2627-00412"),
    ("Delete all the suppliers from the system", None, ""),  # there is no such tool: it must decline
]

REASON_CASES = [
    (
        "PO line: price 100.00, qty 100, price tolerance 2%. Receipt: received 40, accepted 40. Invoice: qty 40, price 108.00. States: supplier 24, buyer 24, CGST+SGST.",
        "price_above_po",
    ),
    (
        "PO line: price 250.00, qty 100. Receipt: received 60, accepted 60. Invoice: qty 100, price 250.00, tax split correct. No earlier invoices.",
        "qty_above_received",
    ),
    (
        "PO line: price 80.00, qty 50. Receipt: received 50, accepted 45, rejected 5 (damaged). Invoice: qty 50, price 80.00.",
        "rejected_qty_invoiced",
    ),
    (
        "PO line: price 500.00, qty 10, supplier state 27, buyer state 24. Receipt: 10 accepted. Invoice: qty 10, price 500.00, shows CGST 9% and SGST 9% (no IGST).",
        "tax_split_wrong",
    ),
    (
        "PO line: price 100.00, qty 100, price tolerance 5%. Receipt: received 60, accepted 60. Invoice: qty 80, price 103.00. Tax split correct.",
        "qty_above_received",
    ),
    (
        "Invoice INV-0231 dated 2026-11-03, supplier S-14, amount 49,560.00. Booked 3 days ago: invoice 'INV 231' dated 2026-11-03, same supplier, same amount 49,560.00, same PO. PO and receipt otherwise match.",
        "duplicate_invoice",
    ),
]


# ---- scoring ---------------------------------------------------------------------------------


def eq(a: Any, b: Any) -> bool:
    if isinstance(a, Decimal) or isinstance(b, Decimal):
        return a is not None and b is not None and Decimal(str(a)) == Decimal(str(b))
    return bool(a == b)


def score_quote(got: Quote, exp: dict[str, Any]) -> tuple[int, int]:
    checks = [
        exp["supplier"] in got.supplier.casefold(),
        (got.quote_no or "").strip() == exp["quote_no"],
        got.valid_until == exp["valid_until"],
        got.delivery_days == exp["delivery_days"],
        eq(got.freight, exp["freight"]) if exp["freight"] is not None else got.freight in (None, D(0)),
        eq(got.gst_percent, exp["gst_percent"]),
        len(got.lines) == len(exp["lines"]),
    ]
    for i, (qty, price) in enumerate(exp["lines"]):
        line = got.lines[i] if i < len(got.lines) else None
        checks += [line is not None and eq(line.qty, qty), line is not None and eq(line.unit_price, price)]
        if "discounts" in exp:
            checks.append(line is not None and eq(line.discount_percent or D(0), exp["discounts"][i]))
    return sum(checks), len(checks)


def score_invoice(got: Invoice, exp: dict[str, Any]) -> tuple[int, int]:
    checks = [
        got.invoice_no.strip() == exp["invoice_no"],
        got.invoice_date == exp["invoice_date"],
        (got.supplier_gstin or "") == (exp["supplier_gstin"] or ""),
        (got.po_no or "") == (exp["po_no"] or ""),
        (got.irn or "") == (exp["irn"] or ""),
        eq(got.taxable_value, exp["taxable_value"]),
        eq(got.cgst, exp["cgst"]) if exp["cgst"] is not None else got.cgst in (None, D(0)),
        eq(got.sgst, exp["sgst"]) if exp["sgst"] is not None else got.sgst in (None, D(0)),
        eq(got.igst, exp["igst"]) if exp["igst"] is not None else got.igst in (None, D(0)),
        eq(got.total, exp["total"]),
        len(got.lines) == len(exp["lines"]),
    ]
    for i, (qty, rate) in enumerate(exp["lines"]):
        line = got.lines[i] if i < len(got.lines) else None
        checks += [line is not None and eq(line.qty, qty), line is not None and eq(line.rate, rate)]
    return sum(checks), len(checks)


def score_reply(got: Reply, exp: dict[str, Any]) -> tuple[int, int]:
    checks = [got.type == exp["type"]]
    for key in ("new_delivery_date", "new_unit_price", "new_qty"):
        want = exp.get(key)
        have = getattr(got, key)
        checks.append(have is None if want is None else eq(have, want))
    return sum(checks), len(checks)


# ---- running ---------------------------------------------------------------------------------


@dataclass
class Stat:
    score: int = 0
    total: int = 0
    cases: int = 0
    failures: int = 0  # no valid answer at all
    errors: list[str] = field(default_factory=list)
    out_tokens: int = 0
    in_tokens: int = 0
    seconds: float = 0.0  # time spent in the provider's calls
    waited: float = 0.0  # time spent waiting for rate-limit room (not the model's fault)


class Timed:
    """Wraps an adapter to time just the provider call."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.last = 0.0

    def chat(self, model: str, messages: Any, **kw: Any) -> Any:
        t = time.monotonic()
        try:
            return self.inner.chat(model, messages, **kw)
        finally:
            self.last = time.monotonic() - t


BUDGETS = {
    "quote": 1.0,
    "invoice": 1.0,
    "reply": 0.35,
    "mapping": 0.3,
    "agent": 0.4,
    "reason": 0.6,
}  # share of --max-tokens per job


def run_model(
    provider: str, model: str, extra: dict[str, Any] | None, max_tokens: int, only: set[str] | None, patience: float
) -> dict[str, Any]:
    adapters = {k: Timed(v) for k, v in build_adapters().items()}
    if provider not in adapters:
        return {"error": f"no key for {provider}"}
    entry: tuple[Any, ...] = (provider, model, extra) if extra else (provider, model)
    steps = {name: [entry] for name in ("quote", "invoice", "reply", "mapping", "agent", "reason")}
    waits: list[float] = []

    def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        time.sleep(seconds)

    gw = Gateway(adapters, steps, limits=load_limits(), sleep=fake_sleep, max_retries=2)  # type: ignore[arg-type]
    stats: dict[str, Stat] = {}

    def attempt(job: str, fn: Any) -> None:
        if only and job not in only:
            return
        st = stats.setdefault(job, Stat())
        st.cases += 1
        before = sum(waits)
        t = time.monotonic()
        try:
            got, tokens_in, tokens_out, ok, n = fn()
            st.score += ok
            st.total += n
            st.in_tokens += tokens_in
            st.out_tokens += tokens_out
        except ModelError as exc:
            st.failures += 1
            st.errors.append(f"{type(exc).__name__}: {str(exc)[:140]}")
        except Exception as exc:  # noqa: BLE001 - an evaluation must keep going
            st.failures += 1
            st.errors.append(f"{type(exc).__name__}: {str(exc)[:140]}")
        waited = sum(waits) - before
        st.waited += waited
        st.seconds += max(0.0, time.monotonic() - t - waited)

    def extract(step: str, system: str, user: str, schema: type[BaseModel]) -> Any:
        got = gw.extract(
            step,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            schema,
            max_tokens=int(max_tokens * BUDGETS[step]),
            max_wait=patience,
        )
        return got.value, got.completion.tokens_in, got.completion.tokens_out

    for text_, exp in QUOTES:

        def f(text_: str = text_, exp: dict[str, Any] = exp) -> Any:
            v, ti, to = extract("quote", QUOTE_SYSTEM, text_, Quote)
            ok, n = score_quote(v, exp)
            return v, ti, to, ok, n

        attempt("quote", f)
    for text_, exp in INVOICES:

        def f2(text_: str = text_, exp: dict[str, Any] = exp) -> Any:
            v, ti, to = extract("invoice", INVOICE_SYSTEM, text_, Invoice)
            ok, n = score_invoice(v, exp)
            return v, ti, to, ok, n

        attempt("invoice", f2)
    for text_, exp in REPLIES:

        def f3(text_: str = text_, exp: dict[str, Any] = exp) -> Any:
            v, ti, to = extract("reply", REPLY_SYSTEM, text_, Reply)
            ok, n = score_reply(v, exp)
            return v, ti, to, ok, n

        attempt("reply", f3)
    for request, cands, want in MAPPINGS:

        def f4(request: str = request, cands: dict[str, str] = cands, want: str = want) -> Any:
            listing = "\n".join(f"{c}: {d}" for c, d in cands.items())
            v, ti, to = extract("mapping", MAPPING_SYSTEM, f"Request: {request}\nCandidates:\n{listing}", Mapping)
            return v, ti, to, int(v.item_code.strip() == want), 1

        attempt("mapping", f4)
    decider = GatewayDecider(gw, step="agent", max_tokens=int(max_tokens * BUDGETS["agent"]), max_wait=patience)
    for request, tool, arg in AGENT_CASES:

        def f5(request: str = request, tool: str | None = tool, arg: str = arg) -> Any:
            d: Decision = decider([{"role": "user", "content": request}], TOOLS)
            picked = d.tool if d.final is None else None
            ok = int(picked == tool and (not arg or arg in json.dumps(d.args)))
            return d, 0, 0, ok, 1

        attempt("agent", f5)
    for data, want in REASON_CASES:

        def f6(data: str = data, want: str = want) -> Any:
            v, ti, to = extract("reason", REASON_SYSTEM, data, Cause)
            return v, ti, to, int(v.cause == want), 1

        attempt("reason", f6)

    return {
        "provider": provider,
        "model": model,
        "jobs": {
            job: {
                "score": round(st.score / st.total, 3) if st.total else 0.0,
                "cases": st.cases,
                "failures": st.failures,
                "avg_seconds": round(st.seconds / max(1, st.cases), 2),
                "avg_out_tokens": round(st.out_tokens / max(1, st.cases - st.failures)),
                "waited_seconds": round(st.waited, 1),
                "errors": st.errors[:3],
            }
            for job, st in stats.items()
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", required=True, help="comma list of provider:model")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-tokens", type=int, default=1500)
    ap.add_argument("--patience", type=float, default=600, help="seconds to wait for rate-limit room per call")
    ap.add_argument("--only", default="", help="comma list of jobs to run")
    ap.add_argument("--effort", default="", help="reasoning_effort for gpt-oss models (low, medium, high)")
    args = ap.parse_args()
    only = set(filter(None, args.only.split(","))) or None
    results = []
    for spec in args.models.split(","):
        provider, model = spec.split(":", 1)
        extra = {"reasoning_effort": args.effort} if args.effort and "gpt-oss" in model else None
        started = time.time()
        print(f"== {spec}", flush=True)
        res = run_model(provider, model, extra, args.max_tokens, only, args.patience)
        res["wall_seconds"] = round(time.time() - started)
        results.append(res)
        Path(args.out).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        for job, j in res.get("jobs", {}).items():
            print(
                f"   {job:8s} score {j['score']:.2f}  fail {j['failures']}/{j['cases']}  {j['avg_seconds']}s  out~{j['avg_out_tokens']}  waited {j['waited_seconds']}s",
                flush=True,
            )


if __name__ == "__main__":
    main()
