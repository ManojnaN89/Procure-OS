"""E139: the supplier's change is the buyer's decision (D53 "a change request is a proposal the buyer decides", spec
8.4, K6, K6b), written without the implementation.

Independence: written from the block 8 spec (sections 5 8.4, 8, 9 P, K6, K6b) and the dataset only. The oracle decides
which version each accepted change makes and whether it is material by the block 4/7 rule (`block4.material` from the
last approved version: a need-by moved more than `supplier_date_days` = 3 days is material) and who approves it
(`approval_rule.csv`, PO-02, U08).

Given a fresh Apex reset on Mon 23 Nov 2026 and P sent (L1):
- K6: with a code the contact asks line 1 7 -> 8 Dec and line 3 14 -> 21 Dec, reason "die repair": two `po_change` rows
  of version 1 sharing one task, the order untouched. buyer@ accepts line 1: version 2, 1 day, carried, and no
  acknowledgement yet; line 3 is not stale (a sibling); accepted: version 3, material (7 days), submitted to U08;
  approved and sent, the contact accepts version 3 with a code: `acknowledged_version` 3 via link.
- variant: line 3 rejected with a reason: no acknowledgement; the rejection mail carries a fresh link for version 2.
- variant: line 1 -> 8 Dec and line 3 -> 15 Dec (both within 3 days): after the second acceptance version 3 carries its
  approval and the answer acknowledges it, `ack_via` app, with the answer's change ids; not after the first.
- K6b: the contact asks line 3 150 -> 160; buyer@ moves line 1's need-by to 8 Dec (version 2); accepting the quantity
  is 409 "the order changed since the supplier asked (version 1, now version 2): ask the supplier again"; the change
  is `stale`, its task completed, audit `po.change_stale`; the order keeps 150.
Ends with a tenant check.
Mutations: a stale change accepted; the acknowledgement on the first accepted change.
"""

from typing import Any

import block1 as b
import block4 as k
import block8 as e
import env
from block1 import D
from lib import Outcome, capture_mail

ID = "E139"
TITLE = "The supplier's change is a proposal the buyer decides, bound to the version it was asked on"
REQUIREMENT = (
    "D53, task 11b.3 (block 8 spec 8.4, K6, K6b): a change from the link needs the code and records its version; each "
    "accepted change makes a version (material ones approved again by U08); the answer acknowledges only when all of "
    "it is accepted and its last version carried its approval; a rejection mails a fresh link while unacknowledged; a "
    "change asked on an older version is refused as stale; tenant check"
)
PREFIX = "e139"
STALE = "the order changed since the supplier asked (version 1, now version 2): ask the supplier again"


def run(ctx: Any) -> Outcome:
    env.forget_tenants(PREFIX)
    try:
        return _run(ctx)
    finally:
        e.pin(e.TODAY)
        env.forget_tenants(PREFIX)


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
        oracle = e.Oracle8(apex)
        m = k.Mutations()
        rule, who = oracle.approver(oracle.p()["total"], frozenset({apex.buyer()}))
        e.self_check(check, "P", {"rule": rule, "approver": who})
        orders: list[str] = []
        tokens: list[str | None] = []
        for fn in (_k6, _k6_reject, _k6_carried, _k6b):
            po, toks = fn(out, check, apex, oracle, m, who, notes)
            orders.append(po)
            tokens += toks
        m.report(check, out, ["stale_change_accepted", "ack_on_first_accepted"])
        e.tenant_check8(check, apex, bw, orders, tokens, start)
    finally:
        mail.send_email = saved  # type: ignore[assignment]
    out.metrics.update({"checks": check.done, "failed": len(check.failed)})
    out.notes += sorted(set(notes)) + [
        ("The decision is posted to /api/supplier-changes/{id}/decide (block 0, the 'existing' endpoint section 8 "
         "names), or to /api/purchase-orders/{no}/changes/{id}/decide when the first is not a route."),
        ("An accepted date change is looked for on the line's need-by or promised date (the spec says the date applies "
         "to its own line, not which field)."),
        "The rejection's reason is sent as both 'reason' and 'note'.",
    ]
    return out


def _ask(check: b.Checker, co: e.Co8, token: str | None, label: str, **fields: Any) -> Any:
    r, code, _ = co.ask_code(token)
    check(r.status_code == 200 and code is not None, f"{label}: Email me a code answered {r.status_code}")
    r = co.answer(token, "change", code or "000000", **fields)
    check(r.status_code == 200, f"{label}: asking for the change answered {r.status_code} {e.page(r)[:200]}")
    return r


def _two_changes(check: b.Checker, co: e.Co8, po: str, label: str) -> dict[int, dict[str, Any]]:
    """The answer's rows by line: version 1, one shared task, the order untouched."""
    rows = {int(x.get("po_line_no") or 0): x for x in co.changes(po) if x.get("status") == "proposed"}
    check(sorted(rows) == [1, 3] and all(x.get("kind") == "date" for x in rows.values()),
          f"{label}: proposed changes {[(n, x.get('kind'), x.get('new_value')) for n, x in rows.items()]}; expected "
          "dates on lines 1 and 3")  # fmt: skip
    check(all(x.get("po_version") == 1 for x in rows.values()), f"{label}: the changes' po_version "
          f"{[x.get('po_version', 'missing') for x in rows.values()]}; expected 1 (8.4.1)")  # fmt: skip
    tasks = {x.get("task_id") for x in rows.values()}
    check(len(tasks) == 1 and None not in tasks, f"{label}: the changes' task_id {tasks}; one shared task expected")
    p = co.po_row(po)
    check(int(p.get("version") or 0) == 1 and p.get("status") == "sent", f"{label}: the change answer moved {po} to "
          f"version {p.get('version')}, {p.get('status')}")  # fmt: skip
    return rows


def _line_date(co: e.Co8, po: str, n: int) -> set[Any]:
    promised, need = co.line_dates(po).get(n, (None, None))
    return {promised, need}


def _accept(check: b.Checker, co: e.Co8, po: str, row: dict[str, Any], label: str) -> Any:
    r = co.decide8(po, row.get("id"), True)
    check(r.status_code == 200, f"{label}: the buyer accepting change {row.get('id')} answered {r.status_code} {r.text[:200]}")
    return r


def _k6(out: Outcome, check: b.Checker, co: e.Co8, oracle: e.Oracle8, m: k.Mutations, who: str,
        notes: list[str]) -> tuple[str, list[str | None]]:  # fmt: skip
    po = co.p_order(check, "K6", who, notes)
    if not po:
        return "", []
    l1, _ = co.send8(check, po, "K6 send")
    _ask(check, co, l1, "K6", date_1=e.DEC8, date_3=e.DEC21, reason="die repair")
    rows = _two_changes(check, co, po, "K6")
    if sorted(rows) != [1, 3]:
        return po, [l1]
    _accept(check, co, po, rows[1], "K6 line 1")
    p = co.po_row(po)
    check(int(p.get("version") or 0) == 2 and p.get("status") == "sent" and p.get("approved_version") == 1
          and e.DEC8 in _line_date(co, po, 1), f"K6: after line 1 version {p.get('version')}, status {p.get('status')}, "
          f"approved_version {p.get('approved_version')}, line 1 {_line_date(co, po, 1)}; expected 2, sent, 1, 8 Dec "
          "(1 day: carried)")  # fmt: skip
    e.not_acked(check, co, po, "K6 after line 1 (8.4.4: not before the whole answer)")
    still = next((x for x in co.changes(po) if x.get("id") == rows[3].get("id")), {})
    check(still.get("status") == "proposed", f"K6: line 3's change is {still.get('status')} after its sibling made "
          "version 2; a sibling of one answer is never stale (8.4.2)")  # fmt: skip
    _accept(check, co, po, rows[3], "K6 line 3")
    p = co.po_row(po)
    steps = [co.code_of(s_.get("approver")) for s_ in co.open_steps(po)]
    check(int(p.get("version") or 0) == 3 and p.get("status") == "submitted" and steps == [who],
          f"K6: after line 3 version {p.get('version')}, status {p.get('status')}, steps {steps}; expected 3, submitted "
          f"to {who} (7 days: material)")  # fmt: skip
    e.not_acked(check, co, po, "K6 version 3 waiting for approval")
    k.approve_ok(check, co, who, po, 3, "K6 approve version 3")
    l3, _ = co.send8(check, po, "K6 send version 3")
    r = co.accept_code(check, l3, {1: e.DEC8, 2: e.DEC7, 3: e.DEC21}, "K6 version 3")
    check(r.status_code == 200, f"K6: accepting version 3 answered {r.status_code} {e.page(r)[:200]}")
    e.ack_ok(check, co, po, 3, "link", "K6 version 3", address=e.SALES)
    made = sorted((int(x.get("po_line_no") or 0), x.get("status"), x.get("version_made")) for x in co.changes(po))
    check(made == [(1, "accepted", 2), (3, "accepted", 3)], f"K6: the changes {made}; expected line 1 accepted making "
          "version 2, line 3 version 3", "medium")  # fmt: skip
    out.metrics["K6"] = {"order": po, "versions": int(co.po_row(po).get("version") or 0), "approver": who}
    return po, [l1, l3]


def _k6_reject(out: Outcome, check: b.Checker, co: e.Co8, oracle: e.Oracle8, m: k.Mutations, who: str,
               notes: list[str]) -> tuple[str, list[str | None]]:  # fmt: skip
    po = co.p_order(check, "K6 reject", who, notes)
    if not po:
        return "", []
    l1, _ = co.send8(check, po, "K6 reject send")
    _ask(check, co, l1, "K6 reject", date_1=e.DEC8, date_3=e.DEC21, reason="die repair")
    rows = _two_changes(check, co, po, "K6 reject")
    if sorted(rows) != [1, 3]:
        return po, [l1]
    _accept(check, co, po, rows[1], "K6 reject line 1")
    lm, since = co.link_mark(), co.mark()
    r = co.decide8(po, rows[3].get("id"), False, reason="we need it on the date ordered")
    check(r.status_code == 200, f"K6 reject: rejecting line 3 answered {r.status_code} {r.text[:200]}")
    e.not_acked(check, co, po, "K6 reject (8.4.5: no acknowledgement from a rejected answer)")
    new = co.respond_links(po, lm)
    mail = co.mails_to(e.SALES, since, po)
    tok = co.token_in(mail[-1]) if mail else None
    check([int(x.get("po_version") or 0) for x in new] == [2] and tok is not None, f"K6 reject: the rejection mailed "
          f"{len(mail)} mail(s) to {e.SALES}, links of versions {[x.get('po_version') for x in new]}; expected a fresh "
          "link for version 2 (8.4.5)")  # fmt: skip
    return po, [l1, tok]


def _k6_carried(out: Outcome, check: b.Checker, co: e.Co8, oracle: e.Oracle8, m: k.Mutations, who: str,
                notes: list[str]) -> tuple[str, list[str | None]]:  # fmt: skip
    po = co.p_order(check, "K6 carried", who, notes)
    if not po:
        return "", []
    l1, _ = co.send8(check, po, "K6 carried send")
    _ask(check, co, l1, "K6 carried", date_1=e.DEC8, date_3=e.DEC15, reason="die repair")
    rows = _two_changes(check, co, po, "K6 carried")
    if sorted(rows) != [1, 3]:
        return po, [l1]
    _accept(check, co, po, rows[1], "K6 carried line 1")
    first = co.po_row(po).get("acknowledged_version")
    m.case("ack_on_first_accepted", "K6 carried: after the first of two accepted changes", first, None, 2)
    e.not_acked(check, co, po, "K6 carried after line 1 (8.4.4)")
    _accept(check, co, po, rows[3], "K6 carried line 3")
    p = co.po_row(po)
    check(int(p.get("version") or 0) == 3 and p.get("status") == "acknowledged" and p.get("approved_version") == 1,
          f"K6 carried: after both version {p.get('version')}, status {p.get('status')}, approved_version "
          f"{p.get('approved_version')}; expected 3, acknowledged, 1 (both within 3 days: carried, OP-10)")  # fmt: skip
    row = e.ack_ok(check, co, po, 3, "app", "K6 carried")
    ids = sorted(int(x) for x in (k.as_json(row.get("ack_change_ids")) or []))
    check(ids == sorted(int(x.get("id")) for x in rows.values()), f"K6 carried: po_version 3 ack_change_ids "
          f"{row.get('ack_change_ids', 'missing')}; expected the answer's changes {sorted(x.get('id') for x in rows.values())}",
          "medium")  # fmt: skip
    check(co.code_of(row.get("acknowledged_by")) == co.buyer() or str(row.get("acknowledged_by") or "").endswith(
        str(co.user_id(co.buyer()))), f"K6 carried: acknowledged_by {row.get('acknowledged_by')}; expected the deciding "
          "buyer (8.4.4)", "medium")  # fmt: skip
    return po, [l1]


def _k6b(out: Outcome, check: b.Checker, co: e.Co8, oracle: e.Oracle8, m: k.Mutations, who: str,
         notes: list[str]) -> tuple[str, list[str | None]]:  # fmt: skip
    po = co.p_order(check, "K6b", who, notes)
    if not po:
        return "", []
    l1, _ = co.send8(check, po, "K6b send")
    _ask(check, co, l1, "K6b", qty_3="160", reason="a full pallet")
    rows = [x for x in co.changes(po) if x.get("status") == "proposed"]
    check(len(rows) == 1 and rows[0].get("kind") == "qty" and int(rows[0].get("po_line_no") or 0) == 3
          and rows[0].get("po_version") == 1, f"K6b: changes {[(x.get('kind'), x.get('po_line_no'), x.get('po_version', 'missing')) for x in rows]}"
          "; expected one quantity change on line 3 of version 1")  # fmt: skip
    r = co.revise8(po, 1, need_by=e.DEC8, version=1, reason="the line is needed a day later")
    check(r.status_code == 200 and int(co.po_row(po).get("version") or 0) == 2, f"K6b: moving line 1's need-by answered "
          f"{r.status_code} {r.text[:150]}; version {co.po_row(po).get('version')}")  # fmt: skip
    if not rows:
        return po, [l1]
    mark = co.audit_mark()
    r = co.decide8(po, rows[0].get("id"), True)
    e.refused(check, r, 409, STALE, "K6b accepting a change asked on version 1", "high")
    row = next((x for x in co.changes(po) if x.get("id") == rows[0].get("id")), {})
    m.case("stale_change_accepted", "K6b the quantity asked on version 1 accepted on version 2",
           (r.status_code, row.get("status")), (409, "stale"), (200, "accepted"))  # fmt: skip
    check(row.get("status") == "stale", f"K6b: the change is {row.get('status')}; the spec says stale (8.4.2)")
    line3 = next((x for x in co.lines8(po) if int(x["line_no"]) == 3), {})
    check(D(line3.get("qty")) == 150 and int(co.po_row(po).get("version") or 0) == 2, f"K6b: line 3 qty "
          f"{line3.get('qty')}, version {co.po_row(po).get('version')}; expected 150 and 2")  # fmt: skip
    task = co.qd("select * from task where id = :i", i=int(rows[0].get("task_id") or 0))
    check(bool(task) and task[0].get("status") not in ("open", "pending") and task[0].get("completed_at") is not None,
          f"K6b: the change's task {[(x.get('status'), x.get('completed_at')) for x in task]}; completed when nothing "
          "of it is left open (8.4.2)", "medium")  # fmt: skip
    check(bool(co.since("po.change_stale", mark)), "K6b: no po.change_stale audit row", "medium")
    return po, [l1]
