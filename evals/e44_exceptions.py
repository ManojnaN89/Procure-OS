"""E44: the exceptions screen lists every stuck case once, with the right reason and owner, oldest first, by role.

The main demo company's invoices are replayed through the platform's own match (as in E19 and E25), so the blocked ones
are real. On top of them the eval plants one of each other kind: goods received without an order (one waiting for the
purchase manager, one approved and waiting for the buyer's order, one rejected and waiting to go back), a service past its
date that its requester has not confirmed, a release asked for on a blocked invoice, an approval reminded and still
waiting, and a decision (proposal) waiting. An oracle written here reads the raw tables with its own queries and says
which rows must appear, who owns each, since when and how many working days old it is (its own count, from the holiday
table). The administrator, who sees everything, must get exactly that set and nothing else; each business role must see
only the kinds its permissions allow, and "mine" only what that role must act on.
"""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import invoice_replay
import lib
from fastapi.testclient import TestClient
from lib import D, Outcome
from replay import insert
from sqlalchemy import text

ID = "E44"
TITLE = "The exceptions screen lists every stuck case with the right reason, owner and age, by role"
REQUIREMENT = (
    "Plan step 11/12, P1-38: one list of everything stuck that needs a person (blocked invoices, goods without an order, "
    "services not confirmed, approvals past their reminder, proposals, suppliers to chase) with what, reason, owner, next "
    "step, age in working days and amount, oldest first, filtered by kind and mine, by permission"
)
IST = ZoneInfo("Asia/Kolkata")
RECEIVABLE = ("sent", "acknowledged", "part_received")
SEE = {  # the kind: permissions that let a role see it (the screen's specification, not the code's)
    "invoice": {"invoice.view", "invoice.match", "invoice.approve"},
    "invoice_authorise": {"invoice.view", "invoice.match", "invoice.approve"},
    "invoice_approve": {"invoice.view", "invoice.match", "invoice.approve"},
    "invoice_sender_check": {"invoice.view", "invoice.book", "invoice.match"},
    "unordered": {"grn.create", "grn.unordered_approve", "po.create"},
    "service": {"po.create"},
    "approval": {"request.approve"},
}
SUPPLIER_ACT = {"rfq": "rfq.create", "ack": "po.send", "late": "po.create"}


def _day(ts: Any) -> date:
    return ts.astimezone(IST).date() if isinstance(ts, datetime) else ts


def _working_days(start: date, end: date, closed: set[date]) -> int:
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        n += d.weekday() < 5 and d not in closed
    return n


def _add_working(start: date, days: int, closed: set[date]) -> date:
    d = start
    while days:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in closed:
            days -= 1
    return d


def oracle(db: Any, today: date) -> dict[tuple[str, str], dict[str, Any]]:
    """Every row the screen must show: (kind, ref) -> the owner it must name, the permissions that act, and since when."""
    q = lambda sql, **kw: [dict(r) for r in db.execute(text(sql), kw).mappings()]  # noqa: E731
    name = {r["code"]: r["name"] for r in q("select code, name from org_user")}
    want: dict[tuple[str, str], dict[str, Any]] = {}

    for i in q("select * from invoice where type = 'invoice' and status = 'blocked' and payment_block <> 'duplicate'"):
        release = q(
            "select 1 from proposal where action = 'invoice.release_block' and status = 'pending' and payload->>'invoice' = :n",
            n=i["no"],
        )
        codes = [
            r["reason_codes"]
            for r in q(
                "select reason_codes from match_result where invoice_no = :n and result = 'blocked' and resolved_at is null "
                "order by invoice_line_no nulls first",
                n=i["no"],
            )
        ]
        if release:
            owner, act = "Finance or an approver", "invoice.approve"
        elif codes and codes[0] == "service_not_confirmed":
            owner, act = "Requester", None
        else:
            owner, act = "AP", "invoice.match"
        want[("invoice", i["no"])] = {
            "owner": owner,
            "act": act,
            "since": i["received_on"],
            "reason": i["block_reason"],
        }

    # block 10 (spec 10.1): the bills without an order waiting past their 18 working hours, and an invoice mail whose
    # sender failed authentication (that one at once)
    profile = {r["user_id"]: r["display_name"] for r in q("select user_id, display_name from user_profile")}
    now = datetime.now(IST)
    for t in q(
        "select id, kind, assignee, payload, due_at, created_at from task "
        "where kind in ('invoice_authorise', 'invoice_approve', 'invoice_sender_check') "
        "and status in ('open', 'overdue')"
    ):
        p = t["payload"] or {}
        if t["kind"] == "invoice_sender_check":
            ref = str(p.get("email_id") or t["id"])
            want[("invoice_sender_check", ref)] = {
                "owner": f"AP: {profile.get(int(t['assignee']), 'a person')}",
                "act": None,
                "since": _day(t["created_at"]),
            }
            continue
        if t["due_at"] > now:
            continue
        person = p.get("person")
        want[(t["kind"], str(p.get("invoice_no")))] = {
            "owner": f"Named person: {name.get(str(person), person) if person else 'not assigned'}",
            "act": None,
            "since": _day(t["due_at"]),
        }

    for u in q(
        "select u.*, p.status as proposal, o.status as po_status from unordered_receipt u "
        "left join proposal p on p.id = u.proposal_id left join po o on o.no = u.po_no"
    ):
        state = "rejected" if u["status"] == "pending" and u["proposal"] in ("rejected", "expired") else u["status"]
        if state == "pending":
            owner, act = "Purchase manager", "grn.unordered_approve"
        elif state == "approved":
            owner, act = "Buyer", "po.create"
        elif state == "rejected" or (state == "ordered" and u["po_status"] in RECEIVABLE):
            owner, act = "Store", "grn.create"
        elif state == "ordered":
            owner, act = "Buyer", "po.send"
        else:
            continue  # received or returned: done
        want[("unordered", u["no"])] = {"owner": owner, "act": act, "since": u["received_on"], "reason": u["reason"]}

    for ln in q(
        "select l.po_no, l.need_by, r.requester from po_line l join po p on p.no = l.po_no "
        "left join item i on i.code = l.item_code left join item_category c on c.code = i.category_code "
        "left join request r on r.no = l.request_no where p.status in ('sent', 'acknowledged', 'part_received') "
        "and l.status in ('open', 'part_received') and coalesce(nullif(i.kind, ''), c.kind) = 'service' "
        "and l.need_by < :d",
        d=today,
    ):
        want[("service", ln["po_no"])] = {
            "owner": f"Requester: {name.get(ln['requester'], ln['requester']) if ln['requester'] else 'the requester'}", "act": None, "since": ln["need_by"],
        }  # fmt: skip

    for s in q(
        "select s.request_no, s.approver, s.delegate, s.opened_at from approval_step s join request r on r.no = s.request_no "
        "where s.status = 'pending' and s.round = r.approval_round and r.status = 'submitted' and s.reminded_at is not null"
    ):
        who = s["delegate"] or s["approver"]
        want[("approval", s["request_no"])] = {
            "owner": f"Approver: {name.get(who, who)}", "act": None, "since": _day(s["opened_at"]),
        }  # fmt: skip

    for p in q(
        "select * from proposal where status = 'pending' and action not in ('invoice.release_block', 'receipt.unordered')"
    ):
        roles = [r["role"] for r in q("select role from role_permission where action = :a", a=p["required_permission"])]
        want[("proposal", f"Proposal {p['id']}")] = {
            "owner": " or ".join(sorted(roles)), "act": p["required_permission"], "since": _day(p["created_at"]),
        }  # fmt: skip

    closed = {
        r["holiday_date"] for r in q("select holiday_date from ref_holiday where kind = 'closed' and scope = 'central'")
    }
    for f in q("select no, due_date from rfq where status = 'sent' and due_date < :d and not exists (select 1 from quote x where x.rfq_no = rfq.no)", d=today):  # fmt: skip
        want[("supplier", f["no"])] = {"owner": "Buyer", "act": SUPPLIER_ACT["rfq"], "since": f["due_date"]}
    ack_days = int(
        (q("select value from org_setting where key = 'supplier_ack_due_days'") or [{"value": "2"}])[0]["value"] or 2
    )
    for p in q("select no, sent_at from po where status = 'sent' and sent_at is not null"):
        if today > _add_working(_day(p["sent_at"]), ack_days, closed):
            want[("supplier", p["no"])] = {"owner": "Buyer", "act": SUPPLIER_ACT["ack"], "since": _day(p["sent_at"])}
    # block 8 spec 8.6.2: a line is due on its promised date, else its need-by date; a line with only a need-by counts
    late = q(
        "select l.po_no, min(coalesce(l.promised_date, l.need_by)) as oldest from po_line l join po on po.no = l.po_no "
        "where coalesce(l.promised_date, l.need_by) < :d "
        "and l.status in ('open', 'part_received') and l.received_qty < l.qty and po.status in ('sent', 'acknowledged', 'part_received') "
        "group by l.po_no",
        d=today,
    )
    for p in late:
        want.setdefault(("supplier", p["po_no"]), {"owner": "Buyer", "act": SUPPLIER_ACT["late"], "since": p["oldest"]})
        want[("supplier-late", p["po_no"])] = {"owner": "Buyer", "act": SUPPLIER_ACT["late"], "since": p["oldest"]}
    for v in want.values():
        v["age"] = _working_days(v["since"], today, closed)
    return want


def _plant(world: Any, client: TestClient, out: Outcome) -> dict[str, str]:
    """One stuck case of each kind the replay does not give. Returns what was planted, by name."""
    from p2p.invoices import service as invoices
    from p2p.platform import proposals
    from p2p.platform.sod import user

    db = world.db
    approvers = sorted(world.org_logins)
    pm = approvers[0]
    planted: dict[str, str] = {}
    store = world.headers("Store")
    item = db.execute(
        text("select code, category_code from item where kind = 'goods' and active order by code limit 1")
    ).one()
    supplier = db.execute(text("select code from supplier where status = 'active' order by code limit 1")).scalar_one()
    loc = lib.location()
    body = {
        "supplier_code": supplier,
        "item_code": item[0],
        "qty": "2",
        "location_code": loc,
        "reason": "E44: came without an order",
    }
    for label in ("unordered pending", "unordered approved", "unordered rejected"):
        r = client.post("/api/unordered-receipts", headers=store, json={**body, "reason": f"E44 {label}"})
        if r.status_code != 200:
            out.find("high", f"could not plant {label}: {r.text[:160]}")
            continue
        planted[label] = r.json()["receipt"]
    if "unordered approved" in planted:
        client.post(
            f"/api/unordered-receipts/{planted['unordered approved']}/approve", headers=world.org_headers(pm), json={}
        )
    if "unordered rejected" in planted:
        client.post(
            f"/api/unordered-receipts/{planted['unordered rejected']}/reject",
            headers=world.org_headers(pm),
            json={"reason": "Not ours"},
        )

    # a service, asked for by an approver who has a login, due five days ago and not confirmed
    template_po = dict(db.execute(text("select * from po order by no limit 1")).mappings().one())
    template_line = dict(
        db.execute(text("select * from po_line where po_no = :p limit 1"), {"p": template_po["no"]}).mappings().one()
    )
    requester = approvers[-1]
    db.execute(
        text("insert into item (code, name, category_code, kind, base_uom, active, stocked) values ('E44-SVC', 'Test rig calibration', :c, 'service', 'NOS', true, false)"),
        {"c": item[1]},
    )  # fmt: skip
    db.execute(
        text("insert into request (no, requester, ship_to_location, type, channel, status, total_value) values ('E44-REQ', :r, :l, 'service', 'form', 'closed', 5000)"),
        {"r": requester, "l": template_po["ship_to_location"]},
    )  # fmt: skip
    insert(db, "po", {k: v for k, v in template_po.items() if v is not None}, {"no": "E44-PO", "status": "sent", "sent_at": datetime.now(IST), "acknowledged_at": None})  # fmt: skip
    insert(
        db, "po_line", {k: v for k, v in template_line.items() if v is not None},
        {"po_no": "E44-PO", "line_no": 1, "request_no": "E44-REQ", "request_line_no": 1, "item_code": "E44-SVC", "qty": 1,
         "unit_price": 5000, "received_qty": 0, "invoiced_qty": 0, "status": "open", "need_by": lib.TODAY - timedelta(days=5),
         "promised_date": None, "taxable": D("5000.00")},
    )  # fmt: skip
    planted["service"] = "E44-PO"

    # an approval reminded and still waiting
    db.execute(
        text(
            "insert into request (no, requester, ship_to_location, type, channel, status, total_value, approval_round) "
            "values ('E44-WAIT', :r, :l, 'goods', 'form', 'submitted', 12000, 1)"
        ),
        {"r": approvers[-1], "l": template_po["ship_to_location"]},
    )
    db.execute(
        text(
            "insert into approval_step (request_no, round, step, role, approver, status, opened_at, reminded_at) "
            "values ('E44-WAIT', 1, 1, 'manager', :a, 'pending', :o, :o)"
        ),
        {"a": pm, "o": datetime.combine(lib.TODAY - timedelta(days=3), datetime.min.time(), IST)},
    )
    planted["approval"] = "E44-WAIT"
    db.commit()

    # a decision waiting, and a release asked for on one blocked invoice
    pid = proposals.propose(
        db, user(world.logins["Buyer"]), action="po.accept_variance", summary="E44: accept a price above the contract",
        payload={}, required_permission="po.approve", value=D("2500"),
    )  # fmt: skip
    db.commit()
    planted["proposal"] = f"Proposal {pid}"
    blocked = db.execute(
        text(
            "select no from invoice where status = 'blocked' and payment_block <> 'duplicate' and type = 'invoice' order by no limit 1"
        )
    ).scalar()
    if blocked:
        invoices.request_release(db, blocked, "E44: agreed with the supplier", world.logins["AP"])
        db.commit()
        planted["release"] = blocked
    # block 10 (spec 10.1): a bill without an order with each of its two decisions overdue, and an invoice mail whose
    # sender failed authentication
    db.execute(
        text(
            "insert into invoice (no, type, kind, supplier_code, supplier_invoice_no, dedupe_key, invoice_date, fy, "
            "received_on, taxable, cgst, sgst, total, status, payment_block, category_code, cost_centre_code, "
            "authoriser, entered_by) values ('E44-BILL', 'invoice', 'non_po', 'SUP-010', 'E44/BILL', 'E44BILL', :d, "
            "'2026-27', :d, 1000, 90, 90, 1180, 'awaiting_approval', 'none', 'UTL', 'CC-ADM', 'U07', 'U04')"
        ),
        {"d": lib.TODAY},
    )  # fmt: skip
    for kind, title in (
        ("invoice_authorise", "Authorise Sahyadri Netlink Pvt Ltd's bill E44/BILL for Rs 1,180.00"),
        ("invoice_approve", "Approve Sahyadri Netlink Pvt Ltd's bill E44/BILL for Rs 1,180.00"),
    ):
        db.execute(
            text(
                "insert into task (kind, title, assignee, sla_hours, due_at, payload, created_at) values "
                "(:k, :t, :a, 18, :due, cast(:p as jsonb), :created)"
            ),
            {
                "k": kind,
                "t": title,
                "a": world.org_logins["U07"],
                "due": datetime.now(IST) - timedelta(days=2),
                "p": f'{{"invoice_no": "E44-BILL", "person": "U07", "calendar_location": "PLT-CKN"}}',
                "created": datetime.now(IST) - timedelta(days=3),
            },
        )
    db.execute(
        text(
            "insert into task (kind, title, assignee, sla_hours, due_at, payload, created_at) values "
            "('invoice_sender_check', 'Invoice mail from billing@sahyadrinetlink.example not read', :a, 18, :due, "
            "cast(:p as jsonb), :created)"
        ),
        {
            "a": world.logins["AP"],
            "due": datetime.now(IST) + timedelta(days=1),
            "p": '{"email_id": 999999, "from": "billing@sahyadrinetlink.example"}',
            "created": datetime.now(IST),
        },
    )  # fmt: skip
    db.commit()
    planted["bill_authorise"] = "E44-BILL"
    planted["bill_approve"] = "E44-BILL"
    planted["sender_check"] = "999999"
    return planted


def run(ctx: lib.Ctx) -> Outcome:
    from p2p.main import app

    out = Outcome(True)
    world = lib.build_world("e44", with_stock=False)
    db = world.db
    client = TestClient(app)
    got_replay = invoice_replay.replay(world, limit_invoices=60 if ctx.quick else None)
    out.metrics["invoices replayed"] = len(got_replay["entered"])
    planted = _plant(world, client, out)

    admin = client.get("/api/exceptions", headers=world.headers("Admin"))
    if admin.status_code != 200:
        out.find("high", f"the administrator could not open the exceptions: {admin.status_code} {admin.text[:160]}")
        return out
    data = admin.json()
    today = date.fromisoformat(data["as_of"])
    db.expire_all()
    want = oracle(db, today)
    late_only = {k[1] for k in want if k[0] == "supplier-late"}
    want = {k: v for k, v in want.items() if k[0] != "supplier-late"}
    rows = data["rows"]
    got: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        got[(r["kind"], r["ref"])].append(r)
    out.metrics["rows the oracle expects"] = len(want)
    out.metrics["rows by kind"] = dict(sorted(Counter(k[0] for k in want).items()))

    # 1. every planted case is there, with the owner the oracle names
    for label, ref in planted.items():
        kind = {
            "release": "invoice",
            "service": "service",
            "approval": "approval",
            "proposal": "proposal",
            "bill_authorise": "invoice_authorise",
            "bill_approve": "invoice_approve",
            "sender_check": "invoice_sender_check",
        }.get(label, "unordered")
        if (kind, ref) not in got:
            out.find("high", f"the planted {label} ({ref}) is not on the screen")
    # 2. exactly the oracle's set, nothing extra, nothing missing (a late order may also be one not acknowledged)
    extra = [k for k in got if k not in want]
    missing = [k for k in want if k not in got]
    if extra:
        out.find("high", f"{len(extra)} row(s) the oracle does not expect, e.g. {extra[:3]}")
    if missing:
        out.find("high", f"{len(missing)} expected row(s) missing, e.g. {missing[:3]}")
    dupes = [k for k, v in got.items() if len(v) > 1 and not (k[0] == "supplier" and k[1] in late_only)]
    if dupes:
        out.find("high", f"{len(dupes)} case(s) listed twice, e.g. {dupes[:3]}")
    wrong_owner, wrong_age, wrong_reason = [], [], []
    for k, v in want.items():
        for r in got.get(k, []):
            if not r["owner"].startswith(v["owner"]):
                wrong_owner.append(f"{k}: {r['owner']!r}, the oracle says {v['owner']!r}")
            if (r["since"], r["age_days"]) != (v["since"].isoformat(), v["age"]):
                wrong_age.append(
                    f"{k}: since {r['since']} age {r['age_days']}, the oracle says {v['since']} age {v['age']}"
                )
            if v.get("reason") and v["reason"] not in r["reason"]:
                wrong_reason.append(f"{k}: {r['reason'][:80]!r}")
            if not r["next_step"]:
                wrong_reason.append(f"{k}: no next step")
    for name, bad in (("owner", wrong_owner), ("since or age", wrong_age), ("reason or next step", wrong_reason)):
        if bad:
            out.find("high", f"{len(bad)} row(s) with the wrong {name}; first: {bad[0]}")
    ages = [r["age_days"] for r in rows]
    if ages != sorted(ages, reverse=True):
        out.find("high", "the rows are not oldest first")
    out.metrics["rows checked (owner, reason, age)"] = sum(len(got.get(k, [])) for k in want)

    # 3. each role sees only what its permissions allow, and "mine" only what it must act on
    perms = defaultdict(set)
    for role, action in db.execute(text("select role, action from role_permission")):
        perms[role].add(action)
    for role in ("Store", "AP", "Buyer", "Finance", "Requester"):
        mine_want = set()
        see_want = set()
        for k, v in want.items():
            kind = k[0]
            visible = bool(perms[role] & SEE[kind]) if kind in SEE else v["act"] in perms[role]
            if visible:
                see_want.add(k)
                if v["act"] in perms[role] and not (role == "AP" and v["act"] == "invoice.approve"):
                    mine_want.add(k)
        if role == "Buyer":  # the buyer proposed the planted decision: never theirs to approve
            mine_want.discard(("proposal", planted.get("proposal", "")))
        seen = {
            (r["kind"], r["ref"]) for r in client.get("/api/exceptions", headers=world.headers(role)).json()["rows"]
        }
        mine = {
            (r["kind"], r["ref"])
            for r in client.get("/api/exceptions?mine=true", headers=world.headers(role)).json()["rows"]
        }
        if seen != see_want:
            out.find(
                "high",
                f"{role} sees {len(seen)} row(s), the oracle allows {len(see_want)}: e.g. {sorted(seen ^ see_want)[:3]}",
            )
        if mine != mine_want:
            out.find(
                "high",
                f"{role}'s own rows are {len(mine)}, the oracle says {len(mine_want)}: e.g. {sorted(mine ^ mine_want)[:3]}",
            )
        out.metrics[f"{role}: rows seen / mine"] = f"{len(seen)} / {len(mine)}"

    # 4. the people named: the service's requester and the waiting approver have it as their own
    for kind, code in (("service", sorted(world.org_logins)[-1]), ("approval", sorted(world.org_logins)[0])):
        rows_ = client.get(f"/api/exceptions?kind={kind}&mine=true", headers=world.org_headers(code)).json()["rows"]
        if planted.get(kind) not in {r["ref"] for r in rows_}:
            out.find("high", f"the {kind} planted for {code} is not among their own rows")
    return out
