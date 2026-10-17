"""固定周期定额策略的版本定义与校验。

首版只有一种固定策略：每周固定星期与时刻、按基金代码顺序对每只基金固定金额买入。
策略版本不可覆盖；修订只能新增更晚 `effective_from` 的版本，下一个尚未开始的周期生效。
版本只按账户归属，不携带渠道或份额类别（规则只按基金代码选取）。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, time as time_of_day
from decimal import Decimal
from typing import Any, Mapping

from .decimals import MONEY_PLACES, DecimalInputError, parse_decimal

SUPPORTED_STRATEGY_TIMEZONES = frozenset({"Asia/Shanghai"})
# 首版只有一种固定定额策略：每周三 14:00，执行窗口至 14:10（不含结束）。
STRATEGY_WEEKDAY = 3
STRATEGY_TIME = time_of_day(14, 0)
STRATEGY_WINDOW_END = time_of_day(14, 10)
_CODE = re.compile(r"^\d{6}$")
_REQUIRED_FIELDS = (
    "account_id",
    "effective_from",
    "timezone",
    "weekday",
    "time",
    "window_end",
    "amounts",
    "rule_set",
)


class StrategyValidationError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class StrategyVersion:
    id: str
    account_id: str
    effective_from: date
    timezone: str
    weekday: int  # ISO 1=周一 … 7=周日
    time: time_of_day
    window_end: time_of_day
    amounts: dict[str, Decimal]
    rule_set: str
    created_at: str


def _text(record: Mapping[str, Any], key: str, errors: list[str]) -> str:
    """非空字符串字段；缺失或空白写入错误并返回空串，由调用方统一抛出。"""
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{key} 必须是非空字符串")
        return ""
    return value.strip()


def strategy_version_id(account_id: str, effective_from: str) -> str:
    """确定性版本 ID：同一账户同一生效日只对应一个版本。"""
    digest = hashlib.sha256(f"{account_id}|{effective_from}".encode("utf-8")).hexdigest()
    return f"strat_{digest[:16]}"


def validate_strategy_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """校验并返回可存储的规范策略版本记录；未知字段直接拒绝。

    版本 ID 由账户与生效日确定性派生：同一账户同一生效日只允许一个版本。
    """
    errors: list[str] = []
    if not isinstance(record, Mapping):
        raise StrategyValidationError(["策略版本必须是 JSON 对象"])
    unknown = sorted(set(record) - set(_REQUIRED_FIELDS))
    if unknown:
        errors.append(f"未知字段：{', '.join(unknown)}")
    account_id = _text(record, "account_id", errors)
    try:
        effective_from = date.fromisoformat(str(record.get("effective_from")))
    except ValueError:
        errors.append("effective_from 必须是 YYYY-MM-DD")
        effective_from = date(2000, 1, 1)
    timezone = record.get("timezone")
    if timezone not in SUPPORTED_STRATEGY_TIMEZONES:
        errors.append("timezone 目前只支持 Asia/Shanghai")
    weekday = record.get("weekday")
    if isinstance(weekday, bool) or not isinstance(weekday, int) or not 1 <= weekday <= 7:
        errors.append("weekday 必须是 1–7 的整数（ISO，3 为周三）")
    clocks: dict[str, time_of_day] = {}
    for key in ("time", "window_end"):
        try:
            clocks[key] = time_of_day.fromisoformat(str(record.get(key)))
        except ValueError:
            errors.append(f"{key} 必须是 HH:MM")
            clocks[key] = time_of_day(0, 0)
    if clocks["window_end"] <= clocks["time"]:
        errors.append("window_end 必须晚于 time（执行窗口不跨午夜）")
    amounts_raw = record.get("amounts")
    amounts: dict[str, str] = {}
    if not isinstance(amounts_raw, Mapping) or not amounts_raw:
        errors.append("amounts 必须是非空对象（基金代码 → 金额）")
    else:
        for code in amounts_raw:
            if not isinstance(code, str) or not _CODE.match(code):
                errors.append(f"amounts 键必须是 6 位基金代码：{code!r}")
                continue
            try:
                value = parse_decimal(amounts_raw[code], f"amounts.{code}", max_places=MONEY_PLACES, allow_zero=False)
            except DecimalInputError as exc:
                errors.append(str(exc))
                continue
            amounts[code] = f"{value:f}"
    rule_set = _text(record, "rule_set", errors)
    if errors:
        raise StrategyValidationError(errors)
    canonical: dict[str, Any] = {
        "account_id": account_id,
        "effective_from": effective_from.isoformat(),
        "timezone": timezone,
        "weekday": weekday,
        "time": f"{clocks['time']:%H:%M}",
        "window_end": f"{clocks['window_end']:%H:%M}",
        "amounts": dict(sorted(amounts.items())),
        "rule_set": rule_set,
    }
    canonical["id"] = strategy_version_id(account_id, effective_from.isoformat())
    return canonical


def load_strategy_version(row: Mapping[str, Any]) -> StrategyVersion:
    """把已存储的策略版本行转换为计算对象。"""
    amounts = {code: Decimal(value) for code, value in json.loads(row["amounts_json"]).items()}
    return StrategyVersion(
        id=row["id"],
        account_id=row["account_id"],
        effective_from=date.fromisoformat(row["effective_from"]),
        timezone=row["timezone"],
        weekday=int(row["weekday"]),
        time=time_of_day.fromisoformat(row["time"]),
        window_end=time_of_day.fromisoformat(row["window_end"]),
        amounts=amounts,
        rule_set=row["rule_set"],
        created_at=row["created_at"],
    )


def default_fixture_strategy(account_id: str, effective_from: date, codes: list[str]) -> dict[str, Any]:
    """隔离验收使用的固定策略样例：每周三 14:00、每只 500.00 元。"""
    return {
        "account_id": account_id,
        "effective_from": effective_from.isoformat(),
        "timezone": "Asia/Shanghai",
        "weekday": STRATEGY_WEEKDAY,
        "time": f"{STRATEGY_TIME:%H:%M}",
        "window_end": f"{STRATEGY_WINDOW_END:%H:%M}",
        "amounts": {code: "500.00" for code in sorted(codes)},
        "rule_set": "fixture_qdii_c_v1",
    }
