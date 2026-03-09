"""Utility functions and test-only constants.

Reference data (providers, locations, procedures, insurance) lives in SQLite.
See db.py for schema and seed data.
"""

from datetime import datetime, timedelta


def generate_availability_slots(date_start: str, date_end: str) -> list[dict]:
    """Generate realistic availability slots within a date range."""
    from datetime import date as date_type

    slots = []
    try:
        raw_start = datetime.fromisoformat(date_start)
        raw_end = datetime.fromisoformat(date_end)
    except (ValueError, TypeError):
        return slots

    if isinstance(raw_start, date_type) and not isinstance(raw_start, datetime):
        raw_start = datetime(raw_start.year, raw_start.month, raw_start.day, 0, 0)
    if isinstance(raw_end, date_type) and not isinstance(raw_end, datetime):
        raw_end = datetime(raw_end.year, raw_end.month, raw_end.day, 23, 59)

    if raw_end.hour == 0 and raw_end.minute == 0:
        raw_end = raw_end.replace(hour=23, minute=59)

    current = raw_start.replace(hour=0, minute=0, second=0, microsecond=0)
    while current.date() <= raw_end.date():
        if current.weekday() < 5:  # weekdays only
            for hour in [9, 10, 11, 14, 15, 16]:
                slot_start = current.replace(hour=hour)
                slot_end = slot_start + timedelta(hours=1)
                slots.append({
                    "start": slot_start.isoformat(),
                    "end": slot_end.isoformat(),
                })
        current += timedelta(days=1)

    return slots
