from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import Request

from fund_analysis.collection import collection_slot
from fund_analysis.web.app import _refresh_all, create_app
from fund_analysis.web.storage import LocalStorage
from test_web_api import FakeEstimateProvider, FakeMarketProvider, FakeMarketQuoteProvider


class FixedDatetime(datetime):
    instant = datetime.fromisoformat("2026-09-30T15:05:00+08:00")

    @classmethod
    def now(cls, tz=None):
        return cls.instant if tz is None else cls.instant.astimezone(tz)


class CollectionTest(unittest.TestCase):
    def test_backup_includes_committed_wal_and_refuses_overwrite(self):
        script = Path(__file__).resolve().parents[1] / "deploy" / "backup.py"
        with tempfile.TemporaryDirectory() as root:
            source, target = Path(root) / "active.db", Path(root) / "snapshot.db"
            conn = sqlite3.connect(source)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("CREATE TABLE sample (value INTEGER)")
                conn.execute("INSERT INTO sample VALUES (42)")
                conn.commit()
                run = subprocess.run([sys.executable, str(script), str(source), str(target)], capture_output=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                saved = target.read_bytes()
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                conn.execute("INSERT INTO sample VALUES (43)")
                conn.commit()
                repeat = subprocess.run([sys.executable, str(script), str(source), str(target)], capture_output=True)
                self.assertNotEqual(repeat.returncode, 0)
                self.assertEqual(target.read_bytes(), saved)
                with sqlite3.connect(target) as snapshot:
                    self.assertEqual(snapshot.execute("SELECT * FROM sample").fetchall(), [(42,)])
            finally:
                conn.close()

    def test_windows_timezone_holidays_and_dst(self):
        cases = {
            "2026-09-30T09:31:00+08:00": "2026-09-30T09:30:00+08:00",
            "2026-09-30T11:30:00+08:00": "2026-09-30T11:30:00+08:00",
            "2026-09-30T12:10:00+08:00": None,
            "2026-09-30T15:32:00+08:00": "2026-09-30T15:30:00+08:00",
            "2026-09-30T19:16:00+08:00": "2026-09-30T19:10:00+08:00",
            "2026-10-01T00:11:00+08:00": "2026-10-01T00:10:00+08:00",
            "2026-10-01T19:10:00+08:00": None,
            "2026-10-03T14:00:00+08:00": None,
            "2026-09-26T00:11:00+08:00": "2026-09-26T00:00:00+08:00",  # US Friday, CN Friday closed
            "2026-09-30T21:35:00+08:00": "2026-09-30T21:30:00+08:00",  # disclosure takes precedence
            "2026-10-01T21:35:00+08:00": "2026-10-01T21:30:00+08:00",  # US trades during CN holiday
            "2026-11-26T23:10:00+08:00": "2026-11-26T23:10:00+08:00",  # CN disclosure, US Thanksgiving
            "2026-11-28T00:35:00+08:00": "2026-11-28T00:30:00+08:00",  # winter US Friday
        }
        for stamp, expected in cases.items():
            with self.subTest(stamp=stamp):
                moment = datetime.fromisoformat(stamp)
                slot = collection_slot(moment)
                self.assertEqual(slot.isoformat() if slot else None, expected)
                self.assertEqual(collection_slot(moment.astimezone(timezone.utc)), slot)
        with self.assertRaisesRegex(ValueError, "not covered"):
            collection_slot(datetime.fromisoformat("2027-01-04T10:00:00+08:00"))
        with self.assertRaisesRegex(ValueError, "timezone"):
            collection_slot(datetime(2026, 9, 30))

    def test_locked_duplicate_restart_and_failed_refresh_preserve_data(self):
        entered, release = threading.Event(), threading.Event()

        class SlowMarket(FakeMarketProvider):
            def fetch_without_quarterly_holdings(self, code):
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("test release timed out")
                return super().fetch_without_quarterly_holdings(code)

        with tempfile.TemporaryDirectory() as root, patch("fund_analysis.web.app.datetime", FixedDatetime):
            storage = LocalStorage(Path(root) / "fund.db")
            storage.add_holding({"code": "009860", "name": "测试基金", "amount": 1000, "cost": 900})
            # 真实迁移库保留旧来源无时区记录，不能用它比较当前采集时间槽。
            storage.record_data_source_run(
                source_key="tiantian_fundgz", job_type="fund_intraday_estimate", status="success",
                started_at="2026-09-01T09:47:45.513860", completed_at="2026-09-01T09:47:54.370539",
            )
            original = storage.get_all_holdings()
            estimates = FakeEstimateProvider(estimated_date="2026-09-30", estimated_time="15:00:00")
            app = create_app(storage=storage, market_provider=SlowMarket(), estimate_provider=estimates,
                             market_quote_provider=FakeMarketQuoteProvider())
            request = Request({"type": "http", "app": app})
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(_refresh_all, request, scheduled=True)
                self.assertTrue(entered.wait(5))
                second = pool.submit(_refresh_all, request, scheduled=True)
                release.set()
                self.assertEqual(first.result()["success"], 1)
                self.assertTrue(second.result()["skipped"])
            self.assertEqual(len(estimates.calls), 1)
            app.state.last_refresh_result = None  # 同一库重新启动仍由持久记录去重
            self.assertTrue(_refresh_all(request, scheduled=True)["skipped"])
            before = storage.get_fund_intraday_estimates_by_code()
            app.state.market_provider = FakeMarketProvider(failing_code="009860")
            app.state.estimate_provider = FakeEstimateProvider(failing_code="009860")
            with patch.object(FixedDatetime, "instant", datetime.fromisoformat("2026-09-30T15:10:00+08:00")):
                result = _refresh_all(request, scheduled=True)
                self.assertEqual(result["failure"], 1)
                self.assertEqual(result["estimate_failure"], 1)
                self.assertTrue(_refresh_all(request, scheduled=True)["skipped"])
            self.assertEqual(storage.get_fund_intraday_estimates_by_code(), before)
            self.assertEqual(storage.get_all_holdings(), original)
            app.state.market_provider = FakeMarketProvider(nav_date="2026-09-30")
            app.state.estimate_provider = estimates
            with patch.object(FixedDatetime, "instant", datetime.fromisoformat("2026-09-30T19:10:00+08:00")):
                self.assertEqual(_refresh_all(request, scheduled=True)["success"], 1)
            with sqlite3.connect(storage.db_path) as conn:
                sample = conn.execute("SELECT target_nav_date,official_nav_date,reconciled_at FROM fund_estimate_daily_accuracy").fetchone()
                # fixture 的前值日期与采集日期相隔一个月，不应通过新基准校验。
                self.assertEqual(sample[:2], (None, None))
                self.assertIsNone(sample[2])
            with patch.object(FixedDatetime, "instant", datetime.fromisoformat("2026-10-03T14:00:00+08:00")):
                self.assertTrue(_refresh_all(request, scheduled=True)["skipped"])


if __name__ == "__main__":
    unittest.main()
