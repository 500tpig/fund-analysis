"""Tests for nav_status resolver module."""

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from fund_analysis.fund_metadata import INITIAL_FUND_METADATA
from fund_analysis.nav_status import (
    STATUS_MARKET_CLOSED,
    STATUS_OVERDUE,
    STATUS_PENDING,
    STATUS_SOURCE_FAILED,
    STATUS_UNKNOWN_RULE,
    STATUS_UPDATED,
    resolve_nav_status,
)

BJT = ZoneInfo("Asia/Shanghai")


class TestNavStatus(unittest.TestCase):
    def setUp(self) -> None:
        self.cn_fund = INITIAL_FUND_METADATA["024481"]
        self.qdii_fund = INITIAL_FUND_METADATA["016702"]
        self.cash = INITIAL_FUND_METADATA["CASH"]

    def _assert_refresh_flags(
        self,
        result,
        *,
        in_disclosure_window: bool,
        auto_refresh_eligible: bool,
    ) -> None:
        self.assertEqual(result.in_disclosure_window, in_disclosure_window)
        self.assertEqual(result.auto_refresh_eligible, auto_refresh_eligible)

    def test_source_failed(self) -> None:
        result = resolve_nav_status(
            metadata=self.cn_fund,
            nav_date="2026-09-02",
            snapshot_failed=True,
            snapshot_error="Connection timeout to upstream server",
        )
        self.assertEqual(result.status, STATUS_SOURCE_FAILED)
        self.assertIn("Connection timeout", result.reason)
        self._assert_refresh_flags(
            result,
            in_disclosure_window=False,
            auto_refresh_eligible=True,
        )

    def test_cash_asset_always_updated(self) -> None:
        result = resolve_nav_status(
            metadata=self.cash,
            nav_date=None,
            is_cash=True,
        )
        self.assertEqual(result.status, STATUS_UPDATED)
        self._assert_refresh_flags(
            result,
            in_disclosure_window=False,
            auto_refresh_eligible=False,
        )

    def test_unknown_rule_or_missing_metadata(self) -> None:
        result = resolve_nav_status(
            metadata=None,
            nav_date="2026-09-01",
        )
        self.assertEqual(result.status, STATUS_UNKNOWN_RULE)
        self._assert_refresh_flags(
            result,
            in_disclosure_window=False,
            auto_refresh_eligible=False,
        )

    def test_unverified_qdii_schedule_stays_unknown_across_t_plus_1_night(self) -> None:
        for when in (
            datetime(2026, 10, 29, 20, 30, tzinfo=BJT),
            datetime(2026, 10, 30, 10, 0, tzinfo=BJT),
        ):
            with self.subTest(as_of=when):
                result = resolve_nav_status(
                    metadata=INITIAL_FUND_METADATA["008401"],
                    nav_date="2026-10-27",
                    as_of=when,
                )
                self.assertEqual(result.status, STATUS_UNKNOWN_RULE)
                self.assertIsNone(result.next_disclosure_date)
                self._assert_refresh_flags(
                    result,
                    in_disclosure_window=False,
                    auto_refresh_eligible=False,
                )

    def test_cn_fund_updated_when_target_reached(self) -> None:
        # Wednesday 2026-09-02 21:00, nav_date is 2026-09-02
        dt = datetime(2026, 9, 2, 21, 0, tzinfo=BJT)
        result = resolve_nav_status(
            metadata=self.cn_fund,
            nav_date="2026-09-02",
            as_of=dt,
        )
        self.assertEqual(result.status, STATUS_UPDATED)
        self.assertEqual(result.target_nav_date, "2026-09-02")
        self._assert_refresh_flags(
            result,
            in_disclosure_window=False,
            auto_refresh_eligible=False,
        )

    def test_cn_fund_pending_during_trading_day_and_window(self) -> None:
        # Wednesday 2026-09-02 14:30 (afternoon trading), nav_date is 2026-09-01 (prev day)
        dt_day = datetime(2026, 9, 2, 14, 30, tzinfo=BJT)
        res_day = resolve_nav_status(
            metadata=self.cn_fund,
            nav_date="2026-09-01",
            as_of=dt_day,
        )
        self.assertEqual(res_day.status, STATUS_PENDING)
        self._assert_refresh_flags(
            res_day,
            in_disclosure_window=False,
            auto_refresh_eligible=False,
        )

        # Wednesday 2026-09-02 20:30 (night disclosure window), nav_date is 2026-09-01
        dt_night = datetime(2026, 9, 2, 20, 30, tzinfo=BJT)
        res_night = resolve_nav_status(
            metadata=self.cn_fund,
            nav_date="2026-09-01",
            as_of=dt_night,
        )
        self.assertEqual(res_night.status, STATUS_PENDING)
        self._assert_refresh_flags(
            res_night,
            in_disclosure_window=True,
            auto_refresh_eligible=True,
        )

    def test_cn_fund_overdue_when_lagged_behind_previous_trading_day(self) -> None:
        # Wednesday 2026-09-02, nav_date is 2026-08-28 (missed Monday & Tuesday)
        dt = datetime(2026, 9, 2, 14, 30, tzinfo=BJT)
        res = resolve_nav_status(
            metadata=self.cn_fund,
            nav_date="2026-08-28",
            as_of=dt,
        )
        self.assertEqual(res.status, STATUS_OVERDUE)
        self._assert_refresh_flags(
            res,
            in_disclosure_window=False,
            auto_refresh_eligible=True,
        )

    def test_cn_fund_market_closed_on_weekend(self) -> None:
        # Saturday 2026-09-05, nav_date is Friday 2026-09-04
        dt = datetime(2026, 9, 5, 10, 0, tzinfo=BJT)
        res = resolve_nav_status(
            metadata=self.cn_fund,
            nav_date="2026-09-04",
            as_of=dt,
        )
        self.assertEqual(res.status, STATUS_MARKET_CLOSED)
        self.assertEqual(res.target_nav_date, "2026-09-04")
        self._assert_refresh_flags(
            res,
            in_disclosure_window=False,
            auto_refresh_eligible=False,
        )

    def test_qdii_fund_not_marked_overdue_on_normal_t_plus_1_lag(self) -> None:
        # Wednesday 2026-09-02 11:00 Beijing time.
        # US traded on Tuesday 2026-09-01. China T+1 is Wednesday 2026-09-02.
        # Official NAV is 2026-08-31 (Monday). This is NORMAL disclosure lag.
        dt = datetime(2026, 9, 2, 11, 0, tzinfo=BJT)
        res = resolve_nav_status(
            metadata=self.qdii_fund,
            nav_date="2026-08-31",
            as_of=dt,
        )
        self.assertEqual(res.status, STATUS_PENDING)
        self.assertEqual(res.target_nav_date, "2026-09-01")
        self._assert_refresh_flags(
            res,
            in_disclosure_window=False,
            auto_refresh_eligible=False,
        )

    def test_qdii_fund_pending_in_disclosure_window(self) -> None:
        dt = datetime(2026, 9, 2, 20, 30, tzinfo=BJT)
        res = resolve_nav_status(
            metadata=self.qdii_fund,
            nav_date="2026-08-31",
            as_of=dt,
        )
        self.assertEqual(res.status, STATUS_PENDING)
        self.assertEqual(res.target_nav_date, "2026-09-01")
        self._assert_refresh_flags(
            res,
            in_disclosure_window=True,
            auto_refresh_eligible=True,
        )

    def test_qdii_fund_updated_when_latest_us_trading_day_disclosed(self) -> None:
        # Wednesday 2026-09-02 22:00 Beijing time.
        # Official NAV of 2026-09-01 is published.
        dt = datetime(2026, 9, 2, 22, 0, tzinfo=BJT)
        res = resolve_nav_status(
            metadata=self.qdii_fund,
            nav_date="2026-09-01",
            as_of=dt,
        )
        self.assertEqual(res.status, STATUS_UPDATED)
        self._assert_refresh_flags(
            res,
            in_disclosure_window=False,
            auto_refresh_eligible=False,
        )

    def test_qdii_fund_remains_pending_on_weekend_until_latest_nav_arrives(self) -> None:
        dt = datetime(2026, 9, 5, 10, 0, tzinfo=BJT)
        res = resolve_nav_status(
            metadata=self.qdii_fund,
            nav_date="2026-09-03",
            as_of=dt,
        )
        self.assertEqual(res.status, STATUS_PENDING)
        self.assertEqual(res.target_nav_date, "2026-09-04")
        self._assert_refresh_flags(
            res,
            in_disclosure_window=False,
            auto_refresh_eligible=False,
        )

    def test_qdii_fund_enters_window_on_next_workday_evening(self) -> None:
        res = resolve_nav_status(
            metadata=self.qdii_fund,
            nav_date="2026-09-03",
            as_of=datetime(2026, 9, 7, 20, 30, tzinfo=BJT),
        )
        self.assertEqual(res.status, STATUS_PENDING)
        self.assertEqual(res.target_nav_date, "2026-09-04")
        self._assert_refresh_flags(
            res,
            in_disclosure_window=True,
            auto_refresh_eligible=True,
        )

    def test_uncovered_calendar_year_returns_unknown_rule(self) -> None:
        res = resolve_nav_status(
            metadata=self.cn_fund,
            nav_date="2026-12-31",
            as_of=datetime(2027, 1, 1, 10, 0, tzinfo=BJT),
        )
        self.assertEqual(res.status, STATUS_UNKNOWN_RULE)
        self._assert_refresh_flags(
            res,
            in_disclosure_window=False,
            auto_refresh_eligible=False,
        )

    def _qdii(self, nav_date: str, when: datetime):
        return resolve_nav_status(metadata=self.qdii_fund, nav_date=nav_date, as_of=when)

    def test_qdii_cn_holiday_with_us_open_is_disclosure_state_not_market_closed(self) -> None:
        # 2026-09-25 国内中秋休市、美股正常交易。
        res = self._qdii("2026-09-24", datetime(2026, 9, 25, 15, 0, tzinfo=BJT))
        self.assertEqual(res.status, STATUS_UPDATED)
        self.assertEqual(res.target_nav_date, "2026-09-24")
        self.assertNotIn("休市，最新", res.reason)

        waiting = self._qdii("2026-09-23", datetime(2026, 9, 25, 15, 0, tzinfo=BJT))
        self.assertEqual(waiting.status, STATUS_PENDING)
        self.assertEqual(waiting.target_nav_date, "2026-09-24")
        self.assertEqual(waiting.next_disclosure_date, "2026-09-28")
        self.assertIn("顺延", waiting.reason)
        self._assert_refresh_flags(
            waiting, in_disclosure_window=False, auto_refresh_eligible=False,
        )

        weekend = self._qdii("2026-09-23", datetime(2026, 9, 26, 22, 0, tzinfo=BJT))
        self.assertEqual(weekend.status, STATUS_PENDING)
        self.assertEqual(weekend.next_disclosure_date, "2026-09-28")

    def test_qdii_long_holiday_waits_for_first_domestic_disclosure_day(self) -> None:
        when = datetime(2026, 10, 7, 15, 0, tzinfo=BJT)
        updated = self._qdii("2026-09-30", when)
        self.assertEqual(updated.status, STATUS_UPDATED)
        self.assertEqual(updated.target_nav_date, "2026-09-30")
        self.assertEqual(updated.next_disclosure_date, "2026-10-09")

        pending = self._qdii("2026-09-29", when)
        self.assertEqual(pending.status, STATUS_PENDING)
        self.assertEqual(pending.target_nav_date, "2026-09-30")
        self.assertEqual(pending.next_disclosure_date, "2026-10-08")
        self.assertIn("顺延", pending.reason)
        self.assertFalse(pending.auto_refresh_eligible)

        self.assertEqual(self._qdii("2026-09-28", when).status, STATUS_OVERDUE)

        reopen = self._qdii("2026-09-29", datetime(2026, 10, 8, 20, 30, tzinfo=BJT))
        self.assertEqual(reopen.status, STATUS_PENDING)
        self._assert_refresh_flags(
            reopen, in_disclosure_window=True, auto_refresh_eligible=True,
        )

    def test_qdii_us_holiday_with_cn_open_has_no_nav_day(self) -> None:
        # 2026-11-26 美国感恩节休市、国内正常交易，该日没有 QDII 净值。
        window = self._qdii("2026-11-24", datetime(2026, 11, 26, 20, 30, tzinfo=BJT))
        self.assertEqual(window.status, STATUS_PENDING)
        self.assertEqual(window.target_nav_date, "2026-11-25")
        self.assertTrue(window.in_disclosure_window)

        next_day = self._qdii("2026-11-25", datetime(2026, 11, 27, 20, 30, tzinfo=BJT))
        self.assertEqual(next_day.status, STATUS_UPDATED)
        self.assertEqual(next_day.target_nav_date, "2026-11-25")
        self.assertEqual(next_day.next_disclosure_date, "2026-11-30")

        lagged = self._qdii("2026-11-24", datetime(2026, 11, 27, 10, 0, tzinfo=BJT))
        self.assertEqual(lagged.status, STATUS_OVERDUE)

    def test_qdii_year_end_keeps_status_but_next_disclosure_unknown(self) -> None:
        res = self._qdii("2026-12-30", datetime(2026, 12, 31, 22, 0, tzinfo=BJT))
        self.assertEqual(res.status, STATUS_UPDATED)
        self.assertIsNone(res.next_disclosure_date)
        uncovered = self._qdii("2026-12-31", datetime(2027, 1, 4, 10, 0, tzinfo=BJT))
        self.assertEqual(uncovered.status, STATUS_UNKNOWN_RULE)

    def test_cn_rule_uses_domestic_days_for_hk_connect_fund(self) -> None:
        # 014674 港股通：09-25 国内休市但港股交易，不应要求 09-25 净值。
        hk_connect = INITIAL_FUND_METADATA["014674"]
        res = resolve_nav_status(
            metadata=hk_connect,
            nav_date="2026-09-24",
            as_of=datetime(2026, 9, 26, 10, 0, tzinfo=BJT),
        )
        self.assertEqual(res.status, STATUS_MARKET_CLOSED)
        self.assertEqual(res.target_nav_date, "2026-09-24")
        self.assertEqual(res.next_disclosure_date, "2026-09-28")

        # 2026-07-01 香港休市、国内交易：当日仍等待净值。
        hk_holiday = resolve_nav_status(
            metadata=hk_connect,
            nav_date="2026-06-30",
            as_of=datetime(2026, 7, 1, 20, 0, tzinfo=BJT),
        )
        self.assertEqual(hk_holiday.status, STATUS_PENDING)
        self.assertTrue(hk_holiday.in_disclosure_window)

    def test_cn_fund_pending_reports_tonight_as_next_disclosure(self) -> None:
        res = resolve_nav_status(
            metadata=self.cn_fund,
            nav_date="2026-09-01",
            as_of=datetime(2026, 9, 2, 14, 30, tzinfo=BJT),
        )
        self.assertEqual(res.next_disclosure_date, "2026-09-02")


if __name__ == "__main__":
    unittest.main()
