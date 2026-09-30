from __future__ import annotations

import csv
import math
from datetime import datetime
from pathlib import Path

from .models import PortfolioHolding, PortfolioInputError

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "code": ("code", "fund_code", "基金代码", "代码"),
    "name": ("name", "fund_name", "基金名称", "名称"),
    "amount": ("amount", "market_value", "holding_amount", "持仓金额", "金额", "市值"),
    "weight_pct": ("weight", "weight_pct", "ratio", "占比", "持仓占比", "仓位"),
    "cost": ("cost", "cost_amount", "cost_basis", "成本", "持仓成本"),
    "asset_type": ("asset_type", "account_type", "账户资产类型", "资产归类"),
    "asset_class": ("asset_class", "asset", "资产类别", "资产类型"),
    "theme": ("theme", "sector", "topic", "主题", "行业", "赛道"),
    "holding_profit_rate": (
        "holding_profit_rate",
        "profit_rate",
        "return_pct",
        "收益率",
        "持仓收益率",
    ),
    "daily_profit": ("daily_profit", "today_profit", "当日收益", "今日收益"),
    "cumulative_profit": ("cumulative_profit", "total_profit", "累计收益"),
    "year_profit": ("year_profit", "ytd_profit", "本年收益", "今年收益"),
    "amount_nav_date": (
        "amount_nav_date",
        "holding_nav_date",
        "金额基准日",
        "净值基准日",
        "金额净值日期",
    ),
}
IGNORED_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "dca_status": ("dca_status",),
}

_ALIASES_BY_NORMALIZED_KEY: dict[str, str]
_IGNORED_FIELDS_BY_NORMALIZED_KEY: dict[str, str]


def read_portfolio_csv(
    path: str | Path,
    *,
    ignored_fields: set[str] | None = None,
) -> list[PortfolioHolding]:
    input_path = Path(path)
    if not input_path.exists():
        raise PortfolioInputError(f"Portfolio CSV does not exist: {input_path}")

    try:
        with input_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise PortfolioInputError("Portfolio CSV must include a header row.")
            rows = list(reader)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise PortfolioInputError(f"Unable to read portfolio CSV: {exc}") from exc

    if not rows:
        raise PortfolioInputError("Portfolio CSV must include at least one holding.")

    holdings = [
        _parse_row(row, index + 2, ignored_fields=ignored_fields)
        for index, row in enumerate(rows)
    ]
    _validate_unique_codes(holdings)
    _validate_sizing_mode(holdings)
    return holdings


def _parse_row(
    row: dict[str, object],
    line_no: int,
    *,
    ignored_fields: set[str] | None = None,
) -> PortfolioHolding:
    if None in row:
        raise PortfolioInputError(f"Line {line_no}: unexpected extra CSV columns.")

    normalized = {_normalize_header(key): _cell_to_text(value) for key, value in row.items()}

    values: dict[str, str] = {}
    extras: list[str] = []
    for key, value in normalized.items():
        canonical = _ALIASES_BY_NORMALIZED_KEY.get(key)
        if canonical:
            values[canonical] = value.strip()
        elif ignored := _IGNORED_FIELDS_BY_NORMALIZED_KEY.get(key):
            if ignored_fields is not None:
                ignored_fields.add(ignored)
        else:
            extras.append(key)
    if extras:
        extra_columns = ", ".join(sorted(extras))
        raise PortfolioInputError(f"Line {line_no}: unexpected CSV columns: {extra_columns}.")

    name = values.get("name", "").strip()
    asset_type = _blank_to_none(values.get("asset_type"))
    asset_class = _blank_to_none(values.get("asset_class"))
    code = values.get("code", "").strip()
    if not code and _is_cash_asset(asset_type, name, asset_class):
        code = f"CASH:{name or '未命名现金资产'}"
    if not code or not name:
        raise PortfolioInputError(f"Line {line_no}: code and name are required.")

    return PortfolioHolding(
        code=code,
        name=name,
        amount=_parse_number(values.get("amount"), "amount", line_no),
        reported_weight_pct=_parse_percent(values.get("weight_pct"), "weight_pct", line_no),
        cost=_parse_number(values.get("cost"), "cost", line_no),
        asset_type=asset_type,
        asset_class=asset_class,
        theme=_blank_to_none(values.get("theme")),
        holding_profit_rate=_parse_percent(
            values.get("holding_profit_rate"),
            "holding_profit_rate",
            line_no,
            allow_negative=True,
            max_value=None,
        ),
        daily_profit=_parse_number(
            values.get("daily_profit"), "daily_profit", line_no, allow_negative=True
        ),
        cumulative_profit=_parse_number(
            values.get("cumulative_profit"),
            "cumulative_profit",
            line_no,
            allow_negative=True,
        ),
        year_profit=_parse_number(
            values.get("year_profit"), "year_profit", line_no, allow_negative=True
        ),
        amount_nav_date=_parse_iso_date(values.get("amount_nav_date"), line_no),
    )


def _validate_unique_codes(holdings: list[PortfolioHolding]) -> None:
    first_line_by_code: dict[str, int] = {}
    for index, holding in enumerate(holdings):
        line_no = index + 2
        normalized_code = holding.code.strip().casefold()
        first_line = first_line_by_code.get(normalized_code)
        if first_line is not None:
            raise PortfolioInputError(
                f"Line {line_no}: duplicate code {holding.code!r}; first seen on line {first_line}."
            )
        first_line_by_code[normalized_code] = line_no


def _validate_sizing_mode(holdings: list[PortfolioHolding]) -> None:
    has_amount_for_all = all(holding.amount is not None for holding in holdings)
    has_weight_for_all = all(holding.reported_weight_pct is not None for holding in holdings)
    total_amount = sum(holding.amount or 0 for holding in holdings)
    total_weight = sum(holding.reported_weight_pct or 0 for holding in holdings)

    if has_amount_for_all and total_amount > 0:
        return
    if has_weight_for_all and total_weight > 0:
        return
    raise PortfolioInputError(
        "CSV must include amount for every holding or weight_pct for every holding."
    )


def _is_cash_asset(
    asset_type: str | None, name: str, asset_class: str | None = None
) -> bool:
    markers = {
        (asset_type or "").strip().lower().replace("-", "_"),
        (asset_class or "").strip().lower().replace("-", "_"),
    }
    if markers & {"cash", "yuebao", "yu_e_bao", "现金", "余额宝", "货币", "货币基金"}:
        return True
    return name.strip() in {"现金", "余额宝"}


def _normalize_header(value: str | None) -> str:
    if value is None:
        return ""
    return value.strip().lower().replace(" ", "").replace("_", "")


def _cell_to_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ",".join(str(item) for item in value)
    return str(value)


def _blank_to_none(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _parse_iso_date(value: str | None, line_no: int) -> str | None:
    text = _blank_to_none(value)
    if text is None:
        return None
    for date_format, length in (("%Y-%m-%d", 10), ("%Y%m%d", 8)):
        try:
            return datetime.strptime(text[:length], date_format).date().isoformat()
        except ValueError:
            continue
    raise PortfolioInputError(
        f"Line {line_no}: invalid amount_nav_date: {value!r}; expected YYYY-MM-DD."
    )


def _parse_number(
    value: str | None,
    field_name: str,
    line_no: int,
    *,
    allow_negative: bool = False,
) -> float | None:
    if value is None or not value.strip():
        return None
    cleaned = _clean_numeric(value)
    try:
        parsed = float(cleaned)
    except ValueError as exc:
        raise PortfolioInputError(
            f"Line {line_no}: invalid {field_name} numeric value: {value!r}"
        ) from exc
    if not math.isfinite(parsed):
        raise PortfolioInputError(f"Line {line_no}: {field_name} must be finite.")
    if not allow_negative and parsed < 0:
        raise PortfolioInputError(
            f"Line {line_no}: {field_name} must be greater than or equal to 0."
        )
    return parsed


def _parse_percent(
    value: str | None,
    field_name: str,
    line_no: int,
    *,
    allow_negative: bool = False,
    max_value: float | None = 100.0,
) -> float | None:
    if value is None or not value.strip():
        return None
    raw = value.strip()
    cleaned = _clean_numeric(raw)
    try:
        parsed = float(cleaned)
    except ValueError as exc:
        raise PortfolioInputError(
            f"Line {line_no}: invalid {field_name} percent value: {value!r}"
        ) from exc
    if not math.isfinite(parsed):
        raise PortfolioInputError(f"Line {line_no}: {field_name} must be finite.")
    if "%" not in raw and abs(parsed) <= 1:
        parsed *= 100
    if not allow_negative and parsed < 0:
        raise PortfolioInputError(
            f"Line {line_no}: {field_name} must be greater than or equal to 0."
        )
    if max_value is not None and parsed > max_value:
        raise PortfolioInputError(f"Line {line_no}: {field_name} must be <= {max_value:g}.")
    return parsed


def _clean_numeric(value: str) -> str:
    return (
        value.strip()
        .replace(",", "")
        .replace("￥", "")
        .replace("¥", "")
        .replace("元", "")
        .replace("%", "")
    )


_ALIASES_BY_NORMALIZED_KEY = {
    _normalize_header(alias): field for field, aliases in FIELD_ALIASES.items() for alias in aliases
}
_IGNORED_FIELDS_BY_NORMALIZED_KEY = {
    _normalize_header(alias): field
    for field, aliases in IGNORED_FIELD_ALIASES.items()
    for alias in aliases
}
