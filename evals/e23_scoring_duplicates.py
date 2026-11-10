"""E23: vendor scores against the dataset's hidden supplier behaviour and an independent recomputation, the best-vendor
pick against the cheapest pick, and the check before ordering against the repeat requests the dataset planted."""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction

import lib
from lib import D, Outcome, rows
from sqlalchemy import text

ID = "E23"
TITLE = "Vendor scores follow how suppliers really behave, and planted repeat requests are caught"
REQUIREMENT = (
    "Plan P1-05 and the vendor choice: a supplier's score reflects its delivery, quality and price; the best-vendor mode is "
    "no worse than the cheapest; a request that repeats another is flagged and an ordinary one is not"
)
IST = timezone(timedelta(hours=5, minutes=30))
NEUTRAL, PRIOR, NEUTRAL_PRICE = Fraction(7, 10), 3, Fraction(8, 10)


def columns(db, table: str) -> set[str]:
    return {r[0] for r in db.execute(text("select column_name from information_schema.columns where table_name = :t"), {"t": table})}


def insert(db, table: str, row: dict, extra: dict | None = None) -> None:
    have = columns(db, table)
    values = {k: (v if v != "" else None) for k, v in row.items() if k in have}
    values.update(extra or {})
    db.execute(text(f"insert into {table} ({', '.join(values)}) values ({', '.join(':' + k for k in values)})"), values)  # noqa: S608


def when(value: str) -> datetime:
    return datetime.fromisoformat(value)


def spearman(a: list[float], b: list[float]) -> float:
    """Rank correlation, ties given the average rank."""

    def ranks(xs: list[float]) -> list[float]:
        order = sorted(range(len(xs)), key=lambda i: xs[i])
        out = [0.0] * len(xs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
                j += 1
            for k in range(i, j + 1):
                out[order[k]] = (i + j) / 2 + 1
            i = j + 1
        return out

    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb, strict=True))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((y - mb) ** 2 for y in rb) ** 0.5
    return cov / (va * vb) if va and vb else 0.0


def smoothed(good: Fraction, n: int) -> Fraction:
    return (good + NEUTRAL * PRIOR) / (n + PRIOR)


def run(ctx) -> Outcome:
    from p2p.requests import duplicates
    from p2p.sourcing import service as sourcing
    from p2p.suppliers import scoring

    out = Outcome(True)
    world = lib.build_world("e23")
    db = world.db
    history = lambda name: rows("history", name)  # noqa: E731
    as_of = lib.TODAY

    # ---- load the orders and receipts of the dataset
    for r in history("po"):
        insert(db, "po", r)
    for r in history("po_line"):
        insert(db, "po_line", r, {"received_qty": r["received_qty"] or 0, "invoiced_qty": 0, "source": "award", "request_no": r["po_no"], "request_line_no": int(r["line_no"]), "taxable": (D(r["qty"]) * D(r["unit_price"])).quantize(D("0.01"))})
    for g in history("goods_receipt"):
        insert(db, "goods_receipt", g, {"posted_by": "import"})
    for ln in history("gr_line"):
        insert(db, "gr_line", ln)
    db.commit()

    scores = scoring.score_all(db, as_of)
    truth = {r["supplier_code"]: r for r in rows("truth", "truth_supplier")}
    active = [c for c in scores if c in truth]
    out.metrics["suppliers scored"] = len(scores)

    # ---- A. the delivery and quality scores follow the behaviour the dataset gave each supplier
    delivery = [float(scores[c].delivery) for c in active]
    quality = [float(scores[c].quality) for c in active]
    on_time = [float(truth[c]["on_time_prob"]) for c in active]
    accepted = [1 - float(truth[c]["rejection_rate"]) for c in active]
    rho_delivery, rho_quality = spearman(delivery, on_time), spearman(quality, accepted)
    by_profile: dict[str, list[float]] = defaultdict(list)
    for c in active:
        by_profile[truth[c]["profile"]].append(float(scores[c].total))
    mean = {p: sum(v) / len(v) for p, v in by_profile.items()}
    out.metrics["rank agreement: delivery score vs true on-time rate"] = round(rho_delivery, 2)
    out.metrics["rank agreement: quality score vs true acceptance rate"] = round(rho_quality, 2)
    out.metrics["mean score by hidden profile"] = {p: round(v, 1) for p, v in sorted(mean.items())}
    judged = sum(scores[c].history.receipts for c in active) / max(len(active), 1)
    out.metrics["receipt lines judged per supplier (on average)"] = round(judged, 1)
    if judged < 15:
        out.notes.append("too little history for rank agreement to mean much: only the order of the profile means is required")
    if judged >= 15 and rho_delivery < 0.4:
        out.find("high", f"the delivery score hardly follows how often suppliers really deliver on time (rank agreement {rho_delivery:.2f})")
    if judged >= 15 and rho_quality < 0.2:
        out.find("medium", f"the quality score hardly follows how much each supplier really rejects (rank agreement {rho_quality:.2f})")
    if not (mean.get("reliable", 0) > mean.get("average", 0) > mean.get("poor", 0)):
        out.find("high", f"the mean score does not fall from reliable to average to poor suppliers: {mean}")

    # ---- B. the scores recomputed from the raw files, without the platform's queries
    po_supplier = {r["no"]: r["supplier_code"] for r in history("po")}
    promised = {(r["po_no"], r["line_no"]): r["promised_date"] for r in history("po_line")}
    receipts = {r["no"]: when(r["gate_entry_at"]).astimezone(IST).date() for r in history("goods_receipt")}
    deliveries: dict[str, list[bool]] = defaultdict(list)
    shares: dict[str, list[Fraction]] = defaultdict(list)
    for g in history("gr_line"):
        sup, day = po_supplier[g["po_no"]], receipts[g["gr_no"]]
        if day > as_of:
            continue
        p = promised.get((g["po_no"], g["po_line_no"]))
        if p:
            deliveries[sup].append(day <= date.fromisoformat(p))
        if g["qty_accepted"] != "":
            shares[sup].append(Fraction(D(g["qty_accepted"])) / Fraction(D(g["qty_received"])))
    suppliers = {r["code"]: r for r in rows("master", "supplier")}
    contracts = {r["code"]: r for r in rows("master", "contract")}
    base: dict[tuple[str, str], dict[str, tuple[Fraction, Fraction]]] = defaultdict(dict)
    for r in rows("master", "price_record"):
        s = suppliers.get(r["supplier_code"])
        if not s or s["status"] != "active":
            continue
        if not (date.fromisoformat(r["valid_from"]) <= as_of and (not r["valid_to"] or date.fromisoformat(r["valid_to"]) >= as_of)):
            continue
        if r["contract_code"]:
            c = contracts.get(r["contract_code"])
            if not c or c["status"] != "active" or not (date.fromisoformat(c["valid_from"]) <= as_of and (not c["valid_to"] or date.fromisoformat(c["valid_to"]) >= as_of)):
                continue
        key = (r["item_code"], r["uom"])
        mine = (Fraction(D(r["min_qty"] or "0")), Fraction(D(r["price"])))
        held = base[key].get(r["supplier_code"])
        if held is None or mine < held:
            base[key][r["supplier_code"]] = mine
    price_shares: dict[str, list[Fraction]] = defaultdict(list)
    for prices in base.values():
        if len(prices) < 2:
            continue
        best = min(p for _, p in prices.values())
        for sup, (_, p) in prices.items():
            price_shares[sup].append(min(Fraction(1), best / p))
    weights = (Fraction(40), Fraction(40), Fraction(20))
    off = []
    for code, sc in scores.items():
        n = len(deliveries[code])
        on = sum(deliveries[code])
        delivery_part = Fraction(8, 10) * smoothed(Fraction(on), n) + Fraction(2, 10) * smoothed(Fraction(0), 0)
        quality_part = smoothed(sum(shares[code], Fraction(0)), len(shares[code]))
        pr = sum(price_shares[code], Fraction(0)) / len(price_shares[code]) if price_shares[code] else NEUTRAL_PRICE
        want = (weights[0] * pr + weights[1] * delivery_part + weights[2] * quality_part) / sum(weights) * 100
        if abs(float(want) - float(sc.total)) > 0.06:
            off.append(f"{code}: platform {sc.total}, recomputed {float(want):.2f}")
    out.metrics["scores that differ from the independent recomputation"] = len(off)
    if off:
        out.find("high", f"{len(off)} score(s) differ from a recomputation from the raw files; first: {off[0]}")

    # ---- C. best-vendor picks are no worse than the cheapest pick, against what the suppliers are really like
    db.execute(text("insert into app_setting (key, value) values ('sourcing_mode', 'best_vendor') on conflict (key) do update set value = 'best_vendor'"))
    db.commit()
    picked, cheapest = [], []
    changed = compared = 0
    for (item, uom), prices in sorted(base.items()):
        if len(prices) < 2 or item not in {r["code"] for r in rows("master", "item") if r["stocked"] == "true"}:
            continue
        line = {"line_no": 1, "item_code": item, "name": item, "qty": D(1), "uom": uom}
        base_line = {k: line[k] for k in ("line_no", "item_code", "name", "qty", "uom")}
        choice = sourcing._best_vendor(db, line, as_of, base_line)  # noqa: SLF001 - the unit under test
        if choice is None or choice.supplier_code not in truth:
            continue
        cheap = min(prices, key=lambda s: (prices[s][1], s))
        if cheap not in truth:
            continue
        compared += 1
        changed += choice.supplier_code != cheap
        picked.append(float(truth[choice.supplier_code]["on_time_prob"]))
        cheapest.append(float(truth[cheap]["on_time_prob"]))
    mean_pick = sum(picked) / len(picked) if picked else 0.0
    mean_cheap = sum(cheapest) / len(cheapest) if cheapest else 0.0
    out.metrics["items with two or more suppliers priced"] = compared
    out.metrics["picks that differ from the cheapest"] = changed
    out.metrics["true on-time rate of the picks vs of the cheapest"] = f"{mean_pick:.3f} vs {mean_cheap:.3f}"
    if compared and mean_pick + 1e-9 < mean_cheap:
        out.find("high", f"the best vendors are less reliable than the cheapest ones ({mean_pick:.3f} against {mean_cheap:.3f})")
    if compared == 0:
        out.find("medium", "no item had two priced suppliers: the best-vendor comparison did not run")

    # ---- D. the repeat requests the dataset planted
    requests = sorted(history("request"), key=lambda r: (when(r["created_at"]), r["no"]))
    lines: dict[str, list[dict]] = defaultdict(list)
    for ln in history("request_line"):
        lines[ln["request_no"]].append(ln)
    planted: dict[str, str] = {}  # the later request -> the one it repeats
    for c in rows("truth", "truth_case"):
        if c["type"] == "duplicate_request":
            later, earlier = c["documents"].replace(";", ",").split(",")[:2]
            planted[later] = earlier
    flagged: dict[str, list] = {}
    for r in requests:
        # the check is made when a request is submitted: it is then a draft with open lines, whatever became of it later
        insert(db, "request", r, {"status": "draft"})
        for ln in lines[r["no"]]:
            insert(db, "request_line", ln, {"status": "open"})
        db.commit()
        found = duplicates.find_duplicates(db, r["no"])
        if found:
            flagged[r["no"]] = found
        db.execute(text("update request set status = :s where no = :n"), {"s": r["status"], "n": r["no"]})
        for ln in lines[r["no"]]:
            final = "ordered" if ln["status"] not in ("open", "sourcing", "cancelled") else ln["status"]
            db.execute(text("update request_line set status = :s where request_no = :n and line_no = :l"), {"s": final, "n": r["no"], "l": int(ln["line_no"])})
        db.commit()
    caught = sum(1 for later, earlier in planted.items() if later in flagged and earlier in {d.other_request for d in flagged[later]})
    others = [no for no in flagged if no not in planted]
    out.metrics["planted repeat requests / caught"] = f"{len(planted)} / {caught}"
    out.metrics["other requests flagged"] = f"{len(others)} of {len(requests) - len(planted)}"
    out.metrics["requests flagged in all"] = len(flagged)
    if planted and caught < len(planted):
        missed = [no for no, e in planted.items() if no in {r['no'] for r in requests} and not (no in flagged and e in {d.other_request for d in flagged[no]})]
        out.find("high", f"{len(planted) - caught} planted repeat request(s) were not flagged, e.g. {missed[:3]}")
    if requests and len(others) / max(len(requests) - len(planted), 1) > 0.25:
        out.find("medium", f"{len(others)} ordinary requests were flagged as repeats ({len(others) / (len(requests) - len(planted)):.0%}): the rule is too eager")
    return out
