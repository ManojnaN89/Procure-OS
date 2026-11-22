"""E96: a request for quotation reaches each supplier once (DF-28, email half), written without the implementation.

Independence: written from the AI foundation spec (2.2 "send_rfq", 2.1 `rfq_supplier.message_id`) only.

Scenario, on the main demo company (Apex): A0, the requests approved, `p2p.cli source` makes the quotation requests
(the `rfq.send` decision waits for a buyer unless it is automatic). One supplier of the RFQ with the most suppliers gets an
address the mail server refuses. Then three senders at once: the buyer approves the `rfq.send` proposal through the API,
and two more `send_rfq` calls (a sweep, a retry) run in their own sessions; afterwards `p2p.cli source --request NO` and
the worker run again. Checks: one message per supplier with a good address, none twice, each matching its
`rfq_supplier.message_id`; `invited_at` set once each and not moved by the later senders; one `rfq.send` audit row with
`queued` equal to the suppliers; the refused supplier's `send_error` filled and shown by `GET /api/rfqs/{no}`.
Mutations caught: a commit inside the `send_rfq` loop (a second sender mails a supplier again); `invited_at` set after
SMTP (moved by the later senders, or a double).
"""

import threading
from typing import Any

from sqlalchemy import text

import aif
import block1 as b
import block2 as s2
import env
from block1 import js
from lib import Outcome

ID = "E96"
TITLE = "A request for quotation reaches each supplier once, even with several senders at once"
REQUIREMENT = (
    "DF-28 email half (spec 2.2): send_rfq queues every supplier's mail and sets invited_at and message_id in one "
    "transaction under the RFQ lock, commits once, then sends; a second sender finds nobody left; a refused address "
    "does not hold up the others and its error is shown on the RFQ page"
)
PREFIX = "e96"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        env.forget_tenants(PREFIX)


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient
    from p2p.main import app

    out = Outcome(True)
    check = b.Checker(out)
    smtp = aif.install_smtp()
    co = s2.Co("apex", PREFIX, TestClient(app))
    with aif.no_live_models(aif.Model()):
        co.base_state()
        for no in sorted(co.first_requests):
            co.approve(no)
        b.cli("source", co.slug)
        rfqs = co.q("select r.no, r.request_no, r.proposal_id, count(s.*) from rfq r join rfq_supplier s on "
                    "s.rfq_no = r.no group by r.no, r.request_no, r.proposal_id order by count(s.*) desc, r.no")  # fmt: skip
        if not check(bool(rfqs), "set-up: sourcing made no request for quotation with suppliers", "medium"):
            return out
        rfq, request_no, pid, n = str(rfqs[0][0]), str(rfqs[0][1]), rfqs[0][2], int(rfqs[0][3])
        check(n >= 2, f"set-up: {rfq} asks {n} supplier(s); the race needs two or more", "medium")
        sent_before = int(co.one("select count(*) from rfq_supplier where rfq_no = :r and invited_at is not null",
                                 r=rfq) or 0)  # fmt: skip
        bad = None
        if sent_before == 0 and n >= 2:
            bad = str(co.one("select supplier_code from rfq_supplier where rfq_no = :r order by supplier_code desc "
                             "limit 1", r=rfq))  # fmt: skip
            co.db.execute(text("update rfq_supplier set email = 'nobody@bounce-eval.example' where rfq_no = :r and "
                                 "supplier_code = :s"), {"r": rfq, "s": bad})  # fmt: skip
            co.db.commit()
            smtp.bounce.add("bounce-eval")
        audit0 = co.last_audit_id()
        smtp.slow = 0.3
        _race(co, rfq, pid)
        smtp.slow = 0.0
        first = {str(r[0]): r[1] for r in co.q("select supplier_code, invited_at from rfq_supplier where rfq_no = :r",
                                               r=rfq)}  # fmt: skip
        b.cli("source", co.slug, "--request", request_no)
        aif.run_jobs(co.slug, ["email"], now=aif.later(1))
        _checks(check, co, smtp, rfq, bad, first, audit0, sent_before)
    out.metrics.update({"checks": check.done, "failed": len(check.failed), "suppliers": n})
    out.notes += [
        (
            "Spec gap: whether the later senders write their own `rfq.send` audit row (with queued 0) is not said; the eval "
            "requires exactly one row with suppliers queued and lists others as low."
        ),
        (
            "When `rfq.send` is automatic for the company, sourcing has already sent; the race then runs the two extra "
            "senders only."
        ),
    ]
    return out


def _race(co: s2.Co, rfq: str, pid: Any) -> None:
    from fastapi.testclient import TestClient
    from p2p.main import app
    from p2p.sourcing import service

    gate = threading.Barrier(3)
    headers = co.headers(co.role("buyer"))
    answers: list[Any] = [None, None, None]

    def approve() -> None:
        c = TestClient(app)
        gate.wait(timeout=30)
        if pid is not None:
            answers[0] = c.post(f"/api/admin/proposals/{pid}/approve", json={}, headers=headers).status_code

    def sender(i: int) -> None:
        s = aif.new_session(co)
        try:
            gate.wait(timeout=30)
            answers[i] = service.send_rfq(s, rfq)
            s.commit()
        except Exception as exc:  # noqa: BLE001 - refusing a second sender is fine; mailing twice is not
            answers[i] = type(exc).__name__
            s.rollback()
        finally:
            s.close()

    threads = [threading.Thread(target=approve), threading.Thread(target=sender, args=(1,)),
               threading.Thread(target=sender, args=(2,))]  # fmt: skip
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)


def _checks(
    check: b.Checker, co: s2.Co, smtp: aif.Smtp, rfq: str, bad: str | None, first: dict[str, Any], audit0: int,
    sent_before: int,
) -> None:  # fmt: skip
    suppliers = aif.rows(co, "select * from rfq_supplier where rfq_no = :r order by supplier_code", r=rfq)
    has_mid = bool(suppliers) and "message_id" in suppliers[0]
    check(has_mid, "rfq_supplier has no message_id column (spec 2.1)")
    for s in suppliers:
        code, email = str(s["supplier_code"]), str(s["email"])
        got = [m for m in smtp.sent if str(m["to"]).lower() == email.lower() and rfq in str(m["subject"])]
        check(s.get("invited_at") is not None, f"{rfq} {code}: invited_at not set")
        check(first.get(code) == s.get("invited_at"), f"{rfq} {code}: invited_at moved from {first.get(code)} to "
              f"{s.get('invited_at')} after the later senders")  # fmt: skip
        if code == bad:
            check(not got, f"{rfq} {code}: a message counted as sent to the refused address")
            check(bool(s.get("send_error")), f"{rfq} {code}: the refused address has no send_error")
            continue
        check(len(got) == 1, f"{rfq} {code}: {len(got)} message(s) to {email}, expected exactly 1")
        if has_mid and got:
            check(got[0]["message_id"] == s.get("message_id"), f"{rfq} {code}: the message left as "
                  f"{got[0]['message_id']}, rfq_supplier.message_id is {s.get('message_id')}", "medium")  # fmt: skip
    audits = aif.audit_after(co, "rfq.send", audit0)
    mine = [a for a in audits if str(a.get("entity_id")) == rfq]
    queued = [a for a in mine if int((a.get("after") or {}).get("queued") or 0) > 0]
    if sent_before == 0:
        check(len(queued) == 1 and int(queued[0]["after"]["queued"]) == len(suppliers), f"{rfq}: rfq.send audit rows "
              f"{[a.get('after') for a in mine]}, expected one with queued {len(suppliers)}")  # fmt: skip
        check(len(mine) == len(queued), f"{rfq}: {len(mine) - len(queued)} extra rfq.send audit row(s) with nothing "
              "queued", "low")  # fmt: skip
    if bad:
        page = js(co.call(co.role("buyer"), "GET", f"/api/rfqs/{rfq}"))
        shown = [s for s in page.get("suppliers") or [] if s.get("code") == bad]
        check(bool(shown) and shown[0].get("error"), f"the RFQ page does not show the refused supplier's error "
              f"({shown[:1]})")  # fmt: skip
