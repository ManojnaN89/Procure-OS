"""E119: the award note never invents (decision 14, block 5 spec 7), written without the implementation.

Independence: written from the block 5 spec (sections 7, 9, 10.2) and the dataset only. The template is written here
from the spec's words and the eval's own P1 figures (names from `user.csv`, `supplier.csv`, `item.csv`, `location.csv`;
money in Indian grouping; the award value from `block5.Oracle5`); the number check reads every number in a note and
accepts it only when the recommendation's engine figures hold it (or it is a part of the RFQ number, the period's dates,
the line number or the count of quotes, which the template names but the spec's figure list does not).

P1 on a fresh Apex reset, 23 Nov 2026, proposed three times (the buyer withdraws between rounds): (1) the `sourcing`
capability switched off: the note is the template exactly, `note_source` template, no model call and no `model_call`
row; (2) switched on, a scripted model answers with a made-up price (870.00): refused (`ai_step.rejected`), the template
stays; (3) the model answers with figures from the template only: the note may be the model's (`ai`), and every number
in it is in the engine figures. The recommendation record is the buyer's (kind award, the RFQ as key, the proposal
linked). With `--live` a fourth round calls the real model: its note passes the number check or the template stays.
Tenant check.
Mutations: a model-made number accepted; the model called with the capability off.
"""

import re
from decimal import Decimal
from typing import Any

import aif
import block1 as b
import block3 as b3
import block4 as k
import block5 as f
import env
import lib
from lib import Outcome

ID = "E119"
TITLE = "The award note is the template with the engine's figures, or a model text that invents none"
REQUIREMENT = (
    "Decision 14, block 5 spec 7: the template note in the spec's words with the engine's figures; one model call only "
    "with the sourcing capability on, its every number and date found in the figures or the whole answer refused and "
    "the template kept (ai_step.rejected); note_source; the recommendation record of the award; a model never sets a "
    "figure; tenant check"
)
PREFIX = "e119"
BASIS = "@@basis@@"


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
        m = k.Mutations()
        rfq = _rounds(ctx, out, check, apex, m)
        m.report(check, out, ["model_number_accepted", "model_called_off"])
        f.tenant_check(check, apex, bw, [rfq], [], start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        (
            "Spec gap: the template's basis words ('on <basis>') are not fixed by the spec; any words are accepted there. "
            "The rupee may be written 'Rs ' or '₹ ' (format_inr), both read as the same."
        ),
        (
            "Spec gap: the engine figures the spec lists hold no dates and no RFQ number, while the template names both; the "
            "number check accepts those from the RFQ itself."
        ),
    ]
    return out


def template(co: f.Co5, oracle: f.Oracle5, rfq: str) -> str:
    """Spec 7.3 with the eval's own P1 figures; the basis left open."""
    buyer = co.name_of(co.login("buyer"))
    place = oracle.locations[f.LOC]["name"]
    item = oracle.items[f.WIRE]["name"]
    s = oracle.suppliers["SUP-005"]
    supplier = s.get("trade_name") or s["legal_name"]
    v = oracle.award_value([(f.WIRE, 900, "880.00")], "SUP-005", f.LOC)
    return (
        f"{buyer} proposes to award {rfq} for {f.long_date(f.P_FROM)} to {f.long_date(f.P_TO)} at {place}. "
        f"Line 1, {item}: {supplier} at Rs {b3.inr(Decimal('880.00'))} per KGS for {b.fq(Decimal(900))} KGS, "
        f"Rs {b3.inr(v['taxable'])} before GST; ranked 1 of 3 qualifying quotes on {BASIS}. "
        f"Award value Rs {b3.inr(v['total'])} with GST."
    )


def matches(note: str, want: str) -> bool:
    pattern = re.escape(f.norm(want).rstrip(".")).replace(
        re.escape(BASIS), r"([a-z][a-z ,()-]*?)"
    )
    return re.fullmatch(pattern, f.norm(note).rstrip(".")) is not None


def invented(co: f.Co5, rfq: str, note: str, rec: dict[str, Any]) -> list[str]:
    allowed = b3.record_numbers(k.as_json(rec.get("engine_figures")) or {})
    allowed |= b3.record_numbers(k.as_json(rec.get("rule_versions")) or {})
    extra = f"{rfq} {f.P_FROM.isoformat()} {f.P_TO.isoformat()} 01 31 1 2 3"
    allowed |= {t.normalize() for t in b3.number_tokens(extra)} | {
        Decimal(x) for x in (1, 2, 3, 31, 2027)
    }
    return b3.invented(note, {a.normalize() for a in allowed})


def _bad(messages: Any) -> dict[str, Any]:
    return {
        "summary": "Pune Copper Co. is the best at Rs 870.00 per KGS, saving Rs 27,000.00 on 900 KGS.",
        "risks": [],
    }


def _good(note_figures: str) -> dict[str, Any]:
    return {
        "summary": f"Pune Copper Co. ranks first at Rs 880.00 per KGS for 900 KGS. {note_figures}",
        "risks": [],
    }


def _propose(check: b.Checker, co: f.Co5, rfq: str, label: str) -> dict[str, Any]:
    r = co.award(co.login("buyer"), rfq, {1: "SUP-005"})
    check(
        r.status_code == 202,
        f"{label}: the award answered {r.status_code} {r.text[:200]}",
    )
    co.work()
    return co.decision(rfq)


def _withdraw(check: b.Checker, co: f.Co5, rfq: str, label: str) -> None:
    r = co.decide(
        co.login("buyer"), rfq, "withdraw", reason=f"eval: {label}, select again"
    )
    check(r.status_code == 200 and co.rfq(rfq).get("status") == "sent", f"{label}: the buyer's withdraw answered "
          f"{r.status_code} {r.text[:200]}; RFQ {co.rfq(rfq).get('status')}")  # fmt: skip


def _note_ok(
    check: b.Checker,
    co: f.Co5,
    rfq: str,
    d: dict[str, Any],
    want: str,
    label: str,
    must: str | None,
) -> str:
    note, source = str(d.get("note") or ""), d.get("note_source")
    rec = co.recommendation(d.get("recommendation_id"))
    made_up = invented(co, rfq, note, rec)
    check(bool(note) and not made_up, f"{label}: the note has numbers the engine figures do not hold: {made_up}: "
          f"{note[:300]}")  # fmt: skip
    if source == "template":
        check(matches(note, want), f"{label}: the template note differs from the spec's words:\n  got:  {note}\n  want: "
              f"{want.replace(BASIS, '<basis>')}")  # fmt: skip
    if must:
        check(
            source == must,
            f"{label}: note_source {source!r}, expected {must!r}",
            "high" if must == "template" else "medium",
        )
    else:
        check(source in ("template", "ai"), f"{label}: note_source {source!r}")
    return str(source)


def _rounds(ctx: Any, out: Outcome, check: b.Checker, co: f.Co5, m: k.Mutations) -> str:
    oracle = f.Oracle5(co)
    model = aif.Model(default=_bad)
    with aif.no_live_models(model):
        r = aif.switch_off(co, "sourcing")
        check(r.status_code in (200, 202) and aif.enabled(co).get("sourcing") is False,
              f"switching the sourcing capability off answered {r.status_code} {r.text[:150]}", "medium")  # fmt: skip
        mark_calls = int(co.one("select coalesce(max(id), 0) from model_call") or 0)
        calls0 = model.calls
        rfq, _ = f.p1_proposal(check, co)
        if not rfq:
            return ""
        co.work()
        want = template(co, oracle, rfq)
        d = co.decision(rfq)
        _note_ok(check, co, rfq, d, want, "round 1 (capability off)", "template")
        new_calls = int(
            co.one("select count(*) from model_call where id > :i", i=mark_calls) or 0
        )
        m.case(
            "model_called_off",
            "round 1 with the capability off",
            model.calls - calls0,
            0,
            1,
        )
        check(model.calls == calls0 and new_calls == 0, f"round 1: the model was called {model.calls - calls0} time(s), "
              f"{new_calls} model_call row(s), with the sourcing capability off")  # fmt: skip
        _record(check, co, rfq, d)

        _withdraw(check, co, rfq, "round 1")
        asked, done = aif.switch_on(co, "sourcing")
        check(aif.enabled(co).get("sourcing") is True, f"switching sourcing on: {asked.status_code} "
              f"{getattr(done, 'status_code', None)}", "medium")  # fmt: skip
        mark = co.last_audit_id()
        calls1 = model.calls
        model.default = _bad
        d = _propose(check, co, rfq, "round 2 (made-up price)")
        source = _note_ok(
            check, co, rfq, d, want, "round 2 (made-up price)", "template"
        )
        m.case(
            "model_number_accepted",
            "round 2: the model says 870.00",
            source,
            "template",
            "ai",
        )
        check(
            model.calls > calls1,
            "round 2: the AI step never ran with the sourcing capability on (decision 14)",
            "medium",
        )
        check(bool(co.since("ai_step.rejected", mark)) or model.calls == calls1, "round 2: no ai_step.rejected audit row "
              "for the refused answer", "medium")  # fmt: skip

        _withdraw(check, co, rfq, "round 2")
        model.default = lambda msgs: _good("Award value" + want.split("Award value")[1])
        d = _propose(check, co, rfq, "round 3 (good answer)")
        source = _note_ok(check, co, rfq, d, want, "round 3 (good answer)", None)
        check(source == "ai", f"round 3: a model answer with only the template's figures was not used (note_source "
              f"{source})", "low")  # fmt: skip
        out.metrics["notes (round: source)"] = {
            "1": "template",
            "2": "template",
            "3": source,
        }
    if ctx.live:
        _withdraw(check, co, rfq, "round 3")
        d = _propose(check, co, rfq, "live round")
        out.metrics["live note_source"] = _note_ok(
            check, co, rfq, d, want, "live round", None
        )
    return rfq


def _record(check: b.Checker, co: f.Co5, rfq: str, d: dict[str, Any]) -> None:
    rec = co.recommendation(d.get("recommendation_id"))
    figures = k.as_json(rec.get("engine_figures")) or {}
    held = b3.record_numbers(figures)
    check(rec.get("capability") == "sourcing" and rec.get("kind") == "award" and rec.get("business_key") == rfq
          and f.same_person(co, rec.get("directed_by"), co.login("buyer")),
          f"the recommendation of {rfq}: capability {rec.get('capability')}, kind {rec.get('kind')}, key "
          f"{rec.get('business_key')}, directed by {rec.get('directed_by')}; expected sourcing, award, {rfq}, the buyer")  # fmt: skip
    check(
        all(Decimal(x) in held for x in ("880.00", "900", "792000.00", "934560.00")),
        f"the engine figures do not hold the price, quantity, taxable and total: {str(figures)[:300]}",
    )
    linked = rec.get("proposal_id") == d.get("proposal_id") or str(
        rec.get("id")
    ) in str(co.proposal(d.get("proposal_id")).get("payload"))
    check(
        linked,
        f"recommendation {rec.get('id')} is not linked to the award's proposal {d.get('proposal_id')}",
        "medium",
    )
