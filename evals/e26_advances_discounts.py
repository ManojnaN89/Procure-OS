"""E26: advances paid on orders and taken off their invoices, early-payment discounts, and the notice that an order is paid.

Part A replays the dataset's goods orders from the suppliers who are paid 30% in advance, pays the advances on the days the
dataset paid them, and compares advance payments, tax at source, what each invoice takes off, and what is paid for it with
the dataset's own answers. Part B gives every supplier discount terms and checks the discount with a calculation written
here in exact fractions, on and around the edge of every window, and checks a payment run built around the discount.
Part C checks who is told when a run closes an order."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from fractions import Fraction

import lib
from lib import D, Outcome, pct, rows
from replay import replay
from sqlalchemy import text

ID = "E26"
TITLE = "Advances, early-payment discounts and the closing notice are right"
REQUIREMENT = (
    "Plan P1-19/20: 30% advance on an order and its adjustment against invoices (tax at source on the advance), "
    "a cash discount for paying inside the window, the requester told when the order is paid and closed"
)

ADVANCE_SUPPLIERS = {"SUP-0038", "SUP-0025", "SUP-0029", "SUP-0057", "SUP-0059"}  # terms ADV30N30
BIG = lib.ROOT / "data" / "datasets" / "cbc-demo-v1-seed42"


def half_up(x: Fraction) -> Decimal:
    """Round an exact amount to paise, halves upward."""
    return Decimal((x * 100 + Fraction(1, 2)).__floor__()) / 100


def discount_oracle(taxable: Decimal, rate: Decimal, days: int, baseline: date, paid: date) -> Decimal:
    if rate <= 0 or days <= 0 or (paid - baseline).days > days:
        return Decimal(0)
    return half_up(Fraction(taxable) * Fraction(rate) / 100)


def run(ctx) -> Outcome:
    out = Outcome(True)
    _advances(out)
    _threshold(out)
    _discounts(out, quick=ctx.quick)
    return out


# ---- A. advances against the dataset ----------------------------------------------------------


def _advances(out: Outcome) -> None:
    from p2p.invoices import payment

    if not BIG.exists():
        out.notes.append("the cbc-demo dataset is not generated: the advance part was skipped")
        return
    world = lib.build_world("e26a", with_stock=False, dataset=BIG)
    db = world.db
    r = replay(
        world, dataset=BIG, suppliers=ADVANCE_SUPPLIERS, goods_only=True, advances=True, release_blocked=True,
        load_bank_rates=True,
    )  # fmt: skip
    db.commit()
    inverse = {v: k for k, v in r.platform_no.items()}
    for e in r.errors:
        if e.startswith("advance"):
            out.find("high", f"an advance of the dataset could not be paid: {e}")
    out.metrics["advance orders replayed"] = len(r.advance_rows)
    out.metrics["invoices replayed"] = len(r.platform_no)

    # 1. each advance payment: what is paid and what is held back
    ok = bad = 0
    wrong: list[str] = []
    for no, p in r.advance_rows.items():
        plat = r.advance_payment.get(no)
        if plat is None:
            bad += 1
            wrong.append(f"{no}: not paid on the platform")
            continue
        amount, tds = db.execute(text("select amount, tds_amount from payment where no = :n"), {"n": plat}).one()
        if D(amount) == D(p["amount"]) and D(tds) == D(p["tds_amount"]):
            ok += 1
        else:
            bad += 1
            wrong.append(f"{no}: paid {amount} with tax {tds}, dataset {p['amount']} with tax {p['tds_amount']}")
    out.metrics["advance payments agree %"] = pct(ok, ok + bad)
    if bad:
        out.find("high", f"{bad} advance payment(s) differ from the dataset; first: {wrong[0]}")
    if ok + bad == 0:
        out.find("medium", "no advance was replayed: nothing to compare")

    # 2. what each invoice takes off, and nothing the dataset does not
    booked = {x[0] for x in db.execute(text("select no from invoice where booked_at is not null"))}
    platform_adj = {
        (x[0], x[1]): D(x[2])
        for x in db.execute(text("select advance_payment_no, invoice_no, amount from advance_adjustment"))
    }
    want: dict[tuple[str, str], Decimal] = {}
    for a in rows("history", "advance_adjustment", BIG):
        plat_pay = r.advance_payment.get(a["advance_payment_no"])
        plat_inv = r.platform_no.get(a["invoice_no"])
        if plat_pay and plat_inv:
            want[(plat_pay, plat_inv)] = D(a["amount"])
    adj_ok = adj_bad = adj_skipped = 0
    wrong = []
    for key, amount in want.items():
        if key[1] not in booked:
            adj_skipped += 1  # the dataset's invoice was never approved on the platform (blocked, cancelled)
        elif platform_adj.get(key) == amount:
            adj_ok += 1
        else:
            adj_bad += 1
            wrong.append(f"{inverse[key[1]]}: platform {platform_adj.get(key)}, dataset {amount}")
    back = {v: k for k, v in r.advance_payment.items()}
    # the dataset notes an adjustment when the invoice is paid; the platform takes the advance off when the invoice is
    # approved. So an adjustment the dataset lacks is fine on an invoice the dataset had not paid by its cut-off.
    paid_in_dataset = {a["invoice_no"] for a in rows("history", "payment_allocation", BIG)}
    extra = []
    not_yet_paid = 0
    for k in platform_adj:
        if k in want:
            continue
        if inverse.get(k[1]) not in paid_in_dataset:
            not_yet_paid += 1
        else:
            extra.append(f"{back.get(k[0], k[0])} on {inverse.get(k[1], k[1])} ({platform_adj[k]})")
    out.metrics["adjustments on invoices the dataset had not yet paid"] = not_yet_paid
    out.metrics["adjustments agree %"] = pct(adj_ok, adj_ok + adj_bad)
    out.metrics["adjustments compared"] = adj_ok + adj_bad
    out.metrics["adjustments left out (invoice not approved)"] = adj_skipped
    if adj_bad:
        out.find("high", f"{adj_bad} advance adjustment(s) differ from the dataset; first: {wrong[0]}")
    if extra:
        out.find(
            "high", f"{len(extra)} advance(s) were taken off invoices the dataset does not adjust; first: {extra[0]}"
        )

    # 3. what is payable for each invoice on the day the dataset paid it
    deliveries: dict[tuple[str, str], int] = defaultdict(int)
    for lines_ in r.receipt_lines.values():
        for g in lines_:
            deliveries[(g["po_no"], g["po_line_no"])] += 1
    net_ok = net_bad = 0
    wrong = []
    for p in rows("history", "payment", BIG):
        if p["type"] != "invoice" or p["reference"] not in r.platform_no:
            continue
        plat = r.platform_no[p["reference"]]
        if plat not in booked:
            continue
        got = payment.payable(db, plat, date.fromisoformat(p["paid_on"]))
        multi = any(deliveries[(ln["po_no"], ln["po_line_no"])] > 1 for ln in r.inv_lines.get(p["reference"], []))
        if got.net == D(p["amount"]) or multi:  # which delivery an invoice is for is not in the data
            net_ok += 1
        else:
            net_bad += 1
            wrong.append(f"{p['reference']}: net {got.net} (advance {got.advance}), dataset {p['amount']}")
    out.metrics["invoice payments compared"] = net_ok + net_bad
    out.metrics["invoice payments agree %"] = pct(net_ok, net_ok + net_bad)
    if net_bad:
        out.find("high", f"{net_bad} payable amount(s) differ from the dataset; first: {wrong[0]}")

    # 4. promises that hold whatever the data says
    checks = {
        "an advance's used amount is not the sum of its adjustments": (
            "select count(*) from advance a where a.used_gross <> coalesce((select sum(amount) from advance_adjustment j "
            "where j.advance_payment_no = a.payment_no), 0) or a.used_taxable <> coalesce((select sum(taxable) from "
            "advance_adjustment j where j.advance_payment_no = a.payment_no), 0)"
        ),
        "an advance was used beyond what was paid": "select count(*) from advance where used_gross > gross or used_taxable > taxable",
        "an invoice's advance does not equal its adjustments": (
            "select count(*) from invoice i where i.advance_used <> coalesce((select sum(amount) from advance_adjustment j "
            "where j.invoice_no = i.no), 0)"
        ),
        "an advance was taken off an invoice of another order": (
            "select count(*) from advance_adjustment j join advance a on a.payment_no = j.advance_payment_no "
            "where not exists (select 1 from invoice_line l where l.invoice_no = j.invoice_no and l.po_no = a.po_no)"
        ),
        "an advance was taken off an invoice that arrived before it was paid": (
            "select count(*) from advance_adjustment j join advance a on a.payment_no = j.advance_payment_no "
            "join invoice i on i.no = j.invoice_no where a.paid_on is null or i.received_on < a.paid_on"
        ),
        "an advance's tax row is missing or on the wrong day": (
            "select count(*) from advance a join payment p on p.no = a.payment_no where not exists (select 1 from tds_entry t "
            "where t.payment_no = a.payment_no and t.booked_on = a.paid_on)"
        ),
        "an invoice is paid more than its total": (
            "select count(*) from invoice where advance_used + tds_amount > total and type = 'invoice'"
        ),
    }
    for message, sql in checks.items():
        n = db.execute(text(sql)).scalar_one()
        if n:
            out.find("high", f"{n}: {message}")
    # 5. a payment run over what was approved: each invoice is paid less the advance taken off it, and the file says so
    last_due = db.execute(
        text("select max(due_date) from invoice where status = 'approved' and payment_block = 'none'")
    ).scalar()
    if last_due is not None:
        run_no = payment.propose_run(
            db, last_due + timedelta(days=1), world.logins["Finance"], window_days=0, include_advances=False
        )
        allocations = db.execute(
            text(
                "select a.invoice_no, a.amount, i.total, i.tds_amount, i.advance_used from payment_allocation a "
                "join invoice i on i.no = a.invoice_no where i.type = 'invoice' and a.payment_no in "
                "(select no from payment where run_no = :r)"
            ),
            {"r": run_no},
        ).all()
        wrong_alloc = [x[0] for x in allocations if D(x[1]) != D(x[2]) - D(x[3]) - D(x[4])]
        if wrong_alloc:
            out.find(
                "high",
                f"{len(wrong_alloc)} invoice(s) are not paid as total less tax and advance; first {wrong_alloc[0]}",
            )
        paid_amounts = db.execute(
            text(
                "select count(*) from payment p where p.run_no = :r and p.amount <> (select coalesce(sum(a.amount - a.discount), 0) "
                "from payment_allocation a where a.payment_no = p.no) + p.msme_interest"
            ),
            {"r": run_no},
        ).scalar_one()
        if paid_amounts:
            out.find("high", f"{paid_amounts} payment(s) of the run are not their allocations plus interest")
        out.metrics["invoices in the run that had an advance taken off"] = sum(1 for x in allocations if D(x[4]) > 0)
        if not any(D(x[4]) > 0 for x in allocations):
            out.find("medium", "no invoice with an advance was in the run: the deduction was not exercised")
    out.metrics["advances paid"] = db.execute(
        text("select count(*) from advance where paid_on is not null")
    ).scalar_one()
    out.metrics["adjustments made"] = len(platform_adj)


# ---- A2. tax at source on advances and invoices together, with the threshold lowered so that it is crossed ----


def _threshold(out: Outcome) -> None:
    """With the 50-lakh limit lowered to 5 lakh the advance suppliers cross it. A supplier's tax bases over the year, on its
    advances and its invoices together, must add up to what it bought above the limit (an advance's taxable value is counted
    once, on the advance, and not again on the invoice that takes it off), whatever order things were booked in."""
    from p2p.formulas import payment as rules

    if not BIG.exists():
        return
    limit = Decimal("500000")
    world = lib.build_world("e26c", with_stock=False, dataset=BIG)
    db = world.db
    kept = rules.TDS_194Q_THRESHOLD
    rules.TDS_194Q_THRESHOLD = limit
    try:
        replay(
            world, dataset=BIG, suppliers=ADVANCE_SUPPLIERS, goods_only=True, advances=True, release_blocked=True,
            load_bank_rates=True,
        )  # fmt: skip
    finally:
        rules.TDS_194Q_THRESHOLD = kept
    db.commit()

    def year(d: date) -> int:
        return d.year if d.month >= 4 else d.year - 1

    bought: dict[tuple[str, int], Fraction] = defaultdict(Fraction)
    held: dict[tuple[str, int], Fraction] = defaultdict(Fraction)
    wrong_amount = 0
    for supplier, day, taxable, used, base, rate, amount in db.execute(
        text(
            "select i.supplier_code, t.booked_on, i.taxable, coalesce((select sum(j.taxable) from advance_adjustment j "
            "where j.invoice_no = i.no), 0), t.base, t.rate, t.amount from tds_entry t join invoice i on i.no = t.invoice_no "
            "where i.status <> 'cancelled' union all "
            "select p.supplier_code, t.booked_on, v.taxable, 0, t.base, t.rate, t.amount from tds_entry t "
            "join payment p on p.no = t.payment_no join advance v on v.payment_no = p.no"
        )
    ):
        key = (supplier, year(day))
        bought[key] += Fraction(taxable) - Fraction(used)
        held[key] += Fraction(base)
        if D(amount) != half_up(Fraction(base) * Fraction(rate) / 100):
            wrong_amount += 1
    crossing = 0
    wrong: list[str] = []
    for key, total in bought.items():
        want = max(Fraction(0), total - Fraction(limit))
        crossing += 1 if want > 0 else 0
        if held[key] != want:
            wrong.append(
                f"{key[0]} FY {key[1]}: bases add up to {float(held[key]):.2f}, bought above the limit {float(want):.2f}"
            )
    out.metrics["supplier-years checked (limit lowered)"] = len(bought)
    out.metrics["of which cross the limit"] = crossing
    if wrong:
        out.find(
            "high",
            f"{len(wrong)} supplier-year(s): tax bases differ from what was bought above the limit; first: {wrong[0]}",
        )
    if wrong_amount:
        out.find("high", f"{wrong_amount} tax amount(s) are not the base times the rate")
    if crossing == 0:
        out.find("medium", "no supplier crossed the lowered limit: the threshold was not exercised")


# ---- B. discounts, and C. the closing notice --------------------------------------------------


def _discounts(out: Outcome, *, quick: bool) -> None:
    from p2p.invoices import payment
    from p2p.platform import proposals, users

    world = lib.build_world("e26b", with_stock=False)
    db = world.db
    finance, owner = world.logins["Finance"], world.logins["Approver"]
    db.execute(
        text(
            "insert into payment_terms (code, name, days, baseline, advance_pct, discount_pct, discount_days) values "
            "('E26A', '2/10 net 30 from the invoice', 30, 'invoice_date', 0, 2, 10), "
            "('E26B', '1.5/15 net 45 from acceptance', 45, 'receipt_date', 0, 1.5, 15), "
            "('E26C', 'net 30, no discount', 30, 'invoice_date', 0, 0, 0)"
        )
    )
    terms = {
        "E26A": (Decimal("2"), 10, "invoice_date"),
        "E26B": (Decimal("1.5"), 15, "receipt_date"),
        "E26C": (Decimal(0), 0, "invoice_date"),
    }
    for i, sup in enumerate(sorted(s["code"] for s in rows("master", "supplier"))):
        db.execute(
            text("update supplier set payment_terms_code = :t where code = :c"), {"t": list(terms)[i % 3], "c": sup}
        )
    db.commit()
    r = replay(world, release_blocked=True, load_bank_rates=True)
    db.commit()

    # what the oracle needs, read straight from the tables
    accepted: dict[tuple[str, int], list[date]] = defaultdict(list)
    for po_no, line, day in db.execute(
        text(
            "select l.po_no, l.po_line_no, g.accepted_on from goods_receipt g join gr_line l on l.gr_no = g.no where g.accepted_on is not null"
        )
    ):
        accepted[(po_no, int(line))].append(day)
    inv_lines: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for no, po_no, line in db.execute(text("select invoice_no, po_no, po_line_no from invoice_line")):
        inv_lines[no].append((po_no, int(line)))
    supplier_terms = {c: t for c, t in db.execute(text("select code, payment_terms_code from supplier"))}
    invoices = []
    for no, sup, inv_date, received, taxable, adv_taxable, due, blocked, status in db.execute(
        text(
            "select no, supplier_code, invoice_date, received_on, taxable, advance_taxable_used, due_date, payment_block, status "
            "from invoice where type = 'invoice' and booked_at is not null"
        )
    ):
        rate, days, base = terms[supplier_terms[sup]]
        if base == "invoice_date":
            baseline = inv_date
        else:
            days_ = [d for key in inv_lines[no] for d in accepted[key] if d <= received]
            baseline = max(days_, default=received)
        invoices.append(
            {
                "no": no,
                "rate": rate,
                "days": days,
                "baseline": baseline,
                "taxable": D(taxable) - D(adv_taxable),
                "due": due,
                "block": blocked,
                "status": status,
            }
        )
    out.metrics["invoices with discount terms"] = sum(1 for i in invoices if i["rate"] > 0)

    # B1. the discount on and around the edge of every window
    ok = bad = edge = 0
    wrong: list[str] = []
    for inv in invoices:
        for offset in (-3, 0, inv["days"] - 1, inv["days"], inv["days"] + 1, inv["days"] + 40):
            paid = inv["baseline"] + timedelta(days=offset)
            want = discount_oracle(inv["taxable"], inv["rate"], inv["days"], inv["baseline"], paid)
            got = payment.payable(db, inv["no"], paid).discount
            edge += 1 if offset in (inv["days"], inv["days"] + 1) and inv["rate"] > 0 else 0
            if got == want:
                ok += 1
            else:
                bad += 1
                wrong.append(f"{inv['no']} paid {paid} (baseline {inv['baseline']}): {got}, expected {want}")
    out.metrics["discount checks"] = ok + bad
    out.metrics["of which on or just after a window's last day"] = edge
    out.metrics["discount agrees %"] = pct(ok, ok + bad)
    if bad:
        out.find("high", f"{bad} discount(s) differ from the exact calculation; first: {wrong[0]}")
    if edge == 0:
        out.find("medium", "no invoice was checked at the edge of a discount window")

    # B2. the company can switch discounts off
    db.execute(
        text(
            "insert into org_setting (key, value) values ('payment_take_discounts', 'false') on conflict (key) do update set value = 'false'"
        )
    )
    db.commit()
    still = sum(1 for inv in invoices[:60] if payment.payable(db, inv["no"], inv["baseline"]).discount != 0)
    if still:
        out.find("high", f"{still} invoice(s) still earn a discount with discounts switched off")
    db.execute(text("delete from org_setting where key = 'payment_take_discounts'"))
    db.commit()

    # B3. a run planned for the day on which the most discounts are still to be earned
    live = [i for i in invoices if i["status"] == "approved" and i["block"] == "none" and i["due"] is not None]
    if not live:
        out.find("high", "no invoice is approved for payment: no run can be planned")
        return
    window = 7
    days_range = [
        min(i["baseline"] for i in live) + timedelta(days=n)
        for n in range((max(i["baseline"] for i in live) - min(i["baseline"] for i in live)).days + 1)
    ]
    if quick:
        days_range = days_range[:: max(len(days_range) // 40, 1)]

    def expected_in_run(d: date) -> set[str]:
        chosen = set()
        for i in live:
            if i["due"] <= d + timedelta(days=window):
                chosen.add(i["no"])
            elif (
                i["rate"] > 0
                and discount_oracle(i["taxable"], i["rate"], i["days"], i["baseline"], d) > 0
                and i["baseline"] + timedelta(days=i["days"]) <= d + timedelta(days=window)
            ):
                chosen.add(i["no"])  # paid now it earns the discount; paid in the next run it would not
        return chosen

    def only_by_discount(d: date) -> int:
        return sum(1 for i in live if i["due"] > d + timedelta(days=window) and i["no"] in expected_in_run_cache(d))

    cache: dict[date, set[str]] = {}

    def expected_in_run_cache(d: date) -> set[str]:
        if d not in cache:
            cache[d] = expected_in_run(d)
        return cache[d]

    run_date = max(days_range, key=only_by_discount)
    want_set = expected_in_run_cache(run_date)
    pulled = only_by_discount(run_date)
    out.metrics["run date"] = run_date.isoformat()
    out.metrics["invoices pulled into the run only by their discount"] = pulled
    if pulled == 0:
        out.find("medium", "no run date had an invoice that is paid early for its discount")
    run_no = payment.propose_run(db, run_date, finance, window_days=window, include_advances=False)
    in_run = {
        x[0]
        for x in db.execute(
            text(
                "select a.invoice_no from payment_allocation a join invoice i on i.no = a.invoice_no where i.type = 'invoice' and a.payment_no in (select no from payment where run_no = :r)"
            ),
            {"r": run_no},
        )
    }
    if in_run != want_set:
        out.find(
            "high",
            f"the run holds {len(in_run)} invoice(s) but the due-or-discount rule gives {len(want_set)}; "
            f"{len(in_run - want_set)} too many (e.g. {sorted(in_run - want_set)[:2]}), {len(want_set - in_run)} missing (e.g. {sorted(want_set - in_run)[:2]})",
        )
    by_no = {i["no"]: i for i in invoices}
    bad = 0
    for no, disc in db.execute(
        text(
            "select a.invoice_no, a.discount from payment_allocation a join invoice i on i.no = a.invoice_no where i.type = 'invoice' and a.payment_no in (select no from payment where run_no = :r)"
        ),
        {"r": run_no},
    ):
        i = by_no[no]
        if D(disc) != discount_oracle(i["taxable"], i["rate"], i["days"], i["baseline"], run_date):
            bad += 1
    if bad:
        out.find("high", f"{bad} allocation(s) in the run carry a discount that differs from the exact calculation")
    # a payment is what its allocations say: the amounts, less the discounts, plus the interest
    off = db.execute(
        text(
            "select count(*) from payment p where p.run_no = :r and p.amount <> (select coalesce(sum(a.amount - a.discount), 0) "
            "from payment_allocation a where a.payment_no = p.no) + p.msme_interest"
        ),
        {"r": run_no},
    ).scalar_one()
    if off:
        out.find("high", f"{off} payment(s) are not their allocations less discounts plus interest")
    total = D(db.execute(text("select total from payment_run where no = :r"), {"r": run_no}).scalar_one())
    paid_sum = D(db.execute(text("select sum(amount) from payment where run_no = :r"), {"r": run_no}).scalar_one())
    if total != paid_sum:
        out.find("high", f"the run total is {total} but its payments add up to {paid_sum}")
    saving = D(
        db.execute(
            text(
                "select coalesce(sum(a.discount), 0) from payment_allocation a join payment p on p.no = a.payment_no where p.run_no = :r"
            ),
            {"r": run_no},
        ).scalar_one()
    )
    summary = db.execute(
        text("select summary from proposal where id = (select proposal_id from payment_run where no = :r)"),
        {"r": run_no},
    ).scalar_one()
    if saving and f"{saving:,.2f}" not in summary:
        out.find("high", f"the owner is told '{summary}' but the discount is {saving:,.2f}")
    out.metrics["early-payment discount earned"] = str(saving)

    # C. buyers have logins so that the closing notice can reach them; the run is approved, paid and closes orders
    sfx = lib.uuid.uuid4().hex[:5]
    for code, email in db.execute(
        text("select distinct b.code, b.email from po join org_user b on b.code = po.buyer where b.email is not null")
    ):
        if users.user_id_by_email(world.slug, email):
            continue
        own = f"{email.partition('@')[0]}.{sfx}@e26.example"
        db.execute(text("update org_user set email = :e where code = :c"), {"e": own, "c": code})
        users.create_user(world.slug, own, lib.PASSWORD, code, "Buyer")
    db.commit()
    pid = db.execute(text("select proposal_id from payment_run where no = :n"), {"n": run_no}).scalar_one()
    proposals.approve(db, pid, owner)
    payment.generate_file(db, run_no, finance)
    closed = payment.mark_paid(db, run_no, finance, paid_on=run_date, tenant=world.slug)
    db.commit()

    taken = {
        no: D(d)
        for no, d in db.execute(text("select no, discount_taken from invoice where no = any(:n)"), {"n": list(in_run)})
    }
    wrong_taken = [
        no
        for no, d in taken.items()
        if d
        != discount_oracle(by_no[no]["taxable"], by_no[no]["rate"], by_no[no]["days"], by_no[no]["baseline"], run_date)
    ]
    if wrong_taken:
        out.find(
            "high",
            f"{len(wrong_taken)} paid invoice(s) record a discount taken that differs from the exact calculation",
        )
    unpaid = db.execute(
        text("select count(*) from invoice where no = any(:n) and status <> 'paid'"), {"n": list(in_run)}
    ).scalar_one()
    if unpaid:
        out.find("high", f"{unpaid} invoice(s) of the run are not marked paid")
    spent = D(db.execute(text("select coalesce(sum(amount), 0) from budget_actual")).scalar_one())
    expect_spent = D(
        db.execute(
            text(
                "select coalesce(sum(taxable - discount_taken), 0) from invoice where status = 'paid' and type = 'invoice'"
            )
        ).scalar_one()
    )
    if abs(spent - expect_spent) > D("0.05"):
        out.find("high", f"budget actuals are {spent} but the paid invoices less their discounts are {expect_spent}")

    # who is told: the buyer of each order the run closed, once, and nobody for an order that stayed open
    notified = {
        (x[0], x[1]) for x in db.execute(text("select user_id, link from notification where kind = 'order_paid'"))
    }
    count = db.execute(text("select count(*) from notification where kind = 'order_paid'")).scalar_one()
    expected: set[tuple[int, str]] = set()
    for po_no in closed:
        buyer_email = db.execute(
            text("select b.email from po join org_user b on b.code = po.buyer where po.no = :p"), {"p": po_no}
        ).scalar()
        uid = users.user_id_by_email(world.slug, buyer_email) if buyer_email else None
        if uid:
            expected.add((uid, f"#/purchase-orders/{po_no}"))
    if notified != expected:
        out.find(
            "high", f"{len(notified - expected)} unexpected and {len(expected - notified)} missing closing notice(s)"
        )
    if count != len(notified):
        out.find("high", f"{count - len(notified)} closing notice(s) were sent twice")
    open_told = [n for n in notified if n[1].rsplit("/", 1)[1] not in set(closed)]
    if open_told:
        out.find("high", f"{len(open_told)} notice(s) were sent for an order that was not closed")
    out.metrics["orders closed by the run"] = len(closed)
    out.metrics["closing notices"] = len(notified)
    if closed and not notified:
        out.find("medium", "the run closed orders but nobody was told: the notice was not exercised")
    _ = r
