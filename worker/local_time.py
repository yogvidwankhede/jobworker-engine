"""Candidate-local wall time, independent of the cloud runner's system timezone."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

TIMEZONE = ZoneInfo("America/Chicago")


def now() -> datetime:
    # Tracker timestamps historically use local wall time without an offset.
    return datetime.now(TIMEZONE).replace(tzinfo=None)


def today() -> date:
    return now().date()
