"""E13: reading supplier emails into quotes, on synthetic emails with known answers."""

import random
from decimal import Decimal

import ai_harness as h
import lib
from ai_cases import build_cases
from lib import Outcome

ID = "E13"
TITLE = "Supplier emails are read into the right quotes, and when unsure the system asks a person"
REQUIREMENT = (
    "Plan step 8 and rule 2: the model reads, code checks; a wrong reading must never look clean (silent errors = 0)"
)


def corrupt(reading, kind: str, rng: random.Random):
    """A sloppy model: the same reading with one kind of mistake."""
    data = reading.model_copy(deep=True)
    if kind == "invented price" and data.lines:
        ln = data.lines[0]
        ln.unit_price = (ln.unit_price or Decimal(1)) + Decimal(
            rng.choice([1, 3, 7])
        )  # a number that is not in the email
    elif kind == "invented gst" and data.lines:
        data.lines[0].gst_rate = Decimal(rng.choice([6, 7, 11]))
    elif kind == "swapped prices" and len(data.lines) >= 2:
        a, b = data.lines[0], data.lines[1]
        a.unit_price, b.unit_price = (
            b.unit_price,
            a.unit_price,
        )  # both numbers are in the email
    elif kind == "invented delivery" and data.lines:
        data.lines[0].delivery_days = rng.choice([61, 77, 93])
    else:
        return None
    return data


def run(ctx) -> Outcome:
    out = Outcome(True)
    world = lib.build_world("e13", with_stock=False)
    cases = build_cases(quick=ctx.quick)
    out.metrics["email cases"] = len(cases)

    # 1. a careful (perfect) reader: checks that the pipeline itself loses nothing
    gw, provider = h.scripted_gateway()
    scored = [h.run_case(world, c, gw, provider, h.perfect_reading(c)) for c in cases]
    s = h.summarise(scored)
    out.metrics["perfect reader: exact %"] = s["exact %"]
    out.metrics["perfect reader: silent errors"] = s["silent errors"]
    if s["exact %"] < 100 or s["silent errors"]:
        out.find(
            "high",
            f"with a perfect model the pipeline still loses or alters information: {s['misses'][:3]} {s['silent error cases'][:2]}",
        )

    # 2. a sloppy reader: how many of its mistakes does the code around the model catch?
    rng = random.Random(8)
    caught: dict[str, list[bool]] = {}
    for i, c in enumerate(cases):
        if c.truth.intent != "quote" or not c.truth.lines:
            continue
        for kind in (
            "invented price",
            "invented gst",
            "swapped prices",
            "invented delivery",
        ):
            bad = corrupt(h.perfect_reading(c), kind, rng)
            if bad is None:
                continue
            gw2, p2 = h.scripted_gateway()
            sc = h.run_case(
                world,
                type(c)(
                    f"{c.id}-{kind.replace(chr(32), chr(45))}",
                    c.style,
                    c.items,
                    c.body,
                    c.truth,
                    c.tricky,
                ),
                gw2,
                p2,
                bad,
            )
            caught.setdefault(kind, []).append(not sc.silent_error)
    rates = {k: f"{sum(v)}/{len(v)} not clean-but-wrong" for k, v in caught.items()}
    out.metrics["sloppy reader: mistakes that did not slip through"] = rates
    for kind in ("invented price", "invented gst", "invented delivery"):
        v = caught.get(kind, [])
        if v and not all(v):
            out.find(
                "high",
                f"a made-up value ({kind}) slipped through as a clean draft in {v.count(False)} of {len(v)} case(s)",
            )
    swapped = caught.get("swapped prices", [])
    if swapped and not all(swapped):
        out.find(
            "medium",
            f"swapped prices between two lines look clean in {swapped.count(False)} of {len(swapped)} case(s): both numbers are in the email, so the evidence check cannot tell. A person confirms every draft by default; with automatic confirmation these would be accepted",
        )

    # 3. the real models
    if ctx.live:
        from p2p.ai_steps.gateway import Gateway

        gw_live = Gateway()
        live = [
            h.run_case(
                world,
                type(c)(f"live-{c.id}", c.style, c.items, c.body, c.truth, c.tricky),
                gw_live,
                None,
                None,
            )
            for c in cases
        ]
        sl = h.summarise(live)
        out.metrics["LIVE model: exact %"] = sl["exact %"]
        out.metrics["LIVE model: exact or safely refused %"] = sl["exact or safely refused %"]
        out.metrics["LIVE model: silent errors"] = sl["silent errors"]
        out.metrics["LIVE model: sent to a person"] = sl["sent to a person"]
        out.metrics["LIVE model: intent right %"] = sl["intent right %"]
        out.metrics["LIVE model: median seconds per email"] = sl["median seconds"]
        out.metrics["LIVE model: by style"] = sl["by style"]
        out.notes.append(f"live misses: {sl['misses']}")
        if sl["silent errors"]:
            out.find(
                "high",
                f"the live model produced {sl['silent errors']} wrong draft(s) that look clean: {sl['silent error cases']}",
            )
        if sl["exact or safely refused %"] < 90:
            out.find(
                "medium",
                f"the live model got {sl['exact or safely refused %']}% right or safely refused (target 90%): {sl['misses'][:4]}",
            )
        if sl["intent right %"] < 95:
            out.find(
                "medium",
                f"the live model read the intent right in only {sl['intent right %']}% of emails",
            )
    else:
        out.notes.append("the real models were not called (run with --live)")
    return out
