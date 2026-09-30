"""NAV update status resolver for fund holdings.

Resolves the official NAV update status for a fund based on its metadata,
calendar, disclosure rules, latest snapshot, and current evaluation time.

Supported status values (disclosure state, not market trading state):
- updated: Target NAV date has been officially disclosed.
- pending: A newer NAV date exists and its normal disclosure evening has not passed.
- overdue: A NAV date's normal disclosure evening has passed without official update.
- market_closed: Domestic same-day rule only: CN market closed today and the
  previous trading day NAV is disclosed.
- source_failed: Upstream data provider failed during refresh.
- unknown_rule: Incomplete or unknown disclosure rule, or calendar not covered.

Date semantics:
- NAV dates of domestic same-day funds are CN trading days (including HK Connect
  funds, which still publish NAV on HK-only holidays).
- QDII NAV dates are days open on both CN and the fund's overseas calendar;
  each is disclosed on the evening of the next CN trading day, so domestic
  holidays postpone disclosure instead of making it overdue.
- ``next_disclosure_date`` is the CN date on whose evening the next
  undisclosed NAV is expected; ``None`` when overdue or unknown.

Refresh flags (do not parse ``reason``):
- in_disclosure_window: waiting for tonight's NAV during 19:00-24:00.
- auto_refresh_eligible: in that window, or overdue, or source-failed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Mapping

from fund_analysis.fund_metadata import (
    RULE_CN_SAME_DAY_NIGHT,
    RULE_QDII_NEXT_DAY_NIGHT,
    RULE_UNKNOWN,
    FundMetadata,
)
from fund_analysis.trading_calendar import (
    CALENDAR_CN_STOCK,
    get_latest_trading_day,
    get_next_trading_day,
    get_previous_trading_day,
    is_trading_day,
)

STATUS_UPDATED = "updated"
STATUS_PENDING = "pending"
STATUS_OVERDUE = "overdue"
STATUS_MARKET_CLOSED = "market_closed"
STATUS_SOURCE_FAILED = "source_failed"
STATUS_UNKNOWN_RULE = "unknown_rule"

DISCLOSURE_WINDOW_START = time(19, 0)
_MAX_CALENDAR_SCAN_DAYS = 40


@dataclass(frozen=True)
class NavStatusResult:
    status: str
    target_nav_date: str | None
    reason: str
    in_disclosure_window: bool = False
    auto_refresh_eligible: bool = False
    next_disclosure_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _nav_status_result(
    status: str,
    target_nav_date: str | None,
    reason: str,
    *,
    in_disclosure_window: bool = False,
    next_disclosure_date: date | None = None,
) -> NavStatusResult:
    """Build a status result and derive refresh-eligibility flags.

    ``in_disclosure_window`` is true only while waiting for tonight's NAV
    during 19:00-24:00 on a disclosure day. ``auto_refresh_eligible`` is
    true for that window, plus overdue and source-failed rows that still
    need a one-shot refresh. Callers must not parse ``reason``.
    """
    if status in {STATUS_UPDATED, STATUS_MARKET_CLOSED, STATUS_UNKNOWN_RULE}:
        window = False
        eligible = False
    elif status in {STATUS_OVERDUE, STATUS_SOURCE_FAILED}:
        window = False
        eligible = True
    else:
        window = in_disclosure_window
        eligible = in_disclosure_window
    return NavStatusResult(
        status=status,
        target_nav_date=target_nav_date,
        reason=reason,
        in_disclosure_window=window,
        auto_refresh_eligible=eligible,
        next_disclosure_date=(
            next_disclosure_date.isoformat() if next_disclosure_date else None
        ),
    )


def _parse_date(d: date | str | None) -> date | None:
    if not d:
        return None
    if isinstance(d, date):
        return d
    try:
        return datetime.strptime(str(d)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _safe_date(fn: Any, *args: Any) -> date | None:
    """Evaluate an optional date; uncovered calendar years yield ``None``."""
    try:
        return fn(*args)
    except ValueError:
        return None


def _next_cn_trading_day(d: date) -> date:
    return get_next_trading_day(CALENDAR_CN_STOCK, d)


def _is_qdii_nav_day(calendar: str, d: date) -> bool:
    return is_trading_day(CALENDAR_CN_STOCK, d) and is_trading_day(calendar, d)


def _qdii_nav_day_before(calendar: str, d: date) -> date:
    cursor = d - timedelta(days=1)
    for _ in range(_MAX_CALENDAR_SCAN_DAYS):
        if _is_qdii_nav_day(calendar, cursor):
            return cursor
        cursor -= timedelta(days=1)
    raise ValueError(f"no QDII NAV day found before {d}")


def _qdii_nav_day_after(calendar: str, d: date) -> date:
    cursor = d + timedelta(days=1)
    for _ in range(_MAX_CALENDAR_SCAN_DAYS):
        if _is_qdii_nav_day(calendar, cursor):
            return cursor
        cursor += timedelta(days=1)
    raise ValueError(f"no QDII NAV day found after {d}")


def _has_cn_weekday_holiday(start: date, end: date) -> bool:
    """Whether a CN weekday holiday lies strictly between two dates."""
    cursor = start + timedelta(days=1)
    while cursor < end:
        if cursor.weekday() < 5 and not is_trading_day(CALENDAR_CN_STOCK, cursor):
            return True
        cursor += timedelta(days=1)
    return False


def resolve_nav_status(
    *,
    metadata: FundMetadata | Mapping[str, Any] | None,
    nav_date: date | str | None,
    as_of: datetime | None = None,
    snapshot_failed: bool = False,
    snapshot_error: str | None = None,
    is_cash: bool = False,
) -> NavStatusResult:
    """Derive the official NAV status for a holding at a given timestamp."""
    # 1. Source failed
    if snapshot_failed or (snapshot_error and str(snapshot_error).strip()):
        return _nav_status_result(
            STATUS_SOURCE_FAILED,
            str(nav_date)[:10] if nav_date else None,
            f"官方净值数据源刷新失败: {snapshot_error or '接口请求异常'}",
        )

    # 2. Cash assets
    meta_dict = metadata.to_dict() if isinstance(metadata, FundMetadata) else (metadata or {})
    fund_type = str(meta_dict.get("fund_type") or "").strip().lower()
    if is_cash or fund_type == "cash":
        as_of_date = (as_of or datetime.now().astimezone()).date()
        return _nav_status_result(
            STATUS_UPDATED,
            as_of_date.strftime("%Y-%m-%d"),
            "现金资产按日结算，无需按股票交易日披露净值",
        )

    # 3. Unknown rule
    rule = str(meta_dict.get("nav_disclosure_rule") or "").strip().lower()
    if not meta_dict or not rule or rule == RULE_UNKNOWN:
        return _nav_status_result(
            STATUS_UNKNOWN_RULE,
            str(nav_date)[:10] if nav_date else None,
            "缺少可靠净值披露规则，无法推导披露状态",
        )

    now = as_of or datetime.now().astimezone()
    calendar = str(meta_dict.get("calendar") or CALENDAR_CN_STOCK)
    official_d = _parse_date(nav_date)

    try:
        if rule == RULE_CN_SAME_DAY_NIGHT:
            return _resolve_cn_same_day(now, official_d)
        if rule == RULE_QDII_NEXT_DAY_NIGHT:
            return _resolve_qdii_next_day(now, official_d, calendar)
    except ValueError as exc:
        return _nav_status_result(
            STATUS_UNKNOWN_RULE,
            str(nav_date)[:10] if nav_date else None,
            str(exc),
        )

    # Fallback
    return _nav_status_result(
        STATUS_UNKNOWN_RULE,
        str(nav_date)[:10] if nav_date else None,
        f"未识别的净值披露规则: {rule}",
    )


def _resolve_cn_same_day(now: datetime, official_d: date | None) -> NavStatusResult:
    """Domestic same-day night disclosure; NAV dates follow CN trading days.

    HK Connect funds use this rule too: they publish NAV on HK-only holidays
    and skip CN holidays, so the fund's valuation calendar is not used here.
    """
    today = now.date()
    calendar = CALENDAR_CN_STOCK
    today_is_trading = is_trading_day(calendar, today)

    if not today_is_trading:
        last_td = get_latest_trading_day(calendar, today, include_today=False)
        target_str = last_td.strftime("%Y-%m-%d")
        next_disclosure = _safe_date(get_next_trading_day, calendar, today)
        if official_d and official_d >= last_td:
            return _nav_status_result(
                STATUS_MARKET_CLOSED,
                target_str,
                f"国内市场今日休市，上一交易日（{target_str}）净值已披露",
                next_disclosure_date=next_disclosure,
            )
        return _nav_status_result(
            STATUS_OVERDUE,
            target_str,
            f"国内市场今日休市，但上一交易日（{target_str}）净值逾期未披露",
        )

    target_str = today.strftime("%Y-%m-%d")
    if official_d and official_d >= today:
        return _nav_status_result(
            STATUS_UPDATED,
            target_str,
            f"目标净值日（{target_str}）官方净值已披露",
            next_disclosure_date=_safe_date(get_next_trading_day, calendar, today),
        )

    prev_td = get_previous_trading_day(calendar, today)
    if official_d and official_d < prev_td:
        return _nav_status_result(
            STATUS_OVERDUE,
            target_str,
            f"前一交易日（{prev_td}）官方净值未披露，数据已滞后",
        )

    if now.time() >= DISCLOSURE_WINDOW_START:
        return _nav_status_result(
            STATUS_PENDING,
            target_str,
            f"处于今日（{target_str}）官方净值正常披露窗口（19:00-24:00）",
            in_disclosure_window=True,
            next_disclosure_date=today,
        )
    return _nav_status_result(
        STATUS_PENDING,
        target_str,
        f"今日（{target_str}）官方净值预计将于 19:00 后陆续披露",
        next_disclosure_date=today,
    )


def _resolve_qdii_next_day(
    now: datetime,
    official_d: date | None,
    calendar: str,
) -> NavStatusResult:
    """QDII T+1 night disclosure driven by NAV dates and CN disclosure days."""
    today = now.date()
    # Validate today's coverage up front so uncovered years stay unknown.
    is_trading_day(CALENDAR_CN_STOCK, today)
    is_trading_day(calendar, today)

    target = _qdii_nav_day_before(calendar, today)
    target_str = target.strftime("%Y-%m-%d")
    target_disclosure = _next_cn_trading_day(target)

    # Latest NAV date whose disclosure evening has already passed.
    due = target
    for _ in range(_MAX_CALENDAR_SCAN_DAYS):
        if _next_cn_trading_day(due) < today:
            break
        due = _qdii_nav_day_before(calendar, due)

    if official_d and official_d >= target:
        following = _safe_date(_qdii_nav_day_after, calendar, target)
        next_disclosure = (
            _safe_date(_next_cn_trading_day, following) if following else None
        )
        suffix = (
            f"；下一次预计 {next_disclosure.isoformat()} 晚间披露"
            if next_disclosure
            else ""
        )
        return _nav_status_result(
            STATUS_UPDATED,
            target_str,
            f"最新海外交易日（{target_str}）净值已披露{suffix}",
            next_disclosure_date=next_disclosure,
        )

    if official_d and official_d < due:
        return _nav_status_result(
            STATUS_OVERDUE,
            target_str,
            f"{due} 净值应于 {_next_cn_trading_day(due)} 晚间披露，仍未更新",
        )

    disclosure_str = target_disclosure.isoformat()
    if target_disclosure == today:
        if now.time() >= DISCLOSURE_WINDOW_START:
            return _nav_status_result(
                STATUS_PENDING,
                target_str,
                f"处于 QDII 净值（{target_str}）正常披露窗口（19:00-24:00）",
                in_disclosure_window=True,
                next_disclosure_date=target_disclosure,
            )
        return _nav_status_result(
            STATUS_PENDING,
            target_str,
            f"海外交易日（{target_str}）净值预计今晚 19:00 后披露",
            next_disclosure_date=target_disclosure,
        )

    if _has_cn_weekday_holiday(target, target_disclosure):
        reason = f"国内休市，海外交易日（{target_str}）净值顺延至 {disclosure_str} 晚间披露"
    else:
        reason = f"海外交易日（{target_str}）净值预计于 {disclosure_str} 晚间披露"
    return _nav_status_result(
        STATUS_PENDING,
        target_str,
        reason,
        next_disclosure_date=target_disclosure,
    )
