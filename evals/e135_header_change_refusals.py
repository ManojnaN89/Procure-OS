"""E135: header changes that are refused, with nothing written (spec 7.4 and 7 P9), written without the implementation.

Independence: written from the block 7 spec (sections 4, 5.5 R11-R15, 7 P9, 8, 9.2, OP-3, OP-18, OP-19) and the
dataset only. The oracle is the spec's own rule per field and its words: the supplier and the item are never changed on
an order (cancel and order again), the delivery place is refused once goods were received or for a contract line whose
price does not apply at the new place (`price_record.csv`: CNT-0001 is region MH, `region.csv`: "Maharashtra plants";
PLT-SND is region GJ), the payment terms once an invoice exists, the version the buyer saw must be the current one,
re-pricing needs a contract line, delivery terms are 1-200 characters and must differ, only a buyer revises, and another
company sees nothing. "Nothing written" is the order's version, status, header, lines, audit rows, links, version rows,
revisions and steps, before and after.

Given a fresh Apex reset on Mon 23 Nov 2026 with three orders: A = Q(120 kg, 905.00) approved by U08, sent and cut to
100 (version 2); B = K(RM-01, 200, 15 Jan 2027) on CNT-0001 waiting for U08; C = K(RM-05, 10) touchless and sent.
- A: the supplier in the body 422 "the supplier cannot be changed on an order: cancel it and order the lines again";
  the item likewise 422; `version` 1 on version 2: 409 "<A> has changed since you opened it: it is now version 2";
  `reprice` on the RM-08 quotation line 409 "line 1 is not bought on a contract"; delivery terms of 201 characters 422;
  production@ 403; an unknown place or payment terms code refused; after a receipt of 50 kg the delivery place 409
  "goods have been received at ..."; after an invoice for those 50 kg the payment terms 409 "an invoice has been entered
  against <A>: its payment terms are fixed".
- B to PLT-SND: 409 "line 1: CNT-0001 applies in Maharashtra plants only".
- C: the same delivery terms again: 422 "delivery_terms is already <value>".
- Every refusal writes nothing. Brightwave's buyer gets 404 on A's revise, versions, comparison and PDFs.
Mutations: a supplier change accepted on revise; a location change after a receipt.
"""

from typing import Any

import block1 as b
import block4 as k
import block7 as t
import env
from lib import Outcome, capture_mail

ID = "E135"
TITLE = "Refused header changes say why and write nothing; another company cannot touch the order"
REQUIREMENT = (
    "Q19, OP-3, OP-18, OP-19 (block 7 spec R11-R15, P9): supplier and item never change on an order; the delivery place "
    "is refused after a receipt or where a contract line's price does not apply; payment terms after an invoice; a stale "
    "version, a re-price of a quotation line, delivery terms too long or unchanged, a requester revising: each refused "
    "with its code and words and nothing written; Brightwave gets 404"
)
PREFIX = "e135"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        t.pin(t.TODAY)
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient

    from p2p.main import app
    from p2p.platform import mail

    out = Outcome(True)
    check = b.Checker(out)
    saved = mail.send_email
    box = capture_mail()
    try:
        client = TestClient(app)
        bw = t.Co7("brightwave", PREFIX, client, box)
        apex = t.Co7("apex", PREFIX, client, box)
        t.start(apex)
        start = apex.mark()
        m = k.Mutations()
        a, tokens = _order_a(check, apex)
        b_ = apex.k_order(check, t.CASTING, 200, t.P8_NEED, "P9 B")
        c = apex.k_order(check, t.SHAFT, 10, t.NEED_BY, "P9 C")
        refused: dict[str, int] = {}
        if a:
            _refusals_a(check, apex, a, m, refused)
        if b_:
            _refusal_b(check, apex, b_, refused)
        if c:
            _refusal_c(check, apex, c, refused)
        m.report(check, out, ["supplier_change_accepted", "location_after_receipt"])
        t.tenant_check7(check, apex, bw, [a, b_, c], tokens, start)
        out.metrics["orders"] = {"A": a, "B": b_, "C": c}
        out.metrics["refusals (status)"] = refused
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        ("Spec gap: where the supplier or item goes in the body is not said; the eval sends `header.supplier_code` and "
         "a line's `item_code`, the shapes section 8 uses for the other fields."),
        ("An unknown place or payment terms code has no code in P9; any of 404, 409, 422 passes (medium), with "
         "nothing written."),
        ("The receipt and the invoice are made through the existing receipt and invoice APIs as the store and AP; the "
         "invoice may be held by the match, it exists all the same (R13: not cancelled)."),
    ]
    return out


def _try(check: b.Checker, co: t.Co7, no: str, label: str, codes: int | tuple[int, ...], words: str | None,
         refused: dict[str, int], *, by: str | None = None, **kw: Any) -> Any:  # fmt: skip
    before = co.footprint(no)
    r = co.revise7(no, by=by, **kw)
    refused[label] = r.status_code
    t.refusal(check, r, codes, words, label)
    t.unchanged(check, co, no, before, label)
    return r


def _order_a(check: b.Checker, co: t.Co7) -> tuple[str, list[str | None]]:
    oracle = t.Oracle7(co)
    po = co.q_order(check, "P9 A")
    if not po:
        return "", []
    supplier = str(co.po_row(po).get("supplier_code"))
    v1 = oracle.version(1, t.WIRE, 120, "905.00", t.NEED_BY, supplier, t.LOC)
    rule, who = oracle.approver(v1.amounts["total"], frozenset({co.buyer()}))
    t.self_check(check, "P1v1", t.figures(v1, rule, who))
    k.approve_ok(check, co, who, po, 1, "P9 A approve version 1")
    l1, _m1, _ = co.send_v(check, po, "P9 A send")
    r = co.revise7(po, lines=[{"line_no": 1, "qty": "100"}], version=1)
    check(r.status_code == 200 and int(co.po_row(po).get("version") or 0) == 2, f"P9 A: the cut to 100 answered "
          f"{r.status_code} {r.text[:150]} (precondition: version 2)", "medium")  # fmt: skip
    return po, [l1]


def _refusals_a(check: b.Checker, co: t.Co7, no: str, m: k.Mutations, refused: dict[str, int]) -> None:
    cancel = "cannot be changed on an order: cancel it and order the lines again"
    r = _try(check, co, no, "A supplier in the body", 422, f"the supplier {cancel}", refused,
             header={"supplier_code": "SUP-003"}, version=2)  # fmt: skip
    m.case("supplier_change_accepted", "A header supplier_code SUP-003",
           (r.status_code, "cannot be changed" in t.norm(r.text)), (422, True), (200, False))
    _try(check, co, no, "A item in the body", 422, f"the item {cancel}", refused,
         lines=[{"line_no": 1, "item_code": "RM-09"}], version=2)  # fmt: skip
    version = int(co.po_row(no).get("version") or 0)
    _try(check, co, no, "A version 1 seen on version 2", 409,
         f"{no} has changed since you opened it: it is now version {version}", refused,
         header={"delivery_terms": "ex_works"}, version=1)  # fmt: skip
    _try(check, co, no, "A re-price of the quotation line", 409, "line 1 is not bought on a contract", refused,
         lines=[{"line_no": 1, "reprice": True}], version=version)  # fmt: skip
    _try(check, co, no, "A delivery terms of 201 characters", 422, None, refused,
         header={"delivery_terms": "x" * 201}, version=version)  # fmt: skip
    _try(check, co, no, "A production revising", 403, None, refused, by=co.login("production"),
         header={"delivery_terms": "ex_works"}, version=version)  # fmt: skip
    _try(check, co, no, "A unknown delivery place", (404, 409, 422), None, refused,
         header={"ship_to_location": "PLT-XXX"}, version=version)  # fmt: skip
    _try(check, co, no, "A unknown payment terms", (404, 409, 422), None, refused,
         header={"payment_terms_code": "NET999"}, version=version)  # fmt: skip

    # a receipt of 50 kg: the delivery place is fixed
    with b.on_day(t.TODAY):
        r = co.call(co.role("store"), "POST", f"/api/purchase-orders/{no}/receive",
                    {"lines": [{"line_no": 1, "qty_received": "50"}], "challan": f"DC-E135-{no[-5:]}"})  # fmt: skip
    t.pin(t.TODAY)
    co.work()
    check(r.status_code == 200, f"P9 A: receiving 50 kg answered {r.status_code} {r.text[:200]} (precondition)", "medium")
    r = _try(check, co, no, "A delivery place after a receipt of 50 kg", 409, "goods have been received at", refused,
             header={"ship_to_location": t.SND}, version=version)  # fmt: skip
    m.case("location_after_receipt", "A to PLT-SND after 50 kg received",
           (r.status_code, "received" in t.norm(r.text)), (409, True), (200, False))

    # an invoice for those 50 kg: the payment terms are fixed
    sup = next(x for x in co.rows("master", "supplier") if x["code"] == co.po_row(no).get("supplier_code"))
    body = {"supplier_code": sup["code"], "number": f"E135-{no[-5:]}", "date": t.TODAY.isoformat(),
            "supplier_gstin": sup["gstin"], "buyer_gstin": co.po_row(no).get("buyer_gstin"),
            "lines": [{"po_no": no, "po_line_no": 1, "qty": "50", "unit_price": "905.00"}]}  # fmt: skip
    if sup.get("e_invoice_applicable") == "true":
        body["irn"] = "E" * 64
    r = co.call(co.role("ap"), "POST", "/api/invoices", body)
    co.work()
    check(r.status_code in (200, 201), f"P9 A: the invoice for 50 kg answered {r.status_code} {r.text[:200]} "
          "(precondition)", "medium")  # fmt: skip
    _try(check, co, no, "A payment terms after an invoice", 409,
         f"an invoice has been entered against {no}: its payment terms are fixed", refused,
         header={"payment_terms_code": "NET30"}, version=version)  # fmt: skip


def _refusal_b(check: b.Checker, co: t.Co7, no: str, refused: dict[str, int]) -> None:
    oracle = t.Oracle7(co)
    region = oracle.region_name(t.LOC)
    check(region == "Maharashtra plants" and oracle.region_of(t.SND) != oracle.region_of(t.LOC),
          f"P9 B: the oracle's regions {region} / {oracle.region_of(t.SND)} (precondition)", "medium")  # fmt: skip
    version = int(co.po_row(no).get("version") or 1)
    _try(check, co, no, "B to PLT-SND", 409, f"line 1: {t.CONTRACT} applies in {region} only", refused,
         header={"ship_to_location": t.SND}, version=version)  # fmt: skip


def _refusal_c(check: b.Checker, co: t.Co7, no: str, refused: dict[str, int]) -> None:
    row = co.po_row(no)
    current = row.get("delivery_terms")
    if not current:  # the spec's refusal needs a value to repeat: set one first (a minor change)
        co.revise7(no, header={"delivery_terms": "ex_works"}, version=int(row.get("version") or 1))
        current = co.po_row(no).get("delivery_terms")
    check(bool(current), f"P9 C: delivery terms {current!r} (precondition)", "medium")
    version = int(co.po_row(no).get("version") or 1)
    _try(check, co, no, "C the same delivery terms", 422, f"delivery_terms is already {current}", refused,
         header={"delivery_terms": current}, version=version)  # fmt: skip
