"""
Calendar utilities for week alignment.
"""

from __future__ import annotations

from datetime import date, timedelta


def get_week_start(d: date, week_start: int = 0) -> date:
    """
    Get the week-start date for a given date.
    
    Args:
        d: Input date
        week_start: Day of week for week start (0=Monday, 6=Sunday)
    
    Returns:
        Date of the week start
    """
    days_since_week_start = (d.weekday() - week_start) % 7
    return d - timedelta(days=days_since_week_start)


def get_week_end(d: date, week_start: int = 0) -> date:
    """Get the week-end date for a given date."""
    start = get_week_start(d, week_start)
    return start + timedelta(days=6)


def generate_week_range(start_date: date, num_weeks: int, week_start: int = 0) -> list[date]:
    """
    Generate a list of week-start dates.
    
    Args:
        start_date: Starting date (will be aligned to week start)
        num_weeks: Number of weeks to generate
        week_start: Day of week for week start
    
    Returns:
        List of week-start dates
    """
    first_week = get_week_start(start_date, week_start)
    return [first_week + timedelta(weeks=i) for i in range(num_weeks)]


def date_to_week_str(d: date) -> str:
    """Convert date to ISO week string (YYYY-Www)."""
    iso_calendar = d.isocalendar()
    return f"{iso_calendar[0]}-W{iso_calendar[1]:02d}"


def align_date_to_week(d: date | str, week_start: int = 0) -> date:
    """
    Align a date (or date string) to its week start.
    """
    if isinstance(d, str):
        d = date.fromisoformat(d)
    return get_week_start(d, week_start)
