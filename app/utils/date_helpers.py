from datetime import datetime, timedelta, timezone

# CRM runs in IST — dashboards, campaign hours, and "today's tasks" all mean
# IST boundaries to the user. Keep this here so the rest of the codebase
# doesn't need to know about timezones.
IST = timezone(timedelta(hours=5, minutes=30))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_ist() -> datetime:
    return datetime.now(IST)


def add_business_days(start: datetime, days: int) -> datetime:
    """Advance ``start`` by ``days`` weekdays (skipping Sat/Sun).

    If ``days`` is negative, walks backwards. A ``days`` of 0 is a no-op even
    when ``start`` falls on a weekend — callers that want "next weekday from
    weekend" should pass ``days=1``.
    """
    if days == 0:
        return start
    step = 1 if days > 0 else -1
    remaining = abs(days)
    current = start
    while remaining > 0:
        current = current + timedelta(days=step)
        if current.weekday() < 5:  # Mon-Fri == 0..4
            remaining -= 1
    return current


def start_of_today(tz: timezone = IST) -> datetime:
    """Start of the current day in ``tz`` (default IST)."""
    now = datetime.now(tz)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def end_of_today(tz: timezone = IST) -> datetime:
    """End of the current day in ``tz`` (default IST)."""
    now = datetime.now(tz)
    return now.replace(hour=23, minute=59, second=59, microsecond=999999)
