from __future__ import annotations

import contextlib
import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from fund_analysis.fund_data import (
    CompositeFundEstimateProvider,
    EastmoneyFundDataProvider,
    FundEstimateBatch,
    FundIntradayEstimate,
    _parse_sina_estimate,
    parse_estimate_datetime,
)
from fund_analysis.web.app import create_app
from fund_analysis.web.storage import LocalStorage
from test_web_api import FakeCurveProvider, FakeMarketQuoteProvider
from test_web_storage import market_data
from test_fund_data import core_row, sina_payload, TODAY


LEGACY_SQL = """CREATE TABLE fund_estimate_daily_accuracy (
    id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, observed_date TEXT NOT NULL,
    target_nav_date TEXT, estimated_nav REAL, estimated_return_pct REAL, estimated_at TEXT,
    estimate_source TEXT, official_nav REAL, official_return_pct REAL, official_nav_date TEXT,
    error_pct_points REAL, reconciled_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(code, observed_date))"""


def quote(**changes) -> FundIntradayEstimate:
    return replace(FundIntradayEstimate(
        code="009860", name="境内测试", previous_nav=1.2, previous_nav_date="2026-09-01",
        estimated_nav=1.21, estimated_return_pct=0.8333, estimated_at="2026-09-02 15:00",
        source="eastmoney_fund_core_diy", source_url="https://fundcomapi.eastmoney.com/core?FCODES=009860",
        fetched_at="2026-09-02T15:05:00+08:00", status="available", warnings=[],
        payload={"GSZ": "1.2100", "GSZZL": "0.8333", "GZTIME": "2026-09-02 15:00", "PTYPE": "F"},
    ), **changes)


def official(storage, day="2026-09-01", nav=1.2, rate=0.0, code="009860"):
    storage.record_official_daily_profit(
        market_data(code, nav_date=day, latest_nav=nav, daily_return_pct=rate), 1000,
    )


class EstimateCandidatesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "test.db"
        self.storage = LocalStorage(self.path)

    def rows(self):
        with contextlib.closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM fund_estimate_daily_accuracy ORDER BY id")]

    def save(self, item=None, observed="2026-09-02", domestic=True):
        self.storage.upsert_estimate_daily_sample(item or quote(), observed, allow_same_day_target=domestic)

    def reconcile(self):
        self.storage.reconcile_accuracy_samples("009860", 1.209, 0.75, "2026-09-02")

    def test_duplicate_ignores_unrelated_fields_and_keeps_first_and_last_fetch(self):
        self.save()
        self.save(quote(
            name="改名", warnings=[{"message": "改文案"}], status="stale",
            estimated_at="2026-09-02T07:00:00+00:00", previous_nav_date="20260901",
            source_url="https://fundcomapi.eastmoney.com/core?FCODES=009860,016665&deviceid=changed",
            fetched_at="2026-09-03T10:00:00+08:00",
            payload={**quote().payload, "desc": "变化", "selected_point": {"pre_nav2": "999"}},
        ), observed="2026-09-03")
        row, = self.rows()
        evidence = json.loads(row["evidence_json"])
        self.assertEqual(row["estimated_at"], "2026-09-02 15:00")
        self.assertEqual(evidence["fetched_at"], "2026-09-02T15:05:00+08:00")
        self.assertEqual(evidence["last_fetched_at"], "2026-09-03T10:00:00+08:00")
        self.assertEqual(evidence["payload"], quote().payload)

    def test_quote_baseline_mode_and_endpoint_changes_are_retained(self):
        self.save()
        for item in (
            quote(estimated_nav=1.22), quote(estimated_return_pct=1.0),
            quote(previous_nav=1.19), quote(previous_nav_date="2026-08-31"),
            quote(payload={**quote().payload, "PTYPE": "other"}),
            quote(source_url="https://other.example/core"),
        ):
            self.save(item)
        self.assertEqual(len(self.rows()), 7)

    def test_latest_quote_does_not_regress_or_compare_iso_strings(self):
        self.storage.save_fund_intraday_estimate(quote(estimated_at="2026-09-02T08:00:00+00:00"))
        self.storage.save_fund_intraday_estimate(quote(estimated_at="2026-09-02 15:00"))
        latest = self.storage.get_fund_intraday_estimates_by_code()["009860"]
        self.assertEqual(latest["estimated_at"], "2026-09-02T08:00:00+00:00")
        self.storage.save_fund_intraday_estimate(quote(estimated_at="2026-09-02T07:59:00+00:00"))
        self.assertEqual(self.storage.get_fund_intraday_estimates_by_code()["009860"]["estimated_at"], latest["estimated_at"])

    def test_confirmed_domestic_latest_accepts_offset_format_switches(self):
        stamps = ["2026-09-02 15:00", "2026-09-02T08:00:00Z",
                  "2026-09-03 15:00", "2026-09-03T08:00:00Z"]
        for stamp in stamps:
            self.storage.save_fund_intraday_estimate(quote(estimated_at=stamp, fetched_at="2026-09-03T16:05:00+08:00"))
            self.assertEqual(self.storage.get_fund_intraday_estimates_by_code()["009860"]["estimated_at"], stamp)
        self.storage.save_fund_intraday_estimate(quote(estimated_at="2026-09-02 17:00"))
        self.assertEqual(self.storage.get_fund_intraday_estimates_by_code()["009860"]["estimated_at"], stamps[-1])

    def test_later_valid_fetch_unblocks_quote_and_preserves_first_evidence(self):
        official(self.storage)
        self.save(quote(fetched_at="2026-09-02T14:54:00+08:00"))
        self.reconcile()
        self.assertEqual(self.rows()[0]["attribution_reason"], "quote_time_invalid")
        self.save(quote(fetched_at="2026-09-02T15:05:00+08:00"))
        self.reconcile()
        summary = self.storage.get_accuracy_summary("009860", quote().source)
        self.assertEqual((summary["sample_count"], summary["pending_count"]), (1, 0))
        row, = self.rows()
        evidence = json.loads(row["evidence_json"])
        self.assertEqual(evidence["fetched_at"], "2026-09-02T14:54:00+08:00")
        self.assertEqual(evidence["last_fetched_at"], "2026-09-02T15:05:00+08:00")
        self.assertEqual(evidence["first_valid_fetched_at"], "2026-09-02T15:05:00+08:00")

    def test_valid_fetch_recovers_invalid_or_naive_fetch_without_replacing_first(self):
        for code, invalid in [("009860", "invalid"), ("024481", "2026-09-02 14:54")]:
            official(self.storage, code=code)
            self.save(quote(code=code, fetched_at=invalid))
            self.save(quote(code=code))
            self.storage.reconcile_accuracy_samples(code, 1.209, 0.75, "2026-09-02")
            row, = [row for row in self.rows() if row["code"] == code]
            evidence = json.loads(row["evidence_json"])
            self.assertEqual(evidence["fetched_at"], invalid)
            self.assertEqual(evidence["last_fetched_at"], quote().fetched_at)
            self.assertEqual(evidence["first_valid_fetched_at"], quote().fetched_at)
            self.assertIsNone(row["attribution_reason"])

    def test_recovered_fetch_does_not_bypass_unverified_mode_revision(self):
        official(self.storage)
        self.save(quote(fetched_at="2026-09-02T14:54:00+08:00"))
        self.save(quote(estimated_at="2026-09-02T07:00:00Z", estimated_nav=1.212,
                        estimated_return_pct=1.0, payload={**quote().payload, "PTYPE": "unverified"}))
        self.save()
        self.reconcile()
        summary = self.storage.get_accuracy_summary("009860", quote().source)
        self.assertEqual((summary["sample_count"], summary["pending_count"]), (0, 1))
        self.assertEqual(json.loads(self.rows()[0]["evidence_json"])["first_valid_fetched_at"], quote().fetched_at)

    def test_unverified_mode_revision_excludes_previously_reconciled_quote(self):
        official(self.storage)
        self.save()
        self.reconcile()
        self.assertEqual(self.storage.get_accuracy_summary("009860", quote().source)["sample_count"], 1)
        self.save(quote(estimated_nav=1.212, estimated_return_pct=1.0,
                        payload={**quote().payload, "PTYPE": "unverified"}))
        self.reconcile()
        summary = self.storage.get_accuracy_summary("009860", quote().source)
        self.assertEqual((summary["sample_count"], summary["pending_count"]), (0, 1))
        self.assertEqual(self.rows()[0]["attribution_reason"], "quote_revision_ambiguous")
        self.assertIsNone(self.rows()[1]["target_nav_date"])
        self.assertEqual(self.rows()[1]["attribution_reason"], "source_mode_unverified")

    def test_midnight_and_source_switch_never_overwrite_qdii(self):
        early = quote(code="016665", estimated_at="2026-10-28 04:00", fetched_at="2026-10-28T08:00:00+08:00")
        for item in (
            early, replace(early, source="sina_fund_estimate", estimated_at="2026-10-28 04:00:00", estimated_return_pct=-1.0424),
            replace(early, estimated_at="2026-10-28 21:30", fetched_at="2026-10-28T21:31:00+08:00"),
            replace(early, estimated_at="2026-10-29 00:10", fetched_at="2026-10-29T00:11:00+08:00"),
        ):
            self.save(item, "2026-10-28")
        self.storage.reconcile_accuracy_samples("016665", 2.1166, -0.8, "2026-10-26")
        self.assertEqual(len(self.rows()), 4)
        self.assertTrue(all(r["target_nav_date"] is None and r["attribution_reason"] == "source_date_unverified" for r in self.rows()))
        self.assertTrue(all(r["reconciled_at"] is None for r in self.rows()))

    def test_holiday_weekend_intraday_and_unverified_sina_do_not_reconcile(self):
        cases = [
            ("2026-09-02 14:59", "eastmoney_fund_core_diy", "not_session_close"),
            ("2026-09-05 15:00", "eastmoney_fund_core_diy", "not_session_close"),
            ("2026-10-01 15:00", "eastmoney_fund_core_diy", "not_session_close"),
            ("2026-09-02 15:00", "sina_fund_estimate", "source_mode_unverified"),
        ]
        for stamp, source, reason in cases:
            with self.subTest(stamp=stamp, source=source):
                self.save(quote(estimated_at=stamp, source=source, fetched_at=stamp[:10]+"T16:00:00+08:00"))
                self.assertEqual(self.rows()[-1]["attribution_reason"], reason)
                self.assertIsNone(self.rows()[-1]["target_nav_date"])

    def test_delayed_disclosure_and_late_quote_use_exact_historical_dates(self):
        self.save(quote(fetched_at="2026-09-04T08:00:00+08:00"), observed="2026-09-04")
        self.storage.reconcile_accuracy_samples("009860", 1.3, 2.0, "2026-09-04")
        self.assertEqual(self.rows()[0]["attribution_reason"], "official_missing")
        official(self.storage)
        official(self.storage, "2026-09-02", 1.209, 0.75)
        self.storage.reconcile_accuracy_samples("009860", 1.3, 2.0, "2026-09-04")
        row, = self.rows()
        self.assertEqual(row["official_nav_date"], "2026-09-02")
        self.assertAlmostEqual(row["error_pct_points"], 0.0833)

    def test_baseline_missing_and_conflicts_keep_source_values(self):
        self.save()
        self.reconcile()
        self.assertEqual(self.rows()[0]["attribution_reason"], "baseline_missing")
        official(self.storage, nav=1.19)
        self.reconcile()
        self.assertEqual(self.rows()[0]["attribution_reason"], "baseline_mismatch")
        official(self.storage)
        self.storage.reconcile_accuracy_samples("009860", 1.209, 9.0, "2026-09-02")
        self.assertEqual(self.rows()[0]["attribution_reason"], "baseline_mismatch")
        self.assertEqual(self.rows()[0]["estimated_return_pct"], 0.8333)
        self.reconcile()
        self.assertIsNone(self.rows()[0]["attribution_reason"])

    def test_same_time_revision_is_ambiguous_and_later_candidate_can_resolve(self):
        official(self.storage)
        self.save()
        self.reconcile()
        self.save(quote(estimated_at="2026-09-02T15:00:00+08:00", estimated_nav=1.212, estimated_return_pct=1.0))
        self.reconcile()
        self.assertTrue(all(r["attribution_reason"] == "quote_revision_ambiguous" for r in self.rows()))
        summary = self.storage.get_accuracy_summary("009860", quote().source)
        self.assertEqual((summary["sample_count"], summary["pending_count"]), (0, 1))
        self.save(quote(estimated_at="2026-09-02 16:00", fetched_at="2026-09-02T16:01:00+08:00"))
        self.reconcile()
        summary = self.storage.get_accuracy_summary("009860", quote().source)
        self.assertEqual((summary["sample_count"], summary["pending_count"]), (1, 0))
        self.assertEqual(len(self.rows()), 3)

    def test_latest_conflicting_baseline_does_not_fall_back_to_earlier_better_error(self):
        official(self.storage)
        self.save()
        self.save(quote(estimated_at="2026-09-02 16:00", fetched_at="2026-09-02T16:01:00+08:00", previous_nav_date="2026-08-31"))
        self.reconcile()
        self.assertEqual([r["attribution_reason"] for r in self.rows()], ["superseded", "baseline_mismatch"])
        self.assertEqual(self.storage.get_accuracy_summary("009860", quote().source)["sample_count"], 0)

    def test_domestic_monday_previous_official_is_friday(self):
        self.save(quote(estimated_at="2026-09-07 15:00", fetched_at="2026-09-07T15:01:00+08:00", previous_nav_date="2026-09-04"), "2026-09-07")
        official(self.storage, "2026-09-04")
        self.storage.reconcile_accuracy_samples("009860", 1.209, 0.75, "2026-09-07")
        self.assertIsNone(self.rows()[0]["attribution_reason"])

    def test_previous_date_outside_calendar_coverage_remains_unknown(self):
        self.save(quote(estimated_at="2025-01-02 15:00", fetched_at="2025-01-02T15:01:00+08:00", previous_nav_date="2024-12-31"), "2025-01-02")
        self.storage.reconcile_accuracy_samples("009860", 1.209, 0.75, "2025-01-02")
        self.assertEqual(self.rows()[0]["attribution_reason"], "calendar_uncovered")

    def test_official_correction_recomputes_or_invalidates_pair(self):
        self.save()
        official(self.storage)
        self.reconcile()
        self.storage.reconcile_accuracy_samples("009860", 1.212, 1.0, "2026-09-02")
        self.assertAlmostEqual(self.rows()[0]["error_pct_points"], -0.1667)
        self.storage.reconcile_accuracy_samples("009860", 1.5, 1.0, "2026-09-02")
        self.assertEqual(self.rows()[0]["attribution_reason"], "baseline_mismatch")
        self.assertIsNone(self.rows()[0]["error_pct_points"])

    def test_history_nav_table_is_read_without_replacing_official_return(self):
        with contextlib.closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("CREATE TABLE fund_nav_history (fund_code TEXT, trade_date TEXT, unit_nav REAL, daily_return_pct REAL)")
            conn.executemany("INSERT INTO fund_nav_history VALUES ('009860',?,?,?)", [("2026-09-01",1.2,0), ("2026-09-02",1.209,0.75)])
        self.save()
        self.storage.reconcile_accuracy_samples("009860")
        self.assertIsNone(self.rows()[0]["attribution_reason"])
        self.assertEqual(self.rows()[0]["official_return_pct"], 0.75)


class AccuracyMigrationTest(unittest.TestCase):
    def test_captured_448_samples_keep_original_results_and_221_qdii_unknown(self):
        path = Path(__file__).resolve().parents[1] / "docs/evidence/qdii-estimate-attribution-20261028.json"
        evidence = json.loads(path.read_text())
        columns = evidence["sample_columns"]
        original = evidence["samples"] + evidence["other_legacy_samples"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/"captured.db"
            with contextlib.closing(sqlite3.connect(path)) as conn, conn:
                conn.execute(LEGACY_SQL)
                conn.executemany(
                    f"INSERT INTO fund_estimate_daily_accuracy ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                    original,
                )
            storage = LocalStorage(path)
            with contextlib.closing(sqlite3.connect(path)) as conn:
                saved = conn.execute(f"SELECT {','.join(columns)} FROM fund_estimate_daily_accuracy").fetchall()
                self.assertEqual(sorted(saved), sorted(tuple(r) for r in original))
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM fund_estimate_daily_accuracy WHERE error_pct_points IS NOT NULL AND reconciled_at IS NOT NULL").fetchone()[0], 189)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM fund_estimate_daily_accuracy WHERE attribution_reason='legacy_evidence_missing'").fetchone()[0], 448)
            for code, source in {(dict(zip(columns,r))["code"],dict(zip(columns,r))["estimate_source"]) for r in original}:
                self.assertEqual(storage.get_accuracy_summary(code, source)["sample_count"], 0)
            for row in evidence["samples"]:
                self.assertIsNone(dict(zip(columns,row))["target_nav_date"])
            self.assertEqual(len(evidence["samples"]), 221)

    def test_migration_preserves_all_original_values_and_reopen_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            with contextlib.closing(sqlite3.connect(path)) as conn, conn:
                conn.execute(LEGACY_SQL)
                conn.execute("INSERT INTO fund_estimate_daily_accuracy VALUES (7,'009860','2026-09-02','2026-09-02',1.21,0.83,'2026-09-02 15:00','eastmoney_fund_core_diy',1.209,0.75,'2026-09-02',0.08,'original','first','last')")
                before = conn.execute("SELECT * FROM fund_estimate_daily_accuracy").fetchone()
            storage = LocalStorage(path)
            LocalStorage(path)
            storage.reconcile_accuracy_samples("009860", 999, 999, "2026-09-02")
            with contextlib.closing(sqlite3.connect(path)) as conn:
                after = conn.execute("SELECT * FROM fund_estimate_daily_accuracy").fetchone()
                self.assertEqual(after[:len(before)], before)
                self.assertEqual(after[len(before):], (None,"legacy_evidence_missing",None))
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            summary = storage.get_accuracy_summary("009860", "eastmoney_fund_core_diy")
            self.assertEqual((summary["sample_count"], summary["pending_count"]), (0,1))

    def test_migration_failure_rolls_back_original_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            with contextlib.closing(sqlite3.connect(path)) as conn, conn:
                conn.execute(LEGACY_SQL)
            def connection(_self):
                conn = sqlite3.connect(path)
                conn.row_factory = sqlite3.Row
                conn.set_authorizer(lambda operation,*args: sqlite3.SQLITE_DENY if operation == sqlite3.SQLITE_DROP_TABLE else sqlite3.SQLITE_OK)
                return conn
            with patch.object(LocalStorage, "_get_conn", connection), self.assertRaises(sqlite3.DatabaseError):
                LocalStorage(path)
            with contextlib.closing(sqlite3.connect(path)) as conn:
                columns = [r[1] for r in conn.execute("PRAGMA table_info(fund_estimate_daily_accuracy)")]
                self.assertNotIn("evidence_json", columns)
                self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='estimate_accuracy_legacy'").fetchone())


class AccuracyRefreshFlowTest(unittest.TestCase):
    def test_three_days_of_real_parsers_to_api_metrics(self):
        class FixedTime(datetime):
            instant = datetime.fromisoformat("2026-09-02T16:00:00+08:00")
            @classmethod
            def now(cls, tz=None):
                return cls.instant if tz is None else cls.instant.astimezone(tz)
        errors = [0.1, -0.2, 0.3]
        calls = {"official":0, "estimate":0}
        def official_json(url, params):
            calls["official"] += 1
            return {"Datas":{"FCODE":"009860", "FSRQ":FixedTime.instant.date().isoformat(), "DWJZ":1.2, "RZDF":0}}
        def estimate_json(url, params):
            calls["estimate"] += 1
            day = FixedTime.instant.day
            return {"data":[core_row("009860", FSRQ=f"2026-09-{day-1:02d}", DWJZ=1.2,
                        GSZ=1.2*(1+errors[day-2]/100), GSZZL=errors[day-2], GZTIME=f"2026-09-{day:02d} 15:00")]}
        with tempfile.TemporaryDirectory() as tmp, patch("fund_analysis.web.app.datetime", FixedTime), patch("fund_analysis.fund_data.datetime", FixedTime):
            storage = LocalStorage(Path(tmp)/"flow.db")
            storage.add_holding({"code":"009860", "name":"银行", "amount":1000})
            official(storage)
            provider = CompositeFundEstimateProvider(json_getter=estimate_json)
            with TestClient(create_app(storage=storage, market_provider=EastmoneyFundDataProvider(json_getter=official_json), estimate_provider=provider, market_quote_provider=FakeMarketQuoteProvider(), curve_provider=FakeCurveProvider())) as client:
                for day in [2,3,4]:
                    FixedTime.instant = datetime.fromisoformat(f"2026-09-{day:02d}T16:00:00+08:00")
                    provider._today = FixedTime.instant.date()
                    self.assertEqual(client.post("/api/funds/refresh").status_code, 200)
                summary = client.get("/api/funds").json()["rows"][0]["accuracy"]
                self.assertEqual((summary["sample_count"],summary["pending_count"]), (3,0))
                self.assertAlmostEqual(summary["mae_pct_points"], 0.2)
                self.assertAlmostEqual(summary["max_abs_error_pct_points"], 0.3)
                self.assertEqual(calls, {"official":3, "estimate":3})

    def test_refresh_to_api_summary_and_official_failure_revalidates_candidates(self):
        class Estimates:
            item = quote()
            calls = 0
            def fetch_many(self, codes):
                self.calls += 1
                return FundEstimateBatch({"009860": self.item}, {}, {})
        class Market:
            fail = False
            def fetch_without_quarterly_holdings(self, code):
                if self.fail:
                    raise RuntimeError("fixture unavailable")
                return market_data(code, nav_date="2026-09-02", latest_nav=1.209, daily_return_pct=0.75)
        class FixedTime(datetime):
            @classmethod
            def now(cls, tz=None):
                value = datetime.fromisoformat("2026-09-02T16:00:00+08:00")
                return value if tz is None else value.astimezone(tz)
        with tempfile.TemporaryDirectory() as tmp, patch("fund_analysis.web.app.datetime", FixedTime):
            storage = LocalStorage(Path(tmp)/"flow.db")
            storage.add_holding({"code":"009860", "name":"银行", "amount":1000})
            official(storage)
            estimates, market = Estimates(), Market()
            with TestClient(create_app(storage=storage, market_provider=market, estimate_provider=estimates, market_quote_provider=FakeMarketQuoteProvider(), curve_provider=FakeCurveProvider())) as client:
                for _ in range(2):
                    result = client.post("/api/funds/refresh")
                    self.assertEqual(result.status_code, 200)
                summary = client.get("/api/funds").json()["rows"][0]["accuracy"]
                self.assertEqual((summary["sample_count"],summary["pending_count"]), (1,0))
                market.fail = True
                estimates.item = quote(estimated_nav=1.212, estimated_return_pct=1.0)
                client.post("/api/funds/refresh")
                summary = client.get("/api/funds").json()["rows"][0]["accuracy"]
                self.assertEqual((summary["sample_count"],summary["pending_count"]), (0,1))
                self.assertEqual(estimates.calls, 3)


class SourceEvidenceTest(unittest.TestCase):
    def test_sina_keeps_mode_and_selected_raw_point_without_minute_array(self):
        payload = sina_payload()
        data = payload["result"]["data"]
        data.update(detail_page_type=2, zx_page_type=1, heavy_stock_us_flag=1,
                    desc={"text_base":"source description"}, worth_rate=0.01)
        item = _parse_sina_estimate("110011", payload, "https://example.test", today=TODAY)
        self.assertEqual(item.payload["selected_point"]["pre_nav2"], "1.218")
        self.assertEqual(item.payload["heavy_stock_us_flag"], 1)
        self.assertEqual(item.payload["desc"], data["desc"])
        self.assertEqual(item.payload["worth_rate"], 0.01)
        self.assertNotIn("networth", item.payload)

    def test_core_keeps_raw_basis_and_type(self):
        raw = core_row("110011", PTYPE="F", RZDF=2.5)
        batch = CompositeFundEstimateProvider(json_getter=lambda url,params:{"data":[raw]}, today=TODAY).fetch_many(["110011"])
        self.assertEqual(batch.estimates["110011"].payload["RZDF"], 2.5)
        self.assertEqual(batch.estimates["110011"].payload["PTYPE"], "F")

    def test_quote_parser_keeps_offsets_and_rejects_invalid_suffix(self):
        self.assertEqual(parse_estimate_datetime("2026-09-02 15:00"), parse_estimate_datetime("2026-09-02 15:00:00"))
        self.assertEqual(parse_estimate_datetime("2026-09-02T15:00:00+08:00"), parse_estimate_datetime("2026-09-02T07:00:00Z"))
        self.assertIsNone(parse_estimate_datetime("2026-09-02T15:00:00garbage"))
