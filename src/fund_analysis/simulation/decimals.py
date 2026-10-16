"""模拟账本的十进制解析与格式化，禁止 float 参与金额、份额和净值。"""

from __future__ import annotations

import re
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
ZERO = Decimal("0")
MONEY_PLACES = 2
SHARE_MAX_PLACES = 4
NAV_MAX_PLACES = 8
ROUNDING_MODES = {"ROUND_HALF_UP": ROUND_HALF_UP, "ROUND_DOWN": ROUND_DOWN}

_DECIMAL_TEXT = re.compile(r"^-?(0|[1-9]\d*)(\.\d+)?$")


class DecimalInputError(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


def places_of(value: Decimal) -> int:
    exponent = value.as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


def parse_decimal(
    value: object,
    field: str,
    *,
    max_places: int,
    allow_negative: bool = False,
    allow_zero: bool = True,
) -> Decimal:
    """只接受普通十进制字符串；拒绝 JSON number、科学计数法、NaN 和超精度。"""
    if isinstance(value, bool) or not isinstance(value, str):
        raise DecimalInputError(field, "必须是十进制字符串")
    text = value.strip()
    if not _DECIMAL_TEXT.match(text):
        raise DecimalInputError(field, "格式无效")
    number = Decimal(text)
    if places_of(number) > max_places:
        raise DecimalInputError(field, f"最多 {max_places} 位小数")
    if number < 0 and not allow_negative:
        raise DecimalInputError(field, "不能为负数")
    if number == 0 and not allow_zero:
        raise DecimalInputError(field, "不能为零")
    return number


def round_money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def money_text(value: Decimal) -> str:
    """金额统一两位小数；调用方必须先按规则取整，这里拒绝隐式舍入。"""
    quantized = value.quantize(CENT)
    if quantized != value:
        raise ValueError(f"amount is not rounded to cents: {value}")
    return f"{quantized + ZERO:f}"


def decimal_text(value: Decimal) -> str:
    """规范十进制文本，不产生科学计数法；保留调用方给定的精度。"""
    return f"{value + ZERO:f}"


def quantize_exact(value: Decimal, quantum: Decimal) -> str:
    quantized = value.quantize(quantum)
    if quantized != value:
        raise ValueError(f"value {value} does not fit quantum {quantum}")
    return f"{quantized + ZERO:f}"


def dec(value: str | None) -> Decimal:
    """读取库内已规范化的十进制文本。"""
    if value is None:
        raise ValueError("decimal value is missing")
    return Decimal(value)
