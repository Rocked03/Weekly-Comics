import calendar
import datetime as dt

from objects.configuration import Schedule


def week_anchor(d: dt.date) -> dt.date:
    """Monday of the release week containing d."""
    return d - dt.timedelta(days=d.weekday())


def shift_months(d: dt.date, months: int) -> dt.date:
    """Shift a date back by whole calendar months, clamping the day to the target month."""
    month_index = d.month - 1 - months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return dt.date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def target_week(schedule: Schedule, on_date: dt.date) -> dt.date:
    """Week a feed should show: live week for PRINT, the print week that became digitally
    available this week for the delayed schedules (platform windows, week-snapped)."""
    if schedule is Schedule.DIGITAL_1M:
        return week_anchor(on_date - dt.timedelta(days=30))
    if schedule is Schedule.DIGITAL_3M:
        return week_anchor(shift_months(on_date, 3))
    if schedule is Schedule.DIGITAL_6M:
        return week_anchor(shift_months(on_date, 6))
    return week_anchor(on_date)
