"""E25: a blocked invoice is explained with the right cause, a sloppy model cannot change that, and nothing is changed.

The dataset's invoices are entered through the platform (as in E19); the ones the platform blocks that the dataset planted a
cause for (price, quantity, rejected goods billed, wrong GSTIN, composition tax, missing e-invoice) are the test. The
answer key is the dataset's own `planted` cause, not anything the platform computed. A scripted investigator plays the model:
a careful one, one that names the wrong cause, and one that adds a number no tool returned; with --live the real model
investigates. The agent only advises: the invoices must be exactly as they were before."""

import json
from typing import Any

import invoice_replay
import lib
from lib import Outcome, pct
from sqlalchemy import text

ID = "E25"
TITLE = "Blocked invoices are explained with the right cause, and a block is never changed by the agent"
REQUIREMENT = (
    "Plan P1-18 (AG03): at least 80% of mismatch cases diagnosed correctly (proposed target); the payment block stays until a "
    "person releases it; numbers in the answer come from the data"
)

PLANTED = {
    "price_mismatch",
    "qty_mismatch",
    "rejected_qty_invoiced",
    "wrong_gstin",
    "composition_tax",
    "e_invoice_missing",
    "gst_mismatch",
}
OTHER_CAUSE = {
    "price_mismatch": "qty_mismatch", "qty_mismatch": "price_mismatch", "rejected_qty_invoiced": "wrong_gstin",
    "wrong_gstin": "rejected_qty_invoiced", "composition_tax": "e_invoice_missing", "e_invoice_missing": "composition_tax",
    "gst_mismatch": "arithmetic",
}  # fmt: skip


class Investigator:
    """A fake model that investigates one invoice: it looks at the invoice, then writes its answer."""

    def __init__(self) -> None:
        self.invoice = ""
        self.order = ""
        self.supplier = ""
        self.mode = "careful"
        self.explanation = ""
        self.cause = ""
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
    ) -> Any:
        from p2p.ai_steps.adapters import RawResponse

        self.calls += 1
        looked = sum("result]" in m["content"] for m in messages)
        steps = [
            ("get_invoice", {"no": self.invoice}),
            ("get_order", {"no": self.order}),
            ("get_receipts", {"no": self.order}),
            ("supplier_profile", {"code": self.supplier}),
        ]
        if (
            looked < len(steps)
        ):  # a real investigator looks at the invoice, the order, the receipts and the supplier
            tool, args = steps[looked]
            return RawResponse(
                json.dumps({"tool": tool, "args": args, "confidence": 0.9}),
                100,
                20,
                "stop",
            )
        cause = (
            OTHER_CAUSE.get(self.cause, "arithmetic")
            if self.mode == "wrong cause"
            else self.cause
        )
        text_ = self.explanation + (
            " The difference is 99999." if self.mode == "invented number" else ""
        )
        return RawResponse(
            json.dumps({"final": f"{text_}\nCause: {cause}", "confidence": 0.9}),
            100,
            20,
            "stop",
        )


def snapshot(db: Any) -> dict[str, Any]:
    q = lambda sql: [tuple(r) for r in db.execute(text(sql))]  # noqa: E731
    return {
        "invoices": q(
            "select no, status, payment_block, block_reason, due_date, tds_amount from invoice order by no"
        ),
        "lines": q(
            "select po_no, line_no, received_qty, invoiced_qty, status from po_line order by po_no, line_no"
        ),
        "match": q(
            "select invoice_no, invoice_line_no, result, reason_codes, resolved_at is null from match_result order by id"
        ),
        "proposals": q("select count(*) from proposal"),
        "payments": q("select count(*) from payment_run"),
    }


def agrees(cause: str, planted: str) -> bool:
    """A quantity over the receipt can be stated as the rejected pieces billed when the receipt had a rejection."""
    return cause == planted or (
        planted == "qty_mismatch" and cause == "rejected_qty_invoiced"
    )


def run(ctx: lib.Ctx) -> Outcome:
    from p2p.agents import invoice_exceptions as agent
    from p2p.ai_steps.gateway import Gateway

    out = Outcome(True)
    world = lib.build_world("e25", with_stock=False)
    db = world.db
    got = invoice_replay.replay(world)
    out.metrics["invoices entered"] = len(got["entered"])
    if got["errors"]:
        out.find(
            "high",
            f"{len(got['errors'])} invoice(s) could not be entered, e.g. {got['errors'][0]}",
        )
    truth, by_no = got["truth"], got["by_no"]

    blocked = [(key, d) for key, d in got["entered"].items() if d.status == "blocked"]
    cases = [
        (key, d.no, truth[key]["planted"])
        for key, d in blocked
        if key in truth and truth[key]["planted"] in PLANTED
    ]
    stray = [
        (key, d.no)
        for key, d in blocked
        if key not in truth or truth[key]["planted"] not in PLANTED
    ]
    out.metrics["blocked by the match"] = len(blocked)
    out.metrics["of those, with a cause the dataset planted"] = len(cases)
    out.metrics[
        "blocked without a planted cause (part-delivery carry-over, see E19)"
    ] = len(stray)

    # 1. the rules: the cause and the query to the supplier
    right, bad, emails_ok, fixes = 0, [], 0, {}
    diagnoses = {}
    for key, no, planted in cases:
        d = agent.diagnose(db, no)
        assert d is not None
        diagnoses[no] = d
        fixes[d.fix] = fixes.get(d.fix, 0) + 1
        if agrees(d.cause, planted):
            right += 1
        else:
            bad.append(f"{no} ({key}): planted {planted}, diagnosed {d.cause}")
        if no in d.vendor_body and d.vendor_subject.startswith("Query on invoice"):
            emails_ok += 1
    out.metrics["rules: cause diagnosed right %"] = pct(right, len(cases))
    out.metrics["rules: fixes proposed"] = fixes
    out.metrics["rules: queries to the supplier that name our reference %"] = pct(
        emails_ok, len(cases)
    )
    if bad:
        out.find(
            "high",
            f"{len(bad)} cause(s) differ from the planted cause; first: {bad[0]}",
        )
    if len(cases) < 10:
        out.find(
            "medium",
            f"only {len(cases)} blocked invoice(s) with a planted cause: too few to judge",
        )

    # 2. the scripted investigator, careful and sloppy: the final cause must be right whatever the model says
    before = snapshot(db)
    investigator = Investigator()
    routes = {
        k: [("fake", "m")] for k in ("agent_invoice_exceptions", "agent", "default")
    }
    gw = Gateway({"fake": investigator}, routes, sleep=lambda s: None)
    sample = [
        c
        for c in cases
        if diagnoses[c[1]].cause == c[2] or not agrees(diagnoses[c[1]].cause, c[2])
    ][: 40 if not ctx.quick else 12]
    db.commit()
    results: dict[str, dict[str, int]] = {}
    for mode in ("careful", "wrong cause", "invented number"):
        investigator.mode = mode
        db.execute(
            text("delete from model_cache")
        )  # answers are cached by message: a different mode must not get the last one's
        db.commit()
        used = final_right = 0
        for key, no, planted in sample:
            base = diagnoses[no]
            investigator.invoice, investigator.explanation, investigator.cause = (
                no,
                base.explanation,
                base.cause,
            )
            first = db.execute(
                text(
                    "select po_no from invoice_line where invoice_no = :n order by line_no limit 1"
                ),
                {"n": no},
            ).scalar_one()
            investigator.order = first
            investigator.supplier = db.execute(
                text("select supplier_code from invoice where no = :n"), {"n": no}
            ).scalar_one()
            db.commit()  # the agent's runtime creates an index and waits for any open transaction
            d = agent.explain(db, world.slug, world.logins["AP"], no, gw)
            assert d is not None
            used += d.source == "agent"
            final_right += agrees(d.cause, planted)
        results[mode] = {
            "model wording used": used,
            "final cause right": final_right,
            "cases": len(sample),
        }
    out.metrics["investigator, careful: wording used / cause right"] = (
        f"{results['careful']['model wording used']} / {results['careful']['final cause right']} of {len(sample)}"
    )
    out.metrics["investigator naming a wrong cause: wording used / cause right"] = (
        f"{results['wrong cause']['model wording used']} / {results['wrong cause']['final cause right']} of {len(sample)}"
    )
    out.metrics["investigator adding a made-up number: wording used / cause right"] = (
        f"{results['invented number']['model wording used']} / {results['invented number']['final cause right']} of {len(sample)}"
    )
    for mode in ("wrong cause", "invented number"):
        if results[mode]["model wording used"]:
            out.find(
                "high",
                f"a model that {'named a wrong cause' if mode == 'wrong cause' else 'added a made-up number'} had its wording used in {results[mode]['model wording used']} case(s)",
            )
    for mode, r in results.items():
        if r["final cause right"] != r["cases"]:
            out.find(
                "high",
                f"with the '{mode}' investigator the final cause was wrong in {r['cases'] - r['final cause right']} case(s)",
            )
    if results["careful"]["model wording used"] != len(sample):
        out.find(
            "medium",
            f"a careful, grounded answer was not used in {len(sample) - results['careful']['model wording used']} case(s)",
        )

    # 3. nothing was changed: no block released, no proposal, no payment
    after = snapshot(db)
    changed = [k for k in before if before[k] != after[k]]
    out.metrics["tables changed by looking into invoices"] = changed or "none"
    if changed:
        out.find(
            "high",
            f"looking into invoices changed {changed}: the agent must only advise",
        )

    if ctx.live:
        live_gw = Gateway()
        live = cases[:10]
        db.execute(text("delete from model_cache"))
        db.commit()
        used = right_live = 0
        for key, no, planted in live:
            db.commit()
            d = agent.explain(db, world.slug, world.logins["AP"], no, live_gw)
            assert d is not None
            used += d.source == "agent"
            right_live += agrees(d.cause, planted)
            if d.source != "agent":
                why = db.execute(
                    text(
                        "select after from audit_event where action = 'invoice.explain_rejected' and entity_id = :i order by id desc limit 1"
                    ),
                    {"i": no},
                ).scalar()
                out.notes.append(
                    f"live wording not used for {no}: "
                    + (f"rejected {why}" if why else "no answer from the model")
                )
        out.metrics["LIVE model: wording used (named the right cause, grounded) %"] = (
            pct(used, len(live))
        )
        out.metrics["LIVE model: final cause right %"] = pct(right_live, len(live))
        if (
            pct(used, len(live)) < 40
        ):  # the rules' wording is the floor and the cause shown is right either way; below this the model adds little
            out.find(
                "medium",
                f"the live model's explanation was used in only {pct(used, len(live))}% of cases (floor 40%)",
            )
    else:
        out.notes.append("the real models were not called (run with --live)")
    return out
