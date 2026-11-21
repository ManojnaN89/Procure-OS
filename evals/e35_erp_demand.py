"""E35: the ERP's demand files load row for row, bad rows are listed with their reasons, nothing loads twice.

The main demo company (Apex Pumps) sends production orders, their lines, sales orders and the demand plan. The IT services
company (Brightwave) sends its joiner kits and the joiners planned by department: each joiner becomes the items of their
kit, needed by the start date, for the joiner's department, checked against an oracle from the raw files."""

import csv
import io
from collections import defaultdict
from datetime import date
from decimal import Decimal

import lib
from fastapi.testclient import TestClient
from lib import Outcome
from sqlalchemy import text

ID = "E35"
TITLE = "ERP demand files load exactly once, with every bad row explained"
REQUIREMENT = (
    "Plan P1-29: production orders with their component lines, MRP results, sales orders and the demand plan are "
    "imported from the client's ERP files; every rejected row is listed with its reason; the same ERP order is never "
    "loaded twice; a changed order is applied and listed with what changed (Q21, 20 Nov: the change propagates); one "
    "company never sees another's imports. For an IT "
    "services company the joiner kits and the joiner plan load the same way: each joiner row becomes its kit's items "
    "times the joiners, by the start date, for the joiner's department, and one material request per plan and department"
)
BRIGHTWAVE = lib.ROOT / "data" / "datasets" / "brightwave-v1"
JOINER_PLANTED = {  # line of the planted joiner plan -> (row, a word the reason must contain)
    2: (["JP-2027-01", "ENG", "developer", "2", "2027-01-04"], None),  # clean: must load
    3: (["JP-2027-01", "ENG", "intern", "1", "2027-01-04"], "kit"),
    4: (["JP-2027-01", "XYZ", "standard", "1", "2027-01-04"], "department"),
    5: (["JP-2027-01", "ADM", "standard", "0", "2027-01-04"], "above zero"),
    6: (["JP-2027-01", "ADM", "standard", "1.5", "2027-01-04"], "whole number"),
    7: (["JP-2027-01", "ADM", "standard", "1", ""], "required"),
    8: (["JP-2027-01", "ENG", "developer", "2", "2027-01-04"], "duplicate"),
}

KINDS = (
    "production_order",
    "production_order_line",
    "sales_order",
    "demand_plan",
)  # the files the demo company has
PLANTED = {  # line of the planted lines file -> (row, a word the reason must contain)
    2: (["MO-90", "RM-01", "10", "NOS", "2026-12-20"], None),  # clean: must load
    3: (["MO-90", "ZZ-404", "10", "NOS", "2026-12-20"], "item"),
    4: (["MO-90", "RM-03", "10", "CRATE", "2026-12-20"], "unit"),
    5: (["MO-90", "RM-05", "10", "NOS", "31-12-2026"], "date"),
    6: (["MO-90", "RM-06", "0", "NOS", "2026-12-20"], "above zero"),
    7: (["MO-90", "RM-07", "-4", "NOS", "2026-12-20"], "above zero"),
    8: (["MO-91", "RM-01", "10", "NOS", "2026-12-20"], "header"),  # no such order
    9: (["MO-90", "RM-01", "10", "NOS", "2026-12-20"], "duplicate"),
    10: (["MO-90", "RM-09", "", "NOS", "2026-12-20"], "required"),
}


def _csv(header: list[str], rows: list[list[str]]) -> bytes:
    out = io.StringIO(newline="")
    w = csv.writer(out)
    w.writerow(header)
    w.writerows(rows)
    return out.getvalue().encode("utf-8")


def _file(kind: str) -> bytes:
    return (lib.DATASET / "inputs" / f"{kind}.csv").read_bytes()


def _expected_requirements() -> dict[tuple[str, str], tuple[Decimal, date, str]]:
    """The oracle: each production order's materials worked out from the bill of materials (not from the lines file),
    in the item master's unit."""
    unit = {r["code"]: r["base_uom"] for r in lib.rows("master", "item")}
    bom: dict[str, list[dict[str, str]]] = defaultdict(list)
    for r in lib.rows("inputs", "bom"):
        bom[r["product_code"]].append(r)
    out = {}
    for order in lib.rows("inputs", "production_order"):
        for part in bom[order["product_code"]]:
            out[(order["order_no"], part["item_code"])] = (
                lib.D(part["qty"]) * lib.D(order["qty"]),
                date.fromisoformat(order["need_by"]),
                unit[part["item_code"]],
            )
    return out


def run(ctx) -> Outcome:
    from p2p.main import app
    from p2p.requests import demand

    out = Outcome(True)
    if (BRIGHTWAVE / "inputs" / "joiner_plan.csv").is_file():
        brightwave(out)
    else:
        out.notes.append("no Brightwave dataset: the joiner files were not checked")
    if not (lib.DATASET / "inputs" / "production_order.csv").is_file():
        out.notes.append(f"{lib.DATASET.name} has no production orders: the production files were not checked")
        return out
    a = lib.build_world("e35a", with_stock=False)
    b = lib.build_world("e35b", with_stock=False)
    client = TestClient(app)
    store = a.headers("Store")

    def upload(
        kind: str,
        body: bytes,
        *,
        dry_run: bool = False,
        headers: dict[str, str] = store,
    ) -> dict:
        resp = client.post(
            f"/api/demand/import/{kind}" + ("?dry_run=true" if dry_run else ""),
            files={"file": (f"{kind}.csv", body, "text/csv")},
            headers=headers,
        )
        if resp.status_code != 200:
            out.find(
                "high",
                f"uploading {kind} answered {resp.status_code}: {resp.text[:200]}",
            )
            return {"loaded": 0, "already_loaded": 0, "rejected": [], "import_id": None}
        return resp.json()

    rows_in_files = {k: len(lib.rows("inputs", k)) for k in KINDS}

    # 1. a dry run checks everything and writes nothing
    for kind in ("production_order", "sales_order", "demand_plan"):
        checked = upload(kind, _file(kind), dry_run=True)
        if checked["loaded"] != rows_in_files[kind] or checked["import_id"] is not None:
            out.find(
                "high",
                f"the dry run of {kind} said {checked['loaded']} rows, or wrote an import",
            )
    written = a.db.execute(
        text(
            "select (select count(*) from production_order) + (select count(*) from demand_import)"
        )
    ).scalar_one()
    if written:
        out.find("high", f"a dry run wrote {written} rows")

    # 2. the real load: every row of every file
    first = {kind: upload(kind, _file(kind)) for kind in KINDS}
    for kind in KINDS:
        if first[kind]["rejected"] or first[kind]["loaded"] != rows_in_files[kind]:
            out.find(
                "high",
                f"{kind}: {first[kind]['loaded']} of {rows_in_files[kind]} rows loaded; left out {first[kind]['rejected'][:3]}",
            )

    # 3. what was stored against the bill of materials
    expected = _expected_requirements()
    stored = {
        (r.source_doc_no, r.item_code): (r.base_qty, r.need_by, r.unit)
        for r in a.db.execute(
            text(
                "select source_doc_no, item_code, base_qty, need_by, unit from material_requirement"
            )
        )
    }
    wrong = [k for k in expected if stored.get(k) != expected[k]]
    extra = sorted(set(stored) - set(expected))
    if wrong or extra:
        out.find(
            "high",
            f"requirements differ from the bill of materials: {wrong[:4]} missing or wrong, {extra[:4]} extra",
        )
    orders = {r["order_no"]: r for r in lib.rows("inputs", "production_order")}
    loaded_orders = {
        r.order_no: (r.qty, r.need_by.isoformat())
        for r in a.db.execute(
            text("select order_no, qty, need_by from production_order")
        )
    }
    if loaded_orders != {n: (lib.D(r["qty"]), r["need_by"]) for n, r in orders.items()}:
        out.find("high", f"production orders stored as {loaded_orders}")

    # 4. the order the next step works in: earliest need-by first, nothing lost
    wanted = demand.open_requirements(a.db)
    if [r.need_by for r in wanted] != sorted(r.need_by for r in wanted):
        out.find("high", "open requirements are not earliest need-by first")
    by_item: dict[str, Decimal] = defaultdict(Decimal)
    for (_, item), (qty, _, _) in expected.items():
        by_item[item] += qty
    got: dict[str, Decimal] = defaultdict(Decimal)
    for r in wanted:
        got[r.item_code] += r.base_qty
    if dict(got) != dict(by_item):
        out.find(
            "high",
            "open requirements per item differ from the bill of materials times the orders",
        )

    # 5. the same files again load nothing, and say so
    again = {kind: upload(kind, _file(kind)) for kind in KINDS}
    reloaded = sum(r["loaded"] for r in again.values())
    already = sum(r["already_loaded"] for r in again.values())
    if (
        reloaded
        or already != sum(rows_in_files.values())
        or any(r["rejected"] for r in again.values())
    ):
        out.find(
            "high",
            f"a second import loaded {reloaded} rows and counted {already} as loaded before",
        )
    if not all(r["summary"].startswith("nothing new") for r in again.values()):
        out.find(
            "medium",
            f"a second import does not say nothing was new: {again['production_order']['summary']}",
        )

    # 6. planted faults: a new order with bad lines, and MO-1 changed by the ERP
    upload(
        "production_order",
        _csv(
            ["order_no", "product_code", "qty", "need_by"],
            [["MO-90", "FG-01", "10", "2026-12-20"]],
        ),
    )
    planted = upload(
        "production_order_line",
        _csv(
            ["order_no", "item_code", "qty", "unit", "need_by"],
            [PLANTED[n][0] for n in sorted(PLANTED)],
        ),
    )
    reasons = {p["line"]: p["reason"] for p in planted["rejected"]}
    caught = 0
    for line, (_, word) in PLANTED.items():
        if word is None:
            if line in reasons:
                out.find("high", f"a clean planted line was left out: {reasons[line]}")
        elif line not in reasons:
            out.find("high", f"planted line {line} (expected: {word}) was loaded")
        elif word not in reasons[line].lower():
            out.find(
                "medium",
                f"planted line {line} was left out for '{reasons[line]}', expected a reason about {word}",
            )
        else:
            caught += 1
    mo1 = orders["MO-1"]
    changed = upload(
        "production_order",
        _csv(
            ["order_no", "product_code", "qty", "need_by"],
            [["MO-1", mo1["product_code"], "75", mo1["need_by"]]],
        ),
    )
    # Q21 (decided 20 Nov): a changed row is applied and listed, not refused
    got_change = changed.get("changes") or [{}]
    if changed["rejected"] or changed.get("changed") != 1 or got_change[0].get("what") != ["header_qty"]:
        out.find("high", f"a changed order was not applied and listed as changed: {changed}")
    if a.db.execute(
        text("select qty from production_order where order_no = 'MO-1'")
    ).scalar_one() != lib.D(75):
        out.find("high", "a changed order was not applied")

    # 7. every real import is audited; the other company sees none of it
    imports = a.db.execute(text("select count(*) from demand_import")).scalar_one()
    audited = a.db.execute(
        text("select count(*) from audit_event where action = 'demand.import'")
    ).scalar_one()
    if imports != audited or imports != 2 * len(KINDS) + 3:
        out.find(
            "high",
            f"{imports} imports, {audited} audit rows (expected {2 * len(KINDS) + 3} of each)",
        )
    seen_by_b = client.get("/api/demand/imports", headers=b.headers("Store"))
    leaked = b.db.execute(text("select count(*) from production_order")).scalar_one()
    if seen_by_b.status_code != 200 or seen_by_b.json() or leaked:
        out.find(
            "high",
            f"another company sees the imports ({seen_by_b.status_code}, {leaked} orders)",
        )
    if (
        client.get("/api/demand/imports", headers=a.headers("Requester")).status_code
        != 403
    ):
        out.find("high", "a requester can see the ERP imports")

    out.metrics = {
        **out.metrics,
        "rows in the files": sum(rows_in_files.values()),
        "requirements checked against the BOM": len(expected),
        "planted faults caught": f"{caught} of {sum(1 for _, w in PLANTED.values() if w)}",
        "rows loaded twice": reloaded,
    }
    return out


def _joiner_oracle() -> dict[tuple[str, str], tuple[Decimal, date, str]]:
    """From the raw files: per plan and department, and item, the quantity (the kit's quantity times the joiners), the
    date it is needed (the earliest start) and the department it is for."""
    kits: dict[str, list[tuple[str, Decimal]]] = defaultdict(list)
    for r in lib.rows("inputs", "joiner_kit", BRIGHTWAVE):
        kits[r["kit"]].append((r["item_code"], lib.D(r["qty"])))
    out: dict[tuple[str, str], tuple[Decimal, date, str]] = {}
    for r in lib.rows("inputs", "joiner_plan", BRIGHTWAVE):
        doc = f"{r['plan_no']}/{r['department']}"
        for item, qty in kits[r["kit"]]:
            before = out.get((doc, item))
            start = date.fromisoformat(r["start_date"])
            out[(doc, item)] = (
                (before[0] if before else Decimal(0)) + qty * lib.D(r["count"]),
                min(start, before[1]) if before else start,
                r["department"],
            )
    return out


def brightwave(out: Outcome) -> None:  # noqa: C901 - one scenario, step by step
    """The IT services company's joiner kits and joiner plan, loaded through the upload screen's API."""
    from p2p.main import app

    w = lib.build_world("e35bw", with_stock=False, dataset=BRIGHTWAVE)
    client = TestClient(app)
    store = w.headers("Store")

    def upload(kind: str, body: bytes) -> dict:
        resp = client.post(
            f"/api/demand/import/{kind}", files={"file": (f"{kind}.csv", body, "text/csv")}, headers=store
        )
        if resp.status_code != 200:
            out.find("high", f"Brightwave: uploading {kind} answered {resp.status_code}: {resp.text[:200]}")
            return {"loaded": 0, "already_loaded": 0, "rejected": [], "summary": ""}
        return resp.json()

    files = {k: (BRIGHTWAVE / "inputs" / f"{k}.csv").read_bytes() for k in ("joiner_kit", "joiner_plan")}
    rows_in = {k: len(lib.rows("inputs", k, BRIGHTWAVE)) for k in files}
    plan_first = upload("joiner_plan", files["joiner_plan"])  # before the kits: every row must say why it waits
    if plan_first["loaded"] or len(plan_first["rejected"]) != rows_in["joiner_plan"] or not all(
        "kit" in p["reason"] for p in plan_first["rejected"]
    ):
        out.find("high", f"Brightwave: a joiner plan loaded before its kits answered {plan_first}")
    first = {k: upload(k, body) for k, body in files.items()}
    for k, got in first.items():
        if got["rejected"] or got["loaded"] != rows_in[k]:
            out.find("high", f"Brightwave {k}: {got['loaded']} of {rows_in[k]} rows loaded; left out {got['rejected'][:3]}")

    # what was stored against the oracle
    want = _joiner_oracle()
    stored: dict[tuple[str, str], tuple[Decimal, date, str]] = {}
    for doc, item, qty, need_by, dep in w.db.execute(
        text(
            "select source_doc_no, item_code, sum(base_qty), min(need_by), max(department_code) "
            "from material_requirement where source_kind = 'joiner' group by source_doc_no, item_code"
        )
    ):
        stored[(doc, item)] = (Decimal(qty), need_by, dep)
    if stored != want:
        wrong = sorted(k for k in set(want) | set(stored) if want.get(k) != stored.get(k))
        out.find("high", f"Brightwave: joiner requirements differ from kit x joiners for {wrong[:4]}")
    made = {
        r.order_no: (r.department_code, r.need_by, r.lines)
        for r in w.db.execute(
            text(
                "select m.order_no, m.department_code, m.need_by, count(l.id) as lines from material_request m "
                "join material_request_line l on l.mr_no = m.no where m.source_kind = 'joiner' "
                "group by m.order_no, m.department_code, m.need_by"
            )
        )
    }
    docs = {doc for doc, _ in want}
    expected_mr = {
        doc: (
            next(d for (o, _), (_, _, d) in want.items() if o == doc),
            min(n for (o, _), (_, n, _) in want.items() if o == doc),
            sum(1 for o, _ in want if o == doc),
        )
        for doc in docs
    }
    if made != expected_mr:
        out.find("high", f"Brightwave: material requests {made}, expected one per plan and department {expected_mr}")

    # the store check buys the kits for the joiner's department, at the estimate
    resp = client.post("/api/material-requests/check", json={}, headers=store)
    if resp.status_code != 200:
        out.find("high", f"Brightwave: the store check answered {resp.status_code}: {resp.text[:200]}")
    else:
        w.db.rollback()
        departments = {
            r[0]: r[1]
            for r in w.db.execute(
                text("select m.order_no, r.department_code from request r join material_request m on m.no = r.source_ref")
            )
        }
        wrong_dep = {doc: d for doc, d in departments.items() if d != expected_mr[doc][0]}
        if wrong_dep or not departments:
            out.find("high", f"Brightwave: kit purchase requests raised for {departments}, expected the joiner's department")

    # the same files again load nothing; planted bad rows are each left out with their reason; a change is reported
    again = {k: upload(k, body) for k, body in files.items()}
    if any(g["loaded"] or g["rejected"] or g["already_loaded"] != rows_in[k] for k, g in again.items()):
        out.find("high", f"Brightwave: a second import of the same files answered {again}")
    planted = upload(
        "joiner_plan",
        _csv(["plan_no", "department", "kit", "count", "start_date"], [JOINER_PLANTED[n][0] for n in sorted(JOINER_PLANTED)]),
    )
    reasons = {p["line"]: p["reason"] for p in planted["rejected"]}
    caught = 0
    for line, (_, word) in JOINER_PLANTED.items():
        if word is None:
            if line in reasons:
                out.find("high", f"Brightwave: a clean planted joiner row was left out: {reasons[line]}")
        elif line not in reasons or word not in reasons[line].lower():
            out.find("high", f"Brightwave: planted joiner row {line} (expected: {word}) gave {reasons.get(line)}")
        else:
            caught += 1
    plan = lib.rows("inputs", "joiner_plan", BRIGHTWAVE)[0]
    changed = upload(
        "joiner_plan",
        _csv(
            ["plan_no", "department", "kit", "count", "start_date"],
            [[plan["plan_no"], plan["department"], plan["kit"], str(int(plan["count"]) + 1), plan["start_date"]]],
        ),
    )
    if changed["loaded"] or changed["rejected"] or changed.get("changed") != 1 or changed["changes"][0]["what"] != ["qty_up"]:
        out.find("high", f"Brightwave: a changed joiner count was not applied and listed: {changed}")
    out.metrics.update(
        {
            "Brightwave joiner rows (kits / plan)": f"{rows_in['joiner_kit']} / {rows_in['joiner_plan']}",
            "Brightwave requirements checked against kit x joiners": len(want),
            "Brightwave planted joiner faults caught": f"{caught} of {sum(1 for _, w_ in JOINER_PLANTED.values() if w_)}",
        }
    )
