"""D42 (task 1.4): an RFQ is never sent without a buyer's confirmation. `rfq.send` cannot be set to automatic, sending
refuses an RFQ with no confirmation record, a buyer confirms one RFQ or several at once, and reminders go only for a
confirmed RFQ."""

import importlib.util
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session
from test_sourcing import (  # noqa: F401 - fixtures
    PASSWORD,
    Outbox,
    _headers,
    _request,
    _source,
    db,
    outbox,
)

from p2p.agents import autonomy
from p2p.main import app
from p2p.platform import proposals, sod, users
from p2p.sourcing import reminders, service

client = TestClient(app)
D = Decimal
WED = date(2026, 11, 11)


def _buyer(db: Session, name: str = "buyer") -> int:  # noqa: F811
    return users.create_user(db.info["slug"], f"{name}{db.info['sfx']}@x.example", PASSWORD, name.title(), "Buyer")


def _two_waiting(db: Session) -> list[str]:  # noqa: F811
    first = _source(db, _request(db, "R-1", [("B", "5", None, "C1")])).rfqs
    second = _source(db, _request(db, "R-2", [("D", "5", None, "C2")])).rfqs
    return first + second


def test_rfq_send_is_locked_whatever_the_setting_says(db: Session) -> None:  # noqa: F811
    with pytest.raises(ValueError, match="always decided by a person"):
        autonomy.configure(db, sod.user(1), "rfq.send", level="off")
    db.rollback()
    db.execute(text("insert into autonomy_setting (decision_type, level, min_confidence, max_wrong_rate) "
                    "values ('rfq.send', 'auto', 0, 1)"))  # fmt: skip
    db.commit()

    gate = autonomy.gate(db, "rfq.send", value=D(1), confidence=1.0)

    assert gate.allowed is False and gate.reason == "“Send a request for quotation” is always decided by a person"


def test_the_migration_puts_a_stored_automatic_setting_back_to_propose_and_backfills_sent_rfqs(
    db: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    buyer = _buyer(db)
    (pid,) = _source(db, _request(db, "R-1", [("B", "5", None, "C1")])).proposals
    proposals.approve(db, pid, buyer)
    db.execute(text("update rfq set confirmed_at = null, confirmed_by = null, confirmed_via = null"))
    db.execute(text("insert into autonomy_setting (decision_type, level, min_confidence, max_wrong_rate) "
                    "values ('rfq.send', 'auto', 0, 1)"))  # fmt: skip
    db.commit()
    path = (
        Path(service.__file__).parents[1]
        / "platform"
        / "migrations"
        / "tenant"
        / "versions"
        / "0120_period_sourcing.py"
    )
    spec = importlib.util.spec_from_file_location("m0120", path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    for statement in migration.BACKFILL:
        db.execute(text(statement))
    db.commit()

    assert (
        db.execute(text("select level from autonomy_setting where decision_type = 'rfq.send'")).scalar_one()
        == "propose"
    )
    sent_at, at, who, via = db.execute(text("select sent_at, confirmed_at, confirmed_by, confirmed_via from rfq")).one()
    assert (at, who, via) == (sent_at, buyer, "backfill")


def test_sending_an_unconfirmed_rfq_is_refused_with_no_mail_and_no_audit_row(db: Session, outbox: Outbox) -> None:  # noqa: F811
    (rfq,) = _source(db, _request(db, "R-1", [("B", "5", None, "C1")])).rfqs

    with pytest.raises(service.SourcingError, match=f"{rfq} has no buyer's confirmation: it is not sent \\(D42\\)"):
        service.send_rfq(db, rfq)

    assert outbox.sent == []
    assert db.execute(text("select count(*) from audit_event where action = 'rfq.send'")).scalar_one() == 0
    assert db.execute(text("select count(*) from email_message where linked_type = 'rfq'")).scalar_one() == 0
    assert db.execute(text("select status, invited_at is null from rfq r join rfq_supplier s on s.rfq_no = r.no "
                           "limit 1")).one() == ("draft", True)  # fmt: skip


def test_approving_the_proposal_confirms_and_sends_in_one_go(db: Session, outbox: Outbox) -> None:  # noqa: F811
    buyer = _buyer(db)
    result = _source(db, _request(db, "R-1", [("B", "5", None, "C1")]))

    proposals.approve(db, result.proposals[0], buyer)

    who, via, at, sent = db.execute(text("select confirmed_by, confirmed_via, confirmed_at, sent_at from rfq")).one()
    assert (who, via) == (buyer, "proposal") and at is not None and sent is not None
    assert len(outbox.sent) == 3
    actions = db.execute(text("select action from audit_event where entity = 'rfq' and action like 'rfq.%' "
                              "order by id")).scalars().all()  # fmt: skip
    assert actions == ["rfq.create", "rfq.confirm", "rfq.send"]


def test_a_buyer_confirms_several_at_once_and_one_refusal_does_not_stop_the_others(
    db: Session,  # noqa: F811
    outbox: Outbox,  # noqa: F811
) -> None:
    buyer = _buyer(db)
    rfqs = _two_waiting(db)
    h = _headers(db.info["slug"], buyer, "Buyer")

    r = client.post("/api/rfqs/confirm-send", json={"rfqs": [rfqs[0], "RFQ-NOPE", rfqs[1]]}, headers=h)

    assert r.status_code == 200
    got = r.json()["results"]
    assert [(x["rfq_no"], x["status"]) for x in got] == [(rfqs[0], "sent"), ("RFQ-NOPE", "refused"), (rfqs[1], "sent")]
    assert got[0]["sent"] == 3 and got[2]["sent"] == 2 and "does not exist" in got[1]["reason"]
    assert db.execute(text("select array_agg(distinct confirmed_via) from rfq")).scalar_one() == ["bulk"]
    again = client.post("/api/rfqs/confirm-send", json={"rfqs": [rfqs[0]]}, headers=h).json()["results"][0]
    assert again["status"] == "refused" and "sent already" in again["reason"]
    assert len(outbox.sent) == 5  # nobody mailed twice


def test_confirming_needs_the_send_permission_and_a_bounded_list(db: Session, outbox: Outbox) -> None:  # noqa: F811
    asker = users.create_user(db.info["slug"], f"req{db.info['sfx']}@x.example", PASSWORD, "Req", "Requester")
    buyer = _headers(db.info["slug"], _buyer(db), "Buyer")
    rfqs = _two_waiting(db)

    refused = client.post("/api/rfqs/confirm-send", json={"rfqs": rfqs}, headers=_headers(db.info["slug"], asker,
                                                                                         "Requester"))  # fmt: skip
    assert refused.status_code == 403
    assert client.post("/api/rfqs/confirm-send", json={"rfqs": []}, headers=buyer).status_code == 422
    assert (
        client.post("/api/rfqs/confirm-send", json={"rfqs": [f"R{i}" for i in range(51)]}, headers=buyer).status_code
        == 422
    )
    assert client.post("/api/rfqs/confirm-send", json={"rfqs": "RFQ-1"}, headers=buyer).status_code == 422
    assert outbox.sent == []


def test_reminders_never_go_for_an_unconfirmed_rfq(db: Session, outbox: Outbox) -> None:  # noqa: F811
    (rfq,) = _source(db, _request(db, "R-1", [("B", "5", None, "C1")])).rfqs
    # an RFQ marked out by hand, with invitations but no confirmation record: never reminded
    db.execute(text("update rfq set status = 'sent', sent_at = now() where no = :n"), {"n": rfq})
    db.execute(text("update rfq_supplier set invited_at = :t where rfq_no = :n"),
               {"t": datetime(2026, 11, 2, 5, tzinfo=UTC), "n": rfq})  # fmt: skip
    db.commit()

    result = reminders.run(db, as_of=WED, dry_run=True)

    assert result.sent == []
    db.execute(text("update rfq set confirmed_at = now(), confirmed_by = 1, confirmed_via = 'proposal' where no = :n"),
               {"n": rfq})  # fmt: skip
    db.commit()
    assert len(reminders.run(db, as_of=WED, dry_run=True).sent) == 3


def test_a_confirmed_rfq_sent_again_mails_nobody_twice(db: Session, outbox: Outbox) -> None:  # noqa: F811
    buyer = _buyer(db)
    result = _source(db, _request(db, "R-1", [("B", "5", None, "C1")]))
    proposals.approve(db, result.proposals[0], buyer)
    sent_before = len(outbox.sent)

    assert service.send_rfq(db, result.rfqs[0]) == 0  # confirmed and out: nobody left to invite
    assert len(outbox.sent) == sent_before
