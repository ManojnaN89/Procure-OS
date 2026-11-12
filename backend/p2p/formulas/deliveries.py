"""When a supplier is reminded about a delivery date, on the business calendar. Plain date rules, no database.

* Before: on the working day that is 'delivery_reminder_before_days' working days before the promised date (a weekend or
  holiday in between does not count), or on any later day up to the promised date if the job did not run that day.
* Overdue: when nothing has arrived, first 'delivery_overdue_after_days' working days after the promised date, then every
  'delivery_overdue_every_days' working days, at most 'delivery_overdue_max' times.
"""

from datetime import date, timedelta

from p2p.formulas.calendar import BusinessCalendar


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
