from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from fund_analysis.cli import main as cli_main
from fund_analysis.simulation.acceptance import FIXTURE_CODE, at, run_acceptance
from fund_analysis.simulation.maintenance import backup_simulation_db, restore_simulation_db
from fund_analysis.simulation.nav_sources import MainDbNavSource, NavObservation
from fund_analysis.simulation.rules import (
    FIXTURE_QDII_C_V1,
    UNLIMITED,
    RuleValidationError,
    fixture_variant,
    validate_rule_record,
)
from fund_analysis.simulation.service import SimulationError, SimulationService
from fund_analysis.simulation.store import (
    SimulationConfigError,
    SimulationStore,
    resolve_simulation_path,
)
from fund_analysis.web.storage import LocalStorage

FRESH_DAY = date(2026, 10, 9)
FRESH_SEEN = at(2026, 10, 13, 18, 30)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def production_rule(code: str = "016665") -> dict:
    record = json.loads(json.dumps(FIXTURE_QDII_C_V1))
    record.update(id=f"prod_test_{code}", environment="production", code=code)
    return record


class SimulationTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def fixture_service(self, name: str = "sim.db", rules: list[dict] | None = None) -> SimulationService:
        service = SimulationService(SimulationStore(self.root / name, environment="fixture"))
        for rule in rules or [FIXTURE_QDII_C_V1]:
            service.import_rule(rule)
        return service

    def open_account(self, service: SimulationService) -> str:
        return service.create_account("initial", now=at(2026, 10, 14, 9))[0]["id"]

    def buy(self, service: SimulationService, account_id: str, key: str, amount: str, when=None, code=FIXTURE_CODE):
        return service.submit_order(
            account_id, key, side="buy", code=code, amount=amount, now=when or at(2026, 10, 14, 14)
        )


class AcceptanceTest(SimulationTestCase):
    def test_all_plan_cases_pass(self) -> None:
        checks = run_acceptance(self.root / "acceptance")
        failed = [f"{c.case} {c.label}: 预期 {c.expected} 实际 {c.actual}" for c in checks if not c.ok]
        self.assertEqual(failed, [])
        self.assertGreater(len(checks), 100)

    def test_acceptance_requires_empty_directory(self) -> None:
        target = self.root / "used"
        target.mkdir()
        (target / "keep.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            run_acceptance(target)


class PathAndEnvironmentTest(SimulationTestCase):
    def test_rejects_main_db_aliases(self) -> None:
        main = self.root / "fund_analysis.db"
        LocalStorage(main)
        with self.assertRaises(SimulationConfigError):
            resolve_simulation_path(main, main)
        with self.assertRaises(SimulationConfigError):
            resolve_simulation_path(self.root / "fund_analysis.db-wal", main)
        link = self.root / "link.db"
        link.symlink_to(main)
        with self.assertRaises(SimulationConfigError):
            resolve_simulation_path(link, main)
        hard = self.root / "hard.db"
        os.link(main, hard)
        with self.assertRaises(SimulationConfigError):
            resolve_simulation_path(hard, main)
        self.assertEqual(resolve_simulation_path(self.root / "sim.db", main), (self.root / "sim.db").resolve())

    def test_environment_is_fixed_and_production_rejects_fixture_inputs(self) -> None:
        service = SimulationService(SimulationStore(self.root / "prod.db"))
        self.assertEqual(service.store.environment, "production")
        with self.assertRaises(SimulationConfigError):
            SimulationStore(self.root / "prod.db", environment="fixture")
        with self.assertRaises(SimulationError) as rule_error:
            service.import_rule(FIXTURE_QDII_C_V1)
        self.assertEqual(rule_error.exception.code, "rule_environment_mismatch")
        with self.assertRaises(SimulationError) as time_error:
            service.create_account("initial", now=at(2026, 10, 14, 9))
        self.assertEqual(time_error.exception.code, "time_override_forbidden")
        with self.assertRaises(SimulationError):
            service.record_nav(FIXTURE_CODE, FRESH_DAY, "1.0000")

    def test_rule_validation_treats_null_as_unknown(self) -> None:
        record = json.loads(json.dumps(FIXTURE_QDII_C_V1))
        record["body"]["max_daily_buy"] = None
        with self.assertRaises(RuleValidationError):
            validate_rule_record(record)
        record["status"] = "incomplete"
        validate_rule_record(record)  # 未核验规则可留档，但不能下单
        broken = json.loads(json.dumps(FIXTURE_QDII_C_V1))
        broken["body"]["redemption_fee_tiers"][1]["min_days"] = 8
        with self.assertRaises(RuleValidationError):
            validate_rule_record(broken)
        active_with_index = fixture_variant("x", "990001", "x", index_group="NDX")
        with self.assertRaises(RuleValidationError):
            validate_rule_record(active_with_index)

    def test_incomplete_rule_blocks_orders_and_versions_are_immutable(self) -> None:
        incomplete = json.loads(json.dumps(FIXTURE_QDII_C_V1))
        incomplete.update(id="fixture_incomplete", code="017731", status="incomplete")
        service = self.fixture_service(rules=[FIXTURE_QDII_C_V1, incomplete])
        account_id = self.open_account(service)
        order, _ = self.buy(service, account_id, "k", "100.00", code="017731")
        self.assertEqual((order["status"], order["reason_code"]), ("rejected", "rule_incomplete"))
        changed = json.loads(json.dumps(FIXTURE_QDII_C_V1))
        changed["body"]["min_buy"] = "1.00"
        with self.assertRaises(SimulationError) as error:
            service.import_rule(changed)
        self.assertEqual(error.exception.code, "rule_version_immutable")
        self.assertEqual(service.import_rule(FIXTURE_QDII_C_V1), (FIXTURE_QDII_C_V1["id"], False))

    def test_rule_versions_reject_ambiguous_same_code_intervals(self) -> None:
        """规则只按代码选取，因此同代码不允许歧义版本；修订必须使用更晚的生效日。"""
        service = self.fixture_service()
        same_day = json.loads(json.dumps(FIXTURE_QDII_C_V1))
        same_day.update(id="fixture_revision", effective_from=FIXTURE_QDII_C_V1["effective_from"])
        with self.assertRaises(SimulationError) as same_day_error:
            service.import_rule(same_day)
        self.assertEqual(same_day_error.exception.code, "rule_interval_conflict")
        other_class = fixture_variant("fixture_share_a", FIXTURE_CODE, "A 类变体")
        other_class["share_class"] = "A"
        other_class["effective_from"] = "2027-01-01"
        with self.assertRaises(SimulationError) as class_error:
            service.import_rule(other_class)
        self.assertEqual(class_error.exception.code, "rule_identity_conflict")

        closed = fixture_variant("fixture_closed", FIXTURE_CODE, "声明结束日的版本")
        closed["effective_from"] = "2026-06-01"
        closed["effective_to"] = "2026-12-31"
        service.import_rule(closed)
        covering = fixture_variant("fixture_covering", FIXTURE_CODE, "穿入已声明区间")
        covering["effective_from"] = "2026-07-01"
        with self.assertRaises(SimulationError) as cover_error:
            service.import_rule(covering)
        self.assertEqual(cover_error.exception.code, "rule_interval_conflict")

        # 左闭右开：新区间正好接在旧版本的结束日上，接缝日只属新版本。
        successor = fixture_variant("fixture_successor", FIXTURE_CODE, "续期变体", min_buy="7.00")
        successor["effective_from"] = "2026-12-31"
        service.import_rule(successor)
        with service.store.read() as conn:
            before = service._rule_for(conn, FIXTURE_CODE, date(2026, 10, 14))
            seam = service._rule_for(conn, FIXTURE_CODE, date(2026, 12, 31))
        self.assertEqual(before[1:], ("fixture_closed", None))
        self.assertEqual(seam[1:], ("fixture_successor", None))

    def test_rule_revision_in_later_version_keeps_fund_tradable(self) -> None:
        """修订是新增更晚版本，不是改写旧版本；旧的开放结束日版本不应锁死后续买入。"""
        service = self.fixture_service()
        revised = fixture_variant("fixture_qdii_c_v2", FIXTURE_CODE, "续期修订", min_buy="1.00")
        revised["effective_from"] = "2026-10-15"
        self.assertEqual(service.import_rule(revised), ("fixture_qdii_c_v2", True))
        account_id = self.open_account(service)
        service.record_nav(FIXTURE_CODE, FRESH_DAY, "1.2000", now=FRESH_SEEN)
        order, _ = self.buy(service, account_id, "k", "100.00")
        self.assertEqual((order["status"], order["reason_code"]), ("awaiting_nav", None))
        # 修订日之后按新版本受理，旧版本不再参与。
        with service.store.read() as conn:
            self.assertEqual(
                service._rule_for(conn, FIXTURE_CODE, date(2026, 10, 15))[1:],
                ("fixture_qdii_c_v2", None),
            )

    def test_ambiguous_versions_written_outside_import_still_block_orders(self) -> None:
        """导入校验之外直接写入的歧义版本仍须阻断，不静默选一条执行。"""
        service = self.fixture_service()
        duplicated = json.loads(json.dumps(FIXTURE_QDII_C_V1))
        duplicated["id"] = "fixture_direct_write"
        with service.store.write() as conn:
            conn.execute(
                """
                INSERT INTO rule_versions (
                    id, code, channel, share_class, currency, environment, status,
                    effective_from, effective_to, record_json, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "fixture_direct_write",
                    FIXTURE_CODE,
                    duplicated["channel"],
                    duplicated["share_class"],
                    duplicated["currency"],
                    "fixture",
                    "verified",
                    duplicated["effective_from"],
                    None,
                    json.dumps(duplicated, ensure_ascii=False, sort_keys=True),
                    "2026-10-16T00:00:00.000000+00:00",
                ),
            )
        account_id = self.open_account(service)
        service.record_nav(FIXTURE_CODE, FRESH_DAY, "1.2000", now=FRESH_SEEN)
        order, _ = self.buy(service, account_id, "k", "100.00")
        self.assertEqual((order["status"], order["reason_code"]), ("rejected", "rule_conflict"))


class OrderSafetyTest(SimulationTestCase):
    def test_idempotent_replay_and_conflict(self) -> None:
        service = self.fixture_service()
        account_id = self.open_account(service)
        service.record_nav(FIXTURE_CODE, FRESH_DAY, "1.2000", now=FRESH_SEEN)
        first, created = self.buy(service, account_id, "k1", "500")
        replay, replay_created = self.buy(service, account_id, "k1", "500.00", when=at(2026, 10, 14, 14, 5))
        self.assertTrue(created)
        self.assertFalse(replay_created)
        self.assertEqual(first["id"], replay["id"])
        with self.assertRaises(SimulationError) as error:
            self.buy(service, account_id, "k1", "600.00")
        self.assertEqual(error.exception.code, "idempotency_conflict")
        rejected, _ = self.buy(service, account_id, "k2", "900.00")
        self.assertEqual(rejected["reason_code"], "daily_limit")
        again, again_created = self.buy(service, account_id, "k2", "900.00")
        self.assertEqual((again["id"], again_created), (rejected["id"], False))
        balances = service.account_view(account_id)["balances"]
        self.assertEqual((balances["available"], balances["frozen"]), ("199500.00", "500.00"))
        self.assertEqual(service.verify(), [])

    def test_input_validation_rejects_floats_and_extra_precision(self) -> None:
        service = self.fixture_service()
        account_id = self.open_account(service)
        for kwargs in (
            {"side": "buy", "code": FIXTURE_CODE, "amount": 500.0},
            {"side": "buy", "code": FIXTURE_CODE, "amount": "1e3"},
            {"side": "buy", "code": FIXTURE_CODE, "amount": "10.001"},
            {"side": "buy", "code": FIXTURE_CODE, "amount": "0"},
            {"side": "buy", "code": FIXTURE_CODE, "amount": "10", "shares": "1"},
            {"side": "sell", "code": FIXTURE_CODE, "shares": "1.00001"},
        ):
            with self.assertRaises(SimulationError) as error:
                service.submit_order(account_id, "bad", now=at(2026, 10, 14, 14), **kwargs)
            self.assertEqual(error.exception.status, 422, kwargs)

    def test_transaction_failure_rolls_back_and_retry_succeeds(self) -> None:
        service = self.fixture_service()
        account_id = self.open_account(service)
        service.record_nav(FIXTURE_CODE, FRESH_DAY, "1.2000", now=FRESH_SEEN)
        original = SimulationService._ledger

        def failing(*args, **kwargs):
            if args[3] == "buy_accept":
                raise RuntimeError("simulated crash after freeze")
            return original(*args, **kwargs)

        with patch.object(SimulationService, "_ledger", staticmethod(failing)):
            with self.assertRaises(RuntimeError):
                self.buy(service, account_id, "k1", "500.00")
        with service.store.read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)
        self.assertEqual(service.account_view(account_id)["balances"]["available"], "200000.00")
        order, created = self.buy(service, account_id, "k1", "500.00")
        self.assertTrue(created)
        self.assertEqual(order["status"], "awaiting_nav")
        self.assertEqual(service.verify(), [])

    def test_concurrent_submissions_never_overspend_or_duplicate(self) -> None:
        codes = [f"99010{i}" for i in range(5)]
        rules = [fixture_variant(f"r{c}", c, c, max_daily_buy=UNLIMITED) for c in codes]
        service = self.fixture_service(rules=rules)
        account_id = self.open_account(service)
        for code in codes:
            service.record_nav(code, FRESH_DAY, "1.0000", now=FRESH_SEEN)
        path = service.store.path
        results: list[dict] = []
        errors: list[Exception] = []

        def worker(code: str, key: str) -> None:
            try:
                local = SimulationService(SimulationStore(path))
                results.append(
                    local.submit_order(account_id, key, side="buy", code=code, amount="50000.00", now=at(2026, 10, 14, 14))[0]
                )
            except Exception as exc:  # pragma: no cover - 失败时在断言中暴露
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(code, f"k-{code}")) for code in codes]
        threads += [threading.Thread(target=worker, args=(codes[0], f"k-{codes[0]}")) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        with service.store.read() as conn:
            orders = conn.execute("SELECT status, reason_code FROM orders").fetchall()
        self.assertEqual(len(orders), 5)
        accepted = [row for row in orders if row["status"] == "awaiting_nav"]
        self.assertEqual(len(accepted), 4)
        self.assertEqual([row["reason_code"] for row in orders if row["status"] == "rejected"], ["insufficient_cash"])
        self.assertEqual(service.account_view(account_id)["balances"]["available"], "0.00")
        self.assertEqual(service.verify(), [])

    def test_concurrent_initialization_creates_one_account(self) -> None:
        service = self.fixture_service()
        path = service.store.path
        outcomes: list[str] = []

        def worker(key: str) -> None:
            local = SimulationService(SimulationStore(path))
            try:
                local.create_account(key, now=at(2026, 10, 14, 9))
                outcomes.append("created")
            except SimulationError as exc:
                outcomes.append(exc.code)

        threads = [threading.Thread(target=worker, args=(f"k{i}",)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(outcomes), ["active_account_exists"] * 3 + ["created"])
        with service.store.read() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM ledger_events WHERE event_type = 'initial_deposit'").fetchone()[0], 1
            )

    def test_sell_freezes_shares_and_rejects_oversell(self) -> None:
        service = self.fixture_service()
        account_id = self.open_account(service)
        service.record_nav(FIXTURE_CODE, FRESH_DAY, "1.2000", now=FRESH_SEEN)
        self.buy(service, account_id, "buy", "1000.00")
        service.record_nav(FIXTURE_CODE, date(2026, 10, 14), "1.2500", now=at(2026, 10, 15, 20))
        service.settle(account_id, now=at(2026, 10, 16, 18))

        def sell(key: str, shares: str):
            return service.submit_order(account_id, key, side="sell", code=FIXTURE_CODE, shares=shares, now=at(2026, 10, 19, 10))[0]

        self.assertEqual(sell("too-fine", "100.005")["reason_code"], "invalid_quantity")
        self.assertEqual(sell("over", "801.00")["reason_code"], "insufficient_shares")
        self.assertEqual(sell("dust", "799.95")["reason_code"], "min_remaining")
        self.assertEqual(sell("half", "500.00")["status"], "awaiting_nav")
        self.assertEqual(sell("second", "400.00")["reason_code"], "insufficient_shares")
        position = service.positions_view(account_id)[0]
        self.assertEqual((position["frozen_shares"], position["available_shares"]), ("500.00", "300.00"))
        self.assertEqual(sell("rest", "300.00")["status"], "awaiting_nav")
        self.assertEqual(service.verify(), [])

    def test_pause_blocks_new_buys_and_checks_version(self) -> None:
        service = self.fixture_service()
        account_id = self.open_account(service)
        service.record_nav(FIXTURE_CODE, FRESH_DAY, "1.2000", now=FRESH_SEEN)
        version = service.account_view(account_id)["version"]
        paused = service.pause(account_id, "p1", version, now=at(2026, 10, 14, 13))
        self.assertEqual((paused["status"], paused["pause_reason"]), ("paused", "manual"))
        self.assertEqual(service.pause(account_id, "p1", version, now=at(2026, 10, 14, 13))["version"], paused["version"])
        with self.assertRaises(SimulationError) as error:
            service.pause(account_id, "p2", version, now=at(2026, 10, 14, 13))
        self.assertEqual(error.exception.code, "version_conflict")
        order, _ = self.buy(service, account_id, "k", "100.00")
        self.assertEqual(order["reason_code"], "account_paused")
        resumed = service.resume(account_id, "r1", paused["version"], now=at(2026, 10, 14, 14, 1))
        self.assertEqual(resumed["status"], "active")
        self.assertEqual(self.buy(service, account_id, "k2", "100.00", when=at(2026, 10, 14, 14, 2))[0]["status"], "awaiting_nav")

    def test_append_only_tables_reject_updates(self) -> None:
        service = self.fixture_service()
        self.open_account(service)
        with self.assertRaises(sqlite3.IntegrityError):
            with service.store.write() as conn:
                conn.execute("UPDATE ledger_events SET cash_available_delta = '1.00'")
        with self.assertRaises(sqlite3.IntegrityError):
            with service.store.write() as conn:
                conn.execute("DELETE FROM rule_versions")

    def test_nav_correction_pauses_and_blocks_resume(self) -> None:
        service = self.fixture_service()
        account_id = self.open_account(service)
        service.record_nav(FIXTURE_CODE, FRESH_DAY, "1.2000", now=FRESH_SEEN)
        self.buy(service, account_id, "buy", "1000.00")
        service.record_nav(FIXTURE_CODE, date(2026, 10, 14), "1.2500", now=at(2026, 10, 15, 20))
        service.settle(account_id, now=at(2026, 10, 16, 18))
        service.record_nav(FIXTURE_CODE, date(2026, 10, 14), "1.2600", now=at(2026, 10, 17, 9))
        report = service.settle(account_id, now=at(2026, 10, 17, 9, 30))
        self.assertIn("nav_correction", [item["reason"] for item in report.blocked])
        account = service.account_view(account_id)
        self.assertEqual((account["status"], account["pause_reason"]), ("paused", "nav_correction"))
        with self.assertRaises(SimulationError) as error:
            service.resume(account_id, "r", account["version"], now=at(2026, 10, 17, 10))
        self.assertEqual(error.exception.code, "nav_conflict")
        with service.store.read() as conn:
            settled = conn.execute("SELECT nav_value, confirmed_shares FROM orders").fetchone()
        self.assertEqual((settled["nav_value"], settled["confirmed_shares"]), ("1.2500", "800.00"))


class MaintenanceTest(SimulationTestCase):
    def test_backup_restore_keeps_idempotency_and_pauses(self) -> None:
        service = self.fixture_service()
        account_id = self.open_account(service)
        service.record_nav(FIXTURE_CODE, FRESH_DAY, "1.2000", now=FRESH_SEEN)
        self.buy(service, account_id, "k1", "500.00")
        backup = self.root / "backup.db"
        backup_simulation_db(service.store.path, backup)
        with self.assertRaises(FileExistsError):
            backup_simulation_db(service.store.path, backup)
        restored_path = self.root / "restored.db"
        summary = restore_simulation_db(backup, restored_path, None)
        self.assertEqual(summary["accounts"][0]["status"], "paused")
        with self.assertRaises(SimulationConfigError):
            restore_simulation_db(backup, restored_path, None)
        restored = SimulationService(SimulationStore(restored_path))
        self.assertEqual(restored.verify(), [])
        replay, created = restored.submit_order(
            account_id, "k1", side="buy", code=FIXTURE_CODE, amount="500.00", now=at(2026, 10, 14, 14, 30)
        )
        self.assertFalse(created)
        self.assertEqual(restored.account_view(account_id)["balances"]["frozen"], "500.00")
        self.assertEqual(restored.create_account("initial", now=at(2026, 10, 14, 15))[0]["id"], account_id)

    def test_archive_requires_pause_and_no_pending(self) -> None:
        service = self.fixture_service()
        account_id = self.open_account(service)
        service.record_nav(FIXTURE_CODE, FRESH_DAY, "1.2000", now=FRESH_SEEN)
        order, _ = self.buy(service, account_id, "k1", "500.00")
        kwargs = dict(confirm_account_id=account_id, operator="tester", reason="new experiment", now=at(2026, 10, 14, 14, 10))
        with self.assertRaises(SimulationError) as active:
            service.archive_and_create(account_id, **kwargs)
        self.assertEqual(active.exception.code, "account_not_paused")
        version = service.account_view(account_id)["version"]
        service.pause(account_id, "p", version, now=at(2026, 10, 14, 14, 11))
        with self.assertRaises(SimulationError) as pending:
            service.archive_and_create(account_id, **kwargs)
        self.assertEqual(pending.exception.code, "pending_orders")
        service.cancel_order(order["id"], "c", order["version"], now=at(2026, 10, 14, 14, 12))
        with self.assertRaises(SimulationError):
            service.archive_and_create(account_id, **{**kwargs, "confirm_account_id": "wrong"})
        new_account = service.archive_and_create(account_id, **kwargs)
        self.assertNotEqual(new_account["id"], account_id)
        self.assertEqual(new_account["balances"]["available"], "200000.00")
        accounts = {row["id"]: row["status"] for row in service.list_accounts()}
        self.assertEqual(accounts[account_id], "archived")
        self.assertEqual(len(service.list_orders(account_id)["items"]), 1)
        self.assertEqual(service.verify(), [])


class ProductionSourceTest(SimulationTestCase):
    def test_production_settlement_reads_main_db_without_changing_it(self) -> None:
        main = self.root / "fund_analysis.db"
        storage = LocalStorage(main)
        storage.add_holding({"code": "016665", "name": "真实持仓", "amount": 1234.56})
        with contextlib.closing(sqlite3.connect(main)) as conn, conn:
            conn.execute(
                """
                INSERT INTO fund_market_snapshots (
                    code, name, latest_nav, nav_date, daily_return_pct, source, fetched_at,
                    payload_json, warning_json, updated_at
                ) VALUES ('016665', 'x', 1.2345, '2026-10-14', 0.1, 'test', 'now', '{}', '[]', 'now')
                """
            )
        with contextlib.closing(sqlite3.connect(main)) as conn:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        before = sha256(main)
        source = MainDbNavSource(main)
        observation = source.fetch("016665", date(2026, 10, 14), at(2026, 10, 15, 20))
        self.assertIsNotNone(observation)
        assert observation is not None
        self.assertEqual(str(observation.nav), "1.2345")
        self.assertIsNone(source.fetch("016665", date(2026, 10, 13), at(2026, 10, 15, 20)))
        with self.assertRaises(sqlite3.OperationalError):
            with contextlib.closing(source._connect()) as conn:
                conn.execute("DELETE FROM holdings")

        clock = {"now": at(2026, 10, 14, 14)}
        sim = SimulationService(
            SimulationStore(resolve_simulation_path(self.root / "simulation.db", main)),
            clock=lambda: clock["now"],
        )
        sim.import_rule(production_rule())
        account_id = sim.create_account("initial")[0]["id"]
        with sim.store.write() as conn:  # 生产库没有手工净值入口；测试直接写入一条来源快照
            sim._record_nav(
                conn, NavObservation("016665", FRESH_DAY, observation.nav, "test", at(2026, 10, 13, 18, 30))
            )
        order, _ = sim.submit_order(account_id, "buy", side="buy", code="016665", amount="1000.00")
        self.assertEqual(order["status"], "awaiting_nav")
        clock["now"] = at(2026, 10, 16, 18)
        report = sim.settle(account_id, nav_source=source)
        self.assertEqual(report.errors, [])
        detail = sim.order_detail(order["id"])
        self.assertEqual((detail["status"], detail["confirmed_shares"]), ("settled", "810.04"))
        self.assertEqual(sha256(main), before)
        self.assertEqual(storage.get_all_holdings()[0]["amount"], 1234.56)


class CliTest(SimulationTestCase):
    def test_fixture_workflow_and_production_order_refusal(self) -> None:
        sim = self.root / "cli.db"
        main = self.root / "main.db"
        base = ["simulation", "--sim-db", str(sim), "--main-db", str(main)]
        with patch("builtins.print"):
            self.assertEqual(cli_main([*base, "init", "--environment", "fixture"]), 0)
            self.assertEqual(cli_main([*base, "import-rules", "--fixture"]), 0)
            self.assertEqual(cli_main([*base, "verify"]), 0)
        prod = ["simulation", "--sim-db", str(self.root / "prod.db"), "--main-db", str(main)]
        with patch("builtins.print"):
            self.assertEqual(cli_main([*prod, "init"]), 0)
            account_id = SimulationService(SimulationStore(self.root / "prod.db")).list_accounts()[0]["id"]
            code = cli_main([*prod, "order", "--account", account_id, "--key", "k", "--side", "buy", "--code", FIXTURE_CODE, "--amount", "1.00"])
        self.assertEqual(code, 2)
        self.assertFalse(main.exists())


if __name__ == "__main__":
    unittest.main()
