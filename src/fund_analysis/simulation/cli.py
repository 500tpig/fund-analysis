"""`fund-analysis simulation ...` 本地管理与验收命令。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..web.defaults import DEFAULT_DB_PATH
from .acceptance import run_acceptance
from .maintenance import backup_simulation_db, restore_simulation_db
from .nav_sources import MainDbNavSource
from .rules import FIXTURE_QDII_C_V1
from .service import SimulationError, SimulationService
from .store import SimulationConfigError, SimulationStore, StorageBusy, resolve_simulation_path
from .strategy import default_fixture_strategy


def _at(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("时间必须带时区，例如 2026-10-14T14:00:00+08:00")
    return parsed


def register(commands: Any) -> None:
    sim = commands.add_parser("simulation", help="独立模拟账本的本地管理与验收。")
    sim.add_argument("--sim-db", default=None, help="模拟库路径；默认读取 FUND_ANALYSIS_SIMULATION_DB_PATH 或 data/simulation.db")
    sim.add_argument("--main-db", default=str(DEFAULT_DB_PATH), help="真实库路径，仅用于防误配和只读净值")
    sub = sim.add_subparsers(dest="sim_command", required=True)

    init = sub.add_parser("init", help="创建模拟库并初始化 200,000 元账户（幂等）。")
    init.add_argument("--environment", choices=["fixture", "production"], default=None)
    init.add_argument("--key", default="initial")

    rules = sub.add_parser("import-rules", help="导入规则版本。")
    group = rules.add_mutually_exclusive_group(required=True)
    group.add_argument("--fixture", action="store_true", help="导入 fixture_qdii_c_v1")
    group.add_argument("--file", type=Path, help="规则 JSON（对象或数组）")

    nav = sub.add_parser("nav", help="fixture 库登记人造官方净值。")
    nav.add_argument("--code", required=True)
    nav.add_argument("--date", required=True, type=date.fromisoformat)
    nav.add_argument("--nav", required=True)
    nav.add_argument("--at", type=_at)

    order = sub.add_parser("order", help="fixture 库验收下单（生产库拒绝）。")
    order.add_argument("--account", required=True)
    order.add_argument("--key", required=True)
    order.add_argument("--side", choices=["buy", "sell"], required=True)
    order.add_argument("--code", required=True)
    order.add_argument("--amount")
    order.add_argument("--shares")
    order.add_argument("--at", type=_at)

    cancel = sub.add_parser("cancel", help="撤销订单。")
    cancel.add_argument("--order", required=True)
    cancel.add_argument("--key", required=True)
    cancel.add_argument("--expected-version", required=True, type=int)
    cancel.add_argument("--at", type=_at)

    fail = sub.add_parser("fail", help="凭明确证据标记业务失败。")
    fail.add_argument("--order", required=True)
    fail.add_argument("--reason", required=True)
    fail.add_argument("--evidence", required=True)
    fail.add_argument("--at", type=_at)

    dividend = sub.add_parser("dividend", help="登记现金分红公告。")
    dividend.add_argument("--account", required=True)
    dividend.add_argument("--key", required=True, help="公告 ID")
    dividend.add_argument("--code", required=True)
    dividend.add_argument("--per-share", required=True)
    dividend.add_argument("--tax", default="0.00")
    dividend.add_argument("--record-date", required=True)
    dividend.add_argument("--ex-date", required=True)
    dividend.add_argument("--pay-date", required=True)
    dividend.add_argument("--evidence", required=True)
    dividend.add_argument("--at", type=_at)

    for name, help_text in (("settle", "推进到期订单、分红并保存观察快照。"), ("valuate", "保存一次观察快照。")):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("--account", required=True)
        cmd.add_argument("--at", type=_at)

    for name in ("pause", "resume"):
        cmd = sub.add_parser(name, help="暂停新增买入。" if name == "pause" else "恢复新增买入（重新校验）。")
        cmd.add_argument("--account", required=True)
        cmd.add_argument("--key", required=True)
        cmd.add_argument("--expected-version", required=True, type=int)

    show = sub.add_parser("show", help="查看账户、持仓与最近订单。")
    show.add_argument("--account", required=True)

    sub.add_parser("verify", help="由账本重放核对余额与份额。")

    backup = sub.add_parser("backup", help="在线一致性备份并核对。")
    backup.add_argument("--target", required=True, type=Path)

    restore = sub.add_parser("restore", help="把备份恢复到一个新路径（恢复后账户暂停）。")
    restore.add_argument("--backup", required=True, type=Path)
    restore.add_argument("--target", required=True, type=Path)

    archive = sub.add_parser("archive", help="归档旧实验并新建 200,000 元账户。")
    archive.add_argument("--account", required=True)
    archive.add_argument("--confirm", help="再次输入旧账户 ID 以确认")
    archive.add_argument("--operator", default="")
    archive.add_argument("--reason", default="")

    acceptance = sub.add_parser("acceptance", help="在新目录中运行 C01–C16 与日期算例。")
    acceptance.add_argument("--output-dir", required=True, type=Path)

    strategy = sub.add_parser("strategy-import", help="导入固定策略版本（不可覆盖，修订需更晚生效日）。")
    strategy_group = strategy.add_mutually_exclusive_group(required=True)
    strategy_group.add_argument("--fixture", action="store_true", help="按已导入的 fixture 规则生成每周三 14:00、每只 500 元的样例策略")
    strategy_group.add_argument("--file", type=Path, help="策略版本 JSON")
    strategy.add_argument("--account", required=True)
    strategy.add_argument("--effective-from", required=True, type=date.fromisoformat)

    cycle = sub.add_parser("run-cycle", help="处理到期周期：窗口内受理，过期补记 missed。")
    cycle.add_argument("--account", default=None, help="默认使用当前未归档账户")
    cycle.add_argument("--at", type=_at)

    tick = sub.add_parser("tick", help="每日调度入口：结算检查与 23:30 日终快照。")
    tick.add_argument("--at", type=_at)

    runs = sub.add_parser("runs", help="查看周期运行记录。")
    runs.add_argument("--account", required=True)
    runs.add_argument("--limit", type=int, default=20)

    run_show = sub.add_parser("run", help="查看单个周期运行详情。")
    run_show.add_argument("--run", required=True)

    metrics = sub.add_parser("metrics", help="查看费用后指标（现金占比、回撤、换手）。")
    metrics.add_argument("--account", required=True)


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _service(args: argparse.Namespace, *, create: bool = False, environment: str | None = None) -> SimulationService:
    path = resolve_simulation_path(args.sim_db, args.main_db)
    if not create and not path.exists():
        raise SimulationConfigError(f"模拟库不存在：{path}；请先运行 simulation init")
    return SimulationService(SimulationStore(path, environment=environment))


def _require_fixture(service: SimulationService, action: str) -> None:
    if service.store.environment != "fixture":
        raise SimulationError("fixture_only", f"{action}仅用于 fixture 模拟库", status=403)


def _run_acceptance(args: argparse.Namespace) -> int:
    checks = run_acceptance(args.output_dir)
    failed = [check for check in checks if not check.ok]
    current = None
    for check in checks:
        if check.case != current:
            current = check.case
            print(f"\n{check.case} {check.title}")
        mark = "✓" if check.ok else "✗"
        detail = check.actual if check.ok else f"预期 {check.expected}，实际 {check.actual}"
        print(f"  {mark} {check.label}：{detail}")
    print(f"\n共 {len(checks)} 项，失败 {len(failed)} 项。样例库位于 {args.output_dir}")
    return 1 if failed else 0


def run(args: argparse.Namespace) -> int:
    try:
        return _dispatch(args)
    except SimulationError as exc:
        print(f"error: [{exc.code}] {exc.message}", file=sys.stderr)
        return 2
    except (SimulationConfigError, StorageBusy, FileExistsError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    command = args.sim_command
    if command == "acceptance":
        return _run_acceptance(args)
    if command == "restore":
        _print(restore_simulation_db(args.backup, args.target, args.main_db))
        return 0
    if command == "init":
        service = _service(args, create=True, environment=args.environment)
        account, created = service.create_account(args.key)
        _print({"created": created, "account": account})
        return 0
    service = _service(args)
    if command == "import-rules":
        records = [FIXTURE_QDII_C_V1] if args.fixture else json.loads(args.file.read_text("utf-8"))
        records = records if isinstance(records, list) else [records]
        _print([dict(zip(("id", "created"), service.import_rule(record))) for record in records])
    elif command == "nav":
        _print({"snapshot_id": service.record_nav(args.code, args.date, args.nav, now=args.at)})
    elif command == "order":
        _require_fixture(service, "命令行下单")
        order, created = service.submit_order(
            args.account, args.key, side=args.side, code=args.code,
            amount=args.amount, shares=args.shares, now=args.at,
        )
        _print({"created": created, "order": order})
    elif command == "cancel":
        _print(service.cancel_order(args.order, args.key, args.expected_version, now=args.at))
    elif command == "fail":
        _print(service.fail_order(args.order, reason=args.reason, evidence=args.evidence, now=args.at))
    elif command == "dividend":
        row, created = service.register_dividend(
            args.account, announcement_key=args.key, code=args.code, per_share=args.per_share,
            tax=args.tax, record_date=args.record_date, ex_date=args.ex_date,
            pay_date=args.pay_date, evidence=args.evidence, now=args.at,
        )
        _print({"created": created, "dividend": row})
    elif command == "settle":
        source = MainDbNavSource(args.main_db) if service.store.environment == "production" else None
        _print(service.settle(args.account, now=args.at, nav_source=source).to_dict())
    elif command == "valuate":
        _print(service.valuate(args.account, now=args.at))
    elif command == "pause":
        _print(service.pause(args.account, args.key, args.expected_version))
    elif command == "resume":
        _print(service.resume(args.account, args.key, args.expected_version))
    elif command == "show":
        _print(
            {
                "account": service.account_view(args.account),
                "positions": service.positions_view(args.account),
                "orders": service.list_orders(args.account, limit=20)["items"],
            }
        )
    elif command == "verify":
        problems = service.verify()
        _print({"ok": not problems, "problems": problems})
        return 1 if problems else 0
    elif command == "backup":
        _print(backup_simulation_db(service.store.path, args.target))
    elif command == "archive":
        preview = service.archive_preview(args.account)
        if args.confirm != args.account:
            _print({"preview": preview, "confirmed": False})
            print("未确认：核对上述信息后，用 --confirm <旧账户 ID> --operator --reason 重新执行。", file=sys.stderr)
            return 1
        _print(
            service.archive_and_create(
                args.account, confirm_account_id=args.confirm, operator=args.operator, reason=args.reason
            )
        )
    elif command == "strategy-import":
        if args.fixture:
            codes = service.verified_codes()
            if not codes:
                raise SimulationError("rule_incomplete", "没有已核验规则，不能生成 fixture 策略")
            record = default_fixture_strategy(args.account, args.effective_from, codes)
        else:
            record = json.loads(args.file.read_text("utf-8"))
            record = {**record, "account_id": args.account}
        version_id, created = service.import_strategy_version(record)
        _print({"id": version_id, "created": created})
    elif command == "run-cycle":
        source = MainDbNavSource(args.main_db) if service.store.environment == "production" else None
        result = service.run_cycle(args.account, now=args.at, nav_source=source)
        _print(result if result is not None else {"skipped": "没有可运行的账户"})
    elif command == "tick":
        source = MainDbNavSource(args.main_db) if service.store.environment == "production" else None
        _print(service.scheduled_tick(now=args.at, nav_source=source))
    elif command == "runs":
        _print(service.list_runs(args.account, limit=args.limit))
    elif command == "run":
        _print(service.run_detail(args.run))
    elif command == "metrics":
        _print(service.account_metrics(args.account))
    return 0
