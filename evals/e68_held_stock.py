"""E68: held stock is never used (task 4.1), written without the implementation.

Independence: written from the block 2 spec (sections 3, 9, 11), the plan and the dataset only. The oracle
(`block2.Stock`) replays usable stock from `stock_opening.csv` and `stock_movement.csv` (untouched by block 2), takes the
held lots from `history/stock_held.csv` and sets free stock aside earliest need-by first; a decision on a hold moves its
quantity and hands usable stock on at once; a hold of stock set aside gives back the latest need-by first, then the store
check buys for that place (OP-12).

Scenarios:
- Apex A0 (demo reset, Run store check): block 1's table unchanged by the held lots (RM-07 20 in quarantine, RM-06 8
  blocked); positions and the hold ledger; the holds list (fields, oldest first, filters).
- Q3: the store holds 10 terminal boxes (RM-10) as blocked: all 200 were set aside, so the MRP plan (latest need) gives back
  10 and the store check buys it 10 more (Rs 600 on a new request).
- Q1: H-APX-01 decided 14 usable, 6 blocked: RM-07 usable 74, blocked 6; MO-2 24 / 76, its request Rs 2,08,370; RM-06's
  8 blocked never set aside (MO-1 RM-06 40 / 60). Refused decisions (404, 409, 422, 403) change nothing.
- A count of RM-06 counts usable stock only (the 8 blocked stay 8).
- An inspection receipt: the castings (RM-01) marked for inspection in this company (as E18 does, the dataset's own
  inspection items have no order to receive on), MO-1's request approved and sourced, 50 received: all 50 in quarantine,
  `received_qty` unchanged, nothing set aside; inspected 44 accepted, 6 rejected: 44 usable and set aside for MO-1, 6
  blocked with the reject reason; an invoice for 50 is held.
- Brightwave B0, Q2: issuing the blocked laptop or the quarantined monitors is refused (409); the burn-in passes (2
  usable): engineering's monitors 2 / 2, its request Rs 4,15,200; the blocked laptop never set aside.
Each ends with a second run of the store check (nothing changes) and a tenant check. Mutations: blocked stock counted as
on hand; quarantine set aside before the decision.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import block1 as b
import block2 as s2
import env
import lib
from lib import Outcome

ID = "E68"
TITLE = "Held stock (quarantine, blocked) is never set aside, issued or counted; decisions hand usable stock on at once"
REQUIREMENT = (
    "Task 4.1 (block 2 spec 3, 9.3 Q1-Q3): three qualities; held stock never reserved, issued or counted; an inspection "
    "receipt waits in quarantine and is not invoiceable; rejected at inspection goes to blocked; a manual hold of set-aside "
    "stock gives back the latest need first, then the store check buys; a decision to usable sets stock aside at once; "
    "holds API fields and errors; audit stock.hold / stock.hold_decide; ledger equals the quality view; tenant isolation"
)
PREFIX = "e68"
CKN, BLR = "PLT-CKN", "OFF-BLR"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient

    from p2p.main import app

    out = Outcome(True)
    check = b.Checker(out)
    lib.capture_mail()
    client = TestClient(app)

    apex = s2.Co("apex", PREFIX, client)
    st, rank = s2.start2(apex, check)
    bw = s2.Co("brightwave", PREFIX, client)
    bst, _ = s2.start2(bw, check)

    _a0(out, check, apex, st, rank)
    bw_before = bw.snapshot2()
    _q3(check, apex, st)
    _q1(check, apex, st)
    _count(check, apex, st)
    _hold_errors(check, apex)
    _inspection(check, apex, st)
    s2.second_run(check, apex, "Apex after Q1/Q3/inspection", lambda: apex.store_check())
    s2.tenant_check(check, apex, bw, bw_before)

    apex_before = apex.snapshot2()
    _q2(check, bw, bst)
    s2.second_run(check, bw, "Brightwave after Q2", lambda: bw.store_check())
    s2.tenant_check(check, bw, apex, apex_before)

    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        "Setup, not a spec rule: the castings (RM-01) are marked inspection_required in this company only, so an "
        "inspection receipt can be posted on the touchless contract order (the dataset's inspection items RM-06 and RM-07 "
        "are bought by quotation, which needs quotes and an award first).",
        "Spec gap: whether the store check run by a hold (OP-12) submits its request follows `store_check_submit` "
        "(22 Nov decision for event checks) is not said; Q3's request is checked for its lines and value only.",
        "Spec gap: the truth files' `step` values are the generator's own; each step must equal one state the eval saw, "
        "the last step the last state, compared only for the items of that scenario.",
    ]
    return out


def _a0(out: Outcome, check: b.Checker, co: s2.Co, st: s2.Stock, rank: dict) -> None:
    s2.held_ok(check, co)
    s2.base_ok(check, co, st)
    s2.check_positions(check, "Apex A0", co, st, [("RM-06", CKN), ("RM-07", CKN), ("RM-10", CKN)])
    s2.check_ledger(check, "Apex A0", co)
    status, rows = co.holds()
    check(status == 200, f"GET /api/inventory/holds answered {status}")
    fields = {"id", "item_code", "item_name", "location_code", "quality", "qty", "left_qty", "reason", "reference",
              "source", "gr_no", "held_by", "held_at"}  # fmt: skip
    check(all(fields <= set(h) for h in rows) and len(rows) == 2, f"the holds list has {len(rows)} row(s) with fields "
          f"{sorted(set(rows[0]) if rows else [])}, expected the two dataset lots with {sorted(fields)}")  # fmt: skip
    stamps = [str(h.get("held_at")) for h in rows]
    check(stamps == sorted(stamps), "the holds list is not oldest first", "medium")
    check(all(h.get("source") == "import" for h in rows), f"dataset lots' source {[h.get('source') for h in rows]}, "
          "expected import", "medium")  # fmt: skip
    _, only = co.holds(item_code="RM-07", location=CKN)
    check([h.get("item_code") for h in only] == ["RM-07"], f"holds?item_code=RM-07 lists {only}", "medium")
    # mutations, on the base state itself
    for name, flag in (("blocked stock counted as on hand", "blocked_usable"),
                       ("quarantine set aside before the decision", "quarantine_usable")):  # fmt: skip
        wrong = s2.Stock(co.dataset, s2.Wrong(**{flag: True}), rank)
        wrong.store_check(tag="first")
        s2.mutation(check, out, name, s2.told(st.observe2(), wrong.observe2()))


def _q3(check: b.Checker, co: s2.Co, st: s2.Stock) -> None:
    """Q3: 10 terminal boxes crushed, held as blocked, while all 200 are set aside."""
    s2.scenario_known(check, co, "Q3-hold")
    store = co.role("store")
    before = co.requests_now()
    holds0, audit0 = co.audit_count("stock.hold"), co.audit_count("material_request.release")
    r = co.hold(store, "RM-10", CKN, "10", "blocked", "Crushed in the rack", "Store round 6 Nov")
    body = b.js(r)
    given = st.hold("RM-10", CKN, Decimal(10), "blocked")
    check(r.status_code == 201, f"Q3: the hold answered {r.status_code}: {r.text[:200]}")
    check(given == {b.MRP_CKN: Decimal(10)}, f"Q3: the oracle gives back {given}, spec: the MRP plan 10 (self-check)",
          "medium")  # fmt: skip
    mr_no = dict((lbl, n) for lbl, n, _ in co.mr_labels()).get(b.MRP_CKN)
    released = {k: {i: b.dec(q) for i, q in (v or {}).items()} for k, v in (body.get("released") or {}).items()}
    check(released == {mr_no: {"RM-10": Decimal(10)}}, f"Q3: released {body.get('released')}, expected "
          f"{{{mr_no}: {{RM-10: 10}}}}")  # fmt: skip
    check(body.get("store_check") is True, f"Q3: the hold answered store_check {body.get('store_check')}", "medium")
    s2.agree2(check, "Q3", st, co, only=[(lbl, "RM-10") for lbl in ("MO-1", "MO-2", b.MRP_CKN)])
    s2.check_positions(check, "Q3", co, st, [("RM-10", CKN)])
    new = co.requests_now() - before
    made = {n: co.request_lines(n) for n in new}
    hit = [n for n, lines in made.items() if lines == {"RM-10": Decimal(10)}]
    check(len(hit) == 1 and co.request_head(hit[0]).get("total") == Decimal("600"), f"Q3: new request(s) {made}, "
          "expected one for RM-10 10 worth Rs 600")  # fmt: skip
    check(co.audit_count("stock.hold") == holds0 + 1, "Q3: the hold is not audited once (stock.hold)")
    check(co.audit_count("material_request.release") > audit0, "Q3: the reservation given back is not audited "
          "(material_request.release)")  # fmt: skip
    s2.check_ledger(check, "Q3", co)
    s2.truth_check(check, co, "Q3-hold", [co.observe2()], [st.observe2()], items={"RM-10"})
    s2.truth_positions(check, co, "Q3-hold", st, items={"RM-10"})
    s2.truth_requests(check, co, "Q3-hold", new)


def _q1(check: b.Checker, co: s2.Co, st: s2.Stock) -> None:
    """Q1: the seal lot passes, six with a cut lip."""
    s2.scenario_known(check, co, "Q1-seals")
    store, ap = co.role("store"), co.role("ap")
    lot = co.hold_of("RM-07", "quarantine", CKN)
    rust = co.hold_of("RM-06", "blocked", CKN)
    if not check(lot is not None and rust is not None, f"Q1: dataset holds missing (RM-07 {lot}, RM-06 {rust})"):
        return
    snap = co.snapshot2()
    decides0 = co.audit_count("stock.hold_decide")
    refused = [
        (store, lot["id"], "21", "0", "eval", 409, "more than left"),
        (store, lot["id"], "14", "6", "", 422, "no reason"),
        (store, lot["id"], "0", "0", "eval", 422, "both zero"),
        (store, rust["id"], "0", "1", "eval", 422, "to_blocked on a blocked hold"),
        (store, 99999999, "1", "0", "eval", 404, "no such hold"),
        (ap, lot["id"], "14", "6", "eval", 403, "AP has no stock.quality"),
    ]
    for by, hid, u, bl, why, want, what in refused:
        r = co.decide(by, hid, u, bl, why)
        check(r.status_code == want, f"Q1: a decision with {what} answered {r.status_code}, expected {want}")
    check(co.snapshot2() == snap, "Q1: refused decisions changed something")
    mo2 = co.request_of("MO-2")
    r = co.decide(store, lot["id"], "14", "6", "Seal face check passed; six with a cut lip")
    body = b.js(r)
    st.decide("RM-07", CKN, "quarantine", Decimal(14), Decimal(6))
    check(r.status_code == 200, f"Q1: the decision answered {r.status_code}: {r.text[:200]}")
    mo2_no = dict((lbl, n) for lbl, n, _ in co.mr_labels()).get("MO-2")
    now = {k: {i: b.dec(q) for i, q in (v or {}).items()} for k, v in (body.get("reserved_now") or {}).items()}
    check(b.dec(body.get("left_qty")) == 0 and body.get("blocked_hold_id") is not None
          and now == {mo2_no: {"RM-07": Decimal(14)}}, f"Q1: the decision answered {body}, expected left 0, a blocked "
          f"hold and reserved_now {{{mo2_no}: {{RM-07: 14}}}}")  # fmt: skip
    check(co.audit_count("stock.hold_decide") == decides0 + 1, "Q1: the decision is not audited once")
    obs = st.observe2()
    check((obs[("MO-2", "RM-07")]["reserved"], obs[("MO-2", "RM-07")]["requested"]) == (Decimal(24), Decimal(76))
          and st.request_value("MO-2", "first") == Decimal("208370"), "Q1: the oracle does not give the spec's MO-2 "
          "RM-07 24 / 76 and Rs 2,08,370 (self-check)", "medium")  # fmt: skip
    s2.agree2(check, "Q1", st, co, only=[(lbl, i) for lbl in ("MO-1", "MO-2", b.MRP_CKN) for i in ("RM-06", "RM-07")])
    s2.check_positions(check, "Q1", co, st, [("RM-06", CKN), ("RM-07", CKN)])
    got = sum((co.request(n).get("total", b.Z) for n in mo2), b.Z)
    check(b.money(got) == Decimal("208370"), f"Q1: MO-2's request is worth {got}, expected 2,08,370")
    check(co.decide(store, lot["id"], "1", "0", "again").status_code == 409, "Q1: deciding a closed hold again is not "
          "409")  # fmt: skip
    s2.check_ledger(check, "Q1", co)
    s2.truth_check(check, co, "Q1-seals", [co.observe2()], [st.observe2()], items={"RM-06", "RM-07"})
    s2.truth_positions(check, co, "Q1-seals", st, items={"RM-06", "RM-07"})


def _count(check: b.Checker, co: s2.Co, st: s2.Stock) -> None:
    """A count counts usable stock: 41 bearings found beside the 8 rusty ones on hold."""
    r = co.call(co.role("store"), "POST", "/api/inventory/count",
                {"item_code": "RM-06", "location": CKN, "counted_qty": "41", "reason": "Cycle count"})  # fmt: skip
    check(r.status_code == 200, f"count of RM-06 answered {r.status_code}: {r.text[:200]}")
    st.count("RM-06", CKN, Decimal(41))
    s2.check_positions(check, "count RM-06", co, st, [("RM-06", CKN)])
    s2.agree2(check, "count RM-06", st, co, only=[(lbl, "RM-06") for lbl in ("MO-1", "MO-2", b.MRP_CKN)])
    s2.check_ledger(check, "count RM-06", co)


def _hold_errors(check: b.Checker, co: s2.Co) -> None:
    store, ap = co.role("store"), co.role("ap")
    snap = co.snapshot2()
    for args, want, what in (
        (("RM-99", CKN, "1", "blocked", "x"), 404, "an unknown item"),
        (("RM-05", "PLT-XXX", "1", "blocked", "x"), 404, "an unknown place"),
        (("RM-05", CKN, "1000", "blocked", "x"), 409, "more than usable on hand"),
        (("RM-05", CKN, "1", "usable", "x"), 422, "quality usable"),
        (("RM-05", CKN, "0", "blocked", "x"), 422, "qty 0"),
        (("RM-05", CKN, "1", "blocked", ""), 422, "no reason"),
    ):
        r = co.hold(store, *args)
        check(r.status_code == want, f"a hold with {what} answered {r.status_code}, expected {want}")
    r = co.hold(ap, "RM-05", CKN, "1", "blocked", "x")
    check(r.status_code == 403, f"AP holding stock answered {r.status_code}, expected 403")
    check(co.snapshot2() == snap, "refused holds changed something")


def _inspection(check: b.Checker, co: s2.Co, st: s2.Stock) -> None:
    """An inspection receipt waits in quarantine and is not invoiceable; inspection splits it."""
    from sqlalchemy import text

    co.db.execute(text("update item set inspection_required = true where code = 'RM-01'"))
    co.db.commit()
    for no in co.request_of("MO-1"):
        co.approve(no)
    co.source()
    st.mark_buyer(co.with_buyer())
    found = co.po_of("MO-1", "RM-01")
    if not check(found is not None, "inspection: no purchase order for MO-1's castings after sourcing"):
        return
    po, line = found
    status = co.advance_po(po)
    check(status == "sent", f"inspection: the castings order {po} is {status}, expected sent (touchless)", "medium")
    received0 = b.D(co.one("select received_qty from po_line where po_no = :p and line_no = :l", p=po, l=line))
    r = co.receive(po, line, "50", lib.TODAY)
    body = b.js(r)
    check(r.status_code == 200 and body.get("pending_inspection"), f"inspection: the receipt answered {r.status_code} "
          f"{str(body)[:200]}, expected goods pending inspection")  # fmt: skip
    gr = body.get("receipt")
    st.held[("RM-01", CKN, "quarantine")] += Decimal(50)
    s2.check_positions(check, "inspection receipt", co, st, [("RM-01", CKN)])
    received1 = b.D(co.one("select received_qty from po_line where po_no = :p and line_no = :l", p=po, l=line))
    check(received1 == received0, f"inspection: received_qty {received1} after the receipt, expected {received0}")
    hold = co.q(
        "select source, gr_no, quality, left_qty from stock_hold where item_code = 'RM-01' and closed_at is null"
    )
    check([(h[0], h[1], h[2], b.D(h[3])) for h in hold] == [("receipt", gr, "quarantine", Decimal(50))],
          f"inspection: RM-01 holds {hold}, expected one receipt hold of 50 in quarantine for {gr}")  # fmt: skip
    s2.agree2(check, "inspection receipt", st, co, only=[("MO-1", "RM-01")])
    s2.check_ledger(check, "inspection receipt", co)
    r = co.call(co.role("quality"), "POST", f"/api/receipts/{gr}/inspect",
                {"lines": [{"line_no": line, "qty_accepted": "44", "qty_rejected": "6", "reason": "Blow holes"}]})  # fmt: skip
    check(r.status_code == 200, f"inspection: inspect answered {r.status_code}: {r.text[:200]}")
    st.held[("RM-01", CKN, "quarantine")] -= Decimal(50)
    st.held[("RM-01", CKN, "blocked")] += Decimal(6)
    st.arrive("MO-1", "RM-01", Decimal(44))
    s2.check_positions(check, "after inspection", co, st, [("RM-01", CKN)])
    s2.agree2(check, "after inspection", st, co, fields=("need", "reserved", "issued"), only=[("MO-1", "RM-01")])
    received2 = b.D(co.one("select received_qty from po_line where po_no = :p and line_no = :l", p=po, l=line))
    check(received2 == received0 + 44, f"inspection: received_qty {received2}, expected {received0 + 44}")
    blocked = co.q("select reason, left_qty from stock_hold where item_code = 'RM-01' and quality = 'blocked' and "
                   "closed_at is null")  # fmt: skip
    check(len(blocked) == 1 and "blow holes" in str(blocked[0][0]).lower() and b.D(blocked[0][1]) == 6,
          f"inspection: blocked RM-01 holds {blocked}, expected 6 with the reject reason")  # fmt: skip
    after = co.q("select after::text from audit_event where action = 'grn.post' order by id desc limit 1")
    check(bool(after) and "quarantine" in str(after[0][0]), "inspection: the grn.post audit row does not record the "
          "quarantine", "medium")  # fmt: skip
    # block 9 (spec 9.5 and 10.1): the blocked six go back with a return document, and the line and budget stay as they are
    store = co.role("store")
    blocked_id = co.one(
        "select id from stock_hold where item_code = 'RM-01' and quality = 'blocked' and closed_at is null"
    )
    r = co.call(store, "POST", "/api/returns", {
        "reason": "eval: the six with blow holes go back",
        "lines": [{"hold_id": int(blocked_id), "qty": "6"}]})  # fmt: skip
    check(r.status_code == 201, f"inspection: the return answered {r.status_code}: {r.text[:200]}")
    rtn = b.js(r).get("return_no") if r.status_code == 201 else None
    if rtn:
        r = co.call(store, "POST", f"/api/returns/{rtn}/dispatch",
                    {"challan": "RDC-E68", "vehicle": "MH 12 XX 0000"})  # fmt: skip
        check(r.status_code == 200, f"inspection: dispatching {rtn} answered {r.status_code}: {r.text[:200]}")
        st.held[("RM-01", CKN, "blocked")] -= Decimal(6)
        open_blocked = co.one("select coalesce(sum(left_qty), 0) from stock_hold where item_code = 'RM-01' "
                              "and quality = 'blocked' and closed_at is null")  # fmt: skip
        check(b.D(open_blocked) == 0, f"inspection: blocked RM-01 left {open_blocked} after the return, expected 0")
        closed = co.one("select closed_by_doc from stock_hold where id = :i", i=int(blocked_id))
        check(closed == rtn, f"inspection: the returned hold was closed by {closed}, expected {rtn}")
        s2.check_positions(check, "after the return", co, st, [("RM-01", CKN)])
        check(co.one("select received_qty from po_line where po_no = :p and line_no = :l", p=po, l=line)
              == received0 + 44, "inspection: the return changed what was received")  # fmt: skip
        check(co.one("select count(*) from recovery_claim") == 0,
              "inspection: the return raised a claim although nothing was invoiced beyond the goods")  # fmt: skip
    s2.check_ledger(check, "after inspection", co)
    supplier, price = co.q("select p.supplier_code, pl.unit_price from po p join po_line pl on pl.po_no = p.no where "
                           "p.no = :p and pl.line_no = :l", p=po, l=line)[0]  # fmt: skip
    r = co.call(co.role("ap"), "POST", "/api/invoices", {
        "supplier_code": supplier, "number": f"EV68-{po[-5:]}", "date": date(2026, 11, 6).isoformat(),
        "lines": [{"po_no": po, "po_line_no": line, "qty": "50", "unit_price": str(price)}]})  # fmt: skip
    check(r.status_code in (200, 201) and b.js(r).get("status") == "blocked", f"inspection: an invoice for 50 when 44 "
          f"were accepted answered {r.status_code} {str(b.js(r))[:200]}, expected held (blocked)")  # fmt: skip


def _q2(check: b.Checker, co: s2.Co, st: s2.Stock) -> None:
    """Q2 (Brightwave): the burn-in passes; held stock can never be issued."""
    s2.scenario_known(check, co, "Q2-monitors")
    s2.held_ok(check, co)
    s2.base_ok(check, co, st)
    s2.check_positions(check, "Brightwave B0", co, st, [("LP-01", BLR), ("MN-01", BLR)])
    s2.check_ledger(check, "Brightwave B0", co)
    store = co.role("store")
    snap = co.snapshot2()
    limit = st.manual_limit("LP-01", BLR)
    for item, qty in (("LP-01", limit + 1), ("MN-01", Decimal(1))):
        r = co.call(store, "POST", "/api/inventory/issue",
                    {"item_code": item, "location": BLR, "qty": str(qty), "reference": "eval"})  # fmt: skip
        check(r.status_code == 409, f"Q2: issuing {fq_(qty)} {item} (held stock beside it) answered {r.status_code}")
    check(co.snapshot2() == snap, "Q2: refused issues changed something")
    lot = co.hold_of("MN-01", "quarantine", BLR)
    if not check(lot is not None, "Q2: H-BWT-01 is not in the holds list"):
        return
    eng = co.request_of(b.ENG)
    r = co.decide(store, lot["id"], "2", "0", "Burn-in test passed")
    check(r.status_code == 200, f"Q2: the decision answered {r.status_code}: {r.text[:200]}")
    st.decide("MN-01", BLR, "quarantine", Decimal(2), b.Z)
    obs = st.observe2()
    check((obs[(b.ENG, "MN-01")]["reserved"], obs[(b.ENG, "MN-01")]["requested"]) == (Decimal(2), Decimal(2)),
          "Q2: the oracle does not give engineering's monitors 2 / 2 (self-check)", "medium")  # fmt: skip
    # only the pairs the joiner kits have (the admin kit has no LP-02, engineering's no LP-01): a pair absent from both
    # the oracle and the platform is no finding
    pairs = [(m, i) for m in (b.ADM, b.ENG) for i in ("MN-01", "LP-01", "LP-02") if (m, i) in obs]
    s2.agree2(check, "Q2", st, co, only=pairs)
    s2.check_positions(check, "Q2", co, st, [("LP-01", BLR), ("MN-01", BLR)])
    got = sum((co.request(n).get("total", b.Z) for n in eng), b.Z)
    check(b.money(got) == Decimal("415200"), f"Q2: engineering's request is worth {got}, expected 4,15,200")
    lp01 = sum((ln.reserved for ln in st.lines if ln.item == "LP-01"), b.Z)
    got_lp01 = b.D(co.one("select coalesce(sum(reserved_qty), 0) from material_request_line where item_code = 'LP-01'"))
    check(got_lp01 == lp01 and got_lp01 <= b.D(co.one(
        "select on_hand from stock_level where item_code = 'LP-01' and location_code = :l", l=BLR)),
        f"Q2: LP-01 set aside {got_lp01}, expected {lp01} (the blocked laptop never)")  # fmt: skip
    s2.check_ledger(check, "Q2", co)
    s2.truth_check(check, co, "Q2-monitors", [co.observe2()], [st.observe2()], items={"MN-01", "LP-01"})
    s2.truth_positions(check, co, "Q2-monitors", st, items={"MN-01", "LP-01"})


def fq_(q: Decimal) -> str:
    return b.fq(q)
