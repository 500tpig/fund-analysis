"""Current-holdings portfolio analysis without look-through or trade history."""

from __future__ import annotations

import math
import statistics
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Mapping, Sequence

from .fund_data import FundDataFetchError


SCHEMA_VERSION = "portfolio_analysis.v1"
RISK_SCHEMA_VERSION = "portfolio_risk.v1"
MIN_RISK_SAMPLES = 20
TRADING_DAYS_PER_YEAR = 252
UNCLASSIFIED_KEY = "unclassified"
UNCLASSIFIED_LABEL = "未分类"

MARKET_LABELS: Mapping[str, str] = {
    "CN": "境内",
    "HK": "港股",
    "US": "美股",
    "GLOBAL": "全球（未拆分）",
}
CASH_MARKET_KEY = "cash"
CASH_MARKET_LABEL = "现金"

INDUSTRY_UNAVAILABLE_REASON = "无季度底层持仓，不使用人工主题代替穿透"
CURRENCY_UNAVAILABLE_REASON = "无币种元数据，且无季度底层持仓可穿透"
OVERLAP_UNAVAILABLE_REASON = "无可靠季度底层持仓，不使用人工主题代替穿透"
CONTRIBUTION_NOTE = "仅计入当日可核对收益；官方测算与预估不会重复加总，不含手续费和分红"
RISK_NOTE = "基金官方净值序列，不是个人收益曲线，不构成买卖建议"

FetchPerformance = Callable[[str, str], Mapping[str, Any]]


def build_portfolio_analysis(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    holdings = [dict(row) for row in rows]
    total_amount = sum(_amount(row) for row in holdings)
    return {
        "schema_version": SCHEMA_VERSION,
        "basis": {
            "holdings_scope": "current_holdings",
            "weight_basis": "current_amount",
            "look_through": False,
            "has_quarterly_holdings": False,
            "has_transactions": False,
            "contribution_basis": "today_profit_included",
        },
        "distribution": {
            "asset_class": _available_breakdown(
                source="fund_valuation_profiles.fund_type",
                scope="fund_level",
                note="基金类型，非底层资产穿透",
                items=_aggregate(holdings, total_amount, _asset_class_bucket),
            ),
            "region": _available_breakdown(
                source="fund_valuation_profiles.market",
                scope="valuation_market",
                note="基金估值市场，非底层地区穿透；现金单独列出",
                items=_aggregate(holdings, total_amount, _market_bucket),
            ),
            "currency": _unavailable_breakdown(CURRENCY_UNAVAILABLE_REASON),
            "industry": _unavailable_breakdown(INDUSTRY_UNAVAILABLE_REASON),
        },
        "concentration": _concentration(holdings, total_amount),
        "overlap": {
            "available": False,
            "reason": OVERLAP_UNAVAILABLE_REASON,
        },
        "contribution": _contribution(holdings),
    }


def build_portfolio_risk(
    rows: Sequence[Mapping[str, Any]],
    histories: Mapping[str, Mapping[str, Any]],
    failures: Sequence[Mapping[str, str]] | None = None,
    *,
    period: str = "1y",
) -> dict[str, Any]:
    funds: list[dict[str, Any]] = []
    series_by_code: dict[str, list[tuple[str, float]]] = {}
    names: dict[str, str] = {}
    for row in rows:
        if _is_cash(row):
            continue
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        names[code] = str(row.get("name") or code)
        history = histories.get(code)
        series = _nav_series(history)
        series_by_code[code] = series
        returns = _daily_returns(series)
        funds.append(
            {
                "code": code,
                "name": names[code],
                "source": None if history is None else history.get("source"),
                "start_date": series[0][0] if series else None,
                "end_date": series[-1][0] if series else None,
                "sample_count": len(returns),
                "nav_point_count": len(series),
                "volatility_pct": _annualized_vol_pct(returns),
                "max_drawdown_pct": _max_drawdown_pct(series),
            }
        )
    return {
        "schema_version": RISK_SCHEMA_VERSION,
        "range": period,
        "basis": {
            "series": "official_nav",
            "look_through": False,
            "personal_return": False,
            "advice": False,
            "note": RISK_NOTE,
        },
        "funds": funds,
        "pairs": _correlation_pairs(series_by_code, names),
        "failures": [dict(item) for item in failures or ()],
    }


def fetch_nav_histories(
    codes: Sequence[str],
    fetch_performance: FetchPerformance,
    period: str = "1y",
    max_workers: int = 8,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]]]:
    unique_codes = list(dict.fromkeys(code.strip() for code in codes if code.strip()))
    histories: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, str]] = []
    if not unique_codes:
        return histories, failures

    def _one(code: str) -> tuple[str, dict[str, Any] | None, str | None]:
        try:
            payload = dict(fetch_performance(code, period))
        except (FundDataFetchError, ValueError, OSError) as exc:
            return code, None, str(exc)
        return code, payload, None

    workers = max(1, min(max_workers, len(unique_codes)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_one, unique_codes))
    for code, payload, error in results:
        if payload is None:
            failures.append({"code": code, "error": error or "missing historical NAV data"})
            continue
        histories[code] = payload
    return histories, failures


def _available_breakdown(
    *,
    source: str,
    scope: str,
    note: str,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "available": True,
        "source": source,
        "scope": scope,
        "note": note,
        "items": items,
    }


def _unavailable_breakdown(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "reason": reason,
        "items": [],
    }


def _asset_class_bucket(row: Mapping[str, Any]) -> tuple[str, str]:
    if _is_cash(row):
        return "cash", "现金"
    fund_type = str(row.get("fund_type") or "").strip()
    label = str(row.get("fund_type_label") or "").strip()
    if fund_type:
        return fund_type, label or fund_type
    return UNCLASSIFIED_KEY, UNCLASSIFIED_LABEL


def _market_bucket(row: Mapping[str, Any]) -> tuple[str, str]:
    if _is_cash(row):
        return CASH_MARKET_KEY, CASH_MARKET_LABEL
    market = str(row.get("valuation_market") or "").strip().upper()
    if market:
        return market, MARKET_LABELS.get(market, market)
    return UNCLASSIFIED_KEY, UNCLASSIFIED_LABEL


def _aggregate(
    rows: Sequence[Mapping[str, Any]],
    total_amount: float,
    bucket_of: Callable[[Mapping[str, Any]], tuple[str, str]],
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        key, label = bucket_of(row)
        entry = grouped.setdefault(
            key,
            {"key": key, "label": label, "amount": 0.0, "holding_count": 0},
        )
        entry["amount"] += _amount(row)
        entry["holding_count"] += 1
    items = []
    for entry in grouped.values():
        amount = float(entry["amount"])
        items.append(
            {
                "key": entry["key"],
                "label": entry["label"],
                "amount": _money(amount),
                "weight_pct": _weight_pct(amount, total_amount),
                "holding_count": int(entry["holding_count"]),
            }
        )
    items.sort(key=lambda item: (-(item["amount"] or 0.0), str(item["key"])))
    return items


def _concentration(
    rows: Sequence[Mapping[str, Any]],
    total_amount: float,
) -> dict[str, Any]:
    holdings = []
    for row in rows:
        amount = _amount(row)
        holdings.append(
            {
                "code": str(row.get("code") or ""),
                "name": str(row.get("name") or ""),
                "amount": _money(amount),
                "weight_pct": _weight_pct(amount, total_amount),
            }
        )
    holdings.sort(key=lambda item: (-(item["weight_pct"] or 0.0), item["code"]))
    weights = [item["weight_pct"] or 0.0 for item in holdings]
    return {
        "source": "current_holdings.amount",
        "max_weight_pct": _percent(weights[0] if weights else 0.0),
        "top3_weight_pct": _percent(sum(weights[:3])),
        "top5_weight_pct": _percent(sum(weights[:5])),
        "holdings": holdings,
    }


def _contribution(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    total = 0.0
    has_included = False
    for row in rows:
        today_profit = row.get("today_profit")
        if not isinstance(today_profit, Mapping) or not today_profit.get("included"):
            continue
        profit = today_profit.get("amount")
        if profit is None:
            continue
        try:
            profit_value = float(profit)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(profit_value):
            continue
        has_included = True
        total += profit_value
        return_pct = today_profit.get("return_pct")
        if return_pct is None:
            return_pct = row.get("estimated_return_pct")
        items.append(
            {
                "code": str(row.get("code") or ""),
                "name": str(row.get("name") or ""),
                "amount": _money(_amount(row)),
                "kind": today_profit.get("kind"),
                "estimated_return_pct": _percent(return_pct),
                "estimated_profit": _money(profit_value),
                "estimated_profit_state": _value_state(profit_value),
            }
        )
    for item in items:
        profit = item["estimated_profit"] or 0.0
        item["share_pct"] = _percent(profit / total * 100) if has_included and total != 0 else None
    items.sort(
        key=lambda item: (-abs(item["estimated_profit"] or 0.0), item["code"])
    )
    return {
        "basis": "today_profit_included",
        "note": CONTRIBUTION_NOTE,
        "total_estimated_profit": _money(total) if has_included else None,
        "total_estimated_profit_state": _value_state(total if has_included else None),
        "items": items,
    }


def _nav_series(history: Mapping[str, Any] | None) -> list[tuple[str, float]]:
    if not history:
        return []
    points = history.get("points")
    if not isinstance(points, list):
        return []
    series: list[tuple[str, float]] = []
    for point in points:
        if not isinstance(point, Mapping):
            continue
        date_text = str(point.get("date") or "").strip()
        value = point.get("raw_value")
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if date_text and math.isfinite(number):
            series.append((date_text, number))
    series.sort(key=lambda item: item[0])
    return series


def _daily_returns(series: Sequence[tuple[str, float]]) -> list[tuple[str, float]]:
    returns: list[tuple[str, float]] = []
    for index in range(1, len(series)):
        previous = series[index - 1][1]
        current = series[index][1]
        if previous == 0:
            continue
        change = current / previous - 1
        if math.isfinite(change):
            returns.append((series[index][0], change))
    return returns


def _annualized_vol_pct(returns: Sequence[tuple[str, float]]) -> float | None:
    values = [item[1] for item in returns]
    if len(values) < MIN_RISK_SAMPLES:
        return None
    try:
        sigma = statistics.stdev(values)
    except statistics.StatisticsError:
        return None
    if not math.isfinite(sigma):
        return None
    return _percent(sigma * math.sqrt(TRADING_DAYS_PER_YEAR) * 100)


def _max_drawdown_pct(series: Sequence[tuple[str, float]]) -> float | None:
    if len(series) < MIN_RISK_SAMPLES + 1:
        return None
    peak = series[0][1]
    max_drawdown = 0.0
    for _, value in series:
        if value > peak:
            peak = value
        if peak == 0:
            continue
        drawdown = value / peak - 1
        if drawdown < max_drawdown:
            max_drawdown = drawdown
    return _percent(max_drawdown * 100)


def _correlation_pairs(
    series_by_code: Mapping[str, Sequence[tuple[str, float]]],
    names: Mapping[str, str],
) -> list[dict[str, Any]]:
    codes = [code for code, series in series_by_code.items() if series]
    pairs: list[dict[str, Any]] = []
    for left_index, left_code in enumerate(codes):
        left_returns = dict(_daily_returns(series_by_code[left_code]))
        for right_code in codes[left_index + 1 :]:
            right_returns = dict(_daily_returns(series_by_code[right_code]))
            overlap_dates = sorted(set(left_returns) & set(right_returns))
            xs = [left_returns[day] for day in overlap_dates]
            ys = [right_returns[day] for day in overlap_dates]
            pairs.append(
                {
                    "code_a": left_code,
                    "code_b": right_code,
                    "name_a": names.get(left_code, left_code),
                    "name_b": names.get(right_code, right_code),
                    "overlap_count": len(overlap_dates),
                    "correlation": _pearson(xs, ys),
                }
            )
    pairs.sort(
        key=lambda item: (
            item["correlation"] is None,
            -abs(item["correlation"] or 0.0),
            item["code_a"],
            item["code_b"],
        )
    )
    return pairs


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < MIN_RISK_SAMPLES or len(xs) != len(ys):
        return None
    try:
        value = statistics.correlation(xs, ys)
    except statistics.StatisticsError:
        return None
    return round(value, 4) if math.isfinite(value) else None


def _is_cash(row: Mapping[str, Any]) -> bool:
    if bool(row.get("is_cash_asset")):
        return True
    code = str(row.get("code") or "").strip()
    name = str(row.get("name") or "").strip()
    return code.startswith("CASH:") or name in {"现金", "余额宝"}


def _money(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def _percent(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 4) if math.isfinite(number) else None


def _value_state(value: float | None) -> str:
    if value is None:
        return "muted"
    if value > 0:
        return "rise"
    if value < 0:
        return "fall"
    return "muted"


def _amount(row: Mapping[str, Any]) -> float:
    value = row.get("amount")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def _weight_pct(amount: float, total_amount: float) -> float | None:
    if total_amount <= 0:
        return None
    return _percent(amount / total_amount * 100)
