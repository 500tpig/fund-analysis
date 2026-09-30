from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Mapping, Sequence, TypedDict

from ..fund_metadata import FundMetadata
from ..nav_status import resolve_nav_status
from ..today_profit import (
    beijing_today,
    build_monitoring_profit,
    build_row_profit_fields,
    build_session_profit,
    build_today_summary,
    cny_money,
    holding_profit_quality,
    official_history_quality,
    parse_iso_date,
    resolve_display_session_target,
)


class ExpectedSourceJob(TypedDict):
    source_key: str
    job_type: str
    label: str
    stale_after_days: int


EXPECTED_SOURCE_JOBS: tuple[ExpectedSourceJob, ...] = (
    {
        "source_key": "eastmoney_fundmobapi",
        "job_type": "fund_market_snapshot",
        "label": "官方基金净值",
        "stale_after_days": 7,
    },
    {
        "source_key": "fund_intraday_composite",
        "job_type": "fund_intraday_estimate",
        "label": "盘中估算",
        "stale_after_days": 0,
    },
)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _money(value: float | None) -> float | None:
    return round(value, 2) if value is not None else None


def _cny_money(value: Any) -> float | None:
    return cny_money(value)


def _percent(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _iso_or_raw(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return value


def _date_or_none(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def build_data_freshness_api_payload(
    latest_runs: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    current_date = today or date.today()
    by_key = {
        (str(run.get("source_key") or ""), str(run.get("job_type") or "")): run
        for run in latest_runs
    }
    sources: list[dict[str, Any]] = []
    for expected in EXPECTED_SOURCE_JOBS:
        run = by_key.get((expected["source_key"], expected["job_type"]))
        if run is None:
            freshness_status = "missing"
        elif run.get("status") == "failed":
            freshness_status = "provider_failed"
        elif run.get("status") == "partial":
            freshness_status = "partial"
        else:
            data_date = _date_or_none(run.get("max_data_date"))
            stale_after = timedelta(days=int(expected["stale_after_days"]))
            freshness_status = (
                "fresh"
                if data_date is not None and current_date - data_date <= stale_after
                else "stale"
            )
        sources.append(
            {
                "source_key": expected["source_key"],
                "job_type": expected["job_type"],
                "label": expected["label"],
                "freshness_status": freshness_status,
                "run_status": run.get("status") if run else None,
                "completed_at": _iso_or_raw(run.get("completed_at")) if run else None,
                "max_data_date": run.get("max_data_date") if run else None,
                "row_count": run.get("row_count") if run else None,
                "error_message": run.get("error_message") if run else None,
            }
        )
    statuses = {source["freshness_status"] for source in sources}
    if statuses == {"fresh"}:
        overall_status = "fresh"
    elif "provider_failed" in statuses:
        overall_status = "provider_failed"
    elif "partial" in statuses:
        overall_status = "partial"
    elif "stale" in statuses:
        overall_status = "stale"
    else:
        overall_status = "missing"
    return {
        "schema_version": "data_freshness.v1",
        "overall_status": overall_status,
        "sources": sources,
    }


def _holding_profit_amount(holding: Mapping[str, Any]) -> float | None:
    amount = _float_or_none(holding.get("amount"))
    cost = _float_or_none(holding.get("cost"))
    rate = _float_or_none(holding.get("holding_profit_rate"))
    if amount is not None and cost is not None:
        return amount - cost
    if amount is not None and rate is not None and rate != -100:
        return amount * rate / (100 + rate)
    return None


def _holding_profit_rate(
    holding: Mapping[str, Any],
    profit_amount: float | None,
) -> float | None:
    rate = _float_or_none(holding.get("holding_profit_rate"))
    if rate is not None:
        return rate
    cost = _float_or_none(holding.get("cost"))
    if cost is not None and cost != 0 and profit_amount is not None:
        return profit_amount / cost * 100
    return None


def is_cash_asset(holding: Mapping[str, Any]) -> bool:
    marker = str(holding.get("asset_type") or "").strip().lower().replace("-", "_")
    if marker in {"cash", "yuebao", "yu_e_bao", "现金", "余额宝"}:
        return True
    code = str(holding.get("code") or "").strip().upper()
    name = str(holding.get("name") or "").strip()
    asset_class = str(holding.get("asset_class") or "").strip()
    return code.startswith("CASH:") or name in {"现金", "余额宝"} or asset_class in {
        "现金",
        "余额宝",
        "CASH",
    }


def is_fund_asset(holding: Mapping[str, Any]) -> bool:
    return not is_cash_asset(holding)


def _value_state(value: float | None) -> str:
    if value is None or value == 0:
        return "muted"
    return "rise" if value > 0 else "fall"


def _latest_refresh(rows: Mapping[str, Mapping[str, Any]], *keys: str) -> str | None:
    values = [
        str(row.get(key))
        for row in rows.values()
        for key in keys
        if row.get(key)
    ]
    return max(values) if values else None


def _first_warning_message(row: Mapping[str, Any] | None) -> str | None:
    if not row:
        return None
    try:
        warnings = json.loads(str(row.get("warning_json") or "[]"))
    except json.JSONDecodeError:
        return None
    if not isinstance(warnings, list) or not warnings:
        return None
    first = warnings[0]
    if isinstance(first, Mapping):
        message = first.get("message")
        return str(message) if message else None
    return str(first) if first else None


def estimate_failures_from_runs(latest_runs: list[dict[str, Any]]) -> dict[str, str]:
    for run in latest_runs:
        if str(run.get("job_type") or "") != "fund_intraday_estimate":
            continue
        raw = run.get("evidence_json") or run.get("evidence") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        if not isinstance(raw, Mapping):
            return {}
        failures = raw.get("failures") or {}
        if not isinstance(failures, Mapping):
            return {}
        return {
            str(code): str(error)
            for code, error in failures.items()
            if str(error or "").strip()
        }
    return {}


def snapshot_failures_from_runs(latest_runs: list[dict[str, Any]]) -> dict[str, str]:
    for run in latest_runs:
        if str(run.get("job_type") or "") != "fund_market_snapshot":
            continue
        raw = run.get("evidence_json") or run.get("evidence") or {}
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = {}
        if not isinstance(raw, Mapping):
            return {}
        failures = raw.get("failures") or []
        if isinstance(failures, list):
            return {
                str(f.get("code")): str(f.get("error"))
                for f in failures
                if isinstance(f, Mapping) and f.get("code") and f.get("error")
            }
    return {}


def build_official_daily_profit_api_payload(
    code: str,
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    cumulative = Decimal("0.00")
    ordered = sorted(
        records,
        key=lambda row: str(row.get("nav_date") or ""),
    )
    for row in ordered:
        profit = _cny_money(row.get("profit_amount"))
        if profit is None:
            continue
        cumulative += Decimal(str(profit))
        nav_date = str(row.get("nav_date") or "")[:10]
        items.append(
            {
                "nav_date": nav_date,
                "holding_amount": _cny_money(row.get("holding_amount")),
                "official_nav": _float_or_none(row.get("official_nav")),
                "official_return_pct": _percent(_float_or_none(row.get("official_return_pct"))),
                "profit_amount": profit,
                "cumulative_profit": float(cumulative),
                "source": str(row.get("source") or ""),
                "recorded_at": _iso_or_raw(str(row.get("recorded_at") or "") or None),
                "basis_status": "unverified_legacy",
            }
        )
    latest = None
    if items:
        last = items[-1]
        latest = {
            "nav_date": last["nav_date"],
            "profit_amount": last["profit_amount"],
        }
    quality = official_history_quality(items)
    return {
        "schema_version": "fund_official_daily_profit.v1",
        "code": str(code or "").strip(),
        "record_count": len(items),
        "start_date": items[0]["nav_date"] if items else None,
        "end_date": items[-1]["nav_date"] if items else None,
        "latest": latest,
        "cumulative_profit": float(cumulative) if items else 0.0,
        "cumulative_kind": quality["cumulative_kind"],
        "observed_dates": quality["observed_dates"],
        "completeness": quality["completeness"],
        "basis_note": quality["basis_note"],
        "items": items,
    }


def _overseas_recent(
    estimate: Mapping[str, Any],
    *,
    is_current: bool,
    basis_status: str | None,
) -> dict[str, Any]:
    """海外最近一次来源估值；只展示，不参与今日合计。

    ``previous_nav_date`` 是来源给出的涨跌基准净值日，不代表收益覆盖期间。
    """
    previous_nav_date = parse_iso_date(estimate.get("previous_nav_date"))
    return {
        "return_pct": _percent(_float_or_none(estimate.get("estimated_return_pct"))),
        "estimated_nav": _float_or_none(estimate.get("estimated_nav")),
        "estimated_at": estimate.get("estimated_at"),
        "source": estimate.get("source"),
        "previous_nav": _float_or_none(estimate.get("previous_nav")),
        "previous_nav_date": previous_nav_date.isoformat() if previous_nav_date else None,
        "basis_status": basis_status,
        "is_current": is_current,
    }


def build_holdings_api_payload(
    holdings: list[dict[str, Any]],
    snapshots_by_code: dict[str, dict[str, Any]],
    estimates_by_code: dict[str, dict[str, Any]] | None = None,
    *,
    today: date | None = None,
    as_of: datetime | None = None,
    estimate_failures_by_code: Mapping[str, str] | None = None,
    snapshot_failures_by_code: Mapping[str, str] | None = None,
    accuracy_by_code: Mapping[str, dict[str, Any]] | None = None,
    metadata_by_code: Mapping[str, Mapping[str, Any] | FundMetadata] | None = None,
    official_daily_profits_by_code: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    estimates_by_code = estimates_by_code or {}
    estimate_failures_by_code = dict(estimate_failures_by_code or {})
    snapshot_failures_by_code = dict(snapshot_failures_by_code or {})
    accuracy_by_code = dict(accuracy_by_code or {})
    metadata_by_code = dict(metadata_by_code or {})
    current_date = today or beijing_today()
    total_amount = sum(_float_or_none(row.get("amount")) or 0 for row in holdings)
    rows: list[dict[str, Any]] = []
    current_estimate_times: list[str] = []
    totals = {
        "holding": Decimal("0.00"),
        "cumulative": Decimal("0.00"),
        "year": Decimal("0.00"),
    }
    present = {key: False for key in totals}

    for holding in holdings:
        code = str(holding.get("code") or "")
        metadata_entry = metadata_by_code.get(code)
        metadata = (
            metadata_entry.to_dict()
            if isinstance(metadata_entry, FundMetadata)
            else (dict(metadata_entry) if metadata_entry else None)
        )
        is_cash_type = bool(metadata) and str(metadata.get("fund_type") or "").strip().lower() == "cash"
        cash = is_cash_asset(holding) or is_cash_type
        amount = _float_or_none(holding.get("amount"))
        weight_pct = _float_or_none(holding.get("weight_pct"))
        if weight_pct is None and amount is not None and total_amount > 0:
            weight_pct = amount / total_amount * 100
        snapshot = snapshots_by_code.get(code)
        snapshot_err = snapshot_failures_by_code.get(code)
        nav_status_result = resolve_nav_status(
            metadata=metadata,
            nav_date=snapshot.get("nav_date") if snapshot else None,
            as_of=as_of,
            snapshot_failed=bool(snapshot_err),
            snapshot_error=snapshot_err,
            is_cash=cash,
        )
        estimate = estimates_by_code.get(code)
        estimate_is_current = bool(
            estimate and _date_or_none(estimate.get("estimated_at")) == current_date
        )
        daily_return_pct = _float_or_none(snapshot.get("daily_return_pct") if snapshot else None)
        estimated_return_pct = _float_or_none(
            estimate.get("estimated_return_pct") if estimate_is_current and estimate else None
        )
        estimated_nav = _float_or_none(
            estimate.get("estimated_nav") if estimate_is_current and estimate else None
        )
        profit_fields = build_row_profit_fields(
            holding,
            today=current_date,
            snapshot=snapshot,
            estimate=estimate,
            estimate_is_current=estimate_is_current,
            metadata=metadata,
            cash=cash,
        )
        imported_daily_profit = (
            profit_fields["imported_daily_profit"]["amount"]
            if profit_fields["imported_daily_profit"]
            else None
        )
        daily_profit = imported_daily_profit
        estimated_daily_profit = (
            profit_fields["estimate_scenario"]["amount"]
            if profit_fields["estimate_scenario"]
            else None
        )
        if estimate_is_current and estimate and estimate.get("estimated_at"):
            current_estimate_times.append(str(estimate["estimated_at"]))
        estimated_warning = _first_warning_message(estimate)
        if estimate and not estimate_is_current and estimated_warning is None:
            estimated_warning = "盘中估算不是今日数据，未计入估算收益"
        estimated_status = (
            None
            if estimate is None
            else (str(estimate.get("status") or "available") if estimate_is_current else "stale")
        )
        holding_profit = None if cash else _holding_profit_amount(holding)
        holding_profit_rate = _holding_profit_rate(holding, holding_profit)
        holding_quality = (
            {
                "holding_profit_amount_source": None,
                "holding_profit_rate_source": None,
                "holding_profit_conflict": False,
                "holding_profit_conflict_reason_codes": [],
            }
            if cash
            else holding_profit_quality(holding, holding_profit)
        )
        cumulative_profit = _cny_money(holding.get("cumulative_profit"))
        year_profit = _cny_money(holding.get("year_profit"))
        for key, value in (
            ("holding", holding_profit),
            ("cumulative", cumulative_profit),
            ("year", year_profit),
        ):
            if value is not None:
                present[key] = True
                totals[key] += Decimal(str(_cny_money(value)))

        refreshed_at = snapshot.get("fetched_at") or snapshot.get("updated_at") if snapshot else None
        accuracy_raw = accuracy_by_code.get(code)
        accuracy: dict[str, Any] | None = None
        if accuracy_raw and not cash:
            accuracy = {
                "source": accuracy_raw.get("source"),
                "sample_count": accuracy_raw.get("sample_count"),
                "pending_count": accuracy_raw.get("pending_count") or 0,
                "window": accuracy_raw.get("window"),
                "mae_pct_points": accuracy_raw.get("mae_pct_points"),
                "bias_pct_points": accuracy_raw.get("bias_pct_points"),
                "max_abs_error_pct_points": accuracy_raw.get("max_abs_error_pct_points"),
            }
        rows.append(
            {
                **{name: holding.get(name) for name in (
                    "id", "code", "name", "shares", "asset_type", "asset_class", "theme",
                )},
                "amount": _cny_money(amount),
                "weight_pct": _percent(weight_pct),
                "cost": _cny_money(_float_or_none(holding.get("cost"))),
                "amount_nav_date": profit_fields["amount_nav_date"],
                "amount_basis_status": profit_fields["amount_basis_status"],
                "amount_basis_source": profit_fields["amount_basis_source"],
                "daily_return_pct": _percent(daily_return_pct),
                "daily_return_state": _value_state(daily_return_pct),
                "daily_profit": _cny_money(daily_profit),
                "daily_profit_state": _value_state(daily_profit),
                "today_profit": profit_fields["today_profit"],
                "monitoring_profit": build_monitoring_profit(
                    profit_fields, snapshot=snapshot, estimate=estimate, today=current_date,
                ),
                "overseas_recent": (
                    _overseas_recent(
                        estimate,
                        is_current=estimate_is_current,
                        basis_status=profit_fields["estimate_basis_status"],
                    )
                    if estimate and "estimate_return_date_unverified" in profit_fields["estimate_basis_reason_codes"]
                    else None
                ),
                "official_scenario": profit_fields["official_scenario"],
                "estimate_scenario": profit_fields["estimate_scenario"],
                "imported_daily_profit": profit_fields["imported_daily_profit"],
                "estimated_nav": estimated_nav,
                "estimated_return_pct": _percent(estimated_return_pct),
                "estimated_return_state": _value_state(estimated_return_pct),
                "estimated_daily_profit": _cny_money(estimated_daily_profit),
                "estimated_daily_profit_state": _value_state(estimated_daily_profit),
                "estimate_basis_status": profit_fields["estimate_basis_status"],
                "estimate_basis_reason_codes": profit_fields["estimate_basis_reason_codes"],
                "estimated_at": estimate.get("estimated_at") if estimate else None,
                "estimated_source": estimate.get("source") if estimate else None,
                "estimated_status": estimated_status,
                "estimated_warning": estimated_warning,
                "estimated_unavailable_reason": (
                    None
                    if cash or estimate is not None
                    else (estimate_failures_by_code.get(code) or None)
                ),
                "holding_profit": _cny_money(holding_profit),
                "holding_profit_state": _value_state(holding_profit),
                "holding_profit_rate": _percent(holding_profit_rate),
                "holding_profit_rate_state": _value_state(holding_profit_rate),
                **holding_quality,
                "cumulative_profit": _cny_money(cumulative_profit),
                "cumulative_profit_state": _value_state(cumulative_profit),
                "year_profit": _cny_money(year_profit),
                "year_profit_state": _value_state(year_profit),
                "is_cash_asset": cash,
                "is_fund_asset": not cash,
                "latest_nav": _float_or_none(snapshot.get("latest_nav") if snapshot else None),
                "nav_date": snapshot.get("nav_date") if snapshot else None,
                "fetched_at": _iso_or_raw(str(refreshed_at) if refreshed_at else None),
                "market_status": "cash" if cash else ("fresh" if snapshot else "missing"),
                "nav_update_status": nav_status_result.status,
                "nav_update_reason": nav_status_result.reason,
                "target_nav_date": nav_status_result.target_nav_date,
                "next_disclosure_date": nav_status_result.next_disclosure_date,
                "in_disclosure_window": nav_status_result.in_disclosure_window,
                "auto_refresh_eligible": nav_status_result.auto_refresh_eligible,
                "fund_type": metadata.get("fund_type") if metadata else None,
                "fund_type_label": (
                    metadata.get("fund_type_label")
                    if metadata
                    else (holding.get("asset_class") or holding.get("asset_type"))
                ),
                "valuation_market": metadata.get("market") if metadata else None,
                "market_calendar": metadata.get("calendar") if metadata else None,
                "nav_disclosure_rule": metadata.get("nav_disclosure_rule") if metadata else None,
                "benchmark_name": metadata.get("benchmark_name") if metadata else None,
                "benchmark_code": metadata.get("benchmark_code") if metadata else None,
                "source_url": metadata.get("source_url") if metadata else None,
                "verified_at": metadata.get("verified_at") if metadata else None,
                "accuracy": accuracy,
            }
        )

    fund_rows = [row for row in holdings if is_fund_asset(row)]
    cash_rows = [row for row in holdings if is_cash_asset(row)]
    display_fund_rows = [row for row in rows if row.get("is_fund_asset")]

    monitoring_count = sum(
        1 for row in display_fund_rows
        if (row.get("monitoring_profit") or {}).get("amount") is not None
    )
    mode, display_date, is_today = resolve_display_session_target(
        display_fund_rows, today=current_date, monitoring_count=monitoring_count,
    )
    for row in rows:
        row["session_profit"] = build_session_profit(
            row,
            mode=mode,
            display_date=display_date,
            official_profit_records=official_daily_profits_by_code,
        )

    today_summary = build_today_summary(rows, today=current_date)
    imported_total = today_summary["imported_undated"]["profit"]
    estimated_total = today_summary["estimate_scenario"]["profit"]
    summary = {
        "holding_count": len(rows),
        "fund_holding_count": len(fund_rows),
        "cash_holding_count": len(cash_rows),
        "total_amount": _cny_money(total_amount),
        "fund_amount": _cny_money(sum(_float_or_none(row.get("amount")) or 0 for row in fund_rows)),
        "cash_amount": _cny_money(sum(_float_or_none(row.get("amount")) or 0 for row in cash_rows)),
        "latest_refresh": _iso_or_raw(
            _latest_refresh(snapshots_by_code, "fetched_at", "updated_at")
        ),
        "latest_intraday_refresh": _iso_or_raw(
            max(current_estimate_times) if current_estimate_times else None
        ),
        "return_date": today_summary["return_date"],
        "today": today_summary,
        "total_daily_profit": imported_total,
        "total_daily_profit_state": _value_state(imported_total),
        "total_estimated_daily_profit": estimated_total,
        "total_estimated_daily_profit_state": _value_state(estimated_total),
    }
    for key, response_key in (
        ("holding", "total_holding_profit"),
        ("cumulative", "total_cumulative_profit"),
        ("year", "total_year_profit"),
    ):
        value = float(totals[key]) if present[key] else None
        summary[response_key] = _cny_money(value)
        summary[f"{response_key}_state"] = _value_state(value)
    return {"rows": rows, "summary": summary}


def _same_number(left: Any, right: Any) -> bool:
    if left is None and right is None:
        return True
    left_number = _float_or_none(left)
    right_number = _float_or_none(right)
    if left_number is None or right_number is None:
        return left_number is None and right_number is None
    return abs(left_number - right_number) < 0.005


def _same_text(left: Any, right: Any) -> bool:
    return (str(left).strip() if left is not None else "") == (
        str(right).strip() if right is not None else ""
    )


def _preview_size(holding: Any) -> str:
    amount = getattr(holding, "amount", None)
    if amount is None and isinstance(holding, Mapping):
        amount = holding.get("amount")
    if amount is not None:
        return str(amount)
    weight = getattr(holding, "reported_weight_pct", None)
    if weight is None and isinstance(holding, Mapping):
        weight = holding.get("weight_pct")
    return "" if weight is None else str(weight)


def _incoming_matches_current(current: Mapping[str, Any], incoming: Any) -> bool:
    return (
        _same_text(current.get("name"), incoming.name)
        and _same_number(current.get("amount"), incoming.amount)
        and _same_number(current.get("cost"), incoming.cost)
        and _same_number(current.get("weight_pct"), incoming.reported_weight_pct)
        and _same_text(current.get("theme"), incoming.theme)
        and _same_text(current.get("asset_type"), incoming.asset_type)
        and _same_text(current.get("asset_class"), incoming.asset_class)
        and _same_number(current.get("holding_profit_rate"), incoming.holding_profit_rate)
        and _same_number(current.get("daily_profit"), incoming.daily_profit)
        and _same_number(current.get("cumulative_profit"), incoming.cumulative_profit)
        and _same_number(current.get("year_profit"), incoming.year_profit)
        and _same_text(current.get("amount_nav_date"), incoming.amount_nav_date)
    )


def build_import_preview_payload(
    incoming: list[Any],
    current: Sequence[Mapping[str, Any]],
    *,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    current_by_code = {
        str(row.get("code") or "").strip(): row
        for row in current
        if str(row.get("code") or "").strip()
    }
    csv_codes: set[str] = set()
    rows: list[dict[str, Any]] = []
    for index, holding in enumerate(incoming):
        code = str(holding.code).strip()
        csv_codes.add(code)
        existing = current_by_code.get(code)
        if existing is None:
            action = "add"
        elif _incoming_matches_current(existing, holding):
            action = "keep"
        else:
            action = "update"
        rows.append(
            {
                "line": index + 2,
                "code": code,
                "name": holding.name,
                "amount": _preview_size(holding),
                "cost": "" if holding.cost is None else str(holding.cost),
                "theme": holding.theme or "",
                "action": action,
            }
        )
    removals = [
        {
            "line": 0,
            "code": str(row.get("code") or ""),
            "name": str(row.get("name") or ""),
            "amount": "" if row.get("amount") is None else str(row.get("amount")),
            "cost": "" if row.get("cost") is None else str(row.get("cost")),
            "theme": str(row.get("theme") or ""),
            "action": "remove",
        }
        for row in current
        if str(row.get("code") or "").strip()
        and str(row.get("code") or "").strip() not in csv_codes
    ]
    all_rows = [*rows, *removals]
    return {
        "rows": all_rows,
        "warnings": list(warnings or []),
        "valid_count": len(rows),
        "add_count": sum(1 for row in all_rows if row["action"] == "add"),
        "update_count": sum(1 for row in all_rows if row["action"] == "update"),
        "keep_count": sum(1 for row in all_rows if row["action"] == "keep"),
        "remove_count": len(removals),
    }
