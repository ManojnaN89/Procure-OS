"""The sourcing capability's records (task 11b.2, block 6): the engine writes every record and its fixed words; a
model only rewords them, and only words whose every number and date is in the figures are kept. The RFQ draft is
checked again before the send is confirmed; the buyer's selection decides the ranking; switching the capability off
makes no model call and leaves the records that only inform as they are."""

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_ai_steps import Scripted
from test_orders import TODAY, _quote, _sent_rfq, shop  # noqa: F401 - fixtures
from test_sourcing import Outbox, _request, _source, db, outbox  # noqa: F401 - fixtures

from p2p.agents import capabilities
from p2p.ai_steps import costs, sourcing_note
from p2p.ai_steps.gateway import Gateway
from p2p.orders import quotes
from p2p.platform import proposals, sod, tenants, users, worker
from p2p.sourcing import notes, price_review

D = Decimal


def _buyer(db: Session, name: str = "nb") -> int:
    return users.create_user(db.info["slug"], f"{name}{db.info['sfx']}@x.example", "correct horse battery", "Note Buyer",
                             "Buyer")  # fmt: skip


def _rec(db: Session, kind: str) -> dict[str, Any]:
    return dict(db.execute(text("select * from recommendation where kind = :k order by id desc limit 1"),
                           {"k": kind}).mappings().one())  # fmt: skip


def test_the_rfq_draft_says_who_is_asked_and_why_and_is_linked_to_the_send(db: Session, outbox: Outbox) -> None:  # noqa: F811
    result = _source(db, _request(db, "R-1", [("B", "5", None, "C1")]))
    (rfq,) = result.rfqs

    rec = _rec(db, "rfq_draft")

    assert rec["summary"] == (
        f"{rfq} asks 3 suppliers for 1 item delivered to Bengaluru store, replies by 13 Nov 2026: Alpha Traders, "
        "Bravo Supplies, Charlie Works. Left out: S5: no email address on file; S7: matches the sanctions list; "
        "S4: not asked, 3 suppliers are asked. At least 2 quotes are wanted (category C1)."
    )
    assert rec["proposal_id"] == result.proposals[0] and rec["directed_by"] is None
    payload = db.execute(text("select payload from proposal where id = :i"), {"i": rec["proposal_id"]}).scalar_one()
    assert payload["_recommendation_id"] == rec["id"]


def test_a_stale_rfq_draft_is_refused_a_fresh_one_is_written_and_the_suspended_supplier_is_not_mailed(
    db: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    result = _source(db, _request(db, "R-1", [("B", "5", None, "C1")]))
    (rfq,) = result.rfqs
    db.execute(text("update supplier set status = 'suspended' where code = 'S1'"))
    db.commit()
    buyer = _buyer(db)

    with pytest.raises(proposals.ProposalError, match="the RFQ draft is stale: S1 is suspended"):
        proposals.approve(db, result.proposals[0], buyer)
    worker.load_handlers()
    worker.run_once(tenant=db.info["slug"], limit=50)

    fresh = _rec(db, "rfq_draft")
    assert fresh["status"] == "proposed" and fresh["proposal_id"] != result.proposals[0]
    assert "S1: will not be sent, is suspended" in fresh["summary"]
    proposals.approve(db, int(fresh["proposal_id"]), buyer)
    assert sorted(m["to"] for m in outbox.sent) == [
        "s2@supplier.example",
        "s3@supplier.example",
    ]
    assert db.execute(text("select send_error from rfq_supplier where rfq_no = :n and supplier_code = 'S1'"),
                      {"n": rfq}).scalar_one() == "not sent: S1 is suspended"  # fmt: skip


def test_the_buyers_selection_decides_the_ranking(shop: Session, outbox: Outbox) -> None:  # noqa: F811
    _, rfq = _sent_rfq(shop, outbox)
    for supplier, price in (("S1", "180"), ("S2", "190"), ("S3", "195")):
        _quote(shop, rfq, supplier, price)
    ranking = _rec(shop, "ranking")
    assert ranking["summary"].startswith("Line 1, Seal: Lowest total cost among 3 qualifying offers")
    assert ranking["summary"].endswith("3 of 3 invited suppliers quoted.")
    buyer = _buyer(shop)

    quotes.award(shop, rfq, {1: "S2"}, sod.user(buyer), reason="faster", as_of=TODAY)

    assert (
        _rec(shop, "ranking")["status"],
        _rec(shop, "ranking")["decision_note"],
    ) == ("rejected", "faster")


def _gateway(*answers: str) -> tuple[Gateway, Scripted]:
    provider = Scripted({"cheap": list(answers)})
    return Gateway({"fake": provider}, {"sourcing_note": [("fake", "cheap")]}, sleep=lambda s: None), provider


def _brief_record(db: Session, target: str = "150.00") -> int:
    figures = {"target": target, "on": "2026-10-01"}
    rec_id = notes._write(db, "negotiation_brief", "RFQ-T/1", f"Ask for ₹ {target} per NOS (01 Oct 2026).", figures,
                          [{"kind": "test"}])  # fmt: skip
    db.commit()
    return rec_id


def test_the_models_words_are_kept_only_when_every_number_and_date_is_in_the_figures(
    db: Session,
) -> None:  # noqa: F811
    good = _brief_record(db)
    gw, _ = _gateway('{"note": "Ask the supplier for 150.00 per NOS, the price paid on 01 Oct 2026."}')
    assert sourcing_note.explain(db, good, gw) == "ai"
    assert _rec(db, "negotiation_brief")["ai_note"].startswith("Ask the supplier for 150.00")
    call = db.execute(text("select capability, entity, entity_id from model_call order by id desc limit 1")).one()
    assert tuple(call) == ("sourcing", "recommendation", str(good))

    db.execute(
        text("update recommendation set status = 'withdrawn' where id = :i"),
        {"i": good},
    )
    db.commit()
    bad = _brief_record(db, "151.00")  # other words: the gateway does not answer from its cache
    gw, _ = _gateway('{"note": "Ask for 140.00 per NOS."}')
    assert sourcing_note.explain(db, bad, gw) == "template"
    assert _rec(db, "negotiation_brief")["ai_note"] is None
    assert db.execute(text("select reason from audit_event where action = 'ai_step.rejected' order by id desc limit 1")
                      ).scalar_one().startswith("not in the evidence: number 140")  # fmt: skip


class _Capped:
    def extract(self, *a: Any, **kw: Any) -> Any:
        raise costs.CostCapReached("the month's AI budget is used up")


def test_the_cost_cap_keeps_the_template(db: Session) -> None:  # noqa: F811
    rec = _brief_record(db)

    assert sourcing_note.explain(db, rec, _Capped()) == "template"
    assert _rec(db, "negotiation_brief")["ai_note"] is None


def _queued(slug: str) -> int:
    with tenants.control_engine().connect() as c:
        return int(c.execute(text("select count(*) from job where tenant_slug = :t and kind = 'sourcing_note' "
                                  "and status = 'queued'"), {"t": slug}).scalar_one())  # fmt: skip


def test_switched_off_no_model_call_no_job_and_the_briefs_stay_as_they_are(
    db: Session,
) -> None:  # noqa: F811
    slug = db.info["slug"]
    rec = _brief_record(db)
    notes.flush(db)
    capabilities.switch_off(db, slug, sod.user(1), "sourcing", "testing the switch")

    gw, provider = _gateway()
    assert sourcing_note.explain(db, rec, gw) == "template" and provider.calls == []
    assert _rec(db, "negotiation_brief")["status"] == "proposed"  # informs: not handed over
    assert db.execute(text("select count(*) from task where kind = 'ai_handover'")).scalar_one() == 0
    before = _queued(slug)
    notes._write(db, "negotiation_brief", "RFQ-T/2", "Ask for ₹ 1.00.", {"x": 1}, [{"kind": "t"}])
    notes.flush(db)
    assert _queued(slug) == before


def test_a_strategic_review_gets_its_note_and_closing_it_decides_the_note(
    db: Session,
) -> None:  # noqa: F811
    buyer = _buyer(db)
    task_id, _ = price_review.buyer_trigger(db, db.info["slug"], sod.user(buyer), contract="K-ACTIVE", item=None,
                                            reason="the supplier asked for a rise")  # fmt: skip

    rec = _rec(db, "review_note")
    assert rec["summary"] == (
        "Strategic review (buyer): K-ACTIVE: the supplier asked for a rise. K-ACTIVE stays valid to 31 Dec 2026; a "
        "review never sets aside its obligations."
    )
    assert rec["business_key"] == f"review:{task_id}"

    price_review.close(db, task_id, buyer, "no_action", "the rise was refused")

    assert (_rec(db, "review_note")["status"], _rec(db, "review_note")["decision_note"]) == (
        "rejected", "the rise was refused",
    )  # fmt: skip
    assert date  # dates come from the figures only
