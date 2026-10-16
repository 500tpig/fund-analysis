from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener

from .csv_loader import read_portfolio_csv
from .models import PortfolioInputError
from .simulation.cli import register as register_simulation_commands
from .simulation.cli import run as run_simulation
from .web.defaults import DEFAULT_DB_PATH


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fund-analysis",
        description="本地基金估值与持仓管理工具。",
    )
    commands = parser.add_subparsers(dest="command")

    web = commands.add_parser("web", help="启动本地基金估值页面。")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    web.add_argument(
        "--simulation-db-path",
        default=None,
        help="模拟库路径；默认读取 FUND_ANALYSIS_SIMULATION_DB_PATH 或 data/simulation.db",
    )

    collect = commands.add_parser("collect", help="通过 localhost 服务采集并对账。")
    collect.add_argument("--port", type=int, default=8765)
    collect.add_argument("--scheduled", action="store_true", help="按交易日和时间槽判断是否采集。")

    import_cmd = commands.add_parser("import", help="导入持仓 CSV。")
    import_cmd.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    import_cmd.add_argument("--portfolio", required=True, help="持仓 CSV 路径。")
    register_simulation_commands(commands)
    return parser


def _run_web(args: argparse.Namespace) -> int:
    try:
        import uvicorn

        from .web.app import create_app
    except ImportError as exc:
        print(f"error: Web dependencies are unavailable: {exc}", file=sys.stderr)
        return 2
    uvicorn.run(
        create_app(
            Path(args.db_path),
            lazy_storage=False,
            simulation_db_path=args.simulation_db_path,
        ),
        host=args.host,
        port=args.port,
    )
    return 0


def _run_import(args: argparse.Namespace) -> int:
    try:
        holdings = read_portfolio_csv(Path(args.portfolio))
        from .web.storage import LocalStorage

        imported = LocalStorage(Path(args.db_path)).replace_holdings(holdings)
    except (PortfolioInputError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"Imported {imported} holdings into {args.db_path}")
    return 0


def _run_collect(args: argparse.Namespace) -> int:
    if not 1 <= args.port <= 65535:
        print("error: port must be between 1 and 65535", file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{args.port}/api/funds/refresh"
    if args.scheduled:
        url += "?scheduled=true"
    try:
        # 不经过 HTTP_PROXY；所有任务进入唯一 Web 进程的刷新锁。
        with build_opener(ProxyHandler({})).open(Request(url, method="POST"), timeout=600) as response:
            result = json.load(response)
    except (URLError, TimeoutError, ValueError) as exc:
        print(f"error: collection failed: {exc}", file=sys.stderr)
        return 1
    print(result["message"])
    for failure in result.get("failures", []) + result.get("estimate_failures", []) + result.get("quote_failures", []):
        print(json.dumps(failure, ensure_ascii=False))
    return 1 if result.get("has_failure") else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "web":
        return _run_web(args)
    if args.command == "import":
        return _run_import(args)
    if args.command == "collect":
        return _run_collect(args)
    if args.command == "simulation":
        return run_simulation(args)
    parser.print_help()
    return 1
