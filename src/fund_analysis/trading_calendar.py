"""Trading calendar utilities for Chinese A-share, HK, and US stock markets.

Pure-function trading calendar calculations without external network dependencies.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Final

# Market calendar identifiers
CALENDAR_CN_STOCK: Final[str] = "CN_STOCK"
CALENDAR_HK_STOCK: Final[str] = "HK_STOCK"
CALENDAR_US_STOCK: Final[str] = "US_STOCK"
SUPPORTED_CALENDAR_YEARS: Final[frozenset[int]] = frozenset({2025, 2026})

# Known holiday closures (YYYY-MM-DD) for 2025-2026
CN_STOCK_HOLIDAYS: Final[frozenset[str]] = frozenset({
    # 2025
    "2025-01-01",
    "2025-01-28", "2025-01-29", "2025-01-30", "2025-01-31",
    "2025-02-03", "2025-02-04",
    "2025-04-04",
    "2025-05-01", "2025-05-02", "2025-05-05",
    "2025-06-02",
    "2025-10-01", "2025-10-02", "2025-10-03", "2025-10-06", "2025-10-07", "2025-10-08",
    # 2026
    "2026-01-01", "2026-01-02",
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-23",
    "2026-04-06",
    "2026-05-01", "2026-05-04", "2026-05-05",
    "2026-06-19",
    "2026-09-25",
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",
})

US_STOCK_HOLIDAYS: Final[frozenset[str]] = frozenset({
    # 2025
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
})

HK_STOCK_HOLIDAYS: Final[frozenset[str]] = frozenset({
    # 2025
    "2025-01-01", "2025-01-29", "2025-01-30", "2025-01-31",
    "2025-04-04", "2025-04-18", "2025-04-21", "2025-05-01", "2025-05-05",
    "2025-07-01", "2025-10-01", "2025-10-07", "2025-10-29", "2025-12-25", "2025-12-26",
    # 2026
    "2026-01-01", "2026-02-17", "2026-02-18", "2026-02-19",
    "2026-04-03", "2026-04-06", "2026-05-01", "2026-05-25",
    "2026-07-01", "2026-10-01", "2026-10-02", "2026-10-19", "2026-12-25", "2026-12-28",
})


def _parse_date(d: date | str) -> date:
    if isinstance(d, date):
        return d
    return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()


def is_trading_day(calendar: str, query_date: date | str) -> bool:
    """Check whether query_date is a trading day on the specified calendar."""
    d = _parse_date(query_date)
    cal = str(calendar or "").upper()
    if cal not in {CALENDAR_CN_STOCK, CALENDAR_HK_STOCK, CALENDAR_US_STOCK}:
        raise ValueError(f"unsupported trading calendar: {calendar}")
    if d.year not in SUPPORTED_CALENDAR_YEARS:
        raise ValueError(f"trading calendar year is not covered: {d.year}")

    # Weekends (Saturday=5, Sunday=6) are always non-trading
    if d.weekday() >= 5:
        return False

    date_str = d.strftime("%Y-%m-%d")

    if cal == CALENDAR_CN_STOCK:
        return date_str not in CN_STOCK_HOLIDAYS
    if cal == CALENDAR_US_STOCK:
        return date_str not in US_STOCK_HOLIDAYS
    if cal == CALENDAR_HK_STOCK:
        return date_str not in HK_STOCK_HOLIDAYS

    raise AssertionError(f"unreachable trading calendar: {cal}")


def get_previous_trading_day(calendar: str, query_date: date | str) -> date:
    """Return the nearest trading day strictly before query_date."""
    d = _parse_date(query_date) - timedelta(days=1)
    for _ in range(30):
        if is_trading_day(calendar, d):
            return d
        d -= timedelta(days=1)
    return d


def get_next_trading_day(calendar: str, query_date: date | str) -> date:
    """Return the nearest trading day strictly after query_date."""
    d = _parse_date(query_date) + timedelta(days=1)
    for _ in range(30):
        if is_trading_day(calendar, d):
            return d
        d += timedelta(days=1)
    return d


def get_latest_trading_day(
    calendar: str,
    as_of: date | str,
    include_today: bool = True,
) -> date:
    """Return the latest trading day on or before as_of (if include_today=True)."""
    d = _parse_date(as_of)
    if include_today and is_trading_day(calendar, d):
        return d
    return get_previous_trading_day(calendar, d)
