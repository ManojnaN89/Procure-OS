"""E140: the supplier agent on versions and lines (AG02, task 11b.3, DF-27, D53, spec 8.7, K5, K5b, K5c, K8), written
without the implementation.

Independence: written from the block 8 spec (sections 5 8.6-8.7, 8, 9 P, K5-K5c, K8) and the dataset only. The oracle
is each line's due date (promised, else need-by) against `supplier_date_days` (3, `tolerance.csv`), the one line a mail
names, the need date of a material requirement the line covers (planted through the demand import), the version of
the order mail a reply threads to, and the sender's registration (`supplier_contact.csv`) and verdict. Mails are stored
by the mailbox's own intake and read by the worker with a scripted model that reads as a careful person would
(`--live`: the real models read the same mails; at least 90% handled right and 0 wrong changes applied, OP-20).

Given a fresh Apex reset (Mon 23 Nov 2026), two administrators set `mail_authserv_id` mx.apexpumps.example and the gate
`supplier.change` to automatic:
- K5: P acknowledged with 7, 9, 14 Dec; on Mon 30 Nov a reply to P's version 1 mail from sales@ (pass): "The Impeller
  1 HP (RM-03) will come on 10 Dec 2026; the rest as ordered": only line 2 moves 9 -> 10 Dec; lines 1 and 3 stay; one
  `po_commitment` (line 2, version 1); audit `po.promised_date_change` with line 2.
- K5b: "All goods against P will be delivered on 12 Dec 2026": nothing moves; one task; proposed date changes for lines
  1, 2, 3 (7, 10, 14 Dec to 12 Dec).
- K5c: an order whose RM-03 line covers a material request of a production order needed on 9 Dec (the demand import,
  the store check), acknowledged on 9 Dec; the K5 mail: not applied (10 Dec passes the 9 Dec need); a task and a
  proposed change for that line.
- K8: P2 sent (M1), revised on 25 Nov (M2): "We confirm the order" (pass) to M1: not recorded, `supplier_message` noted
  "answers version 1; the order is now version 2", the fixed reply "Order <P2> has been revised (version 2, sent on
  25 Nov 2026). Please accept that version from the link in our email." with version 2's stored PDF; to M2 with verdict
  none: task `supplier_ack_confirm` "Record Deccan Castings Pvt Ltd's acknowledgement of <P2> version 2 (email from
  sales@deccancastings.example)", nothing recorded; dmarc=fail: not read, no model call, task `supplier_sender_check`;
  an unregistered address of the same domain: not read either; pass: `acknowledged_version` 2, `ack_via` email with
  the email and its verdict; version 3 answered by orders@ (a registered second contact): recorded, `ack_address`
  orders@.
Ends with a tenant check.
Mutations: every line moved by a one-line date mail; a reply to an old version recorded.
"""

from collections.abc import Callable
from datetime import date
from typing import Any

import aif
import block1 as b
import block4 as k
import block6 as s
import block8 as e
import env
from lib import Outcome, capture_mail

ID = "E140"
TITLE = "The supplier agent moves one line, never every line, and records an email acknowledgement only for its version"
REQUIREMENT = (
    "AG02, task 11b.3, DF-27, D53 (block 8 spec 8.7, K5, K5b, K5c, K8): a date for one named line moves that line only, "
    "within tolerance and with no stock effect; a date naming no line is never applied; a reply's version is its "
    "thread's; an email acknowledgement only from a registered contact with sender authentication pass, for the "
    "current version; none -> the buyer records it; fail or unregistered -> not read; --live: >= 90% right, 0 wrong "
    "applied; tenant check"
)
PREFIX = "e140"
CONFIRM = "Dear Sir,\n\nWe confirm the order. We will deliver as per the order.\n\nRegards,\nDeccan Castings"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        e.pin(e.TODAY)
        env.forget_tenants(PREFIX)


class Judge:
    """One mail, one verdict: right or not, and whether something was applied that should not have been. Scripted:
    every wrong one is a high finding. Live: the share right (>= 90%) and the wrong changes applied (0) decide (OP-20);
    detail checks become notes."""

    def __init__(self, out: Outcome, check: b.Checker, live: bool) -> None:
        self.out, self.check, self.live = out, check, live
        self.cases: list[tuple[str, bool, bool]] = []

    def __call__(self, label: str, ok: bool, wrong_applied: bool, message: str) -> None:
        self.cases.append((label, ok, wrong_applied))
        if not self.live:
            self.check(ok, message)
        elif wrong_applied:
            self.check(False, f"LIVE: {message}")
        elif not ok:
            self.out.notes.append(f"LIVE miss: {message}")

    def detail(self) -> Callable[..., bool]:
        if not self.live:
            return self.check

        def note(ok: bool, message: str, severity: str = "high") -> bool:
            if not ok:
                self.out.notes.append(f"LIVE detail ({severity}): {message}")
            return bool(ok)

        return note

    def report(self) -> None:
        right = sum(1 for _l, ok, _w in self.cases if ok)
        wrong = sum(1 for _l, _ok, w in self.cases if w)
        share = round(100 * right / len(self.cases), 1) if self.cases else 0.0
        key = "LIVE model" if self.live else "scripted model"
        self.out.metrics[f"{key}: handled right %"] = share
        self.out.metrics[f"{key}: wrong changes applied"] = wrong
        self.out.metrics[f"{key}: mails"] = len(self.cases)
        if self.live:
            self.check(share >= 90, f"LIVE: {share}% of {len(self.cases)} mails handled right; the threshold is 90% "
                       "(OP-20)", "medium")  # fmt: skip


def _run(ctx: Any) -> Outcome:
    from fastapi.testclient import TestClient

    from p2p.main import app
    from p2p.platform import mail

    out = Outcome(True)
    check = b.Checker(out)
    saved = mail.send_email
    box = capture_mail()
    notes: list[str] = []
    try:
        client = TestClient(app)
        bw = e.Co8("brightwave", PREFIX, client, box)
        apex = e.Co8("apex", PREFIX, client, box)
        e.start(apex)
        start = apex.mark()
        e.data_gaps(check, apex, contacts=True)
        apex.ensure_second_contact(notes)
        apex.set_policy(check, "mail_authserv_id", e.AUTHSERV, "set-up")
        asked, done = apex.automation("supplier.change", "auto")
        check(asked.status_code == 202 and done is not None and done.status_code == 200, f"set-up: the gate "
              f"supplier.change could not be set to automatic ({asked.status_code} {getattr(done, 'status_code', None)} "
              f"{asked.text[:150]})", "medium")  # fmt: skip
        judge = Judge(out, check, ctx.live)
        model = None if ctx.live else aif.Model(default={"type": "ack", "confidence": 0.95})
        oracle = e.Oracle8(apex)
        m = k.Mutations()
        _rule, who = oracle.approver(oracle.p()["total"], frozenset({apex.buyer()}))
        c_po, c_mail = _k5c_order(check, apex, notes)
        p1, l1, m1 = _p1(check, apex, who, notes)
        p2, tokens2 = _k8(out, check, apex, oracle, m, judge, model, who, notes)
        e.pin(e.NOV30)
        if p1:
            _k5(check, apex, m, judge, model, p1, m1)
            _k5b(check, apex, judge, model, p1, m1)
        if c_po:
            _k5c(check, apex, judge, model, c_po, c_mail)
        judge.report()
        m.report(check, out, ["every_line_moved", "old_version_reply_recorded"])
        e.pin(e.TODAY)
        e.tenant_check8(check, apex, bw, [p1, p2, c_po], [l1, *tokens2], start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += sorted(set(notes)) + [
        ("The scripted model answers what E28's careful reader writes: {type: ack | date_change, new_date, item (the "
         "line's description), confidence 0.95}; the item is never given for the mail that names no line."),
        ("Replies thread to the captured order mail's message_id (In-Reply-To, References) and go to its reply_to, "
         "else the order's plus address."),
        ("Spec gap: the K5c production order is named MO-81 when the demo already holds MO-1 (the reset loads the "
         "ERP's MO-1 needed on 5 Dec); its only line is RM-03, needed on 9 Dec."),
        ("The live result (OP-20) is this eval's metrics; `python evals/run.py --live --only E28,E140 --out "
         "evals/results/live` keeps it in evals/results/live.*."),
    ]
    return out


# ---- orders ---------------------------------------------------------------------------------------------------------


def _p1(check: b.Checker, co: e.Co8, who: str, notes: list[str]) -> tuple[str, str | None, dict[str, Any]]:
    e.pin(e.TODAY)
    po = co.p_order(check, "P", who, notes)
    if not po:
        return "", None, {}
    l1, m1 = co.send8(check, po, "P send")
    r = co.accept_code(check, l1, {1: e.DEC7, 2: e.DEC9, 3: e.DEC14}, "K5 P accepted with 7, 9, 14 Dec")
    check(r.status_code == 200 and co.promised(po) == {1: e.DEC7, 2: e.DEC9, 3: e.DEC14}, f"K5 precondition: P accepted "
          f"answered {r.status_code}; promised {co.promised(po)}; expected 7, 9, 14 Dec (K4)")  # fmt: skip
    return po, l1, m1


def _mo_number(co: e.Co8) -> str:
    return "MO-1" if not co.one("select count(*) from production_order where order_no = 'MO-1'") else "MO-81"


def _k5c_order(check: b.Checker, co: e.Co8, notes: list[str]) -> tuple[str, dict[str, Any]]:
    """K5c's order: a production order needing RM-03 on 9 Dec loaded through the demand import, the store check, its
    request approved and sourced on CNT-0001, the order sent and accepted on the date ordered. Returns (order, its
    order mail)."""
    e.pin(e.TODAY)
    mo, store = _mo_number(co), co.role("store")
    po_csv = b.csv_bytes(["order_no", "product_code", "qty", "need_by", "reason", "location_code", "status"],
                         [[mo, "FG-01", "60", e.DEC9.isoformat(), "K5c", e.LOC, ""]])  # fmt: skip
    line_csv = b.csv_bytes(["order_no", "item_code", "qty", "unit", "need_by"],
                           [[mo, "RM-03", "60.000", "NOS", e.DEC9.isoformat()]])  # fmt: skip
    for kind, body in (("production_order", po_csv), ("production_order_line", line_csv)):
        status, got = co.upload(store, kind, body)
        check(status == 200, f"K5c set-up: loading {kind} answered {status} {str(got)[:200]}", "medium")
    r = co.store_check()
    check(r.status_code == 200, f"K5c set-up: the store check answered {r.status_code} {r.text[:200]}", "medium")
    co.work()
    reqs = [x[0] for x in co.q(
        "select distinct mb.request_no from material_request_buy mb join material_request_line l on l.id = mb.mr_line_id "
        "join material_request mr on mr.no = l.mr_no where mr.order_no = :o and l.item_code = 'RM-03'", o=mo)]  # fmt: skip
    if not check(len(reqs) == 1, f"K5c set-up: {mo}'s RM-03 is bought on requests {reqs}; expected one (Q17 cover)",
                 "medium"):  # fmt: skip
        return "", {}
    req = reqs[0]
    since = co.mark()
    if co.one("select status from request where no = :n", n=req) == "draft":
        co.call(store, "POST", f"/api/requisitions/{req}/submit", {"override_reason": "K5c: a separate case"})
    co.approve(req)
    co.work()
    orders = sorted({x["po_no"] for x in co.lines_ordered(req) if x.get("item_code") == "RM-03"})
    if not check(len(orders) == 1, f"K5c set-up: request {req} ordered RM-03 on {orders}; expected one order", "medium"):
        return "", {}
    po = orders[0]
    for step in co.open_steps(po):
        co.approve_po(co.code_of(step.get("approver")) or co.buyer(), po, 1)
        co.work()
    if str(co.po_row(po).get("status")) not in ("sent", "acknowledged"):
        co.send(po)
    mail = co.last_order_mail(po, since)
    token = co.token_in(mail) if mail else None
    dates = {n: nb for n, (_p, nb) in co.line_dates(po).items() if nb}
    line = next((x for x in co.lines8(po) if x.get("item_code") == "RM-03"), {})
    check(b.day(line.get("need_by")) == e.DEC9, f"K5c set-up: {po}'s RM-03 line is needed on {line.get('need_by')}; "
          f"{mo} needs it on 9 Dec", "medium")  # fmt: skip
    r = co.accept_code(check, token, dates, "K5c set-up accept")
    check(r.status_code == 200, f"K5c set-up: accepting {po} answered {r.status_code}", "medium")
    notes.append(f"K5c: {mo} loaded with RM-03 60 needed on 9 Dec; request {req}; order {po}")
    return po, mail


# ---- K5, K5b, K5c ---------------------------------------------------------------------------------------------------


def _date_reading(co: e.Co8, po: str, new: date, item: str | None) -> dict[str, Any]:
    reading: dict[str, Any] = {"type": "date_change", "new_date": new.isoformat(), "confidence": 0.95}
    if item:
        line = next((x for x in co.lines8(po) if x.get("item_code") == item), {})
        reading["item"] = line.get("description") or item
    return reading


def _k5(check: b.Checker, co: e.Co8, m: k.Mutations, judge: Judge, model: aif.Model | None, po: str,
        m1: dict[str, Any]) -> None:  # fmt: skip
    detail = judge.detail()
    before = co.promised(po)
    cm, mark = co.commit_mark(), co.audit_mark()
    if model is not None:
        model.default = _date_reading(co, po, e.DEC10, "RM-03")
    eid = co.mail_in(po, m1, f"Sales Desk <{e.SALES}>", "Dear Sir,\n\nThe Impeller 1 HP (RM-03) will come on 10 Dec "
                     "2026; the rest as ordered.\n\nRegards,\nDeccan Castings", model, auth=(e.auth_header("pass"),))  # fmt: skip
    got = co.promised(po)
    want = {1: e.DEC7, 2: e.DEC10, 3: e.DEC14}
    m.case("every_line_moved", "K5 a date for line 2 only", got, want, {1: e.DEC10, 2: e.DEC10, 3: e.DEC10})
    judge("K5", got == want, got != want and got != before, f"K5: after the mail naming RM-03 (email {eid}) the promised "
          f"dates are {got}; only line 2 moves 9 -> 10 Dec (DF-27): {want}")  # fmt: skip
    rows = [(int(x.get("po_line_no") or 0), b.day(x.get("promised_date")), x.get("po_version"), x.get("source"))
            for x in co.commitments(po, cm)]  # fmt: skip
    detail(rows == [(2, e.DEC10, 1, "agent_date")], f"K5: new po_commitment rows {rows}; expected one: line 2, 10 Dec, "
           "version 1, agent_date (8.7.4, section 7)")  # fmt: skip
    moved = co.since("po.promised_date_change", mark)
    detail(len(moved) == 1 and str(moved[0]["after"].get("line")) == "2", f"K5: po.promised_date_change audits "
           f"{[a['after'] for a in moved]}; expected one with line 2", "medium")  # fmt: skip


def _k5b(check: b.Checker, co: e.Co8, judge: Judge, model: aif.Model | None, po: str, m1: dict[str, Any]) -> None:
    detail = judge.detail()
    before = co.promised(po)
    known = {x["id"] for x in co.changes(po)}
    tm = co.task_mark()
    if model is not None:
        model.default = _date_reading(co, po, e.DEC12, None)
    eid = co.mail_in(po, m1, f"Sales Desk <{e.SALES}>", f"Dear Sir,\n\nAll goods against {po} will be delivered on 12 "
                     "Dec 2026.\n\nRegards,\nDeccan Castings", model, auth=(e.auth_header("pass"),))  # fmt: skip
    after = co.promised(po)
    new = [x for x in co.changes(po) if x["id"] not in known]
    proposed = sorted((int(x.get("po_line_no") or 0), e.iso(x.get("old_value")), e.iso(x.get("new_value")))
                      for x in new if x.get("kind") == "date" and x.get("status") == "proposed")  # fmt: skip
    want = [(1, "2026-12-07", "2026-12-12"), (2, "2026-12-10", "2026-12-12"), (3, "2026-12-14", "2026-12-12")]
    judge("K5b", after == before and [x[0] for x in proposed] == [1, 2, 3], after != before,
          f"K5b: the mail naming no line (email {eid}) left promised {before} -> {after} and proposed {proposed}; "
          f"expected nothing moved and a proposed date per open line (OP-13)")  # fmt: skip
    detail(proposed == want, f"K5b: proposed changes {proposed}; expected {want}", "medium")
    tasks = [x for x in co.qd("select * from task where id > :i", i=tm) if po in e.blob(x).upper()]
    detail(len(tasks) == 1, f"K5b: new tasks naming {po}: {[(x.get('kind'), x.get('title')) for x in tasks]}; the spec "
           "says one", "medium")  # fmt: skip


def _k5c(check: b.Checker, co: e.Co8, judge: Judge, model: aif.Model | None, po: str, mail: dict[str, Any]) -> None:
    detail = judge.detail()
    line = next((x for x in co.lines8(po) if x.get("item_code") == "RM-03"), {})
    n = int(line.get("line_no") or 0)
    before = co.promised(po)
    known = {x["id"] for x in co.changes(po)}
    tm = co.task_mark()
    if model is not None:
        model.default = _date_reading(co, po, e.DEC10, "RM-03")
    eid = co.mail_in(po, mail, f"Sales Desk <{e.SALES}>", "Dear Sir,\n\nThe Impeller 1 HP (RM-03) will come on 10 Dec "
                     "2026; the rest as ordered.\n\nRegards,\nDeccan Castings", model, auth=(e.auth_header("pass"),))  # fmt: skip
    after = co.promised(po)
    new = [(int(x.get("po_line_no") or 0), e.iso(x.get("new_value"))) for x in co.changes(po)
           if x["id"] not in known and x.get("kind") == "date"]  # fmt: skip
    judge("K5c", after == before and new == [(n, "2026-12-10")], after != before,
          f"K5c: the K5 mail on {po} (email {eid}) left promised {before} -> {after}, proposed {new}; 10 Dec passes the "
          f"9 Dec need of the material request line {n} covers: not applied, a proposed change for line {n} (8.7.4)")  # fmt: skip
    tasks = [x for x in co.qd("select * from task where id > :i", i=tm) if po in e.blob(x).upper()]
    detail(len(tasks) >= 1, f"K5c: no task naming {po} after the mail with a stock effect", "medium")


# ---- K8 -------------------------------------------------------------------------------------------------------------


def _k8(out: Outcome, check: b.Checker, co: e.Co8, oracle: e.Oracle8, m: k.Mutations, judge: Judge,
        model: aif.Model | None, who: str, notes: list[str]) -> tuple[str, list[str | None]]:  # fmt: skip
    detail = judge.detail()
    e.pin(e.TODAY)
    po = co.p_order(check, "K8 P2", who, notes)
    if not po:
        return "", []
    l1, m1 = co.send8(check, po, "K8 send")
    co.place(po, 1, "sent_at", e.TODAY)
    e.pin(e.NOV25)
    since = co.mark()
    r = co.revise8(po, 3, qty=140, version=1, reason="plan cut")
    m2 = co.last_order_mail(po, since)
    check(r.status_code == 200 and bool(m2), f"K8: the revision to version 2 answered {r.status_code}; mail M2 "
          f"{bool(m2)}", "medium")  # fmt: skip
    for col in ("made_at", "sent_at"):
        co.place(po, 2, col, e.NOV25)
    out_v = co.qd("select po_version from email_message where direction = 'out' and linked_id = :n order by id", n=po)
    detail([x.get("po_version") for x in out_v][-2:] == [1, 2], f"K8: email_message.po_version of the order mails "
           f"{[x.get('po_version', 'missing') for x in out_v]}; expected 1 then 2 (8.7.2)", "medium")  # fmt: skip
    if model is not None:
        model.default = {"type": "ack", "confidence": 0.95}
    sender = f"Sales Desk <{e.SALES}>"

    # (a) a reply to version 1's mail: not recorded, noted, the fixed reply with version 2's stored PDF
    since = co.mark()
    eid = co.mail_in(po, m1, sender, CONFIRM, model, auth=(e.auth_header("pass"),))
    ackv = co.po_row(po).get("acknowledged_version")
    m.case("old_version_reply_recorded", "K8 a confirmation replying to version 1's mail", ackv, None, 2)
    judge("K8 reply to M1", ackv is None, ackv is not None, f"K8: a confirmation threaded to version 1's mail (email "
          f"{eid}) made acknowledged_version {ackv}; an old reply never acknowledges new terms (8.7.3, OP-12)")  # fmt: skip
    msgs = co.messages(eid)
    detail(bool(msgs) and msgs[-1].get("status") == "noted" and "answers version 1; the order is now version 2"
           in e.blob(msgs[-1]), f"K8: supplier_message of the reply {[(x.get('status'), x.get('action')) for x in msgs]}"
           "; expected noted 'answers version 1; the order is now version 2'")  # fmt: skip
    words = (f"Order {po} has been revised (version 2, sent on 25 Nov 2026). Please accept that version from the link in "
             "our email.")  # fmt: skip
    fixed = co.mails_to(e.SALES, since, words)
    detail(len(fixed) == 1, f"K8: {len(fixed)} reply mail(s) to {e.SALES} with the fixed words '{words}'")
    pdf = co.version_pdf(po, 2)
    stored = e.t.sha(pdf.content) if pdf.status_code == 200 and pdf.content[:5] == b"%PDF-" else None
    attached = [e.t.sha(x) for x in e.t.attached_pdfs(fixed[-1])] if fixed else []
    detail(stored is not None and attached == [stored], f"K8: the fixed reply's PDF {attached}; version 2's stored "
           f"document {stored} (B7-5)", "medium")  # fmt: skip

    # (b) verdict none: the buyer records it
    tm = co.task_mark()
    eid = co.mail_in(po, m2, sender, CONFIRM, model)
    ackv = co.po_row(po).get("acknowledged_version")
    tasks = co.new_tasks("supplier_ack_confirm", tm)
    title = (f"Record {oracle.supplier_name(e.SUP)}'s acknowledgement of {po} version 2 (email from {e.SALES})")
    judge("K8 none", ackv is None and len(tasks) == 1, ackv is not None, f"K8: a confirmation with no authentication "
          f"(email {eid}) made acknowledged_version {ackv}, tasks supplier_ack_confirm {len(tasks)}; expected nothing "
          "recorded and one task (OP-4 interim)")  # fmt: skip
    detail(bool(tasks) and e.norm(title) in e.norm(tasks[-1].get("title")) and s.person_is(co, tasks[-1].get("assignee"),
           co.buyer()), f"K8: the task {[(x.get('title'), co.code_of(x.get('assignee'))) for x in tasks]}; expected "
           f"'{title}' for {co.buyer()}", "medium")  # fmt: skip

    # (c) dmarc=fail and (c2) an unregistered address: not read, no model call, a sender check
    for label, frm, auth in (("K8 fail", sender, (e.auth_header("fail"),)),
                             ("K8 unregistered", "Dispatch <dispatch@deccancastings.example>", (e.auth_header("pass"),))):  # fmt: skip
        tm, mm = co.task_mark(), co.model_mark()
        calls = model.calls if model is not None else 0
        eid = co.mail_in(po, m2, frm, CONFIRM, model, auth=auth)
        ackv = co.po_row(po).get("acknowledged_version")
        read = (model.calls != calls if model is not None else False) or any(
            "supplier" in str(x.get("step") or "") for x in co.model_calls_since(mm))  # fmt: skip
        tasks = co.new_tasks("supplier_sender_check", tm)
        judge(label, ackv is None and not read and len(tasks) == 1, ackv is not None, f"{label}: email {eid} made "
              f"acknowledged_version {ackv}, read by a model {read}, supplier_sender_check tasks {len(tasks)}; "
              "expected not read, nothing recorded, one task (8.7.1)")  # fmt: skip

    # (d) pass from the registered contact: version 2 acknowledged by email
    e.pin(e.NOV26)
    eid = co.mail_in(po, m2, sender, CONFIRM, model, auth=(e.auth_header("pass"),))
    ackv = co.po_row(po).get("acknowledged_version")
    judge("K8 pass", ackv == 2, ackv not in (None, 2), f"K8: an authenticated confirmation to M2 (email {eid}) made "
          f"acknowledged_version {ackv}; expected 2 via email")  # fmt: skip
    row = co.po_versions(po).get(2, {})
    verdict = (k.as_json(row.get("ack_sender_auth")) or {}).get("verdict") if isinstance(
        k.as_json(row.get("ack_sender_auth")), dict) else None  # fmt: skip
    detail(row.get("ack_via") == "email" and str(row.get("ack_email_id")) == str(eid) and verdict == "pass",
           f"K8: po_version 2 ack_via {row.get('ack_via')}, ack_email_id {row.get('ack_email_id', 'missing')}, "
           f"ack_sender_auth {row.get('ack_sender_auth', 'missing')}; expected email, {eid}, verdict pass")  # fmt: skip

    # (e) version 3 answered by the registered second contact
    e.pin(e.NOV27)
    since = co.mark()
    r = co.revise8(po, 3, qty=130, version=2, reason="plan cut again")
    m3 = co.last_order_mail(po, since)
    detail(r.status_code == 200 and bool(m3), f"K8: version 3 answered {r.status_code}; mail M3 {bool(m3)}", "medium")
    eid = co.mail_in(po, m3 or m2, f"Order Desk <{e.ORDERS}>", CONFIRM, model, auth=(e.auth_header("pass"),))
    ackv = co.po_row(po).get("acknowledged_version")
    judge("K8 orders@", ackv == 3, ackv not in (None, 3), f"K8: orders@'s confirmation of version 3 (email {eid}) made "
          f"acknowledged_version {ackv}; expected 3 (a registered contact)")  # fmt: skip
    detail(str(co.po_versions(po).get(3, {}).get("ack_address") or "").lower() == e.ORDERS, f"K8: po_version 3 "
           f"ack_address {co.po_versions(po).get(3, {}).get('ack_address', 'missing')}; expected {e.ORDERS}", "medium")  # fmt: skip
    return po, [l1, co.token_in(m2) if m2 else None]
