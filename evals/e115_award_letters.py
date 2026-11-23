"""E115: the award letter and the regret notes (task 12.2, D45, OP-9, OP-10), written without the implementation.

Independence: written from the block 5 spec (sections 6.2, 8.3 P1 and P4, 9) and the dataset only. Who gets which
letter comes from the quotes the eval enters and `supplier_contact.csv` (the primary contact); the words are the spec's
fixed sentences; the figures a regret note must not show are the eval's own quotes and the oracle's award value.

On a fresh Apex reset, 23 Nov 2026. P1 to the director's approval. Before approval no letter of either kind is queued
or mailed. After it: exactly one award mail to SUP-005's contact (the notice sentence, the RFQ, RM-08, 880.00 and a
`/contract/<token>` link, the contract code, a PDF attached) and one regret note to each of SUP-003 and SUP-001 in the
spec's words, with no price, quantity, value or other supplier's name; one `award_letter` row per mail with its message
id, the award row naming the contract; audit `award.letter_queued`. The worker run again and a second approval (409)
send nothing more. P4 proposed with `regret_notes` false and approved by the purchase manager: the award letter to
SUP-002 only, no regret note to SUP-003. Tenant check.
Mutations: a regret note listing the winner's price; letters queued before approval.
"""

import re
from typing import Any

import block1 as b
import block3 as b3
import block4 as k
import block5 as f
import env
import lib
from block1 import js
from lib import Outcome

ID = "E115"
TITLE = "One award letter (a notice, not an order) and regret notes without prices, sent once after approval"
REQUIREMENT = (
    "Task 12.2, D45, OP-9, OP-10 (block 5 spec 6.2): letters only after the band approver approves, queued by the "
    "system, once; the award letter's notice words and signed link; regret notes to the others who quoted, in fixed "
    "words, with no price, quantity, value or other supplier's name; regret_notes false sends none; award_letter rows "
    "and audit; tenant check"
)
PREFIX = "e115"
QUOTES = {"SUP-005": "880.00", "SUP-003": "895.00", "SUP-001": "910.00"}


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
        p1, contracts = _p1(out, check, apex, oracle, m)
        p4 = _p4(out, check, apex, oracle)
        m.report(check, out, ["regret_with_price", "letters_before_approval"])
        f.tenant_check(check, apex, bw, [p1, p4], contracts, start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += [
        (
            "Mailpit in the spec is the mail caught in this process (the outbox sends into it after each commit and when the "
            "worker runs), as in the block 4 evals."
        ),
        (
            "A regret note may name its own reader (its own supplier and quote number); every other supplier's name, every "
            "quoted price, the quantity and the award value are forbidden in any spelling (880, 880.00, 7,92,000.00 ...)."
        ),
        "The award letter's attachment is looked for in the mail's attachments (a PDF by name or type), checked as medium.",
    ]
    return out


def forbidden(oracle: f.Oracle5, own: str) -> list[str]:
    """What a regret note to `own` must never show: any quoted price, the quantity, the value, another supplier's name."""
    value = oracle.award_value([(f.WIRE, 900, "880.00")], "SUP-005", f.LOC)
    figures = list(QUOTES.values()) + [p.split(".")[0] for p in QUOTES.values()]
    figures += [b3.inr(value[x]) for x in ("taxable", "total", "cgst")] + ["900"]
    names = [
        str(oracle.suppliers[s].get("trade_name") or oracle.suppliers[s]["legal_name"])
        for s in QUOTES
        if s != own
    ]
    return figures + names + [s for s in QUOTES if s != own]


def shows(body: str, word: str) -> bool:
    if re.fullmatch(r"[\d.,]+", word):
        return re.search(rf"(?<![\d.,]){re.escape(word)}(?![\d])", body) is not None
    return word.lower() in body.lower()


def _p1(
    out: Outcome, check: b.Checker, co: f.Co5, oracle: f.Oracle5, m: k.Mutations
) -> tuple[str, list[str]]:
    director = co.login("director")
    contacts = {s: oracle.contacts[s] for s in QUOTES}
    mark0 = co.mark()
    rfq, r = f.p1_proposal(check, co)
    if not rfq or r is None:
        return rfq, []
    d = co.decision(rfq)
    # nothing before approval
    early = [x for x in co.mails(mark0) if any(str(x.get("to", "")).lower() == c["email"].lower() for c in contacts.values())
             and ("notice of selection" in str(x.get("body", "")).lower() or "not selected yours" in str(x.get("body", "")).lower())]  # fmt: skip
    rows = co.letters(d.get("id")) if d else []
    m.case(
        "letters_before_approval",
        "P1 letters before the director decides",
        len(early) + len(rows),
        0,
        3,
    )
    check(not early and not rows, f"P1: {len(early)} letter mail(s) and {len(rows)} award_letter row(s) before the "
          "approval (D45: never before it)")  # fmt: skip
    since, mark = co.mark(), co.last_audit_id()
    r = co.decide(director, rfq, "approve", note="eval: agreed")
    if not check(
        r.status_code == 200,
        f"P1: the director's approval answered {r.status_code} {r.text[:200]}",
    ):
        return rfq, []
    body = js(r)
    contracts = [str(c) for c in body.get("contracts") or []] or [
        str(c["code"]) for c in co.contracts_of(rfq)
    ]
    check(
        int(body.get("letters") or 0) == 3,
        f"P1 approval answer: letters {body.get('letters')}; expected 3",
        "medium",
    )
    co.work()
    winner = f.mails_to(co, contacts["SUP-005"]["email"], since)
    winner = [
        x for x in winner if "notice of selection" in str(x.get("body", "")).lower()
    ]
    if check(
        len(winner) == 1,
        f"P1: {len(winner)} award mail(s) to SUP-005 ({contacts['SUP-005']['email']}), expected 1",
    ):
        text_ = str(winner[0].get("body") or "")
        check(
            f.norm(f.NOTICE) in f.norm(text_),
            f"P1 award mail lacks '{f.NOTICE}': {text_[:400]}",
        )
        check(rfq in text_ and ("Copper winding wire" in text_ or f.WIRE in text_) and "880" in text_
              and "/contract/" in text_ + str(winner[0].get("html") or ""),
              f"P1 award mail lacks the RFQ, the item, the price or the signed link: {text_[:400]}")  # fmt: skip
        check(any(c in text_ for c in contracts) and bool(contracts), f"P1 award mail does not name the contract "
              f"{contracts}", "medium")  # fmt: skip
        attached = str(winner[0].get("attachments") or "")
        check(
            "pdf" in attached.lower(),
            f"P1 award mail has no contract PDF attached ({attached[:120]})",
            "medium",
        )
    for loser in ("SUP-003", "SUP-001"):
        got = [
            x
            for x in f.mails_to(co, contacts[loser]["email"], since)
            if rfq in str(x.get("body", "")) + str(x.get("subject", ""))
        ]
        if not check(
            len(got) == 1,
            f"P1: {len(got)} mail(s) to {loser} after the approval; expected one regret note",
        ):
            continue
        text_ = str(got[0].get("body") or "") + " " + str(got[0].get("subject") or "")
        quote = str(co.one("select no from quote where rfq_no = :r and supplier_code = :s order by revision desc limit 1",
                           r=rfq, s=loser) or "")  # fmt: skip
        words = f.REGRET.format(contact=contacts[loser]["name"], quote=quote, rfq=rfq)
        check(f.norm(words) in f.norm(text_), f"P1 regret note to {loser} is not the spec's words:\n  {text_[:400]}\n  "
              f"want: {words}", "medium")  # fmt: skip
        leaked = [
            w
            for w in forbidden(oracle, loser)
            if shows(text_.replace(rfq, " ").replace(quote, " "), w)
        ]
        m.case(
            "regret_with_price",
            f"P1 regret note to {loser}",
            "880.00" in leaked or "880" in leaked,
            False,
            True,
        )
        check(
            not leaked,
            f"P1 regret note to {loser} shows {leaked} (D45: no price, quantity, value or other name)",
        )
    rows = co.letters(d.get("id"))
    kinds = sorted((str(x.get("supplier_code")), str(x.get("kind"))) for x in rows)
    check(kinds == [("SUP-001", "regret"), ("SUP-003", "regret"), ("SUP-005", "award")] and all(x.get("message_id") for x in rows),
          f"P1 award_letter rows {kinds}, message ids {[x.get('message_id') for x in rows]}")  # fmt: skip
    award_row = next((x for x in rows if x.get("kind") == "award"), {})
    check(str(award_row.get("contract_code") or "") in contracts and bool(contracts), f"P1 award letter row names contract "
          f"{award_row.get('contract_code')}; the approval made {contracts}", "medium")  # fmt: skip
    queued = co.since("award.letter_queued", mark)
    check(
        len(queued) == 3,
        f"P1: {len(queued)} award.letter_queued audit row(s); expected 3",
        "medium",
    )
    # once: the worker again, a second approval
    count = len(co.mails(since))
    co.work()
    r = co.decide(director, rfq, "approve", note="eval: again")
    co.work()
    check(r.status_code == 409 and len(co.mails(since)) == count and len(co.letters(d.get("id"))) == 3,
          f"P1: a second approval answered {r.status_code}; mails {count} -> {len(co.mails(since))}; expected 409 and no "
          "new letter")  # fmt: skip
    out.metrics["P1 letters"] = f"{rfq}: {kinds}"
    return rfq, contracts


def _p4(out: Outcome, check: b.Checker, co: f.Co5, oracle: f.Oracle5) -> str:
    rfq, r = f.p4_proposal(check, co, regret=False)
    if not rfq or r is None:
        return rfq
    d = co.decision(rfq)
    check(d.get("regret_notes") is False, f"P4: the decision keeps regret_notes {d.get('regret_notes')}; the buyer said "
          "false", "medium")  # fmt: skip
    since = co.mark()
    r = co.decide(co.login("purchasemanager"), rfq, "approve", note="eval")
    co.work()
    check(
        r.status_code == 200,
        f"P4: the purchase manager's approval answered {r.status_code} {r.text[:200]}",
    )
    to_winner = f.mails_to(co, oracle.contacts["SUP-002"]["email"], since, rfq)
    to_loser = f.mails_to(co, oracle.contacts["SUP-003"]["email"], since, rfq)
    kinds = sorted(str(x.get("kind")) for x in co.letters(d.get("id")))
    check(len(to_winner) == 1 and not to_loser and kinds == ["award"],
          f"P4 with regret_notes false: {len(to_winner)} mail(s) to SUP-002, {len(to_loser)} to SUP-003, letter rows "
          f"{kinds}; expected the award letter only")  # fmt: skip
    out.metrics["P4 letters (regret_notes false)"] = f"{rfq}: {kinds}"
    return rfq
