"""Delivery dates of order lines and when a supplier is reminded about them, on the business calendar of the place the
goods go to. Plain date rules, no database.

* A line's due date is the date the supplier promised, else the date it is needed by (`line_due`).
* A date the supplier gives for a line when accepting (`check_promised`) may not be in the past, must be a working day
  where the goods are delivered (OP-7), and may be at most 'supplier_date_days' calendar days after the date ordered;
  anything later is a change the buyer decides (OP-6).
* Before: on the working day that is 'delivery_reminder_before_days' working days before the promised date (a weekend or
  holiday in between does not count), or on any later day up to the promised date if the job did not run that day.
* Overdue: when nothing has arrived, first 'delivery_overdue_after_days' working days after the promised date, then every
  'delivery_overdue_every_days' working days, at most 'delivery_overdue_max' times.
"""

from datetime import date, timedelta

from p2p.formulas.calendar import BusinessCalendar
from p2p.formulas.words import count


def line_due(promised: date | None, need_by: date | None) -> tuple[date | None, str | None]:
    """A line's due date and where it comes from ('promised' or 'need_by')."""
    if promised is not None:
        return promised, "promised"
    if need_by is not None:
        return need_by, "need_by"
    return None, None


def check_promised(
    line_no: int,
    ordered: date | None,
    promised: date,
    tolerance_days: int,
    today: date,
    calendar: BusinessCalendar,
    place: str,
    *,
    closed_ok: bool = False,
) -> str | None:
    """Why a date the supplier gives for a line cannot be accepted, in words; None when it can. With `closed_ok` a day
    the place is closed is let through (the buyer records what the supplier said, and is told of it)."""
    if promised < today:
        return f"line {line_no}: {promised:%d %b %Y} is in the past"
    if not closed_ok and not calendar.is_working_day(promised):
        return f"line {line_no}: {promised:%d %b %Y} is not a working day at {place}"
    if ordered is not None and (promised - ordered).days > tolerance_days:
        return (
            f"line {line_no}: {promised:%d %b %Y} is {count((promised - ordered).days, 'day')} after the date ordered "
            f"({ordered:%d %b %Y}), more than the {tolerance_days} accepted: ask for a change instead"
        )
    return None


def working_days_before(day: date, n: int, calendar: BusinessCalendar) -> date:
    """The nth working day counting back from `day`, the day itself not counted (n = 0 is the day itself)."""
    for _ in range(n):
        day -= timedelta(days=1)
        while not calendar.is_working_day(day):
            day -= timedelta(days=1)
    return day


def before_reminder_due(promised: date, days_before: int, today: date, calendar: BusinessCalendar) -> bool:
    return working_days_before(promised, days_before, calendar) <= today <= promised


def overdue_reminder_on(
    promised: date, number: int, after_days: int, every_days: int, calendar: BusinessCalendar
) -> date:
    """The day the nth (from 1) reminder about a missed date is due."""
    return calendar.add_working_days(promised, after_days + (number - 1) * every_days)


def overdue_reminder_due(
    promised: date, sent: int, after_days: int, every_days: int, most: int, today: date, calendar: BusinessCalendar
) -> bool:
    """Whether the next reminder (after `sent` of them) is due today."""
    if sent >= most:
        return False
    return today >= overdue_reminder_on(promised, sent + 1, after_days, every_days, calendar)
