"""E47: cancelling a production order gives its stock back, and it is handed on at once to the next orders, never so
that an order is both stocked and bought for (plan step 2, P1-30).

The main demo company's two production orders plus a third of MO-1's product, planted after MO-2, come in from the ERP and
the store checks them. Then MO-1 (needed first) is called off. An oracle written here from the raw files (stock replayed
from the opening and the movement history, materials from `bom.csv` times the order quantity) says what each line held
before, what the cancel frees, and what each later order holds after it, earliest need-by first, up to what it still
lacks. Its shortfall was already on purchase requests not yet with the buyer, so the oracle also says by how much each
request line must be cut: what the stock now covers. Set aside plus still requested never exceeds a line's need, and the
check after the cancel buys nothing. The planted order's quantity puts one item exactly on the edge (what is freed equals
what it lacks) and the bearings fall short of it (freed less than lacking).
"""

from collections import defaultdict
from decimal import Decimal

import lib
from e37_material_requests import on_hand_at_cutoff
from e38_combined_budget import upload
from fastapi.testclient import TestClient
from lib import D, Outcome, rows
from sqlalchemy import text

ID = "E47"
TITLE = "Cancelling a production order releases its stock to the next order at once, never stocked and bought twice"
REQUIREMENT = (
    "Plan P1-30 and step 2 of the flow: cancelling an order releases its reservation (a reason is needed; only the "
    "store; once); the freed stock is set aside at once for the open orders earliest need-by first, each up to what it "
    "still lacks, never more than is on the shelf; a purchase request still open for that shortfall is cut by as much "
    "(set aside plus requested never exceeds the need) and the next check buys nothing; purchase requests made only for "
    "the cancelled order stay and are named; issue never moves more than is set aside; the cancel is audited"
)
PLANTED = ("MO-3", "FG-01", "60", "2026-12-28")  # 60 pumps: impellers left after MO-1 (10) plus MO-1's 50 = exactly 60


def orders() -> list[dict[str, str]]:
    planted = dict(zip(("order_no", "product_code", "qty", "need_by"), PLANTED, strict=True))
    return [{k: o[k] for k in planted} for o in rows("inputs", "production_order")] + [planted]


def oracle(
    all_orders: list[dict[str, str]], cancelled: str
) -> tuple[dict[str, dict[str, Decimal]], dict[str, dict[str, Decimal]], dict[str, dict[str, Decimal]]]:
    """Per order and item: what it needs, what the first check sets aside, and what it holds after the cancel and a
    second check."""
    bom: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows("inputs", "bom"):
        bom[r["product_code"]].append(r)
    ordered = sorted(all_orders, key=lambda o: o["need_by"])
    need = {o["order_no"]: {p["item_code"]: D(p["qty"]) * D(o["qty"]) for p in bom[o["product_code"]]} for o in ordered}
    free = on_hand_at_cutoff()
    first: dict[str, dict[str, Decimal]] = {}
    for o in ordered:
        first[o["order_no"]] = {}
        for item, want in need[o["order_no"]].items():
            take = min(want, max(free.get(item, D(0)), D(0)))
            free[item] = free.get(item, D(0)) - take
            first[o["order_no"]][item] = take
    for item, held in first[cancelled].items():
        free[item] = free.get(item, D(0)) + held
    second: dict[str, dict[str, Decimal]] = {}
    for o in ordered:
        if o["order_no"] == cancelled:
            continue
        second[o["order_no"]] = {}
        for item, want in need[o["order_no"]].items():
            take = min(want - first[o["order_no"]][item], max(free.get(item, D(0)), D(0)))
            free[item] = free.get(item, D(0)) - take
            second[o["order_no"]][item] = first[o["order_no"]][item] + take
    return need, first, second


def run(ctx) -> Outcome:  # noqa: C901 - one scenario, step by step
    from p2p.main import app

    out = Outcome(True)
    if not (lib.DATASET / "inputs" / "production_order.csv").is_file():
        out.notes.append(f"{lib.DATASET.name} has no production orders: nothing to check")
        return out
    world = lib.build_world("e47")
    db = world.db
    client = TestClient(app)
    store = world.headers("Store")
    plan = orders()
    first_order = min(plan, key=lambda o: o["need_by"])["order_no"]
    need, want_first, want_second = oracle(plan, first_order)

    bom: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in rows("inputs", "bom"):
        bom[r["product_code"]].append(r)
    lines = [
        {"order_no": o["order_no"], "item_code": p["item_code"], "qty": str(D(p["qty"]) * D(o["qty"]))}
        for o in plan
        for p in bom[o["product_code"]]
    ]
    for kind, data in (("production_order", plan), ("production_order_line", lines)):
        got = upload(client, kind, data, store)
        if got["status"] != 200 or got.get("rejected"):
            out.find("high", f"importing {kind} answered {got}")
            return out
    by_order = dict(db.execute(text("select order_no, no from material_request")).all())
    if sorted(by_order) != sorted(need):
        out.find("high", f"material requests {by_order} for orders {sorted(need)}")
        return out

    def held() -> dict[str, dict[str, Decimal]]:
        db.rollback()
        got: dict[str, dict[str, Decimal]] = defaultdict(dict)
        for order, item, qty in db.execute(
            text(
                "select m.order_no, l.item_code, l.reserved_qty from material_request_line l "
                "join material_request m on m.no = l.mr_no"
            )
        ):
            got[order][item] = D(qty)
        return dict(got)

    def compare(label: str, want: dict[str, dict[str, Decimal]]) -> int:
        got = held()
        wrong = [
            f"{o} {i}: {got.get(o, {}).get(i)} set aside, expected {q}"
            for o in want
            for i, q in want[o].items()
            if got.get(o, {}).get(i) != q
        ]
        if wrong:
            out.find("high", f"{label}: {len(wrong)} line(s) differ from the oracle; first: {wrong[0]}")
        return len(wrong)

    def cover_wrong(want: dict[str, dict[str, Decimal]]) -> int:
        """Still requested per line (what live purchase requests buy for it) against the oracle, and never set aside
        plus requested above the need."""
        db.rollback()
        got: dict[str, dict[str, tuple[Decimal, Decimal, Decimal]]] = defaultdict(dict)
        for order, item, need_qty, reserved, requested in db.execute(
            text(
                "select m.order_no, c.item_code, c.need_qty, c.reserved_qty, c.requested_qty "
                "from material_request_cover c join material_request m on m.no = c.mr_no where c.status = 'open'"
            )
        ):
            got[order][item] = (D(need_qty), D(reserved), D(requested))
        over = [f"{o} {i}" for o, items in got.items() for i, (n, r, q) in items.items() if r + q > n]
        if over:
            out.find("high", f"set aside plus requested is above the need for {over[:4]}: bought twice")
        wrong = [
            f"{o} {i}: {got.get(o, {}).get(i, (0, 0, 0))[2]} requested, expected {q}"
            for o in want
            for i, q in want[o].items()
            if got.get(o, {}).get(i, (0, 0, 0))[2] != q
        ]
        if wrong:
            out.find("high", f"after the cancel {len(wrong)} line(s) are still requested differently; first: {wrong[0]}")
        return len(wrong) + len(over)

    def over_shelf(label: str) -> None:
        db.rollback()
        over = db.execute(
            text(
                "select l.item_code from material_request_line l join stock_level s on s.item_code = l.item_code "
                "and s.location_code = l.location_code where l.status = 'open' "
                "group by l.item_code, s.on_hand having sum(l.reserved_qty) > s.on_hand"
            )
        ).all()
        if over:
            out.find("high", f"{label}: more set aside than is on hand for {[r[0] for r in over]}")

    # 0. the shelf the oracle starts from is the shelf the platform has
    shelf = dict(db.execute(text("select item_code, on_hand from stock_level")).all())
    replayed = on_hand_at_cutoff()
    differ = [i for i in {i for o in need.values() for i in o} if D(shelf.get(i, 0)) != replayed.get(i, D(0))]
    if differ:
        out.find("high", f"the stock loaded differs from the replayed history for {differ}")

    # 1. the first check
    resp = client.post("/api/material-requests/check", json={}, headers=store)
    if resp.status_code != 200:
        out.find("high", f"the store check answered {resp.status_code}: {resp.text[:200]}")
        return out
    made = {c["order_no"]: (c["purchase_request"] or {}).get("no") for c in resp.json()["checked"]}
    wrong_first = compare("first check", want_first)
    over_shelf("first check")

    # 2. refusals: no reason, the wrong person
    mr = by_order[first_order]
    no_reason = client.post(f"/api/material-requests/{mr}/cancel", json={"reason": "  "}, headers=store).status_code
    if no_reason not in (409, 422):
        out.find("high", f"cancelling without a reason answered {no_reason}")
    by_requester = client.post(
        f"/api/material-requests/{mr}/cancel", json={"reason": "eval"}, headers=world.headers("Requester")
    ).status_code
    if by_requester != 403:
        out.find("high", f"a requester cancelling a material request answered {by_requester}")
    if held().get(first_order) != want_first[first_order]:
        out.find("high", "a refused cancel changed what is set aside")

    # 3. the cancel: everything it held goes back; its purchase request stays and is named
    resp = client.post(
        f"/api/material-requests/{mr}/cancel", json={"reason": "The customer called the order off"}, headers=store
    )
    if resp.status_code != 200:
        out.find("high", f"cancelling {mr} answered {resp.status_code}: {resp.text[:200]}")
        return out
    left = resp.json()["purchase_requests_left"]
    if made.get(first_order) and left != [made[first_order]]:
        out.find("high", f"the cancel named purchase requests {left}, expected [{made[first_order]}]")
    if made.get(first_order):
        status = db.execute(text("select status from request where no = :n"), {"n": made[first_order]}).scalar()
        if status == "cancelled":
            out.find("high", f"the cancel cancelled {made[first_order]}; it should stay for the buyer")
    still = {i: q for i, q in held().get(first_order, {}).items() if q}
    if still:
        out.find("high", f"{first_order} still holds {still} after its cancel")
    again = client.post(f"/api/material-requests/{mr}/cancel", json={"reason": "again"}, headers=store).status_code
    if again != 409:
        out.find("high", f"cancelling {mr} a second time answered {again}")
    audit = db.execute(
        text("select after from audit_event where action = 'material_request.cancel' and entity_id = :n order by id"),
        {"n": mr},
    ).all()
    released = {i: str(q) for i, q in want_first[first_order].items() if q}
    if len(audit) != 1 or {k: D(v) for k, v in (audit[0][0] or {}).get("released", {}).items()} != {
        k: D(v) for k, v in released.items()
    }:
        out.find("high", f"the cancel is audited as {[a[0] for a in audit]}, expected one row releasing {released}")

    # 4. the cancel hands the freed stock on at once, earliest need-by first, and cuts the requests it covers
    wrong_second = compare("after the cancel", want_second)
    over_shelf("after the cancel")
    want_coming = {
        o: {i: need[o][i] - q for i, q in items.items()} for o, items in want_second.items()
    }  # every shortfall was requested by the first check, so what stock does not cover stays requested
    cut_wrong = cover_wrong(want_coming)
    requests_before = db.execute(text("select count(*) from request")).scalar_one()
    resp = client.post("/api/material-requests/check", json={}, headers=store)
    if resp.status_code != 200:
        out.find("high", f"the store check after the cancel answered {resp.status_code}: {resp.text[:200]}")
        return out
    db.rollback()
    if compare("the check after the cancel", want_second) or cover_wrong(want_coming):
        out.find("high", "the store check after the cancel moved stock or cover again")
    bought = db.execute(text("select count(*) from request")).scalar_one() - requests_before
    if bought:
        out.find("high", f"the store check after the cancel made {bought} new purchase request(s): buying twice")

    # the edges the scenario was built for
    edges: dict[str, list[str]] = {"exact": [], "less": [], "more": []}
    for item, freed in want_first[first_order].items():
        if not freed:
            continue
        nxt = next(
            (o["order_no"] for o in sorted(plan, key=lambda o: o["need_by"])
             if o["order_no"] != first_order and item in need[o["order_no"]]
             and need[o["order_no"]][item] > want_first[o["order_no"]][item]),
            None,
        )  # fmt: skip
        if nxt is None:
            continue
        lacks = need[nxt][item] - want_first[nxt][item]
        edges["exact" if freed == lacks else "less" if freed < lacks else "more"].append(f"{item}->{nxt}")
    for kind in ("exact", "less"):
        if not edges[kind]:
            out.find("medium", f"no item hit the '{kind}' edge (freed {kind} than what the next order lacks)")

    # 5. issue never moves more than is set aside, and moves exactly that
    nxt_order = min((o for o in plan if o["order_no"] != first_order), key=lambda o: o["need_by"])["order_no"]
    nxt_mr = by_order[nxt_order]
    line_no, reserved = db.execute(
        text(
            "select line_no, reserved_qty from material_request_line where mr_no = :n and reserved_qty > 0 "
            "order by line_no limit 1"
        ),
        {"n": nxt_mr},
    ).one()
    before = dict(db.execute(text("select item_code, on_hand from stock_level")).all())
    too_much = client.post(
        f"/api/material-requests/{nxt_mr}/issue", json={"lines": {str(line_no): str(reserved + 1)}}, headers=store
    ).status_code
    db.rollback()
    if too_much == 200 or dict(db.execute(text("select item_code, on_hand from stock_level")).all()) != before:
        out.find("high", f"issuing more than is set aside on {nxt_mr} line {line_no} answered {too_much}")
    resp = client.post(f"/api/material-requests/{nxt_mr}/issue", json={}, headers=store)
    db.rollback()
    if resp.status_code != 200:
        out.find("high", f"issuing {nxt_mr} answered {resp.status_code}: {resp.text[:200]}")
    else:
        after = dict(db.execute(text("select item_code, on_hand from stock_level")).all())
        moved = {i: D(before[i]) - D(after[i]) for i in before if before[i] != after[i]}
        want_moved = {i: q for i, q in want_second[nxt_order].items() if q}
        if moved != want_moved:
            out.find("high", f"issuing {nxt_order} moved {moved}, expected {want_moved}")

    out.metrics = {
        "production orders (one planted)": len(plan),
        "lines checked against the oracle, before and after": f"{sum(len(v) for v in want_first.values())} / "
        f"{sum(len(v) for v in want_second.values())}",
        "lines differing (first check / after the cancel)": f"{wrong_first} / {wrong_second}",
        "lines whose request was not cut to what stock leaves short": cut_wrong,
        "freed per item": {i: str(q) for i, q in want_first[first_order].items() if q},
        "edges hit": {k: v for k, v in edges.items() if v},
        "purchase requests left after the cancel": left,
    }
    return out
