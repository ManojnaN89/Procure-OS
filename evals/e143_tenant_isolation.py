"""E143: tenant isolation of the supplier's answer (rule 6, D53, spec 11 tenant safety, K10), written without the
implementation.

Independence: written from the block 8 spec (sections 5 8.1-8.2, 8, 9 P and K10, 11) and the two datasets only. The
oracle is which company each order, contact and code belongs to (`supplier_contact.csv` of each dataset): Apex's P to
sales@deccancastings.example, Brightwave's B (2 LP-01 at OFF-BLR on its CNT-0001, approved by its CFO) to
sales@bangaloreit.example.

Given fresh Apex and Brightwave resets on Mon 23 Nov 2026, P and B each sent with a respond link:
- P's token with the company value changed to Brightwave's: (400) not valid; no code can be asked on it and nothing
  is accepted; nothing written on P.
- a code Apex's contact asked on P's link, typed on B's link: refused, B unchanged; B accepted with its own contact's
  code (the link works); then P accepted with its code.
- Brightwave's buyer: GET /api/purchase-orders/<P> and its versions 404; recording an acknowledgement of P or sending
  it again: refused, nothing written; no Apex acknowledgement, task or exceptions row in Brightwave's company; Apex's
  `one_time_code` rows are not in Brightwave's database (and Brightwave's not in Apex's).
Ends with block 7's tenant check both ways.
Mutations: the tenant not in the token check; codes shared across companies.
"""

from typing import Any

import block1 as b
import block4 as k
import block7 as t
import block8 as e
import env
from block1 import js
from lib import Outcome, capture_mail

ID = "E143"
TITLE = "The supplier's answer never crosses companies: forged tokens, foreign codes and other buyers are refused"
REQUIREMENT = (
    "Rule 6, D53 (block 8 spec 8.1-8.2, K10, section 11): the company is in the signed token; a code works only on the "
    "link and company it was asked for; another company's buyer gets 404 for the order and cannot record or resend; "
    "codes, acknowledgements, tasks and exception rows stay in their own company's database"
)
PREFIX = "e143"
BW_CONTACT = "sales@bangaloreit.example"  # Brightwave's SUP-001


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        e.pin(e.TODAY)
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient

    from p2p.main import app
    from p2p.platform import mail

    out = Outcome(True)
    check = b.Checker(out)
    saved = mail.send_email
    box = capture_mail()
    notes: list[str] = []
    try:
        client = TestClient(app)
        bw = e.Co8("brightwave", PREFIX, client, box)
        apex = e.Co8("apex", PREFIX, client, box)
        e.start(apex)
        _k10(out, check, apex, bw, notes)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += sorted(set(notes)) + [
        ("The forged token swaps the company's slug inside the signed values and keeps the signature (block 5's "
         "foreign_token): the eval cannot sign, so a token for the other company that verifies is impossible to make."),
        "B is made as E131 makes it: Brightwave's employee asks for 2 LP-01 at OFF-BLR, approved through the chain.",
        "Refusals across companies may be 403 or 404; either passes, a 200 or a write never does.",
    ]
    return out


def _b_order(check: b.Checker, co: e.Co8) -> tuple[str, str | None]:
    oracle = t.Oracle7(co)
    po = co.k_order(check, t.LAPTOP, 2, t.NEED_BY, "B", loc=t.BLR, by=co.login("employee"))
    if not po:
        return "", None
    total = e.D(co.po_row(po).get("total"))
    _rule, who = oracle.approver(total, frozenset({co.buyer()}))
    if co.open_steps(po):
        k.approve_ok(check, co, who, po, 1, "B approve version 1")
    tok, _mail = co.send8(check, po, "B send")
    return po, tok


def _k10(out: Outcome, check: b.Checker, apex: e.Co8, bw: e.Co8, notes: list[str]) -> None:
    oracle = e.Oracle8(apex)
    m = k.Mutations()
    _rule, who = oracle.approver(oracle.p()["total"], frozenset({apex.buyer()}))
    a_start, b_start = apex.mark(), bw.mark()
    po = apex.p_order(check, "P", who, notes)
    lp, _ = apex.send8(check, po, "P send") if po else (None, {})
    e.pin(e.TODAY)
    bo, lb = _b_order(check, bw)
    if not (po and lp and bo and lb):
        check(False, f"K10: precondition: P {po} ({lp is not None}), B {bo} ({lb is not None})", "medium")
        return

    # P's token with the company value changed
    forged = t.f.foreign_token(lp, apex.slug, bw.slug)
    before_p, before_b = apex.fp(po), bw.fp(bo)
    r = apex.supplier_page(forged)
    m.case("tenant_not_in_token", "K10 P's token with Brightwave's slug", r.status_code in e.REFUSED, True, False)
    check(r.status_code == 400 and e.says(r, "not valid"), f"K10: the forged token's page answered {r.status_code} "
          f"{e.page(r)[:150]}; the spec says (400) not valid")  # fmt: skip
    since = apex.mark()
    r = apex.link_post(f"/supplier/{forged}/code")
    check(r.status_code in e.REFUSED and not apex.code_mails(since), f"K10: Email me a code on the forged token "
          f"answered {r.status_code} with {len(apex.code_mails(since))} code mail(s)")  # fmt: skip
    r = apex.answer(forged, "accept", "123456", **apex.date_fields({1: t.NEED_BY}))
    check(r.status_code in e.REFUSED, f"K10: accepting on the forged token answered {r.status_code}")
    e.unchanged(check, apex, po, before_p, "K10 the forged token (P)")
    e.unchanged(check, bw, bo, before_b, "K10 the forged token (B)")

    # Apex's code typed on Brightwave's link
    r, code_p, mails = apex.ask_code(lp)
    check(r.status_code == 200 and code_p is not None and [str(x.get("to", "")).lower() for x in mails] == [e.SALES],
          f"K10: Email me a code on P answered {r.status_code}, mails to {[x.get('to') for x in mails]}")  # fmt: skip
    r = bw.answer(lb, "accept", code_p or "000000", **bw.date_fields({1: t.NEED_BY}))
    m.case("codes_shared", "K10 Apex's code typed on B's link", (r.status_code, bw.po_row(bo).get("acknowledged_version")),
           (409, None), (200, 1))  # fmt: skip
    e.refused(check, r, 409, None, "K10 Apex's code on B's link")
    e.unchanged(check, bw, bo, before_b, "K10 Apex's code on B's link")
    e.not_acked(check, bw, bo, "K10 Apex's code on B's link")
    r = bw.accept_code(check, lb, {1: t.NEED_BY}, "K10 B with its own code", to=BW_CONTACT)
    check(r.status_code == 200 and bw.po_row(bo).get("acknowledged_version") == 1, f"K10: B accepted with its own "
          f"contact's code answered {r.status_code}; acknowledged_version {bw.po_row(bo).get('acknowledged_version')}")  # fmt: skip
    r = apex.answer(lp, "accept", code_p or "000000", **apex.date_fields({n: nb for n, _i, _q, nb in e.P_LINES}))
    check(r.status_code == 200 and apex.po_row(po).get("acknowledged_version") == 1, f"K10: P accepted with its own "
          f"code answered {r.status_code}; acknowledged_version {apex.po_row(po).get('acknowledged_version')}")  # fmt: skip

    # Brightwave's buyer and database
    by = bw.buyer()
    before_p = apex.fp(po)
    for path in (f"/api/purchase-orders/{po}", f"/api/purchase-orders/{po}/versions"):
        r = bw.call(by, "GET", path)
        check(r.status_code == 404, f"K10: Brightwave's buyer GET {path} answered {r.status_code}")
    for path, body in ((f"/api/purchase-orders/{po}/acknowledge", {"version": 1, "lines": {"1": "2026-12-07"},
                                                                   "note": "K10: from the other company"}),
                       (f"/api/purchase-orders/{po}/resend-link", {})):  # fmt: skip
        r = bw.call(by, "POST", path, body)
        bw.work()
        check(r.status_code in (403, 404), f"K10: Brightwave's buyer POST {path} answered {r.status_code}")
    e.unchanged(check, apex, po, before_p, "K10 Brightwave's buyer on P")
    if not bw.one("select count(*) from po where no = :n", n=po):  # else the other's own order of the same number
        check(not bw.qd("select id from po_version where po_no = :n", n=po), f"K10: Brightwave's database holds "
              f"version rows of {po}")  # fmt: skip
    a_codes = apex.codes()
    b_codes = bw.codes()
    check(bool(a_codes) and all(str(x.get("ref") or "").startswith(po) for x in a_codes) and not any(
        str(x.get("address") or "").lower() == e.SALES for x in b_codes), f"K10: Apex's codes "
          f"{[(x.get('ref'), x.get('address')) for x in a_codes]}, Brightwave's {[(x.get('ref'), x.get('address')) for x in b_codes]}"
          "; each company's codes stay in its own database")  # fmt: skip
    check(not any(str(x.get("address") or "").lower() == BW_CONTACT for x in a_codes), "K10: Apex's database holds a code "
          f"for {BW_CONTACT}")  # fmt: skip
    r = apex.call(apex.buyer(), "GET", f"/api/purchase-orders/{bo}")
    check(r.status_code == 404 or (r.status_code == 200 and js(r).get("supplier_code") != bw.po_row(bo).get(
        "supplier_code") or e.D(js(r).get("total")) != e.D(bw.po_row(bo).get("total"))),
          f"K10: Apex's buyer reads Brightwave's {bo} ({r.status_code})")  # fmt: skip
    m.report(check, out, ["tenant_not_in_token", "codes_shared"])
    e.tenant_check8(check, apex, bw, [po], [lp], a_start)
    e.tenant_check8(check, bw, apex, [bo], [lb], b_start)
    out.metrics["K10"] = {"P": po, "B": bo, "forged token page": apex.supplier_page(forged).status_code}
