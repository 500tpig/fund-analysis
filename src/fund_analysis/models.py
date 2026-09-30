from __future__ import annotations

from dataclasses import dataclass


class PortfolioInputError(ValueError):
    """Raised when a holdings CSV cannot be parsed safely."""


@dataclass(frozen=True)
class PortfolioHolding:
    code: str
    name: str
    amount: float | None = None
    reported_weight_pct: float | None = None
    cost: float | None = None
    asset_type: str | None = None
    asset_class: str | None = None
    theme: str | None = None
    holding_profit_rate: float | None = None
    daily_profit: float | None = None
    cumulative_profit: float | None = None
    year_profit: float | None = None
    amount_nav_date: str | None = None
