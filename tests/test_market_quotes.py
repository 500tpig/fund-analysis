from __future__ import annotations

import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fund_analysis.market_quotes import (
    EASTMONEY_MARKET_QUOTE_URL,
    EastmoneyMarketQuoteProvider,
    MarketQuoteFetchError,
    build_market_quotes_payload,
    market_session,
)


VERIFIED_EASTMONEY_PAYLOADS = {
    "1.000985": {
        "rc": 0,
        "data": {
            "f43": 586718,
            "f57": "000985",
            "f58": "中证全指",
            "f59": 2,
            "f60": 586085,
            "f86": 1788423114,
            "f169": 633,
            "f170": 11,
        },
    },
    "118.AU9999": {
        "rc": 0,
        "data": {
            "f43": 97230,
            "f57": "AU9999",
            "f58": "黄金9999",
            "f59": 2,
            "f60": 95750,
            "f86": 1788451404,
            "f169": 1480,
            "f170": 155,
        },
    },
    "100.NDX": {
        "rc": 0,
        "data": {
            "f43": 2658964,
            "f57": "NDX",
            "f58": "纳斯达克",
            "f59": 2,
            "f60": 2621783,
            "f86": 1788451410,
            "f169": 37181,
            "f170": 142,
        },
    },
}


def _payload_for_params(_url: str, params: dict[str, str]) -> dict[str, object]:
    return VERIFIED_EASTMONEY_PAYLOADS[params["secid"]]


class MarketQuoteProviderTest(unittest.TestCase):
    def test_parses_verified_eastmoney_fields_for_all_symbols(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def get_json(url: str, params: dict[str, str]):
            calls.append((url, dict(params)))
            return _payload_for_params(url, params)

        provider = EastmoneyMarketQuoteProvider(json_getter=get_json)
        csi = provider.fetch("CSI_ALL_SHARE")
        gold = provider.fetch("AU9999")
        ndx = provider.fetch("NDX")

        self.assertEqual([item[0] for item in calls], [EASTMONEY_MARKET_QUOTE_URL] * 3)
        self.assertEqual([item[1]["secid"] for item in calls], ["1.000985", "118.AU9999", "100.NDX"])

        self.assertEqual(csi.name, "中证全指")
        self.assertEqual(csi.last_price, 5867.18)
        self.assertEqual(csi.previous_close, 5860.85)
        self.assertEqual(csi.change_value, 6.33)
        self.assertEqual(csi.change_pct, 0.11)
        self.assertEqual(csi.trade_date, "2026-09-03")
        self.assertEqual(csi.quoted_at, "2026-09-03T16:11:54+08:00")

        self.assertEqual(gold.name, "黄金 9999")
        self.assertEqual(gold.last_price, 972.3)
        self.assertEqual(gold.previous_close, 957.5)
        self.assertEqual(gold.change_value, 14.8)
        self.assertEqual(gold.change_pct, 1.55)
        self.assertEqual(gold.trade_date, "2026-09-04")
        self.assertEqual(gold.quoted_at, "2026-09-04T00:03:24+08:00")

        self.assertEqual(ndx.name, "纳斯达克 100")
        self.assertEqual(ndx.last_price, 26589.64)
        self.assertEqual(ndx.previous_close, 26217.83)
        self.assertEqual(ndx.change_value, 371.81)
        self.assertEqual(ndx.change_pct, 1.42)
        self.assertEqual(ndx.trade_date, "2026-09-03")
        self.assertEqual(ndx.quoted_at, "2026-09-03T12:03:30-04:00")

    def test_gold_night_session_uses_next_calendar_trade_date(self) -> None:
        evening = int(datetime(2026, 9, 3, 20, 0, 5, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())

        def get_json(_url: str, _params: dict[str, str]):
            payload = {
                "data": dict(VERIFIED_EASTMONEY_PAYLOADS["118.AU9999"]["data"]),
            }
            payload["data"]["f86"] = evening
            return payload

        quote = EastmoneyMarketQuoteProvider(json_getter=get_json).fetch("AU9999")
        self.assertEqual(quote.quoted_at, "2026-09-03T20:00:05+08:00")
        self.assertEqual(quote.trade_date, "2026-09-04")

    def test_rejects_wrong_upstream_symbol(self) -> None:
        provider = EastmoneyMarketQuoteProvider(
            json_getter=lambda _url, _params: {
                "data": {
                    "f43": 1,
                    "f57": "OTHER",
                    "f59": 2,
                    "f60": 1,
                    "f86": 1788451404,
                    "f169": 0,
                    "f170": 0,
                }
            }
        )
        with self.assertRaisesRegex(MarketQuoteFetchError, "unexpected upstream symbol"):
            provider.fetch("NDX")

    def test_rejects_empty_numeric_fields_and_zero_timestamp(self) -> None:
        def missing_price(_url: str, _params: dict[str, str]):
            payload = {"data": dict(VERIFIED_EASTMONEY_PAYLOADS["1.000985"]["data"])}
            payload["data"]["f43"] = "-"
            return payload

        def zero_timestamp(_url: str, _params: dict[str, str]):
            payload = {"data": dict(VERIFIED_EASTMONEY_PAYLOADS["1.000985"]["data"])}
            payload["data"]["f86"] = 0
            return payload

        with self.assertRaisesRegex(MarketQuoteFetchError, "invalid numeric quote field f43"):
            EastmoneyMarketQuoteProvider(json_getter=missing_price).fetch("CSI_ALL_SHARE")
        with self.assertRaisesRegex(MarketQuoteFetchError, "invalid quote timestamp"):
            EastmoneyMarketQuoteProvider(json_getter=zero_timestamp).fetch("CSI_ALL_SHARE")

    def test_fetch_all_keeps_successes_when_one_symbol_fails(self) -> None:
        def get_json(url: str, params: dict[str, str]):
            if params["secid"] == "100.NDX":
                raise MarketQuoteFetchError("NDX timeout")
            return _payload_for_params(url, params)

        batch = EastmoneyMarketQuoteProvider(json_getter=get_json).fetch_all()
        self.assertEqual(set(batch.quotes), {"CSI_ALL_SHARE", "AU9999"})
        self.assertEqual(batch.failures, {"NDX": "NDX timeout"})

    def test_fetch_all_records_each_failure_when_all_fail(self) -> None:
        def get_json(_url: str, _params: dict[str, str]):
            raise MarketQuoteFetchError("upstream down")

        batch = EastmoneyMarketQuoteProvider(json_getter=get_json).fetch_all()
        self.assertEqual(batch.quotes, {})
        self.assertEqual(set(batch.failures), {"CSI_ALL_SHARE", "AU9999", "NDX"})
        self.assertTrue(all(error == "upstream down" for error in batch.failures.values()))


class MarketQuotePayloadTest(unittest.TestCase):
    def test_failed_refresh_keeps_last_successful_snapshot(self) -> None:
        now = datetime(2026, 9, 4, 0, 5, tzinfo=ZoneInfo("Asia/Shanghai"))
        snapshots = [
            {
                "symbol": "AU9999",
                "last_price": 972.3,
                "previous_close": 957.5,
                "change_value": 14.8,
                "change_pct": 1.55,
                "trade_date": "2026-09-04",
                "quoted_at": "2026-09-04T00:03:24+08:00",
                "source": "eastmoney_market_quote",
                "source_url": "https://example.test",
                "fetched_at": "2026-09-04T00:03:25+08:00",
            }
        ]
        runs = [
            {
                "job_type": "market_quote",
                "completed_at": "2026-09-04T00:04:00+08:00",
                "evidence_json": '{"failures":{"AU9999":"upstream timeout"}}',
            }
        ]

        payload = build_market_quotes_payload(snapshots, runs, now=now)
        gold = next(item for item in payload["items"] if item["symbol"] == "AU9999")

        self.assertEqual(gold["status"], "failed")
        self.assertEqual(gold["last_price"], 972.3)
        self.assertEqual(gold["warning"], "upstream timeout")
        self.assertFalse(payload["refresh_eligible"])

    def test_missing_quotes_are_refresh_eligible_until_a_recent_attempt(self) -> None:
        now = datetime(2026, 9, 4, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        empty = build_market_quotes_payload([], [], now=now)
        self.assertTrue(empty["refresh_eligible"])
        self.assertEqual([item["status"] for item in empty["items"]], ["missing"] * 3)

        recent = build_market_quotes_payload(
            [],
            [
                {
                    "job_type": "market_quote",
                    "completed_at": "2026-09-04T09:58:00+08:00",
                    "evidence_json": '{"failures":{"CSI_ALL_SHARE":"timeout","AU9999":"timeout","NDX":"timeout"}}',
                }
            ],
            now=now,
        )
        self.assertFalse(recent["refresh_eligible"])
        self.assertEqual([item["status"] for item in recent["items"]], ["failed"] * 3)

    def test_stale_open_market_quote_is_refresh_eligible_without_recent_attempt(self) -> None:
        now = datetime(2026, 9, 4, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        snapshots = [
            {
                "symbol": "CSI_ALL_SHARE",
                "last_price": 5867.18,
                "previous_close": 5860.85,
                "change_value": 6.33,
                "change_pct": 0.11,
                "trade_date": "2026-09-04",
                "quoted_at": "2026-09-04T09:30:00+08:00",
                "source": "eastmoney_market_quote",
                "source_url": "https://example.test",
                "fetched_at": "2026-09-04T09:30:01+08:00",
            }
        ]
        stale = build_market_quotes_payload(snapshots, [], now=now)
        self.assertTrue(stale["refresh_eligible"])
        self.assertEqual(stale["items"][0]["status"], "stale")

        recent = build_market_quotes_payload(
            snapshots,
            [{"job_type": "market_quote", "completed_at": "2026-09-04T09:59:00+08:00", "evidence_json": "{}"}],
            now=now,
        )
        self.assertFalse(recent["refresh_eligible"])

    def test_market_sessions_use_each_market_timezone(self) -> None:
        cn_open = datetime(2026, 9, 3, 2, 0, tzinfo=timezone.utc)
        us_open = datetime(2026, 9, 3, 15, 0, tzinfo=timezone.utc)
        gold_night = datetime(2026, 9, 3, 16, 0, tzinfo=timezone.utc)

        self.assertEqual(market_session("CSI_ALL_SHARE", cn_open), ("trading", "交易中"))
        self.assertEqual(market_session("NDX", us_open), ("trading", "交易中"))
        self.assertEqual(market_session("AU9999", gold_night), ("trading", "夜盘交易中"))

    def test_gold_friday_night_session_runs_until_saturday_morning(self) -> None:
        shanghai = ZoneInfo("Asia/Shanghai")
        friday_night = datetime(2026, 9, 4, 23, 4, tzinfo=shanghai)
        saturday_morning = datetime(2026, 9, 5, 1, 0, tzinfo=shanghai)
        saturday_after_close = datetime(2026, 9, 5, 2, 31, tzinfo=shanghai)
        monday_morning = datetime(2026, 9, 7, 0, 30, tzinfo=shanghai)
        before_holiday = datetime(2026, 9, 24, 20, 30, tzinfo=shanghai)

        self.assertEqual(market_session("AU9999", friday_night), ("trading", "夜盘交易中"))
        self.assertEqual(market_session("AU9999", saturday_morning), ("trading", "夜盘交易中"))
        self.assertEqual(market_session("AU9999", saturday_after_close), ("closed", "休市"))
        self.assertEqual(market_session("AU9999", monday_morning), ("closed", "已收盘"))
        self.assertEqual(market_session("AU9999", before_holiday), ("closed", "已收盘"))

    def test_ndx_regular_hours_follow_new_york_dst(self) -> None:
        winter_open = datetime(2026, 1, 15, 14, 35, tzinfo=timezone.utc)
        winter_preopen = datetime(2026, 1, 15, 14, 20, tzinfo=timezone.utc)
        summer_open = datetime(2026, 7, 15, 13, 35, tzinfo=timezone.utc)
        summer_preopen = datetime(2026, 7, 15, 13, 20, tzinfo=timezone.utc)

        self.assertEqual(market_session("NDX", winter_open), ("trading", "交易中"))
        self.assertEqual(market_session("NDX", winter_preopen), ("closed", "已收盘"))
        self.assertEqual(market_session("NDX", summer_open), ("trading", "交易中"))
        self.assertEqual(market_session("NDX", summer_preopen), ("closed", "已收盘"))

    def test_unsupported_calendar_year_returns_pending_label(self) -> None:
        future = datetime(2027, 1, 4, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        gold_year_end = datetime(2026, 12, 31, 20, 30, tzinfo=ZoneInfo("Asia/Shanghai"))

        self.assertEqual(market_session("CSI_ALL_SHARE", future), ("unknown", "日历待更新"))
        self.assertEqual(market_session("NDX", future), ("unknown", "日历待更新"))
        self.assertEqual(market_session("AU9999", future), ("unknown", "日历待更新"))
        self.assertEqual(market_session("AU9999", gold_year_end), ("unknown", "日历待更新"))
