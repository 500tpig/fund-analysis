from __future__ import annotations

import unittest
from datetime import date

from fund_analysis.web.api_payloads import (
    build_holdings_api_payload,
    build_official_daily_profit_api_payload,
)


TODAY = date(2026, 9, 8)


def holding(
    code: str,
    amount: float,
    *,
    amount_nav_date: str | None = None,
    daily_profit: float | None = None,
    cost: float | None = None,
    holding_profit_rate: float | None = None,
    name: str | None = None,
) -> dict[str, object]:
    return {
        "id": abs(hash(code)) % 10_000_000,
        "code": code,
        "name": name or code,
        "amount": amount,
        "amount_nav_date": amount_nav_date,
        "daily_profit": daily_profit,
        "cost": cost,
        "holding_profit_rate": holding_profit_rate,
    }


def snapshot(
    nav_date: str | None,
    daily_return_pct: float | None,
    *,
    latest_nav: float = 1.2,
) -> dict[str, object]:
    return {
        "nav_date": nav_date,
        "daily_return_pct": daily_return_pct,
        "latest_nav": latest_nav,
        "source": "eastmoney_fundmobapi",
        "fetched_at": "2026-09-08T20:00:00+08:00",
    }


def estimate(
    return_pct: float,
    *,
    estimated_at: str = "2026-09-08T15:00:00+08:00",
    source: str = "eastmoney_fund_core_diy",
    previous_nav: float | None = None,
    previous_nav_date: str | None = None,
    estimated_nav: float | None = 1.212,
) -> dict[str, object]:
    return {
        "estimated_at": estimated_at,
        "estimated_return_pct": return_pct,
        "estimated_nav": estimated_nav,
        "source": source,
        "status": "available",
        "previous_nav": previous_nav,
        "previous_nav_date": previous_nav_date,
    }


def payload(
    holdings: list[dict[str, object]],
    snapshots: dict[str, dict[str, object]] | None = None,
    estimates: dict[str, dict[str, object]] | None = None,
    *,
    today: date = TODAY,
    metadata: dict[str, dict[str, object]] | None = None,
    official_profits: dict[object, object] | None = None,
) -> dict[str, object]:
    return build_holdings_api_payload(
        holdings,
        snapshots or {},
        estimates,
        today=today,
        metadata_by_code=metadata,
        official_daily_profits_by_code=official_profits,
    )


class TodayProfitSelectionTest(unittest.TestCase):
    def test_monitoring_without_amount_date_and_official_switch(self) -> None:
        holdings = [holding("A", 10000, daily_profit=999)]
        quotes = {"A": estimate(2, previous_nav=1, previous_nav_date="2026-09-07", estimated_nav=1.02)}
        before = payload(holdings, estimates=quotes)
        self.assertEqual(before["summary"]["today"]["monitoring"]["profit"], 200)
        self.assertIsNone(before["summary"]["today"]["verifiable"]["profit"])
        after = payload(holdings, {"A": snapshot("2026-09-08", 1)}, quotes)
        monitor = after["summary"]["today"]["monitoring"]
        self.assertEqual(monitor["profit"], 100)
        self.assertEqual(monitor["official_count"], 1)
        self.assertEqual(monitor["estimate_count"], 0)
        self.assertEqual(after["rows"][0]["monitoring_profit"]["amount"], 100)

    def test_monitoring_partial_excludes_overseas_stale_conflict_and_missing(self) -> None:
        quotes = {
            "A": estimate(1),
            "B": estimate(20, source="sina_fund_estimate", estimated_at="2026-09-08T04:00:00+08:00"),
            "C": estimate(20, estimated_at="2026-09-07T15:00:00+08:00"),
            "D": estimate(20, previous_nav=1, previous_nav_date="2026-09-07", estimated_nav=1.01),
        }
        result = payload([holding(code, 10000) for code in "ABCDE"], estimates=quotes)
        monitor = result["summary"]["today"]["monitoring"]
        self.assertEqual(monitor["profit"], 100)
        self.assertEqual(monitor["count"], 1)
        self.assertEqual(monitor["total"], 5)
        self.assertEqual(monitor["excluded_codes"], list("BCDE"))
        self.assertEqual(result["rows"][1]["overseas_recent"]["return_pct"], 20)
        self.assertIsNone(result["rows"][1]["monitoring_profit"]["amount"])
        # Same-day official data can replace the overseas quote without assigning its date.
        disclosed = payload([holding("B", 10000)], {"B": snapshot("2026-09-08", 3)}, {"B": quotes["B"]})
        self.assertEqual(disclosed["summary"]["today"]["monitoring"]["profit"], 300)

    def test_monitoring_zero_missing_and_midnight(self) -> None:
        for quotes, day, expected in (
            ({"A": estimate(0)}, TODAY, 0),
            ({}, TODAY, None),
            ({"A": estimate(1)}, date(2026, 9, 9), None),
            ({"A": estimate(1, estimated_nav=None)}, TODAY, None),
            ({"A": {**estimate(1), "status": "failed"}}, TODAY, None),
        ):
            with self.subTest(quotes=quotes, day=day):
                result = payload([holding("A", 10000, daily_profit=55)], estimates=quotes, today=day)
                self.assertEqual(result["summary"]["today"]["monitoring"]["profit"], expected)

    def test_monitoring_mixed_total_matches_displayed_rows_to_fen(self) -> None:
        from decimal import Decimal
        result = payload(
            [holding("A", 1234.56), holding("B", 2345.67)],
            {"A": snapshot("2026-09-08", -1.23)}, {"B": estimate(0.77)},
        )
        total = sum(Decimal(str(row["monitoring_profit"]["amount"])) for row in result["rows"])
        monitor = result["summary"]["today"]["monitoring"]
        self.assertEqual(Decimal(str(monitor["profit"])), total)
        self.assertEqual((monitor["official_count"], monitor["estimate_count"]), (1, 1))

    def test_official_same_day_with_confirmed_previous_amount_beats_estimate(self) -> None:
        result = payload(
            [holding("110011", 10000, amount_nav_date="2026-09-07")],
            {"110011": snapshot("2026-09-08", 1.0)},
            {"110011": estimate(2.0)},
        )
        row = result["rows"][0]
        self.assertEqual(row["today_profit"]["kind"], "official_measurement")
        self.assertTrue(row["today_profit"]["included"])
        self.assertEqual(row["today_profit"]["amount"], 100.0)
        self.assertEqual(row["today_profit"]["return_date"], "2026-09-08")
        self.assertEqual(row["official_scenario"]["amount"], 100.0)
        self.assertEqual(row["estimate_scenario"]["amount"], 200.0)
        self.assertFalse(row["estimate_scenario"]["included"])
        self.assertEqual(result["summary"]["today"]["verifiable"]["profit"], 100.0)
        self.assertEqual(result["summary"]["today"]["estimate"]["profit"], None)
        self.assertEqual(result["summary"]["today"]["account_status"], "complete")

    def test_old_official_does_not_enter_today_when_estimate_belongs_to_d(self) -> None:
        result = payload(
            [holding("110011", 10000, amount_nav_date="2026-09-07")],
            {"110011": snapshot("2026-09-07", 1.0)},
            {"110011": estimate(2.0, previous_nav=1.0, previous_nav_date="2026-09-07", estimated_nav=1.02)},
        )
        row = result["rows"][0]
        self.assertIsNone(row["official_scenario"])
        self.assertEqual(row["today_profit"]["kind"], "estimate")
        self.assertTrue(row["today_profit"]["included"])
        self.assertEqual(row["today_profit"]["amount"], 200.0)
        self.assertEqual(row["today_profit"]["return_date"], "2026-09-08")

    def test_old_amount_date_cannot_remain_confirmed_across_days(self) -> None:
        for basis_date in ("2026-01-01", "2026-09-04", "2026-09-06"):
            with self.subTest(basis_date=basis_date):
                result = payload(
                    [holding("110011", 10000, amount_nav_date=basis_date)],
                    {"110011": snapshot("2026-09-08", 1.0)},
                )
                row = result["rows"][0]
                self.assertFalse(row["today_profit"]["included"])
                self.assertIn("amount_basis_unverified", row["today_profit"]["reason_codes"])
                self.assertEqual(row["official_scenario"]["amount"], 100)
                self.assertIsNone(result["summary"]["today"]["verifiable"]["profit"])
        # A date accepted for D must expire when viewing D+1.
        result = payload(
            [holding("110011", 10000, amount_nav_date="2026-09-07")],
            {"110011": snapshot("2026-09-09", 1.0)}, today=date(2026, 9, 9),
        )
        self.assertFalse(result["rows"][0]["today_profit"]["included"])

    def test_unverified_or_incomplete_estimate_never_enters_total(self) -> None:
        valid = estimate(2, previous_nav=1, previous_nav_date="2026-09-07", estimated_nav=1.02)
        cases = [
            {"previous_nav": None}, {"previous_nav_date": None},
            {"previous_nav_date": "2026-09-08"}, {"previous_nav_date": "2026-09-09"},
            {"previous_nav": 0}, {"previous_nav": -1},
            {"estimated_nav": None}, {"estimated_nav": 0},
            {"estimated_return_pct": None}, {"estimated_nav": 1.20},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                result = payload(
                    [holding("110011", 10000, amount_nav_date="2026-09-07")],
                    estimates={"110011": {**valid, **changes}},
                )
                self.assertFalse(result["rows"][0]["today_profit"]["included"])
                self.assertIsNone(result["summary"]["today"]["verifiable"]["profit"])

    def test_estimate_amount_must_match_source_previous_date(self) -> None:
        for amount_date, included in (("2026-09-04", True), ("2026-09-06", False)):
            with self.subTest(amount_date=amount_date):
                result = payload(
                    [holding("110011", 10000, amount_nav_date=amount_date)],
                    estimates={"110011": estimate(
                        2, estimated_at="2026-09-07T15:00:00+08:00",
                        previous_nav=1, previous_nav_date="2026-09-04", estimated_nav=1.02,
                    )}, today=date(2026, 9, 7),
                )
                self.assertEqual(result["rows"][0]["today_profit"]["included"], included)

    def test_scenario_totals_exclude_included_and_disclosed_estimates(self) -> None:
        valid = estimate(2, previous_nav=1, previous_nav_date="2026-09-07", estimated_nav=1.02)
        result = payload(
            [
                holding("A", 10000, amount_nav_date="2026-09-07"),
                holding("B", 20000),
                holding("C", 30000, amount_nav_date="2026-09-07"),
                holding("D", 40000),
            ],
            {"A": snapshot("2026-09-08", 1), "B": snapshot("2026-09-08", 1)},
            {code: valid for code in ("A", "B", "C", "D")},
        )
        today = result["summary"]["today"]
        self.assertEqual(today["verifiable"]["profit"], 700)
        self.assertEqual(today["official_scenario"]["profit"], 200)
        self.assertEqual(today["official_scenario"]["count"], 1)
        self.assertEqual(today["estimate_scenario"]["profit"], 800)
        self.assertEqual(today["estimate_scenario"]["count"], 1)
        # Detail comparisons remain available, even when excluded from hero buckets.
        self.assertEqual(result["rows"][0]["estimate_scenario"]["amount"], 200)

    def test_disclosed_official_without_rate_cannot_fall_back_to_estimate(self) -> None:
        result = payload(
            [holding("A", 10000, amount_nav_date="2026-09-07")],
            {"A": snapshot("2026-09-08", None)},
            {"A": estimate(2, previous_nav=1, previous_nav_date="2026-09-07", estimated_nav=1.02)},
        )
        self.assertFalse(result["rows"][0]["today_profit"]["included"])
        self.assertIsNone(result["summary"]["today"]["estimate_scenario"]["profit"])

    def test_official_same_day_without_amount_date_is_scenario_not_verifiable(self) -> None:
        result = payload(
            [holding("110011", 10000, daily_profit=12.5)],
            {"110011": snapshot("2026-09-08", 1.0)},
            {"110011": estimate(2.0)},
        )
        row = result["rows"][0]
        self.assertFalse(row["today_profit"]["included"])
        self.assertIsNone(row["today_profit"]["amount"])
        self.assertIn("amount_basis_unknown", row["today_profit"]["reason_codes"])
        self.assertEqual(row["official_scenario"]["amount"], 100.0)
        self.assertFalse(row["official_scenario"]["included"])
        self.assertFalse(row["estimate_scenario"]["included"])
        self.assertEqual(result["summary"]["today"]["account_status"], "unavailable")
        self.assertIsNone(result["summary"]["today"]["verifiable"]["profit"])
        self.assertEqual(result["summary"]["today"]["official_scenario"]["profit"], 100.0)

    def test_amount_nav_date_equal_to_d_is_mismatch(self) -> None:
        result = payload(
            [holding("110011", 10000, amount_nav_date="2026-09-08")],
            {"110011": snapshot("2026-09-08", 1.0)},
        )
        row = result["rows"][0]
        self.assertEqual(row["amount_basis_status"], "mismatch")
        self.assertFalse(row["today_profit"]["included"])
        self.assertIn("amount_basis_mismatch", row["today_profit"]["reason_codes"])

    def test_imported_undated_profit_never_becomes_today_even_across_dates(self) -> None:
        for day in (date(2026, 9, 8), date(2026, 9, 9), date(2026, 9, 12)):
            result = payload(
                [holding("110011", 10000, daily_profit=12.5)],
                {"110011": snapshot("2026-09-07", 1.0)},
                {"110011": estimate(2.0, estimated_at="2026-09-08T15:00:00+08:00")},
                today=day,
            )
            row = result["rows"][0]
            self.assertEqual(row["daily_profit"], 12.5)
            self.assertEqual(row["imported_daily_profit"]["amount"], 12.5)
            self.assertFalse(row["imported_daily_profit"]["dated"])
            self.assertNotEqual(row["today_profit"]["amount"], 12.5)
            self.assertFalse(row["today_profit"]["included"])
            self.assertEqual(result["summary"]["today"]["imported_undated"]["profit"], 12.5)
            self.assertIsNone(result["summary"]["today"]["verifiable"]["profit"])

    def test_all_missing_stays_null_not_zero(self) -> None:
        result = payload(
            [holding("110011", 10000), holding("000001", 20000)],
            {},
            {},
        )
        today = result["summary"]["today"]
        self.assertEqual(today["account_status"], "unavailable")
        self.assertIsNone(today["verifiable"]["profit"])
        self.assertIsNone(today["official_scenario"]["profit"])
        self.assertIsNone(today["estimate_scenario"]["profit"])
        self.assertIsNone(result["summary"]["total_estimated_daily_profit"])
        self.assertNotEqual(today["verifiable"]["profit"], 0)
        self.assertNotEqual(today["verifiable"]["rate_pct"], 0)

    def test_partial_coverage_excludes_missing_code_and_does_not_claim_full_account(self) -> None:
        result = payload(
            [
                holding("110011", 10000, amount_nav_date="2026-09-07"),
                holding("022184", 14470.34),
            ],
            {
                "110011": snapshot("2026-09-08", 1.0),
                "022184": snapshot("2026-09-07", 2.33),
            },
            {
                "110011": estimate(0.5),
            },
        )
        today = result["summary"]["today"]
        self.assertEqual(today["account_status"], "partial")
        self.assertEqual(today["verifiable"]["profit"], 100.0)
        self.assertEqual(today["verifiable_coverage"]["count"], 1)
        self.assertEqual(today["verifiable_coverage"]["total"], 2)
        excluded = {item["code"]: item for item in today["excluded"]}
        self.assertIn("022184", excluded)
        self.assertIn("部分小计", today["account_message"])

    def test_confirmed_zero_rate_is_zero_not_unknown(self) -> None:
        result = payload(
            [holding("110011", 10000, amount_nav_date="2026-09-07")],
            {"110011": snapshot("2026-09-08", 0.0)},
        )
        row = result["rows"][0]
        self.assertTrue(row["today_profit"]["included"])
        self.assertEqual(row["today_profit"]["amount"], 0.0)
        self.assertEqual(result["summary"]["today"]["verifiable"]["profit"], 0.0)
        self.assertEqual(result["summary"]["today"]["account_status"], "complete")

    def test_sina_zero_rate_without_basis_is_not_confirmed_zero(self) -> None:
        result = payload(
            [holding("021842", 10047.37)],
            {"021842": snapshot("2026-09-07", 0.26)},
            {
                "021842": estimate(
                    0.0,
                    source="sina_fund_estimate",
                    previous_nav=6.7730,
                    previous_nav_date="20260907",
                    estimated_nav=6.7499,
                )
            },
            metadata={"021842": {"market": "GLOBAL", "fund_type": "qdii_hybrid"}},
        )
        row = result["rows"][0]
        self.assertFalse(row["today_profit"]["included"])
        self.assertIsNone(row["today_profit"]["amount"])
        self.assertIn("estimate_return_date_unverified", row["estimate_basis_reason_codes"])
        self.assertIn("estimate_basis_conflict", row["estimate_basis_reason_codes"])
        self.assertIn("sina_pre_date_not_return_date", row["estimate_basis_reason_codes"])

    def test_sina_016665_keeps_source_rate_and_marks_conflict(self) -> None:
        result = payload(
            [holding("016665", 3098.58)],
            {"016665": snapshot("2026-09-07", 4.51)},
            {
                "016665": estimate(
                    -1.2057,
                    source="sina_fund_estimate",
                    previous_nav=2.8152,
                    previous_nav_date="20260907",
                    estimated_nav=2.6705,
                )
            },
            metadata={"016665": {"market": "GLOBAL", "fund_type": "qdii_hybrid"}},
        )
        row = result["rows"][0]
        self.assertEqual(row["estimated_return_pct"], -1.2057)
        self.assertEqual(row["estimate_basis_status"], "conflict")
        self.assertFalse(row["today_profit"]["included"])
        self.assertIsNone(row["today_profit"]["return_date"])

    def test_overseas_recent_row_carries_basis_date_and_stays_out_of_totals(self) -> None:
        # 2026-09-26 周六：来源 04:00 的新浪估值，基准为 09-23 官方净值。
        metadata = {"017731": {
            "market": "GLOBAL", "fund_type": "qdii_equity",
            "nav_disclosure_rule": "qdii_next_day_night", "calendar": "US_STOCK",
        }}
        fresh = estimate(
            -0.8758,
            estimated_at="2026-09-26 04:00:00",
            source="sina_fund_estimate",
            previous_nav=4.2105,
            previous_nav_date="20260923",
            estimated_nav=4.1736,
        )
        for today, is_current in ((date(2026, 9, 26), True), (date(2026, 9, 27), False)):
            result = payload(
                [holding("017731", 3168.26, amount_nav_date="2026-09-23")],
                {"017731": snapshot("2026-09-23", -0.24)},
                {"017731": fresh},
                today=today,
                metadata=metadata,
            )
            row = result["rows"][0]
            recent = row["overseas_recent"]
            self.assertEqual(recent["return_pct"], -0.8758)
            self.assertEqual(recent["estimated_nav"], 4.1736)
            self.assertEqual(recent["previous_nav_date"], "2026-09-23")
            self.assertEqual(recent["basis_status"], "consistent")
            self.assertEqual(recent["is_current"], is_current)
            self.assertIsNone(row["monitoring_profit"]["amount"])
            self.assertFalse(row["today_profit"]["included"])
            self.assertEqual(result["summary"]["today"]["monitoring"]["count"], 0)

        # 旧实现拼接的配对（pre_nav 与 nav2_pct）会以基准冲突出现，不进合计。
        legacy = dict(fresh, estimated_return_pct=-1.2979)
        row = payload(
            [holding("017731", 3168.26)],
            {"017731": snapshot("2026-09-23", -0.24)},
            {"017731": legacy},
            today=date(2026, 9, 26),
            metadata=metadata,
        )["rows"][0]
        self.assertEqual(row["overseas_recent"]["basis_status"], "conflict")
        self.assertIsNone(row["monitoring_profit"]["amount"])

    def test_core_previous_nav_date_equal_to_d_does_not_block_official(self) -> None:
        result = payload(
            [holding("006503", 4692.43, amount_nav_date="2026-09-07")],
            {"006503": snapshot("2026-09-08", -0.46)},
            {
                "006503": estimate(
                    0.05,
                    previous_nav=6.7955,
                    previous_nav_date="2026-09-08",
                    estimated_nav=6.8303,
                )
            },
        )
        row = result["rows"][0]
        self.assertIn("previous_nav_not_pre_value", row["estimate_basis_reason_codes"])
        self.assertEqual(row["today_profit"]["kind"], "official_measurement")
        self.assertTrue(row["today_profit"]["included"])

    def test_rate_uses_same_group_basis_amount(self) -> None:
        result = payload(
            [
                holding("A", 10000, amount_nav_date="2026-09-07"),
                holding("B", 20000, amount_nav_date="2026-09-07"),
            ],
            {
                "A": snapshot("2026-09-08", 1.0),
                "B": snapshot("2026-09-08", -1.0),
            },
        )
        today = result["summary"]["today"]
        self.assertEqual(today["verifiable"]["profit"], -100.0)
        self.assertEqual(today["verifiable"]["amount"], 30000.0)
        self.assertEqual(today["verifiable"]["rate_pct"], -0.3333)

    def test_row_fen_then_sum_matches_total(self) -> None:
        result = payload(
            [
                holding("A", 1234.56, amount_nav_date="2026-09-07"),
                holding("B", 2345.67, amount_nav_date="2026-09-07"),
                holding("C", 3456.78, amount_nav_date="2026-09-07"),
            ],
            {
                "A": snapshot("2026-09-08", -1.23),
                "B": snapshot("2026-09-08", 0.77),
                "C": snapshot("2026-09-08", -0.19),
            },
        )
        from decimal import Decimal

        row_sum = sum(Decimal(str(row["today_profit"]["amount"])) for row in result["rows"])
        self.assertEqual(
            Decimal(str(result["summary"]["today"]["verifiable"]["profit"])),
            row_sum,
        )

    def test_holding_profit_conflict_does_not_change_cost_or_today(self) -> None:
        result = payload(
            [holding("014674", 10348.47, cost=16618.76, holding_profit_rate=-8.69, amount_nav_date="2026-09-07")],
            {"014674": snapshot("2026-09-08", -0.71)},
        )
        row = result["rows"][0]
        self.assertTrue(row["holding_profit_conflict"])
        self.assertEqual(row["cost"], 16618.76)
        self.assertEqual(row["holding_profit_amount_source"], "cost_measurement")
        self.assertEqual(row["holding_profit_rate_source"], "imported_rate")
        self.assertEqual(row["today_profit"]["kind"], "official_measurement")
        self.assertNotIn("cost", row["today_profit"]["reason_codes"])

    def test_snapshot_18_funds_official_coverage_and_zero_amount_basis(self) -> None:
        rows = [
            ("006503", 4692.43, "2026-09-08", -0.46, 0.05, "eastmoney_fund_core_diy", "CN", "domestic_equity"),
            ("021528", 4880.05, "2026-09-08", -0.35, -0.06, "eastmoney_fund_core_diy", "CN", "domestic_hybrid"),
            ("024481", 5874.13, "2026-09-08", -0.31, -0.1, "eastmoney_fund_core_diy", "CN", "domestic_hybrid"),
            ("011452", 11744.18, "2026-09-08", -0.93, -0.21, "eastmoney_fund_core_diy", "CN", "domestic_hybrid"),
            ("016665", 3098.58, "2026-09-07", 4.51, -1.2057, "sina_fund_estimate", "GLOBAL", "qdii_hybrid"),
            ("014674", 10348.47, "2026-09-08", -0.71, -0.72, "eastmoney_fund_core_diy", "HK", "hk_connect"),
            ("015060", 2742.10, "2026-09-08", -1.54, -1.07, "eastmoney_fund_core_diy", "CN", "domestic_equity"),
            ("017731", 3168.26, "2026-09-07", 2.97, -0.6303, "sina_fund_estimate", "GLOBAL", "qdii_equity"),
            ("002207", 10863.58, "2026-09-08", -0.31, -0.06, "eastmoney_fund_core_diy", "CN", "domestic_hybrid"),
            ("022184", 14470.34, "2026-09-07", 2.33, -1.4795, "sina_fund_estimate", "GLOBAL", "qdii_equity"),
            ("016702", 14699.89, "2026-09-04", 1.23, 0.0, "sina_fund_estimate", "US", "qdii_hybrid"),
            ("009860", 7934.91, "2026-09-08", 0.35, 0.37, "eastmoney_fund_core_diy", "CN", "domestic_index"),
            ("018036", 8022.16, "2026-09-07", 1.17, -0.1889, "sina_fund_estimate", "GLOBAL", "qdii_equity"),
            ("027138", 7045.71, "2026-09-08", 0.93, 0.93, "eastmoney_fund_core_diy", "CN", "domestic_index"),
            ("021842", 10047.37, "2026-09-07", 0.26, 0.0, "sina_fund_estimate", "GLOBAL", "qdii_hybrid"),
            ("021277", 8411.31, "2026-09-07", 0.9, -1.7546, "sina_fund_estimate", "GLOBAL", "qdii_equity"),
            ("012922", 2305.26, "2026-09-07", 2.84, -0.4327, "sina_fund_estimate", "GLOBAL", "qdii_hybrid"),
            ("021740", 21681.91, "2026-09-08", 0.22, 0.2, "eastmoney_fund_core_diy", "CN", "domestic_commodity"),
        ]
        holdings = [
            holding(code, amount, daily_profit=1.0)
            for code, amount, *_ in rows
        ]
        snapshots = {
            code: snapshot(nav_date, official_pct)
            for code, _amount, nav_date, official_pct, *_ in rows
        }
        estimates = {
            code: estimate(est_pct, source=source)
            for code, _amount, _nav, _off, est_pct, source, *_ in rows
        }
        metadata = {
            code: {"market": market, "fund_type": fund_type}
            for code, _amount, _nav, _off, _est, _source, market, fund_type in rows
        }
        result = payload(holdings, snapshots, estimates, metadata=metadata)
        today = result["summary"]["today"]
        self.assertEqual(today["official_data_coverage"]["count"], 10)
        self.assertEqual(today["official_data_coverage"]["total"], 18)
        self.assertEqual(today["official_data_coverage"]["amount"], 87807.47)
        self.assertEqual(today["amount_basis_coverage"]["count"], 0)
        self.assertEqual(today["verifiable_coverage"]["count"], 0)
        self.assertEqual(today["account_status"], "unavailable")
        self.assertIsNone(today["verifiable"]["profit"])
        self.assertEqual(today["imported_undated"]["count"], 18)
        self.assertNotEqual(today["verifiable"]["profit"], today["estimate_scenario"]["profit"])
        nav_dates = {row["nav_date"] for row in result["rows"]}
        self.assertEqual(nav_dates, {"2026-09-08", "2026-09-07", "2026-09-04"})


class OfficialHistoryQualityTest(unittest.TestCase):
    def test_history_marks_unverified_legacy_and_does_not_fill_gaps(self) -> None:
        result = build_official_daily_profit_api_payload(
            "110011",
            [
                {
                    "nav_date": "2026-09-01",
                    "holding_amount": 10000,
                    "official_nav": 1.2,
                    "official_return_pct": 1.0,
                    "profit_amount": 100,
                    "source": "eastmoney_fundmobapi",
                    "recorded_at": "2026-09-03T10:00:00+08:00",
                },
                {
                    "nav_date": "2026-09-04",
                    "holding_amount": 10000,
                    "official_nav": 1.21,
                    "official_return_pct": 0.5,
                    "profit_amount": 50,
                    "source": "eastmoney_fundmobapi",
                    "recorded_at": "2026-09-04T21:00:00+08:00",
                },
            ],
        )
        self.assertEqual(result["cumulative_kind"], "recorded_dates_sum")
        self.assertEqual(result["completeness"], "gapped_recorded_dates")
        self.assertEqual(result["observed_dates"], ["2026-09-01", "2026-09-04"])
        self.assertEqual(result["cumulative_profit"], 150.0)
        self.assertEqual(result["items"][0]["basis_status"], "unverified_legacy")
        self.assertIn("首次观察金额", result["basis_note"])


class DisplaySessionContractTest(unittest.TestCase):
    def test_intraday_trading_day_with_today_estimates(self) -> None:
        """1. 交易日盘中（有当日估值）：mode=today，数值与 monitoring 一致。"""
        holdings = [holding("A", 10000), holding("B", 20000)]
        quotes = {
            "A": estimate(2.0, estimated_at="2026-09-08T14:00:00+08:00", previous_nav=1.0, previous_nav_date="2026-09-07", estimated_nav=1.02),
            "B": estimate(-1.0, estimated_at="2026-09-08T14:00:00+08:00", previous_nav=2.0, previous_nav_date="2026-09-07", estimated_nav=1.98),
        }
        res = payload(holdings, estimates=quotes, today=TODAY)
        ds = res["summary"]["today"]["display_session"]
        self.assertEqual(ds["mode"], "today")
        self.assertTrue(ds["is_today"])
        self.assertEqual(ds["date"], "2026-09-08")
        self.assertEqual(ds["profit"], 0.0)  # 200 - 200 = 0.0
        self.assertEqual(ds["rate_pct"], 0.0)
        self.assertEqual(ds["count"], 2)
        self.assertEqual(ds["total"], 2)
        self.assertEqual(ds["official_count"], 0)
        self.assertEqual(ds["estimate_count"], 2)
        self.assertEqual(ds["amount"], 30000.0)

        row_a = res["rows"][0]
        row_b = res["rows"][1]
        self.assertEqual(row_a["session_profit"], {
            "amount": 200.0,
            "return_pct": 2.0,
            "return_date": "2026-09-08",
            "kind": "estimate",
            "in_session": True,
        })
        self.assertEqual(row_b["session_profit"], {
            "amount": -200.0,
            "return_pct": -1.0,
            "return_date": "2026-09-08",
            "kind": "estimate",
            "in_session": True,
        })
        in_session_count = sum(1 for r in res["rows"] if (r.get("session_profit") or {}).get("in_session") and (r.get("session_profit") or {}).get("amount") is not None)
        self.assertEqual(in_session_count, ds["count"])

    def test_post_market_partial_disclosure(self) -> None:
        """2. 收盘后部分披露：A 披露当日官方净值，B 仍为盘中估值。"""
        holdings = [holding("A", 10000), holding("B", 20000)]
        snapshots = {"A": snapshot("2026-09-08", 1.5)}
        quotes = {
            "B": estimate(-1.0, estimated_at="2026-09-08T15:00:00+08:00", previous_nav=2.0, previous_nav_date="2026-09-07", estimated_nav=1.98),
        }
        res = payload(holdings, snapshots=snapshots, estimates=quotes, today=TODAY)
        ds = res["summary"]["today"]["display_session"]
        self.assertEqual(ds["mode"], "today")
        self.assertTrue(ds["is_today"])
        self.assertEqual(ds["date"], "2026-09-08")
        self.assertEqual(ds["profit"], -50.0)  # 150 - 200 = -50
        self.assertEqual(ds["official_count"], 1)
        self.assertEqual(ds["estimate_count"], 1)
        self.assertEqual(ds["count"], 2)
        self.assertEqual(res["rows"][0]["session_profit"]["kind"], "official_measurement")
        self.assertEqual(res["rows"][1]["session_profit"]["kind"], "estimate")

    def test_post_market_full_disclosure(self) -> None:
        """3. 全部披露：A 和 B 均披露当日官方净值。"""
        holdings = [holding("A", 10000), holding("B", 20000)]
        snapshots = {
            "A": snapshot("2026-09-08", 1.0),
            "B": snapshot("2026-09-08", 2.0),
        }
        res = payload(holdings, snapshots=snapshots, today=TODAY)
        ds = res["summary"]["today"]["display_session"]
        self.assertEqual(ds["mode"], "today")
        self.assertTrue(ds["is_today"])
        self.assertEqual(ds["profit"], 500.0)  # 100 + 400 = 500
        self.assertEqual(ds["official_count"], 2)
        self.assertEqual(ds["estimate_count"], 0)
        self.assertEqual(ds["count"], 2)

    def test_weekend_fallback_to_historical_official(self) -> None:
        """4. 周末：无当日估值与净值，展示日回退到最新官方净值日。"""
        sunday = date(2026, 9, 27)
        holdings = [holding("A", 10000), holding("B", 20000)]
        snapshots = {
            "A": snapshot("2026-09-24", 1.0),
            "B": snapshot("2026-09-24", -2.0),
        }
        res = payload(holdings, snapshots=snapshots, today=sunday)
        ds = res["summary"]["today"]["display_session"]
        self.assertEqual(ds["mode"], "latest_official")
        self.assertFalse(ds["is_today"])
        self.assertEqual(ds["date"], "2026-09-24")
        self.assertEqual(ds["profit"], -300.0)  # 100 - 400 = -300
        self.assertEqual(ds["rate_pct"], -1.0)
        self.assertEqual(ds["count"], 2)
        self.assertEqual(ds["official_count"], 2)
        self.assertEqual(ds["estimate_count"], 0)
        self.assertTrue(res["rows"][0]["session_profit"]["in_session"])
        self.assertTrue(res["rows"][1]["session_profit"]["in_session"])

    def test_pre_market_fallback(self) -> None:
        """5. 交易日盘前：无当日估值与净值，展示日回退到前一交易日。"""
        tuesday = date(2026, 9, 8)
        holdings = [holding("A", 10000)]
        snapshots = {"A": snapshot("2026-09-07", 1.5)}
        res = payload(holdings, snapshots=snapshots, today=tuesday)
        ds = res["summary"]["today"]["display_session"]
        self.assertEqual(ds["mode"], "latest_official")
        self.assertFalse(ds["is_today"])
        self.assertEqual(ds["date"], "2026-09-07")
        self.assertEqual(ds["profit"], 150.0)
        self.assertEqual(ds["count"], 1)
        self.assertTrue(res["rows"][0]["session_profit"]["in_session"])

    def test_qdii_nav_date_lagging(self) -> None:
        """6. QDII nav_date 落后一天：照常给单行数值，但 in_session=false 且不计入展示日合计。"""
        holdings = [holding("CN_FUND", 10000), holding("QDII_FUND", 10000)]
        snapshots = {
            "CN_FUND": snapshot("2026-09-07", 1.0),
            "QDII_FUND": snapshot("2026-09-04", 2.0),
        }
        res = payload(holdings, snapshots=snapshots, today=TODAY)
        ds = res["summary"]["today"]["display_session"]
        self.assertEqual(ds["mode"], "latest_official")
        self.assertEqual(ds["date"], "2026-09-07")
        self.assertEqual(ds["count"], 1)  # 只计入 CN_FUND
        self.assertEqual(ds["profit"], 100.0)
        self.assertEqual(ds["amount"], 10000.0)
        self.assertEqual(ds["total"], 2)

        row_cn = res["rows"][0]
        row_qdii = res["rows"][1]
        self.assertEqual(row_cn["session_profit"], {
            "amount": 100.0,
            "return_pct": 1.0,
            "return_date": "2026-09-07",
            "kind": "official_measurement",
            "in_session": True,
        })
        self.assertEqual(row_qdii["session_profit"], {
            "amount": 200.0,
            "return_pct": 2.0,
            "return_date": "2026-09-04",
            "kind": "official_measurement",
            "in_session": False,
        })

    def test_cash_asset_row_and_total(self) -> None:
        """7. 现金行：session_profit=null，display_session.total 不含现金。"""
        holdings = [
            holding("A", 10000),
            {"id": 999, "code": "CASH:CNY", "name": "现金", "amount": 5000, "asset_type": "cash"},
        ]
        snapshots = {"A": snapshot("2026-09-07", 1.0)}
        res = payload(holdings, snapshots=snapshots, today=TODAY)
        ds = res["summary"]["today"]["display_session"]
        self.assertEqual(ds["total"], 1)  # 仅 1 只基金，现金不计入 total
        self.assertEqual(ds["count"], 1)
        self.assertIsNone(res["rows"][1]["session_profit"])

    def test_no_official_nav_available(self) -> None:
        """8. 没有任何可用净值时：profit=null, count=0, date 取国内最近交易日。"""
        holdings = [holding("NEW_FUND", 10000)]
        res = payload(holdings, today=TODAY)
        ds = res["summary"]["today"]["display_session"]
        self.assertEqual(ds["mode"], "latest_official")
        self.assertFalse(ds["is_today"])
        self.assertIsNone(ds["profit"])
        self.assertIsNone(ds["rate_pct"])
        self.assertIsNone(ds["amount"])
        self.assertEqual(ds["count"], 0)
        self.assertEqual(ds["total"], 1)
        self.assertEqual(ds["official_count"], 0)
        self.assertEqual(ds["estimate_count"], 0)
        self.assertEqual(ds["date"], "2026-09-08")  # 2026-09-08 为周二交易日
        row = res["rows"][0]
        self.assertEqual(row["session_profit"], {
            "amount": None,
            "return_pct": None,
            "return_date": None,
            "kind": None,
            "in_session": False,
        })

    def test_official_daily_profit_precedence_and_fallback(self) -> None:
        """9. fund_official_daily_profit 有记录优先读取与无记录回退两条路径。"""
        holdings = [holding("A", 10000)]
        snapshots = {"A": snapshot("2026-09-07", 1.0)}

        # 路径 1：无记录，走 profit_from_rate 回退
        res_fallback = payload(holdings, snapshots=snapshots, today=TODAY)
        self.assertEqual(res_fallback["summary"]["today"]["display_session"]["profit"], 100.0)
        self.assertEqual(res_fallback["rows"][0]["session_profit"]["amount"], 100.0)

        # 路径 2：有记录，优先取 fund_official_daily_profit 中的 profit_amount
        records = {
            ("A", "2026-09-07"): {
                "code": "A",
                "nav_date": "2026-09-07",
                "profit_amount": 123.45,
                "official_return_pct": 1.0,
            }
        }
        res_with_record = payload(holdings, snapshots=snapshots, today=TODAY, official_profits=records)
        self.assertEqual(res_with_record["summary"]["today"]["display_session"]["profit"], 123.45)
        self.assertEqual(res_with_record["rows"][0]["session_profit"]["amount"], 123.45)

    def test_metadata_cash_type_preserves_summary_holding_counts(self) -> None:
        """第 1 条修正：含元数据现金类型的行，fund_holding_count/fund_amount 保持原有基于 holdings 的口径。"""
        holdings = [
            holding("000001", 10000, name="现金宝"),
            holding("110011", 20000, name="混合基"),
        ]
        metadata = {
            "000001": {"fund_type": "cash", "market": "CN"},
        }
        res = payload(holdings, metadata=metadata, today=TODAY)
        summary = res["summary"]
        self.assertEqual(summary["fund_holding_count"], 2)
        self.assertEqual(summary["cash_holding_count"], 0)
        self.assertEqual(summary["fund_amount"], 30000.0)
        self.assertEqual(summary["cash_amount"], 0.0)
        ds = summary["today"]["display_session"]
        self.assertEqual(ds["total"], 1)

    def test_intraday_today_mode_qdii_fallback_to_latest_official_nav(self) -> None:
        """第 4 条修正：mode=today 盘中，无 monitoring_profit 的 QDII 行回退到最近官方净值日收益，in_session=false。"""
        holdings = [holding("CN_FUND", 10000), holding("QDII_FUND", 10000)]
        quotes = {
            "CN_FUND": estimate(1.0, estimated_at="2026-09-08T14:00:00+08:00", previous_nav=1.0, previous_nav_date="2026-09-07", estimated_nav=1.01),
        }
        snapshots = {
            "QDII_FUND": snapshot("2026-09-04", 2.5),
        }
        res = payload(holdings, snapshots=snapshots, estimates=quotes, today=TODAY)
        ds = res["summary"]["today"]["display_session"]
        self.assertEqual(ds["mode"], "today")
        self.assertTrue(ds["is_today"])
        self.assertEqual(ds["date"], "2026-09-08")
        self.assertEqual(ds["profit"], 100.0)
        self.assertEqual(ds["count"], 1)
        self.assertEqual(ds["total"], 2)

        row_qdii = res["rows"][1]
        self.assertEqual(row_qdii["session_profit"], {
            "amount": 250.0,
            "return_pct": 2.5,
            "return_date": "2026-09-04",
            "kind": "official_measurement",
            "in_session": False,
        })

    def test_uncovered_calendar_year_with_no_nav_returns_null_date(self) -> None:
        """第 5 条修正：没有任何净值且日历未覆盖年份时，display_session.date 为 None，不猜测日期。"""
        uncovered_today = date(2030, 6, 1)
        holdings = [holding("NEW_FUND", 10000)]
        res = payload(holdings, today=uncovered_today)
        ds = res["summary"]["today"]["display_session"]
        self.assertEqual(ds["mode"], "latest_official")
        self.assertFalse(ds["is_today"])
        self.assertIsNone(ds["date"])
        self.assertIsNone(ds["profit"])
        self.assertEqual(ds["count"], 0)
        self.assertEqual(ds["total"], 1)


if __name__ == "__main__":
    unittest.main()
