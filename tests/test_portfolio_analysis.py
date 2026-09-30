from __future__ import annotations

import unittest
from datetime import date, timedelta

from fund_analysis.fund_data import FundDataFetchError
from fund_analysis.portfolio_analysis import (
    CONTRIBUTION_NOTE,
    CURRENCY_UNAVAILABLE_REASON,
    INDUSTRY_UNAVAILABLE_REASON,
    MIN_RISK_SAMPLES,
    OVERLAP_UNAVAILABLE_REASON,
    build_portfolio_analysis,
    build_portfolio_risk,
    fetch_nav_histories,
)


def _today_profit(amount: float, *, kind: str = "estimate", included: bool = True) -> dict[str, object]:
    return {
        "amount": amount,
        "kind": kind,
        "return_date": "2026-09-08",
        "source": "test",
        "basis_amount": None,
        "basis_status": "confirmed" if included else "unknown",
        "included": included,
        "reason_codes": [],
        "return_pct": None,
    }


def _row(
    code: str,
    *,
    name: str | None = None,
    amount: float,
    fund_type: str | None = None,
    fund_type_label: str | None = None,
    valuation_market: str | None = None,
    estimated_return_pct: float | None = None,
    estimated_daily_profit: float | None = None,
    today_profit: dict[str, object] | None = None,
    is_cash_asset: bool = False,
    asset_class: str | None = "FUND",
    theme: str | None = "半导体",
) -> dict[str, object]:
    return {
        "code": code,
        "name": name or code,
        "amount": amount,
        "fund_type": fund_type,
        "fund_type_label": fund_type_label,
        "valuation_market": valuation_market,
        "estimated_return_pct": estimated_return_pct,
        "estimated_daily_profit": estimated_daily_profit,
        "today_profit": today_profit,
        "is_cash_asset": is_cash_asset,
        "is_fund_asset": not is_cash_asset,
        "asset_class": asset_class,
        "theme": theme,
    }


def _nav_points(start: str, values: list[float]) -> list[dict[str, object]]:
    day = date.fromisoformat(start)
    points = []
    base = values[0]
    for offset, value in enumerate(values):
        points.append(
            {
                "date": (day + timedelta(days=offset)).isoformat(),
                "raw_value": value,
                "return_pct": round((value / base - 1) * 100, 4),
            }
        )
    return points


class PortfolioAnalysisTest(unittest.TestCase):
    def test_distribution_uses_metadata_and_skips_theme_and_holding_asset_class(self) -> None:
        payload = build_portfolio_analysis(
            [
                _row(
                    "015060",
                    amount=3000,
                    fund_type="domestic_equity",
                    fund_type_label="普通股票型",
                    valuation_market="CN",
                    estimated_return_pct=1.0,
                    estimated_daily_profit=30,
                ),
                _row(
                    "016702",
                    amount=2000,
                    fund_type="qdii_hybrid",
                    fund_type_label="QDII偏股混合型",
                    valuation_market="US",
                    estimated_return_pct=-0.5,
                    estimated_daily_profit=-10,
                ),
                _row(
                    "CASH:TEST",
                    name="现金",
                    amount=1000,
                    is_cash_asset=True,
                    asset_class="现金",
                    theme=None,
                ),
            ]
        )

        self.assertFalse(payload["basis"]["look_through"])
        self.assertFalse(payload["basis"]["has_quarterly_holdings"])
        self.assertFalse(payload["basis"]["has_transactions"])

        asset_items = {item["key"]: item for item in payload["distribution"]["asset_class"]["items"]}
        self.assertEqual(asset_items["domestic_equity"]["weight_pct"], 50.0)
        self.assertEqual(asset_items["qdii_hybrid"]["weight_pct"], 33.3333)
        self.assertEqual(asset_items["cash"]["label"], "现金")
        self.assertNotIn("FUND", asset_items)
        self.assertNotIn("半导体", {item["label"] for item in asset_items.values()})

        markets = {item["key"]: item for item in payload["distribution"]["region"]["items"]}
        self.assertEqual(markets["CN"]["weight_pct"], 50.0)
        self.assertEqual(markets["US"]["label"], "美股")
        self.assertEqual(markets["cash"]["label"], "现金")
        self.assertEqual(markets["cash"]["weight_pct"], 16.6667)
        self.assertEqual(
            payload["distribution"]["region"]["note"],
            "基金估值市场，非底层地区穿透；现金单独列出",
        )

        self.assertFalse(payload["distribution"]["currency"]["available"])
        self.assertEqual(payload["distribution"]["currency"]["reason"], CURRENCY_UNAVAILABLE_REASON)
        self.assertEqual(payload["distribution"]["currency"]["items"], [])
        self.assertFalse(payload["distribution"]["industry"]["available"])
        self.assertEqual(payload["distribution"]["industry"]["reason"], INDUSTRY_UNAVAILABLE_REASON)
        self.assertEqual(payload["distribution"]["industry"]["items"], [])
        self.assertFalse(payload["overlap"]["available"])
        self.assertEqual(payload["overlap"]["reason"], OVERLAP_UNAVAILABLE_REASON)

    def test_unclassified_when_metadata_missing_does_not_use_theme(self) -> None:
        payload = build_portfolio_analysis(
            [_row("999999", amount=1000, theme="黄金", asset_class="商品")]
        )
        items = payload["distribution"]["asset_class"]["items"]
        self.assertEqual(items, [
            {
                "key": "unclassified",
                "label": "未分类",
                "amount": 1000.0,
                "weight_pct": 100.0,
                "holding_count": 1,
            }
        ])
        self.assertFalse(payload["distribution"]["industry"]["available"])

    def test_concentration_and_estimated_contribution(self) -> None:
        payload = build_portfolio_analysis(
            [
                _row(
                    "A",
                    amount=5000,
                    estimated_return_pct=2.0,
                    estimated_daily_profit=100,
                    today_profit=_today_profit(100, kind="official_measurement"),
                    fund_type="domestic_hybrid",
                    fund_type_label="偏股混合型",
                    valuation_market="CN",
                ),
                _row(
                    "B",
                    amount=3000,
                    estimated_return_pct=-1.0,
                    estimated_daily_profit=-30,
                    today_profit=_today_profit(-30, kind="official_measurement"),
                    fund_type="domestic_equity",
                    fund_type_label="普通股票型",
                    valuation_market="CN",
                ),
                _row(
                    "C",
                    amount=2000,
                    fund_type="qdii_equity",
                    fund_type_label="QDII普通股票型",
                    valuation_market="GLOBAL",
                ),
            ]
        )
        concentration = payload["concentration"]
        self.assertEqual(concentration["max_weight_pct"], 50.0)
        self.assertEqual(concentration["top3_weight_pct"], 100.0)
        self.assertEqual(concentration["holdings"][0]["code"], "A")

        contribution = payload["contribution"]
        self.assertEqual(contribution["basis"], "today_profit_included")
        self.assertEqual(contribution["note"], CONTRIBUTION_NOTE)
        self.assertEqual(contribution["total_estimated_profit"], 70.0)
        self.assertEqual([item["code"] for item in contribution["items"]], ["A", "B"])
        self.assertEqual(contribution["items"][0]["share_pct"], 142.8571)
        self.assertEqual(contribution["items"][1]["code"], "B")
        self.assertEqual(contribution["items"][1]["share_pct"], -42.8571)

    def test_contribution_ranks_by_absolute_impact(self) -> None:
        payload = build_portfolio_analysis(
            [
                _row("P6", amount=100, estimated_daily_profit=6, today_profit=_today_profit(6)),
                _row("P5", amount=100, estimated_daily_profit=5, today_profit=_today_profit(5)),
                _row("P4", amount=100, estimated_daily_profit=4, today_profit=_today_profit(4)),
                _row("P3", amount=100, estimated_daily_profit=3, today_profit=_today_profit(3)),
                _row("P2", amount=100, estimated_daily_profit=2, today_profit=_today_profit(2)),
                _row("P1", amount=100, estimated_daily_profit=1, today_profit=_today_profit(1)),
                _row("LOSS", amount=100, estimated_daily_profit=-100, today_profit=_today_profit(-100)),
            ]
        )
        codes = [item["code"] for item in payload["contribution"]["items"]]
        self.assertEqual(codes[:5], ["LOSS", "P6", "P5", "P4", "P3"])
        self.assertEqual(payload["contribution"]["items"][0]["estimated_profit"], -100.0)

    def test_cash_is_not_classified_as_cn_market(self) -> None:
        payload = build_portfolio_analysis(
            [
                _row(
                    "CASH:MAIN",
                    name="现金",
                    amount=9000,
                    is_cash_asset=True,
                ),
                _row(
                    "016702",
                    amount=1000,
                    fund_type="qdii_hybrid",
                    fund_type_label="QDII偏股混合型",
                    valuation_market="US",
                ),
            ]
        )
        markets = {item["key"]: item for item in payload["distribution"]["region"]["items"]}
        self.assertEqual(markets["cash"]["weight_pct"], 90.0)
        self.assertEqual(markets["US"]["weight_pct"], 10.0)
        self.assertNotIn("CN", markets)

    def test_risk_skips_short_samples_and_does_not_emit_advice(self) -> None:
        short = _nav_points("2026-01-01", [1.0, 1.01, 1.02])
        long_up = _nav_points("2026-01-01", [1.0 + 0.01 * index for index in range(MIN_RISK_SAMPLES + 2)])
        long_down = _nav_points(
            "2026-01-01",
            [1.0 + 0.008 * index for index in range(MIN_RISK_SAMPLES + 2)],
        )
        payload = build_portfolio_risk(
            [
                _row("SHORT", amount=1000),
                _row("UP", amount=2000),
                _row("DOWN", amount=3000),
                _row("CASH:X", name="现金", amount=500, is_cash_asset=True),
            ],
            {
                "SHORT": {"source": "eastmoney_lsjz", "points": short},
                "UP": {"source": "eastmoney_pingzhongdata", "points": long_up},
                "DOWN": {"source": "eastmoney_pingzhongdata", "points": long_down},
            },
            [{"code": "MISSING", "error": "timeout"}],
        )
        funds = {item["code"]: item for item in payload["funds"]}
        self.assertNotIn("CASH:X", funds)
        self.assertIsNone(funds["SHORT"]["volatility_pct"])
        self.assertIsNone(funds["SHORT"]["max_drawdown_pct"])
        self.assertIsNotNone(funds["UP"]["volatility_pct"])
        self.assertEqual(funds["UP"]["max_drawdown_pct"], 0.0)
        self.assertFalse(payload["basis"]["personal_return"])
        self.assertFalse(payload["basis"]["advice"])
        self.assertNotIn("recommendation", payload)
        self.assertNotIn("advice", payload)
        pair = next(item for item in payload["pairs"] if {item["code_a"], item["code_b"]} == {"UP", "DOWN"})
        self.assertGreater(pair["correlation"], 0.99)
        short_pair = next(
            item for item in payload["pairs"] if "SHORT" in {item["code_a"], item["code_b"]}
        )
        self.assertIsNone(short_pair["correlation"])
        self.assertEqual(payload["failures"], [{"code": "MISSING", "error": "timeout"}])

    def test_fetch_nav_histories_records_failures(self) -> None:
        def fetch(code: str, period: str) -> dict[str, object]:
            if code == "BAD":
                raise FundDataFetchError("upstream down")
            return {"code": code, "range": period, "points": []}

        histories, failures = fetch_nav_histories(["OK", "BAD"], fetch, period="1y")
        self.assertEqual(list(histories), ["OK"])
        self.assertEqual(failures, [{"code": "BAD", "error": "upstream down"}])

    def test_max_drawdown_from_peak_to_trough(self) -> None:
        values = [1.0] * 10 + [2.0] + [1.0] * (MIN_RISK_SAMPLES)
        payload = build_portfolio_risk(
            [_row("PEAK", amount=1000)],
            {"PEAK": {"source": "eastmoney_lsjz", "points": _nav_points("2026-01-01", values)}},
        )
        self.assertEqual(payload["funds"][0]["max_drawdown_pct"], -50.0)


if __name__ == "__main__":
    unittest.main()
