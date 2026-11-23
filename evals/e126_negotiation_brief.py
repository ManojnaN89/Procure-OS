"""E126: the ranking explanation and the negotiation brief carry the engine's references and target (task 11b.2, Base
s5.2 'Negotiation'), written without the implementation.

Independence: written from the block 6 spec (sections 6.3, 6.4, 8.3 N1-N4, 9, 10.2) and the dataset only. The oracle
(`block6.Oracle6.brief`) reads the last price paid at the plant from `history/po.csv` and `po_line.csv` (RM-08 at
PLT-CKN: 905.91 on PO-CKN-2627-00199 of 5 Oct 2026), the counting benchmarks from `market_benchmark.csv` (October
897.00 unverified until the eval verifies it; September 899.00 is 54 days old on 23 Nov, past 45), and tests each
reference with the threshold rule v1 (5%, Rs 10 per unit, Rs 25,000 saving, all above): gap per unit 4 places, percent
on the L1 price 4 places, saving to paise; the target is the lowest triggering reference. The spec's figures are a
self-check; `truth/truth_quotes_selection.csv` is compared.

Given a fresh Apex reset on Mon 23 Nov 2026, three RFQs as R (RM-08 at PLT-CKN, confirmed): R1 and R2 for 500 KGS, R4
for 400 KGS; on each SUP-005 quotes 960.00, SUP-003 975.00, SUP-001 990.00 (the third completes the set):
- N1 (R1): a `ranking` record ("Line 1, Copper winding wire: ..." and "3 of 3 invited suppliers quoted.") and a brief
  (key <rfq>/1): last paid 905.91, gap 54.0900, 5.6344%, saving 27,045.00, target 905.91, saving at target 27,045.00,
  L2 975.00 from SUP-003, no market reference; the template exactly. No supplier mail.
- N4 (R4): no brief (21,636.00 is not above 25,000.00).
- The buyer dismisses R2's brief (`POST /api/recommendations/{id}/decide` used false: 200 and recorded; again 409; by
  production 403; a ranking record 409).
- N2: the purchase manager verifies the October benchmark: R1's brief superseded with the market reference 897.0000,
  gap 63.0000, 6.5625%, saving 31,500.00, target 897.00, saving at target 31,500.00, the history reference as N1.
- N3: SUP-005 revises to 900.00 on R1: the brief withdrawn, the ranking superseded, no new brief.
Ends with a tenant check.
Mutations: the target from the L2 price (or a model); a brief below the saving floor.
"""

import re
from datetime import date
from decimal import Decimal
from typing import Any

import block1 as b
import block4 as k
import block6 as s
import env
import lib
from block1 import js
from lib import Outcome

ID = "E126"
TITLE = "The ranking explanation and the negotiation brief carry the engine's references and target"
REQUIREMENT = (
    "Task 11b.2 (block 6 spec 6.3, 6.4): a ranking record per RFQ in the spec's words; a negotiation brief when L1 is "
    "above the last price paid or the market beyond every threshold, with figures and target = the oracle and the "
    "truth file, superseded by a new benchmark, withdrawn when L1 falls within; none below the saving floor; used or "
    "dismissed by the buyer; never mails; tenant check"
)
PREFIX = "e126"
OCT = date(2026, 10, 31)


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        s.pin(lib.TODAY)
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
        bw = s.Co6("brightwave", PREFIX, client, box)
        apex = s.Co6("apex", PREFIX, client, box)
        s.pin(s.TODAY)
        apex.work()
        start = apex.mark()
        oracle = s.Oracle6(apex)
        m = k.Mutations()
        _, r1 = apex.r_rfq(check, "N1")
        _, r2 = apex.r_rfq(check, "N1 copy")
        _, r4 = apex.r_rfq(check, "N4", qty=400)
        since = apex.mark()
        for rfq in (r1, r2, r4):
            if rfq:
                _quotes(check, apex, rfq)
        apex.work()
        n1 = oracle.brief(("SUP-005", Decimal("960.00")), ("SUP-003", Decimal("975.00")), s.WIRE, s.LOC, Decimal(500),
                          s.TODAY)  # fmt: skip
        if r1 and check(n1 is not None, "N1: the oracle finds no brief (precondition)", "medium"):
            figs = s.brief_figures(n1)
            s.self_check(check, "N1", figs)
            s.compare_truth(check, apex, "N1", figs)
            _ranking(check, apex, oracle, r1, "N1")
            _brief(out, check, apex, oracle, m, r1, n1, "N1")
        if r4:
            _n4(check, apex, oracle, m, r4)
        if r2:
            _decide(check, apex, r2)
        check(not apex.supplier_mails(since), f"no outbound contact: {len(apex.supplier_mails(since))} supplier mail(s) "
              "went while quotes were ranked and briefs written")  # fmt: skip
        if r1 and n1:
            bench = apex.verify_benchmark(check, s.WIRE, OCT, "N2")
            n2 = oracle.brief(("SUP-005", Decimal("960.00")), ("SUP-003", Decimal("975.00")), s.WIRE, s.LOC,
                              Decimal(500), s.TODAY, [bench] if bench else [])  # fmt: skip
            if bench and n2:
                figs = s.brief_figures(n2)
                s.self_check(check, "N2", figs)
                s.compare_truth(check, apex, "N2", figs)
                old = apex.live_rec("negotiation_brief", f"{r1}/1")
                _brief(out, check, apex, oracle, m, r1, n2, "N2")
                new = apex.live_rec("negotiation_brief", f"{r1}/1")
                check(bool(new) and new.get("id") != old.get("id"), "N2: the brief was not superseded by a new record",
                      "medium")  # fmt: skip
            _n3(check, apex, oracle, r1)
        check(not apex.supplier_mails(since), "no outbound contact: a supplier was mailed by a brief")
        m.report(check, out, ["target_from_l2", "brief_below_floor"])
        s.f.tenant_check(check, apex, bw, [r1, r2, r4], [], start)
        s.tenant_tasks(check, apex, bw, [r1, r2, r4])
        out.metrics["RFQs"] = {"N1-N3": r1, "dismissed": r2, "N4": r4}
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        (
            "Spec gap: the figures' JSON keys are not named; the eval checks the record holds each number and that the "
            "summary is the spec's sentence with every number read by its value (₹ or Rs; 05 or 5)."
        ),
        "Spec gap: the ranking's per-line <why> words are the comparison's existing words; only the frame is checked.",
    ]
    return out


def _quotes(check: b.Checker, co: s.Co6, rfq: str) -> None:
    for sup, price in (("SUP-005", "960.00"), ("SUP-003", "975.00"), ("SUP-001", "990.00")):
        r = co.quote6(rfq, sup, price)
        check(r.status_code == 200, f"{rfq}: {sup}'s quote {price}: {r.status_code} {r.text[:200]}")


def _ranking(check: b.Checker, co: s.Co6, oracle: s.Oracle6, rfq: str, label: str) -> dict[str, Any]:
    rec = co.live_rec("ranking", rfq)
    if not check(bool(rec), f"{label}: no ranking record keyed {rfq} after every invited supplier quoted"):
        return {}
    summary = s.norm(rec.get("summary"))
    head = s.norm(f"Line 1, {oracle.item_name(s.WIRE)}:")
    check(summary.startswith(head) and summary.rstrip(".").endswith("3 of 3 invited suppliers quoted"),
          f"{label}: the ranking summary {rec.get('summary')!r}; the spec says 'Line 1, <item>: <why>. ... 3 of 3 "
          "invited suppliers quoted.'", "medium")  # fmt: skip
    missing = s.held(rec["engine_figures"], "960.00", "975.00", "990.00")
    check(not missing, f"{label}: the ranking figures lack the offers {missing}", "medium")
    return rec


def _brief(out: Outcome, check: b.Checker, co: s.Co6, oracle: s.Oracle6, m: k.Mutations, rfq: str,
           br: dict[str, Any], label: str) -> None:  # fmt: skip
    rec = co.live_rec("negotiation_brief", f"{rfq}/1")
    if not check(bool(rec), f"{label}: no negotiation brief keyed {rfq}/1"):
        return
    figs = s.brief_figures(br)
    nums = [v for kk, v in figs.items() if kk not in ("last_paid_order", "last_paid_date")]
    missing = s.held(rec["engine_figures"], *nums)
    check(not missing, f"{label}: the brief's figures lack {missing}: {str(rec['engine_figures'])[:400]}")
    check(figs["last_paid_order"] in str(rec["engine_figures"]), f"{label}: the brief does not name the order "
          f"{figs['last_paid_order']}", "medium")  # fmt: skip
    if "market" not in figs:
        check(Decimal(897) not in {x.normalize() for x in s.b3.record_numbers(rec["engine_figures"])},
              f"{label}: the brief holds the October benchmark 897.00, which does not count (unverified)")  # fmt: skip
    want = oracle.brief_text(br)
    check(s.canon(rec.get("summary")) == s.canon(want), f"{label}: the brief differs from the spec's words:\n  got:  "
          f"{rec.get('summary')}\n  want: {want}")  # fmt: skip
    asked = re.search(r"ask for rs\s?([\d,]+(?:\.\d+)?)", s.norm(rec.get("summary")))
    got_target = s.D(asked.group(1).replace(",", "")) if asked else None
    wrong = oracle.brief(br["l1"], br["l2"], s.WIRE, s.LOC, br["qty"], s.TODAY,
                         [r for r in _counted(oracle, br)], target_rule="l2")  # fmt: skip
    m.case("target_from_l2", f"{label}: the brief's target", got_target, br["target"], wrong["target"] if wrong else None)
    check(got_target == br["target"], f"{label}: the brief asks for {got_target}; the oracle's target {br['target']}")
    out.metrics[f"{label} brief"] = rec.get("summary")


def _counted(oracle: s.Oracle6, br: dict[str, Any]) -> list[dict[str, str]]:
    """The market reference of a brief as an extra benchmark row (so the mutation's brief sees the same references)."""
    return [{"item_code": s.WIRE, "price": str(r["price"]), "uom": br["uom"], "effective_date": r["date"].isoformat(),
             "region_code": "MH", "source_name": r["source"], "status": "verified"}
            for r in br["refs"] if r["kind"] == "market"]  # fmt: skip


def _n4(check: b.Checker, co: s.Co6, oracle: s.Oracle6, m: k.Mutations, rfq: str) -> None:
    br = oracle.brief(("SUP-005", Decimal("960.00")), ("SUP-003", Decimal("975.00")), s.WIRE, s.LOC, Decimal(400),
                      s.TODAY)  # fmt: skip
    last = oracle.last_paid(s.WIRE, s.LOC, s.TODAY)
    saving = s.money((Decimal("960.00") - last["price"]) * 400) if last else None
    s.self_check(check, "N4", {"saving": str(saving)})
    check(br is None, "N4: the oracle writes a brief at 400 KGS (precondition)", "medium")
    got = co.live_rec("negotiation_brief", f"{rfq}/1")
    m.case("brief_below_floor", "N4: 400 KGS, saving 21,636.00", bool(got), False, True)
    check(not got, f"N4: a brief was written for {rfq} (400 KGS, saving {saving} not above 25,000.00): "
          f"{got.get('summary')}")  # fmt: skip


def _decide(check: b.Checker, co: s.Co6, rfq: str) -> None:
    rec = co.live_rec("negotiation_brief", f"{rfq}/1")
    if not check(bool(rec), f"N1 copy: no brief keyed {rfq}/1 to dismiss"):
        return
    path = f"/api/recommendations/{rec['id']}/decide"
    r = co.call(co.login("production"), "POST", path, {"used": False, "note": "eval"})
    check(r.status_code == 403, f"decide: production (no rfq.create) answered {r.status_code}", "medium")
    r = co.call(co.login("buyer"), "POST", path, {"used": False, "note": "eval: the supplier will not move"})
    check(r.status_code == 200, f"decide: the buyer dismissing brief {rec['id']} answered {r.status_code} {r.text[:200]}")
    after = co.recommendation(rec["id"])
    check(after.get("status") not in ("proposed", None) and str(js(r).get("status")) == str(after.get("status")),
          f"decide: the brief is {after.get('status')} after dismissal (answer {js(r)})", "medium")  # fmt: skip
    r = co.call(co.login("buyer"), "POST", path, {"used": True})
    check(r.status_code == 409, f"decide: deciding the brief again answered {r.status_code}; expected 409", "medium")
    rank = co.live_rec("ranking", rfq)
    if rank:
        r = co.call(co.login("buyer"), "POST", f"/api/recommendations/{rank['id']}/decide", {"used": True})
        check(r.status_code == 409, f"decide: a ranking record answered {r.status_code}; the spec says 409 (briefs only)",
              "medium")  # fmt: skip


def _n3(check: b.Checker, co: s.Co6, oracle: s.Oracle6, rfq: str) -> None:
    brief_before = co.live_rec("negotiation_brief", f"{rfq}/1")
    rank_before = co.live_rec("ranking", rfq)
    audit0 = co.last_audit_id()
    r = co.quote6(rfq, "SUP-005", "900.00")
    check(r.status_code == 200, f"N3: SUP-005's revision 900.00 answered {r.status_code} {r.text[:200]}")
    co.work()
    br = oracle.brief(("SUP-005", Decimal("900.00")), ("SUP-003", Decimal("975.00")), s.WIRE, s.LOC, Decimal(500),
                      s.TODAY, _counted(oracle, {"refs": [{"kind": "market", "price": Decimal("897.00"),
                      "date": OCT, "source": "x"}], "uom": "KGS"}))  # fmt: skip
    check(br is None, "N3: the oracle still writes a brief at 900.00 (precondition)", "medium")
    live = co.live_rec("negotiation_brief", f"{rfq}/1")
    check(not live, f"N3: a brief is still in force for {rfq}/1 after L1 fell to 900.00: {live.get('summary')}")
    if brief_before:
        old = co.recommendation(brief_before["id"])
        check(old.get("status") == "withdrawn", f"N3: the brief is {old.get('status')}; the spec says withdrawn ('L1 is "
              "now within the thresholds')", "medium")  # fmt: skip
        check(bool(co.since("recommendation.withdraw", audit0)), "N3: no recommendation.withdraw audit row", "medium")
    rank_after = co.live_rec("ranking", rfq)
    check(bool(rank_after) and rank_after.get("id") != rank_before.get("id"), "N3: the ranking was not superseded by "
          "the revision", "medium")  # fmt: skip
