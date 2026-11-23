"""E118: Brightwave's period RFQ with too few suppliers, the agreement first, then the award, the contract and
isolation (tasks 12.1-12.3, OP-16, 0b #6), written without the implementation.

Independence: written from the block 5 spec (sections 5.1, 6.1, 6.3, 8.3 B1, 9) and Brightwave's dataset only. Invitees
and `min_quotes` from `block5.Oracle5` (ITH suppliers SUP-001 and SUP-002, the D37 band above Rs 2,00,000 at the last
prices paid); the award value with IGST (Tamil Nadu to Karnataka); the approver from Brightwave's `po` rows (PO-01
automatic, PO-02 the CFO); the agreement limit from the plan's D37 default (Rs 25,000); terms from `supplier.csv`.

On fresh Brightwave and Apex resets, 23 Nov 2026. The buyer asks HS-01 60 and KB-01 60 at OFF-BLR for 1 Apr 2027 to
31 Mar 2028: SUP-002 and SUP-001 invited, min_quotes 3, a note says so. Quotes SUP-002 2,350.00 / 1,750.00, SUP-001
2,450.00 / 1,800.00. Awarding SUP-002 both lines without a reason is refused; with one it waits for the
`rfq.award_short` agreement (2,46,000.00 above 25,000), no award decision yet; the head of engineering agrees; then the
award decision: 2,46,000.00, IGST 44,280.00 (no CGST or SGST), 2,90,280.00, PO-02, the CFO, who approves. CNT-0003:
SUP-002, 1 Apr 2027 to 31 Mar 2028, ceiling 2,46,000.00, NET30, HS-01 2,350.00 and KB-01 1,750.00 at OFF-BLR. The
supplier accepts by link and code; the CFO's activation is refused, the IT head's works. Apex cannot read the RFQ or
the contract, and a link forged for Apex opens nothing. Truth file.
Mutations: the too-few-quotes rule skipped for period awards; IGST split as CGST and SGST.
"""

import re
from decimal import Decimal
from typing import Any

import block1 as b
import block4 as k
import block5 as f
import env
import lib
from block1 import D, js
from lib import Outcome

ID = "E118"
TITLE = "Brightwave: too few quotes are agreed first, the award is the CFO's, the contract is activated by another"
REQUIREMENT = (
    "Tasks 12.1-12.3, OP-16, 0b #6 (block 5 spec B1): invitees and min_quotes as operational with a note; a reason and "
    "the rfq.award_short agreement before the award decision; IGST from the oracle; PO-02's CFO approves; the draft "
    "contract's fields; acceptance by code; the award approver never activates; Apex cannot read Brightwave's RFQ, "
    "contract or link; truth file"
)
PREFIX = "e118"
ITEMS = [("HS-01", 60), ("KB-01", 60)]
QUOTES = {"SUP-002": ["2350.00", "1750.00"], "SUP-001": ["2450.00", "1800.00"]}
AGREE_ABOVE = Decimal(25000)


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        f.pin(lib.TODAY)
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient
    from p2p.main import app
    from p2p.platform import mail

    out = Outcome(True)
    check = b.Checker(out)
    saved = mail.send_email
    box = lib.capture_mail()
    try:
        client = TestClient(app)
        apex = f.Co5("apex", PREFIX, client, box)
        bw = f.Co5("brightwave", PREFIX, client, box)
        f.start(bw)
        start = bw.mark()
        oracle = f.Oracle5(bw)
        m = k.Mutations()
        truth: list[dict[str, Any]] = []
        rfq, code, token = _b1(out, check, bw, oracle, m, truth)
        out.metrics["truth rows compared"] = f.compare_truth(check, bw, truth)
        m.report(check, out, ["short_skipped", "igst_split"])
        f.tenant_check(check, bw, apex, [rfq], [code] if code else [], start)
        if token:
            forged = f.foreign_token(token, bw.slug, apex.slug)
            since = bw.mark()
            page = bw.link_get(f"/contract/{forged}")
            asked = bw.link_post(f"/contract/{forged}/code")
            check((page.status_code != 200 or "2350" not in page.text.replace(",", "")) and asked.status_code != 200
                  and not bw.mails(since), f"a Brightwave link forged for Apex answered {page.status_code}/"
                  f"{asked.status_code}, {len(bw.mails(since))} mail(s); expected nothing shown or sent")  # fmt: skip
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        (
            "The award without a reason may be refused 409 (today's award errors) or 422 (the spec's B1 row); both are "
            "accepted."
        ),
        "The agreement threshold is the plan's D37 default (agree_above Rs 25,000), not a dataset file.",
    ]
    return out


def _b1(out: Outcome, check: b.Checker, co: f.Co5, oracle: f.Oracle5, m: k.Mutations,
        truth: list[dict[str, Any]]) -> tuple[str, str, str]:  # fmt: skip
    buyer, cfo, depthead, ithead = (
        co.login("buyer"),
        co.login("cfo"),
        co.login("depthead"),
        co.login("ithead"),
    )
    value = sum(
        (q * oracle.estimate(i, f.BW_LOC, f.TODAY) for i, q in ITEMS), Decimal(0)
    )
    mq = oracle.min_quotes("ITH", value)
    invitees = oracle.invitees("ITH", [i for i, _ in ITEMS], f.BW_LOC, f.TODAY, mq)
    check(set(invitees) == f.SPEC_INVITED["B1"] and mq == f.SPEC_MIN_QUOTES["B1"], f"oracle self-check B1: invitees "
          f"{invitees}, min_quotes {mq}", "medium")  # fmt: skip
    r = co.period_rfq(buyer, f.BW_LOC, f.BW_FROM, f.BW_TO, ITEMS)
    rfq = f.created(check, co, r, "B1")
    if not rfq:
        return "", "", ""
    row = co.rfq(rfq)
    got = sorted(str(s["supplier_code"]) for s in co.invited(rfq))
    check(got == sorted(invitees) and int(row.get("min_quotes") or 0) == mq and bool(row.get("note") or js(r).get("note")),
          f"B1 {rfq}: invited {got}, min_quotes {row.get('min_quotes')}, note {row.get('note')!r}; oracle "
          f"{sorted(invitees)}, {mq}, a note that fewer suppliers can be asked")  # fmt: skip
    check(co.confirm(buyer, rfq), f"B1: the buyer could not confirm sending {rfq}")
    for supplier, prices in QUOTES.items():
        q = co.quote(buyer, rfq, supplier, prices)
        check(
            q.status_code == 200,
            f"B1: the quote of {supplier}: {q.status_code} {q.text[:200]}",
        )
    co.work()
    r = co.award(buyer, rfq, {1: "SUP-002", 2: "SUP-002"})
    check(r.status_code in (409, 422) and not co.decisions(rfq), f"B1: the award without a reason answered "
          f"{r.status_code} {r.text[:200]}; expected refused (too few quotes need a reason)")  # fmt: skip
    r = co.award(
        buyer,
        rfq,
        {1: "SUP-002", 2: "SUP-002"},
        reason="Only two suppliers sell IT hardware to Bengaluru",
    )
    body = js(r)
    short = [p for p in co.qd("select * from proposal where action = 'rfq.award_short' and status = 'pending'")
             if rfq in str(p.get("payload"))]  # fmt: skip
    first = (
        "agreement"
        if short and not co.decisions(rfq)
        else "decision"
        if co.decisions(rfq)
        else "nothing"
    )
    m.case(
        "short_skipped", "B1 award with too few quotes", first, "agreement", "decision"
    )
    awarded_taxable = sum(
        (D(p) * q for p, (_, q) in zip(QUOTES["SUP-002"], ITEMS, strict=True)),
        Decimal(0),
    )
    check(first == "agreement" and awarded_taxable > AGREE_ABOVE, f"B1: after the award with a reason: {r.status_code} "
          f"{body}; pending rfq.award_short {len(short)}, decisions {len(co.decisions(rfq))}; expected the agreement "
          f"first (Rs {awarded_taxable} above {AGREE_ABOVE})")  # fmt: skip
    if short:
        a = co.call(
            depthead,
            "POST",
            f"/api/admin/proposals/{short[0]['id']}/approve",
            {"note": "eval: agreed"},
        )
        co.work()
        check(
            a.status_code == 200,
            f"B1: the head of engineering's agreement answered {a.status_code} {a.text[:200]}",
        )
    want = oracle.award_value(
        [(i, q, p) for (i, q), p in zip(ITEMS, QUOTES["SUP-002"], strict=True)],
        "SUP-002",
        f.BW_LOC,
    )
    rule, approver = oracle.award_approver(want["total"], buyer)
    f.self_check(check, oracle, "B1", want, rule, approver)
    d = f.check_decision(check, co, rfq, want, rule, approver, "B1")
    split = (D(d.get("cgst")), D(d.get("igst"))) if d else None
    m.case("igst_split", "B1 GST from Chennai to Bengaluru", split, (Decimal(0), want["igst"]),
           (want["igst"] / 2, Decimal(0)))  # fmt: skip
    if not d:
        return rfq, "", ""
    r = co.decide(buyer, rfq, "approve")
    check(
        r.status_code == 403,
        f"B1: the buyer approving the award answered {r.status_code}; expected 403",
    )
    since = co.mark()
    r = co.decide(cfo, rfq, "approve", note="eval: agreed")
    check(
        r.status_code == 200,
        f"B1: the CFO's approval answered {r.status_code} {r.text[:200]}",
    )
    made = co.contracts_of(rfq)
    if not check(
        len(made) == 1,
        f"B1: the approval made {len(made)} contract(s); expected one for SUP-002",
    ):
        return rfq, "", ""
    c = made[0]
    code = str(c["code"])
    nums = [int(x["code"].split("-")[1]) for x in co.rows("master", "contract")]
    check(code == f"CNT-{max(nums) + 1:04d}", f"B1: the contract is {code}; the next after the dataset's is "
          f"CNT-{max(nums) + 1:04d}", "medium")  # fmt: skip
    terms = oracle.suppliers["SUP-002"]["payment_terms_code"]
    check(c.get("supplier_code") == "SUP-002" and c.get("valid_from") == f.BW_FROM and c.get("valid_to") == f.BW_TO
          and D(c.get("max_value")) == want["taxable"] and c.get("payment_terms_code") == terms and c.get("status") == "draft"
          and f.same_person(co, c.get("created_by"), buyer),
          f"B1 contract {code}: {c.get('supplier_code')}, {c.get('valid_from')} to {c.get('valid_to')}, ceiling "
          f"{c.get('max_value')}, {c.get('payment_terms_code')}, {c.get('status')}, by {c.get('created_by')}; oracle "
          f"SUP-002, {f.BW_FROM} to {f.BW_TO}, {want['taxable']}, {terms}, draft, the buyer")  # fmt: skip
    prices = {str(p.get("item_code")): p for p in co.prices_of(code)}
    for (item, _), price in zip(ITEMS, QUOTES["SUP-002"], strict=True):
        p = prices.get(item, {})
        check(D(p.get("price")) == D(price) and p.get("location_code") == f.BW_LOC and p.get("status") == "draft",
              f"B1 contract price {item}: {p.get('price')} at {p.get('location_code')} ({p.get('status')}); oracle "
              f"{price} at {f.BW_LOC}, draft")  # fmt: skip
    token = co.contract_token(since, to=oracle.contacts["SUP-002"]["email"])
    if check(
        token is not None,
        "B1: the award mail to SUP-002 carries no /contract/<token> link",
    ):
        _accept(check, co, oracle, code, str(token))
    r = co.call(cfo, "POST", f"/api/contracts/{code}/activate")
    check(r.status_code == 403 and co.contract(code).get("status") == "draft", f"B1: the CFO (award approver) activating "
          f"answered {r.status_code}; expected 403")  # fmt: skip
    r = co.call(ithead, "POST", f"/api/contracts/{code}/activate")
    check(r.status_code == 200 and co.contract(code).get("status") == "active", f"B1: the IT head activating answered "
          f"{r.status_code} {r.text[:200]}")  # fmt: skip
    for n, ((item, qty), price) in enumerate(
        zip(ITEMS, QUOTES["SUP-002"], strict=True), 1
    ):
        line = oracle.award_value([(item, qty, price)], "SUP-002", f.BW_LOC)
        truth.append({"scenario": "B1", "line": str(n), "item": item, "location": f.BW_LOC, "qty": qty,
                      "supplier": "SUP-002", "net_price": price, "taxable": line["taxable"], "cgst": line["cgst"],
                      "sgst": line["sgst"], "igst": line["igst"], "total": {line["total"], want["total"]}, "rule": rule,
                      "approver": approver, "contract_code": code})  # fmt: skip
    out.metrics["B1"] = (
        f"{rfq}: {want['total']} (IGST {want['igst']}), {rule}, {approver}, {code}"
    )
    return rfq, code, str(token or "")


def _accept(
    check: b.Checker, co: f.Co5, oracle: f.Oracle5, code: str, token: str
) -> None:
    address = oracle.contacts["SUP-002"]["email"]
    r = co.link_post(f"/contract/{token}", action="accept")
    check(r.status_code == 409 and co.contract(code).get("supplier_accepted_at") is None,
          f"B1: accepting without a code answered {r.status_code}; expected 409")  # fmt: skip
    since = co.mark()
    co.link_post(f"/contract/{token}/code")
    mails = co.mails(since)
    otp = next(
        (
            x.group(1)
            for x in (
                re.search(r"\b(\d{6})\b", str(mm.get("body") or "")) for mm in mails
            )
            if x
        ),
        None,
    )
    check(bool(otp) and all(str(mm.get("to", "")).lower() == address.lower() for mm in mails),
          f"B1: the code went to {[mm.get('to') for mm in mails]}; expected {address} only")  # fmt: skip
    if otp:
        r = co.link_post(f"/contract/{token}", action="accept", code=otp)
        check(r.status_code == 200 and co.contract(code).get("supplier_accepted_at") is not None,
              f"B1: accepting with the code answered {r.status_code}")  # fmt: skip
