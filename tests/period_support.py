"""A demo company to source in for block 5 (period RFQs, award approval, contracts from awards): Apex Pumps or
Brightwave as `demo reset` leaves them, signed in as their role logins, the mail caught, and the period RFQs of one test
cleared before the next (a module shares one company: a reset takes the better part of a minute)."""

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from p2p.formulas.calendar import pin_today
from p2p.main import app
from p2p.platform import demo, mail, routing, tenants, users, worker
from p2p.platform.tokens import create_access_token

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)
D = Decimal
P1_DAY = date(2026, 11, 23)
CLEAR = (
    "delete from award_letter",
    "delete from contract_link",
    # a later request bought on a contract from an award (P1-use) keeps the price it used: let go of it
    "update sourcing_decision set price_record_id = null where price_record_id in (select p.id from price_record p "
    "join contract c on c.code = p.contract_code where c.source_rfq is not null)",
    "update po_line set price_record_id = null where price_record_id in (select p.id from price_record p "
    "join contract c on c.code = p.contract_code where c.source_rfq is not null)",
    "delete from price_record where contract_code in (select code from contract where source_rfq is not null)",
    "delete from contract where source_rfq is not null",
    "delete from award where decision_id is not null",
    "delete from award_decision",
    "delete from quote_line where quote_no in (select q.no from quote q join rfq r on r.no = q.rfq_no "
    "where r.kind = 'period')",
    "delete from quote where rfq_no in (select no from rfq where kind = 'period')",
    "delete from rfq_supplier where rfq_no in (select no from rfq where kind = 'period')",
    "delete from rfq_line where rfq_no in (select no from rfq where kind = 'period')",
    "delete from rfq where kind = 'period'",
    "delete from period_demand_line",
    "update proposal set status = 'expired' where status = 'pending'",
    "update task set status = 'done' where status = 'open' and kind <> 'strategic_review'",
    "delete from one_time_code",
)


@dataclass
class Co:
    slug: str
    db: Session
    mails: list[dict[str, Any]] = field(default_factory=list)
    domain: str = "apexpumps.example"

    def uid(self, login: str) -> int:
        found = users.user_id_by_email(self.slug, f"{login}@{self.domain}")
        assert found is not None, login
        return found

    def h(self, login: str) -> dict[str, str]:
        uid = self.uid(login)
        role = self.db.execute(text("select role from user_profile where user_id = :u"), {"u": uid}).scalar_one()
        return {"Authorization": f"Bearer {create_access_token(uid, self.slug, str(role))}"}

    def call(self, login: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        self.db.rollback()
        r = client.request(method, path, json=body, headers=self.h(login))
        self.db.rollback()
        return r

    def one(self, sql: str, **params: Any) -> Any:
        self.db.rollback()
        return self.db.execute(text(sql), params).first()

    def all(self, sql: str, **params: Any) -> list[Any]:
        self.db.rollback()
        return list(self.db.execute(text(sql), params).all())

    def work(self) -> None:
        """Run this company's queued jobs (emails, sourcing, ordering) until none are left."""
        self.db.commit()
        for _ in range(80):
            if not worker.run_once(tenant=self.slug):
                break
        self.db.rollback()

    def sent_to(self, address: str) -> list[dict[str, Any]]:
        return [m for m in self.mails if str(m["to"]).lower() == address.lower()]

    def contact(self, supplier: str) -> str:
        return str(
            self.one(
                "select email from supplier_contact where supplier_code = :s and email is not null "
                "order by is_primary desc nulls last, id limit 1",
                s=supplier,
            )[0]
        )

    # ---- the steps of a period RFQ --------------------------------------------------------------------------

    def period_rfq(self, location: str, start: str, end: str, lines: list[tuple[str, str]], **extra: Any) -> Any:
        return self.call(
            "buyer", "POST", "/api/rfqs/period",
            {"location_code": location, "period_from": start, "period_to": end,
             "lines": [{"item_code": i, "qty": q} for i, q in lines], **extra},
        )  # fmt: skip

    def confirm(self, *rfqs: str) -> Any:
        return self.call("buyer", "POST", "/api/rfqs/confirm-send", {"rfqs": list(rfqs)})

    def quote(self, rfq: str, supplier: str, prices: list[str], *, valid_until: str = "2026-12-31") -> Any:
        r = self.call(
            "buyer", "POST", f"/api/rfqs/{rfq}/quotes",
            {"supplier_code": supplier, "valid_until": valid_until, "freight": "0",
             "lines": [{"rfq_line_no": n, "unit_price": p, "gst_rate": "18", "delivery_days": 5}
                       for n, p in enumerate(prices, start=1)]},
        )  # fmt: skip
        assert r.status_code == 200, r.text
        return r

    def award(self, rfq: str, choices: dict[int, str], **extra: Any) -> Any:
        return self.call(
            "buyer", "POST", f"/api/rfqs/{rfq}/award", {"choices": {str(k): v for k, v in choices.items()}, **extra}
        )

    def link(self, supplier: str) -> str:
        """The token of the contract link in the award letter to the supplier."""
        letters = [m for m in self.sent_to(self.contact(supplier)) if m["subject"].startswith("Award notice")]
        assert letters, f"no award letter to {supplier}"
        found = re.search(r"/contract/([A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)", letters[-1]["body"])
        assert found, letters[-1]["body"]
        return found.group(1)

    def code_for(self, address: str) -> str:
        mails = [m for m in self.sent_to(address) if "one-time code" in m["body"]]
        assert mails, f"no code sent to {address}"
        found = re.search(r"code is (\d{6})", mails[-1]["body"])
        assert found
        return found.group(1)


def make(control_db: str, company: str, domain: str) -> Iterator[Co]:
    """A fresh demo company for a test module, with its mail caught; dropped at the end."""
    slug = company[:4] + "p" + uuid.uuid4().hex[:8]
    co = Co(slug, Session(), domain=domain)
    previous = mail.send_email

    def catch(to: str, subject: str, body: str, **kw: Any) -> str:
        co.mails.append({"to": to, "subject": subject, "body": body, **kw})
        return str(kw.get("message_id"))

    mail.send_email = catch  # type: ignore[assignment]
    try:
        pin_today(P1_DAY)
        demo.reset_company(demo.company(company), None, credentials=None, slug=slug)
        with Session(routing.engine_for(slug)) as session:
            co.db = session
            yield co
    finally:
        mail.send_email = previous  # type: ignore[assignment]
        routing.clear_engine_cache()
        tenants.drop_tenant(slug)


def clear(co: Co) -> None:
    """Take away what the last test made: period RFQs, their awards, contracts, letters and codes."""
    co.db.rollback()
    for sql in CLEAR:
        co.db.execute(text(sql))
    co.db.commit()
    co.mails.clear()


def p1_to_proposal(co: Co, *, regret_notes: bool | None = None) -> str:
    """P1 up to the award waiting for the director: the period RFQ, sent, three quotes, SUP-005 selected."""
    made = co.period_rfq("PLT-CKN", "2027-01-01", "2027-03-31", [("RM-08", "900")])
    assert made.status_code == 201, made.text
    rfq = str(made.json()["rfq_no"])
    assert co.confirm(rfq).json()["results"][0]["status"] == "sent"
    for supplier, price in (("SUP-005", "880.00"), ("SUP-003", "895.00"), ("SUP-001", "910.00")):
        co.quote(rfq, supplier, [price])
    extra = {} if regret_notes is None else {"regret_notes": regret_notes}
    proposed = co.award(rfq, {1: "SUP-005"}, **extra)
    assert proposed.status_code == 202, proposed.text
    co.mails.clear()  # the RFQ's own mails; what follows is the award's
    return rfq
