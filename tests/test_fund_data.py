from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping

from fund_analysis.fund_data import (
    EASTMONEY_BASIC_URL,
    EASTMONEY_CORE_SOURCE_NAME,
    EASTMONEY_CORE_URL,
    EASTMONEY_FUND_SEARCH_URL,
    EASTMONEY_HISTORY_URL,
    EASTMONEY_INDEX_KLINE_URL,
    SINA_ESTIMATE_SOURCE_NAME,
    SINA_ESTIMATE_URL,
    SINA_INDEX_KLINE_URL,
    TIANTIAN_VALUATION_SOURCE_NAME,
    TIANTIAN_VALUATION_URL,
    CompositeFundEstimateProvider,
    EastmoneyFundDataProvider,
    FundCurveProvider,
    FundDataFetchError,
    _in_time_ranges,
    _parse_sina_estimate,
    _time_ranges,
    search_public_funds,
)


TODAY = date(2026, 9, 1)


def core_row(code: str, **overrides: Any) -> dict[str, Any]:
    row = {
        "FCODE": code,
        "SHORTNAME": f"基金{code}",
        "DWJZ": "1.20",
        "FSRQ": "2026-08-31",
        "GSZ": "1.212",
        "GSZZL": "1.0",
        "GZTIME": "2026-09-01 10:10",
    }
    row.update(overrides)
    return row


def sina_payload(
    *,
    value: Any = "1.212",
    return_pct: Any = "1.0",
    estimate_date: str = "20260901",
) -> dict[str, Any]:
    return {
        "result": {
            "status": {"code": 0},
            "data": {
                "worth": "1.20",
                "worth_date": "2026-08-31",
                "time_range": ["09:30-11:30", "13:00-15:00"],
                "networth": [
                    {
                        "pre_nav": value,
                        "nav_pct": return_pct,
                        "pre_nav2": "1.218",
                        "nav2_pct": "1.5",
                        "pre_date": estimate_date,
                        "min_time": "14:59",
                        "unused": "must not be stored",
                    },
                    {
                        "pre_nav": "9.99",
                        "nav_pct": "9.99",
                        "pre_date": estimate_date,
                        "min_time": "15:01",
                    },
                ],
            },
        }
    }


class FundDataTest(unittest.TestCase):
    def test_eastmoney_lightweight_provider_uses_only_basic_snapshot(self) -> None:
        calls: list[str] = []

        def getter(url: str, params: Mapping[str, str]) -> Mapping[str, Any]:
            calls.append(url)
            self.assertEqual(params["FCODE"], "110011")
            return {
                "Datas": {
                    "FCODE": "110011",
                    "SHORTNAME": "测试基金",
                    "FSRQ": "2026-09-01",
                    "DWJZ": "1.2345",
                    "RZDF": "0.88",
                }
            }

        data = EastmoneyFundDataProvider(json_getter=getter).fetch_without_quarterly_holdings("110011")
        self.assertEqual(calls, [EASTMONEY_BASIC_URL])
        self.assertEqual(data.latest_nav, 1.2345)
        self.assertEqual(data.daily_return_pct, 0.88)

    def test_eastmoney_rejects_missing_basic_data(self) -> None:
        provider = EastmoneyFundDataProvider(json_getter=lambda _url, _params: {"Datas": []})
        with self.assertRaises(FundDataFetchError):
            provider.fetch("110011")

    def test_core_fetches_multiple_codes_once_and_maps_fields(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def getter(url: str, params: Mapping[str, str]) -> Mapping[str, Any]:
            calls.append((url, dict(params)))
            return {"success": True, "data": [core_row("110011"), core_row("000001", DWJZ=None, NAV="2.0")]}

        batch = CompositeFundEstimateProvider(json_getter=getter, today=TODAY).fetch_many(
            ["110011", "000001"]
        )
        self.assertEqual([url for url, _params in calls], [EASTMONEY_CORE_URL])
        self.assertEqual(calls[0][1]["FCODES"], "110011,000001")
        self.assertEqual(set(batch.estimates), {"110011", "000001"})
        self.assertEqual(batch.estimates["000001"].previous_nav, 2.0)
        self.assertEqual(batch.estimates["110011"].source, EASTMONEY_CORE_SOURCE_NAME)

    def test_core_partial_data_only_calls_sina_for_missing_code(self) -> None:
        calls: list[tuple[str, str | None]] = []

        def getter(url: str, params: Mapping[str, str]) -> Mapping[str, Any]:
            calls.append((url, params.get("symbol")))
            if url == EASTMONEY_CORE_URL:
                return {"success": True, "data": [core_row("110011"), core_row("000001", GSZ=None)]}
            if url == SINA_ESTIMATE_URL:
                return sina_payload()
            self.fail(f"unexpected provider call: {url}")

        batch = CompositeFundEstimateProvider(json_getter=getter, today=TODAY).fetch_many(
            ["110011", "000001"]
        )
        self.assertEqual(calls, [(EASTMONEY_CORE_URL, None), (SINA_ESTIMATE_URL, "000001")])
        self.assertEqual(batch.estimates["000001"].source, SINA_ESTIMATE_SOURCE_NAME)

    def test_core_hard_failure_uses_valuation_last(self) -> None:
        calls: list[str] = []

        def getter(url: str, _params: Mapping[str, str]) -> Mapping[str, Any]:
            calls.append(url)
            if url == EASTMONEY_CORE_URL:
                raise OSError("core unavailable")
            if url == TIANTIAN_VALUATION_URL:
                return {
                    "data": [
                        {
                            "FCODE": "110011",
                            "SHORTNAME": "测试基金",
                            "NAV": "1.20",
                            "PDATE": "2026-08-31",
                            "GSZ": "1.21",
                            "GSZZL": "0.83",
                            "GZTIME": "2026-09-01 10:20",
                        }
                    ]
                }
            self.fail(f"unexpected provider call: {url}")

        batch = CompositeFundEstimateProvider(json_getter=getter, today=TODAY).fetch_many(["110011"])
        self.assertEqual(calls, [EASTMONEY_CORE_URL, TIANTIAN_VALUATION_URL])
        self.assertTrue(batch.evidence["core_hard_failed"])
        self.assertEqual(batch.estimates["110011"].source, TIANTIAN_VALUATION_SOURCE_NAME)

    def test_valuation_last_missing_is_filled_by_sina(self) -> None:
        calls: list[str] = []

        def getter(url: str, _params: Mapping[str, str]) -> Mapping[str, Any]:
            calls.append(url)
            if url == EASTMONEY_CORE_URL:
                return {"success": False, "data": []}
            if url == TIANTIAN_VALUATION_URL:
                return {"data": []}
            if url == SINA_ESTIMATE_URL:
                return sina_payload()
            self.fail(f"unexpected provider call: {url}")

        batch = CompositeFundEstimateProvider(json_getter=getter, today=TODAY).fetch_many(["110011"])
        self.assertEqual(
            calls,
            [EASTMONEY_CORE_URL, TIANTIAN_VALUATION_URL, SINA_ESTIMATE_URL],
        )
        self.assertEqual(batch.estimates["110011"].source, SINA_ESTIMATE_SOURCE_NAME)

    def test_sina_accepts_zero_and_stores_only_selected_point(self) -> None:
        def getter(url: str, _params: Mapping[str, str]) -> Mapping[str, Any]:
            if url == EASTMONEY_CORE_URL:
                return {"data": [core_row("110011", GSZZL=None)]}
            return sina_payload(return_pct=0)

        item = CompositeFundEstimateProvider(json_getter=getter, today=TODAY).fetch_many(
            ["110011"]
        ).estimates["110011"]
        self.assertEqual(item.estimated_return_pct, 0.0)
        self.assertEqual(item.estimated_at, "2026-09-01 14:59:00")
        self.assertNotIn("networth", item.payload)
        self.assertNotIn("unused", item.payload["selected_point"])
        self.assertIn("公开披露持仓", item.warnings[0]["message"])

    def test_sina_keeps_nav_and_rate_from_same_field_pair(self) -> None:
        # 017731 于 2026-09-26 的真实分钟点：两组都以 worth=4.2105 为基准，
        # 旧实现把 pre_nav 与 nav2_pct 拼在一起。
        payload = {
            "result": {
                "status": {"code": 0},
                "data": {
                    "worth": "4.2105",
                    "worth_date": "20260923",
                    "time_range": [["21:30", "00:30"], ["00:30", "04:00"]],
                    "networth": [
                        {
                            "min_time": "21:30:00", "pre_date": "2026-09-25",
                            "pre_nav": "4.1800", "nav_pct": "-0.7244",
                            "pre_nav2": "4.1600", "nav2_pct": "-1.1994",
                        },
                        {
                            "min_time": "03:59:00", "pre_date": "2026-09-26",
                            "pre_nav": "4.1740", "nav_pct": "-0.8669",
                            "pre_nav2": "4.1560", "nav2_pct": "-1.2944",
                        },
                        {
                            "min_time": "04:00:00", "pre_date": "2026-09-26",
                            "pre_nav": "4.1736", "nav_pct": "-0.8758",
                            "pre_nav2": "4.1559", "nav2_pct": "-1.2979",
                        },
                        {
                            "min_time": "05:04:00", "pre_date": "2026-09-26",
                            "pre_nav": "4.1736", "nav_pct": "-0.8758",
                            "pre_nav2": "4.1559", "nav2_pct": "-1.2979",
                        },
                    ],
                },
            }
        }
        batch = CompositeFundEstimateProvider(
            json_getter=lambda url, _params: (
                {"data": []} if url != SINA_ESTIMATE_URL else payload
            ),
            today=date(2026, 9, 26),
        ).fetch_many(["017731"])
        item = batch.estimates["017731"]
        self.assertEqual(item.estimated_nav, 4.1736)
        self.assertEqual(item.estimated_return_pct, -0.8758)
        # 盘后 05:04 的重复点不当作来源更新时间。
        self.assertEqual(item.estimated_at, "2026-09-26 04:00:00")
        self.assertEqual(item.previous_nav_date, "20260923")
        self.assertEqual(item.payload["selected_point"]["nav2_pct"], "-1.2979")
        self.assertEqual(item.payload["field_pair"], "pre_nav/nav_pct")
        self.assertFalse(
            any("不一致" in warning["message"] for warning in item.warnings)
        )

    def test_sina_time_ranges_follow_source_across_dst_and_midnight(self) -> None:
        summer = _time_ranges([["21:30", "00:30"], ["00:30", "04:00"]])
        winter = _time_ranges([["22:30", "01:30"], ["01:30", "05:00"]])
        domestic = _time_ranges(["09:30-11:30", "13:00-15:00"])
        self.assertTrue(_in_time_ranges((23, 10, 0), summer))
        self.assertTrue(_in_time_ranges((4, 0, 0), summer))
        self.assertFalse(_in_time_ranges((4, 30, 0), summer))
        self.assertFalse(_in_time_ranges((22, 0, 0), winter))
        self.assertTrue(_in_time_ranges((4, 30, 0), winter))
        self.assertFalse(_in_time_ranges((12, 0, 0), domestic))
        self.assertFalse(_in_time_ranges((15, 1, 0), domestic))
        self.assertTrue(_in_time_ranges((15, 1, 0), _time_ranges(None)))

    def test_sina_point_without_paired_rate_is_not_mixed_with_other_pair(self) -> None:
        payload = sina_payload()
        for point in payload["result"]["data"]["networth"]:
            point.pop("nav_pct", None)
        batch = CompositeFundEstimateProvider(
            json_getter=lambda url, _params: (
                {"data": []} if url != SINA_ESTIMATE_URL else payload
            ),
            today=TODAY,
        ).fetch_many(["110011"])
        self.assertEqual(batch.estimates, {})
        self.assertIn("110011", batch.failures)

    def test_future_and_unknown_dates_are_rejected(self) -> None:
        def future_getter(url: str, _params: Mapping[str, str]) -> Mapping[str, Any]:
            if url == EASTMONEY_CORE_URL:
                return {"data": [core_row("110011", GZTIME="2026-09-03 15:00")]}
            if url == SINA_ESTIMATE_URL:
                return sina_payload(estimate_date="20260903")
            self.fail(f"unexpected provider call: {url}")

        core_batch = CompositeFundEstimateProvider(
            json_getter=future_getter, today=TODAY
        ).fetch_many(["110011"])
        self.assertEqual(core_batch.estimates, {})
        self.assertIn("110011", core_batch.failures)

        def invalid_date_getter(url: str, _params: Mapping[str, str]) -> Mapping[str, Any]:
            if url == EASTMONEY_CORE_URL:
                return {"data": [core_row("110011", GZTIME="not-a-date")]}
            if url == SINA_ESTIMATE_URL:
                return sina_payload(estimate_date="invalid")
            self.fail(f"unexpected provider call: {url}")

        batch = CompositeFundEstimateProvider(
            json_getter=invalid_date_getter, today=TODAY
        ).fetch_many(["110011"])
        self.assertEqual(batch.estimates, {})
        self.assertIn("110011", batch.failures)

    def test_recent_trading_day_data_on_weekend_is_preserved_with_source_date(self) -> None:
        weekend = date(2026, 9, 26)  # Saturday
        friday_str = "2026-09-25 15:00"

        def weekend_getter(url: str, _params: Mapping[str, str]) -> Mapping[str, Any]:
            if url == EASTMONEY_CORE_URL:
                return {"data": [core_row("110011", GZTIME=friday_str, GSZ="1.5000", GSZZL="1.20")]}
            self.fail(f"unexpected provider call: {url}")

        batch = CompositeFundEstimateProvider(
            json_getter=weekend_getter, today=weekend
        ).fetch_many(["110011"])
        self.assertIn("110011", batch.estimates)
        est = batch.estimates["110011"]
        self.assertEqual(est.estimated_at, friday_str)
        self.assertEqual(est.estimated_return_pct, 1.20)
        self.assertEqual(est.status, "stale")

    def test_stale_core_does_not_block_fresher_sina(self) -> None:
        weekend = date(2026, 9, 26)  # Saturday; latest trading day is 2026-09-25

        def getter(url: str, _params: Mapping[str, str]) -> Mapping[str, Any]:
            if url == EASTMONEY_CORE_URL:
                # Core provides very old estimate (2026-09-09)
                return {"data": [core_row("110011", GZTIME="2026-09-09 10:09", GSZ="1.4000", GSZZL="0.50")]}
            if url == SINA_ESTIMATE_URL:
                # Sina provides latest trading day estimate (2026-09-25)
                return sina_payload(estimate_date="20260925")
            self.fail(f"unexpected provider call: {url}")

        batch = CompositeFundEstimateProvider(
            json_getter=getter, today=weekend
        ).fetch_many(["110011"])
        self.assertIn("110011", batch.estimates)
        est = batch.estimates["110011"]
        self.assertEqual(est.source, SINA_ESTIMATE_SOURCE_NAME)
        self.assertTrue(est.estimated_at.startswith("2026-09-25"))
        self.assertEqual(est.status, "stale")
        self.assertIn("110011", batch.evidence["success_codes_by_source"][SINA_ESTIMATE_SOURCE_NAME])
        self.assertNotIn("110011", batch.evidence["success_codes_by_source"][EASTMONEY_CORE_SOURCE_NAME])

    def test_stale_core_does_not_override_with_older_sina(self) -> None:
        weekend = date(2026, 9, 26)  # Saturday; latest trading day is 2026-09-25

        def getter(url: str, _params: Mapping[str, str]) -> Mapping[str, Any]:
            if url == EASTMONEY_CORE_URL:
                # Core provides 2026-09-23 15:00
                return {"data": [core_row("110011", GZTIME="2026-09-23 15:00", GSZ="1.4500", GSZZL="0.80")]}
            if url == SINA_ESTIMATE_URL:
                # Sina provides older estimate: 2026-09-22 14:59
                payload = {
                    "result": {
                        "status": {"code": 0},
                        "data": {
                            "worth": "1.4500",
                            "worth_date": "2026-09-21",
                            "time_range": "09:30-15:00",
                            "networth": [
                                {"pre_nav": "1.44", "nav_pct": "0.70", "pre_date": "2026-09-22", "min_time": "14:59"}
                            ],
                        },
                    }
                }
                return payload
            self.fail(f"unexpected provider call: {url}")

        batch = CompositeFundEstimateProvider(
            json_getter=getter, today=weekend
        ).fetch_many(["110011"])
        self.assertIn("110011", batch.estimates)
        est = batch.estimates["110011"]
        # 必须保留 Core 的较新估值，绝不能被更旧的 Sina 覆盖
        self.assertEqual(est.source, EASTMONEY_CORE_SOURCE_NAME)
        self.assertEqual(est.estimated_at, "2026-09-23 15:00")
        self.assertEqual(est.estimated_return_pct, 0.80)
        # 来源统计必须准确对应实际选中的 Core，Sina 不计入
        self.assertIn("110011", batch.evidence["success_codes_by_source"][EASTMONEY_CORE_SOURCE_NAME])
        self.assertEqual(batch.evidence["success_codes_by_source"][SINA_ESTIMATE_SOURCE_NAME], [])

    def test_valuation_last_preserves_source_statistics_when_stale(self) -> None:
        weekend = date(2026, 9, 26)

        def getter(url: str, _params: Mapping[str, str]) -> Mapping[str, Any]:
            if url == EASTMONEY_CORE_URL:
                raise HTTPError(url, 500, "Server Error", None, None)  # type: ignore[arg-type]
            if url == TIANTIAN_VALUATION_URL:
                # ValuationLast provides 2026-09-23 15:00
                return {
                    "data": [
                        {
                            "FCODE": "110011",
                            "SHORTNAME": "易方达中小盘",
                            "GZTIME": "2026-09-23 15:00",
                            "NAV": "1.4500",
                            "GSZ": "1.4600",
                            "GSZZL": "0.69",
                            "PDATE": "2026-09-22",
                        }
                    ]
                }
            if url == SINA_ESTIMATE_URL:
                # Sina fails
                raise HTTPError(url, 500, "Server Error", None, None)  # type: ignore[arg-type]
            self.fail(f"unexpected provider call: {url}")

        batch = CompositeFundEstimateProvider(
            json_getter=getter, today=weekend
        ).fetch_many(["110011"])
        self.assertIn("110011", batch.estimates)
        est = batch.estimates["110011"]
        # 保留 ValuationLast 估值，且来源统计绝不能被错误统计为 Core
        self.assertEqual(est.source, TIANTIAN_VALUATION_SOURCE_NAME)
        self.assertIn("110011", batch.evidence["success_codes_by_source"][TIANTIAN_VALUATION_SOURCE_NAME])
        self.assertEqual(batch.evidence["success_codes_by_source"][EASTMONEY_CORE_SOURCE_NAME], [])
        self.assertEqual(batch.evidence["success_codes_by_source"][SINA_ESTIMATE_SOURCE_NAME], [])

    def test_stale_core_preserves_data_when_sina_fails(self) -> None:
        weekend = date(2026, 9, 26)

        def getter(url: str, _params: Mapping[str, str]) -> Mapping[str, Any]:
            if url == EASTMONEY_CORE_URL:
                return {"data": [core_row("110011", GZTIME="2026-09-23 15:00", GSZ="1.4500", GSZZL="0.80")]}
            if url == SINA_ESTIMATE_URL:
                raise HTTPError(url, 500, "Sina Down", None, None)  # type: ignore[arg-type]
            self.fail(f"unexpected provider call: {url}")

        batch = CompositeFundEstimateProvider(
            json_getter=getter, today=weekend
        ).fetch_many(["110011"])
        self.assertIn("110011", batch.estimates)
        self.assertEqual(batch.estimates["110011"].estimated_at, "2026-09-23 15:00")
        self.assertEqual(batch.failures, {})
        self.assertIn("110011", batch.evidence["success_codes_by_source"][EASTMONEY_CORE_SOURCE_NAME])

    def test_sina_multi_date_points_strictly_orders_by_date_and_time(self) -> None:
        payload = {
            "result": {
                "status": {"code": 0},
                "data": {
                    "worth": "1.0000",
                    "worth_date": "2026-09-24",
                    "time_range": "09:30-15:00",
                    "networth": [
                        # Older date with late time (15:00) vs Newer date with early time (09:30)
                        {"pre_nav": "1.01", "nav_pct": "1.0", "pre_date": "2026-09-24", "min_time": "15:00"},
                        {"pre_nav": "1.02", "nav_pct": "2.0", "pre_date": "2026-09-25", "min_time": "09:30"},
                    ],
                },
            }
        }
        est = _parse_sina_estimate("110011", payload, "https://example.invalid", today=date(2026, 9, 26))
        self.assertTrue(est.estimated_at.startswith("2026-09-25 09:30"))
        self.assertEqual(est.estimated_return_pct, 2.0)
        self.assertEqual(est.status, "stale")

    def test_all_failed_codes_have_failure_messages(self) -> None:
        def getter(url: str, _params: Mapping[str, str]) -> Mapping[str, Any]:
            if url == EASTMONEY_CORE_URL:
                return {"data": []}
            if url == TIANTIAN_VALUATION_URL:
                return {"data": "invalid"}
            if url == SINA_ESTIMATE_URL:
                return {"result": {"status": {"code": 1}, "data": {}}}
            self.fail(f"unexpected provider call: {url}")

        batch = CompositeFundEstimateProvider(json_getter=getter, today=TODAY).fetch_many(
            ["110011", "000001"]
        )
        self.assertEqual(set(batch.failures), {"110011", "000001"})
        self.assertEqual(batch.evidence["failed_codes"], ["110011", "000001"])

    def test_codes_are_trimmed_deduplicated_and_empty_values_ignored(self) -> None:
        seen_codes: list[str] = []

        def getter(_url: str, params: Mapping[str, str]) -> Mapping[str, Any]:
            seen_codes.extend(params["FCODES"].split(","))
            return {"data": [core_row("110011"), core_row("000001")]}

        batch = CompositeFundEstimateProvider(json_getter=getter, today=TODAY).fetch_many(
            [" 110011 ", "", "110011", "  ", "000001"]
        )
        self.assertEqual(seen_codes, ["110011", "000001"])
        self.assertEqual(list(batch.estimates), ["110011", "000001"])

    def test_curve_provider_normalizes_fund_and_hs300_history(self) -> None:
        calls: list[str] = []

        def text_getter(_url: str, _params: Mapping[str, str]) -> str:
            raise OSError("page data unavailable")

        def getter(url: str, params: Mapping[str, str]) -> Mapping[str, Any]:
            calls.append(url)
            if url == EASTMONEY_HISTORY_URL:
                self.assertEqual(params["fundCode"], "110011")
                return {
                    "ErrCode": 0,
                    "Data": {
                        "LSJZList": [
                            {"FSRQ": "2026-08-31", "DWJZ": "1.10", "LJJZ": "1.10"},
                            {"FSRQ": "2026-08-29", "DWJZ": "1.00", "LJJZ": "1.00"},
                        ]
                    },
                }
            if url == EASTMONEY_INDEX_KLINE_URL:
                self.assertEqual(params["secid"], "1.000300")
                return {
                    "data": {
                        "klines": [
                            "2026-08-29,100,100,101,99,1,1,1,0,0,0",
                            "2026-08-31,100,105,106,99,1,1,1,0,0,0",
                        ]
                    }
                }
            self.fail(f"unexpected provider call: {url}")

        payload = FundCurveProvider(
            json_getter=getter,
            text_getter=text_getter,
            today=TODAY,
        ).fetch_performance("110011", "1m", benchmark="000300")
        self.assertEqual(calls, [EASTMONEY_HISTORY_URL, EASTMONEY_INDEX_KLINE_URL])
        self.assertEqual(payload["source"], "eastmoney_lsjz")
        self.assertEqual(payload["points"][0]["return_pct"], 0.0)
        self.assertEqual(payload["points"][-1]["return_pct"], 10.0)
        self.assertEqual(payload["benchmark"]["code"], "000300")
        self.assertEqual(payload["benchmark"]["name"], "沪深300")
        self.assertEqual(payload["benchmark"]["points"][-1]["return_pct"], 5.0)

    def test_curve_provider_fetches_selected_benchmark_index(self) -> None:
        def text_getter(_url: str, _params: Mapping[str, str]) -> str:
            raise OSError("page data unavailable")

        def getter(url: str, params: Mapping[str, str]) -> Mapping[str, Any]:
            if url == EASTMONEY_HISTORY_URL:
                return {
                    "ErrCode": 0,
                    "Data": {
                        "LSJZList": [
                            {"FSRQ": "2026-08-31", "DWJZ": "1.10", "LJJZ": "1.10"},
                            {"FSRQ": "2026-08-29", "DWJZ": "1.00", "LJJZ": "1.00"},
                        ]
                    },
                }
            if url == EASTMONEY_INDEX_KLINE_URL:
                self.assertEqual(params["secid"], "0.399006")
                return {
                    "data": {
                        "klines": [
                            "2026-08-29,100,200,201,199,1,1,1,0,0,0",
                            "2026-08-31,100,220,221,199,1,1,1,0,0,0",
                        ]
                    }
                }
            self.fail(f"unexpected provider call: {url}")

        payload = FundCurveProvider(
            json_getter=getter,
            text_getter=text_getter,
            today=TODAY,
        ).fetch_performance("110011", "1m", benchmark="399006")
        self.assertEqual(payload["benchmark"]["code"], "399006")
        self.assertEqual(payload["benchmark"]["name"], "创业板指")
        self.assertEqual(payload["benchmark"]["points"][-1]["return_pct"], 10.0)

    def test_curve_provider_falls_back_to_sina_for_selected_benchmark(self) -> None:
        def text_getter(_url: str, _params: Mapping[str, str]) -> str:
            raise OSError("page data unavailable")

        def getter(url: str, params: Mapping[str, str]) -> Mapping[str, Any]:
            if url == EASTMONEY_HISTORY_URL:
                return {
                    "ErrCode": 0,
                    "Data": {
                        "LSJZList": [
                            {"FSRQ": "2026-08-31", "DWJZ": "1.10", "LJJZ": "1.10"},
                            {"FSRQ": "2026-08-29", "DWJZ": "1.00", "LJJZ": "1.00"},
                        ]
                    },
                }
            if url == EASTMONEY_INDEX_KLINE_URL:
                raise OSError("eastmoney index unavailable")
            if url == SINA_INDEX_KLINE_URL:
                self.assertEqual(params["symbol"], "sh000012")
                return {
                    "result": {
                        "data": [
                            {"day": "2026-08-29", "close": "100"},
                            {"day": "2026-08-31", "close": "101"},
                        ]
                    }
                }
            self.fail(f"unexpected provider call: {url}")

        payload = FundCurveProvider(
            json_getter=getter,
            text_getter=text_getter,
            today=TODAY,
        ).fetch_performance("110011", "1m", benchmark="000012")
        self.assertEqual(payload["benchmark"]["code"], "000012")
        self.assertEqual(payload["benchmark"]["name"], "国债指数")
        self.assertEqual(payload["benchmark"]["source"], "sina_index_kline")
        self.assertEqual(payload["benchmark"]["points"][-1]["return_pct"], 1.0)

    def test_curve_provider_skips_benchmark_when_blank(self) -> None:
        def text_getter(_url: str, _params: Mapping[str, str]) -> str:
            raise OSError("page data unavailable")

        def getter(url: str, _params: Mapping[str, str]) -> Mapping[str, Any]:
            if url == EASTMONEY_HISTORY_URL:
                return {
                    "ErrCode": 0,
                    "Data": {
                        "LSJZList": [
                            {"FSRQ": "2026-08-31", "DWJZ": "1.10", "LJJZ": "1.10"},
                            {"FSRQ": "2026-08-29", "DWJZ": "1.00", "LJJZ": "1.00"},
                        ]
                    },
                }
            self.fail(f"blank benchmark must not fetch index: {url}")

        payload = FundCurveProvider(
            json_getter=getter,
            text_getter=text_getter,
            today=TODAY,
        ).fetch_performance("110011", "1m")
        self.assertIsNone(payload["benchmark"])
        self.assertTrue(all("基准" not in item for item in payload["warnings"]))

    def test_curve_provider_rejects_unknown_benchmark(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported performance benchmark"):
            FundCurveProvider(today=TODAY).fetch_performance(
                "110011",
                "1m",
                benchmark="999999",
            )

    def test_curve_provider_uses_pingzhongdata_in_one_request(self) -> None:
        timestamp = int(datetime(2026, 8, 15, tzinfo=timezone.utc).timestamp() * 1000)

        def getter(_url: str, _params: Mapping[str, str]) -> Mapping[str, Any]:
            raise OSError("history unavailable")

        provider = FundCurveProvider(
            json_getter=getter,
            text_getter=lambda _url, _params: f"var Data_ACWorthTrend = [[{timestamp},1.23]];",
            today=TODAY,
        )
        payload = provider.fetch_performance("110011", "1m")
        self.assertEqual(payload["source"], "eastmoney_pingzhongdata")
        self.assertEqual(payload["points"][0]["raw_value"], 1.23)
        self.assertIsNone(payload["benchmark"])
        self.assertEqual(payload["warnings"], [])

    def test_curve_provider_keeps_pingzhongdata_china_calendar_date(self) -> None:
        china = timezone(timedelta(hours=8))
        timestamp = int(datetime(2026, 9, 1, tzinfo=china).timestamp() * 1000)

        def getter(_url: str, _params: Mapping[str, str]) -> Mapping[str, Any]:
            raise OSError("history unavailable")

        payload = FundCurveProvider(
            json_getter=getter,
            text_getter=lambda _url, _params: f"var Data_ACWorthTrend = [[{timestamp},1.8654]];",
            today=date(2026, 9, 2),
        ).fetch_performance("024481", "1m")
        self.assertEqual(payload["source"], "eastmoney_pingzhongdata")
        self.assertEqual(payload["end_date"], "2026-09-01")
        self.assertEqual(payload["points"][-1]["date"], "2026-09-01")
        self.assertEqual(payload["points"][-1]["raw_value"], 1.8654)

    def test_curve_provider_returns_sina_points_or_explicit_empty_state(self) -> None:
        provider = FundCurveProvider(
            json_getter=lambda _url, _params: sina_payload(),
            today=TODAY,
        )
        payload = provider.fetch_intraday("110011")
        self.assertEqual(payload["availability"], "available")
        self.assertEqual([point["time"] for point in payload["points"]], ["14:59:00"])
        self.assertEqual(payload["points"][0]["estimated_return_pct"], 1.0)

        empty_provider = FundCurveProvider(
            json_getter=lambda _url, _params: {
                "result": {"status": {"code": 0}, "data": {"networth": None}}
            },
            today=TODAY,
        )
        empty = empty_provider.fetch_intraday("027138")
        self.assertEqual(empty["availability"], "no_minute_data")
        self.assertEqual(empty["points"], [])

    def test_curve_provider_keeps_latest_session_on_non_trading_day(self) -> None:
        # 周日打开详情：新浪返回上一交易日（周五）的分时点，按最近已结束会话返回，
        # 不因「不是今天」清空；点上的 datetime 保留真实日期供前端标注。
        provider = FundCurveProvider(
            json_getter=lambda _url, _params: {
                "result": {
                    "status": {"code": 0},
                    "data": {
                        "worth": "1.20",
                        "worth_date": "2026-09-25",
                        "time_range": ["09:30-11:30", "13:00-15:00"],
                        "networth": [
                            {"pre_nav": "1.212", "nav_pct": "1.0", "pre_date": "2026-09-25", "min_time": "14:59"},
                            {"pre_nav": "1.210", "nav_pct": "0.8", "pre_date": "2026-09-25", "min_time": "09:30"},
                            {"pre_nav": "1.100", "nav_pct": "-8.0", "pre_date": "2026-09-18", "min_time": "14:59"},
                        ],
                    },
                }
            },
            today=date(2026, 9, 27),
        )
        payload = provider.fetch_intraday("110011")
        self.assertEqual(payload["availability"], "available")
        self.assertEqual(
            [point["datetime"] for point in payload["points"]],
            ["2026-09-25 09:30:00", "2026-09-25 14:59:00"],
        )
        self.assertEqual(payload["date"], "2026-09-27")

    def test_curve_provider_keeps_latest_overnight_session_on_non_trading_day(self) -> None:
        # 周六白天打开 QDII 详情：最近已结束会话是周五 21:30 至周六凌晨的夜盘，
        # 返回该会话而不是清空。
        provider = FundCurveProvider(
            json_getter=lambda _url, _params: {
                "result": {
                    "status": {"code": 0},
                    "data": {
                        "worth": "4.2105",
                        "worth_date": "2026-09-24",
                        "time_range": [["21:30", "00:30"], ["00:30", "04:00"]],
                        "networth": [
                            {"pre_nav": "4.2000", "nav_pct": "-0.2500", "pre_date": "2026-09-25", "min_time": "21:30"},
                            {"pre_nav": "4.1736", "nav_pct": "-0.8758", "pre_date": "2026-09-26", "min_time": "04:00"},
                            {"pre_nav": "4.3000", "nav_pct": "2.0000", "pre_date": "2026-09-24", "min_time": "22:00"},
                        ],
                    },
                }
            },
            today=date(2026, 9, 26),
        )
        payload = provider.fetch_intraday("017731")
        self.assertEqual(payload["availability"], "available")
        self.assertEqual(
            [point["datetime"] for point in payload["points"]],
            ["2026-09-25 21:30:00", "2026-09-26 04:00:00"],
        )

    def test_curve_provider_handles_cross_midnight_points_and_sorts_chronologically(self) -> None:
        today = date(2026, 9, 26)
        provider = FundCurveProvider(
            json_getter=lambda _url, _params: {
                "result": {
                    "status": {"code": 0},
                    "data": {
                        "worth": "4.2105",
                        "worth_date": "2026-09-23",
                        "time_range": [["21:30", "00:30"], ["00:30", "04:00"]],
                        "networth": [
                            {"pre_nav": "4.1736", "nav_pct": "-0.8758", "pre_date": "2026-09-26", "min_time": "04:00"},
                            {"pre_nav": "4.1700", "nav_pct": "-0.9600", "pre_date": "2026-09-26", "min_time": "00:15"},
                            {"pre_nav": "4.2000", "nav_pct": "-0.2500", "pre_date": "2026-09-25", "min_time": "21:30"},
                            {"pre_nav": "4.1800", "nav_pct": "-0.7200", "pre_date": "2026-09-25", "min_time": "23:45"},
                            {"pre_nav": "4.1736", "nav_pct": "-0.8758", "pre_date": "2026-09-26", "min_time": "05:04"},
                            {"pre_nav": "4.3000", "nav_pct": "2.0000", "pre_date": "2026-09-24", "min_time": "22:00"},
                        ],
                    },
                }
            },
            today=today,
        )
        payload = provider.fetch_intraday("017731")
        self.assertEqual(payload["availability"], "available")
        self.assertEqual(len(payload["points"]), 4)
        times = [point["time"] for point in payload["points"]]
        self.assertEqual(times, ["21:30:00", "23:45:00", "00:15:00", "04:00:00"])
        dates = [point["date"] for point in payload["points"]]
        self.assertEqual(dates, ["2026-09-25", "2026-09-25", "2026-09-26", "2026-09-26"])
        datetimes = [point["datetime"] for point in payload["points"]]
        self.assertEqual(
            datetimes,
            [
                "2026-09-25 21:30:00",
                "2026-09-25 23:45:00",
                "2026-09-26 00:15:00",
                "2026-09-26 04:00:00",
            ],
        )


    def test_curve_provider_handles_overnight_intraday_before_midnight(self) -> None:
        today = date(2026, 9, 25)
        provider = FundCurveProvider(
            json_getter=lambda _url, _params: {
                "result": {
                    "status": {"code": 0},
                    "data": {
                        "worth": "4.2105",
                        "worth_date": "2026-09-23",
                        "time_range": [["21:30", "00:30"], ["00:30", "04:00"]],
                        "networth": [
                            # 正在当晚盘中产生的点（零点前）
                            {"pre_nav": "4.1700", "nav_pct": "-0.9600", "pre_date": "2026-09-25", "min_time": "22:40"},
                            {"pre_nav": "4.2000", "nav_pct": "-0.2500", "pre_date": "2026-09-25", "min_time": "21:30"},
                            {"pre_nav": "4.1850", "nav_pct": "-0.6000", "pre_date": "2026-09-25", "min_time": "22:00"},
                            # 前一天历史点（应被过滤）
                            {"pre_nav": "4.1500", "nav_pct": "-1.4000", "pre_date": "2026-09-24", "min_time": "23:00"},
                        ],
                    },
                }
            },
            today=today,
        )
        payload = provider.fetch_intraday("017731")
        self.assertEqual(payload["availability"], "available")
        self.assertEqual(len(payload["points"]), 3)
        self.assertEqual([p["time"] for p in payload["points"]], ["21:30:00", "22:00:00", "22:40:00"])
        self.assertEqual([p["date"] for p in payload["points"]], ["2026-09-25", "2026-09-25", "2026-09-25"])
        self.assertEqual(
            [p["datetime"] for p in payload["points"]],
            [
                "2026-09-25 21:30:00",
                "2026-09-25 22:00:00",
                "2026-09-25 22:40:00",
            ],
        )

    def test_curve_provider_deduplicates_same_minute_points(self) -> None:
        today = date(2026, 9, 25)
        provider = FundCurveProvider(
            json_getter=lambda _url, _params: {
                "result": {
                    "status": {"code": 0},
                    "data": {
                        "worth": "4.2105",
                        "worth_date": "2026-09-23",
                        "time_range": [["21:30", "00:30"], ["00:30", "04:00"]],
                        "networth": [
                            # 21:30 出现两次，后一条 nav 4.2000 应该覆盖前一条 4.1900
                            {"pre_nav": "4.1900", "nav_pct": "-0.4000", "pre_date": "2026-09-25", "min_time": "21:30"},
                            {"pre_nav": "4.2000", "nav_pct": "-0.2500", "pre_date": "2026-09-25", "min_time": "21:30"},
                            # 22:00 正常点
                            {"pre_nav": "4.1850", "nav_pct": "-0.6000", "pre_date": "2026-09-25", "min_time": "22:00"},
                        ],
                    },
                }
            },
            today=today,
        )
        payload = provider.fetch_intraday("017731")
        self.assertEqual(payload["availability"], "available")
        self.assertEqual(len(payload["points"]), 2)
        self.assertEqual([p["time"] for p in payload["points"]], ["21:30:00", "22:00:00"])
        self.assertEqual(payload["points"][0]["estimated_nav"], 4.2000)
        self.assertEqual(payload["points"][0]["estimated_return_pct"], -0.2500)

    def test_search_public_funds_parses_code_and_name(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def getter(url: str, params: Mapping[str, str]) -> Mapping[str, Any]:
            calls.append((url, dict(params)))
            return {
                "Datas": [
                    {"CODE": "110011", "NAME": "易方达沪深300ETF联接A", "JP": "YFHS300ETFLJA"},
                    {"CODE": "110011.OF", "NAME": "重复应跳过", "JP": "X"},
                    {"CODE": "abc", "NAME": "非六位", "JP": "X"},
                    {"CODE": "000001", "NAME": " ", "JP": "X"},
                    {"CODE": "005827", "NAME": "易方达蓝筹精选混合", "JP": "YFDLCJXH"},
                ]
            }

        hits = search_public_funds("易方达", limit=8, json_getter=getter)
        self.assertEqual(calls, [(EASTMONEY_FUND_SEARCH_URL, {"m": "1", "key": "易方达"})])
        self.assertEqual(
            [hit.to_dict() for hit in hits],
            [
                {"code": "110011", "name": "易方达沪深300ETF联接A"},
                {"code": "005827", "name": "易方达蓝筹精选混合"},
            ],
        )

    def test_search_public_funds_empty_keyword_does_not_fetch(self) -> None:
        def getter(_url: str, _params: Mapping[str, str]) -> Mapping[str, Any]:
            raise AssertionError("empty keyword must not fetch")

        self.assertEqual(search_public_funds("  ", json_getter=getter), [])

    def test_search_public_funds_raises_on_upstream_failure(self) -> None:
        def getter(_url: str, _params: Mapping[str, str]) -> Mapping[str, Any]:
            raise OSError("connection reset")

        with self.assertRaises(FundDataFetchError):
            search_public_funds("110011", json_getter=getter)

    def test_search_public_funds_raises_on_malformed_payload(self) -> None:
        with self.assertRaises(FundDataFetchError):
            search_public_funds(
                "110011",
                json_getter=lambda _url, _params: {"unexpected": []},
            )


if __name__ == "__main__":
    unittest.main()
