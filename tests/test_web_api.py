from __future__ import annotations

import asyncio
import os
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from fund_analysis.fund_data import (
    DEFAULT_PERFORMANCE_BENCHMARK,
    PERFORMANCE_BENCHMARKS,
    FundEstimateBatch,
    FundIntradayEstimate,
    FundMarketData,
)
from fund_analysis.market_quotes import MarketQuote, MarketQuoteBatch
from fund_analysis.web.app import create_app
from fund_analysis.web.storage import LocalStorage


def market_data(
    code: str,
    *,
    nav_date: str | None = "2026-09-01",
    latest_nav: float | None = 1.2,
    daily_return_pct: float | None = 1.0,
    source: str = "eastmoney_fundmobapi",
) -> FundMarketData:
    return FundMarketData(
        code=code,
        name=f"基金{code}",
        nav_date=nav_date,
        latest_nav=latest_nav,
        daily_return_pct=daily_return_pct,
        source=source,
        source_urls=[],
        fetched_at="2026-09-01T10:00:00+08:00",
        warnings=[],
    )


def estimate(
    code: str,
    *,
    estimated_date: str | None = None,
    estimated_time: str = "10:10:00",
    status: str | None = None,
) -> FundIntradayEstimate:
    estimate_date = estimated_date or date.today().isoformat()
    est_status = status or ("stale" if estimated_date and estimated_date != date.today().isoformat() else "available")
    return FundIntradayEstimate(
        code=code,
        name=f"基金{code}",
        previous_nav=1.2,
        previous_nav_date="2026-08-31",
        estimated_nav=1.212,
        estimated_return_pct=1.0,
        estimated_at=f"{estimate_date}T{estimated_time}+08:00",
        source="eastmoney_fund_core_diy",
        source_url="https://example.test",
        fetched_at=f"{estimate_date}T10:10:01+08:00",
        status=est_status,
        warnings=[],
        payload={},
    )


class FakeMarketProvider:
    def __init__(
        self,
        failing_code: str | None = None,
        *,
        nav_date: str | None = "2026-09-01",
        latest_nav: float | None = 1.2,
        daily_return_pct: float | None = 1.0,
        source: str = "eastmoney_fundmobapi",
    ):
        self.failing_code = failing_code
        self.nav_date = nav_date
        self.latest_nav = latest_nav
        self.daily_return_pct = daily_return_pct
        self.source = source

    def fetch_without_quarterly_holdings(self, code: str) -> FundMarketData:
        if code == self.failing_code:
            raise RuntimeError("official failed")
        return market_data(
            code,
            nav_date=self.nav_date,
            latest_nav=self.latest_nav,
            daily_return_pct=self.daily_return_pct,
            source=self.source,
        )


class FakeEstimateProvider:
    def __init__(
        self,
        failing_code: str | None = None,
        *,
        estimated_time: str = "10:10:00",
        estimated_date: str | None = None,
        status: str | None = None,
    ):
        self.failing_code = failing_code
        self.estimated_time = estimated_time
        self.estimated_date = estimated_date
        self.status = status
        self.calls: list[list[str]] = []

    def fetch_many(self, codes: list[str]) -> FundEstimateBatch:
        self.calls.append(list(codes))
        estimates = {
            code: estimate(
                code,
                estimated_date=self.estimated_date,
                estimated_time=self.estimated_time,
                status=self.status,
            )
            for code in codes
            if code != self.failing_code
        }
        failures = (
            {self.failing_code: "estimate failed"}
            if self.failing_code is not None and self.failing_code in codes
            else {}
        )
        return FundEstimateBatch(
            estimates=estimates,
            failures=failures,
            evidence={
                "primary_source": "eastmoney_fund_core_diy",
                "core_hard_failed": False,
                "success_codes_by_source": {
                    "eastmoney_fund_core_diy": list(estimates),
                    "tiantian_fund_valuation_last": [],
                    "sina_fund_estimate": [],
                },
                "failed_codes": list(failures),
            },
        )


class FakeMarketQuoteProvider:
    name = "eastmoney_market_quote"

    def fetch_all(self) -> MarketQuoteBatch:
        details = {
            "CSI_ALL_SHARE": ("中证全指", "index", "中证指数", "CNY", 5867.18),
            "AU9999": ("黄金 9999", "spot", "上海黄金交易所", "CNY/g", 972.3),
            "NDX": ("纳斯达克 100", "index", "Nasdaq", "USD", 26589.64),
        }
        quotes = {
            symbol: MarketQuote(
                symbol=symbol,
                name=name,
                instrument_type=instrument_type,
                market=market,
                currency=currency,
                last_price=last_price,
                previous_close=last_price - 1,
                change_value=1,
                change_pct=0.1,
                trade_date="2026-09-04",
                quoted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                source=self.name,
                source_url="https://example.test/quote",
                fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                payload={},
                warnings=[],
            )
            for symbol, (name, instrument_type, market, currency, last_price) in details.items()
        }
        return MarketQuoteBatch(quotes=quotes, failures={})


class _ScriptedMarketQuoteProvider:
    name = "eastmoney_market_quote"

    def __init__(self, batches: list[MarketQuoteBatch]) -> None:
        self._batches = list(batches)

    def fetch_all(self) -> MarketQuoteBatch:
        return self._batches.pop(0)


class FakeCurveProvider:
    def fetch_performance(
        self,
        code: str,
        period: str,
        benchmark: str = DEFAULT_PERFORMANCE_BENCHMARK,
    ) -> dict[str, object]:
        if period == "bad":
            raise ValueError("unsupported performance range: bad")
        if benchmark and benchmark not in PERFORMANCE_BENCHMARKS:
            raise ValueError(f"unsupported performance benchmark: {benchmark}")
        return {
            "code": code,
            "range": period,
            "source": "eastmoney_lsjz",
            "start_date": "2026-08-01",
            "end_date": "2026-09-01",
            "points": [
                {"date": "2026-08-01", "raw_value": 1.0, "return_pct": 0.0},
                {"date": "2026-09-01", "raw_value": 1.1, "return_pct": 10.0},
            ],
            "benchmark": None,
            "warnings": [],
        }

    def fetch_intraday(self, code: str) -> dict[str, object]:
        return {
            "code": code,
            "date": "2026-09-01",
            "source": "sina_fund_estimate",
            "availability": "no_minute_data",
            "points": [],
            "warning": "仅供参考",
        }


# 测试客户端使用假 Host（testserver，异步用例为 test），显式列入可信写来源。
_WRITE_HOSTS = patch.dict(
    os.environ, {"FUND_ANALYSIS_WRITE_HOSTS": "testserver,test"}
)


def setUpModule() -> None:
    _WRITE_HOSTS.start()


def tearDownModule() -> None:
    _WRITE_HOSTS.stop()


class WebApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = LocalStorage(Path(self.temp_dir.name) / "test.db")
        self.estimate_provider = FakeEstimateProvider("000001")
        self.client = TestClient(
            create_app(
                storage=self.storage,
                market_provider=FakeMarketProvider("000002"),
                estimate_provider=self.estimate_provider,
                market_quote_provider=FakeMarketQuoteProvider(),
                curve_provider=FakeCurveProvider(),
                fund_searcher=lambda keyword, limit=8: (
                    [{"code": "110011", "name": "易方达沪深300ETF联接A"}]
                    if keyword.strip() in {"110011", "易方达"}
                    else []
                ),
            )
        )

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def test_openapi_exposes_only_valuation_mvp_routes(self) -> None:
        paths = self.client.get("/openapi.json").json()["paths"]
        self.assertEqual(
            set(paths),
            {
                "/api/holdings",
                "/api/holdings/{holding_id}",
                "/api/funds",
                "/api/funds/search",
                "/api/funds/portfolio-risk",
                "/api/funds/{code}/performance",
                "/api/funds/{code}/intraday-curve",
                "/api/funds/{code}/official-profit",
                "/api/funds/refresh",
                "/api/holdings/entry/recognize",
                "/api/holdings/entry/preview",
                "/api/holdings/entry/confirm",
                "/api/simulation/accounts",
                "/api/simulation/accounts/{account_id}",
                "/api/simulation/accounts/{account_id}/positions",
                "/api/simulation/accounts/{account_id}/orders",
                "/api/simulation/accounts/{account_id}/runs",
                "/api/simulation/accounts/{account_id}/metrics",
                "/api/simulation/accounts/{account_id}/pause",
                "/api/simulation/accounts/{account_id}/resume",
                "/api/simulation/orders/{order_id}",
                "/api/simulation/orders/{order_id}/cancel",
                "/api/simulation/runs/{run_id}",
            },
        )
        self.assertEqual(sum(len(operations) for operations in paths.values()), 26)

    def test_funds_payload_embeds_latest_market_quotes(self) -> None:
        empty = self.client.get("/api/funds").json()["market_quotes"]
        self.assertEqual(empty["schema_version"], "market_quotes.v1")
        self.assertTrue(empty["refresh_eligible"])
        self.assertEqual([item["status"] for item in empty["items"]], ["missing"] * 3)

        refresh = self.client.post("/api/funds/refresh").json()
        quotes = refresh["monitor"]["market_quotes"]

        self.assertEqual(refresh["quote_success"], 3)
        self.assertEqual(refresh["quote_failure"], 0)
        self.assertEqual(
            [item["symbol"] for item in quotes["items"]],
            ["CSI_ALL_SHARE", "AU9999", "NDX"],
        )
        self.assertEqual([item["status"] for item in quotes["items"]], ["fresh"] * 3)
        self.assertEqual(quotes["items"][1]["last_price"], 972.3)

    def test_market_quote_failures_keep_latest_snapshot_across_restart(self) -> None:
        success = FakeMarketQuoteProvider().fetch_all()
        provider = _ScriptedMarketQuoteProvider(
            [
                MarketQuoteBatch(
                    quotes={key: quote for key, quote in success.quotes.items() if key != "NDX"},
                    failures={"NDX": "NDX timeout"},
                ),
                MarketQuoteBatch(
                    quotes={},
                    failures={symbol: "upstream down" for symbol in success.quotes},
                ),
            ]
        )
        db_path = Path(self.temp_dir.name) / "quotes-restart.db"
        storage = LocalStorage(db_path)
        with TestClient(
            create_app(
                storage=storage,
                market_provider=FakeMarketProvider(),
                estimate_provider=FakeEstimateProvider(),
                market_quote_provider=provider,
                curve_provider=FakeCurveProvider(),
            )
        ) as client:
            partial = client.post("/api/funds/refresh").json()
            self.assertEqual(partial["quote_success"], 2)
            self.assertEqual(partial["quote_failure"], 1)
            self.assertEqual(partial["quote_failures"], [{"code": "NDX", "error": "NDX timeout"}])
            self.assertEqual(
                {
                    item["symbol"]: (item["status"], item["last_price"])
                    for item in partial["monitor"]["market_quotes"]["items"]
                },
                {
                    "CSI_ALL_SHARE": ("fresh", 5867.18),
                    "AU9999": ("fresh", 972.3),
                    "NDX": ("failed", None),
                },
            )

        restarted = LocalStorage(db_path)
        with TestClient(
            create_app(
                storage=restarted,
                market_provider=FakeMarketProvider(),
                estimate_provider=FakeEstimateProvider(),
                market_quote_provider=provider,
                curve_provider=FakeCurveProvider(),
            )
        ) as client:
            loaded = client.get("/api/funds").json()["market_quotes"]
            self.assertEqual(
                {item["symbol"]: item["last_price"] for item in loaded["items"]},
                {"CSI_ALL_SHARE": 5867.18, "AU9999": 972.3, "NDX": None},
            )

            failed = client.post("/api/funds/refresh").json()
            self.assertEqual(failed["quote_success"], 0)
            self.assertEqual(failed["quote_failure"], 3)
            quotes = failed["monitor"]["market_quotes"]
            self.assertEqual(
                {item["symbol"]: item["last_price"] for item in quotes["items"]},
                {"CSI_ALL_SHARE": 5867.18, "AU9999": 972.3, "NDX": None},
            )
            self.assertEqual([item["status"] for item in quotes["items"]], ["failed"] * 3)
            self.assertEqual(
                {row["symbol"]: row["last_price"] for row in restarted.get_market_quote_snapshots()},
                {"AU9999": 972.3, "CSI_ALL_SHARE": 5867.18},
            )
            runs = restarted.get_latest_data_source_runs()
            market_run = next(run for run in runs if run["job_type"] == "market_quote")
            self.assertEqual(market_run["status"], "failed")
            self.assertIn("upstream down", market_run["evidence_json"])

    def test_fund_curve_endpoints_keep_empty_intraday_as_success(self) -> None:
        performance = self.client.get("/api/funds/110011/performance?range=1m")
        self.assertEqual(performance.status_code, 200)
        self.assertEqual(performance.json()["points"][-1]["return_pct"], 10.0)
        self.assertIsNone(performance.json()["benchmark"])

        intraday = self.client.get("/api/funds/027138/intraday-curve")
        self.assertEqual(intraday.status_code, 200)
        self.assertEqual(intraday.json()["availability"], "no_minute_data")

        invalid = self.client.get("/api/funds/110011/performance?range=bad")
        self.assertEqual(invalid.status_code, 400)

        switched = self.client.get("/api/funds/110011/performance?range=1m&benchmark=399006")
        self.assertEqual(switched.status_code, 200)

        invalid_benchmark = self.client.get(
            "/api/funds/110011/performance?range=1m&benchmark=999999"
        )
        self.assertEqual(invalid_benchmark.status_code, 400)

    def test_portfolio_risk_keeps_event_loop_responsive(self) -> None:
        class SlowCurveProvider(FakeCurveProvider):
            def fetch_performance(
                self,
                code: str,
                period: str,
                benchmark: str = DEFAULT_PERFORMANCE_BENCHMARK,
            ) -> dict[str, object]:
                time.sleep(0.25)
                return super().fetch_performance(code, period, benchmark)

        storage = LocalStorage(Path(self.temp_dir.name) / "risk-loop.db")
        storage.add_holding({"code": "110011", "name": "测试基金", "amount": 1000})
        app = create_app(
            storage=storage,
            market_provider=FakeMarketProvider(),
            estimate_provider=FakeEstimateProvider(),
            market_quote_provider=FakeMarketQuoteProvider(),
            curve_provider=SlowCurveProvider(),
        )

        async def _probe() -> float:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                risk_task = asyncio.create_task(
                    client.get("/api/funds/portfolio-risk?range=1y")
                )
                await asyncio.sleep(0.05)
                started = time.perf_counter()
                ping = await client.get("/api/funds")
                elapsed = time.perf_counter() - started
                risk = await risk_task
                self.assertEqual(ping.status_code, 200)
                self.assertEqual(risk.status_code, 200)
                return elapsed

        elapsed = asyncio.run(_probe())
        self.assertLess(elapsed, 0.15)

    def test_refresh_keeps_event_loop_responsive(self) -> None:
        class SlowMarketProvider(FakeMarketProvider):
            def fetch_without_quarterly_holdings(self, code: str) -> FundMarketData:
                time.sleep(0.25)
                return super().fetch_without_quarterly_holdings(code)

        storage = LocalStorage(Path(self.temp_dir.name) / "refresh-loop.db")
        storage.add_holding({"code": "110011", "name": "测试基金", "amount": 1000})
        app = create_app(
            storage=storage,
            market_provider=SlowMarketProvider(),
            estimate_provider=FakeEstimateProvider(),
            market_quote_provider=FakeMarketQuoteProvider(),
            curve_provider=FakeCurveProvider(),
        )

        async def _probe() -> float:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                started = time.perf_counter()
                refresh_task = asyncio.create_task(client.post("/api/funds/refresh"))
                await asyncio.sleep(0.05)
                timer_elapsed = time.perf_counter() - started
                refresh = await refresh_task
                self.assertEqual(refresh.status_code, 200)
                return timer_elapsed

        elapsed = asyncio.run(_probe())
        self.assertLess(elapsed, 0.15)

    def test_concurrent_refresh_requests_share_one_upstream_run(self) -> None:
        calls: list[str] = []

        class SlowMarketProvider(FakeMarketProvider):
            def fetch_without_quarterly_holdings(self, code: str) -> FundMarketData:
                calls.append(code)
                time.sleep(0.2)
                return super().fetch_without_quarterly_holdings(code)

        storage = LocalStorage(Path(self.temp_dir.name) / "refresh-coalesce.db")
        storage.add_holding({"code": "110011", "name": "测试基金", "amount": 1000})
        estimates = FakeEstimateProvider()
        app = create_app(
            storage=storage,
            market_provider=SlowMarketProvider(),
            estimate_provider=estimates,
            market_quote_provider=FakeMarketQuoteProvider(),
            curve_provider=FakeCurveProvider(),
        )

        async def _run() -> list[dict[str, object]]:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                first = asyncio.create_task(client.post("/api/funds/refresh"))
                await asyncio.sleep(0.05)
                second = asyncio.create_task(client.post("/api/funds/refresh"))
                return [response.json() for response in await asyncio.gather(first, second)]

        first, second = asyncio.run(_run())
        self.assertEqual(calls, ["110011"])
        self.assertEqual(len(estimates.calls), 1)
        self.assertFalse(first["coalesced"])
        self.assertTrue(second["coalesced"])
        self.assertEqual(second["success"], 1)
        self.assertIn("rows", second["monitor"])

    def test_holdings_crud(self) -> None:
        created = self.client.post(
            "/api/holdings",
            json={"code": "110011", "name": "测试基金", "amount": 1000, "cost": 900, "theme": "消费"},
        )
        self.assertEqual(created.status_code, 201)
        holding_id = created.json()["id"]
        payload = self.client.get("/api/holdings").json()
        self.assertEqual(payload["rows"][0]["holding_profit"], 100.0)
        updated = self.client.put(
            f"/api/holdings/{holding_id}",
            json={"code": "110011", "name": "测试基金", "amount": 1200, "cost": 900},
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(self.client.delete(f"/api/holdings/{holding_id}").status_code, 200)
        self.assertEqual(self.client.get("/api/holdings").json()["rows"], [])

    def test_holdings_api_rejects_duplicate_codes(self) -> None:
        first = self.client.post(
            "/api/holdings",
            json={"code": "110011", "name": "第一只基金", "amount": 1000},
        )
        self.assertEqual(first.status_code, 201)
        duplicate = self.client.post(
            "/api/holdings",
            json={"code": "110011", "name": "重复基金", "amount": 500},
        )

        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(duplicate.json()["detail"], "基金代码 110011 已存在")

    def test_holding_edit_preserves_import_only_accounting_fields(self) -> None:
        holding_id = self.storage.add_holding(
            {
                "code": "110011",
                "name": "测试基金",
                "amount": 1000,
                "daily_profit": 12.5,
                "cumulative_profit": 80,
            }
        )
        response = self.client.put(
            f"/api/holdings/{holding_id}",
            json={"code": "110011", "name": "新名称", "amount": 1200},
        )
        self.assertEqual(response.status_code, 200)
        stored = self.storage.get_holding(holding_id)
        self.assertEqual(stored["daily_profit"], 12.5)
        self.assertEqual(stored["cumulative_profit"], 80.0)

    def test_holding_amount_nav_date_roundtrip_and_invalidation(self) -> None:
        created = self.client.post(
            "/api/holdings",
            json={
                "code": "110011",
                "name": "测试基金",
                "amount": 10000,
                "amount_nav_date": "2026-09-07",
            },
        )
        self.assertEqual(created.status_code, 201)
        holding_id = created.json()["id"]
        stored = self.storage.get_holding(holding_id)
        self.assertEqual(stored["amount_nav_date"], "2026-09-07")
        funds = self.client.get("/api/funds").json()
        self.assertEqual(funds["rows"][0]["amount_nav_date"], "2026-09-07")
        self.assertIn("today", funds["summary"])
        self.assertIsNotNone(funds["summary"]["today"]["account_status"])

        invalid = self.client.post(
            "/api/holdings",
            json={"code": "000001", "name": "无效日期", "amount": 1, "amount_nav_date": "09-07"},
        )
        self.assertEqual(invalid.status_code, 400)

        self.client.put(
            f"/api/holdings/{holding_id}",
            json={"code": "110011", "name": "测试基金", "amount": 12000},
        )
        cleared = self.storage.get_holding(holding_id)
        self.assertIsNone(cleared["amount_nav_date"])

    def test_holding_json_rejects_invalid_numeric_values(self) -> None:
        negative = self.client.post(
            "/api/holdings",
            json={"code": "110011", "name": "测试基金", "amount": -1},
        )
        self.assertEqual(negative.status_code, 400)
        non_finite = self.client.post(
            "/api/holdings",
            content='{"code":"110011","name":"测试基金","amount":1e999}',
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(non_finite.status_code, 400)
        removed_field = self.client.post(
            "/api/holdings",
            json={"code": "110011", "name": "测试基金", "amount": 1, "dca_status": True},
        )
        self.assertEqual(removed_field.status_code, 400)

    def test_refresh_keeps_successful_official_and_estimate_lanes(self) -> None:
        for code in ("000001", "000002"):
            self.storage.add_holding({"code": code, "name": code, "amount": 1000})
        response = self.client.post("/api/funds/refresh")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual((payload["success"], payload["failure"]), (1, 1))
        self.assertEqual((payload["estimate_success"], payload["estimate_failure"]), (1, 1))
        self.assertIn("000001", self.storage.get_fund_market_snapshots_by_code())
        self.assertIn("000002", self.storage.get_fund_intraday_estimates_by_code())
        self.assertEqual(self.estimate_provider.calls, [["000001", "000002"]])
        estimate_source = next(
            source
            for source in payload["monitor"]["data_freshness"]["sources"]
            if source["job_type"] == "fund_intraday_estimate"
        )
        self.assertEqual(estimate_source["source_key"], "fund_intraday_composite")
        self.assertTrue(payload["has_failure"])
        rows = {row["code"]: row for row in payload["monitor"]["rows"]}
        self.assertEqual(rows["000001"]["estimated_unavailable_reason"], "estimate failed")
        self.assertIsNone(rows["000002"]["estimated_unavailable_reason"])
        self.assertEqual(rows["000002"]["nav_update_status"], "source_failed")

    def test_old_estimate_and_imported_profit_do_not_pollute_estimated_values(self) -> None:
        self.storage.add_holding(
            {
                "code": "110011",
                "name": "测试基金",
                "amount": 1000,
                "daily_profit": 12.5,
            }
        )
        self.storage.save_fund_intraday_estimate(estimate("110011", estimated_date="2026-05-01"))

        payload = self.client.get("/api/funds").json()
        row = payload["rows"][0]
        self.assertIsNone(row["estimated_nav"])
        self.assertIsNone(row["estimated_return_pct"])
        self.assertIsNone(row["estimated_daily_profit"])
        self.assertEqual(row["estimated_status"], "stale")
        self.assertIn("不是今日", row["estimated_warning"])
        self.assertEqual(row["daily_profit"], 12.5)
        self.assertIsNone(payload["summary"]["total_estimated_daily_profit"])
        self.assertIsNone(payload["summary"]["latest_intraday_refresh"])

    def test_current_estimate_drives_only_estimated_profit_and_summary(self) -> None:
        self.storage.add_holding(
            {
                "code": "110011",
                "name": "测试基金",
                "amount": 1000,
                "daily_profit": 12.5,
            }
        )
        self.storage.save_fund_intraday_estimate(estimate("110011"))

        payload = self.client.get("/api/funds").json()
        row = payload["rows"][0]
        self.assertEqual(row["daily_profit"], 12.5)
        self.assertEqual(row["estimated_daily_profit"], 10.0)
        self.assertEqual(payload["summary"]["total_estimated_daily_profit"], 10.0)
        self.assertIsNotNone(payload["summary"]["latest_intraday_refresh"])

    def test_web_csv_import_routes_are_removed(self) -> None:
        self.assertEqual(self.client.post("/api/import").status_code, 404)
        self.assertEqual(self.client.post("/api/import/preview").status_code, 404)

    def test_funds_search_returns_official_name_for_code(self) -> None:
        response = self.client.get("/api/funds/search", params={"q": "110011"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "query": "110011",
                "items": [{"code": "110011", "name": "易方达沪深300ETF联接A"}],
            },
        )

    def test_funds_search_returns_empty_items_when_not_found(self) -> None:
        response = self.client.get("/api/funds/search", params={"q": "999999"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"query": "999999", "items": []})

    def test_funds_search_rejects_blank_query(self) -> None:
        response = self.client.get("/api/funds/search", params={"q": "   "})
        self.assertEqual(response.status_code, 400)

    def test_funds_search_maps_upstream_failure_to_502(self) -> None:
        from fund_analysis.fund_data import FundDataFetchError

        def failing_search(_keyword: str, limit: int = 8):
            raise FundDataFetchError("failed to fetch fund search")

        client = TestClient(
            create_app(
                storage=self.storage,
                market_provider=FakeMarketProvider(),
                estimate_provider=FakeEstimateProvider(),
                market_quote_provider=FakeMarketQuoteProvider(),
                curve_provider=FakeCurveProvider(),
                fund_searcher=failing_search,
            )
        )
        try:
            response = client.get("/api/funds/search", params={"q": "110011"})
            self.assertEqual(response.status_code, 502)
            self.assertIn("failed to fetch", response.json()["detail"])
        finally:
            client.close()


class AccuracyApiTest(unittest.TestCase):
    """验证 accuracy 字段随 /api/funds 响应下发，且刷新后写入每日样本。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = LocalStorage(Path(self.temp_dir.name) / "test.db")
        self.storage.add_holding(
            {
                "code": "110011",
                "name": "易方达蓝筹精选",
                "amount": 10000,
                "asset_class": "A股",
            }
        )
        self.storage.upsert_fund_metadata(
            {
                "code": "110011",
                "name": "易方达蓝筹精选",
                "fund_type": "domestic_hybrid",
                "fund_type_label": "偏股混合型",
                "market": "CN",
                "calendar": "CN_STOCK",
                "nav_disclosure_rule": "cn_same_day_night",
            }
        )
        self.client = TestClient(
            create_app(
                storage=self.storage,
                market_provider=FakeMarketProvider(),
                estimate_provider=FakeEstimateProvider(),
                market_quote_provider=FakeMarketQuoteProvider(),
                curve_provider=FakeCurveProvider(),
            )
        )

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def test_funds_response_rows_include_accuracy_field(self) -> None:
        response = self.client.get("/api/funds")
        self.assertEqual(response.status_code, 200)
        rows = response.json()["rows"]
        self.assertEqual(len(rows), 1)
        # accuracy 字段必须存在（新安装时为 null）
        self.assertIn("accuracy", rows[0])
        self.assertIsNone(rows[0]["accuracy"])

    def test_refresh_saves_daily_sample(self) -> None:
        response = self.client.post("/api/funds/refresh")
        self.assertEqual(response.status_code, 200)
        import sqlite3
        with sqlite3.connect(self.storage.db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM fund_estimate_daily_accuracy WHERE code = '110011'"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_funds_accuracy_pending_when_official_nav_unmatched(self) -> None:
        refresh = self.client.post("/api/funds/refresh")
        self.assertEqual(refresh.status_code, 200)
        row = self.client.get("/api/funds").json()["rows"][0]
        accuracy = row["accuracy"]
        self.assertIsNotNone(accuracy)
        self.assertEqual(accuracy["source"], "eastmoney_fund_core_diy")
        self.assertEqual(accuracy["sample_count"], 0)
        self.assertEqual(accuracy["pending_count"], 1)
        self.assertIsNone(accuracy["mae_pct_points"])

    def test_refresh_idempotent_same_day(self) -> None:
        """同一天多次刷新不会产生重复样本。"""
        self.client.post("/api/funds/refresh")
        self.client.post("/api/funds/refresh")
        import sqlite3
        with sqlite3.connect(self.storage.db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM fund_estimate_daily_accuracy WHERE code = '110011'"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_refresh_rejects_unverified_fixture_basis(self) -> None:
        today = date.today().isoformat()
        with TestClient(
            create_app(
                storage=self.storage,
                market_provider=FakeMarketProvider(nav_date=today),
                estimate_provider=FakeEstimateProvider(estimated_time="16:00:00"),
                market_quote_provider=FakeMarketQuoteProvider(),
                curve_provider=FakeCurveProvider(),
            )
        ) as client:
            response = client.post("/api/funds/refresh")
        self.assertEqual(response.status_code, 200)
        import sqlite3
        with sqlite3.connect(self.storage.db_path) as conn:
            row = conn.execute(
                "SELECT error_pct_points, reconciled_at FROM fund_estimate_daily_accuracy"
            ).fetchone()
        self.assertIsNone(row[0])
        self.assertIsNone(row[1])

    def test_funds_api_includes_metadata_and_nav_update_status(self) -> None:
        # 新增持仓：024481（境内混合）与 016702（QDII）与现金
        self.client.post(
            "/api/holdings",
            json={"code": "024481", "name": "财通品质甄选混合C", "amount": 1000},
        )
        self.client.post(
            "/api/holdings",
            json={"code": "015060", "name": "华夏节能环保股票C", "amount": 1500},
        )
        self.client.post(
            "/api/holdings",
            json={"code": "016702", "name": "银华海外数字经济量化选股混合发起式(QDII)C", "amount": 2000},
        )
        self.client.post(
            "/api/holdings",
            json={"code": "CASH:TEST", "name": "现金", "amount": 500, "asset_type": "cash"},
        )
        res = self.client.get("/api/funds")
        self.assertEqual(res.status_code, 200)
        rows_by_code = {row["code"]: row for row in res.json()["rows"]}

        # 024481 检查
        row_cn = rows_by_code["024481"]
        self.assertIn("nav_update_status", row_cn)
        self.assertIn(row_cn["nav_update_status"], {"updated", "pending", "overdue", "market_closed"})
        self.assertEqual(row_cn["valuation_market"], "CN")
        self.assertEqual(row_cn["fund_type"], "domestic_hybrid")
        self.assertEqual(row_cn["fund_type_label"], "偏股混合型")
        self.assertEqual(row_cn["nav_disclosure_rule"], "cn_same_day_night")
        self.assertIsNotNone(row_cn["benchmark_name"])
        self.assertIsNone(row_cn["benchmark_code"])
        row_env = rows_by_code["015060"]
        self.assertEqual(row_env["benchmark_code"], "000827")
        self.assertIn("in_disclosure_window", row_cn)
        self.assertIsInstance(row_cn["in_disclosure_window"], bool)
        self.assertIn("auto_refresh_eligible", row_cn)
        self.assertIsInstance(row_cn["auto_refresh_eligible"], bool)

        # 016702 QDII 检查
        row_qdii = rows_by_code["016702"]
        self.assertEqual(row_qdii["valuation_market"], "US")
        self.assertEqual(row_qdii["fund_type"], "qdii_hybrid")
        self.assertEqual(row_qdii["nav_disclosure_rule"], "qdii_next_day_night")
        self.assertIsNone(row_qdii["benchmark_code"])

        # CASH 检查
        row_cash = rows_by_code["CASH:TEST"]
        self.assertEqual(row_cash["nav_update_status"], "updated")
        self.assertTrue(row_cash["is_cash_asset"])
        self.assertFalse(row_cash["in_disclosure_window"])
        self.assertFalse(row_cash["auto_refresh_eligible"])

    def test_funds_payload_includes_current_holdings_portfolio_analysis(self) -> None:
        self.client.post(
            "/api/holdings",
            json={"code": "024481", "name": "财通品质甄选混合C", "amount": 4000, "theme": "成长"},
        )
        self.client.post(
            "/api/holdings",
            json={"code": "016702", "name": "银华海外数字经济量化选股混合发起式(QDII)C", "amount": 1000, "theme": "科技"},
        )
        payload = self.client.get("/api/funds").json()["portfolio_analysis"]
        self.assertEqual(payload["schema_version"], "portfolio_analysis.v1")
        self.assertFalse(payload["basis"]["look_through"])
        self.assertFalse(payload["basis"]["has_quarterly_holdings"])
        asset_items = {item["key"]: item for item in payload["distribution"]["asset_class"]["items"]}
        self.assertEqual(set(asset_items), {"domestic_hybrid", "qdii_hybrid"})
        self.assertEqual(asset_items["qdii_hybrid"]["weight_pct"], 6.6667)
        self.assertNotIn("成长", {item["label"] for item in asset_items.values()})
        self.assertNotIn("A股", {item["label"] for item in asset_items.values()})
        self.assertFalse(payload["distribution"]["industry"]["available"])
        self.assertFalse(payload["distribution"]["currency"]["available"])
        self.assertFalse(payload["overlap"]["available"])
        self.assertEqual(payload["contribution"]["basis"], "today_profit_included")
        self.assertIsNone(payload["contribution"]["total_estimated_profit"])
        self.assertEqual(payload["concentration"]["holdings"][0]["code"], "110011")
        self.assertEqual(payload["concentration"]["max_weight_pct"], 66.6667)

    def test_portfolio_risk_uses_official_nav_and_rejects_bad_range(self) -> None:
        self.client.post(
            "/api/holdings",
            json={"code": "024481", "name": "财通品质甄选混合C", "amount": 1000},
        )
        invalid = self.client.get("/api/funds/portfolio-risk?range=bad")
        self.assertEqual(invalid.status_code, 400)
        payload = self.client.get("/api/funds/portfolio-risk?range=1y").json()
        self.assertEqual(payload["schema_version"], "portfolio_risk.v1")
        self.assertEqual(payload["range"], "1y")
        self.assertFalse(payload["basis"]["personal_return"])
        self.assertFalse(payload["basis"]["advice"])
        funds_by_code = {item["code"]: item for item in payload["funds"]}
        self.assertIn("024481", funds_by_code)
        self.assertIsNone(funds_by_code["024481"]["volatility_pct"])
        self.assertNotIn("recommendation", payload)


class OfficialDailyProfitApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage = LocalStorage(Path(self.temp_dir.name) / "test.db")
        self.holding_id = self.storage.add_holding(
            {
                "code": "110011",
                "name": "易方达蓝筹精选",
                "amount": 10000,
            }
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _client(
        self,
        *,
        market_provider: FakeMarketProvider | None = None,
        estimate_provider: FakeEstimateProvider | None = None,
    ) -> TestClient:
        return TestClient(
            create_app(
                storage=self.storage,
                market_provider=market_provider or FakeMarketProvider(),
                estimate_provider=estimate_provider or FakeEstimateProvider(),
                market_quote_provider=FakeMarketQuoteProvider(),
                curve_provider=FakeCurveProvider(),
            )
        )

    def test_empty_official_profit_contract(self) -> None:
        with self._client() as client:
            response = client.get("/api/funds/110011/official-profit")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schema_version"], "fund_official_daily_profit.v1")
        self.assertEqual(payload["code"], "110011")
        self.assertEqual(payload["record_count"], 0)
        self.assertIsNone(payload["start_date"])
        self.assertIsNone(payload["end_date"])
        self.assertIsNone(payload["latest"])
        self.assertEqual(payload["cumulative_profit"], 0.0)
        self.assertEqual(payload["items"], [])
        self.assertEqual(payload["cumulative_kind"], "recorded_dates_sum")
        self.assertEqual(payload["observed_dates"], [])
        self.assertEqual(payload["completeness"], "recorded_dates_only")

    def test_unknown_code_returns_empty_contract_not_404(self) -> None:
        with self._client() as client:
            response = client.get("/api/funds/999999/official-profit")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["record_count"], 0)
        self.assertIsNone(response.json()["latest"])

    def test_refresh_writes_first_official_day(self) -> None:
        with self._client(
            market_provider=FakeMarketProvider(daily_return_pct=0.0766, latest_nav=1.2345)
        ) as client:
            refresh = client.post("/api/funds/refresh")
            payload = client.get("/api/funds/110011/official-profit").json()
        self.assertEqual(refresh.status_code, 200)
        self.assertEqual(payload["record_count"], 1)
        self.assertEqual(payload["start_date"], "2026-09-01")
        self.assertEqual(payload["end_date"], "2026-09-01")
        self.assertEqual(payload["latest"], {"nav_date": "2026-09-01", "profit_amount": 7.66})
        self.assertEqual(payload["cumulative_profit"], 7.66)
        item = payload["items"][0]
        self.assertEqual(item["nav_date"], "2026-09-01")
        self.assertEqual(item["holding_amount"], 10000.0)
        self.assertEqual(item["official_nav"], 1.2345)
        self.assertEqual(item["official_return_pct"], 0.0766)
        self.assertEqual(item["profit_amount"], 7.66)
        self.assertEqual(item["cumulative_profit"], 7.66)

    def test_repeat_refresh_does_not_add_same_day_record(self) -> None:
        with self._client() as client:
            client.post("/api/funds/refresh")
            client.post("/api/funds/refresh")
            payload = client.get("/api/funds/110011/official-profit").json()
        self.assertEqual(payload["record_count"], 1)

    def test_holding_change_does_not_override_locked_same_day_amount(self) -> None:
        with self._client(market_provider=FakeMarketProvider(daily_return_pct=1.0)) as client:
            client.post("/api/funds/refresh")
            client.put(
                f"/api/holdings/{self.holding_id}",
                json={"code": "110011", "name": "易方达蓝筹精选", "amount": 20000},
            )
            client.post("/api/funds/refresh")
            payload = client.get("/api/funds/110011/official-profit").json()
        item = payload["items"][0]
        self.assertEqual(item["holding_amount"], 10000.0)
        self.assertEqual(item["profit_amount"], 100.0)

    def test_official_correction_uses_locked_amount(self) -> None:
        provider = FakeMarketProvider(daily_return_pct=1.0, latest_nav=1.2)
        with self._client(market_provider=provider) as client:
            client.post("/api/funds/refresh")
            provider.daily_return_pct = 1.5
            provider.latest_nav = 1.21
            client.put(
                f"/api/holdings/{self.holding_id}",
                json={"code": "110011", "name": "易方达蓝筹精选", "amount": 20000},
            )
            client.post("/api/funds/refresh")
            payload = client.get("/api/funds/110011/official-profit").json()
        item = payload["items"][0]
        self.assertEqual(payload["record_count"], 1)
        self.assertEqual(item["holding_amount"], 10000.0)
        self.assertEqual(item["official_nav"], 1.21)
        self.assertEqual(item["official_return_pct"], 1.5)
        self.assertEqual(item["profit_amount"], 150.0)

    def test_missing_amount_or_official_fields_do_not_write(self) -> None:
        self.storage.update_holding(
            self.holding_id,
            {"code": "110011", "name": "易方达蓝筹精选", "amount": None},
        )
        with self._client() as client:
            client.post("/api/funds/refresh")
            empty_amount = client.get("/api/funds/110011/official-profit").json()
        self.assertEqual(empty_amount["record_count"], 0)

        self.storage.update_holding(
            self.holding_id,
            {"code": "110011", "name": "易方达蓝筹精选", "amount": 10000},
        )
        with self._client(market_provider=FakeMarketProvider(nav_date=None)) as client:
            client.post("/api/funds/refresh")
            missing_date = client.get("/api/funds/110011/official-profit").json()
        self.assertEqual(missing_date["record_count"], 0)

        with self._client(market_provider=FakeMarketProvider(daily_return_pct=None)) as client:
            client.post("/api/funds/refresh")
            missing_return = client.get("/api/funds/110011/official-profit").json()
        self.assertEqual(missing_return["record_count"], 0)

    def test_estimate_success_without_official_fields_does_not_write_profit(self) -> None:
        with self._client(
            market_provider=FakeMarketProvider(nav_date=None, daily_return_pct=None)
        ) as client:
            refresh = client.post("/api/funds/refresh")
            payload = client.get("/api/funds/110011/official-profit").json()
        self.assertEqual(refresh.status_code, 200)
        self.assertEqual(refresh.json()["estimate_success"], 1)
        self.assertEqual(payload["record_count"], 0)

    def test_official_success_writes_when_estimate_fails(self) -> None:
        with self._client(
            market_provider=FakeMarketProvider(daily_return_pct=1.0),
            estimate_provider=FakeEstimateProvider("110011"),
        ) as client:
            refresh = client.post("/api/funds/refresh")
            payload = client.get("/api/funds/110011/official-profit").json()
        self.assertEqual(refresh.json()["success"], 1)
        self.assertEqual(refresh.json()["estimate_failure"], 1)
        self.assertEqual(payload["record_count"], 1)
        self.assertEqual(payload["items"][0]["profit_amount"], 100.0)

    def test_items_are_ascending_and_cumulative_is_server_computed(self) -> None:
        self.storage.record_official_daily_profit(
            market_data("110011", nav_date="2026-09-04", daily_return_pct=0.1234),
            10000,
        )
        self.storage.record_official_daily_profit(
            market_data("110011", nav_date="2026-09-03", daily_return_pct=0.0766),
            10000,
        )
        with self._client() as client:
            payload = client.get("/api/funds/110011/official-profit").json()
        self.assertEqual(payload["record_count"], 2)
        self.assertEqual(
            [item["nav_date"] for item in payload["items"]],
            ["2026-09-03", "2026-09-04"],
        )
        self.assertEqual(payload["items"][0]["profit_amount"], 7.66)
        self.assertEqual(payload["items"][0]["cumulative_profit"], 7.66)
        self.assertEqual(payload["items"][1]["profit_amount"], 12.34)
        self.assertEqual(payload["items"][1]["cumulative_profit"], 20.0)
        self.assertEqual(payload["cumulative_profit"], 20.0)
        self.assertEqual(payload["latest"], {"nav_date": "2026-09-04", "profit_amount": 12.34})
        self.assertEqual(payload["start_date"], "2026-09-03")
        self.assertEqual(payload["end_date"], "2026-09-04")

    def test_delete_holding_keeps_profit_records(self) -> None:
        with self._client(market_provider=FakeMarketProvider(daily_return_pct=1.0)) as client:
            client.post("/api/funds/refresh")
            client.delete(f"/api/holdings/{self.holding_id}")
            payload = client.get("/api/funds/110011/official-profit").json()
        self.assertEqual(self.storage.get_all_holdings(), [])
        self.assertEqual(payload["record_count"], 1)
        self.assertEqual(payload["items"][0]["profit_amount"], 100.0)

    def test_funds_payload_includes_display_session_and_session_profit(self) -> None:
        with self._client(market_provider=FakeMarketProvider(daily_return_pct=1.5, nav_date="2026-09-01")) as client:
            client.post("/api/funds/refresh")
            funds = client.get("/api/funds").json()

        today = funds["summary"]["today"]
        self.assertIn("display_session", today)
        ds = today["display_session"]
        expected_ds_keys = {
            "date", "is_today", "mode", "profit", "rate_pct", "count",
            "total", "amount", "official_count", "estimate_count", "updated_at",
        }
        self.assertEqual(set(ds.keys()), expected_ds_keys)

        row = funds["rows"][0]
        self.assertIn("session_profit", row)
        sp = row["session_profit"]
        expected_sp_keys = {"amount", "return_pct", "return_date", "kind", "in_session"}
        self.assertEqual(set(sp.keys()), expected_sp_keys)

        # 验证 count 与行中 in_session 且 amount 非空的数量一致
        in_session_count = sum(
            1 for r in funds["rows"]
            if (r.get("session_profit") or {}).get("in_session")
            and (r.get("session_profit") or {}).get("amount") is not None
        )
        self.assertEqual(in_session_count, ds["count"])

    def test_funds_display_session_prefers_stored_official_profit(self) -> None:
        with self._client(
            market_provider=FakeMarketProvider(daily_return_pct=1.0, nav_date="2026-09-01"),
            estimate_provider=FakeEstimateProvider(estimated_time="10:10:00"),
        ) as client:
            client.post("/api/funds/refresh")

            # 模拟在存储中存在官方收益记录（设置特定数值如 66.66）
            with self.storage._get_conn() as conn:
                conn.execute(
                    "UPDATE fund_official_daily_profit SET profit_amount = 66.66 WHERE code = '110011' AND nav_date = '2026-09-01'"
                )

            # 模拟没有当天估值（清除估值），进入 latest_official 模式
            with self.storage._get_conn() as conn:
                conn.execute("DELETE FROM fund_intraday_estimates")

            funds = client.get("/api/funds").json()
            ds = funds["summary"]["today"]["display_session"]
            self.assertEqual(ds["mode"], "latest_official")
            self.assertEqual(ds["date"], "2026-09-01")
            self.assertEqual(ds["profit"], 66.66)
            row = funds["rows"][0]
            self.assertEqual(row["session_profit"]["amount"], 66.66)
            self.assertTrue(row["session_profit"]["in_session"])

    def test_backfilled_estimate_on_weekend_preserves_source_date_and_stale_status(self) -> None:
        # 周末测试：完全基于 2026-09-26（周六），补拉最近交易日（2026-09-25 15:00）的估值
        fixed_weekend = datetime(2026, 9, 26, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is not None:
                    return fixed_weekend.astimezone(tz)
                return fixed_weekend

        with patch("fund_analysis.web.app.datetime", FixedDateTime), patch(
            "fund_analysis.today_profit.datetime", FixedDateTime
        ):
            with self._client(
                market_provider=FakeMarketProvider(daily_return_pct=1.0, nav_date="2026-09-24"),
                estimate_provider=FakeEstimateProvider(
                    estimated_date="2026-09-25",
                    estimated_time="15:00:00",
                    status="stale",
                ),
            ) as client:
                client.post("/api/funds/refresh")

                # 验证 1：数据库中保存了来源真实日期，且状态为 stale
                with self.storage._get_conn() as conn:
                    row = conn.execute(
                        "SELECT estimated_at, status FROM fund_intraday_estimates WHERE code = '110011'"
                    ).fetchone()
                    self.assertIsNotNone(row)
                    self.assertTrue(str(row["estimated_at"]).startswith("2026-09-25"))
                    self.assertEqual(row["status"], "stale")

                    # 验证 2：迟到报价以真实采集日留证，休市日不产生可对账结果。
                    sample_weekend = conn.execute(
                        "SELECT * FROM fund_estimate_daily_accuracy WHERE code = '110011' AND observed_date = '2026-09-26'"
                    ).fetchone()
                    self.assertIsNotNone(sample_weekend)
                    self.assertEqual(sample_weekend["attribution_reason"], "source_date_unverified")
                    self.assertIsNone(sample_weekend["target_nav_date"])
                    self.assertIsNone(sample_weekend["reconciled_at"])
                    sample_friday = conn.execute(
                        "SELECT * FROM fund_estimate_daily_accuracy WHERE code = '110011' AND observed_date = '2026-09-25'"
                    ).fetchone()
                    self.assertIsNone(sample_friday)

                # 验证 3：/api/funds 接口中，旧估值被标为 stale，不计入今日收益
                payload = client.get("/api/funds").json()
                fund_row = payload["rows"][0]
                self.assertTrue(str(fund_row["estimated_at"]).startswith("2026-09-25"))
                self.assertEqual(fund_row["estimated_status"], "stale")
                self.assertIn("不是今日数据", fund_row["estimated_warning"])
                # 旧值绝不可计入今日收益
                self.assertIsNone(fund_row["today_profit"]["amount"])
                self.assertIsNone(payload["summary"]["today"]["monitoring"]["profit"])
                # 展示日回退为最新官方净值（latest_official），不把预估旧值计入合计
                self.assertEqual(payload["summary"]["today"]["display_session"]["mode"], "latest_official")
                # 验证 4：非交易时段，旧估值不会被错误标记为 auto_refresh_eligible
                self.assertFalse(fund_row["auto_refresh_eligible"])


if __name__ == "__main__":
    unittest.main()
