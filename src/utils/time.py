"""Time helpers."""

from datetime import datetime, timezone


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def datetime_to_utc_iso(value: datetime | None) -> str | None:
    """Return an ISO-8601 UTC string for a datetime value."""
    if value is None:
        return None

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)

    return value.isoformat()
