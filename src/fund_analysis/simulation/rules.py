"""基金渠道规则版本、开放日与费用计算。

规则以不可变版本保存。`null` 只表示未知，不表示免费或无限额；
无限额必须显式写成 ``"unlimited"``。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from ..trading_calendar import (
    CALENDAR_CN_STOCK,
    CALENDAR_HK_STOCK,
    CALENDAR_US_STOCK,
    is_trading_day,
)
from .decimals import (
    MONEY_PLACES,
    NAV_MAX_PLACES,
    ROUNDING_MODES,
    SHARE_MAX_PLACES,
    DecimalInputError,
    parse_decimal,
    places_of,
    round_money,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")
ENVIRONMENTS = frozenset({"fixture", "production"})
RULE_STATUSES = frozenset({"verified", "incomplete", "expired", "conflict"})
SUPPORTED_CALENDARS = frozenset({CALENDAR_CN_STOCK, CALENDAR_HK_STOCK, CALENDAR_US_STOCK})
FUND_TYPES = frozenset({"active", "index"})
CHANNEL_ALIPAY = "ant_fund_alipay"
UNLIMITED = "unlimited"

IDENTITY_FIELDS = (
    "id",
    "environment",
    "status",
    "code",
    "name",
    "channel",
    "share_class",
    "currency",
    "effective_from",
    "effective_to",
)
BODY_FIELDS = (
    "fund_type",
    "index_group",
    "subscription_fee_rate",
    "redemption_fee_tiers",
    "min_buy",
    "min_add",
    "max_daily_buy",
    "min_sell_shares",
    "min_remaining_shares",
    "share_quantum",
    "rounding",
    "cutoff",
    "cancel_cutoff",
    "working_calendar",
    "open_calendars",
    "closed_dates",
    "nav_due_working_days",
    "nav_due_time",
    "confirm_working_days",
    "confirm_time",
    "payment_working_days",
    "payment_time",
    "holding_period",
    "lot_order",
    "dividend_mode",
)
EVIDENCE_FIELDS = ("source_url", "evidence_ref", "published_at", "checked_at")
HOLDING_PERIOD_CONFIRM_TO_REDEEM = "buy_confirm_inclusive_to_sell_confirm_exclusive"


class RuleValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


class CalendarUnknown(Exception):
    """日历未覆盖对应年份时不猜工作日。"""


@dataclass(frozen=True)
class FeeTier:
    min_days: int
    max_days: int | None
    rate: Decimal


@dataclass(frozen=True)
class FundRule:
    id: str
    environment: str
    status: str
    code: str
    name: str
    channel: str
    share_class: str
    currency: str
    effective_from: date
    effective_to: date | None
    fund_type: str
    index_group: str | None
    subscription_fee_rate: Decimal
    redemption_fee_tiers: tuple[FeeTier, ...]
    min_buy: Decimal
    min_add: Decimal
    max_daily_buy: Decimal | None  # None 表示显式无限额
    min_sell_shares: Decimal
    min_remaining_shares: Decimal
    share_quantum: Decimal
    rounding: str
    cutoff: time
    cancel_cutoff: time
    working_calendar: str
    open_calendars: tuple[str, ...]
    closed_dates: frozenset[str]
    nav_due_working_days: int
    nav_due_time: time
    confirm_working_days: int
    confirm_time: time
    payment_working_days: int
    payment_time: time

    # ---- 日历 -------------------------------------------------------------
    def is_working_day(self, day: date) -> bool:
        return _trading(self.working_calendar, day)

    def is_open_day(self, day: date) -> bool:
        if day.isoformat() in self.closed_dates:
            return False
        return all(_trading(calendar, day) for calendar in self.open_calendars)

    def add_working_days(self, day: date, count: int) -> date:
        current = day
        added = 0
        while added < count:
            current += timedelta(days=1)
            if self.is_working_day(current):
                added += 1
        return current

    def nav_due_at(self, nav_date: date) -> datetime:
        return local_datetime(
            self.add_working_days(nav_date, self.nav_due_working_days), self.nav_due_time
        )

    def expected_latest_nav_date(self, now: datetime) -> date | None:
        """截至 now 已应披露的最近净值日；用于判断持仓净值是否缺失。"""
        local_day = now.astimezone(SHANGHAI).date()
        for offset in range(0, 60):
            day = local_day - timedelta(days=offset)
            if self.is_open_day(day) and self.nav_due_at(day) <= now:
                return day
        return None

    # ---- 费用与取整 --------------------------------------------------------
    def quantize_shares(self, value: Decimal) -> Decimal:
        return value.quantize(self.share_quantum, rounding=ROUNDING_MODES[self.rounding])

    def subscription_split(self, amount: Decimal) -> tuple[Decimal, Decimal]:
        """外扣申购费：净申购额 = 含费额 / (1 + 费率)，按分四舍五入。"""
        net = round_money(amount / (Decimal(1) + self.subscription_fee_rate))
        return net, amount - net

    def redemption_rate(self, holding_days: int) -> Decimal:
        for tier in self.redemption_fee_tiers:
            if holding_days >= tier.min_days and (
                tier.max_days is None or holding_days < tier.max_days
            ):
                return tier.rate
        raise ValueError(f"no redemption tier for {holding_days} days")


def holding_days(buy_confirm_date: date, sell_confirm_date: date) -> int:
    """申购确认日计第 1 天，截至赎回确认日前一自然日。"""
    return (sell_confirm_date - buy_confirm_date).days


def local_datetime(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=SHANGHAI)


def _trading(calendar: str, day: date) -> bool:
    try:
        return is_trading_day(calendar, day)
    except ValueError as exc:
        raise CalendarUnknown(str(exc)) from exc


# ---- 校验 -----------------------------------------------------------------


def _text(record: Mapping[str, Any], key: str, errors: list[str]) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{key} 必须是非空字符串")
        return ""
    return value.strip()


def _date(value: Any, key: str, errors: list[str], *, optional: bool = False) -> date | None:
    if value is None and optional:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        errors.append(f"{key} 必须是 YYYY-MM-DD")
        return None


def _clock(value: Any, key: str, errors: list[str]) -> time:
    try:
        parsed = time.fromisoformat(str(value))
    except ValueError:
        errors.append(f"{key} 必须是 HH:MM")
        return time(0, 0)
    return parsed


def _decimal(value: Any, key: str, errors: list[str], places: int) -> Decimal:
    try:
        return parse_decimal(value, key, max_places=places)
    except DecimalInputError as exc:
        errors.append(str(exc))
        return Decimal(0)


def _int(value: Any, key: str, errors: list[str]) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        errors.append(f"{key} 必须是非负整数")
        return 0
    return value


def validate_rule_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """校验并返回可存储的规范规则记录。

    `verified` 必须字段齐全；其他状态只校验身份字段，正文按原样留档且不能用于下单。
    """
    errors: list[str] = []
    if not isinstance(record, Mapping):
        raise RuleValidationError(["规则必须是 JSON 对象"])
    unknown = sorted(set(record) - set(IDENTITY_FIELDS) - {"body", "evidence"})
    if unknown:
        errors.append(f"未知字段：{', '.join(unknown)}")
    for key in ("id", "code", "name", "channel", "share_class", "currency"):
        _text(record, key, errors)
    if record.get("environment") not in ENVIRONMENTS:
        errors.append("environment 必须是 fixture 或 production")
    if record.get("status") not in RULE_STATUSES:
        errors.append("status 必须是 verified/incomplete/expired/conflict")
    effective_from = _date(record.get("effective_from"), "effective_from", errors)
    effective_to = _date(record.get("effective_to"), "effective_to", errors, optional=True)
    if effective_from and effective_to and effective_to < effective_from:
        errors.append("effective_to 不能早于 effective_from")
    evidence = record.get("evidence")
    if not isinstance(evidence, Mapping):
        errors.append("evidence 必须是对象")
    else:
        for key in EVIDENCE_FIELDS:
            if key not in evidence:
                errors.append(f"evidence.{key} 缺失（未知请写 null）")
    body = record.get("body")
    if not isinstance(body, Mapping):
        errors.append("body 必须是对象")
    elif record.get("status") == "verified":
        _validate_body(body, errors)
    if errors:
        raise RuleValidationError(errors)
    return json.loads(json.dumps(record, ensure_ascii=False, sort_keys=True))


def _validate_body(body: Mapping[str, Any], errors: list[str]) -> None:
    missing = [key for key in BODY_FIELDS if key not in body]
    if missing:
        errors.append(f"body 缺少字段：{', '.join(missing)}")
        return
    unknown_values = [
        key for key in BODY_FIELDS if body[key] is None and key != "index_group"
    ]
    if unknown_values:
        errors.append(f"verified 规则不能含未知字段：{', '.join(unknown_values)}")
        return
    extra = sorted(set(body) - set(BODY_FIELDS))
    if extra:
        errors.append(f"body 含未知字段：{', '.join(extra)}")
    fund_type = body["fund_type"]
    if fund_type not in FUND_TYPES:
        errors.append("fund_type 必须是 active 或 index")
    if fund_type == "index" and not body["index_group"]:
        errors.append("指数基金必须给出 index_group")
    if fund_type == "active" and body["index_group"] is not None:
        errors.append("主动基金不能虚构 index_group")
    _decimal(body["subscription_fee_rate"], "subscription_fee_rate", errors, 6)
    _validate_tiers(body["redemption_fee_tiers"], errors)
    for key in ("min_buy", "min_add"):
        _decimal(body[key], key, errors, MONEY_PLACES)
    if body["max_daily_buy"] != UNLIMITED:
        _decimal(body["max_daily_buy"], "max_daily_buy", errors, MONEY_PLACES)
    for key in ("min_sell_shares", "min_remaining_shares"):
        _decimal(body[key], key, errors, SHARE_MAX_PLACES)
    quantum = _decimal(body["share_quantum"], "share_quantum", errors, SHARE_MAX_PLACES)
    if quantum <= 0 or quantum.normalize() != Decimal(1).scaleb(-places_of(quantum)):
        errors.append("share_quantum 必须是 1、0.1、0.01 等十进制单位")
    if body["rounding"] not in ROUNDING_MODES:
        errors.append("rounding 必须是 ROUND_HALF_UP 或 ROUND_DOWN")
    for key in ("cutoff", "cancel_cutoff", "nav_due_time", "confirm_time", "payment_time"):
        _clock(body[key], key, errors)
    calendars = [body["working_calendar"], *(body["open_calendars"] or [])]
    if not body["open_calendars"] or any(c not in SUPPORTED_CALENDARS for c in calendars):
        errors.append("working_calendar/open_calendars 必须使用已支持的交易日历")
    if not isinstance(body["closed_dates"], list):
        errors.append("closed_dates 必须是日期数组")
    else:
        for value in body["closed_dates"]:
            _date(value, "closed_dates", errors)
    for key in ("nav_due_working_days", "confirm_working_days", "payment_working_days"):
        _int(body[key], key, errors)
    if body["holding_period"] != HOLDING_PERIOD_CONFIRM_TO_REDEEM:
        errors.append("holding_period 只支持确认日起算至赎回确认日前一日")
    if body["lot_order"] != "FIFO":
        errors.append("lot_order 只支持 FIFO")
    if body["dividend_mode"] != "cash":
        errors.append("dividend_mode 只支持 cash")


def _validate_tiers(value: Any, errors: list[str]) -> None:
    if not isinstance(value, list) or not value:
        errors.append("redemption_fee_tiers 必须是非空数组")
        return
    expected_min = 0
    for index, tier in enumerate(value):
        if not isinstance(tier, Mapping):
            errors.append("redemption_fee_tiers 每项必须是对象")
            return
        if tier.get("min_days") != expected_min:
            errors.append("redemption_fee_tiers 必须从 0 天开始连续分段")
            return
        _decimal(tier.get("rate"), "redemption_fee_tiers.rate", errors, 6)
        max_days = tier.get("max_days")
        is_last = index == len(value) - 1
        if is_last:
            if max_days is not None:
                errors.append("最后一个赎回费分段的 max_days 必须为 null")
        elif not isinstance(max_days, int) or max_days <= expected_min:
            errors.append("redemption_fee_tiers 的 max_days 必须递增")
            return
        else:
            expected_min = max_days


def load_rule(record: Mapping[str, Any]) -> FundRule:
    """把已校验的 verified 规则转换为计算对象。"""
    if record.get("status") != "verified":
        raise RuleValidationError([f"规则 {record.get('id')} 不是 verified"])
    body = record["body"]
    tiers = tuple(
        FeeTier(int(tier["min_days"]), tier["max_days"], Decimal(tier["rate"]))
        for tier in body["redemption_fee_tiers"]
    )
    max_daily = body["max_daily_buy"]
    return FundRule(
        id=record["id"],
        environment=record["environment"],
        status=record["status"],
        code=record["code"],
        name=record["name"],
        channel=record["channel"],
        share_class=record["share_class"],
        currency=record["currency"],
        effective_from=date.fromisoformat(record["effective_from"]),
        effective_to=(
            date.fromisoformat(record["effective_to"]) if record.get("effective_to") else None
        ),
        fund_type=body["fund_type"],
        index_group=body["index_group"],
        subscription_fee_rate=Decimal(body["subscription_fee_rate"]),
        redemption_fee_tiers=tiers,
        min_buy=Decimal(body["min_buy"]),
        min_add=Decimal(body["min_add"]),
        max_daily_buy=None if max_daily == UNLIMITED else Decimal(max_daily),
        min_sell_shares=Decimal(body["min_sell_shares"]),
        min_remaining_shares=Decimal(body["min_remaining_shares"]),
        share_quantum=Decimal(body["share_quantum"]),
        rounding=body["rounding"],
        cutoff=time.fromisoformat(body["cutoff"]),
        cancel_cutoff=time.fromisoformat(body["cancel_cutoff"]),
        working_calendar=body["working_calendar"],
        open_calendars=tuple(body["open_calendars"]),
        closed_dates=frozenset(body["closed_dates"]),
        nav_due_working_days=int(body["nav_due_working_days"]),
        nav_due_time=time.fromisoformat(body["nav_due_time"]),
        confirm_working_days=int(body["confirm_working_days"]),
        confirm_time=time.fromisoformat(body["confirm_time"]),
        payment_working_days=int(body["payment_working_days"]),
        payment_time=time.fromisoformat(body["payment_time"]),
    )


def nav_places_ok(value: Decimal) -> bool:
    return places_of(value) <= NAV_MAX_PLACES


# ---- 阶段 6 隔离规则样例（simulation-plan 第 2.5 节） ------------------------

FIXTURE_RULE_ID = "fixture_qdii_c_v1"

FIXTURE_QDII_C_V1: dict[str, Any] = {
    "id": FIXTURE_RULE_ID,
    "environment": "fixture",
    "status": "verified",
    "code": "016665",
    "name": "天弘全球高端制造混合(QDII)C",
    "channel": CHANNEL_ALIPAY,
    "share_class": "C",
    "currency": "CNY",
    "effective_from": "2025-11-13",
    "effective_to": None,
    "evidence": {
        "source_url": "https://www.9fzt.com/detail/fund_016664_2_10636438.html",
        "evidence_ref": (
            "天弘招募说明书转载，落款 2025-11-13，第九章第 36–43 页；"
            "SHA-256 223b11d2a76b4efb63884e7f152d808b32537ed9d015407bbc07d26db198aca4"
        ),
        "published_at": "2025-11-13",
        "checked_at": "2026-10-16",
        "assumptions": [
            "日累计最高 1,000.00 元是测试约束，无渠道事实含义",
            "15:00 申请与撤单截止、NAV 归属申请日为隔离模型假设",
            "确认 T+2、到账 T+10 工作日 18:00 由历史条款上限构造，不等于支付宝实际时刻",
            "持有期首尾计数与分红权益条件为测试假设",
        ],
    },
    "body": {
        "fund_type": "active",
        "index_group": None,
        "subscription_fee_rate": "0",
        "redemption_fee_tiers": [
            {"min_days": 0, "max_days": 7, "rate": "0.015"},
            {"min_days": 7, "max_days": 30, "rate": "0.005"},
            {"min_days": 30, "max_days": None, "rate": "0"},
        ],
        "min_buy": "0.10",
        "min_add": "0.10",
        "max_daily_buy": "1000.00",
        "min_sell_shares": "0.10",
        "min_remaining_shares": "0.10",
        "share_quantum": "0.01",
        "rounding": "ROUND_HALF_UP",
        "cutoff": "15:00",
        "cancel_cutoff": "15:00",
        "working_calendar": CALENDAR_CN_STOCK,
        "open_calendars": [CALENDAR_CN_STOCK, CALENDAR_US_STOCK],
        "closed_dates": [],
        "nav_due_working_days": 2,
        "nav_due_time": "18:00",
        "confirm_working_days": 2,
        "confirm_time": "18:00",
        "payment_working_days": 10,
        "payment_time": "18:00",
        "holding_period": HOLDING_PERIOD_CONFIRM_TO_REDEEM,
        "lot_order": "FIFO",
        "dividend_mode": "cash",
    },
}


def fixture_variant(rule_id: str, code: str, name: str, **body_overrides: Any) -> dict[str, Any]:
    """基于 fixture_qdii_c_v1 派生的测试变体，仅用于 fixture 环境的边界算例。"""
    record = json.loads(json.dumps(FIXTURE_QDII_C_V1))
    record["id"] = rule_id
    record["code"] = code
    record["name"] = name
    record["evidence"]["assumptions"] = [
        *record["evidence"]["assumptions"],
        f"测试变体：{', '.join(sorted(body_overrides)) or '无'} 为算例构造值",
    ]
    record["body"].update(body_overrides)
    return record
