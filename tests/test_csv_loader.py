from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fund_analysis.csv_loader import read_portfolio_csv
from fund_analysis.models import PortfolioInputError


class CsvLoaderTest(unittest.TestCase):
    def test_reads_chinese_aliases_and_percentages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            path.write_text(
                "基金代码,基金名称,持仓金额,占比,成本,持仓收益率\n"
                "021740,前海开源黄金ETF联接C,900,60%,1000,-10%\n",
                encoding="utf-8",
            )

            holdings = read_portfolio_csv(path)

        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings[0].code, "021740")
        self.assertEqual(holdings[0].reported_weight_pct, 60)
        self.assertEqual(holdings[0].holding_profit_rate, -10)

    def test_reads_english_underscore_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            path.write_text(
                "code,name,amount,weight_pct,asset_type,holding_profit_rate,daily_profit,cumulative_profit,year_profit\n"
                "012922,易方达全球成长精选混合(QDII)C,1000,0.1,fund,0.12,5.5,88.8,66.6\n",
                encoding="utf-8",
            )

            holdings = read_portfolio_csv(path)

        self.assertEqual(holdings[0].reported_weight_pct, 10.0)
        self.assertEqual(holdings[0].asset_type, "fund")
        self.assertEqual(holdings[0].holding_profit_rate, 12.0)
        self.assertEqual(holdings[0].daily_profit, 5.5)
        self.assertEqual(holdings[0].cumulative_profit, 88.8)
        self.assertEqual(holdings[0].year_profit, 66.6)

    def test_reads_cash_asset_without_explicit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            path.write_text(
                "name,amount,asset_type,daily_profit,cumulative_profit\n"
                "余额宝,70296.63,cash,1.66,774.72\n",
                encoding="utf-8",
            )

            holdings = read_portfolio_csv(path)

        self.assertEqual(holdings[0].code, "CASH:余额宝")
        self.assertEqual(holdings[0].asset_type, "cash")
        self.assertEqual(holdings[0].daily_profit, 1.66)
        self.assertEqual(holdings[0].cumulative_profit, 774.72)

    def test_reads_negative_accounting_profit_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            path.write_text(
                "code,name,amount,daily_profit,cumulative_profit,year_profit\n"
                "021740,前海开源黄金ETF联接C,1000,-5.25,-88.8,-66.6\n",
                encoding="utf-8",
            )

            holdings = read_portfolio_csv(path)

        self.assertEqual(holdings[0].daily_profit, -5.25)
        self.assertEqual(holdings[0].cumulative_profit, -88.8)
        self.assertEqual(holdings[0].year_profit, -66.6)

    def test_reads_optional_amount_nav_date_and_keeps_blank_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            path.write_text(
                "code,name,amount,amount_nav_date\n"
                "110011,易方达沪深300ETF联接A,1000,2026-09-07\n"
                "000001,空白基准日,2000,\n",
                encoding="utf-8",
            )
            holdings = read_portfolio_csv(path)
        self.assertEqual(holdings[0].amount_nav_date, "2026-09-07")
        self.assertIsNone(holdings[1].amount_nav_date)

    def test_rejects_negative_non_accounting_amount_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            path.write_text(
                "code,name,amount\n"
                "021740,前海开源黄金ETF联接C,-1000\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                PortfolioInputError, "amount must be greater than or equal to 0"
            ):
                read_portfolio_csv(path)

    def test_ignores_historical_dca_status_and_reports_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            path.write_text(
                "code,name,amount,dca_status\n"
                "025491,平安中证卫星产业指数C,1000,false\n",
                encoding="utf-8",
            )
            ignored_fields: set[str] = set()

            holdings = read_portfolio_csv(path, ignored_fields=ignored_fields)

        self.assertEqual([holding.code for holding in holdings], ["025491"])
        self.assertEqual(ignored_fields, {"dca_status"})

    def test_rejects_duplicate_codes_with_both_line_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            path.write_text(
                "code,name,amount\n"
                "025491,第一只基金,1000\n"
                "025491,重复基金,2000\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                PortfolioInputError,
                "Line 3: duplicate code '025491'; first seen on line 2",
            ):
                read_portfolio_csv(path)

    def test_reads_synthetic_alipay_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            path.write_text(
                "code,name,amount,cost,holding_profit_rate,cumulative_profit,dca_status,asset_class\n"
                "000001,合成测试基金,120,100,20%,25,false,A股\n",
                encoding="utf-8",
            )
            ignored_fields: set[str] = set()
            holdings = read_portfolio_csv(path, ignored_fields=ignored_fields)

        self.assertEqual(len(holdings), 1)
        self.assertEqual(holdings[0].amount, 120)
        self.assertEqual(holdings[0].cost, 100)
        self.assertEqual(holdings[0].holding_profit_rate, 20)
        self.assertEqual(holdings[0].cumulative_profit, 25)
        self.assertEqual(ignored_fields, {"dca_status"})

    def test_rejects_rows_without_a_complete_sizing_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            path.write_text(
                "code,name,amount,weight_pct\n"
                "021740,前海开源黄金ETF联接C,1000,\n"
                "002207,前海开源金银珠宝混合C,,10%\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(PortfolioInputError, "amount for every holding"):
                read_portfolio_csv(path)

    def test_rejects_non_finite_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            path.write_text(
                "code,name,amount\n"
                "021740,前海开源黄金ETF联接C,nan\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(PortfolioInputError, "Line 2: amount must be finite"):
                read_portfolio_csv(path)

    def test_rejects_invalid_utf8_as_portfolio_input_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            path.write_bytes(b"code,name,amount\n110011,\xff,100\n")

            with self.assertRaisesRegex(PortfolioInputError, "Unable to read portfolio CSV"):
                read_portfolio_csv(path)

    def test_rejects_unexpected_extra_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            path.write_text(
                "code,name,amount\n"
                "021740,前海开源黄金ETF联接C,1000,extra\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(PortfolioInputError, "Line 2: unexpected extra CSV columns"):
                read_portfolio_csv(path)

    def test_rejects_unexpected_header_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            path.write_text(
                "code,name,amount,unknown_field\n"
                "021740,前海开源黄金ETF联接C,1000,ignored\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(PortfolioInputError, "unexpected CSV columns: unknownfield"):
                read_portfolio_csv(path)

    def test_reads_asset_class_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            path.write_text(
                "code,name,amount,asset_class\n"
                "021740,前海开源黄金ETF联接C,1000,黄金\n"
                "000001,华夏成长,500,A股\n",
                encoding="utf-8",
            )

            holdings = read_portfolio_csv(path)

        self.assertEqual(holdings[0].asset_class, "黄金")
        self.assertEqual(holdings[1].asset_class, "A股")

    def test_cash_alias_synthesizes_cash_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "portfolio.csv"
            path.write_text(
                "name,amount,asset_class\n"
                "活期余额,1234.5,现金\n",
                encoding="utf-8",
            )

            holdings = read_portfolio_csv(path)

        self.assertEqual(holdings[0].asset_class, "现金")
        self.assertTrue(holdings[0].code.startswith("CASH:"))


if __name__ == "__main__":
    unittest.main()
