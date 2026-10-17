"""阶段 7 固定策略、周期运行与调度入口的测试。

覆盖策略版本的不可覆盖与校验、周期受理与窗口边界、过期补记、
限额缩减、运行状态汇总、指标口径、调度 slot 与 v1 模拟库迁移。
所有用例都使用 fixture 规则与显式时间，不代表支付宝实际交易条件。
"""

from __future__ import annotations

import contextlib
import datetime
import sqlite3
import tempfile
import threading
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from fund_analysis.simulation.acceptance import at, FIXTURE_CODE
from fund_analysis.simulation.rules import FIXTURE_QDII_C_V1, fixture_variant
from fund_analysis.simulation.service import (
    DAILY_CLOSE_SLOT,
    SETTLE_SLOTS,
    TICK_WINDOW_MINUTES,
    SimulationError,
    SimulationService,
    _in_slot,
    _run_status,
)
from fund_analysis.simulation.store import (
    SCHEMA_VERSION,
    SimulationConfigError,
    SimulationStore,
)
from fund_analysis.simulation.strategy import (
    STRATEGY_TIME,
    STRATEGY_WEEKDAY,
    STRATEGY_WINDOW_END,
    StrategyValidationError,
    default_fixture_strategy,
    strategy_version_id,
    validate_strategy_record,
)

OTHER_CODE = "016700"
NO_RULE_CODE = "016702"
# 2026-10-21 是周三，也是 fixture 的开放日；10-16 是上一个开放日。
CYCLE_DAY = datetime.date(2026, 10, 21)
PREV_NAV_DAY = datetime.date(2026, 10, 16)
STRATEGY_SEEN = at(2026, 10, 21, 13, 0)
NAV_SEEN = at(2026, 10, 16, 18, 30)


class SimulationStage7TestCase(unittest.TestCase):
    """共用夹具：fixture 库、账户与策略导入。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def service(self, rules: list[dict] | None = None, name: str = "sim.db") -> SimulationService:
        service = SimulationService(SimulationStore(self.root / name, environment="fixture"))
        for rule in rules or [FIXTURE_QDII_C_V1]:
            service.import_rule(rule)
        return service

    def account(self, service: SimulationService) -> str:
        return service.create_account("initial", now=at(2026, 10, 14, 9))[0]["id"]

    def strategy_account(
        self,
        service: SimulationService,
        *,
        effective_from: datetime.date = CYCLE_DAY,
        seen: datetime.datetime = STRATEGY_SEEN,
        codes: list[str] | None = None,
        amounts: dict[str, str] | None = None,
    ) -> str:
        """建账户并导入策略；`seen` 决定哪些周期在调用时已经到期。"""
        account_id = self.account(service)
        record = default_fixture_strategy(account_id, effective_from, codes or [FIXTURE_CODE])
        if amounts is not None:
            record["amounts"] = amounts
        service.import_strategy_version(record, now=seen)
        return account_id

    def seed_nav(
        self,
        service: SimulationService,
        code: str = FIXTURE_CODE,
        day: datetime.date = PREV_NAV_DAY,
        nav: str = "1.3000",
    ) -> None:
        service.record_nav(code, day, nav, now=at(day.year, day.month, day.day, 18, 30))

    def run_items(self, service: SimulationService, run_id: str) -> list[dict]:
        return service.run_detail(run_id)["items"]

    def item(self, service: SimulationService, run_id: str, index: int = 0) -> dict:
        return self.run_items(service, run_id)[index]


class StrategyValidationTest(SimulationStage7TestCase):
    """策略版本记录的字段校验。"""

    def test_default_fixture_strategy_matches_plan_parameters(self) -> None:
        record = default_fixture_strategy("acct_x", CYCLE_DAY, [OTHER_CODE, FIXTURE_CODE])
        self.assertEqual(record["weekday"], STRATEGY_WEEKDAY)
        self.assertEqual(record["time"], f"{STRATEGY_TIME:%H:%M}")
        self.assertEqual(record["window_end"], f"{STRATEGY_WINDOW_END:%H:%M}")
        self.assertEqual(record["timezone"], "Asia/Shanghai")
        self.assertEqual(record["rule_set"], "fixture_qdii_c_v1")
        # 按基金代码升序，每只 500.00 元
        self.assertEqual(list(record["amounts"]), [FIXTURE_CODE, OTHER_CODE])
        self.assertEqual(set(record["amounts"].values()), {"500.00"})

    def test_version_id_is_deterministic_per_account_and_day(self) -> None:
        expected = strategy_version_id("acct_a", "2026-10-21")
        self.assertEqual(expected, strategy_version_id("acct_a", "2026-10-21"))
        self.assertNotEqual(expected, strategy_version_id("acct_b", "2026-10-21"))
        self.assertNotEqual(expected, strategy_version_id("acct_a", "2026-10-22"))

    def test_validation_reports_each_invalid_field(self) -> None:
        good = default_fixture_strategy("acct_a", CYCLE_DAY, [FIXTURE_CODE])
        validate_strategy_record(good)  # 合法记录不抛错
        cases = (
            ({**good, "extra": 1}, "未知字段"),
            ({**good, "account_id": "  "}, "account_id"),
            ({**good, "account_id": None}, "account_id"),
            ({**good, "effective_from": "not-a-date"}, "effective_from"),
            ({**good, "timezone": "UTC"}, "timezone"),
            ({**good, "weekday": 8}, "weekday"),
            ({**good, "weekday": True}, "weekday"),
            ({**good, "time": "25:00"}, "time"),
            ({**good, "window_end": "13:59"}, "window_end"),
            ({**good, "amounts": {}}, "amounts"),
            ({**good, "amounts": {"01666": "500.00"}}, "基金代码"),
            ({**good, "amounts": {FIXTURE_CODE: "0.00"}}, "amounts"),
            ({**good, "amounts": {FIXTURE_CODE: "10.001"}}, "amounts"),
            ({**good, "rule_set": ""}, "rule_set"),
            ({**good, "rule_set": None}, "rule_set"),
        )
        for record, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaises(StrategyValidationError) as error:
                    validate_strategy_record(record)
                self.assertTrue(
                    any(expected in message for message in error.exception.errors),
                    f"{expected} 未命中：{error.exception.errors}",
                )

    def test_validation_canonicalizes_and_strips(self) -> None:
        record = default_fixture_strategy("  acct_a  ", CYCLE_DAY, [FIXTURE_CODE])
        record["rule_set"] = "  fixture_qdii_c_v1  "
        canonical = validate_strategy_record(record)
        self.assertEqual(canonical["account_id"], "acct_a")
        self.assertEqual(canonical["rule_set"], "fixture_qdii_c_v1")
        self.assertEqual(canonical["amounts"], {FIXTURE_CODE: "500.00"})


class StrategyImportTest(SimulationStage7TestCase):
    """策略版本导入、不可覆盖与修订。"""

    def test_import_is_immutable_and_requires_later_effective_from(self) -> None:
        service = self.service()
        account_id = self.account(service)
        record = default_fixture_strategy(account_id, CYCLE_DAY, [FIXTURE_CODE])
        version_id, created = service.import_strategy_version(record, now=STRATEGY_SEEN)
        self.assertTrue(created)
        # 同键同内容重放返回原版本，不新建
        self.assertEqual(
            service.import_strategy_version(record, now=at(2026, 10, 21, 13, 5)),
            (version_id, False),
        )
        # 同生效日不同内容不可覆盖
        changed = {**record, "amounts": {FIXTURE_CODE: "600.00"}}
        with self.assertRaises(SimulationError) as immutable:
            service.import_strategy_version(changed, now=at(2026, 10, 21, 13, 5))
        self.assertEqual(immutable.exception.code, "strategy_version_immutable")
        # 更早的生效日同样拒绝
        earlier = default_fixture_strategy(account_id, datetime.date(2026, 10, 1), [FIXTURE_CODE])
        with self.assertRaises(SimulationError) as conflict:
            service.import_strategy_version(earlier, now=at(2026, 10, 21, 13, 5))
        self.assertEqual(conflict.exception.code, "strategy_interval_conflict")
        # 更晚的生效日可新增版本，且不会立刻产生运行记录
        later = default_fixture_strategy(account_id, datetime.date(2026, 11, 4), [FIXTURE_CODE])
        self.assertTrue(service.import_strategy_version(later, now=at(2026, 10, 21, 13, 5))[1])
        self.assertEqual(service.list_runs(account_id)["items"], [])

    def test_import_rejects_missing_and_archived_accounts(self) -> None:
        service = self.service()
        with self.assertRaises(SimulationError) as missing:
            service.import_strategy_version(
                default_fixture_strategy("acct_missing", CYCLE_DAY, [FIXTURE_CODE]),
                now=STRATEGY_SEEN,
            )
        self.assertEqual(missing.exception.code, "account_not_found")

        account_id = self.account(service)
        service.pause(account_id, "p", service.account_view(account_id)["version"], now=at(2026, 10, 14, 10))
        service.archive_and_create(
            account_id, confirm_account_id=account_id, operator="t", reason="r", now=at(2026, 10, 14, 11)
        )
        with self.assertRaises(SimulationError) as archived:
            service.import_strategy_version(
                default_fixture_strategy(account_id, CYCLE_DAY, [FIXTURE_CODE]), now=STRATEGY_SEEN
            )
        self.assertEqual(archived.exception.code, "account_archived")

    def test_import_rejects_invalid_record_with_422(self) -> None:
        service = self.service()
        self.account(service)
        with self.assertRaises(SimulationError) as invalid:
            service.import_strategy_version(
                {"account_id": "acct_a", "effective_from": "bad"}, now=STRATEGY_SEEN
            )
        self.assertEqual((invalid.exception.code, invalid.exception.status), ("strategy_invalid", 422))
        # 字段级明细沿用规则导入的做法放在 message 里，fields 保持空列表
        self.assertIn("effective_from", invalid.exception.message)
        with service.store.read() as conn:
            stored = conn.execute("SELECT COUNT(*) FROM strategy_versions").fetchone()[0]
        self.assertEqual(stored, 0)

    def test_version_for_resolves_latest_effective(self) -> None:
        service = self.service()
        account_id = self.account(service)
        self.assertIsNone(service.strategy_version_for(account_id, CYCLE_DAY))
        service.import_strategy_version(
            default_fixture_strategy(account_id, datetime.date(2026, 10, 14), [FIXTURE_CODE]),
            now=at(2026, 10, 14, 10),
        )
        revised = default_fixture_strategy(account_id, CYCLE_DAY, [FIXTURE_CODE])
        revised["amounts"] = {FIXTURE_CODE: "300.00"}
        service.import_strategy_version(revised, now=STRATEGY_SEEN)
        before = service.strategy_version_for(account_id, datetime.date(2026, 10, 15))
        after = service.strategy_version_for(account_id, CYCLE_DAY)
        assert before is not None and after is not None
        self.assertEqual(before["amounts"][FIXTURE_CODE], "500.00")
        self.assertEqual(after["amounts"][FIXTURE_CODE], "300.00")
        # 生效日左闭：版本生效当日即取新版本
        self.assertEqual(
            service.strategy_version_for(account_id, CYCLE_DAY)["effective_from"], "2026-10-21"
        )

    def test_verified_codes_lists_fixture_rules_only(self) -> None:
        service = self.service()
        self.assertEqual(service.verified_codes(), [FIXTURE_CODE])


class CycleExecutionTest(SimulationStage7TestCase):
    """周期受理、窗口边界与幂等。"""

    def test_cycle_accepts_order_within_window(self) -> None:
        service = self.service()
        account_id = self.strategy_account(service)
        self.seed_nav(service)
        result = service.run_cycle(account_id, now=at(2026, 10, 21, 14, 5))
        assert result is not None
        self.assertEqual([run["cycle_date"] for run in result["runs"]], ["2026-10-21"])
        run = result["runs"][0]
        self.assertEqual((run["status"], run["accepted_count"], run["order_count"]), ("completed", 1, 1))
        detail = service.run_detail(run["id"])
        self.assertEqual(detail["strategy"]["id"], run["strategy_version"])
        self.assertEqual(detail["evidence_cutoff"], run["started_at"])
        item = detail["items"][0]
        self.assertEqual(
            (item["code"], item["status"], item["requested_amount"], item["accepted_amount"], item["skipped_amount"]),
            (FIXTURE_CODE, "accepted", "500.00", "500.00", "0.00"),
        )
        self.assertEqual(item["rule_id"], FIXTURE_QDII_C_V1["id"])
        self.assertTrue(detail["snapshot_ids"])
        # 订单由策略受理并绑定周期，净值归属申请日
        order = service.order_detail(item["order_id"])
        self.assertEqual(
            (order["status"], order["accepted_amount"], order["nav_date"]),
            ("awaiting_nav", "500.00", "2026-10-21"),
        )
        self.assertEqual(service.account_view(account_id)["balances"]["frozen"], "500.00")

    def test_window_is_inclusive_start_exclusive_end(self) -> None:
        cases = (
            ("14:00:00", at(2026, 10, 21, 14, 0, 0), True),
            ("14:09:59", at(2026, 10, 21, 14, 9, 59), True),
            ("14:10:00", at(2026, 10, 21, 14, 10, 0), False),
        )
        for label, when, accepted in cases:
            with self.subTest(when=label):
                # 每个算例使用独立库，避免周期落定后无法再复现同一时刻
                service = self.service(name=f"window-{label.replace(':', '')}.db")
                account_id = self.strategy_account(service)
                self.seed_nav(service)
                result = service.run_cycle(account_id, now=when)
                assert result is not None
                run = result["runs"][0]
                if accepted:
                    self.assertEqual((run["status"], run["accepted_count"]), ("completed", 1))
                else:
                    self.assertEqual((run["status"], run["reason_code"]), ("missed", "window_expired"))
                    item = self.item(service, run["id"])
                    self.assertEqual((item["status"], item["reason_code"]), ("skipped", "window_expired"))
                    self.assertIsNone(item["order_id"])
                self.assertEqual(len(service.list_orders(account_id)["items"]), 1 if accepted else 0)

    def test_cycle_not_due_before_window_start(self) -> None:
        service = self.service()
        account_id = self.strategy_account(service)
        self.seed_nav(service)
        result = service.run_cycle(account_id, now=at(2026, 10, 21, 13, 59, 59))
        assert result is not None
        self.assertEqual(result["runs"], [])

    def test_repeated_cycles_do_not_duplicate_orders(self) -> None:
        service = self.service()
        account_id = self.strategy_account(service)
        self.seed_nav(service)
        first = service.run_cycle(account_id, now=at(2026, 10, 21, 14, 1))
        second = service.run_cycle(account_id, now=at(2026, 10, 21, 14, 2))
        assert first is not None and second is not None
        # 同一周期只落一条运行记录与一笔订单
        self.assertEqual(first["runs"][0]["id"], second["runs"][0]["id"])
        self.assertEqual(second["runs"][0]["order_count"], 1)
        self.assertEqual(len(service.list_orders(account_id)["items"]), 1)
        self.assertEqual(len(service.list_runs(account_id)["items"]), 1)
        # 窗口结束后重放不新增订单
        third = service.run_cycle(account_id, now=at(2026, 10, 22, 9))
        assert third is not None
        self.assertEqual((third["runs"][0]["status"], third["runs"][0]["accepted_count"]), ("completed", 1))
        self.assertEqual(len(service.list_orders(account_id)["items"]), 1)

    def test_concurrent_cycles_create_one_order(self) -> None:
        service = self.service()
        account_id = self.strategy_account(service)
        self.seed_nav(service)
        errors: list[Exception] = []

        def work() -> None:
            try:
                service.run_cycle(account_id, now=at(2026, 10, 21, 14, 5))
            except Exception as exc:  # noqa: BLE001 - 并发下收集异常用于断言
                errors.append(exc)

        threads = [threading.Thread(target=work) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(service.list_orders(account_id)["items"]), 1)
        self.assertEqual(len(service.list_runs(account_id)["items"]), 1)
        self.assertEqual(service.account_view(account_id)["balances"]["frozen"], "500.00")

    def test_multiple_codes_processed_in_ascending_order(self) -> None:
        other = fixture_variant("fixture_other", OTHER_CODE, "另一只")
        service = self.service(rules=[FIXTURE_QDII_C_V1, other])
        account_id = self.strategy_account(service, codes=[FIXTURE_CODE, OTHER_CODE])
        for code in (FIXTURE_CODE, OTHER_CODE):
            self.seed_nav(service, code)
        result = service.run_cycle(account_id, now=at(2026, 10, 21, 14, 5))
        assert result is not None
        detail = service.run_detail(result["runs"][0]["id"])
        self.assertEqual([item["code"] for item in detail["items"]], [FIXTURE_CODE, OTHER_CODE])
        self.assertEqual((detail["accepted_count"], detail["order_count"]), (2, 2))
        self.assertEqual(len(detail["snapshot_ids"]), 2)

    def test_run_cycle_without_account(self) -> None:
        service = SimulationService(SimulationStore(self.root / "sim.db", environment="fixture"))
        self.assertIsNone(service.run_cycle(None, now=at(2026, 10, 21, 14, 5)))
        with self.assertRaises(SimulationError) as missing:
            service.run_cycle("acct_missing", now=at(2026, 10, 21, 14, 5))
        self.assertEqual((missing.exception.code, missing.exception.status), ("account_not_found", 404))

    def test_archived_account_returns_none(self) -> None:
        service = self.service()
        account_id = self.account(service)
        service.pause(account_id, "p", service.account_view(account_id)["version"], now=at(2026, 10, 14, 10))
        service.archive_and_create(
            account_id, confirm_account_id=account_id, operator="t", reason="r", now=at(2026, 10, 14, 11)
        )
        self.assertIsNone(service.run_cycle(account_id, now=at(2026, 10, 21, 14, 5)))


class CycleBackfillTest(SimulationStage7TestCase):
    """过期周期补记与版本生效范围。"""

    def test_missed_backfill_records_every_past_cycle_without_orders(self) -> None:
        service = self.service()
        account_id = self.strategy_account(
            service, effective_from=datetime.date(2026, 10, 14), seen=at(2026, 10, 14, 9, 30)
        )
        result = service.run_cycle(account_id, now=at(2026, 11, 4, 15))
        assert result is not None
        self.assertEqual(
            [(run["cycle_date"], run["status"], run["reason_code"]) for run in result["runs"]],
            [
                ("2026-10-14", "missed", "window_expired"),
                ("2026-10-21", "missed", "window_expired"),
                ("2026-10-28", "missed", "window_expired"),
                ("2026-11-04", "missed", "window_expired"),
            ],
        )
        # 过期补记只留证据，不补建订单
        self.assertEqual(service.list_orders(account_id)["items"], [])
        for run in result["runs"]:
            item = self.item(service, run["id"])
            self.assertEqual((item["accepted_amount"], item["order_id"]), ("0.00", None))

    def test_backfill_window_is_capped(self) -> None:
        service = self.service()
        account_id = self.strategy_account(
            service, effective_from=datetime.date(2026, 10, 14), seen=at(2026, 10, 14, 9, 30)
        )
        result = service.run_cycle(account_id, now=at(2027, 3, 1, 10))
        assert result is not None
        self.assertEqual(result["runs"][0]["cycle_date"], "2026-12-02")
        self.assertEqual(result["runs"][-1]["cycle_date"], "2027-02-24")

    def test_cycle_not_backfilled_before_version_import(self) -> None:
        service = self.service()
        # 版本在 10-22 才导入，10-21 的周期当时并不存在
        account_id = self.strategy_account(
            service, effective_from=datetime.date(2026, 10, 14), seen=at(2026, 10, 22, 9)
        )
        result = service.run_cycle(account_id, now=at(2026, 10, 23, 10))
        assert result is not None
        self.assertEqual(result["runs"], [])

    def test_cycle_not_backfilled_before_account_creation(self) -> None:
        service = self.service()
        account_id = self.strategy_account(
            service, effective_from=datetime.date(2026, 10, 14), seen=at(2026, 10, 22, 9)
        )
        # 账户建于 10-14，版本于 10-21/10-22 之间生效：只补记账户存续且版本已生效的周期
        result = service.run_cycle(account_id, now=at(2026, 10, 29, 9))
        assert result is not None
        self.assertEqual([run["cycle_date"] for run in result["runs"]], ["2026-10-28"])

    def test_version_switch_applies_to_later_cycles_only(self) -> None:
        service = self.service()
        account_id = self.strategy_account(
            service, effective_from=datetime.date(2026, 10, 14), seen=at(2026, 10, 14, 9, 30)
        )
        revised = default_fixture_strategy(account_id, CYCLE_DAY, [FIXTURE_CODE])
        revised["amounts"] = {FIXTURE_CODE: "300.00"}
        service.import_strategy_version(revised, now=STRATEGY_SEEN)
        for day in (PREV_NAV_DAY, datetime.date(2026, 10, 20), CYCLE_DAY):
            self.seed_nav(service, day=day)
        result = service.run_cycle(account_id, now=at(2026, 10, 21, 14, 5))
        assert result is not None
        by_date = {run["cycle_date"]: run for run in result["runs"]}
        self.assertEqual(by_date["2026-10-14"]["status"], "missed")
        newer = service.run_detail(by_date["2026-10-21"]["id"])
        self.assertEqual(newer["status"], "completed")
        self.assertEqual(newer["strategy"]["amounts"][FIXTURE_CODE], "300.00")
        self.assertEqual(newer["items"][0]["accepted_amount"], "300.00")
        # 旧周期保留原版本，不被新版本改写
        older = service.run_detail(by_date["2026-10-14"]["id"])
        self.assertEqual(older["strategy"]["amounts"][FIXTURE_CODE], "500.00")


class CycleLimitAndSkipTest(SimulationStage7TestCase):
    """限额缩减与业务跳过原因。"""

    def test_daily_limit_scales_amount_within_bounds(self) -> None:
        # 限额 200.00 低于计划 500.00：按剩余额度受理到分向下取整
        limited = fixture_variant("fixture_limit_200", FIXTURE_CODE, "限额变体", max_daily_buy="200.00")
        limited["effective_from"] = "2026-10-20"
        service = self.service(rules=[FIXTURE_QDII_C_V1, limited])
        account_id = self.strategy_account(service)
        self.seed_nav(service)
        result = service.run_cycle(account_id, now=at(2026, 10, 21, 14, 5))
        assert result is not None
        item = self.item(service, result["runs"][0]["id"])
        self.assertEqual(
            (item["status"], item["requested_amount"], item["accepted_amount"], item["skipped_amount"]),
            ("accepted", "500.00", "200.00", "300.00"),
        )
        self.assertEqual(item["reason_code"], "daily_limit")
        self.assertEqual(item["rule_id"], "fixture_limit_200")

    def test_manual_order_consumes_daily_quota_before_cycle(self) -> None:
        service = self.service()
        account_id = self.strategy_account(service)
        self.seed_nav(service)
        manual, created = service.submit_order(
            account_id, "manual", side="buy", code=FIXTURE_CODE, amount="900.00",
            now=at(2026, 10, 21, 10, 0),
        )
        self.assertTrue(created)
        self.assertEqual(manual["status"], "awaiting_nav")
        result = service.run_cycle(account_id, now=at(2026, 10, 21, 14, 5))
        assert result is not None
        item = self.item(service, result["runs"][0]["id"])
        self.assertEqual((item["accepted_amount"], item["reason_code"]), ("100.00", "daily_limit"))
        # 配额用满后同一净值日的后续订单被拒
        exhausted, _ = service.submit_order(
            account_id, "after-cycle", side="buy", code=FIXTURE_CODE, amount="1.00",
            now=at(2026, 10, 21, 14, 6),
        )
        self.assertEqual((exhausted["status"], exhausted["reason_code"]), ("rejected", "daily_limit"))

    def test_remaining_quota_below_minimum_skips_without_order(self) -> None:
        tiny = fixture_variant("fixture_limit_005", FIXTURE_CODE, "极小限额", max_daily_buy="0.05")
        tiny["effective_from"] = "2026-10-20"
        service = self.service(rules=[FIXTURE_QDII_C_V1, tiny])
        account_id = self.strategy_account(service)
        self.seed_nav(service)
        result = service.run_cycle(account_id, now=at(2026, 10, 21, 14, 5))
        assert result is not None
        item = self.item(service, result["runs"][0]["id"])
        self.assertEqual((item["status"], item["accepted_amount"]), ("skipped", "0.00"))
        self.assertEqual(item["reason_code"], "daily_limit")
        self.assertIn("低于最低申购额", item["reason_detail"])
        self.assertEqual(service.list_orders(account_id)["items"], [])

    def test_paused_account_skips_with_account_paused(self) -> None:
        service = self.service()
        account_id = self.strategy_account(service)
        service.pause(account_id, "p", service.account_view(account_id)["version"], now=at(2026, 10, 17, 9))
        result = service.run_cycle(account_id, now=at(2026, 10, 21, 14, 5))
        assert result is not None
        item = self.item(service, result["runs"][0]["id"])
        self.assertEqual((item["status"], item["reason_code"]), ("skipped", "account_paused"))
        self.assertEqual(service.list_orders(account_id)["items"], [])

    def test_stale_nav_skips_with_nav_missing(self) -> None:
        service = self.service()
        account_id = self.strategy_account(service)
        # 只有 10-09 的净值：到 10-21 时已过应披露日期
        self.seed_nav(service, day=datetime.date(2026, 10, 9), nav="1.2000")
        result = service.run_cycle(account_id, now=at(2026, 10, 21, 14, 5))
        assert result is not None
        run = result["runs"][0]
        self.assertEqual(run["status"], "completed")
        item = self.item(service, run["id"])
        self.assertEqual((item["status"], item["reason_code"]), ("skipped", "nav_missing"))

    def test_rule_incomplete_skips_without_order(self) -> None:
        service = self.service()
        # 016702 没有已导入规则，用于验证规则缺失按项跳过
        account_id = self.strategy_account(
            service, codes=[NO_RULE_CODE], amounts={NO_RULE_CODE: "500.00"}
        )
        result = service.run_cycle(account_id, now=at(2026, 10, 21, 14, 5))
        assert result is not None
        item = self.item(service, result["runs"][0]["id"])
        self.assertEqual((item["code"], item["status"], item["reason_code"]), (NO_RULE_CODE, "skipped", "rule_incomplete"))
        self.assertEqual(service.list_orders(account_id)["items"], [])

    def test_settlement_continues_while_paused(self) -> None:
        service = self.service()
        account_id = self.strategy_account(service)
        self.seed_nav(service)
        result = service.run_cycle(account_id, now=at(2026, 10, 21, 14, 5))
        assert result is not None
        order_id = self.item(service, result["runs"][0]["id"])["order_id"]
        service.pause(account_id, "p", service.account_view(account_id)["version"], now=at(2026, 10, 22, 9))
        self.seed_nav(service, day=CYCLE_DAY)
        report = service.settle(account_id, now=at(2026, 10, 23, 18, 35))
        self.assertEqual(report.errors, [])
        # 暂停只停止新增买入，已受理订单继续确认与结算
        confirmed = service.order_detail(order_id)
        self.assertEqual((confirmed["status"], confirmed["confirmed_shares"]), ("settled", "384.62"))
        self.assertEqual(service.positions_view(account_id)[0]["shares"], "384.62")
        # 暂停期间的后续周期只记跳过，不产生新订单
        later = service.run_cycle(account_id, now=at(2026, 10, 28, 14, 5))
        assert later is not None
        by_date = {run["cycle_date"]: run for run in later["runs"]}
        self.assertEqual(by_date["2026-10-21"]["accepted_count"], 1)  # 已落定周期不被改写
        paused_item = self.item(service, by_date["2026-10-28"]["id"])
        self.assertEqual((paused_item["status"], paused_item["reason_code"]), ("skipped", "account_paused"))
        self.assertEqual(len(service.list_orders(account_id)["items"]), 1)

    def test_source_failure_is_recorded_and_cycle_continues(self) -> None:
        service = self.service()
        account_id = self.strategy_account(service)

        class FailingSource:
            name = "failing"

            def fetch(self, code, nav_date, observed_at):
                raise RuntimeError("来源不可用")

        result = service.run_cycle(account_id, now=at(2026, 10, 21, 14, 5), nav_source=FailingSource())
        assert result is not None
        self.assertEqual(result["fetched_navs"], 0)
        self.assertEqual(result["fetch_errors"], [{"code": FIXTURE_CODE, "error": "来源不可用"}])
        # 来源失败不生成默认数据，周期照常记录实际跳过原因
        item = self.item(service, result["runs"][0]["id"])
        self.assertEqual(item["reason_code"], "nav_missing")


class RunStatusTest(SimulationStage7TestCase):
    """周期状态汇总与运行项技术失败。"""

    def test_run_status_rules(self) -> None:
        self.assertEqual(_run_status([], []), "failed")
        self.assertEqual(_run_status(["failed", "failed"], [None, None]), "failed")
        self.assertEqual(_run_status(["failed", "accepted"], [None, None]), "partial")
        self.assertEqual(_run_status(["skipped", "skipped"], ["window_expired"] * 2), "missed")
        # 一个窗口过期、一个业务跳过，不算整轮 missed
        self.assertEqual(_run_status(["skipped", "skipped"], ["window_expired", "nav_missing"]), "completed")
        self.assertEqual(_run_status(["accepted", "skipped"], [None, "account_paused"]), "completed")

    def multi_code_service(self) -> tuple[SimulationService, str]:
        other = fixture_variant("fixture_other", OTHER_CODE, "另一只")
        service = self.service(rules=[FIXTURE_QDII_C_V1, other])
        account_id = self.strategy_account(service, codes=[FIXTURE_CODE, OTHER_CODE])
        for code in (FIXTURE_CODE, OTHER_CODE):
            self.seed_nav(service, code)
        return service, account_id

    def test_partial_failure_marks_item_and_retry_recovers(self) -> None:
        service, account_id = self.multi_code_service()
        original = SimulationService._gates

        def flaky(self, conn, account, decision, now):
            if decision.code == FIXTURE_CODE:
                raise RuntimeError("boom")
            return original(self, conn, account, decision, now)

        with patch.object(SimulationService, "_gates", flaky):
            first = service.run_cycle(account_id, now=at(2026, 10, 21, 14, 5))
        assert first is not None
        detail = service.run_detail(first["runs"][0]["id"])
        self.assertEqual(
            (detail["status"], detail["accepted_count"], detail["skipped_count"], detail["failed_count"]),
            ("partial", 1, 0, 1),
        )
        failed = next(item for item in detail["items"] if item["code"] == FIXTURE_CODE)
        self.assertEqual(
            (failed["status"], failed["reason_code"], failed["error"]),
            ("failed", "technical_failure", "boom"),
        )
        self.assertIsNone(failed["order_id"])
        # 已受理项不被重试重复下单
        second = service.run_cycle(account_id, now=at(2026, 10, 21, 14, 6))
        assert second is not None
        recovered = service.run_detail(second["runs"][0]["id"])
        self.assertEqual((recovered["status"], recovered["accepted_count"]), ("completed", 2))
        self.assertEqual(
            sorted((order["code"], order["accepted_amount"]) for order in service.list_orders(account_id)["items"]),
            [(FIXTURE_CODE, "500.00"), (OTHER_CODE, "500.00")],
        )

    def test_total_failure_marks_run_failed(self) -> None:
        service, account_id = self.multi_code_service()

        def always_fail(self, conn, account, decision, now):
            raise RuntimeError("boom")

        with patch.object(SimulationService, "_gates", always_fail):
            result = service.run_cycle(account_id, now=at(2026, 10, 21, 14, 5))
        assert result is not None
        detail = service.run_detail(result["runs"][0]["id"])
        self.assertEqual((detail["status"], detail["failed_count"]), ("failed", 2))
        self.assertEqual(service.list_orders(account_id)["items"], [])


class RunQueryTest(SimulationStage7TestCase):
    """运行记录查询：分页、详情与错误。"""

    def four_cycle_service(self) -> tuple[SimulationService, str]:
        service = self.service()
        account_id = self.strategy_account(
            service, effective_from=datetime.date(2026, 10, 14), seen=at(2026, 10, 14, 9, 30)
        )
        service.run_cycle(account_id, now=at(2026, 11, 11, 15))
        return service, account_id

    def test_pagination_walks_all_cycles(self) -> None:
        service, account_id = self.four_cycle_service()
        page = service.list_runs(account_id, limit=2)
        self.assertEqual([run["cycle_date"] for run in page["items"]], ["2026-11-11", "2026-11-04"])
        self.assertIsNotNone(page["next_cursor"])
        rest = service.list_runs(account_id, limit=2, cursor=page["next_cursor"])
        self.assertEqual([run["cycle_date"] for run in rest["items"]], ["2026-10-28", "2026-10-21"])
        last = service.list_runs(account_id, limit=2, cursor=rest["next_cursor"])
        self.assertEqual([run["cycle_date"] for run in last["items"]], ["2026-10-14"])
        self.assertIsNone(last["next_cursor"])

    def test_pagination_and_detail_errors(self) -> None:
        service, account_id = self.four_cycle_service()
        for limit in (0, 101):
            with self.assertRaises(SimulationError) as error:
                service.list_runs(account_id, limit=limit)
            self.assertEqual((error.exception.code, error.exception.fields), ("invalid_limit", ["limit"]))
        with self.assertRaises(SimulationError) as cursor:
            service.list_runs(account_id, cursor="%%%")
        self.assertEqual(cursor.exception.code, "invalid_cursor")
        with self.assertRaises(SimulationError) as missing:
            service.run_detail("run_missing")
        self.assertEqual((missing.exception.code, missing.exception.status), ("run_not_found", 404))
        with self.assertRaises(SimulationError) as unknown_account:
            service.list_runs("acct_missing")
        self.assertEqual(unknown_account.exception.code, "account_not_found")

    def test_run_detail_exposes_strategy_and_items(self) -> None:
        service, account_id = self.four_cycle_service()
        run = service.list_runs(account_id, limit=1)["items"][0]
        detail = service.run_detail(run["id"])
        self.assertEqual(
            (detail["cycle_date"], detail["status"], detail["reason_code"]),
            ("2026-11-11", "missed", "window_expired"),
        )
        self.assertEqual(
            (detail["accepted_count"], detail["skipped_count"], detail["failed_count"], detail["order_count"]),
            (0, 1, 0, 0),
        )
        self.assertEqual(detail["snapshot_ids"], [])
        strategy = detail["strategy"]
        self.assertEqual(
            (strategy["weekday"], strategy["time"], strategy["window_end"], strategy["rule_set"]),
            (STRATEGY_WEEKDAY, "14:00", "14:10", "fixture_qdii_c_v1"),
        )
        item = detail["items"][0]
        self.assertEqual(
            (item["code"], item["status"], item["requested_amount"], item["skipped_amount"]),
            (FIXTURE_CODE, "skipped", "500.00", "500.00"),
        )
        self.assertIn("窗口", item["reason_detail"])


class MetricsTest(SimulationStage7TestCase):
    """费用后指标口径。"""

    def funded_service(self, amount: str = "1000.00") -> tuple[SimulationService, str, str]:
        """建账户、买入并结算一笔订单，返回 (服务, 账户, 订单)。"""
        service = self.service()
        account_id = self.account(service)
        self.seed_nav(service, day=datetime.date(2026, 10, 9), nav="1.2000")
        order, _ = service.submit_order(
            account_id, "k1", side="buy", code=FIXTURE_CODE, amount=amount, now=at(2026, 10, 14, 14)
        )
        service.record_nav(FIXTURE_CODE, datetime.date(2026, 10, 14), "1.2500", now=at(2026, 10, 15, 20))
        service.settle(account_id, now=at(2026, 10, 16, 18))
        return service, account_id, order["id"]

    def test_cash_ratio_counts_frozen_cash(self) -> None:
        service = self.service()
        account_id = self.account(service)
        self.seed_nav(service, day=datetime.date(2026, 10, 9), nav="1.2000")
        service.submit_order(
            account_id, "k1", side="buy", code=FIXTURE_CODE, amount="1000.00", now=at(2026, 10, 14, 14)
        )
        metrics = service.account_metrics(account_id, now=at(2026, 10, 14, 15))
        # 未确认买入计冻结现金，仍属现金口径：(A+F)/V
        self.assertEqual(metrics["cash_ratio"], "1.000000")
        self.assertEqual(metrics["receivable"], "0.00")
        self.assertEqual(metrics["dividend_receivable"], "0.00")
        self.assertEqual(metrics["total_fees"], "0.00")
        self.assertIsNone(metrics["turnover"])

    def test_turnover_requires_daily_close_samples(self) -> None:
        service, account_id, _ = self.funded_service()
        before = service.account_metrics(account_id, now=at(2026, 10, 16, 19))
        # 没有日终样本时换手为空，不用观察快照顶替
        self.assertIsNone(before["turnover"])
        self.assertEqual(before["turnover_inputs"]["daily_close_samples"], 0)
        self.assertEqual(before["turnover_inputs"]["avg_daily_close"], None)

        service.record_daily_close(account_id, now=at(2026, 10, 16, 23, 35))
        after = service.account_metrics(account_id, now=at(2026, 10, 16, 23, 40))
        self.assertEqual(after["turnover_inputs"]["daily_close_samples"], 1)
        self.assertEqual(after["turnover_inputs"]["buy_net"], "1000.00")
        self.assertEqual(after["turnover_inputs"]["sell_gross"], "0.00")
        self.assertEqual(after["turnover_inputs"]["avg_daily_close"], "200000.00")
        # (累计申购净额 + 累计赎回毛额) / 完整日终资产均值，不除以 2、不年化
        self.assertEqual(after["turnover"], "0.005000")

    def test_turnover_uses_sell_gross_not_proceeds(self) -> None:
        service, account_id, _ = self.funded_service()
        # 赎回申请需当日已取得官方净值，才能在下一个确认日结算
        self.seed_nav(service, day=datetime.date(2026, 10, 20), nav="1.2800")
        sell, _ = service.submit_order(
            account_id, "sell1", side="sell", code=FIXTURE_CODE, shares="100.00", now=at(2026, 10, 21, 10)
        )
        self.assertEqual(sell["status"], "awaiting_nav")
        self.seed_nav(service, day=CYCLE_DAY)
        service.settle(account_id, now=at(2026, 10, 23, 18, 35))
        detail = service.order_detail(sell["id"])
        # 短持按 1.5% 计费，净额小于毛额
        self.assertEqual(detail["status"], "awaiting_payment")
        self.assertEqual((detail["gross_amount"], detail["fee"]), ("130.00", "0.65"))
        self.assertEqual(detail["net_amount"], "129.35")

        service.record_daily_close(account_id, now=at(2026, 10, 23, 23, 35))
        metrics = service.account_metrics(account_id, now=at(2026, 10, 23, 23, 40))
        # 换手按赎回毛额计，净值用应收净额，费用单列
        self.assertEqual(metrics["turnover_inputs"]["buy_net"], "1000.00")
        self.assertEqual(metrics["turnover_inputs"]["sell_gross"], "130.00")
        self.assertEqual(metrics["total_fees"], "0.65")
        self.assertGreater(Decimal(metrics["turnover_inputs"]["sell_gross"]), Decimal(detail["net_amount"]))
        expected = (Decimal("1000.00") + Decimal("130.00")) / Decimal(
            metrics["turnover_inputs"]["avg_daily_close"]
        )
        self.assertEqual(Decimal(metrics["turnover"]).quantize(Decimal("0.000001")), expected.quantize(Decimal("0.000001")))

    def test_max_drawdown_uses_complete_snapshots_only(self) -> None:
        service, account_id, _ = self.funded_service()
        # 净值不再更新：持有期间快照标不完整，仍照常保存
        service.valuate(account_id, now=at(2026, 10, 21, 13, 50))
        metrics = service.account_metrics(account_id, now=at(2026, 10, 21, 14))
        self.assertEqual((metrics["complete_snapshots"], metrics["incomplete_snapshots"]), (1, 1))
        # 不完整快照不进最大回撤，也不抬高历史高点
        self.assertEqual(metrics["max_drawdown"], "0.000000")
        self.assertIsNotNone(metrics["valuation_range"]["min"])
        self.assertIsNotNone(metrics["valuation_range"]["max"])

    def test_metrics_runs_summary_and_unknown_account(self) -> None:
        service = self.service()
        account_id = self.strategy_account(
            service, effective_from=datetime.date(2026, 10, 14), seen=at(2026, 10, 14, 9, 30)
        )
        empty = service.account_metrics(account_id, now=at(2026, 10, 14, 10))
        self.assertEqual(empty["runs"]["count"], 0)
        self.assertIsNone(empty["runs"]["first_cycle"])
        self.assertIsNone(empty["runs"]["last_cycle"])
        service.run_cycle(account_id, now=at(2026, 11, 11, 15))
        filled = service.account_metrics(account_id, now=at(2026, 11, 11, 16))
        self.assertEqual(
            (filled["runs"]["count"], filled["runs"]["first_cycle"], filled["runs"]["last_cycle"]),
            (5, "2026-10-14", "2026-11-11"),
        )
        with self.assertRaises(SimulationError) as missing:
            service.account_metrics("acct_missing", now=at(2026, 11, 11, 16))
        self.assertEqual(missing.exception.code, "account_not_found")


class DailyCloseTest(SimulationStage7TestCase):
    """23:30 日终快照。"""

    def test_one_close_per_beijing_day(self) -> None:
        service = self.service()
        account_id = self.account(service)
        first = service.record_daily_close(account_id, now=at(2026, 10, 14, 23, 35))
        second = service.record_daily_close(account_id, now=at(2026, 10, 14, 23, 45))
        next_day = service.record_daily_close(account_id, now=at(2026, 10, 15, 23, 35))
        self.assertEqual((first["created"], second["created"], next_day["created"]), (True, False, True))
        self.assertEqual((first["kind_date"], next_day["kind_date"]), ("2026-10-14", "2026-10-15"))
        # 重复调用返回已有快照
        self.assertEqual(second["valuation"]["id"], first["valuation"]["id"])
        with service.store.read() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM valuations WHERE kind = 'daily_close'"
            ).fetchone()[0]
        self.assertEqual(count, 2)

    def test_daily_close_uses_beijing_date_not_utc(self) -> None:
        service = self.service()
        account_id = self.account(service)
        # 北京时间 10-15 00:30 仍是 10-14 的 UTC 日，日终应按北京时间归日
        result = service.record_daily_close(account_id, now=at(2026, 10, 15, 0, 30))
        self.assertEqual(result["kind_date"], "2026-10-15")

    def test_daily_close_is_unique_per_account_and_day(self) -> None:
        service = self.service()
        account_id = self.account(service)
        service.record_daily_close(account_id, now=at(2026, 10, 14, 23, 35))
        with self.assertRaises(sqlite3.IntegrityError):
            with service.store.write() as conn:
                conn.execute(
                    """
                    INSERT INTO valuations (
                        id, account_id, kind, kind_date, observed_at, nav_refs_json, available,
                        frozen, receivable, dividend_receivable, holdings_value, total, quality,
                        missing_json, peak, drawdown
                    ) VALUES ('val_dup', ?, 'daily_close', '2026-10-14', 'now', '[]', '0.00', '0.00',
                              '0.00', '0.00', '0.00', '0.00', 'complete', '[]', '0.00', '0.000000')
                    """,
                    (account_id,),
                )

    def test_observation_and_close_can_share_a_timestamp(self) -> None:
        service = self.service()
        account_id = self.account(service)
        # 唯一索引只约束 daily_close，观察快照不受影响
        service.valuate(account_id, now=at(2026, 10, 14, 23, 35))
        result = service.record_daily_close(account_id, now=at(2026, 10, 14, 23, 35))
        self.assertTrue(result["created"])


class SlotTest(unittest.TestCase):
    """调度 slot 判定。"""

    def test_slot_window_is_half_open(self) -> None:
        slot = (8, 30)
        self.assertFalse(_in_slot(at(2026, 10, 14, 8, 29), slot))
        self.assertTrue(_in_slot(at(2026, 10, 14, 8, 30), slot))
        self.assertTrue(_in_slot(at(2026, 10, 14, 8, 30 + TICK_WINDOW_MINUTES - 1, 59), slot))
        self.assertFalse(_in_slot(at(2026, 10, 14, 8, 30 + TICK_WINDOW_MINUTES), slot))

    def test_plan_slots_cover_settlement_and_close(self) -> None:
        self.assertEqual(SETTLE_SLOTS, ((8, 30), (18, 30), (21, 30), (23, 30), (0, 30)))
        self.assertEqual(DAILY_CLOSE_SLOT, (23, 30))
        for slot in SETTLE_SLOTS:
            self.assertTrue(_in_slot(at(2026, 10, 14, *slot), slot))
        # 00:30 按当日 00:30 判定，不跨到前一日 23:30
        self.assertFalse(_in_slot(at(2026, 10, 14, 23, 30), (0, 30)))
        # 周期窗口 14:00 不属于任何结算 slot，必须由 tick 的周期处理覆盖
        self.assertFalse(any(_in_slot(at(2026, 10, 21, 14, 5), slot) for slot in SETTLE_SLOTS))


class ScheduledTickTest(SimulationStage7TestCase):
    """每日调度入口。"""

    def test_tick_processes_cycle_outside_settle_slots(self) -> None:
        service = self.service()
        account_id = self.strategy_account(service)
        self.seed_nav(service)
        # 14:05 不在任何结算 slot 内，但仍必须处理周期，否则每周都落 missed
        actions = service.scheduled_tick(now=at(2026, 10, 21, 14, 5))["actions"]
        self.assertEqual([action["action"] for action in actions], ["cycles"])
        self.assertEqual(actions[0]["runs"][0]["accepted_count"], 1)
        self.assertEqual(len(service.list_orders(account_id)["items"]), 1)

    def test_tick_settles_and_records_close_in_slot(self) -> None:
        service = self.service()
        account_id = self.strategy_account(
            service, effective_from=datetime.date(2026, 10, 14), seen=at(2026, 10, 14, 9, 30)
        )
        actions = service.scheduled_tick(now=at(2026, 10, 14, 23, 35))["actions"]
        self.assertEqual([action["action"] for action in actions], ["cycles", "settle", "daily_close"])
        self.assertTrue(actions[2]["created"])
        # 同一 slot 内重复 tick：结算与周期不重复，日终快照返回已存在记录
        again = service.scheduled_tick(now=at(2026, 10, 14, 23, 45))["actions"]
        self.assertEqual([action["action"] for action in again], ["cycles", "settle", "daily_close"])
        self.assertFalse(again[2]["created"])
        self.assertEqual(again[2]["valuation"]["id"], actions[2]["valuation"]["id"])
        self.assertEqual(actions[0]["runs"][0]["id"], again[0]["runs"][0]["id"])
        self.assertEqual(len(service.list_orders(account_id)["items"]), 0)

    def test_tick_skips_when_no_cycle_or_slot_action(self) -> None:
        service = self.service()
        # 10-16 是周五：没有到期周期，也不在任何 slot 内
        self.account(service)
        result = service.scheduled_tick(now=at(2026, 10, 16, 12, 0))
        self.assertEqual(result["actions"], [{"action": "skip", "reason": "outside_slots"}])

    def test_tick_without_active_account(self) -> None:
        service = SimulationService(SimulationStore(self.root / "sim.db", environment="fixture"))
        self.assertEqual(
            service.scheduled_tick(now=at(2026, 10, 21, 14, 5))["actions"],
            [{"action": "skip", "reason": "no_active_account"}],
        )

    def test_tick_can_skip_settlement(self) -> None:
        service = self.service()
        self.strategy_account(
            service, effective_from=datetime.date(2026, 10, 14), seen=at(2026, 10, 14, 9, 30)
        )
        actions = service.scheduled_tick(now=at(2026, 10, 14, 23, 35), settle=False)["actions"]
        self.assertEqual([action["action"] for action in actions], ["cycles", "daily_close"])


class ProductionBoundaryTest(SimulationStage7TestCase):
    """生产库拒绝指定时间，且默认没有可运行规则。"""

    def test_production_rejects_time_override(self) -> None:
        service = SimulationService(SimulationStore(self.root / "prod.db"))
        calls = {
            "run_cycle": lambda: service.run_cycle("acct_x", now=at(2026, 10, 21, 14, 5)),
            "scheduled_tick": lambda: service.scheduled_tick(now=at(2026, 10, 21, 14, 5)),
            "account_metrics": lambda: service.account_metrics("acct_x", now=at(2026, 10, 21, 14, 5)),
            "record_daily_close": lambda: service.record_daily_close("acct_x", now=at(2026, 10, 21, 23, 35)),
        }
        for label, call in calls.items():
            with self.subTest(call=label):
                with self.assertRaises(SimulationError) as error:
                    call()
                self.assertEqual(error.exception.code, "time_override_forbidden")

    def test_production_rejects_fixture_rules(self) -> None:
        # fixture 规则不能导入生产库，因此生产环境没有可运行基金
        service = SimulationService(SimulationStore(self.root / "prod.db"))
        with self.assertRaises(SimulationError) as error:
            service.import_rule(FIXTURE_QDII_C_V1)
        self.assertEqual(error.exception.code, "rule_environment_mismatch")
        self.assertEqual(service.verified_codes(), [])

    def test_production_tick_runs_on_server_clock(self) -> None:
        """定时任务无参调用必须可用：`_now` 把非 None 视为外部指定时间。"""
        clock = {"now": at(2026, 10, 21, 14, 5)}
        service = SimulationService(
            SimulationStore(self.root / "prod.db"), clock=lambda: clock["now"]
        )
        # 无活跃账户时也要走通，不能因为把服务端时刻下传而整体报 time_override_forbidden
        self.assertEqual(
            service.scheduled_tick()["actions"],
            [{"action": "skip", "reason": "no_active_account"}],
        )
        # 有账户且处于结算 slot 时，子步骤同样以服务端时钟推进
        account_id = service.create_account("initial")[0]["id"]
        clock["now"] = at(2026, 10, 21, 23, 35)
        actions = service.scheduled_tick()["actions"]
        self.assertIn("settle", [action["action"] for action in actions])
        self.assertIn("daily_close", [action["action"] for action in actions])
        with service.store.read() as conn:
            closes = conn.execute(
                "SELECT kind_date FROM valuations WHERE account_id = ? AND kind = 'daily_close'",
                (account_id,),
            ).fetchall()
        self.assertEqual([row["kind_date"] for row in closes], ["2026-10-21"])

    def test_production_tick_still_rejects_explicit_time(self) -> None:
        service = SimulationService(SimulationStore(self.root / "prod.db"))
        with self.assertRaises(SimulationError) as error:
            service.scheduled_tick(now=at(2026, 10, 21, 14, 5))
        self.assertEqual(error.exception.code, "time_override_forbidden")


class SchemaMigrationTest(unittest.TestCase):
    """v1 模拟库原地迁移到 v2。"""

    V1_META = "CREATE TABLE sim_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    V1_VALUATIONS = """
        CREATE TABLE valuations (
            id TEXT PRIMARY KEY,
            account_id TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            nav_refs_json TEXT NOT NULL,
            available TEXT NOT NULL,
            frozen TEXT NOT NULL,
            receivable TEXT NOT NULL,
            dividend_receivable TEXT NOT NULL,
            holdings_value TEXT,
            total TEXT,
            quality TEXT NOT NULL,
            missing_json TEXT NOT NULL,
            peak TEXT,
            drawdown TEXT
        )
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def v1_database(self, version: str = "1", name: str = "v1.db") -> Path:
        path = self.root / name
        with contextlib.closing(sqlite3.connect(path)) as conn, conn:
            conn.execute(self.V1_META)
            conn.execute(self.V1_VALUATIONS)
            conn.executemany(
                "INSERT INTO sim_meta (key, value) VALUES (?, ?)",
                [("schema_version", version), ("environment", "fixture"), ("created_at", "now")],
            )
        return path

    def schema_state(self, path: Path) -> tuple[str, list[str], set[str]]:
        with contextlib.closing(sqlite3.connect(path)) as conn:
            version = conn.execute(
                "SELECT value FROM sim_meta WHERE key = 'schema_version'"
            ).fetchone()[0]
            columns = [row[1] for row in conn.execute("PRAGMA table_info(valuations)")]
            tables = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        return version, columns, tables

    def test_v1_database_migrates_in_place(self) -> None:
        path = self.v1_database()
        store = SimulationStore(path)
        self.assertEqual(store.environment, "fixture")
        version, columns, tables = self.schema_state(path)
        self.assertEqual(version, str(SCHEMA_VERSION))
        # 新增列默认归为观察快照，历史行不会被当成日终样本
        self.assertIn("kind", columns)
        self.assertIn("kind_date", columns)
        self.assertTrue({"strategy_versions", "runs", "run_items"} <= tables)
        # 迁移后仍可用：账本重放无问题，服务可读写
        service = SimulationService(store)
        self.assertEqual(service.verify(), [])
        self.assertEqual(service.list_accounts(), [])

    def test_migrated_v1_rows_stay_observation_kind(self) -> None:
        path = self.v1_database()
        with contextlib.closing(sqlite3.connect(path)) as conn, conn:
            conn.execute(
                """
                INSERT INTO valuations (
                    id, account_id, observed_at, nav_refs_json, available, frozen, receivable,
                    dividend_receivable, holdings_value, total, quality, missing_json, peak, drawdown
                ) VALUES ('val_old', 'acct_x', 'now', '[]', '0.00', '0.00', '0.00', '0.00',
                          '0.00', '0.00', 'complete', '[]', '0.00', '0.000000')
                """
            )
        SimulationStore(path)
        with contextlib.closing(sqlite3.connect(path)) as conn:
            row = conn.execute("SELECT kind, kind_date FROM valuations WHERE id = 'val_old'").fetchone()
        self.assertEqual(row, ("observation", None))

    def test_reopening_migrated_database_is_idempotent(self) -> None:
        path = self.v1_database()
        SimulationStore(path)
        SimulationStore(path)  # 再次打开不再迁移
        self.assertEqual(self.schema_state(path)[0], str(SCHEMA_VERSION))

    def test_unsupported_schema_version_is_rejected(self) -> None:
        with self.assertRaises(SimulationConfigError) as error:
            SimulationStore(self.v1_database(version="0"))
        self.assertIn("不支持的模拟库 schema 版本", str(error.exception))


if __name__ == "__main__":
    unittest.main()
