"""Business calendar: working days, holidays and working hours. Used for due times and SLAs (FR41)."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from p2p.formulas.money import to_decimal

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class BusinessCalendar:
    holidays: frozenset[date] = field(default_factory=frozenset)
    weekly_off: frozenset[int] = frozenset({5, 6})  # Monday is 0; the default is Saturday and Sunday off
    day_start: time = time(9, 0)
    day_end: time = time(18, 0)

    @classmethod
    def of(cls, holidays: Iterable[date] = (), *, saturday_working: bool = False) -> "BusinessCalendar":
        return cls(frozenset(holidays), frozenset({6}) if saturday_working else frozenset({5, 6}))

    def is_working_day(self, d: date) -> bool:
        return d.weekday() not in self.weekly_off and d not in self.holidays

    def next_working_day(self, d: date) -> date:
        """The first working day strictly after d."""
        d += timedelta(days=1)
        while not self.is_working_day(d):
            d += timedelta(days=1)
        return d

    def on_or_after(self, d: date) -> date:
        return d if self.is_working_day(d) else self.next_working_day(d)

    def add_working_days(self, d: date, n: int) -> date:
        for _ in range(n):
            d = self.next_working_day(d)
        return d

    def add_business_hours(self, start: datetime, hours: Decimal | int | str) -> datetime:
        """Move forward by working hours only. Time outside working hours or on a holiday does not count."""
        remaining = to_decimal(hours)
        if remaining < 0:
            raise ValueError("hours must not be negative")
        if start.tzinfo is None:
            raise ValueError("start must carry a time zone")
        t = self._into_working_time(start.astimezone(IST))
        while remaining > 0:
            end_of_day = datetime.combine(t.date(), self.day_end, tzinfo=IST)
            available = Decimal((end_of_day - t).total_seconds()) / Decimal(3600)
            if remaining <= available:
                return t + timedelta(seconds=float(remaining * 3600))
            remaining -= available
            t = self._into_working_time(end_of_day)
        return t

    def _into_working_time(self, t: datetime) -> datetime:
        day_start = datetime.combine(t.date(), self.day_start, tzinfo=IST)
        day_end = datetime.combine(t.date(), self.day_end, tzinfo=IST)
        if not self.is_working_day(t.date()) or t >= day_end:
            return datetime.combine(self.next_working_day(t.date()), self.day_start, tzinfo=IST)
        return max(t, day_start)


def sla_due(assigned: datetime, sla_hours: Decimal | int | str, calendar: BusinessCalendar) -> datetime:
    """FR41: due time = assigned time + SLA hours counted on the business calendar."""
    return calendar.add_business_hours(assigned, sla_hours)
