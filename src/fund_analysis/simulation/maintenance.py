"""模拟库在线备份与恢复。恢复只写入新路径，覆盖现有文件需另行人工操作。"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

from .service import SimulationService
from .store import SCHEMA_VERSION, SimulationConfigError, SimulationStore, resolve_simulation_path


def _integrity(conn: sqlite3.Connection) -> None:
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise SimulationConfigError(f"SQLite 完整性检查失败：{result}")


def _copy(source: Path, target: Path) -> None:
    # 目标独占创建，避免误覆盖已有文件；SQLite backup 包含 WAL 中已提交的数据。
    with target.open("xb"):
        pass
    target.chmod(0o600)
    try:
        with contextlib.closing(
            sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
        ) as src, contextlib.closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            _integrity(dst)
    except Exception:
        target.unlink(missing_ok=True)
        raise


def _verify_copy(target: Path) -> list[str]:
    with contextlib.closing(sqlite3.connect(f"{target.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        row = conn.execute("SELECT value FROM sim_meta WHERE key = 'schema_version'").fetchone()
    if row is None or int(row[0]) != SCHEMA_VERSION:
        raise SimulationConfigError("备份不是当前 schema 的模拟库")
    return SimulationService(SimulationStore(target)).verify()


def backup_simulation_db(source: Path, target: Path) -> dict[str, Any]:
    if not source.exists():
        raise SimulationConfigError(f"模拟库不存在：{source}")
    _copy(source, target)
    try:
        problems = _verify_copy(target)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    if problems:
        target.unlink(missing_ok=True)
        raise SimulationConfigError("账本重放核对失败：" + "；".join(problems))
    return {"target": str(target), "verified": True}


def restore_simulation_db(
    backup: Path, target: Path | str, main_db_path: Path | str | None
) -> dict[str, Any]:
    """把备份恢复到一个尚不存在的路径；恢复后所有未归档账户保持暂停。"""
    resolved = resolve_simulation_path(target, main_db_path)
    if resolved.exists():
        raise SimulationConfigError("目标已存在；覆盖现有模拟库需先停服务并人工移走原文件")
    with contextlib.closing(sqlite3.connect(f"{backup.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        _integrity(conn)
    _copy(backup, resolved)
    try:
        problems = _verify_copy(resolved)
        if problems:
            raise SimulationConfigError("账本重放核对失败：" + "；".join(problems))
        store = SimulationStore(resolved)
        with store.write() as conn:
            conn.execute(
                """
                UPDATE accounts SET status = 'paused', pause_reason = 'restored', version = version + 1
                WHERE status = 'active'
                """
            )
        with store.read() as conn:
            last_order = conn.execute("SELECT MAX(submitted_at) FROM orders").fetchone()[0]
            last_valuation = conn.execute("SELECT MAX(observed_at) FROM valuations").fetchone()[0]
            accounts = [dict(row) for row in conn.execute("SELECT id, status, pause_reason FROM accounts")]
    except Exception:
        resolved.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(f"{resolved}{suffix}").unlink(missing_ok=True)
        raise
    return {
        "target": str(resolved),
        "accounts": accounts,
        "last_order_submitted_at": last_order,
        "last_valuation_at": last_valuation,
    }
