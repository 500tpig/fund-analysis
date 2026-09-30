"""Tests for trading_calendar module."""

import unittest
from datetime import date

from fund_analysis.trading_calendar import (
    CALENDAR_CN_STOCK,
    CALENDAR_HK_STOCK,
    CALENDAR_US_STOCK,
    get_latest_trading_day,
    get_next_trading_day,
    get_previous_trading_day,
    is_trading_day,
)


class TestTradingCalendar(unittest.TestCase):
    def test_weekends_are_non_trading(self) -> None:
        # 2026-09-05 is Saturday, 2026-09-06 is Sunday
        self.assertFalse(is_trading_day(CALENDAR_CN_STOCK, date(2026, 9, 5)))
        self.assertFalse(is_trading_day(CALENDAR_CN_STOCK, date(2026, 9, 6)))
        self.assertFalse(is_trading_day(CALENDAR_US_STOCK, date(2026, 9, 5)))
        self.assertFalse(is_trading_day(CALENDAR_HK_STOCK, date(2026, 9, 6)))

    def test_regular_weekdays_are_trading(self) -> None:
        # 2026-09-02 is Wednesday
        self.assertTrue(is_trading_day(CALENDAR_CN_STOCK, date(2026, 9, 2)))
        self.assertTrue(is_trading_day(CALENDAR_US_STOCK, date(2026, 9, 2)))
        self.assertTrue(is_trading_day(CALENDAR_HK_STOCK, date(2026, 9, 2)))

    def test_known_holidays(self) -> None:
        # 2026-01-01 New Year (CN/US/HK)
        self.assertFalse(is_trading_day(CALENDAR_CN_STOCK, date(2026, 1, 1)))
        self.assertFalse(is_trading_day(CALENDAR_US_STOCK, date(2026, 1, 1)))
        self.assertFalse(is_trading_day(CALENDAR_HK_STOCK, date(2026, 1, 1)))

        # 2026-10-01 National Day (CN holiday, but US is normal trading)
        self.assertFalse(is_trading_day(CALENDAR_CN_STOCK, date(2026, 10, 1)))
        self.assertTrue(is_trading_day(CALENDAR_US_STOCK, date(2026, 10, 1)))

    def test_get_previous_and_next_trading_day(self) -> None:
        # Monday 2026-09-07 previous trading day is Friday 2026-09-04
        prev_day = get_previous_trading_day(CALENDAR_CN_STOCK, date(2026, 9, 7))
        self.assertEqual(prev_day, date(2026, 9, 4))

        # Friday 2026-09-04 next trading day is Monday 2026-09-07
        next_day = get_next_trading_day(CALENDAR_CN_STOCK, date(2026, 9, 4))
        self.assertEqual(next_day, date(2026, 9, 7))

    def test_get_latest_trading_day(self) -> None:
        # On Sunday 2026-09-06, latest trading day is Friday 2026-09-04
        latest = get_latest_trading_day(CALENDAR_CN_STOCK, date(2026, 9, 6))
        self.assertEqual(latest, date(2026, 9, 4))

        # On Wednesday 2026-09-02, latest trading day with include_today=True is 2026-09-02
        latest_include = get_latest_trading_day(CALENDAR_CN_STOCK, date(2026, 9, 2), include_today=True)
        self.assertEqual(latest_include, date(2026, 9, 2))

        # With include_today=False, it is 2026-09-01
        latest_exclude = get_latest_trading_day(CALENDAR_CN_STOCK, date(2026, 9, 2), include_today=False)
        self.assertEqual(latest_exclude, date(2026, 9, 1))

    def test_rejects_unknown_calendar_and_uncovered_year(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported trading calendar"):
            is_trading_day("UNKNOWN", date(2026, 9, 2))
        with self.assertRaisesRegex(ValueError, "year is not covered"):
            is_trading_day(CALENDAR_US_STOCK, date(2027, 1, 1))


if __name__ == "__main__":
    unittest.main()
