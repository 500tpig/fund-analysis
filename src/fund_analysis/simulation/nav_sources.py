"""模拟结算的官方净值来源。

真实库只以 ``mode=ro`` 打开，且只选择代码、净值日和净值列，不读取持仓金额。
生产结算优先使用东方财富历史净值接口（lsjz，按基金代码逐日查询，与是否持仓无关），
该接口取不到对应日期或失败时回退真实库的官方净值记录。
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..fund_data import BROWSER_USER_AGENT
from .decimals import NAV_MAX_PLACES, places_of

EASTMONEY_LSJZ_URL = "https://api.fund.eastmoney.com/f10/lsjz"
# 缺 Referer 时接口返回 ErrCode -999；该头不是可选项。
LSJZ_REFERER = "https://fundf10.eastmoney.com/"


@dataclass(frozen=True)
class NavObservation:
    code: str
    nav_date: date
    nav: Decimal
    source: str
    observed_at: datetime
    published_at: datetime | None = None
    source_url: str | None = None


def _nav_from_real(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        # SQLite REAL 先转最短十进制表示，再校验精度；不做 float 运算。
        nav = Decimal(repr(float(value))) if isinstance(value, float) else Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not nav.is_finite() or nav <= 0 or places_of(nav) > NAV_MAX_PLACES:
        return None
    return nav


def _nav_from_text(value: object) -> Decimal | None:
    """解析来源返回的十进制字符串净值；超精度或非法值视为无效。"""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        nav = Decimal(text)
    except InvalidOperation:
        return None
    if not nav.is_finite() or nav <= 0 or places_of(nav) > NAV_MAX_PLACES:
        return None
    return nav


class MainDbNavSource:
    name = "main_db_official_nav"

    def __init__(self, main_db_path: Path | str):
        self.path = Path(main_db_path).expanduser().resolve()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn

    def fetch(self, code: str, nav_date: date | None, observed_at: datetime) -> NavObservation | None:
        """取指定净值日（或最新）的官方净值；没有对应日期时返回 None，不用其他日期代替。"""
        if not self.path.exists():
            return None
        with contextlib.closing(self._connect()) as conn:
            try:
                latest = conn.execute(
                    "SELECT nav_date, latest_nav FROM fund_market_snapshots WHERE code = ?",
                    (code,),
                ).fetchone()
            except sqlite3.OperationalError:
                latest = None
            candidates: list[tuple[object, object]] = []
            if latest is not None:
                candidates.append((latest["nav_date"], latest["latest_nav"]))
            if nav_date is not None:
                try:
                    recorded = conn.execute(
                        "SELECT nav_date, official_nav FROM fund_official_daily_profit "
                        "WHERE code = ? AND nav_date = ?",
                        (code, nav_date.isoformat()),
                    ).fetchone()
                except sqlite3.OperationalError:
                    recorded = None
                if recorded is not None:
                    candidates.append((recorded["nav_date"], recorded["official_nav"]))
        for raw_date, raw_nav in candidates:
            try:
                parsed_date = date.fromisoformat(str(raw_date)[:10])
            except ValueError:
                continue
            if nav_date is not None and parsed_date != nav_date:
                continue
            nav = _nav_from_real(raw_nav)
            if nav is not None:
                return NavObservation(code, parsed_date, nav, self.name, observed_at)
        return None


class _FetchSource(Protocol):
    name: str

    def fetch(
        self, code: str, nav_date: date | None, observed_at: datetime
    ) -> NavObservation | None: ...


class EastmoneyLsjzNavSource:
    """东方财富历史净值接口，按基金代码查询，固定使用单位净值 DWJZ。

    未披露日期、周末和无效代码返回 ErrCode 0 与空数据，视同「缺失」而不是错误，
    与结算语义一致；ErrCode 非 0 或净值字段异常时抛错，不产生默认值。
    累计净值 LJJZ 只服务走势曲线，分红后与 DWJZ 差异很大，结算不能使用。
    """

    name = "eastmoney_lsjz_nav"

    def __init__(
        self,
        *,
        timeout: float = 15.0,
        json_getter: Callable[[str, Mapping[str, str]], Any] | None = None,
    ):
        self.timeout = timeout
        self._json_getter = json_getter

    def fetch(
        self, code: str, nav_date: date | None, observed_at: datetime
    ) -> NavObservation | None:
        """取指定净值日（或最新）的单位净值；该日无数据时返回 None，不用其他日期代替。"""
        params = {"fundCode": code, "pageIndex": "1", "pageSize": "5"}
        if nav_date is not None:
            params["startDate"] = nav_date.isoformat()
            params["endDate"] = nav_date.isoformat()
        payload = self._get_json(params)
        if payload.get("ErrCode") != 0:
            raise ValueError(f"{code}: lsjz returned ErrCode {payload.get('ErrCode')}")
        data = payload.get("Data")
        rows = data.get("LSJZList") if isinstance(data, Mapping) else None
        if rows is None:
            return None
        if not isinstance(rows, list):
            raise ValueError(f"{code}: invalid lsjz payload")
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            raw_date = str(row.get("FSRQ") or "")[:10]
            if nav_date is not None and raw_date != nav_date.isoformat():
                continue
            nav = _nav_from_text(row.get("DWJZ"))
            if nav is None:
                raise ValueError(f"{code}: invalid DWJZ {row.get('DWJZ')!r} on {raw_date}")
            return NavObservation(code, date.fromisoformat(raw_date), nav, self.name, observed_at)
        return None

    def _get_json(self, params: Mapping[str, str]) -> Mapping[str, Any]:
        if self._json_getter is not None:
            payload = self._json_getter(EASTMONEY_LSJZ_URL, params)
        else:
            request = Request(
                f"{EASTMONEY_LSJZ_URL}?{urlencode(params)}",
                headers={
                    "User-Agent": BROWSER_USER_AGENT,
                    "Accept": "application/json",
                    "Referer": LSJZ_REFERER,
                },
            )
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"failed to fetch {EASTMONEY_LSJZ_URL}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ValueError("unexpected lsjz payload")
        return payload


class FallbackNavSource:
    """按优先级取数：主来源有结果就用；返回 None 或抛异常时尝试备用来源。

    同一次 fetch 只产生一条观察，不合并两个来源的取值。账本层面同一净值日
    相同取值按哈希去重、不同取值按 nav_conflict 阻断，冲突保持可见而不是静默选一条。
    两个来源都没有结果且主来源异常时，把主来源错误上抛，让结算报告留下失败记录，
    而不是把一次来源故障静默降级成「净值缺失」。
    """

    def __init__(self, primary: _FetchSource, fallback: _FetchSource):
        self.primary = primary
        self.fallback = fallback
        self.name = f"{primary.name}|fallback:{fallback.name}"

    def fetch(
        self, code: str, nav_date: date | None, observed_at: datetime
    ) -> NavObservation | None:
        primary_error: Exception | None = None
        try:
            observation = self.primary.fetch(code, nav_date, observed_at)
        except Exception as exc:  # noqa: BLE001 - 主来源失败仍尝试备用来源
            observation = None
            primary_error = exc
        if observation is not None:
            return observation
        fallback_observation = self.fallback.fetch(code, nav_date, observed_at)
        if fallback_observation is None and primary_error is not None:
            raise primary_error
        return fallback_observation
