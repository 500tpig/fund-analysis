"""模拟结算的官方净值来源。

真实库只以 ``mode=ro`` 打开，且只选择代码、净值日和净值列，不读取持仓金额。
"""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .decimals import NAV_MAX_PLACES, places_of


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
