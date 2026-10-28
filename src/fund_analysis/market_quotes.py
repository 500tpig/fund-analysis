from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .trading_calendar import CALENDAR_CN_STOCK, CALENDAR_US_STOCK, is_trading_day


EASTMONEY_MARKET_QUOTE_SOURCE = "eastmoney_market_quote"
EASTMONEY_MARKET_QUOTE_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
MARKET_QUOTE_FIELDS = "f12,f14,f2,f1,f18,f124,f4,f3,f13"
MARKET_QUOTE_ORDER = ("CSI_ALL_SHARE", "AU9999", "NDX")
JsonGetter = Callable[[str, Mapping[str, str]], Mapping[str, Any]]


@dataclass(frozen=True)
class MarketQuoteSpec:
    symbol: str
    secid: str
    name: str
    instrument_type: str
    market: str
    currency: str
    timezone_name: str


MARKET_QUOTE_SPECS: dict[str, MarketQuoteSpec] = {
    "CSI_ALL_SHARE": MarketQuoteSpec(
        symbol="CSI_ALL_SHARE",
        secid="1.000985",
        name="中证全指",
        instrument_type="index",
        market="中证指数",
        currency="CNY",
        timezone_name="Asia/Shanghai",
    ),
    "AU9999": MarketQuoteSpec(
        symbol="AU9999",
        secid="118.AU9999",
        name="黄金 9999",
        instrument_type="spot",
        market="上海黄金交易所",
        currency="CNY/g",
        timezone_name="Asia/Shanghai",
    ),
    "NDX": MarketQuoteSpec(
        symbol="NDX",
        secid="100.NDX100",
        name="纳斯达克 100",
        instrument_type="index",
        market="Nasdaq",
        currency="USD",
        timezone_name="America/New_York",
    ),
}


class MarketQuoteFetchError(RuntimeError):
    """Raised when a market quote cannot be fetched or parsed."""


@dataclass(frozen=True)
class MarketQuote:
    symbol: str
    name: str
    instrument_type: str
    market: str
    currency: str
    last_price: float
    previous_close: float
    change_value: float
    change_pct: float
    trade_date: str
    quoted_at: str
    source: str
    source_url: str
    fetched_at: str
    payload: dict[str, Any]
    warnings: list[dict[str, str]]


@dataclass(frozen=True)
class MarketQuoteBatch:
    quotes: dict[str, MarketQuote]
    failures: dict[str, str]


class EastmoneyMarketQuoteProvider:
    name = EASTMONEY_MARKET_QUOTE_SOURCE

    def __init__(self, timeout: float = 8.0, json_getter: JsonGetter | None = None) -> None:
        self.timeout = timeout
        self._json_getter = json_getter

    def fetch_all(self) -> MarketQuoteBatch:
        quotes: dict[str, MarketQuote] = {}
        failures: dict[str, str] = {}
        for symbol in MARKET_QUOTE_ORDER:
            try:
                quotes[symbol] = self.fetch(symbol)
            except Exception as exc:
                try:
                    quote = self._fetch_backup(symbol)
                    quote.warnings.append({"code": "primary_source_failed", "message": str(exc)})
                    quotes[symbol] = quote
                except Exception as backup_exc:
                    failures[symbol] = f"{exc}; backup: {backup_exc}"
        return MarketQuoteBatch(quotes=quotes, failures=failures)

    def fetch(self, symbol: str) -> MarketQuote:
        try:
            spec = MARKET_QUOTE_SPECS[symbol]
        except KeyError as exc:
            raise ValueError(f"unsupported market quote symbol: {symbol}") from exc
        params = {"secids": spec.secid, "fields": MARKET_QUOTE_FIELDS}
        payload = self._get_json(EASTMONEY_MARKET_QUOTE_URL, params)
        result = payload.get("data")
        rows = result.get("diff") if isinstance(result, Mapping) else None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], Mapping):
            raise MarketQuoteFetchError(f"{symbol}: missing quote data")
        data = rows[0]
        if str(data.get("f13")) != spec.secid.split(".", 1)[0]:
            raise MarketQuoteFetchError(f"{symbol}: unexpected upstream market")
        upstream_symbol = str(data.get("f12") or "").strip().upper()
        expected_symbol = spec.secid.split(".", 1)[1].upper()
        if upstream_symbol != expected_symbol:
            raise MarketQuoteFetchError(
                f"{symbol}: unexpected upstream symbol {upstream_symbol or 'empty'}"
            )

        decimals = int(_number(data.get("f1"), "f1"))
        if not 0 <= decimals <= 8:
            raise MarketQuoteFetchError(f"{symbol}: unsupported price precision {decimals}")
        scale = 10**decimals
        quote_timestamp = int(_number(data.get("f124"), "f124"))
        if quote_timestamp <= 0:
            raise MarketQuoteFetchError(f"{symbol}: invalid quote timestamp {quote_timestamp}")
        quoted = datetime.fromtimestamp(quote_timestamp, timezone.utc).astimezone(
            ZoneInfo(spec.timezone_name)
        )
        trade_day = quoted.date()
        if symbol == "AU9999" and quoted.time() >= time(20):
            trade_day += timedelta(days=1)
        source_url = f"{EASTMONEY_MARKET_QUOTE_URL}?{urlencode(params)}"
        return MarketQuote(
            symbol=spec.symbol,
            name=spec.name,
            instrument_type=spec.instrument_type,
            market=spec.market,
            currency=spec.currency,
            last_price=_number(data.get("f2"), "f2") / scale,
            previous_close=_number(data.get("f18"), "f18") / scale,
            change_value=_number(data.get("f4"), "f4") / scale,
            change_pct=_number(data.get("f3"), "f3") / 100,
            trade_date=trade_day.isoformat(),
            quoted_at=quoted.isoformat(timespec="seconds"),
            source=EASTMONEY_MARKET_QUOTE_SOURCE,
            source_url=source_url,
            fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            payload=dict(payload),
            warnings=[],
        )

    def _fetch_backup(self, symbol: str) -> MarketQuote:
        url = _backup_quote_url(symbol)
        request = Request(url, headers={"Referer": "https://finance.sina.com.cn/", "User-Agent": "Mozilla/5.0"})
        try:
            with urlopen(request, timeout=self.timeout) as response:
                text = response.read().decode("gb18030")
        except (HTTPError, URLError, TimeoutError, OSError, UnicodeError) as exc:
            raise MarketQuoteFetchError(f"failed to fetch {url}: {exc}") from exc
        return parse_backup_quote(symbol, text)

    def _get_json(self, url: str, params: Mapping[str, str]) -> Mapping[str, Any]:
        if self._json_getter is not None:
            return self._json_getter(url, params)
        request = Request(
            f"{url}?{urlencode(params)}",
            headers={
                "Accept": "application/json",
                "Referer": "https://quote.eastmoney.com/",
                "User-Agent": "fund-valuation/0.1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise MarketQuoteFetchError(f"failed to fetch {url}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise MarketQuoteFetchError(f"unexpected JSON payload from {url}")
        return payload


def _backup_quote_url(symbol: str) -> str:
    if symbol == "AU9999":
        return "https://hq.sinajs.cn/list=SGE_AU9999"
    return "https://qt.gtimg.cn/q=" + {"NDX": "usNDX", "CSI_ALL_SHARE": "sh000985"}[symbol]


def parse_backup_quote(symbol: str, text: str) -> MarketQuote:
    spec = MARKET_QUOTE_SPECS[symbol]
    variable = {"AU9999": "hq_str_SGE_AU9999", "NDX": "v_usNDX", "CSI_ALL_SHARE": "v_sh000985"}[symbol]
    match = re.search(r"(?:var\s+)?" + re.escape(variable) + r'="([^"\n]*)";', text)
    if not match:
        raise MarketQuoteFetchError(f"{symbol}: missing backup quote")
    fields = match[1].split("," if symbol == "AU9999" else "~")
    try:
        if symbol == "AU9999":
            if fields[0] != "AU9999" or fields[2] != "Au99.99":
                raise ValueError("unexpected gold instrument")
            # 新浪涨跌幅以昨收价（9）为基准，字段 5 是昨日均价。
            last, previous = _number(fields[3], "last"), _number(fields[9], "previous")
            pct = _number(fields[17].removesuffix("%"), "change_pct")
            change = round(last - previous, 2)
            quoted = datetime.strptime(fields[16], "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            source = "sina_market_quote"
        else:
            if fields[2] != {"NDX": ".NDX", "CSI_ALL_SHARE": "000985"}[symbol]:
                raise ValueError("unexpected index instrument")
            last, previous = _number(fields[3], "last"), _number(fields[4], "previous")
            change, pct = _number(fields[31], "change"), _number(fields[32], "change_pct")
            fmt = "%Y-%m-%d %H:%M:%S" if symbol == "NDX" else "%Y%m%d%H%M%S"
            quoted = datetime.strptime(fields[30], fmt).replace(tzinfo=ZoneInfo(spec.timezone_name))
            source = "tencent_market_quote"
        if last <= 0 or previous <= 0 or abs((last / previous - 1) * 100 - pct) > 0.02:
            raise ValueError("inconsistent quote prices and change")
        if abs(last - previous - change) > 0.02:
            raise ValueError("inconsistent quote change")
        if quoted > datetime.now(timezone.utc) + timedelta(minutes=5):
            raise ValueError("future quote timestamp")
    except (IndexError, ValueError) as exc:
        raise MarketQuoteFetchError(f"{symbol}: invalid backup quote: {exc}") from exc
    trade_day = quoted.date()
    if symbol == "AU9999" and quoted.time() >= time(20):
        trade_day += timedelta(days=1)
    return MarketQuote(
        symbol=symbol, name=spec.name, instrument_type=spec.instrument_type,
        market=spec.market, currency=spec.currency, last_price=last, previous_close=previous,
        change_value=change, change_pct=pct, trade_date=trade_day.isoformat(),
        quoted_at=quoted.isoformat(timespec="seconds"), source=source,
        source_url=_backup_quote_url(symbol), fetched_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        payload={"fields": fields}, warnings=[],
    )


def _number(value: Any, field: str) -> float:
    if value is None or value == "" or isinstance(value, bool):
        raise MarketQuoteFetchError(f"missing numeric quote field {field}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketQuoteFetchError(f"invalid numeric quote field {field}") from exc
    if not math.isfinite(number):
        raise MarketQuoteFetchError(f"invalid numeric quote field {field}")
    return number


def _calendar_open(calendar: str, current_date: date) -> bool | None:
    try:
        return is_trading_day(calendar, current_date)
    except ValueError:
        return None


def _gold_night_session(local: datetime) -> tuple[str, str] | None:
    """SGE night session is 20:00-02:30.

    Friday night continues to Saturday 02:30. There is no night session on the
    calendar evening before a weekday public holiday. Returns None when the
    clock is outside night hours.
    """
    clock = local.time()
    today = local.date()
    if clock >= time(20):
        today_open = _calendar_open(CALENDAR_CN_STOCK, today)
        if today_open is None:
            return "unknown", "日历待更新"
        if not today_open:
            return None
        tomorrow = today + timedelta(days=1)
        tomorrow_open = _calendar_open(CALENDAR_CN_STOCK, tomorrow)
        if tomorrow_open is None:
            return "unknown", "日历待更新"
        if tomorrow_open or tomorrow.weekday() >= 5:
            return "trading", "夜盘交易中"
        return "closed", "已收盘"
    if time(0) <= clock < time(2, 30):
        yesterday_open = _calendar_open(CALENDAR_CN_STOCK, today - timedelta(days=1))
        if yesterday_open is None:
            return "unknown", "日历待更新"
        if not yesterday_open:
            return None
        today_open = _calendar_open(CALENDAR_CN_STOCK, today)
        if today_open is None:
            return "unknown", "日历待更新"
        if today_open or today.weekday() == 5:
            return "trading", "夜盘交易中"
        return None
    return None


def market_session(symbol: str, now: datetime | None = None) -> tuple[str, str]:
    current = now or datetime.now(timezone.utc)
    if symbol == "NDX":
        local = current.astimezone(ZoneInfo("America/New_York"))
        open_day = _calendar_open(CALENDAR_US_STOCK, local.date())
        if open_day is None:
            return "unknown", "日历待更新"
        if not open_day:
            return "closed", "休市"
        return ("trading", "交易中") if time(9, 30) <= local.time() < time(16) else ("closed", "已收盘")

    local = current.astimezone(ZoneInfo("Asia/Shanghai"))
    if symbol == "CSI_ALL_SHARE":
        open_day = _calendar_open(CALENDAR_CN_STOCK, local.date())
        if open_day is None:
            return "unknown", "日历待更新"
        if not open_day:
            return "closed", "休市"
        if time(9, 30) <= local.time() < time(11, 30) or time(13) <= local.time() < time(15):
            return "trading", "交易中"
        if time(11, 30) <= local.time() < time(13):
            return "break", "午间休市"
        return "closed", "已收盘"

    if symbol != "AU9999":
        return "unknown", "状态未知"
    night = _gold_night_session(local)
    if night is not None:
        return night
    open_day = _calendar_open(CALENDAR_CN_STOCK, local.date())
    if open_day is None:
        return "unknown", "日历待更新"
    if open_day and (
        time(9) <= local.time() < time(11, 30)
        or time(13, 30) <= local.time() < time(15, 30)
    ):
        return "trading", "交易中"
    if open_day and time(11, 30) <= local.time() < time(13, 30):
        return "break", "午间休市"
    return "closed", "已收盘" if open_day else "休市"


def build_market_quotes_payload(
    snapshots: Sequence[Mapping[str, Any]],
    latest_runs: Sequence[Mapping[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(timezone.utc)
    rows = {str(row.get("symbol") or ""): row for row in snapshots}
    latest_run = next(
        (run for run in latest_runs if str(run.get("job_type") or "") == "market_quote"),
        None,
    )
    failures = _run_failures(latest_run)
    recent_attempt = _recent_attempt(latest_run, current)
    items: list[dict[str, Any]] = []
    refresh_eligible = False
    for symbol in MARKET_QUOTE_ORDER:
        spec = MARKET_QUOTE_SPECS[symbol]
        row = rows.get(symbol)
        session_status, session_label = market_session(symbol, current)
        failure = failures.get(symbol)
        if row is None:
            status = "failed" if failure else "missing"
            if not recent_attempt:
                refresh_eligible = True
        else:
            status = "failed" if failure else "fresh"
            quoted_at = _datetime_or_none(row.get("quoted_at"))
            if not failure and session_status == "trading" and (
                quoted_at is None or current.astimezone(timezone.utc) - quoted_at.astimezone(timezone.utc) > timedelta(minutes=20)
            ):
                status = "stale"
                if not recent_attempt:
                    refresh_eligible = True
        items.append(
            {
                "symbol": symbol,
                "name": spec.name,
                "instrument_type": spec.instrument_type,
                "market": spec.market,
                "currency": spec.currency,
                "last_price": row.get("last_price") if row else None,
                "previous_close": row.get("previous_close") if row else None,
                "change_value": row.get("change_value") if row else None,
                "change_pct": row.get("change_pct") if row else None,
                "trade_date": row.get("trade_date") if row else None,
                "quoted_at": row.get("quoted_at") if row else None,
                "source": row.get("source") if row else None,
                "source_url": row.get("source_url") if row else None,
                "fetched_at": row.get("fetched_at") if row else None,
                "status": status,
                "session_status": session_status,
                "session_label": session_label,
                "warning": failure or ("行情超过 20 分钟未更新" if status == "stale" else None),
            }
        )
    return {
        "schema_version": "market_quotes.v1",
        "refresh_eligible": refresh_eligible,
        "items": items,
    }


def _run_failures(run: Mapping[str, Any] | None) -> dict[str, str]:
    if not run:
        return {}
    raw = run.get("evidence_json") or run.get("evidence") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    failures = raw.get("failures") if isinstance(raw, Mapping) else None
    if not isinstance(failures, Mapping):
        return {}
    return {str(symbol): str(error) for symbol, error in failures.items() if error}


def _recent_attempt(run: Mapping[str, Any] | None, now: datetime) -> bool:
    completed = _datetime_or_none(run.get("completed_at")) if run else None
    if completed is None:
        return False
    return now.astimezone(timezone.utc) - completed.astimezone(timezone.utc) <= timedelta(minutes=5)


def _datetime_or_none(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.astimezone()
