"""E45: reconciliation (plan step 13, P1-39) on the main demo company, against an oracle written here from the raw files.

The dataset's orders, goods receipts, service entries and invoices are replayed into a company in the order they happened.
The oracle then works out, from the dataset's own CSV files with exact fractions:

* received not invoiced and invoiced not received per order line, at every month end of the dataset's GRNI answer key and
  on the eval day: goods accepted at the order price (half up to the paisa per receipt line) and confirmed services on the
  day they were accepted, against the taxable value of the invoices the platform entered (the dataset's cancelled ones
  never count) on the day they were received, with the age of the oldest open receipt or invoice; totals by supplier;
* the MSMED Act list: unpaid invoices of micro and small suppliers past, or within the warning days of, the deadline (the
  days agreed in writing up to `msme_max_days`, else `msme_no_agreement_days`, from the latest acceptance before the
  invoice came), with the days late; and the day-45 / day-46 edge through the API;
* every line of the dataset's own supplier statements (an invoice the supplier says it sent that we never got is only on
  theirs) and of a planted statement (same number written differently, a paisa more, an unknown number, a credit note, a
  line twice, the payment by its bank reference): matched, amount differs, only theirs or only ours; a second import of
  the same file adds nothing;
* one full trace (request, quotation request, order, receipts, invoices, payment) against the raw files.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any

import lib
from fastapi.testclient import TestClient
from lib import D, Outcome, rows
from replay import insert, replay
from sqlalchemy import text

ID = "E45"
TITLE = "Received not invoiced, supplier statements, small-supplier deadlines and the order trace reconcile"
REQUIREMENT = (
    "Plan step 13, P1-39: received not invoiced (and the reverse) exact to the paisa with its age; a supplier's statement "
    "matched line by line with the reason for each difference; MSME invoices listed against the MSMED Act deadline (Q16); "
    "the whole chain of a document traced end to end"
)
F = Fraction


def paise(x: Fraction) -> Decimal:
    return Decimal((x * 100 + F(1, 2)).__floor__()) / Decimal(100)


def day(value: str) -> date:
    return date.fromisoformat(value[:10])


def norm(number: str) -> str:
    return "".join(c for c in number.upper() if c.isalnum())


def run(ctx) -> Outcome:  # noqa: C901 - one story, told in order
    from p2p.invoices import payment
    from p2p.main import app
    from p2p.platform import proposals

    out = Outcome(True)
    world = lib.build_world("e45", with_stock=False)
    db = world.db
    client = TestClient(app)
    hist = lambda name: rows("history", name)  # noqa: E731
    r = replay(world)
    db.commit()
    if r.errors:
        out.notes.append(
            f"{len(r.errors)} invoice(s) not entered by the replay, left out of the oracle: {r.errors[:3]}"
        )
    entered = set(r.platform_no)
    invoices = {i["no"]: i for i in hist("invoice")}
    live = {n for n in entered if invoices[n]["status"] != "cancelled"}
    from p2p.invoices import service as invoice_service

    for n in sorted(entered - live):  # the dataset cancelled these (a corrected invoice followed): AP cancels them too
        status = db.execute(text("select status from invoice where no = :n"), {"n": r.platform_no[n]}).scalar_one()
        if status != "cancelled":
            invoice_service.cancel_invoice(
                db, r.platform_no[n], "cancelled, as the dataset records", world.logins["AP"]
            )
    db.commit()
    po_lines = {(x["po_no"], int(x["line_no"])): x for x in hist("po_line")}
    po_supplier = {p["no"]: p["supplier_code"] for p in hist("po")}

    # ---- 1. received not invoiced: the oracle's own events ----
    got_events: dict[tuple[str, int], list[tuple[date, Fraction]]] = defaultdict(list)
    receipts = {g["no"]: g for g in hist("goods_receipt")}
    for ln in hist("gr_line"):
        g = receipts[ln["gr_no"]]
        if not g["accepted_on"] or not ln["qty_accepted"]:
            continue
        key = (ln["po_no"], int(ln["po_line_no"]))
        value = F(paise(F(ln["qty_accepted"]) * F(po_lines[key]["unit_price"])))
        got_events[key].append((day(g["accepted_on"]), value))
    for s in hist("service_receipt"):
        got_events[(s["po_no"], int(s["po_line_no"]))].append((day(s["accepted_on"]), F(s["amount_accepted"])))
    billed_events: dict[tuple[str, int], list[tuple[date, Fraction]]] = defaultdict(list)
    for ln in hist("invoice_line"):
        inv = invoices[ln["invoice_no"]]
        if ln["invoice_no"] not in live:
            continue
        sign = -1 if inv["type"] == "credit_note" else 1
        billed_events[(ln["po_no"], int(ln["po_line_no"]))].append((day(inv["received_on"]), sign * F(ln["taxable"])))

    def oldest(events: list[tuple[date, Fraction]], covered: Fraction) -> date:
        total = F(0)
        for d, v in sorted(events):
            total += v
            if total > covered:
                return d
        raise AssertionError("nothing open")

    def oracle_grni(on: date) -> dict[tuple[str, int], tuple[Decimal, Decimal, int]]:
        found = {}
        for key in set(got_events) | set(billed_events):
            g = [(d, v) for d, v in got_events.get(key, []) if d <= on]
            b = [(d, v) for d, v in billed_events.get(key, []) if d <= on]
            rec, inv = sum((v for _, v in g), F(0)), sum((v for _, v in b), F(0))
            if rec > inv:
                found[key] = (paise(rec - inv), D("0"), (on - oldest(g, inv)).days)
            elif inv > rec:
                found[key] = (D("0"), paise(inv - rec), (on - oldest(b, rec)).days)
        return found

    answer_key: dict[date, dict[tuple[str, int], Decimal]] = defaultdict(lambda: defaultdict(lambda: D(0)))
    for x in rows("truth", "truth_grni"):
        answer_key[day(x["month_end"])][(x["po_no"], int(x["po_line_no"]))] += D(x["value_not_invoiced"])
    key_dates = set(answer_key)
    key_wrong: list[str] = []
    dates = sorted(key_dates | {lib.TODAY})
    wrong: list[str] = []
    checked = 0
    for on in dates:
        resp = client.get(f"/api/reconciliation/received-not-invoiced?as_of={on}", headers=world.headers("AP"))
        if resp.status_code != 200:
            out.find("high", f"received not invoiced on {on} answered {resp.status_code}")
            continue
        body = resp.json()
        platform = {
            (x["po_no"], int(x["line_no"])): (D(x["not_invoiced"]), D(x["not_received"]), int(x["age_days"]))
            for x in body["lines"]
        }
        expected = oracle_grni(on)
        checked += len(expected)
        for key in sorted(set(platform) | set(expected)):
            if platform.get(key) != expected.get(key):
                wrong.append(f"{on} {key}: platform {platform.get(key)}, oracle {expected.get(key)}")
        by_sup: dict[str, list[Decimal]] = defaultdict(lambda: [D(0), D(0)])
        for key, (a, b, _) in expected.items():
            by_sup[po_supplier[key[0]]][0] += a
            by_sup[po_supplier[key[0]]][1] += b
        shown = {s["supplier_code"]: [D(s["not_invoiced"]), D(s["not_received"])] for s in body["suppliers"]}
        if shown != dict(by_sup):
            wrong.append(f"{on} supplier totals: platform {shown}, oracle {dict(by_sup)}")
        if D(body["not_invoiced"]) != sum((v[0] for v in by_sup.values()), D(0)):
            wrong.append(f"{on} total not invoiced {body['not_invoiced']}")
        if on == lib.TODAY:
            out.metrics["received not invoiced today (Rs)"] = body["not_invoiced"]
            out.metrics["invoiced not received today (Rs)"] = body["not_received"]
        if on in key_dates:  # the dataset's own answer key for the month end
            for key, value in answer_key.get(on, {}).items():
                mine = platform.get(key, (D(0), D(0), 0))[0]
                if mine != value:
                    key_wrong.append(f"{on} {key}: platform {mine}, answer key {value}")
            for key, (a, _, _) in platform.items():
                if a and key not in answer_key.get(on, {}):
                    key_wrong.append(f"{on} {key}: platform {a}, not in the answer key")
    out.metrics["dates checked"] = len(dates)
    out.metrics["answer-key month ends"] = len(key_dates)
    if key_wrong:
        out.find("high", f"{len(key_wrong)} month-end figure(s) differ from the answer key; first: {key_wrong[:3]}")
    out.metrics["open order lines checked"] = checked
    out.metrics["received-not-invoiced differences"] = len(wrong)
    if wrong:
        out.find("high", f"{len(wrong)} received-not-invoiced figure(s) differ from the oracle; first: {wrong[:3]}")
    if client.get("/api/reconciliation/received-not-invoiced", headers=world.headers("Requester")).status_code != 403:
        out.find("high", "a requester can see what the books carry")

    # ---- 2. the MSMED Act list (before anything is paid) ----
    settings = {s["key"]: s["value"] for s in rows("master", "setting")}
    max_days, none_days = int(settings.get("msme_max_days", "45")), int(settings.get("msme_no_agreement_days", "15"))
    warn = int(settings.get("msme_warn_days", "7"))
    small = {s["code"]: s for s in rows("master", "supplier") if s["msme_category"] in ("micro", "small")}
    acceptances: dict[tuple[str, int], list[date]] = defaultdict(list)  # every acceptance on a line, even of nothing
    for ln in hist("gr_line"):
        if receipts[ln["gr_no"]]["accepted_on"]:
            acceptances[(ln["po_no"], int(ln["po_line_no"]))].append(day(receipts[ln["gr_no"]]["accepted_on"]))
    for s in hist("service_receipt"):
        acceptances[(s["po_no"], int(s["po_line_no"]))].append(day(s["accepted_on"]))
    lines_of: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for ln in hist("invoice_line"):
        lines_of[ln["invoice_no"]].append((ln["po_no"], int(ln["po_line_no"])))
    expected_msme: dict[str, tuple[int, date]] = {}
    for no in sorted(live):
        inv = invoices[no]
        sup = small.get(inv["supplier_code"])
        if sup is None or inv["type"] != "invoice":
            continue
        came = day(inv["received_on"])
        accepted = max((d for k in lines_of[no] for d in acceptances.get(k, []) if d <= came), default=came)
        agreed = sup["msme_agreement"] == "true"
        limit = min(max_days, int(sup["agreed_payment_days"] or max_days)) if agreed else none_days
        deadline = accepted + timedelta(days=limit)
        late = (lib.TODAY - deadline).days
        if late >= -warn:
            expected_msme[r.platform_no[no]] = (max(late, 0), deadline)
    msme = client.get(f"/api/reconciliation/msme?as_of={lib.TODAY}", headers=world.headers("Finance")).json()
    shown_msme = {i["invoice"]: (i["days_late"], day(i["deadline"])) for i in msme["invoices"]}
    out.metrics["small-supplier invoices listed"] = len(shown_msme)
    if shown_msme != expected_msme:
        diff = sorted(set(shown_msme.items()) ^ set(expected_msme.items()))[:4]
        out.find("high", f"the MSME list differs from the oracle ({len(shown_msme)} vs {len(expected_msme)}): {diff}")
    if expected_msme:
        latest = max(expected_msme, key=lambda n: expected_msme[n][1])
        deadline = expected_msme[latest][1]
        edge = {}
        for shift in (-1, 0, 1):
            on = deadline + timedelta(days=shift)
            got = client.get(f"/api/reconciliation/msme?as_of={on}&within=0", headers=world.headers("Finance")).json()
            edge[shift] = {i["invoice"]: i["days_late"] for i in got["invoices"]}.get(latest)
        if edge != {-1: None, 0: 0, 1: 1}:
            out.find(
                "high", f"the deadline edge of {latest} ({deadline}) is wrong: {edge} (want the day itself not late)"
            )
    else:
        out.find("medium", "the dataset has no small-supplier invoice to check")

    # ---- 3. pay what is due (so payments are on the books for the statements and the trace) ----
    finance, owner = world.logins["Finance"], world.logins["Approver"]
    last_due = db.execute(text("select max(due_date) from invoice where status = 'approved'")).scalar()
    run_date = last_due + timedelta(days=1)
    run_no = payment.propose_run(db, run_date, finance, window_days=0)
    pid = db.execute(text("select proposal_id from payment_run where no = :n"), {"n": run_no}).scalar_one()
    proposals.approve(db, pid, owner)
    payment.generate_file(db, run_no, finance)
    payment.mark_paid(db, run_no, finance, paid_on=run_date)

    # ---- 4. supplier statements ----
    def upload(supplier: str, body: str, name: str, **period: date) -> Any:
        query = "&".join([f"supplier={supplier}", *(f"{k}={v}" for k, v in period.items())])
        return client.post(
            f"/api/reconciliation/vendor-statements?{query}",
            files={"file": (name, body.encode(), "text/csv")},
            headers=world.headers("AP"),
        )

    def ours_in(supplier: str, start: date, end: date) -> set[str]:
        return {
            r.platform_no[n]
            for n in live
            if invoices[n]["supplier_code"] == supplier
            and start <= day(invoices[n]["invoice_date"]) <= end
            and day(invoices[n]["received_on"]) <= end
        }

    bad_lines: list[str] = []
    statements = 0
    groups: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for x in hist("vendor_statement_line"):
        groups[(x["supplier_code"], x["statement_date"])].append(x)
    for (supplier, as_at), lines_ in sorted(groups.items()):
        body = "date,type,document_no,amount,reference\n" + "".join(
            f"{x['invoice_date']},invoice,{x['supplier_invoice_no']},{x['amount']},\n" for x in lines_
        )
        start = min(day(x["invoice_date"]) for x in lines_)
        end = day(as_at)  # our books as they stood on the statement date
        resp = upload(supplier, body, f"{supplier}-{as_at}.csv", period_from=start, period_to=end)
        if resp.status_code != 200:
            out.find("high", f"the dataset's statement of {supplier} on {as_at} was refused: {resp.text[:200]}")
            continue
        statements += 1
        results = resp.json()["results"]
        named = set()
        for n, x in enumerate(lines_, start=2):
            mine = x["our_invoice_no"]
            if mine and mine in live and day(invoices[mine]["received_on"]) <= end:
                named.add(r.platform_no[mine])
                want = "matched" if F(invoices[mine]["total"]) == F(x["amount"]) else "amount_differs"
            else:
                want = "only_theirs"
            got = next((g for g in results if g["row_no"] == n), None)
            if (
                got is None
                or got["outcome"] != want
                or (want != "only_theirs" and got["our_ref"] != r.platform_no[mine])
            ):
                bad_lines.append(f"{supplier} {x['supplier_invoice_no']}: {got and got['outcome']}, oracle {want}")
        only_ours = {g["our_ref"] for g in results if g["outcome"] == "only_ours"}
        if only_ours != ours_in(supplier, start, end) - named:
            bad_lines.append(
                f"{supplier} {as_at} only ours {sorted(only_ours)}, oracle {sorted(ours_in(supplier, start, end) - named)}"
            )

    # the planted statement: the supplier with the most invoices paid by the run
    paid = db.execute(
        text(
            "select p.supplier_code, p.no, p.amount, p.utr, count(*) from payment p join payment_allocation a "
            "on a.payment_no = p.no where p.run_no = :r and p.type = 'invoice' group by p.no order by 5 desc, 1 limit 1"
        ),
        {"r": run_no},
    ).one()
    supplier, pay_no, pay_amount, utr = paid[0], paid[1], D(paid[2]), paid[3]
    mine = sorted(
        (n for n in live if invoices[n]["supplier_code"] == supplier and invoices[n]["type"] == "invoice"),
        key=lambda n: (invoices[n]["invoice_date"], n),
    )[-3:]
    if len(mine) < 3:
        out.find("medium", f"{supplier} has fewer than three invoices to plant a statement with")
    else:
        a, b, c = (invoices[n] for n in mine)
        start = day(a["invoice_date"])
        plant = [
            (a["invoice_date"], "invoice", a["supplier_invoice_no"], a["total"], "", "matched", mine[0]),
            (b["invoice_date"], "invoice", b["supplier_invoice_no"].lower().replace("/", " "), b["total"], "", "matched", mine[1]),
            (c["invoice_date"], "invoice", c["supplier_invoice_no"], str(D(c["total"]) + D("0.01")), "", "amount_differs", mine[2]),
            (c["invoice_date"], "invoice", "ZZ-404", "999.00", "", "only_theirs", None),
            (c["invoice_date"], "credit note", "CN-E45", "10.00", "", "only_theirs", None),
            (a["invoice_date"], "invoice", a["supplier_invoice_no"], a["total"], "", "only_theirs", None),  # twice
            (str(run_date), "payment", "NEFT", str(pay_amount), utr, "matched", pay_no),
        ]  # fmt: skip
        body = "date,type,document_no,amount,reference\n" + "".join(
            f"{p[0]},{p[1]},{p[2]},{p[3]},{p[4]}\n" for p in plant
        )
        finance_try = client.post(
            f"/api/reconciliation/vendor-statements?supplier={supplier}",
            files={"file": ("p.csv", body.encode(), "text/csv")},
            headers=world.headers("Finance"),
        )
        if finance_try.status_code != 403:
            out.find("high", f"Finance (not AP) imported a supplier statement: {finance_try.status_code}")
        resp = upload(supplier, body, "planted.csv", period_from=start, period_to=run_date)
        if resp.status_code != 200:
            out.find("high", f"the planted statement was refused: {resp.text[:200]}")
        else:
            statements += 1
            results = resp.json()["results"]
            for n, p in enumerate(plant, start=2):
                got = next((g for g in results if g["row_no"] == n), None)
                want_ref = r.platform_no[p[6]] if p[6] in r.platform_no else p[6]
                if got is None or (got["outcome"], got["our_ref"]) != (p[5], want_ref):
                    bad_lines.append(
                        f"planted line {n} {p[2]}: {got and (got['outcome'], got['our_ref'])}, oracle {(p[5], want_ref)}"
                    )
            differs = next((g for g in results if g["row_no"] == 4), {})
            if differs.get("difference") != "0.01":
                bad_lines.append(f"the planted paisa shows as {differs.get('difference')}")
            expected_ours = ours_in(supplier, start, run_date) - {r.platform_no[n] for n in mine}
            only_ours = {g["our_ref"] for g in results if g["outcome"] == "only_ours"}
            if only_ours != expected_ours:
                bad_lines.append(f"planted only ours {sorted(only_ours)}, oracle {sorted(expected_ours)}")
            before = db.execute(text("select count(*) from vendor_statement_line")).scalar_one()
            again = upload(supplier, body, "planted.csv", period_from=start, period_to=run_date).json()
            if (
                not again.get("already_imported")
                or db.execute(text("select count(*) from vendor_statement_line")).scalar_one() != before
            ):
                out.find("high", "importing the same statement again added rows")
    out.metrics["statements imported"] = statements
    out.metrics["statement lines wrong"] = len(bad_lines)
    if bad_lines:
        out.find("high", f"{len(bad_lines)} statement line(s) differ from the oracle; first: {bad_lines[:3]}")
    audited = db.execute(text("select count(*) from audit_event where action = 'vendor_statement.import'")).scalar_one()
    if audited != statements:
        out.find("high", f"{statements} statements imported, {audited} audit rows")

    # ---- 5. one full trace: request, quotation request, order, receipts, invoices, payment ----
    rfq_lines = hist("rfq_line")
    rfq_by_request = defaultdict(set)
    for x in rfq_lines:
        rfq_by_request[x["request_no"]].add(x["rfq_no"])
    allocated = {a[0] for a in db.execute(text("select invoice_no from payment_allocation"))}
    gr_by_po = defaultdict(list)
    for g in receipts.values():
        gr_by_po[g["po_no"]].append(g["no"])
    inv_by_po = defaultdict(set)
    for ln in hist("invoice_line"):
        if ln["invoice_no"] in live:
            inv_by_po[ln["po_no"]].add(ln["invoice_no"])
    request_of = {}
    for x in hist("po_line"):
        request_of.setdefault(x["po_no"], set()).add(x["request_no"])
    chosen = next(
        (
            p
            for p in sorted(po_supplier)
            if len(request_of[p]) == 1
            and rfq_by_request[next(iter(request_of[p]))]
            and gr_by_po[p]
            and inv_by_po[p]
            and all(r.platform_no[n] in allocated for n in inv_by_po[p])
            and all(next(iter(request_of[q])) != next(iter(request_of[p])) or q == p for q in po_supplier)
        ),
        None,
    )
    if chosen is None:
        out.find("medium", "no order with a quotation request, receipts and paid invoices to trace")
        return out
    request_no = next(iter(request_of[chosen]))
    requests = {x["no"]: x for x in hist("request")}
    insert(db, "request", requests[request_no])
    for x in hist("request_line"):
        if x["request_no"] == request_no:
            insert(db, "request_line", x)
    rfq_nos = rfq_by_request[request_no]
    for x in hist("rfq"):
        if x["no"] in rfq_nos:
            insert(db, "rfq", x)
    for x in rfq_lines:
        if x["rfq_no"] in rfq_nos:
            insert(db, "rfq_line", x)
    for x in hist("po_line"):
        if x["po_no"] == chosen:
            db.execute(
                text("update po_line set request_no = :r, request_line_no = :n where po_no = :p and line_no = :l"),
                {"r": x["request_no"], "n": int(x["request_line_no"]), "p": chosen, "l": int(x["line_no"])},
            )
    db.commit()
    invoice_no = r.platform_no[sorted(inv_by_po[chosen])[0]]
    resp = client.get(f"/api/trace/{invoice_no}", headers=world.headers("Finance"))
    if resp.status_code != 200:
        out.find("high", f"the trace of {invoice_no} answered {resp.status_code}")
        return out
    stages = {s["stage"]: {i["no"]: i for i in s["items"]} for s in resp.json()["stages"]}
    people = {u["code"]: u["name"] for u in rows("master", "user")}
    want_receipts = {}
    for g in gr_by_po[chosen]:
        lines_ = [ln for ln in hist("gr_line") if ln["gr_no"] == g]
        qty = sum((F(ln["qty_accepted"] or "0") for ln in lines_), F(0))
        value = sum((F(paise(F(ln["qty_accepted"] or "0") * F(po_lines[(chosen, int(ln["po_line_no"]))]["unit_price"]))) for ln in lines_), F(0))  # fmt: skip
        want_receipts[g] = (
            Decimal(qty.numerator) / Decimal(qty.denominator),
            paise(value),
            people.get(receipts[g]["received_by"]),
        )
    trace_bad = []
    if set(stages.get("request", {})) != {request_no}:
        trace_bad.append(f"requests {sorted(stages.get('request', {}))}")
    if set(stages.get("rfq", {})) != rfq_nos:
        trace_bad.append(f"quotation requests {sorted(stages.get('rfq', {}))}, oracle {sorted(rfq_nos)}")
    if chosen not in stages.get("po", {}):
        trace_bad.append(f"order {chosen} missing")
    for g, (qty, value, who) in want_receipts.items():
        item = stages.get("goods_receipt", {}).get(g)
        if item is None or (D(item["qty"]), D(item["amount"]), item["who"]) != (qty, value, who):
            trace_bad.append(
                f"receipt {g}: {item and (item['qty'], item['amount'], item['who'])}, oracle {(qty, value, who)}"
            )
    for n in inv_by_po[chosen]:
        item = stages.get("invoice", {}).get(r.platform_no[n])
        if item is None or D(item["amount"]) != D(invoices[n]["total"]):
            trace_bad.append(f"invoice {n}: {item and item['amount']}, oracle {invoices[n]['total']}")
    pays = {
        p[0]
        for p in db.execute(
            text("select distinct payment_no from payment_allocation where invoice_no = any(:i)"),
            {"i": [r.platform_no[n] for n in inv_by_po[chosen]]},
        )
    }
    if set(stages.get("payment", {})) != pays or any(i["status"] != "paid" for i in stages.get("payment", {}).values()):
        trace_bad.append(f"payments {sorted(stages.get('payment', {}))}, raw allocation {sorted(pays)}")
    out.metrics["trace of"] = f"{invoice_no} ({chosen})"
    out.metrics["trace stages"] = len(stages)
    if trace_bad:
        out.find("high", f"the trace of {invoice_no} differs from the raw files: {trace_bad[:4]}")
    return out
