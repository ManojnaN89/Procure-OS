"""E127: sourcing notes never invent (task 11b.2, decision 14, D49, D54, rule 2), written without the implementation.

Independence: written from the block 6 spec (sections 6 common rules, 6.5, 8.3, 9, 10.2) and the dataset only. The
number check reads every number in a record's summary or `ai_note` and accepts it only when the record's engine figures
or rule versions hold it (rounded to 0, 2 and 4 places, dates split into parts), or it is a small count (0 to 10) the
templates name but the figures may not. No live model is called unless `--live`; a scripted model answers.

Given a fresh Apex reset on Mon 23 Nov 2026:
1. Capability `sourcing` on, the model answers with made-up numbers ("Ask for Rs 880.00 ... Rs 40,000.00 by 12 Dec
   2026"): R (RM-08 500 KGS, confirmed; an `rfq_draft`), three quotes 960.00 / 975.00 / 990.00 (a `ranking` and a
   `negotiation_brief`), and A1 (RM-01 600 NOS above a verified 600.00 benchmark; a `path_case`): every record's
   summary passes the number check; the model was asked (model_call capability sourcing, entity recommendation); every
   answer refused (`ai_step.rejected`), no `ai_note` kept.
2. The model answers words with no number: a second R and its quotes: an `ai_note` is kept and passes the check.
3. The capability switched off: a third R and its quotes: records still written, no model call, no `model_call` row;
   no hand-over task names a ranking, brief or review note.
4. Switched on again with a monthly cost cap the scripted (unpriced) model cannot pass: a fourth R: template only, the
   provider not asked.
5. Block 5 P6: on 1 Feb 2027 the expiry review of CNT-0001 opens with a `review_note` (key review:<task id>) whose
   summary starts with the task's title and passes the number check; `GET /api/reviews` shows it.
With `--live`, a fifth R is read by the real model: each `ai_note` passes the check or is empty (the template stands).
Ends with a tenant check.
Mutations: a made-up number accepted; the model called while the capability is off.
"""

from datetime import date
from decimal import Decimal
from typing import Any

import aif
import block1 as b
import block3 as b3
import block4 as k
import block6 as s
import env
import lib
from block1 import js
from lib import Outcome

ID = "E127"
TITLE = "Sourcing notes are the engine's template, or model words that invent no number"
REQUIREMENT = (
    "Task 11b.2, decision 14, D54 (block 6 spec 6): rfq_draft, ranking, negotiation_brief, path_case and review_note "
    "records written by the engine with template words whose every number is in their figures; the model only rewords "
    "(ai_note kept only when every number is in the figures, else ai_step.rejected); capability off -> no model call, "
    "records still written, no hand-over of informational records; cost cap -> template; tenant check"
)
PREFIX = "e127"
KINDS = ("rfq_draft", "ranking", "negotiation_brief", "path_case", "review_note")
INFORMATIONAL = ("ranking", "negotiation_brief", "review_note")
P6_DAY = date(2027, 2, 1)
BAD = {"note": "Ask for Rs 880.00 per KGS: it saves Rs 40,000.00 if ordered by 12 Dec 2026."}
GOOD = {"note": "The lowest offer is above what we paid before; asking the supplier for a better price is worth a try."}


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
        m = k.Mutations()
        model = aif.Model(default=BAD)
        rfqs: list[str] = []
        with aif.no_live_models(model):
            rfqs.append(_phase1(out, check, apex, m, model))
            rfqs.append(_phase2(out, check, apex, model))
            rfqs.append(_phase3(out, check, apex, m, model))
            rfqs.append(_phase4(out, check, apex, model))
            _phase5(out, check, apex)
        if ctx.live:
            rfqs.append(_live(out, check, apex))
        m.report(check, out, ["made_up_number_accepted", "model_called_off"])
        s.f.tenant_check(check, apex, bw, rfqs, [], start)
        s.tenant_tasks(check, apex, bw, rfqs)
        theirs = bw.qd("select kind from recommendation where kind = any(:k)", k=list(KINDS))
        check(not theirs, f"tenant check: Brightwave holds {len(theirs)} sourcing record(s) while only Apex acted")
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        (
            "Spec gap: the cost cap's refusal for an unpriced model is the AI foundation's (E103); the eval sets a tiny "
            "cap so the scripted model cannot be priced under it, and checks only that the template stands and the "
            "provider is not asked."
        ),
        "The strategic review is opened by price_review.run on 1 Feb 2027 in this process, as E113 does for block 5 P6.",
    ]
    return out


# ---- the number check ------------------------------------------------------------------------------------------------


def invented(rec: dict[str, Any], words: str) -> list[str]:
    allowed = b3.record_numbers(rec.get("engine_figures") or {}) | b3.record_numbers(rec.get("rule_versions") or {})
    allowed |= b3.record_numbers(rec.get("business_key") or "")
    allowed |= {Decimal(x) for x in range(11)}
    return b3.invented(words or "", {a.normalize() for a in allowed})


def records(co: s.Co6, since_id: int) -> list[dict[str, Any]]:
    found = []
    for kind in KINDS:
        found += [r for r in co.recs(kind) if int(r["id"]) > since_id]
    return sorted(found, key=lambda r: int(r["id"]))


def rec_mark(co: s.Co6) -> int:
    return int(co.one("select coalesce(max(id), 0) from recommendation") or 0)


def check_records(check: b.Checker, label: str, recs: list[dict[str, Any]], want_kinds: tuple[str, ...]) -> None:
    kinds = {r["kind"] for r in recs}
    missing = [x for x in want_kinds if x not in kinds]
    check(not missing, f"{label}: no record of kind {missing} was written (records: {sorted(kinds)})")
    for r in recs:
        bad = invented(r, str(r.get("summary") or ""))
        check(not bad, f"{label}: {r['kind']} {r['business_key']}'s summary has numbers its figures do not hold: {bad}: "
              f"{str(r.get('summary'))[:200]}")  # fmt: skip
        note = str(r.get("ai_note") or "")
        bad = invented(r, note)
        check(not bad, f"{label}: {r['kind']} {r['business_key']}'s ai_note has invented numbers {bad}: {note[:200]}")


def _r_with_quotes(check: b.Checker, co: s.Co6, label: str) -> str:
    _, rfq = co.r_rfq(check, label)
    if rfq:
        for sup, price in (("SUP-005", "960.00"), ("SUP-003", "975.00"), ("SUP-001", "990.00")):
            r = co.quote6(rfq, sup, price)
            check(r.status_code == 200, f"{label}: {sup}'s quote on {rfq}: {r.status_code} {r.text[:150]}", "medium")
        co.work()
    return rfq


# ---- the phases --------------------------------------------------------------------------------------------------------


def _phase1(out: Outcome, check: b.Checker, co: s.Co6, m: k.Mutations, model: aif.Model) -> str:
    mark, calls0, cmark, audit0 = rec_mark(co), model.calls, co.model_mark(), co.last_audit_id()
    model.default = BAD
    rfq = _r_with_quotes(check, co, "phase 1")
    co.benchmark(check, s.CASTING, "600.00", "NOS", date(2026, 11, 20), "MH", "E127 Pune foundry survey", "phase 1")
    co.ask(check, s.CASTING, 600, s.A_NEED, "phase 1 A1")
    co.work()
    recs = records(co, mark)
    check_records(check, "phase 1", recs, ("rfq_draft", "ranking", "negotiation_brief", "path_case"))
    kept = [r for r in recs if r.get("ai_note")]
    m.case("made_up_number_accepted", "phase 1: the model's made-up numbers", bool(kept), False, True)
    check(not kept, f"phase 1: {len(kept)} ai_note(s) kept from answers with made-up numbers: "
          f"{[str(r.get('ai_note'))[:80] for r in kept]}")  # fmt: skip
    calls = [c for c in co.model_calls_since(cmark) if c.get("capability") == "sourcing" and c.get("entity") ==
             "recommendation"]  # fmt: skip
    check(model.calls > calls0 and bool(calls), f"phase 1: the sourcing note step was not asked with the capability on "
          f"({model.calls - calls0} call(s), {len(calls)} model_call row(s) with entity recommendation)", "medium")  # fmt: skip
    rejected = [a for a in co.since("ai_step.rejected", audit0) if a.get("entity") == "recommendation"]
    check(bool(rejected) or model.calls == calls0, "phase 1: no ai_step.rejected audit row (entity recommendation) for "
          "the refused answers", "medium")  # fmt: skip
    out.metrics["phase 1"] = {"records": len(recs), "model calls": model.calls - calls0, "notes kept": len(kept)}
    return rfq


def _phase2(out: Outcome, check: b.Checker, co: s.Co6, model: aif.Model) -> str:
    mark, calls0 = rec_mark(co), model.calls
    model.default = GOOD
    rfq = _r_with_quotes(check, co, "phase 2")
    recs = records(co, mark)
    check_records(check, "phase 2", recs, ("rfq_draft", "ranking", "negotiation_brief"))
    kept = [r for r in recs if r.get("ai_note")]
    check(bool(kept), f"phase 2: no ai_note kept from an answer with no number at all ({model.calls - calls0} model "
          "call(s))", "medium")  # fmt: skip
    if rfq and kept:
        body = js(co.call(co.login("buyer"), "GET", f"/api/rfqs/{rfq}"))
        briefs = (body.get("recommendations") or {}).get("briefs") or []
        check(any(str(x.get("ai_note") or "") for x in briefs if isinstance(x, dict)), f"phase 2: GET /api/rfqs/{rfq} "
              f"shows no brief with its ai_note: {str(briefs)[:200]}", "low")  # fmt: skip
    out.metrics["phase 2"] = {"records": len(recs), "notes kept": len(kept)}
    return rfq


def _phase3(out: Outcome, check: b.Checker, co: s.Co6, m: k.Mutations, model: aif.Model) -> str:
    open_info = [r for r in records(co, 0) if r["kind"] in INFORMATIONAL]
    r = aif.switch_off(co, "sourcing")
    check(r.status_code in (200, 202) and aif.enabled(co).get("sourcing") is False, f"phase 3: switching sourcing off "
          f"answered {r.status_code} {r.text[:150]}", "medium")  # fmt: skip
    co.work()
    handed = [t for t in aif.tasks(co, "ai_handover", None)
              if any(str(x["id"]) in json_text(t["payload"]) for x in open_info)]  # fmt: skip
    check(not handed, f"phase 3: switch-off handed over {len(handed)} informational record(s) (ranking, brief, review "
          "note) as tasks; the spec leaves them without model words")  # fmt: skip
    mark, calls0, cmark = rec_mark(co), model.calls, co.model_mark()
    model.default = GOOD
    rfq = _r_with_quotes(check, co, "phase 3")
    recs = records(co, mark)
    check_records(check, "phase 3", recs, ("rfq_draft", "ranking", "negotiation_brief"))
    rows = [c for c in co.model_calls_since(cmark) if c.get("capability") == "sourcing"]
    m.case("model_called_off", "phase 3: capability off", model.calls - calls0, 0, 1)
    check(model.calls == calls0 and not rows, f"phase 3: with sourcing off the model was called {model.calls - calls0} "
          f"time(s), {len(rows)} model_call row(s)")  # fmt: skip
    queued = [j for j in aif.jobs(co.slug, ["sourcing_note"]) if j.get("status") == "queued"]
    check(not queued, f"phase 3: {len(queued)} sourcing_note job(s) queued with the capability off", "medium")
    check(all(not r.get("ai_note") for r in recs), "phase 3: an ai_note was written with the capability off")
    out.metrics["phase 3"] = {"records": len(recs), "model calls": model.calls - calls0, "handed over": len(handed)}
    return rfq


def json_text(value: Any) -> str:
    import json

    return json.dumps(value, default=str)


def _phase4(out: Outcome, check: b.Checker, co: s.Co6, model: aif.Model) -> str:
    asked, done = aif.switch_on(co, "sourcing")
    check(aif.enabled(co).get("sourcing") is True, f"phase 4: switching sourcing on: {asked.status_code} "
          f"{getattr(done, 'status_code', None)}", "medium")  # fmt: skip
    capped = co.set_policy(check, "ai_monthly_cost_cap_usd", "0.000001", "phase 4")
    mark, calls0 = rec_mark(co), model.calls
    model.default = GOOD
    rfq = _r_with_quotes(check, co, "phase 4")
    recs = records(co, mark)
    check_records(check, "phase 4", recs, ("rfq_draft", "ranking", "negotiation_brief"))
    check(model.calls == calls0, f"phase 4: with the cost cap set the provider was asked {model.calls - calls0} "
          "time(s)", "medium")  # fmt: skip
    check(all(not r.get("ai_note") for r in recs), "phase 4: an ai_note was written past the cost cap", "medium")
    co.set_policy(check, "ai_monthly_cost_cap_usd", "", "phase 4 (cap removed)")
    out.metrics["phase 4"] = {"cap set": capped, "records": len(recs), "model calls": model.calls - calls0}
    return rfq


def _phase5(out: Outcome, check: b.Checker, co: s.Co6) -> None:
    from p2p.platform import routing
    from p2p.sourcing import price_review
    from sqlalchemy.orm import Session

    mark = rec_mark(co)
    with s.pinned(P6_DAY):
        with Session(routing.engine_for(co.slug)) as db:
            price_review.run(db, co.slug, P6_DAY)
            db.commit()
        co.work()
        tasks = [t for t in co.tasks("strategic_review") if "CNT-0001" in json_text(t["payload"]) + str(t["title"])]
        notes = [r for r in records(co, mark) if r["kind"] == "review_note"]
        if not check(bool(tasks), "phase 5: no strategic review of CNT-0001 opened on 1 Feb 2027 (block 5 P6, "
                     "precondition)", "medium"):  # fmt: skip
            return
        t = tasks[-1]
        note = next((r for r in notes if r["business_key"] == f"review:{t['id']}"), {})
        check(bool(note), f"phase 5: no review_note keyed review:{t['id']} ({[r['business_key'] for r in notes]})")
        if note:
            check_records(check, "phase 5", [note], ("review_note",))
            check(s.norm(note.get("summary")).startswith(s.norm(t["title"])), f"phase 5: the review note "
                  f"{note.get('summary')!r} does not start with the task's title {t['title']!r}", "medium")  # fmt: skip
            check("cnt-0001 stays valid to 31 mar 2027" in s.norm(note.get("summary")), f"phase 5: the review note "
                  f"lacks 'CNT-0001 stays valid to 31 Mar 2027 ...': {note.get('summary')}", "medium")  # fmt: skip
        listed = js(co.call(co.login("buyer"), "GET", "/api/reviews"))
        mine = [x for x in listed if isinstance(x, dict) and str(x.get("task_id")) == str(t["id"])] if isinstance(
            listed, list) else []  # fmt: skip
        check(bool(mine) and isinstance(mine[0].get("note"), dict) and "summary" in mine[0]["note"],
              f"phase 5: GET /api/reviews shows review {t['id']} without note {{summary, ai_note}}: "
              f"{str(mine[:1])[:200]}", "medium")  # fmt: skip
        out.metrics["phase 5 review note"] = note.get("summary")


def _live(out: Outcome, check: b.Checker, co: s.Co6) -> str:
    mark = rec_mark(co)
    rfq = _r_with_quotes(check, co, "live")
    recs = records(co, mark)
    check_records(check, "live", recs, ("rfq_draft", "ranking"))
    out.metrics["live notes kept"] = sum(1 for r in recs if r.get("ai_note"))
    return rfq
