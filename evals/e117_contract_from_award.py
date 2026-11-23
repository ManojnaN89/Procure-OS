"""E117: the award becomes a draft rate contract, the supplier accepts with a code, a second person activates, a later
request buys on it (tasks 12.3, AT-02, D48, D53, OP-4, OP-5, OP-13, OP-14), written without the implementation.

Independence: written from the block 5 spec (sections 6.3, 8.3 P1 and P1-use, 9) and the dataset only. The contract's
fields come from the eval's own quotes (880.00, 5 days, valid until 31 Dec 2026), the period, `supplier.csv` (SUP-005's
NET45) and the oracle's taxable value; the order's totals from `block5.Oracle5` (CGST + SGST inside Maharashtra), its
approver from `approval_rule.csv`; the contract's next number from `contract.csv` (one contract, CNT-0001).

On a fresh Apex reset, 23 Nov 2026: P1 to the director's approval: one draft contract CNT-0002 from the award (SUP-005,
rate contract, 1 Jan to 31 Mar 2027, ceiling 7,92,000.00, NET45, made by the buyer, the decision and the RFQ kept), one
draft price RM-08 KGS 880.00 at PLT-CKN, minimum 1, lead 5, version 1; audit `contract.create`. Not usable while draft.
The award mail's `/contract/<token>` page shows the terms and "Email me a code" and no code; accept or decline without a
code is 409 and changes nothing (a forwarded link decides nothing); the code goes to SUP-005's contact only; a wrong code
409 with tries left; activation before acceptance 409; the right code accepts once (accepted at/by, link used, otp row
by address, audit, an activation task); the link again is refused. Activation: the director (award approver) 403, the
buyer (creator) 403, the purchase manager 200. On 4 Jan 2027 production asks for 120 kg (1,05,600.00, the director):
the line takes CNT-0002 at 880.00; the order 1,05,600.00 + 9,504.00 + 9,504.00 = 1,24,608.00 in PO-02 keeps the price
record and waits for the purchase manager, who approves; the contract has 6,86,400.00 left. A link forged for Brightwave
opens nothing; tenant check.
Mutations: activation by the award approver; the contract used before activation or acceptance.
"""

import re
from datetime import date
from decimal import Decimal
from typing import Any

import block1 as b
import block4 as k
import block5 as f
import env
import lib
from block1 import D, js
from lib import Outcome

ID = "E117"
TITLE = "An award becomes a draft rate contract the supplier accepts and a second person activates (AT-02)"
REQUIREMENT = (
    "Tasks 12.3, AT-02, D48, D53, OP-4, OP-5, OP-13, OP-14 (block 5 spec 6.3, P1, P1-use): draft contract and price from "
    "the award with the oracle's fields; acceptance only by signed link plus a code mailed to the letter's address, "
    "once; activation refused before acceptance, for the award approver and the creator, done by a second person; "
    "nothing commits before activation; a later request takes the contract price, its order totals and band from the "
    "oracle, the ceiling's use; tenant check"
)
PREFIX = "e117"
USE_DAY = date(2027, 1, 4)


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
        bw = f.Co5("brightwave", PREFIX, client, box)
        apex = f.Co5("apex", PREFIX, client, box)
        f.start(apex)
        start = apex.mark()
        oracle = f.Oracle5(apex)
        m = k.Mutations()
        pages: list[str] = []
        rfq, code, token = _contract(out, check, apex, oracle, m)
        if code and token:
            _accept(out, check, apex, oracle, m, code, token, pages)
            _activate(check, apex, m, code)
            _use(out, check, apex, oracle, code)
            _forged(check, apex, bw, token)
        m.report(check, out, ["approver_activates", "used_before_active"])
        f.tenant_check(check, apex, bw, [rfq], [code] if code else [], start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        (
            "'Not usable before activation' is read from sourcing.service.contract_options (the existing service a request "
            "line is priced from) on 4 Jan 2027, before and after acceptance and after activation."
        ),
        (
            "The expired-link and decline paths are left to the tests (spec 10.1); this eval covers the code, the forwarded "
            "link and the one-time use."
        ),
        "A forged link is the token with Apex's slug swapped for Brightwave's and the old signature kept.",
    ]
    return out


def next_contract(co: f.Co5) -> str:
    nums = [int(r["code"].split("-")[1]) for r in co.rows("master", "contract")]
    return f"CNT-{max(nums) + 1:04d}"


def options(co: f.Co5, on: date) -> list[str]:
    from p2p.platform import routing
    from p2p.sourcing import service
    from sqlalchemy.orm import Session

    with Session(routing.engine_for(co.slug)) as db:
        return sorted(
            {str(o.contract_code) for o in service.contract_options(db, f.WIRE, "KGS", on, f.LOC)}  # a PriceOption (dataclass)
        )


def _contract(
    out: Outcome, check: b.Checker, co: f.Co5, oracle: f.Oracle5, m: k.Mutations
) -> tuple[str, str, str]:
    buyer, director = co.login("buyer"), co.login("director")
    rfq, r = f.p1_proposal(check, co)
    if not rfq or r is None:
        return rfq, "", ""
    d = co.decision(rfq)
    since, mark = co.mark(), co.last_audit_id()
    r = co.decide(director, rfq, "approve", note="eval: agreed")
    if not check(
        r.status_code == 200,
        f"P1: the director's approval answered {r.status_code} {r.text[:200]}",
    ):
        return rfq, "", ""
    made = co.contracts_of(rfq)
    if not check(
        len(made) == 1,
        f"P1: the approval made {len(made)} contract(s) from {rfq}; expected one (SUP-005)",
    ):
        return rfq, "", ""
    c = made[0]
    code = str(c["code"])
    want_value = oracle.award_value([(f.WIRE, 900, "880.00")], "SUP-005", f.LOC)[
        "taxable"
    ]
    terms = oracle.suppliers["SUP-005"]["payment_terms_code"]
    check(code == next_contract(co), f"P1: the contract is {code}; the next number after the dataset's is "
          f"{next_contract(co)}", "medium")  # fmt: skip
    problems = []
    for col, want in (("supplier_code", "SUP-005"), ("type", "rate_contract"), ("valid_from", f.P_FROM),
                      ("valid_to", f.P_TO), ("payment_terms_code", terms), ("status", "draft"), ("source_rfq", rfq)):  # fmt: skip
        if c.get(col) != want:
            problems.append(f"{col} {c.get(col)} (oracle {want})")
    if D(c.get("max_value")) != want_value:
        problems.append(
            f"max_value {c.get('max_value')} (oracle {want_value}, the taxable value)"
        )
    if not f.same_person(co, c.get("created_by"), buyer):
        problems.append(f"created_by {c.get('created_by')} (oracle the buyer {buyer})")
    if c.get("award_decision_id") != d.get("id"):
        problems.append(
            f"award_decision_id {c.get('award_decision_id')} (decision {d.get('id')})"
        )
    check(not problems, f"P1 contract {code}: {'; '.join(problems)}")
    prices = co.prices_of(code)
    p = prices[0] if len(prices) == 1 else {}
    problems = []
    for col, want in (("item_code", f.WIRE), ("uom", "KGS"), ("location_code", f.LOC), ("version", 1), ("status", "draft"),
                      ("supplier_code", "SUP-005")):  # fmt: skip
        if p.get(col) != want:
            problems.append(f"{col} {p.get(col)} (oracle {want})")
    for col, want in (
        ("price", Decimal("880.00")),
        ("min_qty", Decimal(1)),
        ("lead_days", Decimal(5)),
    ):
        if D(p.get(col)) != want:
            problems.append(f"{col} {p.get(col)} (oracle {want})")
    if p and not f.same_person(co, p.get("created_by"), buyer):
        problems.append(f"created_by {p.get('created_by')} (oracle the buyer)")
    check(
        len(prices) == 1 and not problems,
        f"P1 contract prices {len(prices)}: {'; '.join(problems)}",
    )
    creates = [a for a in co.since("contract.create", mark) if a["entity_id"] == code]
    check(bool(creates) and rfq in str(creates[0]["after"]), f"P1: contract.create audit {[a['after'] for a in creates]}; "
          "expected one with source_rfq", "medium")  # fmt: skip
    api = js(co.call(buyer, "GET", f"/api/contracts/{code}"))
    check(api.get("source_rfq") == rfq and api.get("status") == "draft", f"GET /api/contracts/{code}: source_rfq "
          f"{api.get('source_rfq')}, status {api.get('status')}", "medium")  # fmt: skip
    with f.pinned(USE_DAY):
        used = code in options(co, USE_DAY)
    m.case("used_before_active", "the draft contract on 4 Jan 2027", used, False, True)
    check(
        not used,
        f"P1: the draft {code} is offered for RM-08 on 4 Jan 2027 before acceptance and activation",
    )
    token = co.contract_token(since, to=oracle.contacts["SUP-005"]["email"])
    check(
        token is not None,
        "P1: the award mail to SUP-005 carries no /contract/<token> link",
    )
    out.metrics["contract"] = (
        f"{code}: {c.get('max_value')}, {c.get('payment_terms_code')}, price {p.get('price')}"
    )
    return rfq, code, token or ""


def _state(co: f.Co5, code: str) -> tuple[Any, ...]:
    c = co.contract(code)
    return c.get("status"), c.get("supplier_accepted_at"), c.get("supplier_declined_at")


def _accept(out: Outcome, check: b.Checker, co: f.Co5, oracle: f.Oracle5, m: k.Mutations, code: str, token: str,
            pages: list[str]) -> None:  # fmt: skip
    address = oracle.contacts["SUP-005"]["email"]
    page = co.link_get(f"/contract/{token}")
    pages.append(page.text)
    check(page.status_code == 200 and code in page.text and "880" in page.text and "email me a code" in page.text.lower(),
          f"the contract page answered {page.status_code}; it shows the contract, its price and 'Email me a code': "
          f"{page.text[:200]}")  # fmt: skip
    before = _state(co, code)
    for action in ("accept", "decline"):
        r = co.link_post(
            f"/contract/{token}", action=action, reason="eval: forwarded link"
        )
        pages.append(r.text)
        check(r.status_code == 409 and _state(co, code) == before, f"{action} without a code answered {r.status_code}; "
              f"contract {before} -> {_state(co, code)}; expected 409 and nothing decided (D53)")  # fmt: skip
    r = co.call(co.login("purchasemanager"), "POST", f"/api/contracts/{code}/activate")
    check(r.status_code == 409 and co.contract(code).get("status") == "draft", f"activation before the supplier accepts "
          f"answered {r.status_code} {r.text[:200]}; expected 409 '{code} waits for the supplier to accept its terms'")  # fmt: skip
    since, mark = co.mark(), co.last_audit_id()
    r = co.link_post(f"/contract/{token}/code")
    pages.append(r.text)
    found = co.mails(since)
    codes = [re.search(r"\b(\d{6})\b", str(x.get("body") or "")) for x in found]
    otp = next((c.group(1) for c in codes if c), None)
    check(r.status_code == 200 and len(found) == 1 and str(found[0].get("to", "")).lower() == address.lower() and otp,
          f"asking for a code answered {r.status_code}; mails {[x.get('to') for x in found]}; expected one code mail to "
          f"{address} only")  # fmt: skip
    check(
        address.lower() in r.text.lower(),
        f"the code page does not say where the code went: {r.text[:200]}",
        "low",
    )
    if not otp:
        return
    again = co.link_post(f"/contract/{token}/code")
    check(
        again.status_code == 409,
        f"a second code a moment later answered {again.status_code}; expected 409",
        "low",
    )
    wrong = "000000" if otp != "000000" else "999999"
    r = co.link_post(f"/contract/{token}", action="accept", code=wrong)
    check(r.status_code == 409 and "tries left" in r.text.lower() and _state(co, code) == before,
          f"a wrong code answered {r.status_code}: {r.text[:200]}; expected 409 with tries left")  # fmt: skip
    rows = co.qd(
        "select * from one_time_code where purpose = 'contract_accept' order by id"
    )
    check(bool(rows) and rows[-1].get("user_id") is None and str(rows[-1].get("address") or "").lower() == address.lower(),
          f"the code row: {[(x.get('purpose'), x.get('ref'), x.get('address'), x.get('user_id')) for x in rows]}; "
          "expected purpose contract_accept keyed to the address", "medium")  # fmt: skip
    r = co.link_post(f"/contract/{token}", action="accept", code=otp)
    pages.append(r.text)
    c = co.contract(code)
    check(r.status_code == 200 and c.get("supplier_accepted_at") is not None
          and str(c.get("supplier_accepted_by") or "").lower() == address.lower() and c.get("status") == "draft",
          f"accepting with the code answered {r.status_code}; accepted at {c.get('supplier_accepted_at')} by "
          f"{c.get('supplier_accepted_by')}, status {c.get('status')}; expected accepted by {address}, still draft")  # fmt: skip
    link = co.qd(
        "select * from contract_link where contract_code = :c order by id", c=code
    )
    check(
        bool(link) and link[-1].get("used_at") is not None,
        f"the contract link is not marked used: {link[-1:]}",
        "medium",
    )
    check(
        bool(co.since("contract.supplier_accept", mark)),
        "no contract.supplier_accept audit row",
        "medium",
    )
    otp_rows = co.qd(
        "select action, entity_id from audit_event where action like 'otp.%' and id > :i",
        i=mark,
    )
    check(any(f"contract_accept:{code}" in str(x["entity_id"]) for x in otp_rows), f"no otp.* audit row on "
          f"contract_accept:{code}: {otp_rows[:4]}", "medium")  # fmt: skip
    first = co.login(
        "planthead"
    )  # the first contract.activate holder by code who neither made it nor approved the award
    tasks = co.qd(
        "select * from task where kind = 'contract_activation' and status = 'open'"
    )
    check(any(t.get("assignee") == co.user_id(first) for t in tasks), f"no open contract_activation task for the plant "
          f"head (U07): {[(t.get('assignee'), t.get('title')) for t in tasks]}", "medium")  # fmt: skip
    with f.pinned(USE_DAY):
        used = code in options(co, USE_DAY)
    m.case(
        "used_before_active",
        "the accepted, not yet active contract on 4 Jan 2027",
        used,
        False,
        True,
    )
    check(
        not used, f"{code} is offered for RM-08 after acceptance but before activation"
    )
    r = co.link_post(
        f"/contract/{token}", action="decline", reason="eval: again", code=otp
    )
    check(r.status_code in (400, 404, 409, 410) and co.contract(code).get("status") == "draft",
          f"the used link again answered {r.status_code}; expected refused and the contract unchanged")  # fmt: skip
    check(not any(otp in p for p in pages), "the code is shown on a contract link page")
    out.metrics["acceptance"] = f"{code} by {c.get('supplier_accepted_by')}"


def _activate(check: b.Checker, co: f.Co5, m: k.Mutations, code: str) -> None:
    for who, label in (
        (co.login("director"), "the director (award approver)"),
        (co.login("buyer"), "the buyer (creator)"),
    ):
        r = co.call(who, "POST", f"/api/contracts/{code}/activate")
        if label.startswith("the director"):
            m.case(
                "approver_activates", "the director activates", r.status_code, 403, 200
            )
            check(
                "approved the award" in r.text.lower(),
                f"the director's refusal's words: {r.text[:200]}",
                "low",
            )
        check(r.status_code == 403 and co.contract(code).get("status") == "draft",
              f"activation by {label} answered {r.status_code} {r.text[:200]}; expected 403 (OP-13, D48)")  # fmt: skip
    mark = co.last_audit_id()
    r = co.call(co.login("purchasemanager"), "POST", f"/api/contracts/{code}/activate")
    prices = co.prices_of(code)
    check(r.status_code == 200 and co.contract(code).get("status") == "active" and all(p.get("status") == "active" for p in prices),
          f"activation by the purchase manager answered {r.status_code} {r.text[:200]}; contract "
          f"{co.contract(code).get('status')}, prices {[p.get('status') for p in prices]}")  # fmt: skip
    check(
        bool(co.since("contract.activate", mark)),
        "no contract.activate audit row",
        "medium",
    )


def _use(
    out: Outcome, check: b.Checker, co: f.Co5, oracle: f.Oracle5, code: str
) -> None:
    """P1-use: on 4 Jan 2027 production asks for 120 kg; the director approves; the worker sources and orders."""
    prod, pm = co.login("production"), co.login("purchasemanager")
    want = oracle.award_value([(f.WIRE, 120, "880.00")], "SUP-005", f.LOC)
    rule, approver = oracle.approver(want["total"], frozenset({co.login("buyer")}))
    f.self_check(check, oracle, "P1-use", want, rule, approver)
    with f.pinned(USE_DAY):
        check(
            code in options(co, USE_DAY),
            f"{code} is not offered for RM-08 on 4 Jan 2027 after activation",
        )
        body = {"lines": [{"item_code": f.WIRE, "qty": "120"}], "need_by": "2027-01-20", "location": f.LOC,
                "reason": "E117 winding wire on the new contract"}  # fmt: skip
        r = co.call(prod, "POST", "/api/requisitions", body)
        no = str(js(r).get("id") or "")
        if not check(
            r.status_code == 200 and bool(no),
            f"P1-use: the request was not made: {r.status_code} {r.text[:200]}",
        ):
            return
        co.call(
            prod,
            "POST",
            f"/api/requisitions/{no}/submit",
            {"override_reason": "E117: a separate case"},
        )
        total = D(co.one("select total_value from request where no = :n", n=no))
        check(total == Decimal("105600.00"), f"P1-use: request {no} estimated at {total}; the contract price gives "
              "1,05,600.00", "medium")  # fmt: skip
        status = co.approve(no)
        check(
            status == "approved",
            f"P1-use: request {no} is {status} after its approvers",
            "medium",
        )
        co.work()
        lines = co.qd("select * from po_line where request_no = :r", r=no)
        if not lines:
            b.cli("source", co.slug)
            co.work()
            b.cli("order", co.slug)
            co.work()
            lines = co.qd("select * from po_line where request_no = :r", r=no)
        if not check(
            len(lines) == 1,
            f"P1-use: request {no} gave {len(lines)} order line(s); expected one on {code}",
        ):
            return
        ln = lines[0]
        price_id = next((p["id"] for p in co.prices_of(code)), None)
        check(ln.get("source") == "contract" and ln.get("source_ref") == code and D(ln.get("unit_price")) == Decimal("880.00")
              and ln.get("price_record_id") == price_id,
              f"P1-use line: source {ln.get('source')} {ln.get('source_ref')}, price {ln.get('unit_price')}, price record "
              f"{ln.get('price_record_id')}; expected contract {code} at 880.00, record {price_id}")  # fmt: skip
        po = co.po_row(str(ln["po_no"]))
        got = {x: D(po.get(x)) for x in want}
        check(got == want, f"P1-use order {po.get('no')}: {got}; oracle {want}")
        check(str(po.get("rule_code") or "") in ("", rule) and po.get("status") == "submitted",
              f"P1-use order {po.get('no')}: rule {po.get('rule_code')}, status {po.get('status')}; expected {rule}, "
              "waiting for the purchase manager (not touchless: above the buyer's tier)")  # fmt: skip
        r = co.call(
            pm,
            "POST",
            f"/api/purchase-orders/{po.get('no')}/approve",
            {"comment": "eval"},
        )
        co.work()
        check(r.status_code == 200, f"P1-use: the purchase manager approving {po.get('no')} answered {r.status_code} "
              f"{r.text[:200]}")  # fmt: skip
        use = js(co.call(pm, "GET", f"/api/contracts/{code}"))
        check(D(use.get("used")) == want["taxable"] and D(use.get("remaining")) == Decimal("686400.00"),
              f"{code} use: used {use.get('used')}, left {use.get('remaining')}; expected 1,05,600.00 and 6,86,400.00")  # fmt: skip
    out.metrics["P1-use"] = (
        f"{no} -> {po.get('no')}: {want['total']}, {rule}, {approver}"
    )


def _forged(check: b.Checker, apex: f.Co5, bw: f.Co5, token: str) -> None:
    forged = f.foreign_token(token, apex.slug, bw.slug)
    since = apex.mark()
    page = apex.link_get(f"/contract/{forged}")
    code = apex.link_post(f"/contract/{forged}/code")
    check(
        page.status_code != 200 or "880" not in page.text,
        "a link forged for Brightwave shows Apex's contract",
    )
    check(code.status_code != 200 and not apex.mails(since), f"a link forged for Brightwave mailed a code "
          f"({code.status_code}, {len(apex.mails(since))} mail(s))")  # fmt: skip
