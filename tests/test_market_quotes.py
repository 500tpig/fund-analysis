from __future__ import annotations

import unittest
from unittest import mock
from datetime import datetime, timezone
from io import BytesIO
from zoneinfo import ZoneInfo

from fund_analysis.market_quotes import (
    EASTMONEY_MARKET_QUOTE_URL,
    EastmoneyMarketQuoteProvider,
    MarketQuoteFetchError,
    build_market_quotes_payload,
    market_session,
    parse_backup_quote,
)


VERIFIED_EASTMONEY_PAYLOADS = {
    "1.000985": {
        "rc": 0,
        "data": {"diff": [{
            "f2": 586718,
            "f12": "000985",
            "f14": "中证全指",
            "f1": 2,
            "f18": 586085,
            "f124": 1788423114,
            "f4": 633,
            "f3": 11, "f13": 1,
        }]},
    },
    "118.AU9999": {
        "rc": 0,
        "data": {"diff": [{
            "f2": 97230,
            "f12": "AU9999",
            "f14": "黄金9999",
            "f1": 2,
            "f18": 95750,
            "f124": 1788451404,
            "f4": 1480,
            "f3": 155, "f13": 118,
        }]},
    },
    "100.NDX100": {
        "rc": 0,
        "data": {"diff": [{
            "f2": 2910381,
            "f12": "NDX100",
            "f14": "纳斯达克100",
            "f1": 2,
            "f18": 2982252,
            "f124": 1792687104,
            "f4": -71871,
            "f3": -241, "f13": 100,
        }]},
    },
}


def _payload_for_params(_url: str, params: dict[str, str]) -> dict[str, object]:
    return VERIFIED_EASTMONEY_PAYLOADS[params["secids"]]


VERIFIED_SINA_GOLD_QUOTE = (
    'var hq_str_SGE_AU9999="AU9999,沪  金99,Au99.99,869.00,867.94,860.31,'
    '862.50,873.00,860.20,860.70,867.50,868.50,110.00,50.00,643198.00,'
    '558261248000.00,2026-10-28 15:30:09,0.96%";'
)


class GoldBackupQuoteTest(unittest.TestCase):
    def test_uses_previous_close_instead_of_previous_average(self):
        quote = parse_backup_quote("AU9999", VERIFIED_SINA_GOLD_QUOTE)
        self.assertEqual((quote.last_price, quote.previous_close), (869.00, 860.70))
        self.assertEqual((quote.change_value, quote.change_pct), (8.30, 0.96))
        self.assertEqual(quote.quoted_at, "2026-10-28T15:30:09+08:00")
        self.assertEqual(quote.trade_date, "2026-10-28")
        self.assertEqual(quote.source, "sina_market_quote")
        self.assertEqual(quote.payload["fields"][5], "860.31")

    def test_rejects_invalid_close_and_inconsistent_percentage(self):
        for old, new in (
            ("860.70", "0"), ("860.70", "-1"), ("860.70", ""),
            ("860.70", "nan"), ("860.70", "860.31"),
            ("0.96%", "1.01%"), ("0.96%", "-0.96%"),
            ("Au99.99", "Au99.95"),
        ):
            with self.subTest(old=old, new=new), self.assertRaises(MarketQuoteFetchError):
                parse_backup_quote("AU9999", VERIFIED_SINA_GOLD_QUOTE.replace(old, new))

    def test_primary_failure_uses_gold_backup_and_preserves_failure_evidence(self):
        def get_json(url, params):
            if params["secids"] == "118.AU9999":
                raise MarketQuoteFetchError("HTTP Error 502: Bad Gateway")
            return _payload_for_params(url, params)

        provider = EastmoneyMarketQuoteProvider(json_getter=get_json)
        for raw, succeeds in (
            (VERIFIED_SINA_GOLD_QUOTE, True),
            (VERIFIED_SINA_GOLD_QUOTE.replace("0.96%", "1.01%"), False),
        ):
            with self.subTest(succeeds=succeeds), mock.patch(
                "fund_analysis.market_quotes.urlopen", return_value=BytesIO(raw.encode("gb18030"))
            ):
                batch = provider.fetch_all()
            self.assertEqual(set(batch.quotes) - {"AU9999"}, {"CSI_ALL_SHARE", "NDX"})
            if succeeds:
                self.assertFalse(batch.failures)
                quote = batch.quotes["AU9999"]
                self.assertEqual((quote.source, quote.previous_close), ("sina_market_quote", 860.70))
                self.assertEqual(quote.warnings, [{
                    "code": "primary_source_failed", "message": "HTTP Error 502: Bad Gateway",
                }])
            else:
                self.assertNotIn("AU9999", batch.quotes)
                self.assertIn("502", batch.failures["AU9999"])
                self.assertIn("inconsistent quote prices and change", batch.failures["AU9999"])


class MarketQuoteProviderTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(EastmoneyMarketQuoteProvider, "_fetch_backup", side_effect=MarketQuoteFetchError("backup down"))
        self.backup = patcher.start()
        self.addCleanup(patcher.stop)

    def test_backup_gold_and_index_preserve_source_time_and_reject_wrong_instrument(self):
        gold = 'var hq_str_SGE_AU9999="AU9999,沪 金99,Au99.99,868.60,874.16,880.30,874.50,883.98,868.60,880.30,868.60,877.90,39.00,1.00,55654.00,48650100000.00,2026-10-23 00:48:19,-1.33%";'
        fields = ["0"] * 33
        for i, value in {2: ".NDX", 3: "28974.21", 4: "29822.52", 30: "2026-10-22 12:51:39", 31: "-848.31", 32: "-2.84"}.items():
            fields[i] = value
        ndx = 'v_usNDX="' + "~".join(fields) + '";'
        g = parse_backup_quote("AU9999", gold)
        n = parse_backup_quote("NDX", ndx)
        self.assertEqual((g.last_price, g.previous_close, g.source), (868.60, 880.30, "sina_market_quote"))
        self.assertEqual(n.quoted_at, "2026-10-22T12:51:39-04:00")
        self.assertEqual(n.source, "tencent_market_quote")
        for raw in (ndx.replace(".NDX", "OTHER"), ndx.replace("28974.21", "0"), 'v_usNDX="";'):
            with self.assertRaises(MarketQuoteFetchError):
                parse_backup_quote("NDX", raw)
        fields[2], fields[3], fields[4] = "000985", "5154.03", "5283.50"
        fields[30], fields[31], fields[32] = "20261022161450", "-129.47", "-2.45"
        csi = parse_backup_quote("CSI_ALL_SHARE", 'v_sh000985="' + "~".join(fields) + '";')
        self.assertEqual(csi.quoted_at, "2026-10-22T16:14:50+08:00")
        with self.assertRaises(MarketQuoteFetchError):
            parse_backup_quote("AU9999", gold.replace("-1.33%", "1.33%"))
        self.backup.side_effect = None
        self.backup.return_value = n
        provider = EastmoneyMarketQuoteProvider(json_getter=lambda *_: (_ for _ in ()).throw(MarketQuoteFetchError("502")))
        batch = provider.fetch_all()
        self.assertFalse(batch.failures)
        self.assertEqual(batch.quotes["NDX"].warnings[0]["message"], "502")

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
        self.assertEqual([item[1]["secids"] for item in calls], ["1.000985", "118.AU9999", "100.NDX100"])

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
        self.assertEqual(ndx.last_price, 29103.81)
        self.assertEqual(ndx.previous_close, 29822.52)
        self.assertEqual(ndx.change_value, -718.71)
        self.assertEqual(ndx.change_pct, -2.41)
        self.assertEqual(ndx.trade_date, "2026-10-22")
        self.assertEqual(ndx.quoted_at, "2026-10-22T12:38:24-04:00")

    def test_gold_night_session_uses_next_calendar_trade_date(self) -> None:
        evening = int(datetime(2026, 9, 3, 20, 0, 5, tzinfo=ZoneInfo("Asia/Shanghai")).timestamp())

        def get_json(_url: str, _params: dict[str, str]):
            payload = {
                "data": {"diff": [dict(VERIFIED_EASTMONEY_PAYLOADS["118.AU9999"]["data"]["diff"][0])]},
            }
            payload["data"]["diff"][0]["f124"] = evening
            return payload

        quote = EastmoneyMarketQuoteProvider(json_getter=get_json).fetch("AU9999")
        self.assertEqual(quote.quoted_at, "2026-09-03T20:00:05+08:00")
        self.assertEqual(quote.trade_date, "2026-09-04")

    def test_rejects_wrong_upstream_symbol(self) -> None:
        provider = EastmoneyMarketQuoteProvider(
            json_getter=lambda _url, _params: {
                "data": {"diff": [{
                    "f13": 100,
                    "f2": 1,
                    "f12": "NDX",
                    "f1": 2,
                    "f18": 1,
                    "f124": 1788451404,
                    "f4": 0,
                    "f3": 0,
                }]}
            }
        )
        with self.assertRaisesRegex(MarketQuoteFetchError, "unexpected upstream symbol"):
            provider.fetch("NDX")

    def test_rejects_empty_numeric_fields_and_zero_timestamp(self) -> None:
        def missing_price(_url: str, _params: dict[str, str]):
            payload = {"data": {"diff": [dict(VERIFIED_EASTMONEY_PAYLOADS["1.000985"]["data"]["diff"][0])]}}
            payload["data"]["diff"][0]["f2"] = "-"
            return payload

        def zero_timestamp(_url: str, _params: dict[str, str]):
            payload = {"data": {"diff": [dict(VERIFIED_EASTMONEY_PAYLOADS["1.000985"]["data"]["diff"][0])]}}
            payload["data"]["diff"][0]["f124"] = 0
            return payload

        with self.assertRaisesRegex(MarketQuoteFetchError, "invalid numeric quote field f2"):
            EastmoneyMarketQuoteProvider(json_getter=missing_price).fetch("CSI_ALL_SHARE")
        with self.assertRaisesRegex(MarketQuoteFetchError, "invalid quote timestamp"):
            EastmoneyMarketQuoteProvider(json_getter=zero_timestamp).fetch("CSI_ALL_SHARE")

    def test_fetch_all_keeps_successes_when_one_symbol_fails(self) -> None:
        def get_json(url: str, params: dict[str, str]):
            if params["secids"] == "100.NDX100":
                raise MarketQuoteFetchError("NDX timeout")
            return _payload_for_params(url, params)

        batch = EastmoneyMarketQuoteProvider(json_getter=get_json).fetch_all()
        self.assertEqual(set(batch.quotes), {"CSI_ALL_SHARE", "AU9999"})
        self.assertEqual(batch.failures, {"NDX": "NDX timeout; backup: backup down"})

    def test_fetch_all_records_each_failure_when_all_fail(self) -> None:
        def get_json(_url: str, _params: dict[str, str]):
            raise MarketQuoteFetchError("upstream down")

        batch = EastmoneyMarketQuoteProvider(json_getter=get_json).fetch_all()
        self.assertEqual(batch.quotes, {})
        self.assertEqual(set(batch.failures), {"CSI_ALL_SHARE", "AU9999", "NDX"})
        self.assertTrue(all(error == "upstream down; backup: backup down" for error in batch.failures.values()))


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
