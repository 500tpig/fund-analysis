"""当日收益选择、基准质量与覆盖汇总。

今日可核对收益只纳入日期和金额基准都满足条件的测算。盘中预估与官方测算分栏，
无日期导入收益不进入今日值。当前金额乘涨跌幅在基准未确认时只作为情景，
不标成账户实际收益。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping, Sequence

from .fund_data import SINA_ESTIMATE_SOURCE_NAME
from .trading_calendar import CALENDAR_CN_STOCK, get_latest_trading_day

CN_TZ = timezone(timedelta(hours=8))
CNY_QUANT = Decimal("0.01")
RATE_QUANT = Decimal("0.0001")
NAV_RATIO_TOLERANCE_PCT = Decimal("0.05")
HOLDING_RATE_TOLERANCE_PCT = Decimal("0.05")

MODE_TODAY = "today"
MODE_LATEST_OFFICIAL = "latest_official"

KIND_OFFICIAL = "official_measurement"
KIND_ESTIMATE = "estimate"
KIND_UNKNOWN = "unknown"

BASIS_UNKNOWN = "unknown"
BASIS_CONFIRMED = "confirmed"
BASIS_MISMATCH = "mismatch"
BASIS_UNVERIFIED_LEGACY = "unverified_legacy"

OVERSEAS_MARKETS = {"US", "GLOBAL"}


def beijing_today(now: datetime | None = None) -> date:
    stamp = now or datetime.now(CN_TZ)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=CN_TZ)
    return stamp.astimezone(CN_TZ).date()


def parse_iso_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    candidates = (text[:10], text[:8])
    for candidate in candidates:
        for date_format in ("%Y-%m-%d", "%Y%m%d"):
            try:
                return datetime.strptime(candidate, date_format).date()
            except ValueError:
                continue
    return None


def decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite():
        return None
    return number


def cny_decimal(value: Any) -> Decimal | None:
    number = decimal_or_none(value)
    if number is None:
        return None
    return number.quantize(CNY_QUANT, rounding=ROUND_HALF_UP)


def cny_money(value: Any) -> float | None:
    number = cny_decimal(value)
    return float(number) if number is not None else None


def percent_money(value: Any) -> float | None:
    number = decimal_or_none(value)
    if number is None:
        return None
    return float(number.quantize(RATE_QUANT, rounding=ROUND_HALF_UP))


def profit_from_rate(amount: Any, return_pct: Any) -> Decimal | None:
    basis = cny_decimal(amount)
    rate = decimal_or_none(return_pct)
    if basis is None or rate is None:
        return None
    return (basis * rate / Decimal("100")).quantize(CNY_QUANT, rounding=ROUND_HALF_UP)


def empty_profit(
    *,
    kind: str = KIND_UNKNOWN,
    return_date: str | None = None,
    source: str | None = None,
    basis_amount: float | None = None,
    basis_status: str = BASIS_UNKNOWN,
    reason_codes: Sequence[str] | None = None,
    return_pct: float | None = None,
) -> dict[str, Any]:
    return {
        "amount": None,
        "kind": kind,
        "return_date": return_date,
        "source": source,
        "basis_amount": basis_amount,
        "basis_status": basis_status,
        "included": False,
        "reason_codes": list(reason_codes or []),
        "return_pct": percent_money(return_pct) if return_pct is not None else None,
    }


def measured_profit(
    *,
    amount: Decimal,
    kind: str,
    return_date: str | None,
    source: str | None,
    basis_amount: float | None,
    basis_status: str,
    included: bool,
    reason_codes: Sequence[str],
    return_pct: Any,
) -> dict[str, Any]:
    return {
        "amount": float(amount),
        "kind": kind,
        "return_date": return_date,
        "source": source,
        "basis_amount": basis_amount,
        "basis_status": basis_status,
        "included": included,
        "reason_codes": list(reason_codes),
        "return_pct": percent_money(return_pct),
    }


def assess_amount_basis(
    amount_nav_date: Any, target: date, *, previous_nav_date: date | None = None,
) -> tuple[str, list[str]]:
    parsed = parse_iso_date(amount_nav_date)
    if parsed is None:
        return BASIS_UNKNOWN, ["amount_basis_unknown"]
    if parsed >= target:
        return BASIS_MISMATCH, ["amount_basis_mismatch"]
    # Without a source-linked prior NAV date, only the immediately preceding
    # calendar date can match without guessing across weekends/holidays/gaps.
    expected = previous_nav_date or target - timedelta(days=1)
    if parsed != expected:
        return BASIS_UNKNOWN, ["amount_basis_unverified"]
    return BASIS_CONFIRMED, []


def is_overseas_estimate(
    metadata: Mapping[str, Any] | None,
    estimate_source: str | None,
) -> bool:
    meta = metadata or {}
    market = str(meta.get("market") or meta.get("valuation_market") or "").strip()
    fund_type = str(meta.get("fund_type") or "").strip().lower()
    source = str(estimate_source or "")
    if market in OVERSEAS_MARKETS:
        return True
    if fund_type.startswith("qdii"):
        return True
    if SINA_ESTIMATE_SOURCE_NAME in source or "sina" in source:
        return True
    return False


def assess_estimate_basis(
    estimate: Mapping[str, Any] | None,
    *,
    today: date,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not estimate:
        return {
            "status": BASIS_UNKNOWN,
            "reason_codes": ["estimate_incomplete"],
            "return_date": None,
            "usable_for_inclusion": False,
            "previous_nav_usable": False,
        }
    estimated_at = parse_iso_date(estimate.get("estimated_at"))
    reasons: list[str] = []
    previous_nav = decimal_or_none(estimate.get("previous_nav"))
    previous_nav_date = parse_iso_date(estimate.get("previous_nav_date"))
    estimated_nav = decimal_or_none(estimate.get("estimated_nav"))
    estimated_return_pct = decimal_or_none(estimate.get("estimated_return_pct"))
    source = str(estimate.get("source") or "")
    previous_nav_usable = bool(
        previous_nav is not None
        and previous_nav > 0
        and previous_nav_date is not None
        and estimated_at is not None
        and previous_nav_date < estimated_at
    )
    if estimated_at is None or estimated_nav is None or estimated_nav <= 0 or estimated_return_pct is None:
        reasons.append("estimate_incomplete")
    elif estimated_at != today:
        reasons.append("estimate_not_target_date")
    if previous_nav_date is not None and estimated_at is not None and previous_nav_date >= estimated_at:
        reasons.append("previous_nav_not_pre_value")
    status = "consistent"
    if (
        previous_nav_usable
        and estimated_nav is not None
        and estimated_return_pct is not None
        and previous_nav is not None
    ):
        implied = (estimated_nav / previous_nav - Decimal("1")) * Decimal("100")
        if abs(implied - estimated_return_pct) > NAV_RATIO_TOLERANCE_PCT:
            status = "conflict"
            reasons.append("estimate_basis_conflict")
    elif not previous_nav_usable:
        status = BASIS_UNKNOWN
        if "previous_nav_not_pre_value" not in reasons:
            reasons.append("estimate_basis_unverified")
    if is_overseas_estimate(metadata, source):
        reasons.append("estimate_return_date_unverified")
        if "sina" in source:
            reasons.append("sina_pre_date_not_return_date")
    unique_reasons = list(dict.fromkeys(reasons))
    attributable = (
        estimated_at == today
        and status == "consistent"
        and previous_nav_usable
        and "estimate_basis_conflict" not in unique_reasons
        and "estimate_return_date_unverified" not in unique_reasons
        and "estimate_not_target_date" not in unique_reasons
        and "estimate_incomplete" not in unique_reasons
    )
    return {
        "status": status,
        "reason_codes": unique_reasons,
        "return_date": today.isoformat() if attributable else None,
        "usable_for_inclusion": attributable,
        "previous_nav_usable": previous_nav_usable,
    }


def holding_profit_quality(
    holding: Mapping[str, Any],
    profit_amount: float | None,
) -> dict[str, Any]:
    if decimal_or_none(holding.get("imported_holding_profit")) is not None:
        return {
            "holding_profit_amount_source": "imported_snapshot",
            "holding_profit_rate_source": "imported_snapshot" if holding.get("holding_profit_rate") is not None else None,
            "holding_profit_conflict": False,
            "holding_profit_conflict_reason_codes": [],
        }
    cost = decimal_or_none(holding.get("cost"))
    imported_rate = decimal_or_none(holding.get("holding_profit_rate"))
    amount_source = (
        "cost_measurement"
        if cost is not None
        else ("imported_rate" if imported_rate is not None else None)
    )
    rate_source = (
        "imported_rate"
        if imported_rate is not None
        else ("cost_measurement" if cost is not None else None)
    )
    conflict = False
    if cost is not None and cost != 0 and imported_rate is not None and profit_amount is not None:
        implied = Decimal(str(profit_amount)) / cost * Decimal("100")
        if abs(implied - imported_rate) > HOLDING_RATE_TOLERANCE_PCT:
            conflict = True
    return {
        "holding_profit_amount_source": amount_source,
        "holding_profit_rate_source": rate_source,
        "holding_profit_conflict": conflict,
        "holding_profit_conflict_reason_codes": (
            ["holding_profit_source_conflict"] if conflict else []
        ),
    }


def build_row_profit_fields(
    holding: Mapping[str, Any],
    *,
    today: date,
    snapshot: Mapping[str, Any] | None,
    estimate: Mapping[str, Any] | None,
    estimate_is_current: bool,
    metadata: Mapping[str, Any] | None,
    cash: bool,
) -> dict[str, Any]:
    amount = cny_decimal(holding.get("amount"))
    amount_nav_date = parse_iso_date(holding.get("amount_nav_date"))
    amount_basis_source = str(holding.get("amount_basis_source") or "").strip() or None
    official_nav_date = parse_iso_date(snapshot.get("nav_date") if snapshot else None)
    official_return_pct = decimal_or_none(
        snapshot.get("daily_return_pct") if snapshot else None
    )
    official_source = str(snapshot.get("source") or "") if snapshot else None
    estimated_return_pct = decimal_or_none(
        estimate.get("estimated_return_pct") if estimate_is_current and estimate else None
    )
    estimate_source = str(estimate.get("source") or "") if estimate else None
    imported_daily = cny_money(holding.get("daily_profit"))
    basis_amount = float(amount) if amount is not None else None

    amount_basis_status, amount_reasons = assess_amount_basis(amount_nav_date, today)

    estimate_basis = assess_estimate_basis(estimate, today=today, metadata=metadata)
    estimate_amount_status, estimate_amount_reasons = assess_amount_basis(
        amount_nav_date, today,
        previous_nav_date=(
            parse_iso_date(estimate.get("previous_nav_date"))
            if estimate and estimate_basis["usable_for_inclusion"] else None
        ),
    )
    if official_nav_date != today:
        amount_basis_status, amount_reasons = estimate_amount_status, estimate_amount_reasons
    official_scenario = None
    estimate_scenario = None
    today_reasons: list[str] = []

    if cash:
        today_profit = empty_profit(reason_codes=["cash_asset"], basis_amount=basis_amount)
        return _row_profit_payload(
            today_profit=today_profit,
            official_scenario=None,
            estimate_scenario=None,
            imported_daily=imported_daily,
            imported_date=parse_iso_date(holding.get("daily_profit_date")),
            imported_source=holding.get("profit_source"),
            amount_nav_date=amount_nav_date,
            amount_basis_status=BASIS_UNKNOWN,
            amount_basis_source=None,
            estimate_basis=estimate_basis,
        )

    if amount is None:
        today_reasons.append("amount_missing")

    if official_nav_date == today and official_return_pct is not None and amount is not None:
        official_profit = profit_from_rate(amount, official_return_pct)
        official_included = amount_basis_status == BASIS_CONFIRMED
        official_reasons = list(amount_reasons)
        if official_profit is not None:
            official_scenario = measured_profit(
                amount=official_profit,
                kind=KIND_OFFICIAL,
                return_date=today.isoformat(),
                source=official_source,
                basis_amount=basis_amount,
                basis_status=amount_basis_status,
                included=official_included,
                reason_codes=official_reasons,
                return_pct=official_return_pct,
            )
    elif official_nav_date is not None and official_nav_date != today:
        today_reasons.append("official_nav_not_target_date")
    elif official_return_pct is None or official_nav_date is None:
        today_reasons.append("official_nav_missing")

    if estimate_is_current and estimated_return_pct is not None and amount is not None:
        estimate_profit = profit_from_rate(amount, estimated_return_pct)
        estimate_reasons = list(estimate_amount_reasons) + list(estimate_basis["reason_codes"])
        estimate_included = (
            estimate_amount_status == BASIS_CONFIRMED
            and estimate_basis["usable_for_inclusion"]
            and official_nav_date != today
        )
        if estimate_profit is not None:
            estimate_scenario = measured_profit(
                amount=estimate_profit,
                kind=KIND_ESTIMATE,
                return_date=estimate_basis["return_date"],
                source=estimate_source,
                basis_amount=basis_amount,
                basis_status=estimate_amount_status,
                included=False,
                reason_codes=list(dict.fromkeys(estimate_reasons)),
                return_pct=estimated_return_pct,
            )
            if estimate_included:
                estimate_scenario["included"] = True
    elif estimate and not estimate_is_current:
        today_reasons.append("estimate_not_target_date")
    elif not estimate_is_current:
        today_reasons.append("estimate_incomplete")

    if official_scenario and official_scenario["included"]:
        today_profit = dict(official_scenario)
    elif official_scenario is not None:
        today_profit = empty_profit(
            kind=KIND_UNKNOWN,
            return_date=None,
            source=official_source,
            basis_amount=basis_amount,
            basis_status=amount_basis_status,
            reason_codes=official_scenario["reason_codes"] + ["official_requires_amount_basis"],
        )
    elif estimate_scenario and estimate_scenario["included"]:
        today_profit = dict(estimate_scenario)
    else:
        reasons = list(today_reasons)
        if estimate_scenario:
            reasons.extend(estimate_scenario["reason_codes"])
        else:
            reasons.extend(amount_reasons)
        if imported_daily is not None and not holding.get("daily_profit_date"):
            reasons.append("imported_undated")
        today_profit = empty_profit(
            basis_amount=basis_amount,
            basis_status=amount_basis_status,
            reason_codes=list(dict.fromkeys(reasons)),
            source=estimate_source or official_source,
        )

    return _row_profit_payload(
        today_profit=today_profit,
        official_scenario=official_scenario,
        estimate_scenario=estimate_scenario,
        imported_daily=imported_daily,
        imported_date=parse_iso_date(holding.get("daily_profit_date")),
        imported_source=holding.get("profit_source"),
        amount_nav_date=amount_nav_date,
        amount_basis_status=amount_basis_status,
        amount_basis_source=amount_basis_source if amount_nav_date else None,
        estimate_basis=estimate_basis,
    )


def _row_profit_payload(
    *,
    today_profit: dict[str, Any],
    official_scenario: dict[str, Any] | None,
    estimate_scenario: dict[str, Any] | None,
    imported_daily: float | None,
    imported_date: date | None,
    imported_source: str | None,
    amount_nav_date: date | None,
    amount_basis_status: str,
    amount_basis_source: str | None,
    estimate_basis: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "amount_nav_date": amount_nav_date.isoformat() if amount_nav_date else None,
        "amount_basis_status": amount_basis_status,
        "amount_basis_source": amount_basis_source,
        "today_profit": today_profit,
        "official_scenario": official_scenario,
        "estimate_scenario": estimate_scenario,
        "imported_daily_profit": (
            None
            if imported_daily is None
            else {
                "amount": imported_daily,
                "dated": imported_date is not None,
                "return_date": imported_date.isoformat() if imported_date else None,
                "source": imported_source,
                "label": f"{imported_source} {imported_date.isoformat()} 收益" if imported_date else "历史导入收益，日期未记录",
            }
        ),
        "estimate_basis_status": estimate_basis.get("status"),
        "estimate_basis_reason_codes": list(estimate_basis.get("reason_codes") or []),
    }


def _bucket_total(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, Any]:
    profit_total = Decimal("0.00")
    basis_total = Decimal("0.00")
    count = 0
    amount_total = Decimal("0.00")
    for row in rows:
        item = row.get(field)
        if not isinstance(item, Mapping) or item.get("amount") is None:
            continue
        profit = cny_decimal(item.get("amount"))
        basis = cny_decimal(item.get("basis_amount") if item.get("basis_amount") is not None else row.get("amount"))
        if profit is None:
            continue
        profit_total += profit
        if basis is not None:
            basis_total += basis
            amount_total += basis
        count += 1
    if count == 0:
        return {
            "profit": None,
            "rate_pct": None,
            "count": 0,
            "amount": None,
        }
    rate = None
    if basis_total != 0:
        rate = percent_money(profit_total / basis_total * Decimal("100"))
    return {
        "profit": float(profit_total),
        "rate_pct": rate,
        "count": count,
        "amount": float(amount_total) if amount_total != 0 or count else None,
    }


def build_monitoring_profit(
    fields: Mapping[str, Any], *, snapshot: Mapping[str, Any] | None,
    estimate: Mapping[str, Any] | None, today: date,
) -> dict[str, Any]:
    """Current-amount indication; independent from verifiable-profit eligibility."""
    selected = None
    if parse_iso_date((snapshot or {}).get("nav_date")) == today:
        selected = fields.get("official_scenario")
    elif estimate and parse_iso_date(estimate.get("estimated_at")) == today:
        reasons = set(fields.get("estimate_basis_reason_codes") or [])
        blocked = {
            "estimate_basis_conflict", "estimate_return_date_unverified",
            "previous_nav_not_pre_value", "estimate_incomplete",
        }
        if estimate.get("status") == "available" and not reasons.intersection(blocked):
            selected = fields.get("estimate_scenario")
    if not selected:
        return empty_profit(reason_codes=fields["today_profit"]["reason_codes"])
    return {
        **selected, "included": False, "return_date": today.isoformat(),
        "observed_at": (snapshot or {}).get("fetched_at")
        if selected["kind"] == KIND_OFFICIAL else (estimate or {}).get("estimated_at"),
    }


def build_monitoring_summary(rows: Sequence[Mapping[str, Any]], *, today: date) -> dict[str, Any]:
    participating = [row for row in rows if (row.get("monitoring_profit") or {}).get("amount") is not None]
    official = [row for row in participating if row["monitoring_profit"]["kind"] == KIND_OFFICIAL]
    estimates = [row for row in participating if row["monitoring_profit"]["kind"] == KIND_ESTIMATE]
    stamps = [row["monitoring_profit"].get("observed_at") for row in participating]
    return {
        **_bucket_total(participating, "monitoring_profit"),
        "return_date": today.isoformat(), "total": len(rows),
        "official_count": len(official), "estimate_count": len(estimates),
        "official": _bucket_total(official, "monitoring_profit"),
        "estimate": _bucket_total(estimates, "monitoring_profit"),
        "updated_at": max((str(stamp) for stamp in stamps if stamp), default=None),
        "excluded_codes": [str(row.get("code") or "") for row in rows if (row.get("monitoring_profit") or {}).get("amount") is None],
    }


def resolve_display_session_target(
    fund_rows: Sequence[Mapping[str, Any]],
    *,
    today: date,
    monitoring_count: int,
) -> tuple[str, str | None, bool]:
    """确定展示日模式、展示日日期字符串及是否为今天。"""
    if monitoring_count > 0:
        return MODE_TODAY, today.isoformat(), True

    valid_dates: list[date] = []
    for row in fund_rows:
        nav_date = parse_iso_date(row.get("nav_date"))
        if nav_date is not None and nav_date <= today:
            valid_dates.append(nav_date)

    if valid_dates:
        return MODE_LATEST_OFFICIAL, max(valid_dates).isoformat(), False

    try:
        latest_day = get_latest_trading_day(CALENDAR_CN_STOCK, today, include_today=True)
        date_str: str | None = latest_day.isoformat()
    except ValueError:
        date_str = None
    return MODE_LATEST_OFFICIAL, date_str, False


def _resolve_official_row_profit(
    row: Mapping[str, Any],
    *,
    official_profit_records: Mapping[tuple[str, str], Mapping[str, Any]] | None,
) -> tuple[float | None, float | None, str | None]:
    nav_date = parse_iso_date(row.get("nav_date"))
    if nav_date is None:
        return None, None, None
    date_str = nav_date.isoformat()
    code = str(row.get("code") or "").strip()
    amount = cny_decimal(row.get("amount"))
    daily_return_pct = decimal_or_none(row.get("daily_return_pct"))

    record = official_profit_records.get((code, date_str)) if official_profit_records else None
    profit_amount = None
    if record is not None and record.get("profit_amount") is not None:
        profit_amount = cny_decimal(record.get("profit_amount"))
        if daily_return_pct is None and record.get("official_return_pct") is not None:
            daily_return_pct = decimal_or_none(record.get("official_return_pct"))

    if profit_amount is None and amount is not None and daily_return_pct is not None:
        profit_amount = profit_from_rate(amount, daily_return_pct)

    if profit_amount is not None:
        return float(profit_amount), percent_money(daily_return_pct), date_str
    return None, percent_money(daily_return_pct), date_str


def build_session_profit(
    row: Mapping[str, Any],
    *,
    mode: str,
    display_date: str | None,
    official_profit_records: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """为单行资产生成 session_profit。现金行为 None。"""
    if row.get("is_cash_asset") or not row.get("is_fund_asset", True):
        return None

    if mode == MODE_TODAY:
        mon = row.get("monitoring_profit") or {}
        mon_amount = mon.get("amount")
        if mon_amount is not None:
            return {
                "amount": cny_money(mon_amount),
                "return_pct": percent_money(mon.get("return_pct")),
                "return_date": str(mon.get("return_date") or ""),
                "kind": mon.get("kind"),
                "in_session": True,
            }
        amount_val, return_pct, date_str = _resolve_official_row_profit(
            row, official_profit_records=official_profit_records,
        )
        if amount_val is not None:
            return {
                "amount": amount_val,
                "return_pct": return_pct,
                "return_date": date_str,
                "kind": KIND_OFFICIAL,
                "in_session": False,
            }
        return {
            "amount": None,
            "return_pct": None,
            "return_date": None,
            "kind": None,
            "in_session": False,
        }

    amount_val, return_pct, date_str = _resolve_official_row_profit(
        row, official_profit_records=official_profit_records,
    )
    if amount_val is not None:
        return {
            "amount": amount_val,
            "return_pct": return_pct,
            "return_date": date_str,
            "kind": KIND_OFFICIAL,
            "in_session": bool(display_date is not None and date_str == display_date),
        }
    return {
        "amount": None,
        "return_pct": None,
        "return_date": None,
        "kind": None,
        "in_session": False,
    }


def build_display_session(
    fund_rows: Sequence[Mapping[str, Any]],
    *,
    today: date,
    monitoring: Mapping[str, Any],
) -> dict[str, Any]:
    """计算 summary.today.display_session。"""
    total_fund_count = len(fund_rows)
    monitoring_count = int(monitoring.get("count") or 0)

    if monitoring_count > 0:
        return {
            "date": today.isoformat(),
            "is_today": True,
            "mode": MODE_TODAY,
            "profit": monitoring.get("profit"),
            "rate_pct": monitoring.get("rate_pct"),
            "count": monitoring.get("count") or 0,
            "total": total_fund_count,
            "amount": monitoring.get("amount"),
            "official_count": monitoring.get("official_count") or 0,
            "estimate_count": monitoring.get("estimate_count") or 0,
            "updated_at": monitoring.get("updated_at"),
        }

    profit_total = Decimal("0.00")
    basis_total = Decimal("0.00")
    count = 0
    stamps: list[str] = []
    display_date: str | None = None

    for row in fund_rows:
        sp = row.get("session_profit")
        if not isinstance(sp, Mapping) or not sp.get("in_session") or sp.get("amount") is None:
            continue
        profit = cny_decimal(sp.get("amount"))
        basis = cny_decimal(row.get("amount"))
        if profit is None:
            continue
        profit_total += profit
        if basis is not None:
            basis_total += basis
        count += 1
        if display_date is None and sp.get("return_date"):
            display_date = str(sp.get("return_date"))
        stamp = row.get("fetched_at")
        if stamp:
            stamps.append(str(stamp))

    if display_date is None:
        _, fallback_date, _ = resolve_display_session_target(
            fund_rows, today=today, monitoring_count=0,
        )
        display_date = fallback_date

    if count == 0:
        return {
            "date": display_date,
            "is_today": False,
            "mode": MODE_LATEST_OFFICIAL,
            "profit": None,
            "rate_pct": None,
            "count": 0,
            "total": total_fund_count,
            "amount": None,
            "official_count": 0,
            "estimate_count": 0,
            "updated_at": None,
        }

    rate_pct = None
    if basis_total != 0:
        rate_pct = percent_money(profit_total / basis_total * Decimal("100"))

    return {
        "date": display_date,
        "is_today": False,
        "mode": MODE_LATEST_OFFICIAL,
        "profit": float(profit_total),
        "rate_pct": rate_pct,
        "count": count,
        "total": total_fund_count,
        "amount": float(basis_total) if basis_total != 0 or count else None,
        "official_count": count,
        "estimate_count": 0,
        "updated_at": max(stamps, default=None),
    }


def build_today_summary(
    rows: Sequence[Mapping[str, Any]],
    *,
    today: date,
) -> dict[str, Any]:
    fund_rows = [row for row in rows if row.get("is_fund_asset")]
    total_count = len(fund_rows)
    total_amount = Decimal("0.00")
    for row in fund_rows:
        amount = cny_decimal(row.get("amount"))
        if amount is not None:
            total_amount += amount
    total_amount_float = float(total_amount) if fund_rows else None

    included_rows = [
        row
        for row in fund_rows
        if isinstance(row.get("today_profit"), Mapping) and row["today_profit"].get("included")
    ]
    official_included = [
        row
        for row in included_rows
        if row["today_profit"].get("kind") == KIND_OFFICIAL
    ]
    estimate_included = [
        row
        for row in included_rows
        if row["today_profit"].get("kind") == KIND_ESTIMATE
    ]
    official_scenario_rows = [
        row for row in fund_rows if isinstance(row.get("official_scenario"), Mapping)
        and not row["official_scenario"].get("included")
    ]
    estimate_scenario_rows = [
        row for row in fund_rows if isinstance(row.get("estimate_scenario"), Mapping)
        and not row["estimate_scenario"].get("included")
        and parse_iso_date(row.get("nav_date")) != today
    ]
    quote_rows = [
        row
        for row in fund_rows
        if row.get("estimated_status") == "available" or row.get("estimated_nav") is not None
    ]
    official_data_rows = [
        row
        for row in fund_rows
        if parse_iso_date(row.get("nav_date")) == today
    ]
    amount_basis_rows = [
        row for row in fund_rows if row.get("amount_basis_status") == BASIS_CONFIRMED
    ]
    imported_rows = [
        row
        for row in fund_rows
        if isinstance(row.get("imported_daily_profit"), Mapping)
        and row["imported_daily_profit"].get("amount") is not None
        and not row["imported_daily_profit"].get("dated")
    ]

    imported_dated: dict[tuple[str, str], dict[str, Any]] = {}
    for row in fund_rows:
        item = row.get("imported_daily_profit")
        if not item or not item.get("dated"):
            continue
        key = (item["return_date"], item["source"])
        bucket = imported_dated.setdefault(key, {"return_date": key[0], "source": key[1], "profit": Decimal("0.00"), "count": 0})
        bucket["profit"] += cny_decimal(item["amount"])
        bucket["count"] += 1
    for bucket in imported_dated.values():
        bucket["profit"] = float(bucket["profit"])

    verifiable = _bucket_from_today(included_rows)
    official_measurement = _bucket_from_today(official_included)
    estimate_measurement = _bucket_from_today(estimate_included)
    official_scenario = _bucket_total(official_scenario_rows, "official_scenario")
    estimate_scenario = _bucket_total(estimate_scenario_rows, "estimate_scenario")

    if total_count == 0:
        account_status = "unavailable"
    elif not included_rows:
        account_status = "unavailable"
    elif len(included_rows) < total_count:
        account_status = "partial"
    else:
        account_status = "complete"

    if account_status == "unavailable":
        account_message = "无法确认全账户当日收益"
        verifiable_profit = None
        verifiable_rate = None
    elif account_status == "partial":
        account_message = "部分小计，不能代表全账户当日收益"
        verifiable_profit = verifiable["profit"]
        verifiable_rate = verifiable["rate_pct"]
    else:
        account_message = "当日可核对测算，不是平台实际结算"
        verifiable_profit = verifiable["profit"]
        verifiable_rate = verifiable["rate_pct"]

    excluded = []
    for row in fund_rows:
        profit = row.get("today_profit") if isinstance(row.get("today_profit"), Mapping) else {}
        if profit.get("included"):
            continue
        excluded.append(
            {
                "code": str(row.get("code") or ""),
                "amount": cny_money(row.get("amount")),
                "reason_codes": list(profit.get("reason_codes") or []),
            }
        )

    imported_profit = Decimal("0.00")
    imported_present = False
    for row in imported_rows:
        value = cny_decimal(row["imported_daily_profit"]["amount"])
        if value is None:
            continue
        imported_present = True
        imported_profit += value

    def _coverage(count: int, amount_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        covered_amount = Decimal("0.00")
        for row in amount_rows:
            value = cny_decimal(row.get("amount"))
            if value is not None:
                covered_amount += value
        amount_pct = None
        if total_amount != 0:
            amount_pct = percent_money(covered_amount / total_amount * Decimal("100"))
        return {
            "count": count,
            "total": total_count,
            "amount": float(covered_amount) if amount_rows else None,
            "amount_pct": amount_pct,
        }

    monitoring = build_monitoring_summary(fund_rows, today=today)
    display_session = build_display_session(
        fund_rows,
        today=today,
        monitoring=monitoring,
    )

    return {
        "monitoring": monitoring,
        "display_session": display_session,
        "return_date": today.isoformat(),
        "account_status": account_status,
        "account_message": account_message,
        "verifiable": {
            "profit": verifiable_profit,
            "rate_pct": verifiable_rate,
            "count": verifiable["count"],
            "amount": verifiable["amount"],
            "amount_coverage_pct": (
                percent_money(Decimal(str(verifiable["amount"])) / total_amount * Decimal("100"))
                if verifiable["amount"] is not None and total_amount != 0
                else None
            ),
        },
        "official_measurement": official_measurement,
        "estimate": estimate_measurement,
        "official_scenario": official_scenario,
        "estimate_scenario": estimate_scenario,
        "quote_coverage": _coverage(len(quote_rows), quote_rows),
        "official_data_coverage": _coverage(len(official_data_rows), official_data_rows),
        "amount_basis_coverage": _coverage(len(amount_basis_rows), amount_basis_rows),
        "verifiable_coverage": _coverage(len(included_rows), included_rows),
        "excluded": excluded,
        "imported_dated": sorted(imported_dated.values(), key=lambda item: item["return_date"], reverse=True),
        "imported_undated": {
            "count": len(imported_rows),
            "profit": float(imported_profit) if imported_present else None,
        },
        "fund_count": total_count,
        "fund_amount": total_amount_float,
    }


def _bucket_from_today(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    profit_total = Decimal("0.00")
    basis_total = Decimal("0.00")
    count = 0
    for row in rows:
        item = row.get("today_profit")
        if not isinstance(item, Mapping) or not item.get("included"):
            continue
        profit = cny_decimal(item.get("amount"))
        basis = cny_decimal(item.get("basis_amount") if item.get("basis_amount") is not None else row.get("amount"))
        if profit is None:
            continue
        profit_total += profit
        if basis is not None:
            basis_total += basis
        count += 1
    if count == 0:
        return {"profit": None, "rate_pct": None, "count": 0, "amount": None}
    rate = None
    if basis_total != 0:
        rate = percent_money(profit_total / basis_total * Decimal("100"))
    return {
        "profit": float(profit_total),
        "rate_pct": rate,
        "count": count,
        "amount": float(basis_total),
    }


def official_history_quality(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    observed: list[str] = []
    seen: set[str] = set()
    for row in records:
        nav_date = str(row.get("nav_date") or "")[:10]
        if len(nav_date) == 10 and nav_date not in seen:
            seen.add(nav_date)
            observed.append(nav_date)
    observed.sort()
    completeness = "recorded_dates_only"
    if len(observed) >= 2:
        start = date.fromisoformat(observed[0])
        end = date.fromisoformat(observed[-1])
        span_days = (end - start).days + 1
        if span_days != len(observed):
            completeness = "gapped_recorded_dates"
    return {
        "observed_dates": observed,
        "completeness": completeness,
        "cumulative_kind": "recorded_dates_sum",
        "basis_note": "按首次观察金额测算，不是已验证的历史实际收益",
    }
