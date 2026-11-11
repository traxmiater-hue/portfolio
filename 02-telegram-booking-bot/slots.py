"""Расчёт свободного времени. Без Telegram — легко тестировать."""
from datetime import date, datetime, time, timedelta

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def free_slots(day: date, minutes: int, busy: list[tuple[datetime, datetime]], now: datetime,
               open_t: time, close_t: time, step: int, min_lead: int) -> list[datetime]:
    """Начала всех слотов длиной `minutes`, которые не пересекаются с `busy`."""
    result = []
    t = datetime.combine(day, open_t)
    day_end = datetime.combine(day, close_t)
    earliest = now + timedelta(minutes=min_lead)
    length = timedelta(minutes=minutes)
    while t + length <= day_end:
        end = t + length
        if t >= earliest and all(end <= b0 or t >= b1 for b0, b1 in busy):
            result.append(t)
        t += timedelta(minutes=step)
    return result


def booking_days(today: date, days_ahead: int, days_off: set[int]) -> list[date]:
    days = (today + timedelta(days=i) for i in range(days_ahead + 1))
    return [d for d in days if d.weekday() not in days_off]


def fmt_day(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]} {d:%d.%m}"


def fmt_dt(dt: datetime) -> str:
    return f"{fmt_day(dt.date())} в {dt:%H:%M}"
