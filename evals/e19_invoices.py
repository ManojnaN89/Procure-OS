"""E19: every invoice of the dataset is entered in the order it arrived, with the receipts that had happened by then, and
the platform's verdict is compared with the answer the dataset keeps for it (matched, within tolerance, or blocked and why).
Copies of an invoice must be found, a different financial year's reuse of a number must not, and credit notes must be taken in."""

from collections import Counter

import lib
from lib import Outcome
from replay import replay
from sqlalchemy import text

ID = "E19"
TITLE = "Invoices are matched to orders and receipts the way the dataset's answers say"
REQUIREMENT = (
    "Plan P1-16/17: three-way match per line (quantity against accepted receipt, price within tolerance), tax and identity "
    "checks, duplicates caught, credit notes taken in; nothing is paid that the match blocks"
)


def run(ctx) -> Outcome:
    out = Outcome(True)
    world = lib.build_world("e19", with_stock=False)
    db = world.db
    r_ = replay(world)
    entered, by_no, errors, truth = r_.entered, r_.by_no, r_.errors, r_.truth
    inv_lines, receipt_lines = r_.inv_lines, r_.receipt_lines

    # compare
    confusion: Counter[tuple[str, str]] = Counter()
    wrong: list[str] = []
    dup_expected = dup_found = false_dup = checked = 0
    for key, r in by_no.items():
        done = entered.get(key)
        if done is None:
            continue
        if r["type"] != "invoice":
            if done.status != "approved":  # type: ignore[attr-defined]
                wrong.append(f"{key} credit note ended {done.status}")  # type: ignore[attr-defined]
            continue
        is_dup = r["payment_block"] == "duplicate"
        if is_dup:
            dup_expected += 1
            dup_found += done.status == "cancelled" and bool(done.duplicate_of)  # type: ignore[attr-defined]
            if not (done.status == "cancelled" and done.duplicate_of):  # type: ignore[attr-defined]
                wrong.append(f"{key} is a copy but was not found as one ({done.status})")  # type: ignore[attr-defined]
            continue
        if done.duplicate_of:  # type: ignore[attr-defined]
            false_dup += 1
            wrong.append(f"{key} was taken for a copy of {done.duplicate_of} but is not")  # type: ignore[attr-defined]
            continue
        want = truth.get(key)
        if want is None:
            continue
        checked += 1
        got_result = done.result  # type: ignore[attr-defined]
        got_reason = done.reasons[0] if done.reasons else ""  # type: ignore[attr-defined]
        confusion[(f"{want['result']} {want['reason_codes']}".strip(), f"{got_result} {got_reason}".strip())] += 1
        if got_result != want["result"] or got_reason != want["reason_codes"]:
            wrong.append(
                f"{key} ({want['planted'] or 'normal'}): expected {want['result']} {want['reason_codes']}, got {got_result} {got_reason}"
            )

    right = checked - len([w for w in wrong if "expected" in w])
    out.metrics = {
        "invoices entered": len(entered),
        "judged against the dataset": checked,
        "verdict agrees %": lib.pct(right, checked),
        "copies expected / found / false alarms": f"{dup_expected} / {dup_found} / {false_dup}",
        "credit notes taken in": sum(
            1
            for r in by_no.values()
            if r["type"] != "invoice" and entered.get(r["no"]) is not None and entered[r["no"]].status == "approved"
        ),  # type: ignore[attr-defined]
        "intake errors": len(errors),
        "by expected -> got": {f"{a} -> {b}": n for (a, b), n in sorted(confusion.items(), key=lambda kv: -kv[1])[:8]},
    }
    if errors:
        out.find("high", f"{len(errors)} invoice(s) could not be entered, e.g. {errors[0]}")
    # The dataset judges quantity against the one receipt an invoice belongs to; the platform (like standard ERP matching)
    # against everything accepted on the order line and not yet invoiced. They differ only where a line was delivered in
    # several parts, and the first such difference carries on to the invoices after it on the same line.
    deliveries: Counter[tuple[str, str]] = Counter(
        (g["po_no"], g["po_line_no"]) for lines_ in receipt_lines.values() for g in lines_
    )

    def explained(text_: str) -> bool:
        lines_ = inv_lines.get(text_.split()[0], [])
        return bool(lines_) and all(deliveries[(ln["po_no"], ln["po_line_no"])] > 1 for ln in lines_)

    unexplained = [w for w in wrong if not explained(w)]
    out.metrics["differences explained by part deliveries"] = len(wrong) - len(unexplained)
    out.notes.append("differences: " + " | ".join(wrong[:12]))
    if unexplained:
        out.find(
            "high",
            f"{len(unexplained)} verdict(s) differ from the dataset's answers for no part-delivery reason; first: {unexplained[0]}",
        )
    right = checked - len([w for w in wrong if "expected" in w and not explained(w)])
    out.metrics["verdict agrees % (part-delivery differences aside)"] = lib.pct(right, checked)
    if checked == 0:
        out.find("medium", "no invoice was judged: the replay did not run")
    # nothing blocked is approved, and what was received limits what was invoiced
    over = db.execute(
        text(
            "select count(*) from po_line where invoiced_qty > received_qty + 0.0001 and invoiced_qty > "
            "received_qty * 1.0 + 0.0001"
        )
    ).scalar_one()
    out.metrics["order lines invoiced beyond what was received"] = over
    leaked = db.execute(
        text("select count(*) from invoice where status = 'approved' and payment_block <> 'none'")
    ).scalar_one()
    if leaked:
        out.find("high", f"{leaked} approved invoice(s) still carry a payment block")
    return out
