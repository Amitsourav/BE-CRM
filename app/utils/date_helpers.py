from datetime import datetime, timedelta, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def add_business_days(start: datetime, days: int) -> datetime:
    return start + timedelta(days=days)


def end_of_today() -> datetime:
    now = now_utc()
    return now.replace(hour=23, minute=59, second=59, microsecond=999999)


def start_of_today() -> datetime:
    now = now_utc()
    return now.replace(hour=0, minute=0, second=0, microsecond=0)
