"""结算净值来源：东财 lsjz 客户端、优先级组合，以及生产结算的取数路径。"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from fund_analysis.simulation.acceptance import at
from fund_analysis.simulation.cli import _production_nav_source
from fund_analysis.simulation.nav_sources import (
    EastmoneyLsjzNavSource,
    FallbackNavSource,
    MainDbNavSource,
    NavObservation,
)
from fund_analysis.simulation.rules import FIXTURE_QDII_C_V1, validate_rule_record
from fund_analysis.simulation.service import SimulationService
from fund_analysis.simulation.store import SimulationStore, resolve_simulation_path

CODE = "016665"


def lsjz_row(day: str, dwjz: str, ljjz: str = "9.9999") -> dict:
    return {"FSRQ": day, "DWJZ": dwjz, "LJJZ": ljjz, "JZZZL": "0.50", "SGZT": "限制大额申购"}


def lsjz_payload(rows: list[dict], err_code: int = 0) -> dict:
    return {"ErrCode": err_code, "Data": {"LSJZList": rows}, "TotalCount": len(rows)}


class LsjzSourceTest(unittest.TestCase):
    def test_exact_date_returns_dwjz_not_ljjz(self) -> None:
        seen: list[dict] = []

        def getter(url: str, params: dict) -> dict:
            seen.append(dict(params))
            return lsjz_payload([lsjz_row("2026-10-14", "2.4070", ljjz="3.6790")])

        source = EastmoneyLsjzNavSource(json_getter=getter)
        observation = source.fetch(CODE, date(2026, 10, 14), at(2026, 10, 15, 21, 30))
        self.assertIsNotNone(observation)
        assert observation is not None
        self.assertEqual(observation.nav, Decimal("2.4070"))
        self.assertEqual(observation.nav_date, date(2026, 10, 14))
        self.assertEqual(observation.source, "eastmoney_lsjz_nav")
        self.assertEqual(seen[0]["startDate"], "2026-10-14")
        self.assertEqual(seen[0]["endDate"], "2026-10-14")

    def test_exact_date_missing_returns_none(self) -> None:
        source = EastmoneyLsjzNavSource(json_getter=lambda url, params: lsjz_payload([]))
        self.assertIsNone(source.fetch(CODE, date(2026, 10, 17), at(2026, 10, 17, 12)))

    def test_latest_when_no_date_uses_first_row(self) -> None:
        def getter(url: str, params: dict) -> dict:
            self.assertNotIn("startDate", params)
            return lsjz_payload(
                [lsjz_row("2026-10-15", "2.2806"), lsjz_row("2026-10-14", "2.4070")]
            )

        source = EastmoneyLsjzNavSource(json_getter=getter)
        observation = source.fetch(CODE, None, at(2026, 10, 16, 21, 30))
        assert observation is not None
        self.assertEqual(observation.nav_date, date(2026, 10, 15))
        self.assertEqual(observation.nav, Decimal("2.2806"))

    def test_other_dates_in_response_do_not_substitute(self) -> None:
        source = EastmoneyLsjzNavSource(
            json_getter=lambda url, params: lsjz_payload([lsjz_row("2026-10-13", "2.4965")])
        )
        self.assertIsNone(source.fetch(CODE, date(2026, 10, 14), at(2026, 10, 15, 21, 30)))

    def test_err_code_raises(self) -> None:
        source = EastmoneyLsjzNavSource(json_getter=lambda url, params: lsjz_payload([], err_code=-999))
        with self.assertRaises(ValueError):
            source.fetch(CODE, date(2026, 10, 14), at(2026, 10, 15, 21, 30))

    def test_invalid_dwjz_raises(self) -> None:
        for bad in ("", "abc", "1.123456789"):
            source = EastmoneyLsjzNavSource(
                json_getter=lambda url, params, bad=bad: lsjz_payload([lsjz_row("2026-10-14", bad)])
            )
            with self.assertRaises(ValueError):
                source.fetch(CODE, date(2026, 10, 14), at(2026, 10, 15, 21, 30))


class _FakeSource:
    def __init__(self, name: str, observation: NavObservation | None = None, error: Exception | None = None):
        self.name = name
        self.observation = observation
        self.error = error
        self.calls = 0

    def fetch(self, code: str, nav_date: date | None, observed_at) -> NavObservation | None:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.observation


class FallbackSourceTest(unittest.TestCase):
    def observation(self, name: str) -> NavObservation:
        return NavObservation(CODE, date(2026, 10, 14), Decimal("2.4070"), name, at(2026, 10, 15, 21, 30))

    def test_primary_hit_skips_fallback(self) -> None:
        primary = _FakeSource("primary", observation=self.observation("primary"))
        fallback = _FakeSource("fallback", error=AssertionError("fallback 不应被调用"))
        combined = FallbackNavSource(primary, fallback)
        result = combined.fetch(CODE, date(2026, 10, 14), at(2026, 10, 15, 21, 30))
        assert result is not None
        self.assertEqual(result.source, "primary")
        self.assertEqual(fallback.calls, 0)

    def test_primary_missing_uses_fallback(self) -> None:
        primary = _FakeSource("primary")
        fallback = _FakeSource("fallback", observation=self.observation("fallback"))
        result = FallbackNavSource(primary, fallback).fetch(CODE, date(2026, 10, 14), at(2026, 10, 15, 21, 30))
        assert result is not None
        self.assertEqual(result.source, "fallback")

    def test_primary_error_uses_fallback(self) -> None:
        primary = _FakeSource("primary", error=ValueError("lsjz down"))
        fallback = _FakeSource("fallback", observation=self.observation("fallback"))
        result = FallbackNavSource(primary, fallback).fetch(CODE, date(2026, 10, 14), at(2026, 10, 15, 21, 30))
        assert result is not None
        self.assertEqual(result.source, "fallback")

    def test_both_empty_reraises_primary_error(self) -> None:
        primary = _FakeSource("primary", error=ValueError("lsjz down"))
        fallback = _FakeSource("fallback")
        with self.assertRaises(ValueError):
            FallbackNavSource(primary, fallback).fetch(CODE, date(2026, 10, 14), at(2026, 10, 15, 21, 30))

    def test_both_empty_without_error_returns_none(self) -> None:
        combined = FallbackNavSource(_FakeSource("primary"), _FakeSource("fallback"))
        self.assertIsNone(combined.fetch(CODE, date(2026, 10, 14), at(2026, 10, 15, 21, 30)))

    def test_production_cli_source_wires_lsjz_primary(self) -> None:
        source = _production_nav_source("/tmp/nonexistent-main.db")
        self.assertIsInstance(source, FallbackNavSource)
        self.assertIsInstance(source.primary, EastmoneyLsjzNavSource)
        self.assertIsInstance(source.fallback, MainDbNavSource)


class CommittedRuleArtifactTest(unittest.TestCase):
    def test_committed_production_rule_validates_and_imports(self) -> None:
        path = Path(__file__).resolve().parents[1] / "deploy" / "rules" / "016665-alipay-verified.json"
        record = json.loads(path.read_text("utf-8"))
        canonical = validate_rule_record(record)
        self.assertEqual(canonical["status"], "verified")
        self.assertEqual(canonical["environment"], "production")
        with tempfile.TemporaryDirectory() as tmp:
            service = SimulationService(SimulationStore(Path(tmp) / "prod.db"))
            rule_id, created = service.import_rule(record)
        self.assertTrue(created)
        self.assertEqual(rule_id, record["id"])


class ProductionSettlementWithFallbackTest(unittest.TestCase):
    """生产库在真实库无数据时仍能用 lsjz 净值完成策略周期受理与买入结算。"""

    def test_cycle_accepts_and_settles_from_lsjz_with_empty_main_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / "fund_analysis.db"  # 不存在：回退来源无数据

            def getter(url: str, params: dict) -> dict:
                self.assertEqual(params["fundCode"], CODE)
                if "startDate" in params:
                    # 精确日查询：10-21 的净值在结算时才可取到
                    day = params["startDate"]
                    rows = [lsjz_row(day, "2.4070")] if day == "2026-10-21" and clock["now"] >= at(2026, 10, 22, 21) else []
                    return lsjz_payload(rows)
                # 最新查询：受理时点只能看到 10-20 及更早
                if clock["now"] < at(2026, 10, 22, 21):
                    return lsjz_payload([lsjz_row("2026-10-20", "2.5000")])
                return lsjz_payload([lsjz_row("2026-10-21", "2.4070"), lsjz_row("2026-10-20", "2.5000")])

            source = FallbackNavSource(EastmoneyLsjzNavSource(json_getter=getter), MainDbNavSource(main))
            clock = {"now": at(2026, 10, 20, 9)}
            service = SimulationService(
                SimulationStore(resolve_simulation_path(root / "simulation.db", main)),
                clock=lambda: clock["now"],
            )
            rule = json.loads(json.dumps(FIXTURE_QDII_C_V1))
            rule.update(id="prod_nav_test", environment="production")
            service.import_rule(rule)
            account_id = service.create_account("initial")[0]["id"]
            service.import_strategy_version(
                {
                    "account_id": account_id,
                    "effective_from": "2026-10-21",
                    "timezone": "Asia/Shanghai",
                    "weekday": 3,
                    "time": "14:00",
                    "window_end": "14:10",
                    "amounts": {CODE: "500.00"},
                    "rule_set": "prod_nav_test",
                }
            )

            clock["now"] = at(2026, 10, 21, 14, 5)
            result = service.run_cycle(account_id, nav_source=source)
            assert result is not None
            self.assertEqual(result["fetch_errors"], [])
            run = result["runs"][0]
            self.assertEqual(run["status"], "completed")
            orders = service.list_orders(account_id)["items"]
            self.assertEqual(len(orders), 1)
            order = orders[0]
            self.assertEqual((order["status"], order["nav_date"]), ("awaiting_nav", "2026-10-21"))

            clock["now"] = at(2026, 10, 22, 21, 30)
            report = service.settle(account_id, nav_source=source)
            self.assertEqual(report.errors, [])
            self.assertEqual(service.order_detail(order["id"])["status"], "awaiting_confirmation")

            clock["now"] = at(2026, 10, 23, 18, 30)
            service.settle(account_id, nav_source=source)
            detail = service.order_detail(order["id"])
            self.assertEqual(detail["status"], "settled")
            self.assertEqual(detail["confirmed_shares"], "207.73")
            self.assertEqual(detail["nav_value"], "2.4070")
            self.assertEqual(service.verify(), [])


if __name__ == "__main__":
    unittest.main()
