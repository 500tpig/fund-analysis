from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .trading_calendar import CALENDAR_CN_STOCK, get_latest_trading_day


EASTMONEY_SOURCE_NAME = "eastmoney_fundmobapi"
EASTMONEY_BASIC_URL = "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNNBasicInformation"
EASTMONEY_CORE_SOURCE_NAME = "eastmoney_fund_core_diy"
EASTMONEY_CORE_URL = "https://fundcomapi.eastmoney.com/mm/newCore/FundCoreDiyNew"
TIANTIAN_VALUATION_SOURCE_NAME = "tiantian_fund_valuation_last"
TIANTIAN_VALUATION_URL = (
    "https://fundcomapi.tiantianfunds.com/mm/newCore/FundValuationLast"
)
EASTMONEY_VALUATION_URL = (
    "https://fundcomapi.eastmoney.com/mm/newCore/FundValuationLast"
)
SINA_ESTIMATE_SOURCE_NAME = "sina_fund_estimate"
SINA_ESTIMATE_URL = (
    "https://stock.finance.sina.com.cn/fundInfo/api/openapi.php/"
    "FdFundService.getEstimateNetworthPic"
)
SINA_ESTIMATE_WARNING = "新浪估值基于公开披露持仓计算，可能滞后于基金实际调仓，仅供参考。"
# 新浪分钟点有两组「估值净值 / 涨跌幅」：pre_nav 与 nav_pct、pre_nav2 与 nav2_pct，
# 两组都以 worth（最新官方净值）为基准且各自自洽。接口未公开字段含义，这里固定使用
# 第一组，不跨组拼接；第二组只留档。
SINA_NAV_KEY = "pre_nav"
SINA_RETURN_KEY = "nav_pct"
SINA_POINT_KEYS = ("pre_nav", "nav_pct", "pre_nav2", "nav2_pct", "pre_date", "min_time")
EASTMONEY_HISTORY_URL = "https://api.fund.eastmoney.com/f10/lsjz"
EASTMONEY_PING_URL_TEMPLATE = "https://fund.eastmoney.com/pingzhongdata/{code}.js"
EASTMONEY_INDEX_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EASTMONEY_FUND_SEARCH_URL = (
    "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
)
# pingzhongdata 的 Data_ACWorthTrend 时间戳是北京时间零点，不是 UTC 零点。
CHINA_TZ = timezone(timedelta(hours=8))
SINA_INDEX_KLINE_URL = (
    "https://quotes.sina.cn/cn/api/openapi.php/CN_MarketDataService.getKLineData"
)
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class PerformanceBenchmark:
    code: str
    name: str
    eastmoney_secid: str
    sina_symbol: str


PERFORMANCE_BENCHMARKS: dict[str, PerformanceBenchmark] = {
    "000300": PerformanceBenchmark("000300", "沪深300", "1.000300", "sh000300"),
    "399006": PerformanceBenchmark("399006", "创业板指", "0.399006", "sz399006"),
    "000001": PerformanceBenchmark("000001", "上证指数", "1.000001", "sh000001"),
    "399001": PerformanceBenchmark("399001", "深证成指", "0.399001", "sz399001"),
    "000012": PerformanceBenchmark("000012", "国债指数", "1.000012", "sh000012"),
    "000827": PerformanceBenchmark("000827", "中证环保", "1.000827", "sh000827"),
}
DEFAULT_PERFORMANCE_BENCHMARK = "000300"

JsonGetter = Callable[[str, Mapping[str, str]], Mapping[str, Any]]
TextGetter = Callable[[str, Mapping[str, str]], str]


class FundDataFetchError(RuntimeError):
    """Raised when public fund data cannot be fetched or parsed."""


@dataclass(frozen=True)
class FundSearchHit:
    code: str
    name: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "name": self.name}


@dataclass(frozen=True)
class FundMarketData:
    code: str
    name: str | None
    nav_date: str | None
    latest_nav: float | None
    daily_return_pct: float | None
    source: str
    source_urls: list[str]
    fetched_at: str
    warnings: list[dict[str, str]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "nav_date": self.nav_date,
            "latest_nav": self.latest_nav,
            "daily_return_pct": self.daily_return_pct,
            "source": self.source,
            "source_urls": list(self.source_urls),
            "fetched_at": self.fetched_at,
            "warnings": [dict(item) for item in self.warnings],
        }


@dataclass(frozen=True)
class FundIntradayEstimate:
    code: str
    name: str | None
    previous_nav: float | None
    previous_nav_date: str | None
    estimated_nav: float
    estimated_return_pct: float
    estimated_at: str
    source: str
    source_url: str
    fetched_at: str
    status: str
    warnings: list[dict[str, str]]
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "previous_nav": self.previous_nav,
            "previous_nav_date": self.previous_nav_date,
            "estimated_nav": self.estimated_nav,
            "estimated_return_pct": self.estimated_return_pct,
            "estimated_at": self.estimated_at,
            "source": self.source,
            "source_url": self.source_url,
            "fetched_at": self.fetched_at,
            "status": self.status,
            "warnings": [dict(item) for item in self.warnings],
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class FundEstimateBatch:
    estimates: dict[str, FundIntradayEstimate]
    failures: dict[str, str]
    evidence: dict[str, Any]


class EastmoneyFundDataProvider:
    """Fetch the latest official NAV snapshot with one lightweight request."""

    name = EASTMONEY_SOURCE_NAME

    def __init__(self, timeout: float = 8.0, json_getter: JsonGetter | None = None) -> None:
        self.timeout = timeout
        self._json_getter = json_getter

    def fetch(self, code: str) -> FundMarketData:
        return self.fetch_without_quarterly_holdings(code)

    def fetch_without_quarterly_holdings(self, code: str) -> FundMarketData:
        normalized = code.strip()
        if not normalized:
            raise FundDataFetchError("fund code must not be empty")
        payload = self._get_json(EASTMONEY_BASIC_URL, _eastmoney_basic_params(normalized))
        data = payload.get("Datas")
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, Mapping) or not data:
            raise FundDataFetchError(f"{normalized}: missing basic fund data")
        return FundMarketData(
            code=str(data.get("FCODE") or normalized),
            name=_text_or_none(data.get("SHORTNAME")),
            nav_date=_text_or_none(data.get("FSRQ")),
            latest_nav=_float_or_none(data.get("DWJZ")),
            daily_return_pct=_float_or_none(data.get("RZDF")),
            source=EASTMONEY_SOURCE_NAME,
            source_urls=[EASTMONEY_BASIC_URL],
            fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            warnings=[],
        )

    def _get_json(self, url: str, params: Mapping[str, str]) -> Mapping[str, Any]:
        if self._json_getter is not None:
            return self._json_getter(url, params)
        request = Request(
            f"{url}?{urlencode(params)}",
            headers={"User-Agent": "fund-valuation/0.1", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise FundDataFetchError(f"failed to fetch {url}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise FundDataFetchError(f"unexpected JSON payload from {url}")
        return payload


def search_public_funds(
    keyword: str,
    limit: int = 8,
    *,
    timeout: float = 8.0,
    json_getter: JsonGetter | None = None,
) -> list[FundSearchHit]:
    """Search official fund code and name via Eastmoney FundSearchAPI."""
    key = keyword.strip()
    if not key:
        return []
    size = max(1, min(int(limit or 8), 20))
    params = {"m": "1", "key": key}
    if json_getter is not None:
        try:
            payload = json_getter(EASTMONEY_FUND_SEARCH_URL, params)
        except Exception as exc:
            raise FundDataFetchError(
                f"failed to fetch {EASTMONEY_FUND_SEARCH_URL}: {exc}"
            ) from exc
    else:
        request = Request(
            f"{EASTMONEY_FUND_SEARCH_URL}?{urlencode(params)}",
            headers={
                "User-Agent": BROWSER_USER_AGENT,
                "Accept": "application/json",
                "Referer": "https://fund.eastmoney.com/",
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise FundDataFetchError(
                f"failed to fetch {EASTMONEY_FUND_SEARCH_URL}: {exc}"
            ) from exc
    if not isinstance(payload, Mapping):
        raise FundDataFetchError(
            f"unexpected JSON payload from {EASTMONEY_FUND_SEARCH_URL}"
        )
    datas = payload.get("Datas")
    if not isinstance(datas, list):
        raise FundDataFetchError(
            f"unexpected fund search payload from {EASTMONEY_FUND_SEARCH_URL}"
        )
    hits: list[FundSearchHit] = []
    seen: set[str] = set()
    for row in datas:
        if not isinstance(row, Mapping):
            continue
        code = re.sub(r"\D", "", str(row.get("CODE") or "").strip())
        name = str(row.get("NAME") or "").strip()
        if len(code) != 6 or not name or code in seen:
            continue
        seen.add(code)
        hits.append(FundSearchHit(code=code, name=name))
        if len(hits) >= size:
            break
    return hits


class CompositeFundEstimateProvider:
    """Fetch current intraday estimates in one batch, then fill gaps with Sina."""

    name = "fund_intraday_composite"

    def __init__(
        self,
        batch_timeout: float = 6.0,
        sina_timeout: float = 5.0,
        json_getter: JsonGetter | None = None,
        today: date | None = None,
    ) -> None:
        self.batch_timeout = batch_timeout
        self.sina_timeout = sina_timeout
        self._json_getter = json_getter
        self._today = today

    def fetch_many(self, codes: list[str]) -> FundEstimateBatch:
        normalized_codes = list(dict.fromkeys(code.strip() for code in codes if code.strip()))
        successes = {
            EASTMONEY_CORE_SOURCE_NAME: [],
            TIANTIAN_VALUATION_SOURCE_NAME: [],
            SINA_ESTIMATE_SOURCE_NAME: [],
        }
        evidence: dict[str, Any] = {
            "primary_source": EASTMONEY_CORE_SOURCE_NAME,
            "core_hard_failed": False,
            "success_codes_by_source": successes,
            "failed_codes": [],
        }
        if not normalized_codes:
            return FundEstimateBatch({}, {}, evidence)

        today = self._today or date.today()
        estimates: dict[str, FundIntradayEstimate] = {}
        missing_reasons: dict[str, list[str]] = {code: [] for code in normalized_codes}
        params = _core_params(normalized_codes)
        try:
            core_payload = self._get_json(EASTMONEY_CORE_URL, params, self.batch_timeout)
            core_rows = _batch_rows(core_payload, "Core", require_non_empty=True)
        except FundDataFetchError as exc:
            evidence["core_hard_failed"] = True
            for code in normalized_codes:
                missing_reasons[code].append(f"Core 整体失败：{exc}")
            self._fill_from_valuation(
                normalized_codes, estimates, missing_reasons, successes, today
            )
        else:
            self._fill_from_rows(
                normalized_codes,
                core_rows,
                estimates,
                missing_reasons,
                successes[EASTMONEY_CORE_SOURCE_NAME],
                source=EASTMONEY_CORE_SOURCE_NAME,
                source_url=f"{EASTMONEY_CORE_URL}?{urlencode(params)}",
                today=today,
                previous_nav_key="DWJZ",
                previous_nav_fallback="NAV",
                previous_date_key="FSRQ",
                previous_date_fallback="JZRQ",
                code_fallback="QDCODE",
            )

        target_trade_day: date | None = None
        try:
            target_trade_day = get_latest_trading_day(CALENDAR_CN_STOCK, today, include_today=True)
        except ValueError:
            target_trade_day = None

        stale_codes: list[str] = []
        if target_trade_day is not None:
            for code in normalized_codes:
                if code in estimates:
                    est_d = _date_prefix(estimates[code].estimated_at)
                    if est_d is not None and est_d < target_trade_day:
                        stale_codes.append(code)

        missing_codes = [code for code in normalized_codes if code not in estimates]
        sina_query_codes = list(dict.fromkeys(missing_codes + stale_codes))
        if sina_query_codes:
            sina_estimates: dict[str, FundIntradayEstimate] = {}
            self._fill_from_sina(
                sina_query_codes,
                sina_estimates,
                missing_reasons,
                [],
                today,
            )
            for code in sina_query_codes:
                sina_est = sina_estimates.get(code)
                if sina_est is None:
                    continue
                if code not in estimates:
                    estimates[code] = sina_est
                    successes[SINA_ESTIMATE_SOURCE_NAME].append(code)
                else:
                    existing_est = estimates[code]
                    sina_dt = _parse_estimate_datetime(sina_est.estimated_at)
                    existing_dt = _parse_estimate_datetime(existing_est.estimated_at)
                    if sina_dt is not None and existing_dt is not None and sina_dt > existing_dt:
                        estimates[code] = sina_est
                        if code in successes.get(existing_est.source, []):
                            successes[existing_est.source].remove(code)
                        successes[SINA_ESTIMATE_SOURCE_NAME].append(code)
                    else:
                        missing_reasons[code].append(
                            f"新浪估值时间（{sina_est.estimated_at}）未更新于现有估值时间（{existing_est.estimated_at}），保留原估值"
                        )
        failures = {
            code: "; ".join(missing_reasons[code]) or "所有盘中估值来源均无可用当日数据"
            for code in normalized_codes
            if code not in estimates
        }
        evidence["failed_codes"] = list(failures)
        return FundEstimateBatch(estimates, failures, evidence)

    def _fill_from_valuation(
        self,
        codes: list[str],
        estimates: dict[str, FundIntradayEstimate],
        missing_reasons: dict[str, list[str]],
        successes: dict[str, list[str]],
        today: date,
    ) -> None:
        params = _valuation_params(codes)
        try:
            try:
                payload = self._get_json(TIANTIAN_VALUATION_URL, params, self.batch_timeout)
                source_url = TIANTIAN_VALUATION_URL
            except FundDataFetchError:
                payload = self._get_json(EASTMONEY_VALUATION_URL, params, self.batch_timeout)
                source_url = EASTMONEY_VALUATION_URL
            rows = _batch_rows(payload, "FundValuationLast")
        except FundDataFetchError as exc:
            for code in codes:
                missing_reasons[code].append(f"FundValuationLast 整体失败：{exc}")
            return
        self._fill_from_rows(
            codes,
            rows,
            estimates,
            missing_reasons,
            successes[TIANTIAN_VALUATION_SOURCE_NAME],
            source=TIANTIAN_VALUATION_SOURCE_NAME,
            source_url=f"{source_url}?{urlencode(params)}",
            today=today,
            previous_nav_key="NAV",
            previous_date_key="PDATE",
        )

    @staticmethod
    def _fill_from_rows(
        codes: list[str],
        rows: list[Any],
        estimates: dict[str, FundIntradayEstimate],
        missing_reasons: dict[str, list[str]],
        success_codes: list[str],
        *,
        source: str,
        source_url: str,
        today: date,
        previous_nav_key: str,
        previous_date_key: str,
        previous_nav_fallback: str | None = None,
        previous_date_fallback: str | None = None,
        code_fallback: str | None = None,
    ) -> None:
        rows_by_code: dict[str, Mapping[str, Any]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            code = _text_or_none(row.get("FCODE"))
            if code is None and code_fallback:
                code = _text_or_none(row.get(code_fallback))
            if code in codes:
                rows_by_code[code] = row
        for code in codes:
            if code in estimates:
                continue
            row = rows_by_code.get(code)
            if row is None:
                missing_reasons[code].append(f"{source} 未返回该基金")
                continue
            estimated_nav = _float_or_none(row.get("GSZ"))
            estimated_return_pct = _float_or_none(row.get("GSZZL"))
            estimated_at = _text_or_none(row.get("GZTIME"))
            if estimated_nav is None or estimated_return_pct is None or estimated_at is None:
                missing_reasons[code].append(f"{source} 缺少完整估值字段")
                continue
            estimate_date = _date_prefix(estimated_at)
            if estimate_date is None:
                missing_reasons[code].append(f"{source} 估值日期无效")
                continue
            if estimate_date > today:
                missing_reasons[code].append(f"{source} 估值日期异常（未来日期）")
                continue
            previous_nav = _float_or_none(row.get(previous_nav_key))
            if previous_nav is None and previous_nav_fallback:
                previous_nav = _float_or_none(row.get(previous_nav_fallback))
            previous_date = _text_or_none(row.get(previous_date_key))
            if previous_date is None and previous_date_fallback:
                previous_date = _text_or_none(row.get(previous_date_fallback))
            selected_payload = {
                key: row.get(key)
                for key in (
                    "FCODE", "QDCODE", "SHORTNAME", previous_nav_key,
                    previous_nav_fallback, previous_date_key, previous_date_fallback,
                    "GSZ", "GSZZL", "GZTIME",
                )
                if key and key in row
            }
            status = "available" if estimate_date == today else "stale"
            estimates[code] = FundIntradayEstimate(
                code=code,
                name=_text_or_none(row.get("SHORTNAME")),
                previous_nav=previous_nav,
                previous_nav_date=previous_date,
                estimated_nav=estimated_nav,
                estimated_return_pct=estimated_return_pct,
                estimated_at=estimated_at,
                source=source,
                source_url=source_url,
                fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                status=status,
                warnings=_estimate_basis_warnings(
                    previous_nav=previous_nav,
                    previous_nav_date=previous_date,
                    estimated_nav=estimated_nav,
                    estimated_return_pct=estimated_return_pct,
                    estimated_at=estimated_at,
                    source=source,
                ),
                payload=selected_payload,
            )
            success_codes.append(code)

    def _fill_from_sina(
        self,
        codes: list[str],
        estimates: dict[str, FundIntradayEstimate],
        missing_reasons: dict[str, list[str]],
        success_codes: list[str],
        today: date,
    ) -> None:
        def fetch(code: str) -> tuple[str, FundIntradayEstimate | None, str | None]:
            params = {"symbol": code}
            try:
                payload = self._get_json(SINA_ESTIMATE_URL, params, self.sina_timeout)
                item = _parse_sina_estimate(
                    code,
                    payload,
                    f"{SINA_ESTIMATE_URL}?{urlencode(params)}",
                    today=today,
                )
                return code, item, None
            except FundDataFetchError as exc:
                return code, None, str(exc)

        with ThreadPoolExecutor(max_workers=min(4, len(codes))) as executor:
            for code, item, error in executor.map(fetch, codes):
                if item is not None:
                    estimates[code] = item
                    success_codes.append(code)
                else:
                    missing_reasons[code].append(f"新浪估值失败：{error}")

    def _get_json(
        self,
        url: str,
        params: Mapping[str, str],
        timeout: float,
    ) -> Mapping[str, Any]:
        if self._json_getter is not None:
            try:
                payload = self._json_getter(url, params)
            except Exception as exc:
                raise FundDataFetchError(f"failed to fetch {url}: {exc}") from exc
        else:
            request = Request(
                f"{url}?{urlencode(params)}",
                headers={
                    "User-Agent": "fund-valuation/0.1",
                    "Accept": "application/json",
                    "Referer": "https://fund.eastmoney.com/",
                },
            )
            try:
                with urlopen(request, timeout=timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                raise FundDataFetchError(f"failed to fetch {url}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise FundDataFetchError(f"unexpected JSON payload from {url}")
        return payload


class FundCurveProvider:
    """Fetch on-demand fund history and intraday curve data without persistence."""

    _PERIOD_DAYS = {"1m": 31, "3m": 93, "6m": 186, "1y": 366, "3y": 1096}

    def __init__(
        self,
        timeout: float = 8.0,
        json_getter: JsonGetter | None = None,
        text_getter: TextGetter | None = None,
        today: date | None = None,
    ) -> None:
        self.timeout = timeout
        self._json_getter = json_getter
        self._text_getter = text_getter
        self._today = today

    def fetch_performance(
        self,
        code: str,
        period: str,
        benchmark: str = "",
    ) -> dict[str, Any]:
        normalized = code.strip()
        if not normalized:
            raise ValueError("fund code must not be empty")
        if period not in self._PERIOD_DAYS:
            raise ValueError(f"unsupported performance range: {period}")
        benchmark_key = (benchmark or "").strip()
        spec = None
        if benchmark_key:
            spec = PERFORMANCE_BENCHMARKS.get(benchmark_key)
            if spec is None:
                raise ValueError(f"unsupported performance benchmark: {benchmark}")
        end_date = self._today or date.today()
        start_date = end_date - timedelta(days=self._PERIOD_DAYS[period])
        warnings: list[str] = []
        try:
            history = self._fetch_ping_history(normalized, start_date, end_date)
            source = "eastmoney_pingzhongdata"
        except FundDataFetchError as exc:
            history = self._fetch_lsjz(normalized, start_date, end_date)
            source = "eastmoney_lsjz"
            warnings.append(f"页面走势数据不可用，已使用官方净值表：{exc}")
        if not history:
            raise FundDataFetchError(f"{normalized}: missing historical NAV data")

        benchmark_payload: dict[str, Any] | None = None
        if spec is not None:
            benchmark_source = "eastmoney_index_kline"
            try:
                benchmark_history = self._fetch_index_kline(spec, start_date, end_date)
            except FundDataFetchError as eastmoney_error:
                benchmark_source = "sina_index_kline"
                try:
                    benchmark_history = self._fetch_sina_index(spec, start_date, end_date)
                except FundDataFetchError as sina_error:
                    warnings.append(
                        f"{spec.name}基准暂不可用：{eastmoney_error}; {sina_error}"
                    )
                    benchmark_history = []
            if benchmark_history:
                benchmark_payload = {
                    "code": spec.code,
                    "name": spec.name,
                    "source": benchmark_source,
                    "points": _normalized_curve(benchmark_history),
                }

        curve_start = history[0][0]
        if benchmark_payload and benchmark_payload["points"]:
            curve_start = max(curve_start, str(benchmark_payload["points"][0]["date"]))
            history = [point for point in history if point[0] >= curve_start]
            benchmark_points = [
                (str(point["date"]), float(point["raw_value"]))
                for point in benchmark_payload["points"]
                if str(point["date"]) >= curve_start
            ]
            benchmark_payload["points"] = _normalized_curve(benchmark_points)
        return {
            "code": normalized,
            "range": period,
            "source": source,
            "start_date": curve_start,
            "end_date": history[-1][0],
            "points": _normalized_curve(history),
            "benchmark": benchmark_payload,
            "warnings": warnings,
        }

    def fetch_intraday(self, code: str) -> dict[str, Any]:
        normalized = code.strip()
        if not normalized:
            raise ValueError("fund code must not be empty")
        params = {"symbol": normalized}
        payload = self._get_json(SINA_ESTIMATE_URL, params, "https://finance.sina.com.cn/")
        return _parse_sina_curve(normalized, payload, today=self._today or date.today())

    def _fetch_lsjz(
        self,
        code: str,
        start_date: date,
        end_date: date,
    ) -> list[tuple[str, float]]:
        points: list[tuple[str, float]] = []
        page_index = 1
        while True:
            payload = self._get_json(
                EASTMONEY_HISTORY_URL,
                {
                    "fundCode": code,
                    "pageIndex": str(page_index),
                    "pageSize": "20",
                    "startDate": start_date.isoformat(),
                    "endDate": end_date.isoformat(),
                },
                "https://fundf10.eastmoney.com/",
            )
            if payload.get("ErrCode") != 0:
                raise FundDataFetchError(
                    f"{code}: Eastmoney history returned {payload.get('ErrCode')}"
                )
            data = payload.get("Data")
            rows = data.get("LSJZList") if isinstance(data, Mapping) else None
            if not isinstance(rows, list):
                raise FundDataFetchError(f"{code}: invalid historical NAV data")
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                point_date = _normalized_date(row.get("FSRQ"))
                value = _float_or_none(row.get("LJJZ"))
                if value is None:
                    value = _float_or_none(row.get("DWJZ"))
                if point_date and value is not None:
                    points.append((point_date, value))
            total_count = int(payload.get("TotalCount") or len(rows))
            if not rows or page_index * 20 >= total_count:
                break
            page_index += 1
        return sorted(dict(points).items())

    def _fetch_ping_history(
        self,
        code: str,
        start_date: date,
        end_date: date,
    ) -> list[tuple[str, float]]:
        url = EASTMONEY_PING_URL_TEMPLATE.format(code=code)
        text = self._get_text(url, {})
        accumulated = _parse_js_variable(text, "Data_ACWorthTrend")
        points: list[tuple[str, float]] = []
        if isinstance(accumulated, list):
            for row in accumulated:
                if not isinstance(row, list) or len(row) < 2:
                    continue
                point_date = _eastmoney_trend_date(row[0])
                if point_date is None:
                    continue
                value = _float_or_none(row[1])
                if start_date <= point_date <= end_date and value is not None:
                    points.append((point_date.isoformat(), value))
        return sorted(dict(points).items())

    def _fetch_index_kline(
        self,
        spec: PerformanceBenchmark,
        start_date: date,
        end_date: date,
    ) -> list[tuple[str, float]]:
        payload = self._get_json(
            EASTMONEY_INDEX_KLINE_URL,
            {
                "secid": spec.eastmoney_secid,
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": "101",
                "fqt": "1",
                "beg": start_date.strftime("%Y%m%d"),
                "end": end_date.strftime("%Y%m%d"),
            },
            "https://quote.eastmoney.com/",
        )
        data = payload.get("data")
        rows = data.get("klines") if isinstance(data, Mapping) else None
        if not isinstance(rows, list) or not rows:
            raise FundDataFetchError(f"missing {spec.name} history")
        points: list[tuple[str, float]] = []
        for row in rows:
            parts = str(row).split(",")
            value = _float_or_none(parts[2] if len(parts) > 2 else None)
            point_date = _normalized_date(parts[0] if parts else None)
            if point_date and value is not None:
                points.append((point_date, value))
        return points

    def _fetch_sina_index(
        self,
        spec: PerformanceBenchmark,
        start_date: date,
        end_date: date,
    ) -> list[tuple[str, float]]:
        payload = self._get_json(
            SINA_INDEX_KLINE_URL,
            {"symbol": spec.sina_symbol, "scale": "240", "ma": "no", "datalen": "1023"},
            "https://finance.sina.com.cn/",
        )
        result = payload.get("result")
        rows = result.get("data") if isinstance(result, Mapping) else None
        if not isinstance(rows, list) or not rows:
            raise FundDataFetchError(f"missing Sina {spec.name} history")
        points: list[tuple[str, float]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            point_date = _normalized_date(row.get("day"))
            value = _float_or_none(row.get("close"))
            if point_date and start_date.isoformat() <= point_date <= end_date.isoformat() and value is not None:
                points.append((point_date, value))
        if not points:
            raise FundDataFetchError(f"Sina {spec.name} history is outside requested range")
        return points

    def _get_json(
        self,
        url: str,
        params: Mapping[str, str],
        referer: str,
    ) -> Mapping[str, Any]:
        if self._json_getter is not None:
            try:
                payload = self._json_getter(url, params)
            except Exception as exc:
                raise FundDataFetchError(f"failed to fetch {url}: {exc}") from exc
        else:
            request = Request(
                f"{url}?{urlencode(params)}",
                headers={
                    "User-Agent": BROWSER_USER_AGENT,
                    "Accept": "application/json",
                    "Referer": referer,
                },
            )
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                raise FundDataFetchError(f"failed to fetch {url}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise FundDataFetchError(f"unexpected JSON payload from {url}")
        return payload

    def _get_text(self, url: str, params: Mapping[str, str]) -> str:
        if self._text_getter is not None:
            try:
                return self._text_getter(url, params)
            except Exception as exc:
                raise FundDataFetchError(f"failed to fetch {url}: {exc}") from exc
        request = Request(
            f"{url}?{urlencode(params)}" if params else url,
            headers={"User-Agent": BROWSER_USER_AGENT, "Referer": "https://fund.eastmoney.com/"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.read().decode("utf-8")
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
            raise FundDataFetchError(f"failed to fetch {url}: {exc}") from exc


def _parse_sina_curve(
    code: str,
    payload: Mapping[str, Any],
    *,
    today: date,
) -> dict[str, Any]:
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise FundDataFetchError(f"{code}: invalid Sina result")
    status = result.get("status")
    data = result.get("data")
    if not isinstance(status, Mapping) or str(status.get("code")) != "0":
        raise FundDataFetchError(f"{code}: Sina status is not successful")
    if not isinstance(data, Mapping):
        raise FundDataFetchError(f"{code}: missing Sina data")
    rows = data.get("networth")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        raise FundDataFetchError(f"{code}: invalid Sina networth")
    time_ranges = _time_ranges(data.get("time_range"))
    is_overnight = _is_cross_midnight_ranges(time_ranges)

    candidates_by_time: dict[tuple[date, tuple[int, int, int]], tuple[float, float]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        raw_date = _normalized_date(row.get("pre_date"))
        point_time = _normalized_time(row.get("min_time"))
        nav = _float_or_none(row.get(SINA_NAV_KEY))
        return_pct = _float_or_none(row.get(SINA_RETURN_KEY))
        if None in (raw_date, point_time, nav, return_pct):
            continue
        if not _in_time_ranges(point_time, time_ranges):
            continue
        p_date = date.fromisoformat(raw_date)
        candidates_by_time[(p_date, point_time)] = (nav, return_pct)

    valid_candidates = [
        (k[0], k[1], v[0], v[1])
        for k, v in candidates_by_time.items()
        if k[0] <= today
    ]
    if not valid_candidates:
        return {
            "code": code,
            "date": today.isoformat(),
            "source": SINA_ESTIMATE_SOURCE_NAME,
            "availability": "no_minute_data",
            "points": [],
            "warning": SINA_ESTIMATE_WARNING,
        }

    latest_date, latest_time, _, _ = max(valid_candidates, key=lambda x: (x[0], x[1]))

    # 取最近一个已结束会话：非交易日与盘前回退到最近交易日数据，
    # 由前端按点上的真实日期标注，不因「不是今天」而丢弃。
    if not is_overnight:
        open_date = latest_date
        selected = [
            item for item in valid_candidates
            if item[0] == open_date and (item[0], item[1]) <= (latest_date, latest_time)
        ]
    else:
        if latest_time >= (12, 0, 0):
            open_date = latest_date
        else:
            open_date = latest_date - timedelta(days=1)

        close_date = open_date + timedelta(days=1)

        selected = []
        for item in valid_candidates:
            p_date, p_time = item[0], item[1]
            if (p_date, p_time) > (latest_date, latest_time):
                continue
            if p_time >= (12, 0, 0):
                if p_date == open_date:
                    selected.append(item)
            else:
                if p_date == close_date:
                    selected.append(item)

    selected.sort(key=lambda x: (x[0], x[1]))
    ordered_points = [
        {
            "time": f"{p_time[0]:02d}:{p_time[1]:02d}:{p_time[2]:02d}",
            "datetime": f"{p_date.isoformat()} {p_time[0]:02d}:{p_time[1]:02d}:{p_time[2]:02d}",
            "date": p_date.isoformat(),
            "estimated_nav": nav,
            "estimated_return_pct": round(return_pct, 4),
        }
        for (p_date, p_time, nav, return_pct) in selected
    ]

    return {
        "code": code,
        "date": today.isoformat(),
        "source": SINA_ESTIMATE_SOURCE_NAME,
        "availability": "available" if ordered_points else "no_minute_data",
        "points": ordered_points,
        "warning": SINA_ESTIMATE_WARNING,
    }


def _normalized_curve(points: list[tuple[str, float]]) -> list[dict[str, Any]]:
    if not points:
        return []
    base = points[0][1]
    if base == 0:
        raise FundDataFetchError("curve base value must not be zero")
    return [
        {
            "date": point_date,
            "raw_value": round(value, 6),
            "return_pct": round((value / base - 1) * 100, 4),
        }
        for point_date, value in points
    ]


def _parse_js_variable(text: str, name: str) -> Any:
    match = re.search(rf"\bvar\s+{re.escape(name)}\s*=\s*", text)
    if match is None:
        raise FundDataFetchError(f"missing JavaScript variable {name}")
    try:
        value, _end = json.JSONDecoder().raw_decode(text[match.end():].lstrip())
    except json.JSONDecodeError as exc:
        raise FundDataFetchError(f"invalid JavaScript variable {name}") from exc
    return value


def _batch_rows(
    payload: Mapping[str, Any],
    label: str,
    *,
    require_non_empty: bool = False,
) -> list[Any]:
    if payload.get("success") is False:
        raise FundDataFetchError(f"{label}: upstream returned success=false")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise FundDataFetchError(f"{label}: data is not an array")
    if require_non_empty and not rows:
        raise FundDataFetchError(f"{label}: empty data array")
    return rows


def _parse_sina_estimate(
    code: str,
    payload: Mapping[str, Any],
    source_url: str,
    *,
    today: date,
) -> FundIntradayEstimate:
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise FundDataFetchError(f"{code}: invalid Sina result")
    status = result.get("status")
    data = result.get("data")
    if not isinstance(status, Mapping) or str(status.get("code")) != "0":
        raise FundDataFetchError(f"{code}: Sina status is not successful")
    if not isinstance(data, Mapping):
        raise FundDataFetchError(f"{code}: missing Sina data")
    points = data.get("networth")
    if not isinstance(points, list) or not points:
        raise FundDataFetchError(f"{code}: missing Sina networth")
    time_ranges = _time_ranges(data.get("time_range"))
    candidates: list[tuple[date, tuple[int, int, int], Mapping[str, Any]]] = []
    for point in points:
        if not isinstance(point, Mapping):
            continue
        estimated_nav = _float_or_none(point.get(SINA_NAV_KEY))
        estimated_return_pct = _float_or_none(point.get(SINA_RETURN_KEY))
        point_date_str = _normalized_date(point.get("pre_date"))
        point_time = _normalized_time(point.get("min_time"))
        if None in (estimated_nav, estimated_return_pct, point_date_str, point_time):
            continue
        try:
            point_d = date.fromisoformat(point_date_str)
        except ValueError:
            continue
        if point_d > today:
            continue
        if not _in_time_ranges(point_time, time_ranges):
            continue
        candidates.append((point_d, point_time, point))
    if not candidates:
        raise FundDataFetchError(f"{code}: no complete Sina estimate point")
    point_d, point_time, latest = max(candidates, key=lambda item: (item[0], item[1]))
    point_date = point_d.isoformat()
    estimated_at = f"{point_date} {point_time[0]:02d}:{point_time[1]:02d}:{point_time[2]:02d}"
    selected_point = {
        key: latest.get(key)
        for key in SINA_POINT_KEYS
        if key in latest
    }
    previous_nav = _float_or_none(data.get("worth"))
    previous_nav_date = _text_or_none(data.get("worth_date"))
    estimated_nav = float(_float_or_none(latest.get(SINA_NAV_KEY)))
    estimated_return_pct = float(_float_or_none(latest.get(SINA_RETURN_KEY)))
    status = "available" if point_d == today else "stale"
    return FundIntradayEstimate(
        code=code,
        name=None,
        previous_nav=previous_nav,
        previous_nav_date=previous_nav_date,
        estimated_nav=estimated_nav,
        estimated_return_pct=estimated_return_pct,
        estimated_at=estimated_at,
        source=SINA_ESTIMATE_SOURCE_NAME,
        source_url=source_url,
        fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        status=status,
        warnings=[
            {"stage": "intraday_estimate", "message": SINA_ESTIMATE_WARNING},
            *_estimate_basis_warnings(
                previous_nav=previous_nav,
                previous_nav_date=previous_nav_date,
                estimated_nav=estimated_nav,
                estimated_return_pct=estimated_return_pct,
                estimated_at=estimated_at,
                source=SINA_ESTIMATE_SOURCE_NAME,
            ),
        ],
        payload={
            "selected_point": selected_point,
            "field_pair": f"{SINA_NAV_KEY}/{SINA_RETURN_KEY}",
            "worth": data.get("worth"),
            "worth_date": data.get("worth_date"),
            "time_range": data.get("time_range"),
            "note": "仅保存选中的完整分钟点；另一组 pre_nav2/nav2_pct 只留档，不参与计算。",
        },
    )


def _eastmoney_basic_params(code: str) -> dict[str, str]:
    return {
        "FCODE": code,
        "deviceid": "valuation-mvp",
        "plat": "Iphone",
        "product": "EFund",
        "version": "6.3.8",
    }


def _core_params(codes: list[str]) -> dict[str, str]:
    return {
        "FCODES": ",".join(codes),
        "FIELDS": "SHORTNAME,RZDF,DWJZ,JZRQ,FSRQ,NAV,GSZZL,GZTIME,GSZ,FCODE,QDCODE,PTYPE",
        "deviceid": "1234567.py.service",
        "version": "6.5.5",
        "product": "EFund",
        "plat": "web",
    }


def _valuation_params(codes: list[str]) -> dict[str, str]:
    return {
        "FCODES": ",".join(codes),
        "FIELDS": "FCODE,SHORTNAME,GSZZL,GZTIME,GSZ,NAV,PDATE",
    }


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(str(value).replace("%", "").replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _estimate_basis_warnings(
    *,
    previous_nav: float | None,
    previous_nav_date: str | None,
    estimated_nav: float | None,
    estimated_return_pct: float | None,
    estimated_at: str | None,
    source: str | None,
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    estimate_date = _date_prefix(estimated_at or "")
    previous_iso = _normalized_date(previous_nav_date)
    if estimate_date is not None and previous_iso == estimate_date.isoformat():
        warnings.append(
            {
                "stage": "estimate_basis",
                "message": "previous_nav_date 等于估值日，不能当作涨跌前净值",
            }
        )
    elif (
        previous_nav not in (None, 0)
        and estimated_nav is not None
        and estimated_return_pct is not None
        and previous_iso
        and estimate_date is not None
        and previous_iso != estimate_date.isoformat()
    ):
        implied = (estimated_nav / previous_nav - 1) * 100
        if abs(implied - estimated_return_pct) > 0.05:
            warnings.append(
                {
                    "stage": "estimate_basis",
                    "message": "净值比与来源涨跌幅不一致，未改用来源涨跌幅",
                }
            )
    if source and "sina" in source:
        warnings.append(
            {
                "stage": "estimate_basis",
                "message": "未将估值点日期当作海外收益归属日",
            }
        )
    return warnings


def _date_prefix(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_estimate_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return None


def _eastmoney_trend_date(value: Any) -> date | None:
    """把 pingzhongdata 毫秒时间戳转成中国市场日历日。

    按 UTC 解析会把北京时间零点错成前一天，最新官方净值因此从曲线上消失。
    """
    try:
        return datetime.fromtimestamp(float(value) / 1000, CHINA_TZ).date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _normalized_date(value: Any) -> str | None:
    text = str(value or "").strip()
    for date_format in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text[:10], date_format).date().isoformat()
        except ValueError:
            continue
    return None


def _normalized_time(value: Any) -> tuple[int, int, int] | None:
    text = str(value or "").strip()
    match = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?", text)
    if match is None:
        return None
    hour, minute, second = (int(part or 0) for part in match.groups())
    if hour > 23 or minute > 59 or second > 59:
        return None
    return hour, minute, second


def _time_ranges(value: Any) -> list[tuple[tuple[int, int, int], tuple[int, int, int]]]:
    """解析新浪 time_range，兼容 [["21:30", "00:30"], ...] 与 ["09:30-11:30", ...]。"""
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    ranges: list[tuple[tuple[int, int, int], tuple[int, int, int]]] = []
    for item in items:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            parts = [str(item[0]), str(item[1])]
        else:
            parts = re.findall(r"(?<!\d)\d{1,2}:\d{2}(?::\d{2})?", str(item))
        if len(parts) != 2:
            continue
        start, end = _normalized_time(parts[0]), _normalized_time(parts[1])
        if start is not None and end is not None:
            ranges.append((start, end))
    return ranges


def _in_time_ranges(
    point_time: tuple[int, int, int],
    ranges: list[tuple[tuple[int, int, int], tuple[int, int, int]]],
) -> bool:
    """分钟点是否落在交易时段内；跨午夜时段（如 21:30-00:30）按环绕处理。"""
    if not ranges:
        return True
    for start, end in ranges:
        if start <= end:
            if start <= point_time <= end:
                return True
        elif point_time >= start or point_time <= end:
            return True
    return False


def _is_cross_midnight_ranges(
    ranges: list[tuple[tuple[int, int, int], tuple[int, int, int]]],
) -> bool:
    """判定交易时段是否跨越午夜（如 21:30-04:00 或 [["21:30", "00:30"], ["00:30", "04:00"]]）。"""
    if not ranges:
        return False
    for start, end in ranges:
        if start > end:
            return True
    if len(ranges) >= 2:
        for i in range(len(ranges) - 1):
            if ranges[i][0] > ranges[i + 1][0]:
                return True
    return False

