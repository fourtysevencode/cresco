from datetime import UTC, date, datetime, time, timedelta

from app.core.config import get_settings


def now() -> datetime:
    return datetime.now(UTC)


def local_day_start(at: datetime | None = None) -> datetime:
    """Midnight (school timezone) of the day containing `at`, as an aware datetime."""
    tz = get_settings().tz
    local = (at or now()).astimezone(tz)
    return datetime.combine(local.date(), time.min, tzinfo=tz)


def iso_week(at: datetime | None = None) -> str:
    year, week, _ = (at or now()).astimezone(get_settings().tz).isocalendar()
    return f"{year}-W{week:02d}"


def previous_iso_week(week: str) -> str:
    year, wk = week.split("-W")
    monday = date.fromisocalendar(int(year), int(wk), 1) - timedelta(days=7)
    y, w, _ = monday.isocalendar()
    return f"{y}-W{w:02d}"


def parse_iso_week(week: str) -> str:
    """Validates and normalises 'YYYY-Www'. Raises ValueError."""
    year, wk = week.split("-W")
    date.fromisocalendar(int(year), int(wk), 1)
    return f"{int(year)}-W{int(wk):02d}"
