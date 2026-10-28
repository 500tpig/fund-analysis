from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import sqlite3
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..fund_data import FundIntradayEstimate, FundMarketData
from ..fund_metadata import INITIAL_FUND_METADATA, FundMetadata
from ..market_quotes import MarketQuote
from ..models import PortfolioHolding
from .holdings_entry import ENTRY_OPTIONAL_FIELDS, entry_differs, validate_profit_snapshot

CNY_QUANT = Decimal("0.01")


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not number.is_finite():
        return None
    return number


def compute_official_daily_profit_amount(
    holding_amount: Decimal | float | int | str,
    official_return_pct: Decimal | float | int | str,
) -> Decimal:
    amount = _decimal_or_none(holding_amount)
    pct = _decimal_or_none(official_return_pct)
    if amount is None or pct is None:
        raise ValueError("holding_amount and official_return_pct must be finite numbers")
    return (amount * pct / Decimal("100")).quantize(CNY_QUANT, rounding=ROUND_HALF_UP)


def _nav_date_key(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) < 10:
        return None
    candidate = text[:10]
    try:
        date.fromisoformat(candidate)
    except ValueError:
        return None
    return candidate


HOLDING_COLUMNS: dict[str, str] = {
    "code": "TEXT",
    "name": "TEXT",
    "amount": "REAL",
    "shares": "TEXT",
    "weight_pct": "REAL",
    "cost": "REAL",
    "asset_type": "TEXT",
    "asset_class": "TEXT",
    "theme": "TEXT",
    "holding_profit_rate": "REAL",
    "daily_profit": "REAL",
    "cumulative_profit": "REAL",
    "year_profit": "REAL",
    "amount_nav_date": "TEXT",
    "amount_basis_source": "TEXT",
    "imported_holding_profit": "TEXT",
    "profit_snapshot_date": "TEXT",
    "profit_source": "TEXT",
    "daily_profit_date": "TEXT",
}
ADDITIVE_HOLDING_COLUMNS = {**HOLDING_COLUMNS, "created_at": "TEXT", "updated_at": "TEXT"}

DATA_SOURCE_RUN_STATUS_VALUES = ("success", "partial", "failed")
logger = logging.getLogger(__name__)


def _lastrowid(cursor: sqlite3.Cursor) -> int:
    row_id = cursor.lastrowid
    if row_id is None:
        raise RuntimeError("SQLite insert did not return a row id")
    return row_id


class LocalStorage:
    """SQLite boundary for the valuation MVP.

    Initialization is additive only. Existing non-MVP tables are deliberately
    left untouched so an older database can be opened without data loss.
    """

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.closing(self._get_conn()) as conn, conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY)"
            )
            if conn.execute("SELECT version FROM schema_version").fetchone() is None:
                conn.execute("INSERT INTO schema_version (version) VALUES (1)")
            self._create_holdings_table(conn)
            self._ensure_holding_columns(conn)
            self._create_fund_market_snapshots_table(conn)
            self._create_fund_intraday_estimates_table(conn)
            self._create_data_source_runs_table(conn)
            self._create_market_quote_snapshots_table(conn)
            self._create_estimate_daily_accuracy_table(conn)
            self._create_official_daily_profit_table(conn)
            self._create_fund_valuation_profiles_table(conn)
            self.seed_initial_metadata_if_needed(conn)
            self._audit_duplicate_holdings(conn)

    @staticmethod
    def _audit_duplicate_holdings(conn: sqlite3.Connection) -> None:
        duplicates = conn.execute(
            """
            SELECT TRIM(code) AS code, COUNT(*) AS count, GROUP_CONCAT(id) AS ids
            FROM holdings
            WHERE TRIM(code) != ''
            GROUP BY TRIM(code) COLLATE NOCASE
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        if duplicates:
            summary = "; ".join(
                f"{row['code']} ({row['count']} records, ids: {row['ids']})"
                for row in duplicates
            )
            logger.warning("Existing duplicate holding codes detected: %s", summary)

    @staticmethod
    def _create_holdings_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS holdings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                amount REAL,
                shares TEXT,
                weight_pct REAL,
                cost REAL,
                asset_type TEXT,
                asset_class TEXT,
                theme TEXT,
                holding_profit_rate REAL,
                daily_profit REAL,
                cumulative_profit REAL,
                year_profit REAL,
                amount_nav_date TEXT,
                amount_basis_source TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def _ensure_holding_columns(conn: sqlite3.Connection) -> None:
        existing = {
            str(row["name"])
            for row in conn.execute("PRAGMA table_info(holdings)").fetchall()
        }
        for name, column_type in ADDITIVE_HOLDING_COLUMNS.items():
            if name not in existing:
                conn.execute(f'ALTER TABLE holdings ADD COLUMN "{name}" {column_type}')

    @staticmethod
    def _create_fund_market_snapshots_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fund_market_snapshots (
                code TEXT PRIMARY KEY,
                name TEXT,
                latest_nav REAL,
                nav_date TEXT,
                daily_return_pct REAL,
                source TEXT,
                fetched_at TEXT,
                payload_json TEXT NOT NULL,
                warning_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def _create_fund_intraday_estimates_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fund_intraday_estimates (
                code TEXT PRIMARY KEY,
                name TEXT,
                previous_nav REAL,
                previous_nav_date TEXT,
                estimated_nav REAL,
                estimated_return_pct REAL,
                estimated_at TEXT,
                source TEXT NOT NULL,
                source_url TEXT,
                fetched_at TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                warning_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def _create_data_source_runs_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS data_source_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                fetched_at TEXT,
                min_data_date TEXT,
                max_data_date TEXT,
                row_count INTEGER,
                error_message TEXT,
                payload_hash TEXT,
                evidence_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_data_source_runs_source_job
            ON data_source_runs(source_key, job_type, completed_at DESC)
            """
        )

    @staticmethod
    def _create_market_quote_snapshots_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS market_quote_snapshots (
                symbol TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                instrument_type TEXT NOT NULL,
                market TEXT NOT NULL,
                currency TEXT NOT NULL,
                last_price REAL NOT NULL,
                previous_close REAL NOT NULL,
                change_value REAL NOT NULL,
                change_pct REAL NOT NULL,
                trade_date TEXT NOT NULL,
                quoted_at TEXT NOT NULL,
                source TEXT NOT NULL,
                source_url TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                warning_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def _create_estimate_daily_accuracy_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fund_estimate_daily_accuracy (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                code                 TEXT NOT NULL,
                observed_date        TEXT NOT NULL,
                target_nav_date      TEXT,
                estimated_nav        REAL,
                estimated_return_pct REAL,
                estimated_at         TEXT,
                estimate_source      TEXT,
                official_nav         REAL,
                official_return_pct  REAL,
                official_nav_date    TEXT,
                error_pct_points     REAL,
                reconciled_at        TEXT,
                created_at           TEXT NOT NULL,
                updated_at           TEXT NOT NULL,
                UNIQUE(code, observed_date)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_feda_code_date
            ON fund_estimate_daily_accuracy(code, observed_date DESC)
            """
        )

    @staticmethod
    def _create_official_daily_profit_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fund_official_daily_profit (
                code TEXT NOT NULL,
                nav_date TEXT NOT NULL,
                holding_amount REAL NOT NULL,
                official_nav REAL,
                official_return_pct REAL NOT NULL,
                profit_amount REAL NOT NULL,
                source TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (code, nav_date)
            )
            """
        )

    @staticmethod
    def _create_fund_valuation_profiles_table(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS fund_valuation_profiles (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                fund_type TEXT NOT NULL,
                fund_type_label TEXT NOT NULL,
                market TEXT NOT NULL,
                calendar TEXT NOT NULL,
                nav_disclosure_rule TEXT NOT NULL,
                benchmark_name TEXT,
                benchmark_code TEXT,
                source_url TEXT,
                verified_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

    @staticmethod
    def _holding_to_data(holding: PortfolioHolding) -> dict[str, Any]:
        return {
            "code": holding.code,
            "name": holding.name,
            "amount": holding.amount,
            "weight_pct": holding.reported_weight_pct,
            "cost": holding.cost,
            "asset_type": holding.asset_type,
            "asset_class": holding.asset_class,
            "theme": holding.theme,
            "holding_profit_rate": holding.holding_profit_rate,
            "daily_profit": holding.daily_profit,
            "cumulative_profit": holding.cumulative_profit,
            "year_profit": holding.year_profit,
            "amount_nav_date": holding.amount_nav_date,
            "amount_basis_source": "manual" if holding.amount_nav_date else None,
        }

    @staticmethod
    def _normalized_holding(data: Mapping[str, Any]) -> dict[str, Any]:
        return {name: data.get(name) for name in HOLDING_COLUMNS}

    @classmethod
    def _insert_holding(
        cls,
        conn: sqlite3.Connection,
        data: Mapping[str, Any],
        now: str,
    ) -> int:
        row = cls._apply_amount_basis_fields(cls._normalized_holding(data))
        columns = list(HOLDING_COLUMNS)
        quoted = ", ".join(f'"{name}"' for name in columns)
        placeholders = ", ".join(f":{name}" for name in columns)
        cursor = conn.execute(
            f"""
            INSERT INTO holdings (
                {quoted}, created_at, updated_at
            ) VALUES (
                {placeholders}, :created_at, :updated_at
            )
            """,
            {**row, "created_at": now, "updated_at": now},
        )
        return _lastrowid(cursor)

    @staticmethod
    def _amounts_differ(left: Any, right: Any) -> bool:
        left_number = _decimal_or_none(left)
        right_number = _decimal_or_none(right)
        if left_number is None and right_number is None:
            return False
        if left_number is None or right_number is None:
            return True
        return left_number.quantize(CNY_QUANT, rounding=ROUND_HALF_UP) != right_number.quantize(
            CNY_QUANT, rounding=ROUND_HALF_UP
        )

    @classmethod
    def _apply_amount_basis_fields(
        cls,
        row: dict[str, Any],
        *,
        existing: Mapping[str, Any] | None = None,
        incoming: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = dict(incoming or row)
        validate_profit_snapshot(payload)
        # 金额、份额或基金变化后，未重新确认的截图持有收益失效；历史日收益保留日期。
        changed = existing is not None and any(
            key in payload and (
                cls._amounts_differ(existing.get(key), payload.get(key)) if key == "amount"
                else str(existing.get(key) or "") != str(payload.get(key) or "")
            ) for key in ("amount", "shares", "code")
        )
        source_changed = existing is not None and "profit_source" in payload and payload["profit_source"] != existing.get("profit_source")
        if (changed or source_changed) and "imported_holding_profit" not in payload:
            row["imported_holding_profit"] = None
            row["profit_snapshot_date"] = None
            if "holding_profit_rate" not in payload:
                row["holding_profit_rate"] = None
        if "imported_holding_profit" in payload and "holding_profit_rate" not in payload:
            row["holding_profit_rate"] = None
        if source_changed and "daily_profit" not in payload:
            row["daily_profit"] = None
            row["daily_profit_date"] = None
        if "daily_profit" in payload and "daily_profit_date" not in payload:
            row["daily_profit_date"] = None
        date_in_payload = incoming is None or "amount_nav_date" in payload
        raw_date = payload.get("amount_nav_date") if date_in_payload else None
        date_text = str(raw_date).strip()[:10] if raw_date not in (None, "") else None
        if date_text:
            parsed = _nav_date_key(date_text)
            if parsed is None:
                raise ValueError("amount_nav_date must be YYYY-MM-DD")
            row["amount_nav_date"] = parsed
            row["amount_basis_source"] = str(payload.get("amount_basis_source") or "").strip() or "manual"
            return row
        amount_changed = existing is not None and "amount" in payload and cls._amounts_differ(
            existing.get("amount"), payload.get("amount")
        )
        if date_in_payload:
            row["amount_nav_date"] = None
            row["amount_basis_source"] = None
        elif amount_changed:
            row["amount_nav_date"] = None
            row["amount_basis_source"] = None
        elif existing is not None:
            row.pop("amount_nav_date", None)
            row.pop("amount_basis_source", None)
        else:
            row["amount_nav_date"] = None
            row["amount_basis_source"] = None
        return row

    def get_all_holdings(self) -> list[dict[str, Any]]:
        with contextlib.closing(self._get_conn()) as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM holdings ORDER BY id")]

    def get_holding(self, holding_id: int) -> dict[str, Any] | None:
        with contextlib.closing(self._get_conn()) as conn:
            row = conn.execute("SELECT * FROM holdings WHERE id = ?", (holding_id,)).fetchone()
            return dict(row) if row else None

    def add_holding(self, data: Mapping[str, Any]) -> int:
        now = datetime.now().astimezone().isoformat()
        with contextlib.closing(self._get_conn()) as conn, conn:
            self._ensure_holding_code_available(conn, data.get("code"))
            return self._insert_holding(conn, data, now)

    def update_holding(self, holding_id: int, data: Mapping[str, Any]) -> None:
        row = {name: data[name] for name in HOLDING_COLUMNS if name in data}
        if not row:
            raise ValueError("holding update is empty")
        with contextlib.closing(self._get_conn()) as conn, conn:
            existing = conn.execute(
                "SELECT * FROM holdings WHERE id = ?",
                (holding_id,),
            ).fetchone()
            if existing is None:
                raise KeyError(holding_id)
            row = self._apply_amount_basis_fields(row, existing=dict(existing), incoming=data)
            if not row:
                raise ValueError("holding update is empty")
            assignments = ", ".join(f'"{name}" = :{name}' for name in row)
            if "code" in row:
                self._ensure_holding_code_available(conn, row["code"], exclude_id=holding_id)
            conn.execute(
                f"UPDATE holdings SET {assignments}, updated_at = :updated_at WHERE id = :id",
                {**row, "updated_at": datetime.now().astimezone().isoformat(), "id": holding_id},
            )

    def delete_holding(self, holding_id: int) -> None:
        with contextlib.closing(self._get_conn()) as conn, conn:
            cursor = conn.execute("DELETE FROM holdings WHERE id = ?", (holding_id,))
            if cursor.rowcount == 0:
                raise KeyError(holding_id)

    def replace_holdings(self, holdings: list[PortfolioHolding]) -> int:
        seen_codes: set[str] = set()
        for holding in holdings:
            normalized_code = holding.code.strip().casefold()
            if normalized_code in seen_codes:
                raise ValueError(f"基金代码 {holding.code} 在导入文件中重复")
            seen_codes.add(normalized_code)
        now = datetime.now().astimezone().isoformat()
        with contextlib.closing(self._get_conn()) as conn, conn:
            conn.execute("DELETE FROM holdings")
            for holding in holdings:
                self._insert_holding(conn, self._holding_to_data(holding), now)
        return len(holdings)

    def apply_holdings_entry(
        self,
        items: Sequence[Mapping[str, Any]],
        mode: str,
    ) -> dict[str, int]:
        if mode not in {"merge", "replace"}:
            raise ValueError("mode 只接受 merge 或 replace")
        seen_codes: set[str] = set()
        for item in items:
            code = str(item.get("code") or "").strip()
            normalized = code.casefold()
            if normalized in seen_codes:
                raise ValueError(f"基金代码 {code} 在确认列表中重复")
            seen_codes.add(normalized)

        counts = {"add_count": 0, "update_count": 0, "keep_count": 0, "remove_count": 0}
        now = datetime.now().astimezone().isoformat()
        with contextlib.closing(self._get_conn()) as conn, conn:
            existing_rows = [dict(row) for row in conn.execute("SELECT * FROM holdings ORDER BY id")]
            existing_by_code = {
                str(row.get("code") or "").strip().casefold(): row for row in existing_rows
            }
            for item in items:
                normalized = str(item["code"]).strip().casefold()
                existing = existing_by_code.get(normalized)
                if existing is None:
                    self._insert_holding(conn, item, now)
                    counts["add_count"] += 1
                    continue
                if not entry_differs(existing, item):
                    counts["keep_count"] += 1
                    continue
                changes: dict[str, Any] = {
                    "name": item["name"],
                    "amount": item["amount"],
                    "weight_pct": None,
                }
                changes.update(
                    {field: item[field] for field in ENTRY_OPTIONAL_FIELDS if field in item}
                )
                changes = self._apply_amount_basis_fields(
                    changes,
                    existing=existing,
                    incoming=item,
                )
                assignments = ", ".join(f'"{field}" = :{field}' for field in changes)
                conn.execute(
                    f"UPDATE holdings SET {assignments}, updated_at = :updated_at WHERE id = :id",
                    {**changes, "updated_at": now, "id": existing["id"]},
                )
                counts["update_count"] += 1

            if mode == "replace":
                remove_ids = [
                    row["id"]
                    for row in existing_rows
                    if str(row.get("code") or "").strip().casefold() not in seen_codes
                ]
                if remove_ids:
                    placeholders = ", ".join("?" for _ in remove_ids)
                    cursor = conn.execute(
                        f"DELETE FROM holdings WHERE id IN ({placeholders})",
                        remove_ids,
                    )
                    counts["remove_count"] = cursor.rowcount
        return counts

    @staticmethod
    def _ensure_holding_code_available(
        conn: sqlite3.Connection,
        code: Any,
        *,
        exclude_id: int | None = None,
    ) -> None:
        normalized_code = str(code or "").strip()
        if not normalized_code:
            return
        query = "SELECT id FROM holdings WHERE TRIM(code) = ? COLLATE NOCASE"
        parameters: list[Any] = [normalized_code]
        if exclude_id is not None:
            query += " AND id != ?"
            parameters.append(exclude_id)
        if conn.execute(query, parameters).fetchone() is not None:
            raise ValueError(f"基金代码 {normalized_code} 已存在")

    def save_fund_market_snapshot(self, data: FundMarketData) -> None:
        payload = data.to_dict()
        payload.pop("historical_nav", None)
        now = datetime.now().astimezone().isoformat()
        with contextlib.closing(self._get_conn()) as conn, conn:
            conn.execute(
                """
                INSERT INTO fund_market_snapshots (
                    code, name, latest_nav, nav_date, daily_return_pct, source,
                    fetched_at, payload_json, warning_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name, latest_nav = excluded.latest_nav,
                    nav_date = excluded.nav_date,
                    daily_return_pct = excluded.daily_return_pct,
                    source = excluded.source, fetched_at = excluded.fetched_at,
                    payload_json = excluded.payload_json,
                    warning_json = excluded.warning_json,
                    updated_at = excluded.updated_at
                """,
                (
                    data.code,
                    data.name,
                    data.latest_nav,
                    data.nav_date,
                    data.daily_return_pct,
                    data.source,
                    data.fetched_at,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps(data.warnings, ensure_ascii=False),
                    now,
                ),
            )

    def get_fund_market_snapshots(self) -> list[dict[str, Any]]:
        with contextlib.closing(self._get_conn()) as conn:
            return [
                dict(row)
                for row in conn.execute("SELECT * FROM fund_market_snapshots ORDER BY code")
            ]

    def get_fund_market_snapshots_by_code(self) -> dict[str, dict[str, Any]]:
        return {str(row["code"]): row for row in self.get_fund_market_snapshots()}

    def save_fund_intraday_estimate(self, data: FundIntradayEstimate) -> None:
        now = datetime.now().astimezone().isoformat()
        with contextlib.closing(self._get_conn()) as conn, conn:
            conn.execute(
                """
                INSERT INTO fund_intraday_estimates (
                    code, name, previous_nav, previous_nav_date, estimated_nav,
                    estimated_return_pct, estimated_at, source, source_url,
                    fetched_at, status, payload_json, warning_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name, previous_nav = excluded.previous_nav,
                    previous_nav_date = excluded.previous_nav_date,
                    estimated_nav = excluded.estimated_nav,
                    estimated_return_pct = excluded.estimated_return_pct,
                    estimated_at = excluded.estimated_at, source = excluded.source,
                    source_url = excluded.source_url, fetched_at = excluded.fetched_at,
                    status = excluded.status, payload_json = excluded.payload_json,
                    warning_json = excluded.warning_json,
                    updated_at = excluded.updated_at
                """,
                (
                    data.code,
                    data.name,
                    data.previous_nav,
                    data.previous_nav_date,
                    data.estimated_nav,
                    data.estimated_return_pct,
                    data.estimated_at,
                    data.source,
                    data.source_url,
                    data.fetched_at,
                    data.status,
                    json.dumps(data.payload, ensure_ascii=False),
                    json.dumps(data.warnings, ensure_ascii=False),
                    now,
                ),
            )

    def get_fund_intraday_estimates(self) -> list[dict[str, Any]]:
        with contextlib.closing(self._get_conn()) as conn:
            return [
                dict(row)
                for row in conn.execute("SELECT * FROM fund_intraday_estimates ORDER BY code")
            ]

    def get_fund_intraday_estimates_by_code(self) -> dict[str, dict[str, Any]]:
        return {str(row["code"]): row for row in self.get_fund_intraday_estimates()}

    def save_market_quote_snapshot(self, quote: MarketQuote) -> None:
        now = datetime.now().astimezone().isoformat()
        with contextlib.closing(self._get_conn()) as conn, conn:
            conn.execute(
                """
                INSERT INTO market_quote_snapshots (
                    symbol, name, instrument_type, market, currency, last_price,
                    previous_close, change_value, change_pct, trade_date, quoted_at,
                    source, source_url, fetched_at, payload_json, warning_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    name = excluded.name,
                    instrument_type = excluded.instrument_type,
                    market = excluded.market,
                    currency = excluded.currency,
                    last_price = excluded.last_price,
                    previous_close = excluded.previous_close,
                    change_value = excluded.change_value,
                    change_pct = excluded.change_pct,
                    trade_date = excluded.trade_date,
                    quoted_at = excluded.quoted_at,
                    source = excluded.source,
                    source_url = excluded.source_url,
                    fetched_at = excluded.fetched_at,
                    payload_json = excluded.payload_json,
                    warning_json = excluded.warning_json,
                    updated_at = excluded.updated_at
                """,
                (
                    quote.symbol,
                    quote.name,
                    quote.instrument_type,
                    quote.market,
                    quote.currency,
                    quote.last_price,
                    quote.previous_close,
                    quote.change_value,
                    quote.change_pct,
                    quote.trade_date,
                    quote.quoted_at,
                    quote.source,
                    quote.source_url,
                    quote.fetched_at,
                    json.dumps(quote.payload, ensure_ascii=False),
                    json.dumps(quote.warnings, ensure_ascii=False),
                    now,
                ),
            )

    def get_market_quote_snapshots(self) -> list[dict[str, Any]]:
        with contextlib.closing(self._get_conn()) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    "SELECT * FROM market_quote_snapshots ORDER BY symbol"
                )
            ]

    def record_data_source_run(
        self,
        *,
        source_key: str,
        job_type: str,
        status: str,
        started_at: str,
        completed_at: str,
        fetched_at: str | None = None,
        min_data_date: str | None = None,
        max_data_date: str | None = None,
        row_count: int | None = None,
        error_message: str | None = None,
        evidence: Mapping[str, Any] | None = None,
    ) -> int:
        if not source_key.strip() or not job_type.strip():
            raise ValueError("source_key and job_type are required")
        if status not in DATA_SOURCE_RUN_STATUS_VALUES:
            raise ValueError(f"unsupported data-source status: {status}")
        if row_count is not None and row_count < 0:
            raise ValueError("row_count must be non-negative")
        evidence_json = json.dumps(dict(evidence or {}), ensure_ascii=False, sort_keys=True)
        payload_hash = hashlib.sha256(evidence_json.encode("utf-8")).hexdigest()
        with contextlib.closing(self._get_conn()) as conn, conn:
            return _lastrowid(
                conn.execute(
                    """
                    INSERT INTO data_source_runs (
                        source_key, job_type, status, started_at, completed_at,
                        fetched_at, min_data_date, max_data_date, row_count,
                        error_message, payload_hash, evidence_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_key,
                        job_type,
                        status,
                        started_at,
                        completed_at,
                        fetched_at,
                        min_data_date,
                        max_data_date,
                        row_count,
                        error_message,
                        payload_hash,
                        evidence_json,
                        datetime.now().astimezone().isoformat(),
                    ),
                )
            )

    def get_latest_data_source_runs(self) -> list[dict[str, Any]]:
        with contextlib.closing(self._get_conn()) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT run.*
                    FROM data_source_runs AS run
                    JOIN (
                        SELECT source_key, job_type, MAX(id) AS id
                        FROM data_source_runs
                        GROUP BY source_key, job_type
                    ) AS latest ON latest.id = run.id
                    ORDER BY run.source_key, run.job_type
                    """
                )
            ]

    @staticmethod
    def _resolve_target_nav_date(estimate: "FundIntradayEstimate", observed_date: str) -> str | None:
        """境内普通基金：收盘后（15:00–20:00 本地时间）估值对应当日净值；其余情况留空。"""
        estimated_at = estimate.estimated_at
        if not estimated_at:
            return None
        try:
            dt = datetime.fromisoformat(estimated_at)
            local_dt = dt.astimezone()
            hour = local_dt.hour
            if 15 <= hour < 20 and local_dt.strftime("%Y-%m-%d") == observed_date:
                return observed_date
        except (ValueError, TypeError):
            pass
        return None

    def upsert_estimate_daily_sample(
        self,
        estimate: "FundIntradayEstimate",
        observed_date: str,
        *,
        allow_same_day_target: bool = False,
    ) -> None:
        """保存或更新当日估值样本。同一 (code, observed_date) 只保留 estimated_at 更晚的记录。"""
        if estimate.estimated_return_pct is None:
            return
        target_nav_date = (
            self._resolve_target_nav_date(estimate, observed_date)
            if allow_same_day_target
            else None
        )
        now = datetime.now().astimezone().isoformat()
        with contextlib.closing(self._get_conn()) as conn, conn:
            existing = conn.execute(
                """SELECT id, estimated_at, official_nav, official_return_pct,
                          official_nav_date, reconciled_at
                   FROM fund_estimate_daily_accuracy
                   WHERE code = ? AND observed_date = ?""",
                (estimate.code, observed_date),
            ).fetchone()
            if existing is not None:
                # 只有更新的 estimated_at 才覆盖
                existing_at = str(existing["estimated_at"] or "")
                new_at = str(estimate.estimated_at or "")
                if new_at <= existing_at:
                    return
                keeps_reconciliation = bool(
                    target_nav_date
                    and existing["official_nav_date"] == target_nav_date
                    and existing["official_return_pct"] is not None
                )
                official_return_pct = (
                    float(existing["official_return_pct"])
                    if keeps_reconciliation
                    else None
                )
                conn.execute(
                    """
                    UPDATE fund_estimate_daily_accuracy
                    SET target_nav_date = ?, estimated_nav = ?, estimated_return_pct = ?,
                        estimated_at = ?, estimate_source = ?, official_nav = ?,
                        official_return_pct = ?, official_nav_date = ?,
                        error_pct_points = ?, reconciled_at = ?, updated_at = ?
                    WHERE code = ? AND observed_date = ?
                    """,
                    (
                        target_nav_date,
                        estimate.estimated_nav,
                        estimate.estimated_return_pct,
                        estimate.estimated_at,
                        estimate.source,
                        existing["official_nav"] if keeps_reconciliation else None,
                        official_return_pct,
                        existing["official_nav_date"] if keeps_reconciliation else None,
                        estimate.estimated_return_pct - official_return_pct
                        if official_return_pct is not None
                        else None,
                        existing["reconciled_at"] if keeps_reconciliation else None,
                        now,
                        estimate.code,
                        observed_date,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO fund_estimate_daily_accuracy (
                        code, observed_date, target_nav_date,
                        estimated_nav, estimated_return_pct, estimated_at,
                        estimate_source, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        estimate.code,
                        observed_date,
                        target_nav_date,
                        estimate.estimated_nav,
                        estimate.estimated_return_pct,
                        estimate.estimated_at,
                        estimate.source,
                        now,
                        now,
                    ),
                )

    def reconcile_accuracy_samples(
        self,
        code: str,
        official_nav: float,
        official_return_pct: float,
        official_nav_date: str,
    ) -> None:
        """官方净值到达后，对 target_nav_date 匹配的历史样本写入误差。"""
        if not official_nav_date:
            return
        now = datetime.now().astimezone().isoformat()
        with contextlib.closing(self._get_conn()) as conn, conn:
            conn.execute(
                """
                UPDATE fund_estimate_daily_accuracy
                SET official_nav = ?,
                    official_return_pct = ?,
                    official_nav_date = ?,
                    error_pct_points = estimated_return_pct - ?,
                    reconciled_at = ?,
                    updated_at = ?
                WHERE code = ?
                  AND target_nav_date = ?
                  AND reconciled_at IS NULL
                  AND estimated_return_pct IS NOT NULL
                """,
                (
                    official_nav,
                    official_return_pct,
                    official_nav_date,
                    official_return_pct,
                    now,
                    now,
                    code,
                    official_nav_date,
                ),
            )

    def get_accuracy_summary(
        self,
        code: str,
        estimate_source: str,
        window: int = 30,
    ) -> dict[str, Any] | None:
        """返回指定来源最近 window 个已对账样本的误差摘要，并附带待对账数量。"""
        with contextlib.closing(self._get_conn()) as conn:
            rows = conn.execute(
                """
                SELECT error_pct_points
                FROM fund_estimate_daily_accuracy
                WHERE code = ?
                  AND estimate_source = ?
                  AND reconciled_at IS NOT NULL
                  AND error_pct_points IS NOT NULL
                ORDER BY observed_date DESC
                LIMIT ?
                """,
                (code, estimate_source, window),
            ).fetchall()
            pending_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM fund_estimate_daily_accuracy
                WHERE code = ?
                  AND estimate_source = ?
                  AND reconciled_at IS NULL
                  AND estimated_return_pct IS NOT NULL
                """,
                (code, estimate_source),
            ).fetchone()[0]
        sample_count = len(rows)
        if sample_count == 0 and pending_count == 0:
            return None
        result: dict[str, Any] = {
            "source": estimate_source,
            "sample_count": sample_count,
            "pending_count": int(pending_count),
            "window": window,
        }
        if sample_count >= 3:
            errors = [float(row["error_pct_points"]) for row in rows]
            abs_errors = [abs(e) for e in errors]
            result["mae_pct_points"] = round(sum(abs_errors) / sample_count, 4)
            result["bias_pct_points"] = round(sum(errors) / sample_count, 4)
            result["max_abs_error_pct_points"] = round(max(abs_errors), 4)
        return result

    def get_accuracy_summaries_by_code(
        self,
        sources_by_code: Mapping[str, str],
        window: int = 30,
    ) -> dict[str, dict[str, Any]]:
        """按每只基金当前估值来源批量获取误差摘要。"""
        return {
            code: summary
            for code, source in sources_by_code.items()
            if (summary := self.get_accuracy_summary(code, source, window)) is not None
        }

    def record_official_daily_profit(
        self,
        data: FundMarketData,
        holding_amount: Any,
    ) -> bool:
        """Record observed official daily profit for one fund/nav_date.

        First write locks holding_amount. Later refreshes of the same day keep
        that amount and only update official fields / recompute profit_amount.
        Missing required fields skip the write instead of inserting zeros.
        """
        code = str(data.code or "").strip()
        nav_date = _nav_date_key(data.nav_date)
        amount = _decimal_or_none(holding_amount)
        official_return_pct = _decimal_or_none(data.daily_return_pct)
        if not code or nav_date is None or amount is None or official_return_pct is None:
            return False
        official_nav = _decimal_or_none(data.latest_nav)
        source = str(data.source or "").strip()
        now = datetime.now().astimezone().isoformat()
        with contextlib.closing(self._get_conn()) as conn, conn:
            existing = conn.execute(
                """
                SELECT holding_amount, official_nav, official_return_pct, source
                FROM fund_official_daily_profit
                WHERE code = ? AND nav_date = ?
                """,
                (code, nav_date),
            ).fetchone()
            if existing is None:
                profit_amount = compute_official_daily_profit_amount(
                    amount, official_return_pct
                )
                conn.execute(
                    """
                    INSERT INTO fund_official_daily_profit (
                        code, nav_date, holding_amount, official_nav,
                        official_return_pct, profit_amount, source,
                        recorded_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        code,
                        nav_date,
                        float(amount),
                        float(official_nav) if official_nav is not None else None,
                        float(official_return_pct),
                        float(profit_amount),
                        source,
                        now,
                        now,
                    ),
                )
                return True

            locked_amount = _decimal_or_none(existing["holding_amount"])
            if locked_amount is None:
                return False
            existing_nav = _decimal_or_none(existing["official_nav"])
            existing_return = _decimal_or_none(existing["official_return_pct"])
            existing_source = str(existing["source"] or "")
            if (
                existing_nav == official_nav
                and existing_return == official_return_pct
                and existing_source == source
            ):
                return False
            profit_amount = compute_official_daily_profit_amount(
                locked_amount, official_return_pct
            )
            conn.execute(
                """
                UPDATE fund_official_daily_profit
                SET official_nav = ?,
                    official_return_pct = ?,
                    profit_amount = ?,
                    source = ?,
                    updated_at = ?
                WHERE code = ? AND nav_date = ?
                """,
                (
                    float(official_nav) if official_nav is not None else None,
                    float(official_return_pct),
                    float(profit_amount),
                    source,
                    now,
                    code,
                    nav_date,
                ),
            )
            return True

    def get_official_daily_profit_records(self, code: str) -> list[dict[str, Any]]:
        normalized = str(code or "").strip()
        if not normalized:
            return []
        with contextlib.closing(self._get_conn()) as conn:
            return [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT code, nav_date, holding_amount, official_nav,
                           official_return_pct, profit_amount, source,
                           recorded_at, updated_at
                    FROM fund_official_daily_profit
                    WHERE code = ?
                    ORDER BY nav_date ASC
                    """,
                    (normalized,),
                )
            ]

    def get_official_daily_profit_map(self) -> dict[tuple[str, str], dict[str, Any]]:
        """批量读取所有 fund_official_daily_profit 记录，返回以 (code, nav_date) 为键的字典。"""
        with contextlib.closing(self._get_conn()) as conn:
            return {
                (str(row["code"]), str(row["nav_date"])): dict(row)
                for row in conn.execute(
                    """
                    SELECT code, nav_date, holding_amount, official_nav,
                           official_return_pct, profit_amount, source,
                           recorded_at, updated_at
                    FROM fund_official_daily_profit
                    ORDER BY nav_date ASC
                    """
                )
            }

    def seed_initial_metadata_if_needed(
        self,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """为当前持仓的 17 只基金及现金初始化最小元数据记录（若尚未存在）。"""
        def _do_seed(c: sqlite3.Connection) -> None:
            existing = {
                str(row["code"])
                for row in c.execute("SELECT code FROM fund_valuation_profiles").fetchall()
            }
            now = datetime.now().astimezone().isoformat()
            for code, meta in INITIAL_FUND_METADATA.items():
                if code not in existing:
                    c.execute(
                        """
                        INSERT INTO fund_valuation_profiles (
                            code, name, fund_type, fund_type_label, market, calendar,
                            nav_disclosure_rule, benchmark_name, benchmark_code,
                            source_url, verified_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            meta.code,
                            meta.name,
                            meta.fund_type,
                            meta.fund_type_label,
                            meta.market,
                            meta.calendar,
                            meta.nav_disclosure_rule,
                            meta.benchmark_name,
                            meta.benchmark_code,
                            meta.source_url,
                            meta.verified_at,
                            now,
                            now,
                        ),
                    )
                    continue
                c.execute(
                    """
                    UPDATE fund_valuation_profiles
                    SET benchmark_name = ?, benchmark_code = ?, updated_at = ?
                    WHERE code = ?
                    """,
                    (meta.benchmark_name, meta.benchmark_code, now, code),
                )

        if conn is not None:
            _do_seed(conn)
        else:
            with contextlib.closing(self._get_conn()) as new_conn, new_conn:
                _do_seed(new_conn)

    def get_fund_metadata(self, code: str) -> dict[str, Any] | None:
        """获取指定基金的元数据字典。"""
        with contextlib.closing(self._get_conn()) as conn:
            row = conn.execute(
                """
                SELECT code, name, fund_type, fund_type_label, market, calendar,
                       nav_disclosure_rule, benchmark_name, benchmark_code,
                       source_url, verified_at, created_at, updated_at
                FROM fund_valuation_profiles
                WHERE code = ?
                """,
                (code.strip(),),
            ).fetchone()
            return dict(row) if row else None

    def get_all_fund_metadata(self) -> dict[str, dict[str, Any]]:
        """获取所有基金元数据字典，按 code 索引。"""
        with contextlib.closing(self._get_conn()) as conn:
            rows = conn.execute(
                """
                SELECT code, name, fund_type, fund_type_label, market, calendar,
                       nav_disclosure_rule, benchmark_name, benchmark_code,
                       source_url, verified_at, created_at, updated_at
                FROM fund_valuation_profiles
                """
            ).fetchall()
            return {str(row["code"]): dict(row) for row in rows}

    def upsert_fund_metadata(
        self,
        metadata: Mapping[str, Any] | FundMetadata,
    ) -> None:
        """新增或更新单只基金元数据。"""
        data = metadata.to_dict() if isinstance(metadata, FundMetadata) else dict(metadata)
        required_fields = (
            "code",
            "name",
            "fund_type",
            "fund_type_label",
            "market",
            "calendar",
            "nav_disclosure_rule",
        )
        missing_fields = [name for name in required_fields if not str(data.get(name) or "").strip()]
        if missing_fields:
            raise ValueError(f"fund metadata missing required fields: {', '.join(missing_fields)}")
        now = datetime.now().astimezone().isoformat()
        with contextlib.closing(self._get_conn()) as conn, conn:
            conn.execute(
                """
                INSERT INTO fund_valuation_profiles (
                    code, name, fund_type, fund_type_label, market, calendar,
                    nav_disclosure_rule, benchmark_name, benchmark_code,
                    source_url, verified_at, created_at, updated_at
                ) VALUES (
                    :code, :name, :fund_type, :fund_type_label, :market, :calendar,
                    :nav_disclosure_rule, :benchmark_name, :benchmark_code,
                    :source_url, :verified_at, :created_at, :updated_at
                )
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name,
                    fund_type = excluded.fund_type,
                    fund_type_label = excluded.fund_type_label,
                    market = excluded.market,
                    calendar = excluded.calendar,
                    nav_disclosure_rule = excluded.nav_disclosure_rule,
                    benchmark_name = excluded.benchmark_name,
                    benchmark_code = excluded.benchmark_code,
                    source_url = excluded.source_url,
                    verified_at = excluded.verified_at,
                    updated_at = excluded.updated_at
                """,
                {
                    "code": str(data["code"]).strip(),
                    "name": str(data["name"]).strip(),
                    "fund_type": str(data["fund_type"]).strip(),
                    "fund_type_label": str(data["fund_type_label"]).strip(),
                    "market": str(data["market"]).strip(),
                    "calendar": str(data["calendar"]).strip(),
                    "nav_disclosure_rule": str(data["nav_disclosure_rule"]).strip(),
                    "benchmark_name": data.get("benchmark_name"),
                    "benchmark_code": data.get("benchmark_code"),
                    "source_url": data.get("source_url"),
                    "verified_at": data.get("verified_at"),
                    "created_at": str(data.get("created_at") or now),
                    "updated_at": now,
                },
            )
