from __future__ import annotations

import contextlib
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from fund_analysis.fund_data import FundIntradayEstimate, FundMarketData
from fund_analysis.market_quotes import MarketQuote
from fund_analysis.web.storage import LocalStorage


def market_data(
    code: str = "110011",
    *,
    nav_date: str | None = "2026-09-01",
    latest_nav: float | None = 1.234,
    daily_return_pct: float | None = 0.8,
    source: str = "eastmoney_fundmobapi",
) -> FundMarketData:
    return FundMarketData(
        code=code,
        name="测试基金",
        nav_date=nav_date,
        latest_nav=latest_nav,
        daily_return_pct=daily_return_pct,
        source=source,
        source_urls=["https://example.test/nav"],
        fetched_at="2026-09-01T10:00:00+08:00",
        warnings=[],
    )


def estimate(
    code: str = "110011",
    *,
    estimated_at: str = "2026-09-01T10:15:00+08:00",
    estimated_return_pct: float = 0.83,
    source: str = "eastmoney_fund_core_diy",
) -> FundIntradayEstimate:
    return FundIntradayEstimate(
        code=code,
        name="测试基金",
        previous_nav=1.2,
        previous_nav_date="2026-08-31",
        estimated_nav=1.21,
        estimated_return_pct=estimated_return_pct,
        estimated_at=estimated_at,
        source=source,
        source_url="https://example.test/estimate",
        fetched_at="2026-09-01T10:15:01+08:00",
        status="available",
        warnings=[],
        payload={"gsz": "1.21"},
    )


class LocalStorageTest(unittest.TestCase):
    def test_initialization_is_additive_and_preserves_legacy_tables(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            db_path = Path(root) / "old.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute("CREATE TABLE legacy_reports (id INTEGER PRIMARY KEY, value TEXT)")
                conn.execute("INSERT INTO legacy_reports (value) VALUES ('keep me')")
                conn.execute(
                    "CREATE TABLE holdings (id INTEGER PRIMARY KEY, code TEXT, name TEXT, legacy_note TEXT)"
                )
                conn.execute(
                    "INSERT INTO holdings (code, name, legacy_note) VALUES ('OLD001', '旧持仓', 'keep note')"
                )
            storage = LocalStorage(db_path)
            storage.add_holding({"code": "110011", "name": "新持仓", "amount": 1000})
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(conn.execute("SELECT value FROM legacy_reports").fetchone()[0], "keep me")
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                columns = {row[1] for row in conn.execute("PRAGMA table_info(holdings)")}
                self.assertEqual(
                    conn.execute("SELECT legacy_note FROM holdings WHERE code = 'OLD001'").fetchone()[0],
                    "keep note",
                )
            self.assertTrue(
                {
                    "holdings",
                    "fund_market_snapshots",
                    "fund_intraday_estimates",
                    "fund_estimate_daily_accuracy",
                    "fund_official_daily_profit",
                    "market_quote_snapshots",
                }
                <= tables
            )
            self.assertTrue({"amount", "created_at", "updated_at"} <= columns)
            profit_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='fund_official_daily_profit'"
            ).fetchone()[0]
            self.assertNotIn("FOREIGN KEY", profit_sql.upper())
            self.assertEqual(
                conn.execute("PRAGMA foreign_key_list(fund_official_daily_profit)").fetchall(),
                [],
            )

    def test_holding_update_preserves_unsubmitted_legacy_fields(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = LocalStorage(Path(root) / "test.db")
            with sqlite3.connect(storage.db_path) as conn:
                conn.execute("ALTER TABLE holdings ADD COLUMN legacy_note TEXT")
            holding_id = storage.add_holding({"code": "110011", "name": "测试基金", "amount": 1000})
            with sqlite3.connect(storage.db_path) as conn:
                conn.execute("UPDATE holdings SET legacy_note = 'keep me' WHERE id = ?", (holding_id,))
            storage.update_holding(holding_id, {"code": "110011", "name": "新名称", "amount": 1200})
            row = storage.get_holding(holding_id)
            self.assertEqual(row["name"], "新名称")
            self.assertEqual(row["legacy_note"], "keep me")

    def test_amount_nav_date_is_optional_and_cleared_when_amount_changes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = LocalStorage(Path(root) / "test.db")
            holding_id = storage.add_holding(
                {
                    "code": "110011",
                    "name": "测试基金",
                    "amount": 10000,
                    "amount_nav_date": "2026-09-07",
                }
            )
            stored = storage.get_holding(holding_id)
            self.assertEqual(stored["amount_nav_date"], "2026-09-07")
            self.assertEqual(stored["amount_basis_source"], "manual")

            storage.update_holding(
                holding_id,
                {"code": "110011", "name": "测试基金", "amount": 10000},
            )
            kept = storage.get_holding(holding_id)
            self.assertEqual(kept["amount_nav_date"], "2026-09-07")

            storage.update_holding(
                holding_id,
                {"code": "110011", "name": "测试基金", "amount": 12000},
            )
            cleared = storage.get_holding(holding_id)
            self.assertIsNone(cleared["amount_nav_date"])
            self.assertIsNone(cleared["amount_basis_source"])

            storage.update_holding(
                holding_id,
                {
                    "code": "110011",
                    "name": "测试基金",
                    "amount": 13000,
                    "amount_nav_date": "2026-09-08",
                },
            )
            renewed = storage.get_holding(holding_id)
            self.assertEqual(renewed["amount_nav_date"], "2026-09-08")
            self.assertEqual(renewed["amount"], 13000)

    def test_old_rows_keep_null_amount_nav_date(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            db_path = Path(root) / "old.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "CREATE TABLE holdings (id INTEGER PRIMARY KEY, code TEXT, name TEXT, amount REAL)"
                )
                conn.execute(
                    "INSERT INTO holdings (code, name, amount) VALUES ('110011', '旧持仓', 1000)"
                )
            storage = LocalStorage(db_path)
            row = storage.get_all_holdings()[0]
            self.assertIsNone(row["amount_nav_date"])
            self.assertIsNone(row["amount_basis_source"])

    def test_holding_writes_reject_duplicate_codes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = LocalStorage(Path(root) / "test.db")
            first_id = storage.add_holding(
                {"code": "110011", "name": "第一只基金", "amount": 1000}
            )
            second_id = storage.add_holding(
                {"code": "000001", "name": "第二只基金", "amount": 500}
            )

            with self.assertRaisesRegex(ValueError, "基金代码 110011 已存在"):
                storage.add_holding(
                    {"code": "110011", "name": "重复基金", "amount": 200}
                )
            with self.assertRaisesRegex(ValueError, "基金代码 110011 已存在"):
                storage.update_holding(
                    second_id,
                    {"code": "110011", "name": "第二只基金", "amount": 500},
                )

            self.assertEqual(storage.get_holding(first_id)["code"], "110011")
            self.assertEqual(storage.get_holding(second_id)["code"], "000001")

    def test_initialization_audits_existing_duplicates_without_deleting_them(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            db_path = Path(root) / "legacy.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "CREATE TABLE holdings (id INTEGER PRIMARY KEY, code TEXT, name TEXT)"
                )
                conn.executemany(
                    "INSERT INTO holdings (code, name) VALUES (?, ?)",
                    [("110011", "第一条"), ("110011", "第二条")],
                )

            with self.assertLogs("fund_analysis.web.storage", level="WARNING") as logs:
                storage = LocalStorage(db_path)

            self.assertIn("110011 (2 records", " ".join(logs.output))
            self.assertEqual(len(storage.get_all_holdings()), 2)

    def test_official_snapshot_and_intraday_estimate_stay_separate(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = LocalStorage(Path(root) / "test.db")
            storage.save_fund_market_snapshot(market_data())
            storage.save_fund_intraday_estimate(estimate())
            official = storage.get_fund_market_snapshots_by_code()["110011"]
            intraday = storage.get_fund_intraday_estimates_by_code()["110011"]
            self.assertEqual(official["latest_nav"], 1.234)
            self.assertEqual(intraday["estimated_nav"], 1.21)
            self.assertNotIn("estimated_nav", official)

    def test_market_quote_snapshot_overwrites_latest_success(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = LocalStorage(Path(root) / "test.db")
            first = MarketQuote(
                symbol="NDX",
                name="纳斯达克 100",
                instrument_type="index",
                market="Nasdaq",
                currency="USD",
                last_price=26000.0,
                previous_close=25900.0,
                change_value=100.0,
                change_pct=0.39,
                trade_date="2026-09-03",
                quoted_at="2026-09-03T10:00:00-04:00",
                source="eastmoney_market_quote",
                source_url="https://example.test/ndx",
                fetched_at="2026-09-03T14:00:01+00:00",
                payload={"data": {"f57": "NDX"}},
                warnings=[],
            )
            storage.save_market_quote_snapshot(first)
            storage.save_market_quote_snapshot(
                MarketQuote(**{**first.__dict__, "last_price": 26100.0})
            )

            rows = storage.get_market_quote_snapshots()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["last_price"], 26100.0)
            self.assertIn('"f57": "NDX"', rows[0]["payload_json"])

    def test_market_quote_snapshot_survives_storage_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            db_path = Path(root) / "test.db"
            quote = MarketQuote(
                symbol="CSI_ALL_SHARE",
                name="中证全指",
                instrument_type="index",
                market="中证指数",
                currency="CNY",
                last_price=5867.18,
                previous_close=5860.85,
                change_value=6.33,
                change_pct=0.11,
                trade_date="2026-09-03",
                quoted_at="2026-09-03T16:11:54+08:00",
                source="eastmoney_market_quote",
                source_url="https://example.test/csi",
                fetched_at="2026-09-03T16:12:00+08:00",
                payload={"data": {"f57": "000985"}},
                warnings=[],
            )
            LocalStorage(db_path).save_market_quote_snapshot(quote)

            rows = LocalStorage(db_path).get_market_quote_snapshots()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["symbol"], "CSI_ALL_SHARE")
            self.assertEqual(rows[0]["last_price"], 5867.18)
            self.assertEqual(rows[0]["quoted_at"], "2026-09-03T16:11:54+08:00")
            self.assertIn('"f57": "000985"', rows[0]["payload_json"])


class EstimateAccuracyTest(unittest.TestCase):
    """验证每日估值样本的增删改查及误差统计。"""

    def _storage(self, root: str) -> "LocalStorage":
        return LocalStorage(Path(root) / "test.db")

    def test_upsert_creates_sample_for_new_date(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            storage.upsert_estimate_daily_sample(estimate(), "2026-09-02")
            import sqlite3
            with sqlite3.connect(storage.db_path) as conn:
                row = conn.execute(
                    "SELECT * FROM fund_estimate_daily_accuracy WHERE code = '110011'"
                ).fetchone()
            self.assertIsNotNone(row)

    def test_upsert_retains_later_and_original_candidates(self) -> None:
        """同一观察日的新报价追加候选，原值继续保留。"""
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            early = FundIntradayEstimate(
                code="110011", name="测试基金",
                previous_nav=1.2, previous_nav_date="2026-09-01",
                estimated_nav=1.21, estimated_return_pct=0.83,
                estimated_at="2026-09-02T10:00:00+08:00",
                source="eastmoney_fund_core_diy", source_url=None,
                fetched_at="2026-09-02T10:00:01+08:00",
                status="available", warnings=[], payload={},
            )
            late = FundIntradayEstimate(
                code="110011", name="测试基金",
                previous_nav=1.2, previous_nav_date="2026-09-01",
                estimated_nav=1.22, estimated_return_pct=1.67,
                estimated_at="2026-09-02T14:00:00+08:00",
                source="eastmoney_fund_core_diy", source_url=None,
                fetched_at="2026-09-02T14:00:01+08:00",
                status="available", warnings=[], payload={},
            )
            storage.upsert_estimate_daily_sample(early, "2026-09-02")
            storage.upsert_estimate_daily_sample(late, "2026-09-02")
            import sqlite3
            with sqlite3.connect(storage.db_path) as conn:
                rows = conn.execute(
                    "SELECT estimated_return_pct FROM fund_estimate_daily_accuracy WHERE code = '110011'"
                ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual({row[0] for row in rows}, {0.83, 1.67})

    def test_upsert_retains_out_of_order_candidates(self) -> None:
        """迟到的旧报价保留为独立证据，不覆盖现有记录。"""
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            first = FundIntradayEstimate(
                code="110011", name="测试基金",
                previous_nav=1.2, previous_nav_date="2026-09-01",
                estimated_nav=1.21, estimated_return_pct=0.83,
                estimated_at="2026-09-02T14:00:00+08:00",
                source="eastmoney_fund_core_diy", source_url=None,
                fetched_at="2026-09-02T14:00:01+08:00",
                status="available", warnings=[], payload={},
            )
            older = FundIntradayEstimate(
                code="110011", name="测试基金",
                previous_nav=1.2, previous_nav_date="2026-09-01",
                estimated_nav=1.23, estimated_return_pct=2.5,
                estimated_at="2026-09-02T10:00:00+08:00",
                source="eastmoney_fund_core_diy", source_url=None,
                fetched_at="2026-09-02T10:00:01+08:00",
                status="available", warnings=[], payload={},
            )
            storage.upsert_estimate_daily_sample(first, "2026-09-02")
            storage.upsert_estimate_daily_sample(older, "2026-09-02")
            import sqlite3
            with sqlite3.connect(storage.db_path) as conn:
                rows = conn.execute(
                    "SELECT estimated_return_pct FROM fund_estimate_daily_accuracy WHERE code = '110011'"
                ).fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual({row[0] for row in rows}, {0.83, 2.5})

    def test_reconcile_writes_error_on_date_match(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            # 插入一条 target_nav_date = "2026-09-02" 的样本（需 estimated_at 在 15–20 时段）
            est = FundIntradayEstimate(
                code="110011", name="测试基金",
                previous_nav=1.2, previous_nav_date="2026-09-01",
                estimated_nav=1.21, estimated_return_pct=0.83,
                estimated_at="2026-09-02T16:00:00+08:00",
                source="eastmoney_fund_core_diy", source_url=None,
                fetched_at="2026-09-02T16:00:01+08:00",
                status="available", warnings=[], payload={},
            )
            storage.upsert_estimate_daily_sample(
                est,
                "2026-09-02",
                allow_same_day_target=True,
            )
            storage.record_official_daily_profit(market_data(nav_date="2026-09-01", latest_nav=1.2), 1000)
            storage.reconcile_accuracy_samples("110011", 1.209, 0.75, "2026-09-02")
            import sqlite3
            with sqlite3.connect(storage.db_path) as conn:
                row = conn.execute(
                    "SELECT error_pct_points, reconciled_at FROM fund_estimate_daily_accuracy WHERE code='110011'"
                ).fetchone()
            self.assertIsNotNone(row[1])  # reconciled_at 已填充
            self.assertAlmostEqual(row[0], 0.83 - 0.75, places=4)

    def test_reconcile_skips_when_target_date_null(self) -> None:
        """target_nav_date 为 NULL 时不触发对账。"""
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            # 即使在收盘后，未明确标记为境内基金时也不猜测目标净值日。
            est = estimate(estimated_at="2026-09-02T16:00:00+08:00")
            storage.upsert_estimate_daily_sample(est, "2026-09-02")
            storage.record_official_daily_profit(market_data(nav_date="2026-09-01", latest_nav=1.2), 1000)
            storage.reconcile_accuracy_samples("110011", 1.209, 0.75, "2026-09-02")
            import sqlite3
            with sqlite3.connect(storage.db_path) as conn:
                row = conn.execute(
                    "SELECT reconciled_at FROM fund_estimate_daily_accuracy WHERE code='110011'"
                ).fetchone()
            self.assertIsNone(row[0])

    def test_accuracy_summary_includes_pending_unreconciled_samples(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            storage.upsert_estimate_daily_sample(estimate(), "2026-09-03")
            result = storage.get_accuracy_summary("110011", "eastmoney_fund_core_diy")
            self.assertIsNotNone(result)
            self.assertEqual(result["sample_count"], 0)
            self.assertEqual(result["pending_count"], 1)
            self.assertNotIn("mae_pct_points", result)

    def test_accuracy_summary_excludes_legacy_results_without_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            # 插入 2 条已对账样本（低于最小值 3）
            import sqlite3
            now = "2026-09-03T10:00:00+08:00"
            with sqlite3.connect(storage.db_path) as conn:
                for i, d in enumerate(["2026-09-01", "2026-09-02"]):
                    conn.execute(
                        """INSERT INTO fund_estimate_daily_accuracy
                           (code, observed_date, estimate_source, estimated_return_pct,
                            official_return_pct, error_pct_points, reconciled_at, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        ("110011", d, "source-a", 0.83, 0.75, 0.08, now, now, now),
                    )
            result = storage.get_accuracy_summary("110011", "source-a")
            self.assertIsNotNone(result)
            self.assertEqual(result["sample_count"], 0)
            self.assertEqual(result["pending_count"], 2)
            self.assertNotIn("mae_pct_points", result)

    def test_accuracy_summary_computes_mae_bias_max(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            errors = [0.10, -0.20, 0.30]
            for day, error in zip([2, 3, 4], errors):
                previous = f"2026-09-{day - 1:02d}"
                target = f"2026-09-{day:02d}"
                storage.record_official_daily_profit(market_data(nav_date=previous, latest_nav=1.2, daily_return_pct=0), 1000)
                storage.upsert_estimate_daily_sample(replace(
                    estimate(estimated_at=target+"T16:00:00+08:00", estimated_return_pct=error),
                    previous_nav_date=previous, estimated_nav=1.2*(1+error/100),
                    fetched_at=target+"T16:00:01+08:00",
                ), target, allow_same_day_target=True)
                storage.reconcile_accuracy_samples("110011", 1.2, 0, target)
            result = storage.get_accuracy_summary("110011", "eastmoney_fund_core_diy")
            self.assertEqual(result["sample_count"], 3)
            self.assertEqual(result["pending_count"], 0)
            self.assertAlmostEqual(result["mae_pct_points"], 0.2)
            self.assertAlmostEqual(result["bias_pct_points"], 0.0667)
            self.assertAlmostEqual(result["max_abs_error_pct_points"], 0.3)

    def test_later_estimate_recomputes_previous_reconciliation(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            storage.record_official_daily_profit(market_data(nav_date="2026-09-01", latest_nav=1.2), 1000)
            for hour, rate, nav in [(16, 1.0, 1.212), (17, 2.0, 1.224)]:
                storage.upsert_estimate_daily_sample(replace(
                    estimate(estimated_at=f"2026-09-02T{hour}:00:00+08:00", estimated_return_pct=rate),
                    previous_nav_date="2026-09-01", estimated_nav=nav,
                    fetched_at=f"2026-09-02T{hour}:00:01+08:00",
                ), "2026-09-02", allow_same_day_target=True)
                storage.reconcile_accuracy_samples("110011", 1.2096, 0.8, "2026-09-02")
            with contextlib.closing(sqlite3.connect(storage.db_path)) as conn:
                rows = conn.execute("SELECT error_pct_points, attribution_reason FROM fund_estimate_daily_accuracy ORDER BY id").fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0][1], "superseded")
            self.assertAlmostEqual(rows[1][0], 1.2)
            self.assertIsNone(rows[1][1])

    def test_accuracy_summary_only_uses_requested_source(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            for day in [2, 3, 4]:
                target, previous = f"2026-09-{day:02d}", f"2026-09-{day-1:02d}"
                storage.record_official_daily_profit(market_data(nav_date=previous, latest_nav=1.2, daily_return_pct=0), 1000)
                for source, rate in [("eastmoney_fund_core_diy", 0.1), ("tiantian_fund_valuation_last", 1.0)]:
                    storage.upsert_estimate_daily_sample(replace(
                        estimate(estimated_at=target+"T16:00:00+08:00", estimated_return_pct=rate, source=source),
                        previous_nav_date=previous, estimated_nav=1.2*(1+rate/100),
                        fetched_at=target+"T16:00:01+08:00",
                    ), target, allow_same_day_target=True)
                storage.reconcile_accuracy_samples("110011", 1.2, 0, target)
            result = storage.get_accuracy_summary("110011", "eastmoney_fund_core_diy")
            self.assertEqual(result["source"], "eastmoney_fund_core_diy")
            self.assertEqual(result["sample_count"], 3)
            self.assertAlmostEqual(result["mae_pct_points"], 0.1)

    def test_metadata_table_seeds_portfolio_funds(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            meta_all = storage.get_all_fund_metadata()
            # 应该包含 17 只基金 + CASH
            self.assertIn("024481", meta_all)
            self.assertIn("016702", meta_all)
            self.assertIn("CASH", meta_all)
            self.assertEqual(meta_all["024481"]["market"], "CN")
            self.assertEqual(meta_all["016702"]["market"], "US")
            self.assertEqual(meta_all["016702"]["nav_disclosure_rule"], "qdii_next_day_night")
            self.assertIsNone(meta_all["016702"]["benchmark_code"])
            self.assertEqual(meta_all["015060"]["benchmark_code"], "000827")
            self.assertIsNone(meta_all["CASH"]["benchmark_code"])
            self.assertEqual(meta_all["017731"]["market"], "GLOBAL")
            self.assertEqual(meta_all["017731"]["nav_disclosure_rule"], "unknown_rule")

    def test_metadata_profiles_do_not_replace_legacy_metadata_table(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            db_path = Path(root) / "test.db"
            with sqlite3.connect(db_path) as conn:
                conn.execute("CREATE TABLE fund_metadata (code TEXT PRIMARY KEY, legacy_note TEXT)")
                conn.execute("INSERT INTO fund_metadata VALUES ('OLD001', 'keep me')")
            storage = LocalStorage(db_path)
            with sqlite3.connect(db_path) as conn:
                legacy = conn.execute(
                    "SELECT legacy_note FROM fund_metadata WHERE code = 'OLD001'"
                ).fetchone()
                profile_count = conn.execute(
                    "SELECT COUNT(*) FROM fund_valuation_profiles"
                ).fetchone()[0]
            self.assertEqual(legacy[0], "keep me")
            self.assertGreater(profile_count, 0)

    def test_get_and_upsert_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            meta = storage.get_fund_metadata("024481")
            self.assertIsNotNone(meta)
            self.assertEqual(meta["name"], "财通品质甄选混合C")

            # 更新一条测试基金
            storage.upsert_fund_metadata(
                {
                    "code": "999999",
                    "name": "测试自定义基金",
                    "fund_type": "domestic_hybrid",
                    "fund_type_label": "偏股混合型",
                    "market": "CN",
                    "calendar": "CN_STOCK",
                    "nav_disclosure_rule": "cn_same_day_night",
                    "benchmark_name": "沪深300",
                }
            )
            saved = storage.get_fund_metadata("999999")
            self.assertIsNotNone(saved)
            self.assertEqual(saved["name"], "测试自定义基金")

    def test_upsert_metadata_rejects_missing_rule_fields(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            with self.assertRaisesRegex(ValueError, "nav_disclosure_rule"):
                storage.upsert_fund_metadata({"code": "999999", "name": "测试基金"})


class OfficialDailyProfitStorageTest(unittest.TestCase):
    def _storage(self, root: str) -> LocalStorage:
        return LocalStorage(Path(root) / "test.db")

    def _count(self, storage: LocalStorage, code: str = "110011") -> int:
        with sqlite3.connect(storage.db_path) as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM fund_official_daily_profit WHERE code = ?",
                    (code,),
                ).fetchone()[0]
            )

    def _row(self, storage: LocalStorage, code: str = "110011") -> sqlite3.Row:
        with sqlite3.connect(storage.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT * FROM fund_official_daily_profit
                WHERE code = ? ORDER BY nav_date
                """,
                (code,),
            ).fetchone()
        self.assertIsNotNone(row)
        return row

    def test_first_official_day_is_recorded_with_fen_rounding(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            written = storage.record_official_daily_profit(
                market_data(daily_return_pct=0.0766),
                10000,
            )
            self.assertTrue(written)
            row = self._row(storage)
            self.assertEqual(row["nav_date"], "2026-09-01")
            self.assertEqual(row["holding_amount"], 10000)
            self.assertEqual(row["official_nav"], 1.234)
            self.assertEqual(row["official_return_pct"], 0.0766)
            self.assertAlmostEqual(row["profit_amount"], 7.66)
            self.assertEqual(row["source"], "eastmoney_fundmobapi")
            self.assertEqual(row["recorded_at"], row["updated_at"])

    def test_profit_uses_round_half_up_to_fen(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            storage.record_official_daily_profit(
                market_data(nav_date="2026-09-01", daily_return_pct=1.0),
                122.5,
            )
            self.assertAlmostEqual(self._row(storage)["profit_amount"], 1.23)

    def test_same_code_and_nav_date_does_not_insert_again(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            first = storage.record_official_daily_profit(market_data(), 10000)
            second = storage.record_official_daily_profit(market_data(), 10000)
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(self._count(storage), 1)

    def test_later_holding_change_does_not_override_locked_amount(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            storage.record_official_daily_profit(market_data(daily_return_pct=1.0), 10000)
            storage.record_official_daily_profit(market_data(daily_return_pct=1.0), 20000)
            row = self._row(storage)
            self.assertEqual(row["holding_amount"], 10000)
            self.assertAlmostEqual(row["profit_amount"], 100.0)
            self.assertEqual(self._count(storage), 1)

    def test_official_correction_recomputes_with_locked_amount(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            storage.record_official_daily_profit(
                market_data(latest_nav=1.2, daily_return_pct=1.0),
                10000,
            )
            recorded_at = self._row(storage)["recorded_at"]
            updated = storage.record_official_daily_profit(
                market_data(latest_nav=1.21, daily_return_pct=1.5, source="eastmoney_corrected"),
                20000,
            )
            self.assertTrue(updated)
            row = self._row(storage)
            self.assertEqual(row["holding_amount"], 10000)
            self.assertEqual(row["official_nav"], 1.21)
            self.assertEqual(row["official_return_pct"], 1.5)
            self.assertAlmostEqual(row["profit_amount"], 150.0)
            self.assertEqual(row["source"], "eastmoney_corrected")
            self.assertEqual(row["recorded_at"], recorded_at)
            self.assertGreaterEqual(row["updated_at"], recorded_at)
            self.assertEqual(self._count(storage), 1)

    def test_missing_amount_nav_date_or_return_pct_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            self.assertFalse(storage.record_official_daily_profit(market_data(), None))
            self.assertFalse(
                storage.record_official_daily_profit(market_data(nav_date=None), 10000)
            )
            self.assertFalse(
                storage.record_official_daily_profit(
                    market_data(daily_return_pct=None),
                    10000,
                )
            )
            self.assertEqual(self._count(storage), 0)

    def test_missing_official_nav_still_writes_when_return_exists(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            written = storage.record_official_daily_profit(
                market_data(latest_nav=None, daily_return_pct=0.5),
                10000,
            )
            self.assertTrue(written)
            row = self._row(storage)
            self.assertIsNone(row["official_nav"])
            self.assertAlmostEqual(row["profit_amount"], 50.0)

    def test_records_are_independent_from_estimate_accuracy(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            storage.record_official_daily_profit(market_data(), 10000)
            storage.upsert_estimate_daily_sample(estimate(), "2026-09-02")
            with sqlite3.connect(storage.db_path) as conn:
                profit_count = conn.execute(
                    "SELECT COUNT(*) FROM fund_official_daily_profit"
                ).fetchone()[0]
                accuracy_count = conn.execute(
                    "SELECT COUNT(*) FROM fund_estimate_daily_accuracy"
                ).fetchone()[0]
            self.assertEqual(profit_count, 1)
            self.assertEqual(accuracy_count, 1)

    def test_delete_holding_does_not_delete_profit_records(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            holding_id = storage.add_holding(
                {"code": "110011", "name": "测试基金", "amount": 10000}
            )
            storage.record_official_daily_profit(market_data(), 10000)
            storage.delete_holding(holding_id)
            self.assertEqual(storage.get_all_holdings(), [])
            records = storage.get_official_daily_profit_records("110011")
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["nav_date"], "2026-09-01")

    def test_records_are_returned_in_nav_date_order(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            storage = self._storage(root)
            storage.record_official_daily_profit(
                market_data(nav_date="2026-09-03", daily_return_pct=0.2),
                10000,
            )
            storage.record_official_daily_profit(
                market_data(nav_date="2026-09-01", daily_return_pct=0.1),
                10000,
            )
            records = storage.get_official_daily_profit_records("110011")
            self.assertEqual(
                [row["nav_date"] for row in records],
                ["2026-09-01", "2026-09-03"],
            )


if __name__ == "__main__":
    unittest.main()
