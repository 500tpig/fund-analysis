"""模拟库连接、schema 与路径边界。

模拟库是独立 SQLite 文件；不 ATTACH 真实库，也不构造会迁移主库的 LocalStorage。
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from .rules import ENVIRONMENTS

SCHEMA_VERSION = 2
SIMULATION_DB_ENV = "FUND_ANALYSIS_SIMULATION_DB_PATH"
DEFAULT_SIMULATION_DB_PATH = Path("data/simulation.db")
BUSY_TIMEOUT_SECONDS = 5.0

IMMUTABLE_TABLES = ("rule_versions", "snapshots", "order_events", "ledger_events", "strategy_versions")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sim_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    experiment_id TEXT NOT NULL UNIQUE,
    initial_cash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'paused', 'risk_paused', 'archived')),
    pause_reason TEXT,
    peak TEXT NOT NULL,
    active_slot INTEGER UNIQUE CHECK (active_slot IS NULL OR active_slot = 1),
    created_at TEXT NOT NULL,
    archived_at TEXT,
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency_keys (
    scope TEXT NOT NULL,
    operation TEXT NOT NULL,
    key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    result_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (scope, operation, key)
);
CREATE TABLE IF NOT EXISTS rule_versions (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL,
    channel TEXT NOT NULL,
    share_class TEXT NOT NULL,
    currency TEXT NOT NULL,
    environment TEXT NOT NULL,
    status TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    effective_to TEXT,
    record_json TEXT NOT NULL,
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    code TEXT,
    nav_date TEXT,
    value TEXT,
    payload_json TEXT NOT NULL,
    source TEXT NOT NULL,
    source_url TEXT,
    published_at TEXT,
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    hash TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_snapshots_nav ON snapshots(kind, code, nav_date);
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    code TEXT NOT NULL,
    requested_amount TEXT,
    accepted_amount TEXT,
    requested_shares TEXT,
    status TEXT NOT NULL CHECK (status IN (
        'rejected', 'accepted', 'awaiting_nav', 'awaiting_confirmation',
        'awaiting_payment', 'settled', 'cancelled', 'failed'
    )),
    reason_code TEXT,
    reason_detail TEXT,
    blocked_reason TEXT,
    version INTEGER NOT NULL,
    rule_id TEXT,
    snapshot_ids_json TEXT NOT NULL,
    run_id TEXT,
    submitted_at TEXT NOT NULL,
    nav_date TEXT,
    nav_value TEXT,
    nav_snapshot_id TEXT,
    nav_published_at TEXT,
    nav_observed_at TEXT,
    cancel_deadline_at TEXT,
    confirmation_due_date TEXT,
    confirmation_due_at TEXT,
    confirmed_at TEXT,
    payment_due_date TEXT,
    payment_due_at TEXT,
    paid_at TEXT,
    fee TEXT,
    net_amount TEXT,
    gross_amount TEXT,
    confirmed_shares TEXT,
    rounding_delta TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (account_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_orders_account ON orders(account_id, submitted_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS lots (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    code TEXT NOT NULL,
    buy_order_id TEXT NOT NULL UNIQUE REFERENCES orders(id),
    confirmed_shares TEXT NOT NULL,
    remaining_shares TEXT NOT NULL,
    frozen_shares TEXT NOT NULL,
    net_cost TEXT NOT NULL,
    remaining_cost TEXT NOT NULL,
    confirmed_date TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sell_allocations (
    order_id TEXT NOT NULL REFERENCES orders(id),
    lot_id TEXT NOT NULL REFERENCES lots(id),
    shares TEXT NOT NULL,
    PRIMARY KEY (order_id, lot_id)
);
CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL REFERENCES orders(id),
    from_status TEXT,
    to_status TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    effective_date TEXT,
    reason TEXT,
    evidence_ids_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    event_key TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    order_id TEXT,
    dividend_id TEXT,
    code TEXT,
    lot_id TEXT,
    cash_available_delta TEXT NOT NULL,
    cash_frozen_delta TEXT NOT NULL,
    receivable_delta TEXT NOT NULL,
    dividend_delta TEXT NOT NULL,
    shares_delta TEXT NOT NULL,
    fee TEXT NOT NULL,
    rounding_delta TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_account ON ledger_events(account_id, id);
CREATE TABLE IF NOT EXISTS dividends (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    code TEXT NOT NULL,
    announcement_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    record_date TEXT NOT NULL,
    ex_date TEXT NOT NULL,
    pay_date TEXT NOT NULL,
    per_share TEXT NOT NULL,
    tax TEXT NOT NULL,
    eligible_shares TEXT,
    gross TEXT,
    net TEXT,
    status TEXT NOT NULL CHECK (status IN ('announced', 'ex_recorded', 'paid')),
    evidence_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (account_id, announcement_key)
);
CREATE TABLE IF NOT EXISTS valuations (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    kind TEXT NOT NULL DEFAULT 'observation' CHECK (kind IN ('observation', 'daily_close')),
    kind_date TEXT,
    observed_at TEXT NOT NULL,
    nav_refs_json TEXT NOT NULL,
    available TEXT NOT NULL,
    frozen TEXT NOT NULL,
    receivable TEXT NOT NULL,
    dividend_receivable TEXT NOT NULL,
    holdings_value TEXT,
    total TEXT,
    quality TEXT NOT NULL CHECK (quality IN ('complete', 'incomplete')),
    missing_json TEXT NOT NULL,
    peak TEXT,
    drawdown TEXT
);
CREATE INDEX IF NOT EXISTS idx_valuations_account ON valuations(account_id, observed_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_valuations_daily_close
    ON valuations(account_id, kind_date) WHERE kind = 'daily_close';
CREATE TABLE IF NOT EXISTS strategy_versions (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    effective_from TEXT NOT NULL,
    timezone TEXT NOT NULL,
    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 1 AND 7),
    time TEXT NOT NULL,
    window_end TEXT NOT NULL,
    amounts_json TEXT NOT NULL,
    rule_set TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (account_id, effective_from)
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL REFERENCES accounts(id),
    cycle_date TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'partial', 'failed', 'missed')),
    reason_code TEXT,
    reason_detail TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    snapshot_ids_json TEXT NOT NULL,
    UNIQUE (account_id, cycle_date)
);
CREATE INDEX IF NOT EXISTS idx_runs_account ON runs(account_id, cycle_date DESC);
CREATE TABLE IF NOT EXISTS run_items (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    code TEXT NOT NULL,
    name TEXT,
    status TEXT NOT NULL CHECK (status IN ('accepted', 'skipped', 'failed')),
    requested_amount TEXT NOT NULL,
    accepted_amount TEXT NOT NULL,
    skipped_amount TEXT NOT NULL,
    reason_code TEXT,
    reason_detail TEXT,
    order_id TEXT,
    rule_id TEXT,
    error TEXT,
    UNIQUE (run_id, code)
);
CREATE TABLE IF NOT EXISTS account_operations (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    account_id TEXT NOT NULL,
    new_account_id TEXT,
    operator TEXT NOT NULL,
    reason TEXT NOT NULL,
    confirmed_at TEXT NOT NULL,
    detail_json TEXT NOT NULL
);
"""


class SimulationConfigError(ValueError):
    """模拟库路径或环境配置错误。"""


class StorageBusy(RuntimeError):
    """写锁超时，可沿用原幂等键重试。"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def resolve_simulation_path(
    simulation_path: Path | str | None, main_db_path: Path | str | None
) -> Path:
    """解析模拟库路径，拒绝与真实库相同的文件、inode 或符号链接别名。"""
    raw = Path(simulation_path or os.environ.get(SIMULATION_DB_ENV) or DEFAULT_SIMULATION_DB_PATH)
    if raw.is_symlink():
        raise SimulationConfigError(f"模拟库路径不能是符号链接：{raw}")
    resolved = raw.expanduser().resolve()
    if main_db_path is not None:
        main = Path(main_db_path).expanduser().resolve()
        is_sidecar = resolved.parent == main.parent and resolved.name.startswith(main.name + "-")
        if resolved == main or is_sidecar:
            raise SimulationConfigError("模拟库路径不能指向真实库或其 WAL/SHM 文件")
        if resolved.exists() and main.exists():
            left, right = resolved.stat(), main.stat()
            if (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino):
                raise SimulationConfigError("模拟库与真实库是同一个文件")
    return resolved


class SimulationStore:
    def __init__(self, path: Path, *, environment: str | None = None):
        """打开或创建模拟库。新库需要显式环境；已有库的环境不可更改。

        v1 模拟库（阶段 6 验收样例）在打开时原地迁移到 v2：为 valuations 增加
        `kind`/`kind_date`，并创建阶段 7 的策略版本与运行表。迁移在同一事务内完成，
        中途失败不会留下半迁移状态。
        """
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            has_meta = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'sim_meta'"
            ).fetchone()
            meta = dict(conn.execute("SELECT key, value FROM sim_meta").fetchall()) if has_meta else {}
            if not meta:
                env = environment or "production"
                if env not in ENVIRONMENTS:
                    raise SimulationConfigError(f"未知模拟环境：{env}")
                conn.executescript(SCHEMA)
                conn.execute("BEGIN IMMEDIATE")
                conn.executemany(
                    "INSERT OR IGNORE INTO sim_meta (key, value) VALUES (?, ?)",
                    [
                        ("schema_version", str(SCHEMA_VERSION)),
                        ("environment", env),
                        ("created_at", utc_text(utc_now())),
                    ],
                )
                conn.execute("COMMIT")
                meta = dict(conn.execute("SELECT key, value FROM sim_meta").fetchall())
            else:
                version = int(meta["schema_version"])
                if version < SCHEMA_VERSION:
                    self._migrate(conn, version)
                conn.executescript(SCHEMA)
                meta = dict(conn.execute("SELECT key, value FROM sim_meta").fetchall())
            for table in IMMUTABLE_TABLES:
                conn.execute(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_no_update BEFORE UPDATE ON {table}
                    BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END
                    """
                )
                conn.execute(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS {table}_no_delete BEFORE DELETE ON {table}
                    BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END
                    """
                )
            if int(meta["schema_version"]) != SCHEMA_VERSION:
                raise SimulationConfigError(
                    f"模拟库 schema 版本 {meta['schema_version']} 与代码 {SCHEMA_VERSION} 不一致"
                )
            if environment is not None and meta["environment"] != environment:
                raise SimulationConfigError(
                    f"模拟库环境为 {meta['environment']}，不能按 {environment} 打开"
                )
            self.environment = meta["environment"]

    def _migrate(self, conn: sqlite3.Connection, version: int) -> None:
        if version != 1:
            raise SimulationConfigError(f"不支持的模拟库 schema 版本：{version}")
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute(
                """
                ALTER TABLE valuations ADD COLUMN kind TEXT NOT NULL DEFAULT 'observation'
                    CHECK (kind IN ('observation', 'daily_close'))
                """
            )
            conn.execute("ALTER TABLE valuations ADD COLUMN kind_date TEXT")
            conn.execute(
                "UPDATE sim_meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),),
            )
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        # 其余新表与索引由打开流程随后执行的 SCHEMA 幂等创建。

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_SECONDS, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextlib.contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            yield conn
        finally:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            conn.close()

    @contextlib.contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """BEGIN IMMEDIATE 事务；任何异常整体回滚，写锁超时转为可重试错误。"""
        conn = self._connect()
        try:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if "locked" in str(exc) or "busy" in str(exc):
                    raise StorageBusy("模拟库正忙，请稍后用同一幂等键重试") from exc
                raise
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
