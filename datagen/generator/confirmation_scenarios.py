"""The supplier confirmation scenarios of block 8 (task 1.3 acknowledgement code, 11b.3): `confirmation_scenarios:` of a
company profile, and every figure and date the scenarios must show, worked out here from the profile and the holiday
files alone (never from the backend).

File written: truth/truth_supplier_confirmation.csv, rows `scenario,figure,value`.

The rules, as the block 8 spec says them:
- an order line: taxable = qty x price; a supplier in the delivery place's state charges CGST = SGST = taxable x half
  the GST rate, each rounded half up to paise; total = taxable + tax;
- working days are those of the delivery place (its week and the central and its state's holidays);
- an acknowledgement falls due `supplier_ack_due_days` working days after the version was emailed and is overdue the
  day after; chasers go on working days, the first when overdue, then every `supplier_chase_days` working days, at most
  `supplier_chase_max` per version; on the day the next would be due after the last, the buyer gets a task;
- a date given when accepting is refused when in the past, on a day the place does not work, or more than
  `supplier_date_days` calendar days after the date ordered;
- a delivery reminder goes `delivery_reminder_before_days` working days before a line's due date; when nothing has
  arrived, `delivery_overdue_after_days` working days after it, then every `delivery_overdue_every_days`;
- supplier scoring counts a version acknowledged, or whose due day passed before a newer version replaced it.
"""

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from common import Calendar
from master_files import write_csv

CENT = Decimal("0.01")
TRUTH = ["scenario", "figure", "value"]


def _paise(value: Decimal) -> Decimal:
    return value.quantize(CENT, ROUND_HALF_UP)


def _setting(data: dict[str, Any], key: str) -> int:
    return int(next(v for k, v, *_ in data["settings"] if k == key))


def _tolerance(data: dict[str, Any], key: str) -> int:
    latest = data["tolerances"][-1]
    for version in reversed(data["tolerances"]):
        for row in version.get("values", []):
            if row[0] == key:
                return int(row[1])
    raise KeyError(f"{key} not in tolerances (version {latest.get('version')})")


def _order(lines: list[dict[str, Any]]) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    taxable = cgst = Decimal(0)
    for n, ln in enumerate(lines, start=1):
        amount = _paise(Decimal(ln["qty"]) * Decimal(ln["price"]))
        half = _paise(amount * Decimal(ln["gst"]) / 2 / 100)
        rows += [(f"line_{n}_taxable", str(amount)), (f"line_{n}_cgst", str(half)), (f"line_{n}_sgst", str(half))]
        taxable, cgst = taxable + amount, cgst + half
    rows += [("taxable", str(taxable)), ("cgst", str(cgst)), ("sgst", str(cgst)), ("total", str(taxable + 2 * cgst))]
    return rows


def _working(cal: Calendar, d: date, loc: str) -> bool:
    return bool(cal.is_working(d, loc))


def _add(cal: Calendar, d: date, n: int, loc: str) -> date:
    return cal.add_working(d, n, loc)  # type: ignore[no-any-return]


def _chasers(
    cal: Calendar, loc: str, sent: date, until: date, data: dict[str, Any]
) -> tuple[date, list[date], date | None]:
    """The due day, the chaser days and the day of the task after the last, from sending up to `until`."""
    due = _add(cal, sent, _setting(data, "supplier_ack_due_days"), loc)
    every, most = _setting(data, "supplier_chase_days"), _setting(data, "supplier_chase_max")
    chasers: list[date] = []
    task = None
    day = due + timedelta(days=1)
    while day <= until and task is None:
        if _working(cal, day, loc) and (not chasers or day >= _add(cal, chasers[-1], every, loc)):
            if len(chasers) < most:
                chasers.append(day)
            else:
                task = day
        day += timedelta(days=1)
    return due, chasers, task


def rows_of(sc: dict[str, Any], data: dict[str, Any], cal: Calendar) -> list[dict[str, str]]:
    loc = sc["location"]
    out: list[tuple[str, str, str]] = []
    lines = sc["lines"]
    out += [("P", f, v) for f, v in _order(lines)]

    # K3: version 2 cuts a line's quantity; scoring counts v2 and leaves v1 out
    k3 = sc["k3"]
    revised = [dict(ln) for ln in lines]
    revised[int(k3["line"]) - 1]["qty"] = k3["qty"]
    out += [("K3", f"v2_{f}", v) for f, v in _order(revised)]
    ack_days = _setting(data, "supplier_ack_due_days")
    sent1, sent2 = date.fromisoformat(sc["sent_on"]), date.fromisoformat(k3["revised_on"])
    due1, due2 = _add(cal, sent1, ack_days, loc), _add(cal, sent2, ack_days, loc)
    acked = date.fromisoformat(k3["acknowledged_on"])
    out += [
        ("K3", "v1_ack_due", due1.isoformat()), ("K3", "v1_counted", str(sent2 > due1).lower()),
        ("K3", "v2_ack_due", due2.isoformat()), ("K3", "v2_counted", "true"),
        ("K3", "v2_on_time", str(acked <= due2).lower()),
    ]  # fmt: skip

    # K4: a date per line when accepting; the refusals; reminders per line
    k4 = sc["k4"]
    today = date.fromisoformat(k4["today"])
    slip = _tolerance(data, "supplier_date_days")
    for line_no, given in k4["tries"]:
        day = date.fromisoformat(given)
        ordered = date.fromisoformat(lines[int(line_no) - 1]["need_by"])
        if day < today:
            why = "past"
        elif not _working(cal, day, loc):
            why = "not_working_day"
        elif (day - ordered).days > slip:
            why = f"later_by_{(day - ordered).days}"
        else:
            why = "accepted"
        out.append(("K4", f"try_line_{line_no}_{given}", why))
    promised = {n: date.fromisoformat(ln["need_by"]) for n, ln in enumerate(lines, start=1)}
    promised.update({int(n): date.fromisoformat(d) for n, d in k4["dates"].items()})
    before = _setting(data, "delivery_reminder_before_days")
    after, every_late = _setting(data, "delivery_overdue_after_days"), _setting(data, "delivery_overdue_every_days")
    most_late = _setting(data, "delivery_overdue_max")
    for n, day in sorted(promised.items()):
        ordered = date.fromisoformat(lines[n - 1]["need_by"])
        out += [
            ("K4", f"line_{n}_promised", day.isoformat()),
            ("K4", f"line_{n}_days_later", str((day - ordered).days)),
            ("K4", f"line_{n}_before_reminder", _add(cal, day, -before, loc).isoformat()),
        ]
        out += [
            ("K4", f"line_{n}_overdue_reminder_{k}", _add(cal, day, after + (k - 1) * every_late, loc).isoformat())
            for k in range(1, most_late + 1)
        ]

    # K7: never acknowledged; K7b: revised on a day, the clock and the count start again
    k7 = sc["k7"]
    due, chasers, task = _chasers(cal, loc, date.fromisoformat(k7["sent_on"]), date.fromisoformat(k7["until"]), data)
    out += [("K7", "ack_due", due.isoformat()), ("K7", "overdue_from", (due + timedelta(days=1)).isoformat())]
    out += [("K7", f"chaser_{k}", d.isoformat()) for k, d in enumerate(chasers, start=1)]
    out.append(("K7", "overdue_task", task.isoformat() if task else ""))
    revised_on = date.fromisoformat(sc["k7b"]["revised_on"])
    due_b, chasers_b, _ = _chasers(cal, loc, revised_on, date.fromisoformat(k7["until"]), data)
    out += [
        ("K7b", "v1_chasers", str(sum(1 for d in chasers if d < revised_on))),
        ("K7b", "v2_ack_due", due_b.isoformat()),
    ]
    out += [("K7b", f"v2_chaser_{k}", d.isoformat()) for k, d in enumerate(chasers_b, start=1)]

    # K9: late lines on a day (a line received in full is not late); Q: a line with only its need-by date
    k9 = sc["k9"]
    on = date.fromisoformat(k9["today"])
    late = {n: (on - d).days for n, d in promised.items() if d < on and n not in k9["received_lines"]}
    out += [("K9", f"line_{n}_days_late", str(days)) for n, days in sorted(late.items())]
    out.append(("K9", "days_late", str(max(late.values()) if late else 0)))
    q = sc["q"]
    out.append(("Q", "days_late", str((date.fromisoformat(q["today"]) - date.fromisoformat(q["need_by"])).days)))
    return [{"scenario": a, "figure": b, "value": c} for a, b, c in out]


def write_confirmation(data: dict[str, Any], out: Path) -> dict[str, int]:
    """truth/truth_supplier_confirmation.csv from the profile's `confirmation_scenarios:` (nothing when it has none)."""
    sc = data.get("confirmation_scenarios")
    if not sc:
        return {}
    cal = Calendar([data["location"], *data.get("locations", [])])
    rows = rows_of(sc, data, cal)
    (out / "truth").mkdir(parents=True, exist_ok=True)
    write_csv(out / "truth" / "truth_supplier_confirmation.csv", TRUTH, rows)
    return {"supplier_confirmation_figures": len(rows)}


def check(out: Path) -> list[str]:
    """The orders add up: each line's tax is half the rate twice, the total the sum."""
    path = out / "truth" / "truth_supplier_confirmation.csv"
    if not path.exists():
        return []
    import csv  # noqa: PLC0415

    figures: dict[tuple[str, str], str] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            figures[(r["scenario"], r["figure"])] = r["value"]
    problems = []
    for name, prefix in (("P", ""), ("K3", "v2_")):
        taxable, cgst, sgst, total = (
            Decimal(figures[(name, prefix + k)]) for k in ("taxable", "cgst", "sgst", "total")
        )
        if cgst != sgst or taxable + cgst + sgst != total:
            problems.append(f"truth_supplier_confirmation {name}: the order does not add up")
    return problems
