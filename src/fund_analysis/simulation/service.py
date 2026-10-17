"""模拟账户的受理、结算、分红与估值。

所有现金和份额变化都写入 ``ledger_events``；余额由账本重放得到，
``lots`` 只是可校验的份额投影。网络读取在写事务外完成。
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Callable, Protocol

from .decimals import (
    MONEY_PLACES,
    NAV_MAX_PLACES,
    SHARE_MAX_PLACES,
    ZERO,
    DecimalInputError,
    dec,
    decimal_text,
    money_text,
    parse_decimal,
    places_of,
    quantize_exact,
    round_money,
)
from .nav_sources import NavObservation
from .rules import (
    SHANGHAI,
    CalendarUnknown,
    FundRule,
    RuleValidationError,
    holding_days,
    load_rule,
    local_datetime,
    validate_rule_record,
)
from .strategy import (
    StrategyValidationError,
    StrategyVersion,
    load_strategy_version,
    validate_strategy_record,
)
from .store import SimulationStore, utc_now, utc_text

INITIAL_CASH = Decimal("200000.00")
SINGLE_FUND_LIMIT = Decimal("0.25")
INDEX_GROUP_LIMIT = Decimal("0.40")
DRAWDOWN_LIMIT = Decimal("0.15")
RATIO_QUANTUM = Decimal("0.000001")
# 阶段 7 调度默认值：每日结算检查时刻（北京时间）、23:30 日终快照与 missed 回溯上限。
SETTLE_SLOTS = ((8, 30), (18, 30), (21, 30), (23, 30), (0, 30))
TICK_WINDOW_MINUTES = 20
DAILY_CLOSE_SLOT = (23, 30)
MISSED_BACKFILL_DAYS = 90

FREEZING_STATUSES = ("accepted", "awaiting_nav", "awaiting_confirmation")
PENDING_STATUSES = (*FREEZING_STATUSES, "awaiting_payment")
SETTLING_STATUSES = ("awaiting_nav", "awaiting_confirmation", "awaiting_payment")
ORDER_STATUSES = (
    "rejected",
    "accepted",
    "awaiting_nav",
    "awaiting_confirmation",
    "awaiting_payment",
    "settled",
    "cancelled",
    "failed",
)
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_CODE = re.compile(r"^\d{6}$")


class SimulationError(Exception):
    def __init__(
        self, code: str, message: str, *, status: int = 409, fields: list[str] | None = None
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.fields = fields or []


class NavSource(Protocol):
    name: str

    def fetch(
        self, code: str, nav_date: date | None, observed_at: datetime
    ) -> NavObservation | None: ...


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def request_hash(payload: Mapping[str, Any]) -> str:
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_key(key: object) -> str:
    if not isinstance(key, str) or not _KEY.match(key):
        raise SimulationError(
            "invalid_idempotency_key",
            "幂等键需为 1–128 位字母、数字或 _.:-",
            status=422,
            fields=["Idempotency-Key"],
        )
    return key


def limit_scaled_amount(
    planned: Decimal, remaining: Decimal | None, minimum: Decimal
) -> tuple[Decimal, Decimal]:
    """按已知剩余额度缩减计划金额（到分向下取整）；低于最低额则整笔跳过。"""
    accepted = planned if remaining is None else min(planned, max(remaining, ZERO))
    accepted = accepted.quantize(Decimal("0.01"), rounding="ROUND_DOWN")
    if accepted < minimum:
        accepted = Decimal("0.00")
    return accepted, planned - accepted


def _parse_dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _local_date(value: datetime) -> date:
    return value.astimezone(SHANGHAI).date()


def _ratio_text(value: Decimal) -> str:
    return decimal_text(value.quantize(RATIO_QUANTUM, rounding=ROUND_HALF_UP))


def _input_decimal(value: object, name: str, places: int, *, allow_zero: bool = False) -> Decimal:
    try:
        return parse_decimal(value, name, max_places=places, allow_zero=allow_zero)
    except DecimalInputError as exc:
        raise SimulationError("invalid_decimal", str(exc), status=422, fields=[name]) from exc


def _input_date(value: object, name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise SimulationError("invalid_date", f"{name} 必须是 YYYY-MM-DD", status=422, fields=[name]) from exc


@dataclass
class Balances:
    available: Decimal = ZERO
    frozen: Decimal = ZERO
    receivable: Decimal = ZERO
    dividend: Decimal = ZERO

    def to_dict(self) -> dict[str, str]:
        return {
            "available": money_text(self.available),
            "frozen": money_text(self.frozen),
            "receivable": money_text(self.receivable),
            "dividend_receivable": money_text(self.dividend),
        }


@dataclass
class Valuation:
    observed_at: datetime
    balances: Balances
    holdings_value: Decimal | None
    total: Decimal | None
    quality: str
    missing: list[dict[str, Any]]
    target_missing: list[dict[str, Any]]
    nav_refs: list[dict[str, Any]]
    values_by_code: dict[str, Decimal]
    snapshot_ids: dict[str, str]


@dataclass
class Decision:
    side: str
    code: str
    amount: Decimal | None
    shares: Decimal | None
    accepted: bool = False
    reason_code: str | None = None
    reason_detail: str | None = None
    rule: FundRule | None = None
    rule_id: str | None = None
    nav_date: date | None = None
    cancel_deadline_at: datetime | None = None
    confirmation_due_date: date | None = None
    payment_due_date: date | None = None
    snapshot_ids: list[str] = field(default_factory=list)
    allocations: list[tuple[str, Decimal]] = field(default_factory=list)

    def reject(self, code: str, detail: str) -> "Decision":
        self.accepted = False
        self.reason_code = code
        self.reason_detail = detail
        return self


@dataclass
class SettleReport:
    nav_recorded: int = 0
    transitions: list[dict[str, Any]] = field(default_factory=list)
    waiting: list[dict[str, Any]] = field(default_factory=list)
    blocked: list[dict[str, Any]] = field(default_factory=list)
    dividends: list[dict[str, Any]] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    valuation: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "nav_recorded": self.nav_recorded,
            "transitions": self.transitions,
            "waiting": self.waiting,
            "blocked": self.blocked,
            "dividends": self.dividends,
            "errors": self.errors,
            "valuation": self.valuation,
        }


MISSING_MESSAGES = {
    "rule_incomplete": "没有可用的已核验规则",
    "rule_conflict": "规则版本冲突",
    "calendar_unknown": "交易日历未覆盖",
    "nav_missing": "缺少已应披露的官方净值",
    "nav_conflict": "同一净值日存在不同数值",
}


def _in_slot(now: datetime, slot: tuple[int, int]) -> bool:
    """now 是否处于北京时间 slot 时刻的触发窗口内（窗口不跨整点小时）。"""
    local = now.astimezone(SHANGHAI)
    minutes = local.hour * 60 + local.minute
    hour, minute = slot
    start = hour * 60 + minute
    return start <= minutes < start + TICK_WINDOW_MINUTES


def _run_status(statuses: list[str], reasons: list[str | None]) -> str:
    """由运行项汇总周期状态。

    全部技术失败为 `failed`，部分技术失败为 `partial`；全部因窗口过期未执行
    （从未进入窗口尝试）为 `missed`；其余（含全部业务跳过）为 `completed`。
    """
    if not statuses:
        return "failed"
    if all(status == "failed" for status in statuses):
        return "failed"
    if any(status == "failed" for status in statuses):
        return "partial"
    if all(
        status == "skipped" and reason == "window_expired"
        for status, reason in zip(statuses, reasons)
    ):
        return "missed"
    return "completed"


class SimulationService:
    def __init__(self, store: SimulationStore, *, clock: Callable[[], datetime] = utc_now):
        self.store = store
        self._clock = clock

    # ---- 时间 -----------------------------------------------------------------

    def _now(self, now: datetime | None) -> datetime:
        """生产库只用服务端时钟；fixture 库可显式指定时间，未指定时沿用最近一次样例时间。"""
        if now is not None:
            if self.store.environment != "fixture":
                raise SimulationError(
                    "time_override_forbidden", "生产模拟库不接受指定时间", status=403
                )
            if now.tzinfo is None:
                raise SimulationError("invalid_time", "时间必须带时区", status=422)
            return now
        if self.store.environment == "fixture":
            with self.store.read() as conn:
                row = conn.execute(
                    "SELECT value FROM sim_meta WHERE key = 'fixture_now'"
                ).fetchone()
            if row is not None:
                return _parse_dt(row["value"])
        return self._clock()

    def _write(self, now: datetime) -> Any:
        store = self.store

        class _Tx:
            def __enter__(self_inner) -> sqlite3.Connection:
                self_inner.cm = store.write()
                conn = self_inner.cm.__enter__()
                if store.environment == "fixture":
                    conn.execute(
                        """
                        INSERT INTO sim_meta (key, value) VALUES ('fixture_now', ?)
                        ON CONFLICT(key) DO UPDATE SET value = MAX(value, excluded.value)
                        """,
                        (utc_text(now),),
                    )
                return conn

            def __exit__(self_inner, *exc: Any) -> Any:
                return self_inner.cm.__exit__(*exc)

        return _Tx()

    # ---- 规则 -----------------------------------------------------------------

    def import_rule(self, record: Mapping[str, Any]) -> tuple[str, bool]:
        try:
            canonical = validate_rule_record(record)
        except RuleValidationError as exc:
            raise SimulationError("rule_invalid", str(exc), status=422) from exc
        if canonical["environment"] != self.store.environment:
            raise SimulationError(
                "rule_environment_mismatch",
                f"{self.store.environment} 模拟库不能导入 {canonical['environment']} 规则",
                status=422,
            )
        text = json.dumps(canonical, sort_keys=True, ensure_ascii=False)
        with self.store.write() as conn:
            existing = conn.execute(
                "SELECT record_json FROM rule_versions WHERE id = ?", (canonical["id"],)
            ).fetchone()
            if existing is not None:
                if existing["record_json"] != text:
                    raise SimulationError(
                        "rule_version_immutable", "规则版本已存在且内容不同；请使用新版本 ID"
                    )
                return canonical["id"], False
            self._check_rule_interval(conn, canonical)
            conn.execute(
                """
                INSERT INTO rule_versions (
                    id, code, channel, share_class, currency, environment, status,
                    effective_from, effective_to, record_json, imported_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    canonical["id"],
                    canonical["code"],
                    canonical["channel"],
                    canonical["share_class"],
                    canonical["currency"],
                    canonical["environment"],
                    canonical["status"],
                    canonical["effective_from"],
                    canonical.get("effective_to"),
                    text,
                    utc_text(utc_now()),
                ),
            )
        return canonical["id"], True

    def _check_rule_interval(
        self, conn: sqlite3.Connection, canonical: Mapping[str, Any]
    ) -> None:
        """拒绝同代码的歧义版本：渠道或份额类别不一致、生效日相同，或与已声明的结束日矛盾。

        规则只按基金代码选取，因此同一代码的所有版本必须共用渠道与份额类别。
        版本不可改写，修订只能新增更晚的 `effective_from`：选取时按生效日取最新一条，
        因此 `effective_to` 为空表示「生效至被后续版本取代」，不算与后续版本重叠。
        反之，已显式声明结束日的版本不能被新版本从中间穿入，否则那条声明会被静默作废。
        """
        others = conn.execute(
            """
            SELECT id, channel, share_class, effective_from, effective_to FROM rule_versions
            WHERE code = ? AND environment = ?
            """,
            (canonical["code"], canonical["environment"]),
        ).fetchall()
        start = date.fromisoformat(canonical["effective_from"])
        end = date.fromisoformat(canonical["effective_to"]) if canonical.get("effective_to") else None
        for row in others:
            other_start = date.fromisoformat(row["effective_from"])
            other_end = date.fromisoformat(row["effective_to"]) if row["effective_to"] else None
            if (row["channel"], row["share_class"]) != (canonical["channel"], canonical["share_class"]):
                raise SimulationError(
                    "rule_identity_conflict",
                    f"{canonical['code']} 已有 {row['channel']}/{row['share_class']} 版本 {row['id']}；"
                    "同一代码的规则必须使用相同渠道与份额类别",
                )
            if start == other_start:
                raise SimulationError(
                    "rule_interval_conflict",
                    f"{canonical['code']} 在 {row['effective_from']} 已有版本 {row['id']}；"
                    "修订请使用更晚的 effective_from",
                )
            if other_start < start:
                if other_end is not None and other_end > start:
                    raise SimulationError(
                        "rule_interval_conflict",
                        f"版本 {row['id']} 声明有效至 {row['effective_to']}，"
                        f"新版本 {canonical['id']} 不能从 {canonical['effective_from']} 提前生效",
                    )
            elif end is not None and end > other_start:
                raise SimulationError(
                    "rule_interval_conflict",
                    f"新版本 {canonical['id']} 声明有效至 {canonical['effective_to']}，"
                    f"不能晚于已有版本 {row['id']} 的生效日 {row['effective_from']}",
                )

    def _rule_for(
        self, conn: sqlite3.Connection, code: str, day: date
    ) -> tuple[FundRule | None, str | None, str | None]:
        """取该基金当日生效的规则；有效区间左闭右开，`effective_to` 当日不再生效。"""
        rows = conn.execute(
            """
            SELECT id, status, channel, share_class, effective_from, record_json FROM rule_versions
            WHERE code = ? AND environment = ? AND effective_from <= ?
              AND (effective_to IS NULL OR effective_to > ?)
            ORDER BY effective_from DESC, id
            """,
            (code, self.store.environment, day.isoformat(), day.isoformat()),
        ).fetchall()
        if not rows:
            return None, None, "rule_incomplete"
        top = rows[0]
        # 订单只带基金代码，因此同一代码只能有一个渠道与份额类别、且同一生效日只能有一个版本；
        # 导入时已拒绝这两种情况，这里防的是绕过导入直接写入的历史数据。
        if len({(row["channel"], row["share_class"]) for row in rows}) > 1 or sum(
            row["effective_from"] == top["effective_from"] for row in rows
        ) > 1:
            return None, top["id"], "rule_conflict"
        if top["status"] == "conflict":
            return None, top["id"], "rule_conflict"
        if top["status"] != "verified":
            return None, top["id"], "rule_incomplete"
        return load_rule(json.loads(top["record_json"])), top["id"], None

    def _rule_by_id(self, conn: sqlite3.Connection, rule_id: str) -> FundRule:
        row = conn.execute(
            "SELECT record_json FROM rule_versions WHERE id = ?", (rule_id,)
        ).fetchone()
        if row is None:
            raise SimulationError("rule_incomplete", f"规则 {rule_id} 不存在")
        return load_rule(json.loads(row["record_json"]))

    def _rule_names(self, conn: sqlite3.Connection) -> dict[str, str]:
        names: dict[str, str] = {}
        for row in conn.execute(
            "SELECT code, record_json FROM rule_versions ORDER BY effective_from"
        ):
            names[row["code"]] = json.loads(row["record_json"]).get("name") or row["code"]
        return names

    # ---- 净值快照 ---------------------------------------------------------------

    def record_nav(
        self,
        code: str,
        nav_date: date,
        nav: str,
        *,
        now: datetime | None = None,
        source: str = "fixture_manual",
    ) -> str:
        """fixture 库手工登记官方净值；生产库只能从只读来源结算时取得。"""
        if self.store.environment != "fixture":
            raise SimulationError("fixture_only", "生产模拟库不接受手工净值", status=403)
        nav_value = _input_decimal(nav, "nav", NAV_MAX_PLACES)
        observed = self._now(now)
        with self._write(observed) as conn:
            return self._record_nav(
                conn, NavObservation(code, nav_date, nav_value, source, observed)
            )

    def _record_nav(self, conn: sqlite3.Connection, obs: NavObservation) -> str:
        if obs.nav <= 0 or places_of(obs.nav) > NAV_MAX_PLACES:
            raise SimulationError("nav_invalid", f"{obs.code} 净值无效：{obs.nav}", status=422)
        payload = {
            "code": obs.code,
            "nav_date": obs.nav_date.isoformat(),
            "nav": decimal_text(obs.nav),
        }
        digest = request_hash({"kind": "nav", "source": obs.source, **payload})
        existing = conn.execute("SELECT id FROM snapshots WHERE hash = ?", (digest,)).fetchone()
        if existing is not None:
            return str(existing["id"])
        snapshot_id = new_id("snap")
        observed = utc_text(obs.observed_at)
        conn.execute(
            """
            INSERT INTO snapshots (
                id, kind, code, nav_date, value, payload_json, source, source_url,
                published_at, observed_at, available_at, hash
            ) VALUES (?, 'nav', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                obs.code,
                payload["nav_date"],
                payload["nav"],
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                obs.source,
                obs.source_url,
                utc_text(obs.published_at) if obs.published_at else None,
                observed,
                # 只能证明首次获取时已经可得；不把发布时间倒推成决策可用时间。
                observed,
                digest,
            ),
        )
        return snapshot_id

    @staticmethod
    def _nav_rows(
        conn: sqlite3.Connection, code: str, nav_date: str, as_of: datetime
    ) -> list[sqlite3.Row]:
        return conn.execute(
            """
            SELECT id, value, observed_at, published_at FROM snapshots
            WHERE kind = 'nav' AND code = ? AND nav_date = ? AND available_at <= ?
            ORDER BY available_at, id
            """,
            (code, nav_date, utc_text(as_of)),
        ).fetchall()

    @staticmethod
    def _nav_status(rows: list[sqlite3.Row]) -> str:
        if not rows:
            return "missing"
        if len({Decimal(row["value"]) for row in rows}) > 1:
            return "conflict"
        return "ok"

    def _latest_nav(
        self, conn: sqlite3.Connection, code: str, as_of: datetime
    ) -> tuple[str, list[sqlite3.Row]] | None:
        row = conn.execute(
            """
            SELECT MAX(nav_date) AS nav_date FROM snapshots
            WHERE kind = 'nav' AND code = ? AND available_at <= ?
            """,
            (code, utc_text(as_of)),
        ).fetchone()
        if row is None or row["nav_date"] is None:
            return None
        return row["nav_date"], self._nav_rows(conn, code, row["nav_date"], as_of)

    # ---- 账本 -------------------------------------------------------------------

    @staticmethod
    def ledger_balances(
        conn: sqlite3.Connection, account_id: str, *, through: date | None = None
    ) -> Balances:
        query = (
            "SELECT cash_available_delta, cash_frozen_delta, receivable_delta, dividend_delta "
            "FROM ledger_events WHERE account_id = ?"
        )
        params: list[Any] = [account_id]
        if through is not None:
            query += " AND effective_date <= ?"
            params.append(through.isoformat())
        balances = Balances()
        for row in conn.execute(query, params):
            balances.available += Decimal(row["cash_available_delta"])
            balances.frozen += Decimal(row["cash_frozen_delta"])
            balances.receivable += Decimal(row["receivable_delta"])
            balances.dividend += Decimal(row["dividend_delta"])
        return balances

    @staticmethod
    def ledger_shares(
        conn: sqlite3.Connection, account_id: str, code: str, *, through: date
    ) -> Decimal:
        total = ZERO
        for row in conn.execute(
            """
            SELECT shares_delta FROM ledger_events
            WHERE account_id = ? AND code = ? AND effective_date <= ?
            """,
            (account_id, code, through.isoformat()),
        ):
            total += Decimal(row["shares_delta"])
        return total

    @staticmethod
    def _ledger(
        conn: sqlite3.Connection,
        account_id: str,
        event_key: str,
        event_type: str,
        now: datetime,
        effective_date: date | str,
        *,
        order_id: str | None = None,
        dividend_id: str | None = None,
        code: str | None = None,
        lot_id: str | None = None,
        available: Decimal = ZERO,
        frozen: Decimal = ZERO,
        receivable: Decimal = ZERO,
        dividend: Decimal = ZERO,
        shares: Decimal = ZERO,
        fee: Decimal = ZERO,
        rounding: Decimal = ZERO,
    ) -> None:
        conn.execute(
            """
            INSERT INTO ledger_events (
                account_id, event_key, event_type, order_id, dividend_id, code, lot_id,
                cash_available_delta, cash_frozen_delta, receivable_delta, dividend_delta,
                shares_delta, fee, rounding_delta, effective_date, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account_id,
                event_key,
                event_type,
                order_id,
                dividend_id,
                code,
                lot_id,
                money_text(available),
                money_text(frozen),
                money_text(receivable),
                money_text(dividend),
                decimal_text(shares),
                money_text(fee),
                decimal_text(rounding),
                str(effective_date),
                utc_text(now),
            ),
        )

    @staticmethod
    def _holdings(conn: sqlite3.Connection, account_id: str) -> dict[str, dict[str, Decimal]]:
        result: dict[str, dict[str, Decimal]] = {}
        for row in conn.execute(
            "SELECT code, remaining_shares, frozen_shares, remaining_cost FROM lots WHERE account_id = ?",
            (account_id,),
        ):
            remaining = Decimal(row["remaining_shares"])
            if remaining == 0:
                continue
            item = result.setdefault(
                row["code"], {"shares": ZERO, "frozen": ZERO, "cost": ZERO, "lots": ZERO}
            )
            item["shares"] += remaining
            item["frozen"] += Decimal(row["frozen_shares"])
            item["cost"] += Decimal(row["remaining_cost"])
            item["lots"] += 1
        return result

    # ---- 估值 -------------------------------------------------------------------

    def _code_issues(
        self, conn: sqlite3.Connection, code: str, now: datetime
    ) -> tuple[list[dict[str, Any]], Decimal | None, str | None, str | None]:
        issues: list[dict[str, Any]] = []
        rule, _, reason = self._rule_for(conn, code, _local_date(now))
        if reason:
            issues.append({"code": code, "reason": reason})
        expected: date | None = None
        if rule is not None:
            try:
                expected = rule.expected_latest_nav_date(now)
            except CalendarUnknown as exc:
                issues.append({"code": code, "reason": "calendar_unknown", "detail": str(exc)})
        latest = self._latest_nav(conn, code, now)
        if latest is None:
            issues.append({"code": code, "reason": "nav_missing"})
            return issues, None, None, None
        nav_date, rows = latest
        if self._nav_status(rows) == "conflict":
            issues.append({"code": code, "reason": "nav_conflict", "nav_date": nav_date})
            return issues, None, nav_date, None
        if expected is not None and date.fromisoformat(nav_date) < expected:
            issues.append(
                {
                    "code": code,
                    "reason": "nav_missing",
                    "nav_date": nav_date,
                    "expected_nav_date": expected.isoformat(),
                }
            )
        return issues, Decimal(rows[0]["value"]), nav_date, str(rows[0]["id"])

    def _compute_valuation(
        self,
        conn: sqlite3.Connection,
        account_id: str,
        now: datetime,
        extra_codes: Iterable[str] = (),
    ) -> Valuation:
        balances = self.ledger_balances(conn, account_id)
        holdings = self._holdings(conn, account_id)
        extras = set(extra_codes)
        missing: list[dict[str, Any]] = []
        target_missing: list[dict[str, Any]] = []
        nav_refs: list[dict[str, Any]] = []
        values: dict[str, Decimal] = {}
        snapshot_ids: dict[str, str] = {}
        holdings_value = ZERO
        priced = True
        for code in sorted(set(holdings) | extras):
            issues, nav, nav_date, snapshot_id = self._code_issues(conn, code, now)
            if snapshot_id:
                snapshot_ids[code] = snapshot_id
            if code in holdings:
                missing.extend(issues)
                if nav is None:
                    priced = False
                    continue
                shares = holdings[code]["shares"]
                # 每只基金的份额总价值取整到分后再汇总，不按批次先取整。
                value = round_money(shares * nav)
                values[code] = value
                holdings_value += value
                nav_refs.append(
                    {
                        "code": code,
                        "nav_date": nav_date,
                        "nav": decimal_text(nav),
                        "snapshot_id": snapshot_id,
                        "shares": decimal_text(shares),
                        "value": money_text(value),
                    }
                )
            else:
                target_missing.extend(issues)
        cash = balances.available + balances.frozen + balances.receivable + balances.dividend
        total = cash + holdings_value if priced else None
        return Valuation(
            observed_at=now,
            balances=balances,
            holdings_value=holdings_value if priced else None,
            total=total,
            quality="complete" if priced and not missing else "incomplete",
            missing=missing,
            target_missing=target_missing,
            nav_refs=nav_refs,
            values_by_code=values,
            snapshot_ids=snapshot_ids,
        )

    def _record_valuation(
        self,
        conn: sqlite3.Connection,
        account: sqlite3.Row,
        valuation: Valuation,
        *,
        kind: str = "observation",
        kind_date: str | None = None,
    ) -> dict[str, Any]:
        peak = Decimal(account["peak"])
        drawdown: Decimal | None = None
        if valuation.quality == "complete" and valuation.total is not None:
            peak = max(peak, valuation.total)
            drawdown = Decimal(1) - valuation.total / peak if peak > 0 else ZERO
        valuation_id = new_id("val")
        conn.execute(
            """
            INSERT INTO valuations (
                id, account_id, kind, kind_date, observed_at, nav_refs_json, available, frozen,
                receivable, dividend_receivable, holdings_value, total, quality, missing_json,
                peak, drawdown
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                valuation_id,
                account["id"],
                kind,
                kind_date,
                utc_text(valuation.observed_at),
                json.dumps(valuation.nav_refs, ensure_ascii=False),
                money_text(valuation.balances.available),
                money_text(valuation.balances.frozen),
                money_text(valuation.balances.receivable),
                money_text(valuation.balances.dividend),
                money_text(valuation.holdings_value) if valuation.holdings_value is not None else None,
                money_text(valuation.total) if valuation.total is not None else None,
                valuation.quality,
                json.dumps(valuation.missing, ensure_ascii=False),
                money_text(peak) if drawdown is not None else None,
                _ratio_text(drawdown) if drawdown is not None else None,
            ),
        )
        if drawdown is not None:
            conn.execute(
                "UPDATE accounts SET peak = ? WHERE id = ?", (money_text(peak), account["id"])
            )
            if (
                valuation.total is not None
                and valuation.total <= peak * (Decimal(1) - DRAWDOWN_LIMIT)
                and account["status"] in ("active", "paused")
            ):
                self._set_status(conn, account["id"], "risk_paused", "drawdown")
        return {
            "id": valuation_id,
            "quality": valuation.quality,
            "total": money_text(valuation.total) if valuation.total is not None else None,
            "peak": money_text(peak),
            "drawdown": _ratio_text(drawdown) if drawdown is not None else None,
            "missing": valuation.missing,
        }

    def valuate(self, account_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """生成并保存一次观察快照；完整快照才更新历史高点。"""
        now = self._now(now)
        with self._write(now) as conn:
            account = self._account(conn, account_id)
            valuation = self._compute_valuation(conn, account_id, now)
            return self._record_valuation(conn, account, valuation)

    # ---- 账户 -------------------------------------------------------------------

    @staticmethod
    def _account(conn: sqlite3.Connection, account_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if row is None:
            raise SimulationError("account_not_found", "模拟账户不存在", status=404)
        return row

    @staticmethod
    def _set_status(
        conn: sqlite3.Connection, account_id: str, status: str, reason: str | None
    ) -> None:
        conn.execute(
            "UPDATE accounts SET status = ?, pause_reason = ?, version = version + 1 WHERE id = ?",
            (status, reason, account_id),
        )

    @staticmethod
    def _idem(
        conn: sqlite3.Connection, scope: str, operation: str, key: str, digest: str
    ) -> str | None:
        row = conn.execute(
            "SELECT request_hash, result_id FROM idempotency_keys WHERE scope = ? AND operation = ? AND key = ?",
            (scope, operation, key),
        ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != digest:
            raise SimulationError("idempotency_conflict", "同一幂等键对应了不同请求")
        return str(row["result_id"])

    @staticmethod
    def _save_idem(
        conn: sqlite3.Connection,
        scope: str,
        operation: str,
        key: str,
        digest: str,
        result_id: str,
        now: datetime,
    ) -> None:
        conn.execute(
            """
            INSERT INTO idempotency_keys (scope, operation, key, request_hash, result_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (scope, operation, key, digest, result_id, utc_text(now)),
        )

    def _insert_account(self, conn: sqlite3.Connection, now: datetime) -> str:
        account_id = new_id("acct")
        conn.execute(
            """
            INSERT INTO accounts (
                id, experiment_id, initial_cash, status, pause_reason, peak, active_slot,
                created_at, archived_at, version
            ) VALUES (?, ?, ?, 'active', NULL, ?, 1, ?, NULL, 1)
            """,
            (
                account_id,
                new_id("exp"),
                money_text(INITIAL_CASH),
                money_text(INITIAL_CASH),
                utc_text(now),
            ),
        )
        self._ledger(
            conn,
            account_id,
            f"{account_id}:initial_deposit",
            "initial_deposit",
            now,
            _local_date(now),
            available=INITIAL_CASH,
        )
        return account_id

    def create_account(
        self, idempotency_key: str, *, now: datetime | None = None
    ) -> tuple[dict[str, Any], bool]:
        validate_key(idempotency_key)
        now = self._now(now)
        digest = request_hash({"operation": "create_account"})
        with self._write(now) as conn:
            existing = self._idem(conn, "", "create_account", idempotency_key, digest)
            if existing is not None:
                return self._account_view(conn, existing, now), False
            active = conn.execute("SELECT id FROM accounts WHERE active_slot = 1").fetchone()
            if active is not None:
                raise SimulationError("active_account_exists", "已有未归档的模拟账户")
            account_id = self._insert_account(conn, now)
            self._save_idem(conn, "", "create_account", idempotency_key, digest, account_id, now)
            return self._account_view(conn, account_id, now), True

    def pause(
        self, account_id: str, idempotency_key: str, expected_version: object, *, now: datetime | None = None
    ) -> dict[str, Any]:
        validate_key(idempotency_key)
        version = self._expected_version(expected_version)
        now = self._now(now)
        digest = request_hash({"operation": "pause", "expected_version": version})
        with self._write(now) as conn:
            account = self._account(conn, account_id)
            if self._idem(conn, account_id, "pause", idempotency_key, digest) is not None:
                return self._account_view(conn, account_id, now)
            if account["version"] != version:
                raise SimulationError("version_conflict", "账户状态已变更")
            if account["status"] == "archived":
                raise SimulationError("account_archived", "账户已归档")
            if account["status"] == "active":
                self._set_status(conn, account_id, "paused", "manual")
            self._save_idem(conn, account_id, "pause", idempotency_key, digest, account_id, now)
            return self._account_view(conn, account_id, now)

    def resume(
        self, account_id: str, idempotency_key: str, expected_version: object, *, now: datetime | None = None
    ) -> dict[str, Any]:
        validate_key(idempotency_key)
        version = self._expected_version(expected_version)
        now = self._now(now)
        digest = request_hash({"operation": "resume", "expected_version": version})
        with self._write(now) as conn:
            account = self._account(conn, account_id)
            if self._idem(conn, account_id, "resume", idempotency_key, digest) is not None:
                return self._account_view(conn, account_id, now)
            if account["version"] != version:
                raise SimulationError("version_conflict", "账户状态已变更")
            if account["status"] not in ("paused", "risk_paused"):
                raise SimulationError("invalid_state", "账户未处于暂停状态")
            corrections = self._nav_corrections(conn, account_id, now)
            if corrections:
                raise SimulationError(
                    "nav_conflict", f"已成交订单使用的净值存在更正：{corrections[0]['code']}"
                )
            valuation = self._compute_valuation(conn, account_id, now)
            if valuation.quality != "complete" or valuation.total is None:
                first = valuation.missing[0] if valuation.missing else {"reason": "nav_missing"}
                raise SimulationError(
                    first["reason"],
                    f"数据不完整，不能恢复：{first.get('code', '')} {MISSING_MESSAGES.get(first['reason'], '')}",
                )
            peak = max(Decimal(account["peak"]), valuation.total)
            if valuation.total <= peak * (Decimal(1) - DRAWDOWN_LIMIT):
                raise SimulationError("drawdown_locked", "回撤仍达到 15%，不能恢复新增买入")
            self._record_valuation(conn, account, valuation)
            self._set_status(conn, account_id, "active", None)
            self._save_idem(conn, account_id, "resume", idempotency_key, digest, account_id, now)
            return self._account_view(conn, account_id, now)

    @staticmethod
    def _expected_version(value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise SimulationError(
                "invalid_version", "expected_version 必须是正整数", status=422, fields=["expected_version"]
            )
        return value

    def archive_preview(self, account_id: str) -> dict[str, Any]:
        with self.store.read() as conn:
            account = self._account(conn, account_id)
            pending = conn.execute(
                f"SELECT COUNT(*) FROM orders WHERE account_id = ? AND status IN ({_marks(PENDING_STATUSES)})",
                (account_id, *PENDING_STATUSES),
            ).fetchone()[0]
            open_dividends = conn.execute(
                "SELECT COUNT(*) FROM dividends WHERE account_id = ? AND status != 'paid'",
                (account_id,),
            ).fetchone()[0]
            balances = self.ledger_balances(conn, account_id)
        return {
            "account_id": account_id,
            "status": account["status"],
            "pending_orders": pending,
            "open_dividends": open_dividends,
            "balances": balances.to_dict(),
            "new_initial_cash": money_text(INITIAL_CASH),
        }

    def archive_and_create(
        self,
        account_id: str,
        *,
        confirm_account_id: str,
        operator: str,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """重置 = 归档旧实验并新建账户；不删除、不覆盖旧历史。"""
        if confirm_account_id != account_id:
            raise SimulationError("confirmation_mismatch", "确认的账户 ID 不一致", status=422)
        if not operator.strip() or not reason.strip():
            raise SimulationError("invalid_request", "需要操作者和原因", status=422)
        now = self._now(now)
        with self._write(now) as conn:
            account = self._account(conn, account_id)
            if account["status"] == "archived":
                raise SimulationError("account_archived", "账户已归档")
            if account["status"] == "active":
                raise SimulationError("account_not_paused", "请先暂停新增买入，再归档")
            preview_pending = conn.execute(
                f"SELECT COUNT(*) FROM orders WHERE account_id = ? AND status IN ({_marks(PENDING_STATUSES)})",
                (account_id, *PENDING_STATUSES),
            ).fetchone()[0]
            if preview_pending:
                raise SimulationError("pending_orders", f"仍有 {preview_pending} 笔待处理订单")
            if conn.execute(
                "SELECT COUNT(*) FROM dividends WHERE account_id = ? AND status != 'paid'",
                (account_id,),
            ).fetchone()[0]:
                raise SimulationError("pending_dividends", "仍有未发放的分红")
            balances = self.ledger_balances(conn, account_id)
            if balances.frozen or balances.receivable or balances.dividend:
                raise SimulationError("pending_balances", "仍有冻结资金或应收款")
            conn.execute(
                """
                UPDATE accounts SET status = 'archived', active_slot = NULL, archived_at = ?,
                    pause_reason = NULL, version = version + 1 WHERE id = ?
                """,
                (utc_text(now), account_id),
            )
            new_account_id = self._insert_account(conn, now)
            conn.execute(
                """
                INSERT INTO account_operations (
                    id, kind, account_id, new_account_id, operator, reason, confirmed_at, detail_json
                ) VALUES (?, 'archive_and_create', ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id("op"),
                    account_id,
                    new_account_id,
                    operator.strip(),
                    reason.strip(),
                    utc_text(now),
                    json.dumps({"archived_balances": balances.to_dict()}, ensure_ascii=False),
                ),
            )
            return self._account_view(conn, new_account_id, now)

    # ---- 固定策略与周期运行（阶段 7） -------------------------------------------

    def import_strategy_version(self, record: Mapping[str, Any], *, now: datetime | None = None) -> tuple[str, bool]:
        """导入不可变策略版本；同一账户同一生效日只允许一个版本，修订必须更晚生效。"""
        try:
            canonical = validate_strategy_record(record)
        except StrategyValidationError as exc:
            raise SimulationError("strategy_invalid", str(exc), status=422) from exc
        now = self._now(now)
        digest = request_hash({key: value for key, value in canonical.items() if key != "id"})
        with self._write(now) as conn:
            account = self._account(conn, canonical["account_id"])
            if account["status"] == "archived":
                raise SimulationError("account_archived", "账户已归档")
            existing = conn.execute(
                "SELECT * FROM strategy_versions WHERE id = ?", (canonical["id"],)
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != digest:
                    raise SimulationError(
                        "strategy_version_immutable",
                        "策略版本已存在且内容不同；修订请使用更晚的 effective_from",
                    )
                return canonical["id"], False
            latest = conn.execute(
                "SELECT effective_from FROM strategy_versions WHERE account_id = ? ORDER BY effective_from DESC LIMIT 1",
                (canonical["account_id"],),
            ).fetchone()
            if latest is not None and canonical["effective_from"] <= latest["effective_from"]:
                raise SimulationError(
                    "strategy_interval_conflict",
                    f"已有生效至 {latest['effective_from']} 的版本；修订必须使用更晚的 effective_from",
                )
            conn.execute(
                """
                INSERT INTO strategy_versions (
                    id, account_id, effective_from, timezone, weekday, time, window_end,
                    amounts_json, rule_set, request_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    canonical["id"],
                    canonical["account_id"],
                    canonical["effective_from"],
                    canonical["timezone"],
                    canonical["weekday"],
                    canonical["time"],
                    canonical["window_end"],
                    json.dumps(canonical["amounts"], ensure_ascii=False),
                    canonical["rule_set"],
                    digest,
                    utc_text(now),
                ),
            )
        return canonical["id"], True

    def strategy_version_for(self, account_id: str, day: date) -> dict[str, Any] | None:
        """该账户在指定日期生效的策略版本；没有则返回 None。"""
        with self.store.read() as conn:
            row = conn.execute(
                """
                SELECT * FROM strategy_versions WHERE account_id = ? AND effective_from <= ?
                ORDER BY effective_from DESC LIMIT 1
                """,
                (account_id, day.isoformat()),
            ).fetchone()
            return self._strategy_view(row) if row is not None else None

    @staticmethod
    def _strategy_view(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "account_id": row["account_id"],
            "effective_from": row["effective_from"],
            "timezone": row["timezone"],
            "weekday": row["weekday"],
            "time": row["time"],
            "window_end": row["window_end"],
            "amounts": json.loads(row["amounts_json"]),
            "rule_set": row["rule_set"],
            "created_at": row["created_at"],
        }

    def active_account_id(self) -> str | None:
        with self.store.read() as conn:
            row = conn.execute("SELECT id FROM accounts WHERE active_slot = 1").fetchone()
        return str(row["id"]) if row is not None else None

    def verified_codes(self) -> list[str]:
        """当前环境中存在 verified 规则的基金代码（按代码升序）。"""
        with self.store.read() as conn:
            rows = conn.execute(
                "SELECT DISTINCT code FROM rule_versions WHERE environment = ? AND status = 'verified' ORDER BY code",
                (self.store.environment,),
            ).fetchall()
        return [str(row["code"]) for row in rows]

    def _due_cycles(
        self, conn: sqlite3.Connection, account: sqlite3.Row, now: datetime
    ) -> list[tuple[date, StrategyVersion]]:
        """截至 now 应已开始的周期：按当时生效版本的计划星期与时刻推导。

        周期必须晚于账户创建与版本导入时刻，避免用后来才存在的版本补记历史周期。
        """
        versions = [
            load_strategy_version(row)
            for row in conn.execute(
                "SELECT * FROM strategy_versions WHERE account_id = ? ORDER BY effective_from",
                (account["id"],),
            )
        ]
        if not versions:
            return []
        created_at = _parse_dt(account["created_at"])
        local_today = now.astimezone(SHANGHAI).date()
        start = max(created_at.astimezone(SHANGHAI).date(), local_today - timedelta(days=MISSED_BACKFILL_DAYS))
        due: list[tuple[date, StrategyVersion]] = []
        day = start
        while day <= local_today:
            version = None
            for candidate in versions:
                if candidate.effective_from <= day:
                    version = candidate
                else:
                    break
            if version is not None and day.isoweekday() == version.weekday:
                cycle_start = local_datetime(day, version.time)
                if cycle_start <= now and cycle_start >= _parse_dt(version.created_at):
                    due.append((day, version))
            day += timedelta(days=1)
        return due

    def run_cycle(
        self,
        account_id: str | None = None,
        *,
        now: datetime | None = None,
        nav_source: NavSource | None = None,
    ) -> dict[str, Any] | None:
        """处理到期周期：窗口内受理订单，过期补记 missed；重复调用不重复下单。

        没有账户或账户已归档时返回 None；无到期周期时返回含空列表的结果。
        """
        now = self._now(now)
        with self.store.read() as conn:
            if account_id is None:
                row = conn.execute("SELECT id FROM accounts WHERE active_slot = 1").fetchone()
                account_id = str(row["id"]) if row is not None else None
            if account_id is None:
                return None
            account = self._account(conn, account_id)
            if account["status"] == "archived":
                return None
            due = self._due_cycles(conn, account, now)
            wanted = {row["code"] for row in conn.execute("SELECT DISTINCT code FROM lots WHERE account_id = ?", (account_id,))}
            for _, version in due:
                wanted |= set(version.amounts)
        observations: list[NavObservation] = []
        fetch_errors: list[dict[str, Any]] = []
        if nav_source is not None and due:
            for code in sorted(wanted):
                try:
                    obs = nav_source.fetch(code, None, now)
                except Exception as exc:  # noqa: BLE001 - 来源失败逐项留档，不生成默认数据
                    fetch_errors.append({"code": code, "error": str(exc)})
                    continue
                if obs is not None:
                    observations.append(obs)
        with self._write(now) as conn:
            account = self._account(conn, account_id)
            due = self._due_cycles(conn, account, now)
            for obs in observations:
                self._record_nav(conn, obs)
            runs = [
                self._process_cycle(conn, account, cycle_date, version, now)
                for cycle_date, version in due
            ]
        return {
            "account_id": account_id,
            "fetched_navs": len(observations),
            "fetch_errors": fetch_errors,
            "runs": [run for run in runs if run is not None],
        }

    def _process_cycle(
        self,
        conn: sqlite3.Connection,
        account: sqlite3.Row,
        cycle_date: date,
        version: StrategyVersion,
        now: datetime,
    ) -> dict[str, Any] | None:
        account_id = account["id"]
        existing = conn.execute(
            "SELECT * FROM runs WHERE account_id = ? AND cycle_date = ?",
            (account_id, cycle_date.isoformat()),
        ).fetchone()
        if existing is not None:
            if existing["status"] in ("completed", "missed"):
                return self._run_view(conn, existing)
            # 周期开始后不因新版本改写处理口径：一律使用运行时记录的版本。
            version = self._strategy_for_run(conn, existing)
        window_end = local_datetime(cycle_date, version.window_end)
        if existing is not None:
            if now < window_end:
                run_id = existing["id"]
            else:
                self._expire_pending_items(conn, existing, version, now)
                return self._run_view(conn, self._run(conn, run_id=existing["id"]))
        else:
            run_id = new_id("run")
            missed = now >= window_end
            conn.execute(
                """
                INSERT INTO runs (
                    id, account_id, cycle_date, strategy_version, status, reason_code,
                    reason_detail, started_at, finished_at, snapshot_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]')
                """,
                (
                    run_id,
                    account_id,
                    cycle_date.isoformat(),
                    version.id,
                    "running",
                    None,
                    None,
                    utc_text(now),
                    None,
                ),
            )
            if missed:
                self._expire_pending_items(conn, self._run(conn, run_id=run_id), version, now)
                return self._run_view(conn, self._run(conn, run_id=run_id))
        existing_items = {
            row["code"]: row
            for row in conn.execute("SELECT * FROM run_items WHERE run_id = ?", (run_id,))
        }
        snapshot_ids: set[str] = set()
        for code in sorted(version.amounts):
            item = existing_items.get(code)
            if item is not None and item["status"] != "failed":
                if item["order_id"]:
                    snapshot_ids |= self._order_snapshot_ids(conn, item["order_id"])
                continue
            outcome = self._process_item(conn, account, version, cycle_date, code, now, run_id)
            if outcome["order_id"]:
                snapshot_ids |= self._order_snapshot_ids(conn, outcome["order_id"])
            values = {
                "name": outcome["name"],
                "status": outcome["status"],
                "requested_amount": outcome["requested_amount"],
                "accepted_amount": outcome["accepted_amount"],
                "skipped_amount": outcome["skipped_amount"],
                "reason_code": outcome["reason_code"],
                "reason_detail": outcome["reason_detail"],
                "order_id": outcome["order_id"],
                "rule_id": outcome["rule_id"],
                "error": outcome["error"],
            }
            if item is None:
                conn.execute(
                    f"INSERT INTO run_items (id, run_id, code, {', '.join(values)}) VALUES (?, ?, ?, {_marks(values)})",
                    (new_id("item"), run_id, code, *values.values()),
                )
            else:
                assignments = ", ".join(f"{key} = ?" for key in values)
                conn.execute(
                    f"UPDATE run_items SET {assignments} WHERE id = ?",
                    (*values.values(), item["id"]),
                )
        rows = conn.execute(
            "SELECT status, reason_code FROM run_items WHERE run_id = ?", (run_id,)
        ).fetchall()
        status = _run_status([row["status"] for row in rows], [row["reason_code"] for row in rows])
        conn.execute(
            "UPDATE runs SET status = ?, finished_at = ?, snapshot_ids_json = ? WHERE id = ?",
            (status, utc_text(now), json.dumps(sorted(snapshot_ids)), run_id),
        )
        return self._run_view(conn, self._run(conn, run_id=run_id))

    @staticmethod
    def _strategy_for_run(conn: sqlite3.Connection, run: sqlite3.Row) -> StrategyVersion:
        row = conn.execute(
            "SELECT * FROM strategy_versions WHERE id = ?", (run["strategy_version"],)
        ).fetchone()
        if row is None:
            raise SimulationError("strategy_invalid", f"策略版本 {run['strategy_version']} 不存在")
        return load_strategy_version(row)

    def _expire_pending_items(
        self,
        conn: sqlite3.Connection,
        run: sqlite3.Row,
        version: StrategyVersion,
        now: datetime,
    ) -> None:
        """窗口已过：未落定的运行项记为过期跳过，不补建订单。

        已落定项（含技术失败）保留原状；一轮周期的事务整体提交或整体回滚，
        因此这里只会补齐从未落定的基金。
        """
        existing = {
            row["code"]
            for row in conn.execute("SELECT code FROM run_items WHERE run_id = ?", (run["id"],))
        }
        for code in sorted(version.amounts):
            if code in existing:
                continue
            amount = money_text(version.amounts[code])
            conn.execute(
                """
                INSERT INTO run_items (
                    id, run_id, code, name, status, requested_amount, accepted_amount,
                    skipped_amount, reason_code, reason_detail, order_id, rule_id, error
                ) VALUES (?, ?, ?, NULL, 'skipped', ?, '0.00', ?, 'window_expired',
                          '周期未在执行窗口内执行，不补单', NULL, NULL, NULL)
                """,
                (new_id("item"), run["id"], code, amount, amount),
            )
        rows = conn.execute(
            "SELECT status, reason_code FROM run_items WHERE run_id = ?", (run["id"],)
        ).fetchall()
        status = _run_status([row["status"] for row in rows], [row["reason_code"] for row in rows])
        conn.execute(
            "UPDATE runs SET status = ?, reason_code = ?, reason_detail = ?, finished_at = ? WHERE id = ?",
            (
                status,
                "window_expired" if status == "missed" else None,
                "周期未在执行窗口内执行" if status == "missed" else None,
                utc_text(now),
                run["id"],
            ),
        )

    def _process_item(
        self,
        conn: sqlite3.Connection,
        account: sqlite3.Row,
        version: StrategyVersion,
        cycle_date: date,
        code: str,
        now: datetime,
        run_id: str,
    ) -> dict[str, Any]:
        """处理单个基金：前置检查、限额缩减、受理；业务拒绝与数据缺失记为跳过。"""
        planned = version.amounts[code]
        requested = money_text(planned)
        outcome: dict[str, Any] = {
            "name": None, "status": "skipped", "requested_amount": requested,
            "accepted_amount": "0.00", "skipped_amount": requested,
            "reason_code": None, "reason_detail": None, "order_id": None,
            "rule_id": None, "error": None,
        }
        conn.execute("SAVEPOINT strategy_item")
        try:
            decision = self._gates(conn, account, Decision("buy", code, planned, None), now)
            outcome["rule_id"] = decision.rule_id
            if decision.rule is not None:
                outcome["name"] = decision.rule.name
            if decision.reason_code is not None:
                outcome.update(
                    reason_code=decision.reason_code, reason_detail=decision.reason_detail
                )
            else:
                rule = decision.rule
                assert rule is not None and decision.nav_date is not None
                holdings = self._holdings(conn, account["id"])
                pending = self._open_buys(conn, account["id"])
                minimum = rule.min_add if code in holdings or code in pending else rule.min_buy
                remaining: Decimal | None = None
                if rule.max_daily_buy is not None:
                    reserved = self._reserved_daily(conn, account["id"], code, decision.nav_date)
                    remaining = max(rule.max_daily_buy - reserved, ZERO)
                accepted = planned if remaining is None else min(planned, remaining).quantize(
                    Decimal("0.01"), rounding="ROUND_DOWN"
                )
                if accepted < minimum:
                    outcome.update(
                        reason_code="daily_limit" if remaining is not None else "below_minimum",
                        reason_detail=(
                            f"当日剩余额度 {money_text(remaining)} 元，低于最低申购额 {money_text(minimum)} 元"
                            if remaining is not None
                            else f"低于最低申购额 {money_text(minimum)} 元"
                        ),
                    )
                else:
                    digest = request_hash(
                        {
                            "operation": "strategy_cycle",
                            "cycle_date": cycle_date.isoformat(),
                            "code": code,
                            "amount": money_text(accepted),
                        }
                    )
                    row, _ = self._accept_order(
                        conn, account["id"], f"strat-{cycle_date.isoformat()}-{code}", digest,
                        side="buy", code=code, amount=accepted, shares=None, now=now, run_id=run_id,
                    )
                    if row["status"] == "rejected":
                        outcome.update(
                            reason_code=row["reason_code"],
                            reason_detail=row["reason_detail"],
                            order_id=row["id"],
                        )
                    else:
                        outcome.update(
                            status="accepted",
                            accepted_amount=money_text(accepted),
                            skipped_amount=money_text(planned - accepted),
                            reason_code="daily_limit" if accepted < planned else None,
                            reason_detail=(
                                f"当日剩余额度不足，按 {money_text(accepted)} 元受理"
                                if accepted < planned
                                else None
                            ),
                            order_id=row["id"],
                        )
            conn.execute("RELEASE strategy_item")
        except Exception as exc:  # noqa: BLE001 - 逐项技术失败不拖垮整轮
            conn.execute("ROLLBACK TO strategy_item")
            conn.execute("RELEASE strategy_item")
            outcome.update(
                status="failed", accepted_amount="0.00", skipped_amount=requested,
                reason_code="technical_failure", reason_detail=str(exc), error=str(exc),
            )
        return outcome

    @staticmethod
    def _order_snapshot_ids(conn: sqlite3.Connection, order_id: str) -> set[str]:
        row = conn.execute(
            "SELECT snapshot_ids_json FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        return set(json.loads(row["snapshot_ids_json"])) if row is not None else set()

    @staticmethod
    def _run(conn: sqlite3.Connection, *, run_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise SimulationError("run_not_found", "运行记录不存在", status=404)
        return row

    def _run_view(self, conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        counts = {"accepted": 0, "skipped": 0, "failed": 0}
        orders = 0
        for item in conn.execute(
            "SELECT status, order_id FROM run_items WHERE run_id = ?", (row["id"],)
        ):
            counts[item["status"]] += 1
            if item["order_id"]:
                orders += 1
        return {
            "id": row["id"],
            "account_id": row["account_id"],
            "cycle_date": row["cycle_date"],
            "status": row["status"],
            "strategy_version": row["strategy_version"],
            "reason_code": row["reason_code"],
            "reason_detail": row["reason_detail"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "accepted_count": counts["accepted"],
            "skipped_count": counts["skipped"],
            "failed_count": counts["failed"],
            "order_count": orders,
        }

    def list_runs(
        self,
        account_id: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise SimulationError("invalid_limit", "limit 需在 1–100 之间", status=422, fields=["limit"])
        query = "SELECT * FROM runs WHERE account_id = ?"
        params: list[Any] = [account_id]
        if cursor:
            try:
                cycle_date, run_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
            except (ValueError, UnicodeDecodeError) as exc:
                raise SimulationError("invalid_cursor", "cursor 无效", status=422, fields=["cursor"]) from exc
            query += " AND (cycle_date < ? OR (cycle_date = ? AND id < ?))"
            params.extend([cycle_date, cycle_date, run_id])
        query += " ORDER BY cycle_date DESC, id DESC LIMIT ?"
        params.append(limit + 1)
        with self.store.read() as conn:
            self._account(conn, account_id)
            rows = conn.execute(query, params).fetchall()
            items = [self._run_view(conn, row) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = base64.urlsafe_b64encode(
                f"{last['cycle_date']}|{last['id']}".encode()
            ).decode()
        return {"items": items, "next_cursor": next_cursor}

    def run_detail(self, run_id: str) -> dict[str, Any]:
        with self.store.read() as conn:
            row = self._run(conn, run_id=run_id)
            view = self._run_view(conn, row)
            version_row = conn.execute(
                "SELECT * FROM strategy_versions WHERE id = ?", (row["strategy_version"],)
            ).fetchone()
            view["evidence_cutoff"] = row["started_at"]
            view["snapshot_ids"] = json.loads(row["snapshot_ids_json"])
            view["strategy"] = self._strategy_view(version_row) if version_row is not None else None
            view["items"] = [
                {
                    "id": item["id"],
                    "code": item["code"],
                    "name": item["name"],
                    "status": item["status"],
                    "requested_amount": item["requested_amount"],
                    "accepted_amount": item["accepted_amount"],
                    "skipped_amount": item["skipped_amount"],
                    "reason_code": item["reason_code"],
                    "reason_detail": item["reason_detail"],
                    "order_id": item["order_id"],
                    "rule_id": item["rule_id"],
                    "error": item["error"],
                }
                for item in conn.execute(
                    "SELECT * FROM run_items WHERE run_id = ? ORDER BY code", (run_id,)
                )
            ]
            return view

    # ---- 运行指标（阶段 7） -------------------------------------------------------

    def account_metrics(self, account_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """费用后指标：现金占比、最大回撤、换手与费用；口径见专项方案第 12 节。"""
        now = self._now(now)
        with self.store.read() as conn:
            self._account(conn, account_id)
            valuation = self._compute_valuation(conn, account_id, now)
            rows = conn.execute(
                "SELECT observed_at, quality, drawdown, total FROM valuations WHERE account_id = ? ORDER BY observed_at",
                (account_id,),
            ).fetchall()
            complete = [row for row in rows if row["quality"] == "complete" and row["drawdown"] is not None]
            incomplete_count = len(rows) - len(complete)
            max_drawdown = max((Decimal(row["drawdown"]) for row in complete), default=None)
            total_fees = ZERO
            for row in conn.execute("SELECT fee FROM ledger_events WHERE account_id = ?", (account_id,)):
                total_fees += Decimal(row["fee"])
            buy_net = ZERO
            sell_gross = ZERO
            for row in conn.execute(
                """
                SELECT side, net_amount, gross_amount FROM orders
                WHERE account_id = ? AND confirmed_at IS NOT NULL
                """,
                (account_id,),
            ):
                if row["side"] == "buy" and row["net_amount"] is not None:
                    buy_net += Decimal(row["net_amount"])
                if row["side"] == "sell" and row["gross_amount"] is not None:
                    sell_gross += Decimal(row["gross_amount"])
            daily_totals = [
                Decimal(row["total"])
                for row in conn.execute(
                    """
                    SELECT total FROM valuations
                    WHERE account_id = ? AND kind = 'daily_close' AND quality = 'complete' AND total IS NOT NULL
                    """,
                    (account_id,),
                )
            ]
            run_row = conn.execute(
                "SELECT COUNT(*) AS count, MIN(cycle_date) AS first_cycle, MAX(cycle_date) AS last_cycle FROM runs WHERE account_id = ?",
                (account_id,),
            ).fetchone()
        avg_daily: Decimal | None = None
        turnover: Decimal | None = None
        if daily_totals:
            avg_daily = sum(daily_totals, ZERO) / Decimal(len(daily_totals))
            if avg_daily > 0:
                turnover = (buy_net + sell_gross) / avg_daily
        total = valuation.total
        cash_ratio = None
        if total is not None and total > 0:
            cash_ratio = (valuation.balances.available + valuation.balances.frozen) / total
        return {
            "account_id": account_id,
            "as_of": utc_text(now),
            "cash_ratio": _ratio_text(cash_ratio) if cash_ratio is not None else None,
            "receivable": money_text(valuation.balances.receivable),
            "dividend_receivable": money_text(valuation.balances.dividend),
            "max_drawdown": _ratio_text(max_drawdown) if max_drawdown is not None else None,
            "valuation_range": {
                "min": rows[0]["observed_at"] if rows else None,
                "max": rows[-1]["observed_at"] if rows else None,
            },
            "complete_snapshots": len(complete),
            "incomplete_snapshots": incomplete_count,
            "total_fees": money_text(total_fees),
            "turnover": _ratio_text(turnover) if turnover is not None else None,
            "turnover_inputs": {
                "buy_net": money_text(buy_net),
                "sell_gross": money_text(sell_gross),
                "avg_daily_close": money_text(round_money(avg_daily)) if avg_daily is not None else None,
                "daily_close_samples": len(daily_totals),
            },
            "runs": {
                "count": run_row["count"],
                "first_cycle": run_row["first_cycle"],
                "last_cycle": run_row["last_cycle"],
            },
        }

    # ---- 每日调度（阶段 7） -------------------------------------------------------

    def record_daily_close(self, account_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        """保存 23:30 日终观察快照；同一账户同一北京时间日期只保存一次。

        快照只使用当时已取得的官方净值，不倒填后来披露值；缺失时保存为不完整快照。
        """
        now = self._now(now)
        kind_date = now.astimezone(SHANGHAI).date().isoformat()
        with self._write(now) as conn:
            account = self._account(conn, account_id)
            existing = conn.execute(
                "SELECT id FROM valuations WHERE account_id = ? AND kind = 'daily_close' AND kind_date = ?",
                (account_id, kind_date),
            ).fetchone()
            if existing is not None:
                return {"created": False, "kind_date": kind_date, "valuation": {"id": existing["id"]}}
            valuation = self._compute_valuation(conn, account_id, now)
            recorded = self._record_valuation(conn, account, valuation, kind="daily_close", kind_date=kind_date)
            return {"created": True, "kind_date": kind_date, "valuation": recorded}

    def scheduled_tick(
        self,
        *,
        now: datetime | None = None,
        nav_source: NavSource | None = None,
        settle: bool = True,
        cycles: bool = True,
    ) -> dict[str, Any]:
        """每日调度入口：补记到期周期、结算检查与 23:30 日终快照。

        每次 tick 都以当前时刻重新判定应执行内容；重复执行不重复下单、结算或快照。
        账户处于暂停或回撤锁定时，结算照常推进，只停止新增买入。

        `now` 原样下传给子步骤，而不是传递本方法解析出的时刻：`_now` 把「非 None」
        一律视为外部指定时间，生产库据此拒绝，下传解析值会让生产库拒绝自己解析的
        结果，使定时任务在生产库上完全无法运行。原样下传后语义仍是各步取同一时钟
        来源：显式时间只来自最外层调用方，生产库无参调用各步都取服务端时钟。
        本方法自身解析一次用于 slot 判定，与子步骤的取值最多相差极小的调度延迟。
        """
        resolved = self._now(now)
        result: dict[str, Any] = {"as_of": utc_text(resolved), "actions": []}
        with self.store.read() as conn:
            row = conn.execute("SELECT id, status FROM accounts WHERE active_slot = 1").fetchone()
        if row is None or row["status"] == "archived":
            result["actions"].append({"action": "skip", "reason": "no_active_account"})
            return result
        account_id = str(row["id"])
        # 周期处理始终执行：窗口内受理，窗口已过则补记 missed，重复调用不重复下单。
        if cycles:
            cycle_result = self.run_cycle(account_id, now=now, nav_source=nav_source)
            if cycle_result is not None and cycle_result["runs"]:
                result["actions"].append({"action": "cycles", "runs": cycle_result["runs"]})
        if settle and any(_in_slot(resolved, slot) for slot in SETTLE_SLOTS):
            report = self.settle(account_id, now=now, nav_source=nav_source)
            result["actions"].append({"action": "settle", "report": report.to_dict()})
        if _in_slot(resolved, DAILY_CLOSE_SLOT):
            result["actions"].append({"action": "daily_close", **self.record_daily_close(account_id, now=now)})
        if not result["actions"]:
            result["actions"].append({"action": "skip", "reason": "outside_slots"})
        return result

    # ---- 下单 -------------------------------------------------------------------

    def submit_order(
        self,
        account_id: str,
        idempotency_key: str,
        *,
        side: str,
        code: str,
        amount: str | None = None,
        shares: str | None = None,
        now: datetime | None = None,
        run_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """校验并受理一笔申购或赎回；业务拒绝也持久化为 rejected 订单。"""
        validate_key(idempotency_key)
        if side not in ("buy", "sell"):
            raise SimulationError("invalid_side", "side 必须是 buy 或 sell", status=422, fields=["side"])
        if not isinstance(code, str) or not _CODE.match(code):
            raise SimulationError("invalid_code", "基金代码必须是 6 位数字", status=422, fields=["code"])
        amount_value: Decimal | None = None
        shares_value: Decimal | None = None
        if side == "buy":
            if shares is not None or amount is None:
                raise SimulationError("invalid_request", "申购只能传 amount", status=422, fields=["amount"])
            amount_value = _input_decimal(amount, "amount", MONEY_PLACES)
            normalized: dict[str, str] = {"side": side, "code": code, "amount": money_text(amount_value.quantize(Decimal("0.01")))}
        else:
            if amount is not None or shares is None:
                raise SimulationError("invalid_request", "赎回只能传 shares", status=422, fields=["shares"])
            shares_value = _input_decimal(shares, "shares", SHARE_MAX_PLACES)
            normalized = {"side": side, "code": code, "shares": f"{shares_value.normalize():f}"}
        digest = request_hash(normalized)
        now = self._now(now)
        with self._write(now) as conn:
            row, created = self._accept_order(
                conn, account_id, idempotency_key, digest,
                side=side, code=code, amount=amount_value, shares=shares_value, now=now, run_id=run_id,
            )
            return self._order_view(conn, row), created

    def _accept_order(
        self,
        conn: sqlite3.Connection,
        account_id: str,
        idempotency_key: str,
        digest: str,
        *,
        side: str,
        code: str,
        amount: Decimal | None,
        shares: Decimal | None,
        now: datetime,
        run_id: str | None = None,
    ) -> tuple[sqlite3.Row, bool]:
        """在已打开的事务内受理订单；幂等键命中时返回原订单。

        策略周期与 HTTP/CLI 下单共用这一入口，业务拒绝同样持久化为 rejected 订单。
        """
        existing = conn.execute(
            "SELECT * FROM orders WHERE account_id = ? AND idempotency_key = ?",
            (account_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            if existing["request_hash"] != digest:
                raise SimulationError("idempotency_conflict", "同一幂等键对应了不同请求")
            return existing, False
        account = self._account(conn, account_id)
        decision = self._decide(conn, account, Decision(side, code, amount, shares), now)
        order_id = self._persist_order(
            conn, account_id, decision, idempotency_key, digest, now, run_id
        )
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return row, True

    def _decide(
        self, conn: sqlite3.Connection, account: sqlite3.Row, d: Decision, now: datetime
    ) -> Decision:
        d = self._gates(conn, account, d, now)
        if d.reason_code is not None:
            return d
        if d.side == "buy":
            return self._decide_buy(conn, account, d, now)
        return self._decide_sell(conn, account["id"], d)

    def _gates(
        self, conn: sqlite3.Connection, account: sqlite3.Row, d: Decision, now: datetime
    ) -> Decision:
        """与金额无关的受理前置检查：账户、规则、开放日、截止与日历。

        固定策略先在这里取得逐基金结论，再按限额缩减金额进入 `_decide_buy`。
        """
        if account["status"] == "archived":
            return d.reject("account_archived", "账户已归档")
        if d.side == "buy" and account["status"] in ("paused", "risk_paused"):
            return d.reject("account_paused", "账户已暂停新增买入")
        local = now.astimezone(SHANGHAI)
        today = local.date()
        rule, rule_id, reason = self._rule_for(conn, d.code, today)
        d.rule_id = rule_id
        if rule is None:
            return d.reject(reason or "rule_incomplete", MISSING_MESSAGES[reason or "rule_incomplete"])
        d.rule = rule
        try:
            if not rule.is_open_day(today):
                return d.reject("not_open_day", f"{today.isoformat()} 不是基金开放日")
            if local.time() >= rule.cutoff:
                return d.reject("after_cutoff", f"已过当日 {rule.cutoff:%H:%M} 申请截止")
            d.nav_date = today
            d.cancel_deadline_at = local_datetime(today, rule.cancel_cutoff)
            d.confirmation_due_date = rule.add_working_days(today, rule.confirm_working_days)
            if d.side == "sell":
                d.payment_due_date = rule.add_working_days(today, rule.payment_working_days)
        except CalendarUnknown as exc:
            return d.reject("calendar_unknown", f"交易日历未覆盖：{exc}")
        return d

    def _open_buys(self, conn: sqlite3.Connection, account_id: str) -> dict[str, Decimal]:
        pending: dict[str, Decimal] = {}
        for row in conn.execute(
            f"""
            SELECT code, accepted_amount FROM orders
            WHERE account_id = ? AND side = 'buy' AND status IN ({_marks(FREEZING_STATUSES)})
            """,
            (account_id, *FREEZING_STATUSES),
        ):
            pending[row["code"]] = pending.get(row["code"], ZERO) + Decimal(row["accepted_amount"])
        return pending

    @staticmethod
    def _reserved_daily(
        conn: sqlite3.Connection, account_id: str, code: str, nav_date: date
    ) -> Decimal:
        total = ZERO
        for row in conn.execute(
            """
            SELECT accepted_amount FROM orders
            WHERE account_id = ? AND side = 'buy' AND code = ? AND nav_date = ?
              AND status NOT IN ('rejected', 'cancelled', 'failed')
            """,
            (account_id, code, nav_date.isoformat()),
        ):
            total += Decimal(row["accepted_amount"])
        return total

    def remaining_daily_buy(
        self, account_id: str, code: str, *, now: datetime | None = None
    ) -> Decimal | None:
        """当日已知剩余申购额度；None 表示规则显式无限额。"""
        now = self._now(now)
        with self.store.read() as conn:
            rule, _, reason = self._rule_for(conn, code, _local_date(now))
            if rule is None:
                raise SimulationError(reason or "rule_incomplete", "没有可用的已核验规则")
            if rule.max_daily_buy is None:
                return None
            reserved = self._reserved_daily(conn, account_id, code, _local_date(now))
        return max(rule.max_daily_buy - reserved, ZERO)

    def _decide_buy(
        self, conn: sqlite3.Connection, account: sqlite3.Row, d: Decision, now: datetime
    ) -> Decision:
        assert d.rule is not None and d.amount is not None and d.nav_date is not None
        rule, amount, account_id = d.rule, d.amount, account["id"]
        holdings = self._holdings(conn, account_id)
        pending = self._open_buys(conn, account_id)
        minimum = rule.min_add if d.code in holdings or d.code in pending else rule.min_buy
        if amount < minimum:
            return d.reject("below_minimum", f"低于最低申购额 {money_text(minimum)} 元")
        if rule.max_daily_buy is not None:
            reserved = self._reserved_daily(conn, account_id, d.code, d.nav_date)
            if reserved + amount > rule.max_daily_buy:
                left = max(rule.max_daily_buy - reserved, ZERO)
                return d.reject("daily_limit", f"超过当日申购上限，剩余额度 {money_text(left)} 元")
        balances = self.ledger_balances(conn, account_id)
        if amount > balances.available:
            return d.reject("insufficient_cash", f"可用现金 {money_text(balances.available)} 元不足")
        valuation = self._compute_valuation(conn, account_id, now, extra_codes=[d.code])
        issues = valuation.missing + valuation.target_missing
        if issues or valuation.total is None:
            first = issues[0] if issues else {"code": d.code, "reason": "nav_missing"}
            return d.reject(
                first["reason"],
                f"{first.get('code', d.code)} {MISSING_MESSAGES.get(first['reason'], '数据不完整')}",
            )
        total = valuation.total
        peak = max(Decimal(account["peak"]), total)
        if total <= peak * (Decimal(1) - DRAWDOWN_LIMIT):
            self._set_status(conn, account_id, "risk_paused", "drawdown")
            return d.reject("account_paused", "回撤达到 15%，已暂停新增买入")
        exposure = valuation.values_by_code.get(d.code, ZERO) + pending.get(d.code, ZERO)
        if exposure + amount > total * SINGLE_FUND_LIMIT:
            return d.reject("position_limit", "买入后单只基金将超过模拟总资产的 25%")
        if rule.index_group:
            today = _local_date(now)
            group_exposure = ZERO
            for code in set(holdings) | set(pending) | {d.code}:
                other = rule if code == d.code else self._rule_for(conn, code, today)[0]
                if other is not None and other.index_group == rule.index_group:
                    group_exposure += valuation.values_by_code.get(code, ZERO) + pending.get(code, ZERO)
            if group_exposure + amount > total * INDEX_GROUP_LIMIT:
                return d.reject("position_limit", "买入后同一跟踪指数合计将超过 40%")
        d.snapshot_ids = sorted(set(valuation.snapshot_ids.values()))
        d.accepted = True
        return d

    def _decide_sell(self, conn: sqlite3.Connection, account_id: str, d: Decision) -> Decision:
        assert d.rule is not None and d.shares is not None
        rule, shares = d.rule, d.shares
        if places_of(shares) > places_of(rule.share_quantum):
            return d.reject("invalid_quantity", f"赎回份额精度不能超过 {rule.share_quantum}")
        if shares < rule.min_sell_shares:
            return d.reject("below_minimum", f"低于最低赎回份额 {rule.min_sell_shares}")
        lots = conn.execute(
            """
            SELECT id, remaining_shares, frozen_shares FROM lots
            WHERE account_id = ? AND code = ? ORDER BY confirmed_date, rowid
            """,
            (account_id, d.code),
        ).fetchall()
        available = sum(
            (Decimal(lot["remaining_shares"]) - Decimal(lot["frozen_shares"]) for lot in lots), ZERO
        )
        if shares > available:
            return d.reject("insufficient_shares", f"可卖份额 {decimal_text(available)} 不足")
        remainder = available - shares
        if ZERO < remainder < rule.min_remaining_shares:
            return d.reject(
                "min_remaining",
                f"卖出后剩余 {decimal_text(remainder)} 份低于最低保留份额，请改为全部赎回",
            )
        left = shares
        for lot in lots:
            free = Decimal(lot["remaining_shares"]) - Decimal(lot["frozen_shares"])
            if free <= 0:
                continue
            take = min(free, left)
            d.allocations.append((str(lot["id"]), take))
            left -= take
            if left == 0:
                break
        d.accepted = True
        return d

    def _persist_order(
        self,
        conn: sqlite3.Connection,
        account_id: str,
        d: Decision,
        idempotency_key: str,
        digest: str,
        now: datetime,
        run_id: str | None,
    ) -> str:
        order_id = new_id("ord")
        stamp = utc_text(now)
        accepted = d.accepted
        rule = d.rule
        values: dict[str, Any] = {
            "id": order_id,
            "account_id": account_id,
            "idempotency_key": idempotency_key,
            "request_hash": digest,
            "side": d.side,
            "code": d.code,
            "requested_amount": money_text(d.amount) if d.amount is not None else None,
            "accepted_amount": money_text(d.amount) if accepted and d.amount is not None else None,
            "requested_shares": decimal_text(d.shares) if d.shares is not None else None,
            "status": "awaiting_nav" if accepted else "rejected",
            "reason_code": d.reason_code,
            "reason_detail": d.reason_detail,
            "version": 1,
            "rule_id": d.rule_id,
            "snapshot_ids_json": json.dumps(d.snapshot_ids),
            "run_id": run_id,
            "submitted_at": stamp,
            "created_at": stamp,
            "updated_at": stamp,
        }
        if accepted and rule is not None:
            assert d.nav_date and d.confirmation_due_date and d.cancel_deadline_at
            values.update(
                nav_date=d.nav_date.isoformat(),
                cancel_deadline_at=utc_text(d.cancel_deadline_at),
                confirmation_due_date=d.confirmation_due_date.isoformat(),
                confirmation_due_at=utc_text(local_datetime(d.confirmation_due_date, rule.confirm_time)),
            )
            if d.payment_due_date is not None:
                values.update(
                    payment_due_date=d.payment_due_date.isoformat(),
                    payment_due_at=utc_text(local_datetime(d.payment_due_date, rule.payment_time)),
                )
        columns = ", ".join(values)
        conn.execute(
            f"INSERT INTO orders ({columns}) VALUES ({_marks(values)})", tuple(values.values())
        )
        if not accepted:
            self._event(conn, order_id, None, "rejected", now, reason=d.reason_code)
            return order_id
        self._event(conn, order_id, None, "accepted", now)
        self._event(conn, order_id, "accepted", "awaiting_nav", now, effective_date=values["nav_date"])
        if d.side == "buy":
            assert d.amount is not None
            self._ledger(
                conn, account_id, f"{order_id}:accept", "buy_accept", now, values["nav_date"],
                order_id=order_id, code=d.code, available=-d.amount, frozen=d.amount,
            )
        else:
            for lot_id, take in d.allocations:
                lot = conn.execute("SELECT frozen_shares FROM lots WHERE id = ?", (lot_id,)).fetchone()
                conn.execute(
                    "UPDATE lots SET frozen_shares = ? WHERE id = ?",
                    (decimal_text(Decimal(lot["frozen_shares"]) + take), lot_id),
                )
                conn.execute(
                    "INSERT INTO sell_allocations (order_id, lot_id, shares) VALUES (?, ?, ?)",
                    (order_id, lot_id, decimal_text(take)),
                )
        return order_id

    @staticmethod
    def _event(
        conn: sqlite3.Connection,
        order_id: str,
        from_status: str | None,
        to_status: str,
        now: datetime,
        *,
        effective_date: str | None = None,
        reason: str | None = None,
        evidence: Iterable[str] = (),
    ) -> None:
        conn.execute(
            """
            INSERT INTO order_events (
                order_id, from_status, to_status, occurred_at, effective_date, reason, evidence_ids_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (order_id, from_status, to_status, utc_text(now), effective_date, reason, json.dumps(list(evidence))),
        )

    def _transition(
        self,
        conn: sqlite3.Connection,
        order_id: str,
        to_status: str,
        now: datetime,
        *,
        effective_date: str | None = None,
        reason: str | None = None,
        evidence: Iterable[str] = (),
        **updates: Any,
    ) -> sqlite3.Row:
        current = conn.execute("SELECT status FROM orders WHERE id = ?", (order_id,)).fetchone()
        assignments = ", ".join(f"{key} = ?" for key in updates)
        conn.execute(
            f"""
            UPDATE orders SET status = ?, version = version + 1, updated_at = ?
            {', ' + assignments if assignments else ''} WHERE id = ?
            """,
            (to_status, utc_text(now), *updates.values(), order_id),
        )
        self._event(
            conn, order_id, current["status"], to_status, now,
            effective_date=effective_date, reason=reason, evidence=evidence,
        )
        return conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()

    def _release(self, conn: sqlite3.Connection, order: sqlite3.Row, kind: str, now: datetime) -> None:
        if order["side"] == "buy":
            amount = dec(order["accepted_amount"])
            self._ledger(
                conn, order["account_id"], f"{order['id']}:{kind}", f"buy_{kind}", now,
                _local_date(now), order_id=order["id"], code=order["code"],
                available=amount, frozen=-amount,
            )
            return
        for alloc in conn.execute(
            "SELECT lot_id, shares FROM sell_allocations WHERE order_id = ?", (order["id"],)
        ).fetchall():
            lot = conn.execute("SELECT frozen_shares FROM lots WHERE id = ?", (alloc["lot_id"],)).fetchone()
            conn.execute(
                "UPDATE lots SET frozen_shares = ? WHERE id = ?",
                (decimal_text(Decimal(lot["frozen_shares"]) - Decimal(alloc["shares"])), alloc["lot_id"]),
            )

    def cancel_order(
        self, order_id: str, idempotency_key: str, expected_version: object, *, now: datetime | None = None
    ) -> dict[str, Any]:
        validate_key(idempotency_key)
        version = self._expected_version(expected_version)
        now = self._now(now)
        digest = request_hash({"operation": "cancel", "order_id": order_id, "expected_version": version})
        with self._write(now) as conn:
            order = self._order(conn, order_id)
            if self._idem(conn, order["account_id"], "cancel", idempotency_key, digest) is not None:
                return self._order_view(conn, order)
            if order["version"] != version:
                raise SimulationError("version_conflict", "订单状态已变更")
            if order["status"] not in FREEZING_STATUSES:
                raise SimulationError("invalid_state", "该订单已不能撤销")
            if now >= _parse_dt(order["cancel_deadline_at"]):
                raise SimulationError("cancel_deadline_passed", "已过撤单截止时间")
            self._release(conn, order, "cancel", now)
            row = self._transition(conn, order_id, "cancelled", now, reason="user_cancel")
            self._save_idem(conn, order["account_id"], "cancel", idempotency_key, digest, order_id, now)
            return self._order_view(conn, row)

    def fail_order(
        self, order_id: str, *, reason: str, evidence: str, now: datetime | None = None
    ) -> dict[str, Any]:
        """仅在有明确不受理或确认失败证据时使用；已确认订单不能回滚为失败。"""
        if not reason.strip() or not evidence.strip():
            raise SimulationError("invalid_request", "需要失败原因和证据", status=422)
        now = self._now(now)
        with self._write(now) as conn:
            order = self._order(conn, order_id)
            if order["status"] == "failed":
                return self._order_view(conn, order)
            if order["status"] not in FREEZING_STATUSES:
                raise SimulationError("invalid_state", "已确认或已关闭的订单不能标记失败")
            payload = {"order_id": order_id, "reason": reason.strip(), "evidence": evidence.strip()}
            digest = request_hash({"kind": "failure_evidence", **payload})
            snapshot_id = new_id("snap")
            conn.execute(
                """
                INSERT INTO snapshots (
                    id, kind, code, nav_date, value, payload_json, source, source_url,
                    published_at, observed_at, available_at, hash
                ) VALUES (?, 'failure_evidence', ?, NULL, NULL, ?, 'manual', NULL, NULL, ?, ?, ?)
                """,
                (snapshot_id, order["code"], json.dumps(payload, ensure_ascii=False), utc_text(now), utc_text(now), digest),
            )
            self._release(conn, order, "fail", now)
            row = self._transition(
                conn, order_id, "failed", now, reason="business_failure", evidence=[snapshot_id],
                reason_code="business_failure", reason_detail=reason.strip(),
            )
            return self._order_view(conn, row)

    @staticmethod
    def _order(conn: sqlite3.Connection, order_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row is None:
            raise SimulationError("order_not_found", "订单不存在", status=404)
        return row

    # ---- 结算 -------------------------------------------------------------------

    def settle(
        self, account_id: str, *, now: datetime | None = None, nav_source: NavSource | None = None
    ) -> SettleReport:
        """推进到期订单与分红，再保存一次观察快照；重复执行不会重复记账。"""
        now = self._now(now)
        report = SettleReport()
        observations: list[NavObservation] = []
        if nav_source is not None:
            with self.store.read() as conn:
                self._account(conn, account_id)
                wanted: set[tuple[str, str | None]] = {
                    (row["code"], row["nav_date"])
                    for row in conn.execute(
                        "SELECT code, nav_date FROM orders WHERE account_id = ? AND status = 'awaiting_nav'",
                        (account_id,),
                    )
                }
                wanted |= {(code, None) for code in self._holdings(conn, account_id)}
                # 已核验规则的基金也观察最新净值，供新增买入的目标基金核验。
                wanted |= {
                    (row["code"], None)
                    for row in conn.execute(
                        "SELECT DISTINCT code FROM rule_versions WHERE environment = ? AND status = 'verified'",
                        (self.store.environment,),
                    )
                }
                wanted |= {
                    (row["code"], row["ex_date"])
                    for row in conn.execute(
                        "SELECT code, ex_date FROM dividends WHERE account_id = ? AND status = 'announced'",
                        (account_id,),
                    )
                }
            # 来源读取在写事务外完成，失败只记录错误，不生成默认净值。
            for code, nav_date in sorted(wanted, key=lambda item: (item[0], item[1] or "")):
                try:
                    obs = nav_source.fetch(
                        code, date.fromisoformat(nav_date) if nav_date else None, now
                    )
                except Exception as exc:  # noqa: BLE001 - 来源失败逐项留档
                    report.errors.append({"code": code, "nav_date": nav_date, "error": str(exc)})
                    continue
                if obs is not None:
                    observations.append(obs)
        with self._write(now) as conn:
            self._account(conn, account_id)
            before = conn.execute("SELECT COUNT(*) FROM snapshots WHERE kind = 'nav'").fetchone()[0]
            for obs in observations:
                self._record_nav(conn, obs)
            report.nav_recorded = (
                conn.execute("SELECT COUNT(*) FROM snapshots WHERE kind = 'nav'").fetchone()[0] - before
            )
            self._settle_orders(conn, account_id, now, report)
            self._process_dividends(conn, account_id, now, report)
            for item in self._nav_corrections(conn, account_id, now):
                report.blocked.append({**item, "reason": "nav_correction"})
                account = self._account(conn, account_id)
                if account["status"] == "active":
                    self._set_status(conn, account_id, "paused", "nav_correction")
            account = self._account(conn, account_id)
            valuation = self._compute_valuation(conn, account_id, now)
            report.valuation = self._record_valuation(conn, account, valuation)
        return report

    def _settle_orders(
        self, conn: sqlite3.Connection, account_id: str, now: datetime, report: SettleReport
    ) -> None:
        rows = conn.execute(
            f"""
            SELECT * FROM orders WHERE account_id = ? AND status IN ({_marks(SETTLING_STATUSES)})
            ORDER BY submitted_at, id
            """,
            (account_id, *SETTLING_STATUSES),
        ).fetchall()
        for row in rows:
            order_id = row["id"]
            if row["status"] == "awaiting_nav":
                nav_rows = self._nav_rows(conn, row["code"], row["nav_date"], now)
                status = self._nav_status(nav_rows)
                if status == "missing":
                    report.waiting.append({"order_id": order_id, "reason": "nav_missing", "nav_date": row["nav_date"]})
                    continue
                if status == "conflict":
                    self._block(conn, row, "nav_conflict", now, report)
                    continue
                snap = nav_rows[0]
                row = self._transition(
                    conn, order_id, "awaiting_confirmation", now,
                    effective_date=row["nav_date"], evidence=[snap["id"]],
                    nav_value=snap["value"], nav_snapshot_id=snap["id"],
                    nav_observed_at=snap["observed_at"], nav_published_at=snap["published_at"],
                    blocked_reason=None,
                )
                report.transitions.append({"order_id": order_id, "to": "awaiting_confirmation"})
            if row["status"] == "awaiting_confirmation":
                if now < _parse_dt(row["confirmation_due_at"]):
                    report.waiting.append({"order_id": order_id, "reason": "confirmation_due", "due_at": row["confirmation_due_at"]})
                    continue
                if self._nav_status(self._nav_rows(conn, row["code"], row["nav_date"], now)) == "conflict":
                    self._block(conn, row, "nav_conflict", now, report)
                    continue
                rule = self._rule_by_id(conn, row["rule_id"])
                if row["side"] == "buy":
                    row = self._confirm_buy(conn, row, rule, now)
                else:
                    row = self._confirm_sell(conn, row, rule, now)
                report.transitions.append({"order_id": order_id, "to": row["status"]})
            if row["status"] == "awaiting_payment":
                if now < _parse_dt(row["payment_due_at"]):
                    report.waiting.append({"order_id": order_id, "reason": "payment_due", "due_at": row["payment_due_at"]})
                    continue
                row = self._pay_sell(conn, row, now)
                report.transitions.append({"order_id": order_id, "to": row["status"]})

    def _block(
        self, conn: sqlite3.Connection, row: sqlite3.Row, reason: str, now: datetime, report: SettleReport
    ) -> None:
        if row["blocked_reason"] != reason:
            conn.execute(
                "UPDATE orders SET blocked_reason = ?, updated_at = ? WHERE id = ?",
                (reason, utc_text(now), row["id"]),
            )
        report.blocked.append({"order_id": row["id"], "reason": reason})

    def _confirm_buy(
        self, conn: sqlite3.Connection, row: sqlite3.Row, rule: FundRule, now: datetime
    ) -> sqlite3.Row:
        amount = dec(row["accepted_amount"])
        nav = dec(row["nav_value"])
        net, fee = rule.subscription_split(amount)
        shares = rule.quantize_shares(net / nav)
        effective = row["confirmation_due_date"]
        if shares <= 0:
            self._release(conn, row, "fail", now)
            return self._transition(
                conn, row["id"], "failed", now, effective_date=effective, reason="zero_shares",
                reason_code="zero_shares", reason_detail="确认份额为零",
            )
        rounding = shares * nav - net
        lot_id = new_id("lot")
        conn.execute(
            """
            INSERT INTO lots (
                id, account_id, code, buy_order_id, confirmed_shares, remaining_shares,
                frozen_shares, net_cost, remaining_cost, confirmed_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                lot_id, row["account_id"], row["code"], row["id"],
                quantize_exact(shares, rule.share_quantum), quantize_exact(shares, rule.share_quantum),
                quantize_exact(ZERO, rule.share_quantum), money_text(amount), money_text(amount), effective,
            ),
        )
        self._ledger(
            conn, row["account_id"], f"{row['id']}:confirm", "buy_confirm", now, effective,
            order_id=row["id"], code=row["code"], lot_id=lot_id,
            frozen=-amount, shares=shares, fee=fee, rounding=rounding,
        )
        return self._transition(
            conn, row["id"], "settled", now, effective_date=effective,
            confirmed_at=utc_text(now), fee=money_text(fee), net_amount=money_text(net),
            confirmed_shares=quantize_exact(shares, rule.share_quantum),
            rounding_delta=decimal_text(rounding), blocked_reason=None,
        )

    def _confirm_sell(
        self, conn: sqlite3.Connection, row: sqlite3.Row, rule: FundRule, now: datetime
    ) -> sqlite3.Row:
        nav = dec(row["nav_value"])
        effective = row["confirmation_due_date"]
        confirm_day = date.fromisoformat(effective)
        allocations = conn.execute(
            """
            SELECT a.lot_id, a.shares, l.confirmed_date, l.remaining_shares, l.frozen_shares, l.remaining_cost
            FROM sell_allocations a JOIN lots l ON l.id = a.lot_id
            WHERE a.order_id = ? ORDER BY l.confirmed_date, l.rowid
            """,
            (row["id"],),
        ).fetchall()
        total_shares = sum((Decimal(item["shares"]) for item in allocations), ZERO)
        gross = round_money(total_shares * nav)
        fee_total = ZERO
        for item in allocations:
            take = Decimal(item["shares"])
            days = holding_days(date.fromisoformat(item["confirmed_date"]), confirm_day)
            lot_fee = round_money(take * nav * rule.redemption_rate(days))
            remaining = Decimal(item["remaining_shares"])
            cost = Decimal(item["remaining_cost"])
            cost_out = cost if take == remaining else round_money(cost * take / remaining)
            conn.execute(
                "UPDATE lots SET remaining_shares = ?, frozen_shares = ?, remaining_cost = ? WHERE id = ?",
                (
                    decimal_text(remaining - take),
                    decimal_text(Decimal(item["frozen_shares"]) - take),
                    money_text(cost - cost_out),
                    item["lot_id"],
                ),
            )
            self._ledger(
                conn, row["account_id"], f"{row['id']}:confirm:{item['lot_id']}", "sell_confirm_lot",
                now, effective, order_id=row["id"], code=row["code"], lot_id=item["lot_id"],
                shares=-take, fee=lot_fee,
            )
            fee_total += lot_fee
        net = gross - fee_total
        self._ledger(
            conn, row["account_id"], f"{row['id']}:confirm", "sell_confirm", now, effective,
            order_id=row["id"], code=row["code"], receivable=net,
            rounding=gross - total_shares * nav,
        )
        return self._transition(
            conn, row["id"], "awaiting_payment", now, effective_date=effective,
            confirmed_at=utc_text(now), gross_amount=money_text(gross), fee=money_text(fee_total),
            net_amount=money_text(net), confirmed_shares=decimal_text(total_shares),
            rounding_delta=decimal_text(gross - total_shares * nav), blocked_reason=None,
        )

    def _pay_sell(self, conn: sqlite3.Connection, row: sqlite3.Row, now: datetime) -> sqlite3.Row:
        net = dec(row["net_amount"])
        self._ledger(
            conn, row["account_id"], f"{row['id']}:payment", "sell_payment", now,
            row["payment_due_date"], order_id=row["id"], code=row["code"],
            receivable=-net, available=net,
        )
        return self._transition(
            conn, row["id"], "settled", now, effective_date=row["payment_due_date"], paid_at=utc_text(now)
        )

    def _nav_corrections(
        self, conn: sqlite3.Connection, account_id: str, now: datetime
    ) -> list[dict[str, Any]]:
        """已用于成交的净值若出现不同版本，锁定新增买入，等待人工冲正。"""
        found: list[dict[str, Any]] = []
        for row in conn.execute(
            """
            SELECT DISTINCT code, nav_date FROM orders
            WHERE account_id = ? AND nav_snapshot_id IS NOT NULL
              AND status IN ('awaiting_payment', 'settled')
            """,
            (account_id,),
        ).fetchall():
            if self._nav_status(self._nav_rows(conn, row["code"], row["nav_date"], now)) == "conflict":
                found.append({"code": row["code"], "nav_date": row["nav_date"]})
        return found

    # ---- 分红 -------------------------------------------------------------------

    def register_dividend(
        self,
        account_id: str,
        *,
        announcement_key: str,
        code: str,
        per_share: str,
        tax: str,
        record_date: str,
        ex_date: str,
        pay_date: str,
        evidence: str,
        now: datetime | None = None,
    ) -> tuple[dict[str, Any], bool]:
        validate_key(announcement_key)
        if not _CODE.match(code):
            raise SimulationError("invalid_code", "基金代码必须是 6 位数字", status=422)
        per_share_value = _input_decimal(per_share, "per_share", NAV_MAX_PLACES)
        tax_value = _input_decimal(tax, "tax", MONEY_PLACES, allow_zero=True)
        record, ex, pay = (
            _input_date(record_date, "record_date"),
            _input_date(ex_date, "ex_date"),
            _input_date(pay_date, "pay_date"),
        )
        if not record <= ex <= pay:
            raise SimulationError("invalid_date", "需满足登记日 ≤ 除息日 ≤ 发放日", status=422)
        if not evidence.strip():
            raise SimulationError("invalid_request", "需要分红公告证据", status=422)
        normalized = {
            "code": code,
            "per_share": decimal_text(per_share_value),
            "tax": money_text(tax_value),
            "record_date": record.isoformat(),
            "ex_date": ex.isoformat(),
            "pay_date": pay.isoformat(),
        }
        digest = request_hash(normalized)
        now = self._now(now)
        with self._write(now) as conn:
            account = self._account(conn, account_id)
            existing = conn.execute(
                "SELECT * FROM dividends WHERE account_id = ? AND announcement_key = ?",
                (account_id, announcement_key),
            ).fetchone()
            if existing is not None:
                if existing["request_hash"] != digest:
                    raise SimulationError("idempotency_conflict", "同一公告 ID 对应了不同内容")
                return dict(existing), False
            if account["status"] == "archived":
                raise SimulationError("account_archived", "账户已归档")
            rule, rule_id, reason = self._rule_for(conn, code, record)
            if rule is None or rule_id is None:
                raise SimulationError(reason or "rule_incomplete", "没有可用的已核验规则")
            dividend_id = new_id("div")
            conn.execute(
                """
                INSERT INTO dividends (
                    id, account_id, code, announcement_key, request_hash, rule_id, record_date,
                    ex_date, pay_date, per_share, tax, status, evidence_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'announced', ?, ?)
                """,
                (
                    dividend_id, account_id, code, announcement_key, digest, rule_id,
                    normalized["record_date"], normalized["ex_date"], normalized["pay_date"],
                    normalized["per_share"], normalized["tax"],
                    json.dumps({"text": evidence.strip()}, ensure_ascii=False), utc_text(now),
                ),
            )
            row = conn.execute("SELECT * FROM dividends WHERE id = ?", (dividend_id,)).fetchone()
            return dict(row), True

    def _process_dividends(
        self, conn: sqlite3.Connection, account_id: str, now: datetime, report: SettleReport
    ) -> None:
        rows = conn.execute(
            """
            SELECT * FROM dividends WHERE account_id = ? AND status IN ('announced', 'ex_recorded')
            ORDER BY ex_date, id
            """,
            (account_id,),
        ).fetchall()
        for div in rows:
            rule = self._rule_by_id(conn, div["rule_id"])
            status = div["status"]
            record_day = date.fromisoformat(div["record_date"])
            if status == "announced":
                ready_at = max(
                    local_datetime(record_day + timedelta(days=1), time(0, 0)),
                    local_datetime(date.fromisoformat(div["ex_date"]), time(0, 0)),
                )
                if now < ready_at:
                    report.dividends.append({"dividend_id": div["id"], "state": "waiting_ex"})
                    continue
                pending = conn.execute(
                    """
                    SELECT COUNT(*) FROM orders WHERE account_id = ? AND code = ?
                      AND status IN ('awaiting_nav', 'awaiting_confirmation')
                      AND confirmation_due_date <= ?
                    """,
                    (account_id, div["code"], div["record_date"]),
                ).fetchone()[0]
                if pending:
                    report.dividends.append({"dividend_id": div["id"], "state": "waiting_confirmation"})
                    continue
                eligible = self.ledger_shares(conn, account_id, div["code"], through=record_day)
                gross = round_money(eligible * Decimal(div["per_share"]))
                tax = Decimal(div["tax"])
                if tax > gross:
                    report.blocked.append({"dividend_id": div["id"], "reason": "dividend_tax_exceeds_gross"})
                    continue
                net = gross - tax
                self._ledger(
                    conn, account_id, f"{div['id']}:ex", "dividend_ex", now, div["ex_date"],
                    dividend_id=div["id"], code=div["code"], dividend=net,
                )
                conn.execute(
                    "UPDATE dividends SET status = 'ex_recorded', eligible_shares = ?, gross = ?, net = ? WHERE id = ?",
                    (decimal_text(eligible), money_text(gross), money_text(net), div["id"]),
                )
                report.dividends.append({"dividend_id": div["id"], "state": "ex_recorded", "net": money_text(net)})
                status = "ex_recorded"
                div = conn.execute("SELECT * FROM dividends WHERE id = ?", (div["id"],)).fetchone()
            if status == "ex_recorded":
                if now < local_datetime(date.fromisoformat(div["pay_date"]), rule.payment_time):
                    report.dividends.append({"dividend_id": div["id"], "state": "waiting_pay"})
                    continue
                net = Decimal(div["net"])
                self._ledger(
                    conn, account_id, f"{div['id']}:pay", "dividend_pay", now, div["pay_date"],
                    dividend_id=div["id"], code=div["code"], dividend=-net, available=net,
                )
                conn.execute("UPDATE dividends SET status = 'paid' WHERE id = ?", (div["id"],))
                report.dividends.append({"dividend_id": div["id"], "state": "paid", "net": money_text(net)})

    # ---- 查询视图 ---------------------------------------------------------------

    def list_accounts(self) -> list[dict[str, Any]]:
        with self.store.read() as conn:
            return [
                {
                    "id": row["id"],
                    "experiment_id": row["experiment_id"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                    "archived_at": row["archived_at"],
                }
                for row in conn.execute(
                    "SELECT * FROM accounts ORDER BY (archived_at IS NULL) DESC, created_at DESC"
                )
            ]

    def account_view(self, account_id: str) -> dict[str, Any]:
        now = self._now(None)
        with self.store.read() as conn:
            return self._account_view(conn, account_id, now)

    def _account_view(self, conn: sqlite3.Connection, account_id: str, now: datetime) -> dict[str, Any]:
        account = self._account(conn, account_id)
        valuation = self._compute_valuation(conn, account_id, now)
        initial = Decimal(account["initial_cash"])
        peak = Decimal(account["peak"])
        total = valuation.total
        profit = total - initial if total is not None else None
        complete = valuation.quality == "complete" and total is not None
        if complete and total is not None:
            peak = max(peak, total)
        drawdown = Decimal(1) - total / peak if complete and total is not None and peak > 0 else None
        nav_dates = [ref["nav_date"] for ref in valuation.nav_refs if ref["nav_date"]]
        pending = conn.execute(
            f"SELECT COUNT(*) FROM orders WHERE account_id = ? AND status IN ({_marks(PENDING_STATUSES)})",
            (account_id, *PENDING_STATUSES),
        ).fetchone()[0]
        latest = conn.execute(
            "SELECT observed_at, quality FROM valuations WHERE account_id = ? ORDER BY observed_at DESC, rowid DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        return {
            "id": account["id"],
            "experiment_id": account["experiment_id"],
            "environment": self.store.environment,
            "status": account["status"],
            "pause_reason": account["pause_reason"],
            "version": account["version"],
            "created_at": account["created_at"],
            "archived_at": account["archived_at"],
            "initial_cash": account["initial_cash"],
            "as_of": utc_text(now),
            "balances": {
                **valuation.balances.to_dict(),
                "holdings_value": money_text(valuation.holdings_value) if valuation.holdings_value is not None else None,
                "total": money_text(total) if total is not None else None,
            },
            "profit": money_text(profit) if profit is not None else None,
            "profit_rate": _ratio_text(profit / initial) if profit is not None else None,
            "peak": money_text(peak),
            "drawdown": _ratio_text(drawdown) if drawdown is not None else None,
            "quality": valuation.quality,
            "missing": valuation.missing,
            "nav_date_range": {
                "min": min(nav_dates) if nav_dates else None,
                "max": max(nav_dates) if nav_dates else None,
            },
            "pending_order_count": pending,
            "latest_valuation": dict(latest) if latest is not None else None,
        }

    def positions_view(self, account_id: str) -> list[dict[str, Any]]:
        now = self._now(None)
        with self.store.read() as conn:
            self._account(conn, account_id)
            holdings = self._holdings(conn, account_id)
            names = self._rule_names(conn)
            items: list[dict[str, Any]] = []
            for code in sorted(holdings):
                info = holdings[code]
                issues, nav, nav_date, _ = self._code_issues(conn, code, now)
                _, rule_id, reason = self._rule_for(conn, code, _local_date(now))
                items.append(
                    {
                        "code": code,
                        "name": names.get(code, code),
                        "shares": decimal_text(info["shares"]),
                        "frozen_shares": decimal_text(info["frozen"]),
                        "available_shares": decimal_text(info["shares"] - info["frozen"]),
                        "net_cost": money_text(info["cost"]),
                        "lot_count": int(info["lots"]),
                        "nav": decimal_text(nav) if nav is not None else None,
                        "nav_date": nav_date,
                        "value": money_text(round_money(info["shares"] * nav)) if nav is not None else None,
                        "rule_id": rule_id,
                        "rule_status": reason or "verified",
                        "issues": issues,
                    }
                )
            return items

    def list_orders(
        self,
        account_id: str,
        *,
        cursor: str | None = None,
        limit: int = 20,
        statuses: list[str] | None = None,
    ) -> dict[str, Any]:
        if not 1 <= limit <= 100:
            raise SimulationError("invalid_limit", "limit 需在 1–100 之间", status=422, fields=["limit"])
        for status in statuses or []:
            if status not in ORDER_STATUSES:
                raise SimulationError("invalid_status", f"未知订单状态：{status}", status=422, fields=["status"])
        query = "SELECT * FROM orders WHERE account_id = ?"
        params: list[Any] = [account_id]
        if statuses:
            query += f" AND status IN ({_marks(statuses)})"
            params.extend(statuses)
        if cursor:
            try:
                submitted_at, order_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
            except (ValueError, UnicodeDecodeError) as exc:
                raise SimulationError("invalid_cursor", "cursor 无效", status=422, fields=["cursor"]) from exc
            query += " AND (submitted_at < ? OR (submitted_at = ? AND id < ?))"
            params.extend([submitted_at, submitted_at, order_id])
        query += " ORDER BY submitted_at DESC, id DESC LIMIT ?"
        params.append(limit + 1)
        with self.store.read() as conn:
            self._account(conn, account_id)
            rows = conn.execute(query, params).fetchall()
            names = self._rule_names(conn)
            items = [self._order_view(conn, row, names=names) for row in rows[:limit]]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = base64.urlsafe_b64encode(f"{last['submitted_at']}|{last['id']}".encode()).decode()
        return {"items": items, "next_cursor": next_cursor}

    def order_detail(self, order_id: str) -> dict[str, Any]:
        now = self._now(None)
        with self.store.read() as conn:
            row = self._order(conn, order_id)
            view = self._order_view(conn, row)
            view["cancellable"] = bool(
                row["status"] in FREEZING_STATUSES
                and row["cancel_deadline_at"]
                and now < _parse_dt(row["cancel_deadline_at"])
            )
            view["events"] = [
                {
                    "from_status": event["from_status"],
                    "to_status": event["to_status"],
                    "occurred_at": event["occurred_at"],
                    "effective_date": event["effective_date"],
                    "reason": event["reason"],
                }
                for event in conn.execute(
                    "SELECT * FROM order_events WHERE order_id = ? ORDER BY id", (order_id,)
                )
            ]
            view["allocations"] = [
                {"lot_id": item["lot_id"], "shares": item["shares"], "lot_confirmed_date": item["confirmed_date"]}
                for item in conn.execute(
                    """
                    SELECT a.lot_id, a.shares, l.confirmed_date FROM sell_allocations a
                    JOIN lots l ON l.id = a.lot_id WHERE a.order_id = ? ORDER BY l.confirmed_date, l.rowid
                    """,
                    (order_id,),
                )
            ]
            view["ledger"] = [
                {
                    "event_type": item["event_type"],
                    "cash_available_delta": item["cash_available_delta"],
                    "cash_frozen_delta": item["cash_frozen_delta"],
                    "receivable_delta": item["receivable_delta"],
                    "shares_delta": item["shares_delta"],
                    "fee": item["fee"],
                    "effective_date": item["effective_date"],
                    "recorded_at": item["recorded_at"],
                }
                for item in conn.execute(
                    "SELECT * FROM ledger_events WHERE order_id = ? ORDER BY id", (order_id,)
                )
            ]
            rule_row = (
                conn.execute(
                    "SELECT id, environment, status, record_json FROM rule_versions WHERE id = ?",
                    (row["rule_id"],),
                ).fetchone()
                if row["rule_id"]
                else None
            )
            view["rule"] = (
                {
                    "id": rule_row["id"],
                    "environment": rule_row["environment"],
                    "status": rule_row["status"],
                    "evidence": json.loads(rule_row["record_json"]).get("evidence"),
                }
                if rule_row is not None
                else None
            )
            snapshot_ids = json.loads(row["snapshot_ids_json"] or "[]")
            if row["nav_snapshot_id"]:
                snapshot_ids.append(row["nav_snapshot_id"])
            view["snapshots"] = [
                {
                    "id": snap["id"],
                    "kind": snap["kind"],
                    "code": snap["code"],
                    "nav_date": snap["nav_date"],
                    "value": snap["value"],
                    "source": snap["source"],
                    "observed_at": snap["observed_at"],
                }
                for snap in conn.execute(
                    f"SELECT * FROM snapshots WHERE id IN ({_marks(snapshot_ids)}) ORDER BY nav_date, code",
                    snapshot_ids,
                )
            ] if snapshot_ids else []
            return view

    def _order_view(
        self, conn: sqlite3.Connection, row: sqlite3.Row, *, names: dict[str, str] | None = None
    ) -> dict[str, Any]:
        names = names if names is not None else self._rule_names(conn)
        keys = (
            "id", "account_id", "side", "code", "status", "reason_code", "reason_detail",
            "blocked_reason", "version", "requested_amount", "accepted_amount", "requested_shares",
            "confirmed_shares", "nav_date", "nav_value", "fee", "gross_amount", "net_amount",
            "rounding_delta", "submitted_at", "cancel_deadline_at", "confirmation_due_date",
            "confirmation_due_at", "confirmed_at", "payment_due_date", "payment_due_at", "paid_at",
            "nav_observed_at", "run_id", "rule_id", "created_at", "updated_at",
        )
        view = {key: row[key] for key in keys}
        view["name"] = names.get(row["code"])
        return view

    # ---- 账本重放核对 -----------------------------------------------------------

    def verify(self) -> list[str]:
        """由账本重放核对余额、冻结、份额投影与订单状态；返回发现的问题。"""
        problems: list[str] = []
        with self.store.read() as conn:
            for account in conn.execute("SELECT id FROM accounts").fetchall():
                account_id = account["id"]
                balances = self.ledger_balances(conn, account_id)
                for name, value in vars(balances).items():
                    if value < 0:
                        problems.append(f"{account_id}: {name} 为负 {value}")
                deposits = conn.execute(
                    "SELECT COUNT(*) FROM ledger_events WHERE account_id = ? AND event_type = 'initial_deposit'",
                    (account_id,),
                ).fetchone()[0]
                if deposits != 1:
                    problems.append(f"{account_id}: 期初入金 {deposits} 条")
                frozen = sum(self._open_buys(conn, account_id).values(), ZERO)
                if frozen != balances.frozen:
                    problems.append(f"{account_id}: 冻结现金 {balances.frozen} ≠ 未确认申购 {frozen}")
                receivable = sum(
                    (Decimal(row["net_amount"]) for row in conn.execute(
                        "SELECT net_amount FROM orders WHERE account_id = ? AND status = 'awaiting_payment'",
                        (account_id,),
                    )),
                    ZERO,
                )
                if receivable != balances.receivable:
                    problems.append(f"{account_id}: 赎回应收 {balances.receivable} ≠ 待到账 {receivable}")
                dividend = sum(
                    (Decimal(row["net"]) for row in conn.execute(
                        "SELECT net FROM dividends WHERE account_id = ? AND status = 'ex_recorded'",
                        (account_id,),
                    )),
                    ZERO,
                )
                if dividend != balances.dividend:
                    problems.append(f"{account_id}: 分红应收 {balances.dividend} ≠ 已除息未发放 {dividend}")
                for lot in conn.execute("SELECT * FROM lots WHERE account_id = ?", (account_id,)).fetchall():
                    ledger = sum(
                        (Decimal(row["shares_delta"]) for row in conn.execute(
                            "SELECT shares_delta FROM ledger_events WHERE lot_id = ?", (lot["id"],)
                        )),
                        ZERO,
                    )
                    remaining = Decimal(lot["remaining_shares"])
                    if ledger != remaining:
                        problems.append(f"{lot['id']}: 剩余份额 {remaining} ≠ 账本 {ledger}")
                    frozen_shares = sum(
                        (Decimal(row["shares"]) for row in conn.execute(
                            f"""
                            SELECT a.shares FROM sell_allocations a JOIN orders o ON o.id = a.order_id
                            WHERE a.lot_id = ? AND o.status IN ({_marks(FREEZING_STATUSES)})
                            """,
                            (lot["id"], *FREEZING_STATUSES),
                        )),
                        ZERO,
                    )
                    if frozen_shares != Decimal(lot["frozen_shares"]):
                        problems.append(f"{lot['id']}: 冻结份额 {lot['frozen_shares']} ≠ 未确认赎回 {frozen_shares}")
                    if not ZERO <= Decimal(lot["frozen_shares"]) <= remaining:
                        problems.append(f"{lot['id']}: 冻结份额越界")
                missing_lots = conn.execute(
                    """
                    SELECT COUNT(*) FROM orders o LEFT JOIN lots l ON l.buy_order_id = o.id
                    WHERE o.account_id = ? AND o.side = 'buy' AND o.status = 'settled' AND l.id IS NULL
                    """,
                    (account_id,),
                ).fetchone()[0]
                if missing_lots:
                    problems.append(f"{account_id}: {missing_lots} 笔已确认申购缺少批次")
        return problems


def _marks(values: Iterable[Any] | Mapping[str, Any]) -> str:
    return ", ".join("?" for _ in values)
