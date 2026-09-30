from __future__ import annotations

import threading
from datetime import date, datetime
from math import isfinite
from pathlib import Path
from typing import Any, Mapping, Sequence

from fastapi import APIRouter, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from ..collection import collection_slot
from ..fund_data import (
    CompositeFundEstimateProvider,
    EastmoneyFundDataProvider,
    FundCurveProvider,
    FundDataFetchError,
    FundSearchHit,
    search_public_funds,
)
from ..fund_metadata import RULE_CN_SAME_DAY_NIGHT
from ..market_quotes import (
    MARKET_QUOTE_ORDER,
    EastmoneyMarketQuoteProvider,
    build_market_quotes_payload,
)
from ..portfolio_analysis import (
    build_portfolio_analysis,
    build_portfolio_risk,
    fetch_nav_histories,
)
from .api_payloads import (
    build_data_freshness_api_payload,
    build_holdings_api_payload,
    build_official_daily_profit_api_payload,
    estimate_failures_from_runs,
    is_fund_asset,
    snapshot_failures_from_runs,
)
from .defaults import DEFAULT_DB_PATH
from .holdings_entry import (
    MAX_IMAGE_BYTES,
    MAX_IMAGE_COUNT,
    HoldingsRecognitionError,
    StructuredHoldingsRecognizer,
    build_entry_preview,
    compress_image,
    recognize_candidates,
    validate_entry_request,
)
from .legacy_redirects import register_legacy_redirects
from .storage import LocalStorage


WEB_DIR = Path(__file__).parent
PROJECT_ROOT = WEB_DIR.resolve().parents[2]
DIST_DIR = PROJECT_ROOT / "frontend" / "dist"
router = APIRouter()
HOLDING_INPUT_FIELDS = frozenset(
    {
        "code",
        "name",
        "amount",
        "weight_pct",
        "cost",
        "asset_type",
        "asset_class",
        "theme",
        "amount_nav_date",
    }
)


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _required_text(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field_name} is required")
    return text


def _optional_float(value: Any, field_name: str) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not isfinite(number):
        raise ValueError(f"{field_name} must be finite")
    return number


def _holding_data_from_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    unexpected = sorted(set(payload) - HOLDING_INPUT_FIELDS)
    if unexpected:
        raise ValueError(f"unsupported holding fields: {', '.join(unexpected)}")
    data: dict[str, Any] = {
        "code": _required_text(payload.get("code"), "code"),
        "name": _required_text(payload.get("name"), "name"),
    }
    for field_name in (
        "amount",
        "weight_pct",
        "cost",
    ):
        data[field_name] = _optional_float(payload.get(field_name), field_name)
    for field_name in ("amount", "weight_pct", "cost"):
        if data[field_name] is not None and data[field_name] < 0:
            raise ValueError(f"{field_name} must be greater than or equal to 0")
    if data["weight_pct"] is not None and data["weight_pct"] > 100:
        raise ValueError("weight_pct must be less than or equal to 100")
    for field_name in (
        "asset_type",
        "asset_class",
        "theme",
    ):
        data[field_name] = _optional_text(payload.get(field_name))
    if "amount_nav_date" in payload:
        date_text = _optional_text(payload.get("amount_nav_date"))
        if date_text is None:
            data["amount_nav_date"] = None
        else:
            try:
                data["amount_nav_date"] = date.fromisoformat(date_text[:10]).isoformat()
            except ValueError as exc:
                raise ValueError("amount_nav_date must be YYYY-MM-DD") from exc
    return data


async def _json_payload(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Request body must be JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be an object")
    return payload


def _get_storage(request: Request) -> LocalStorage:
    storage = request.app.state.storage
    if storage is None:
        storage = LocalStorage(request.app.state.db_path)
        request.app.state.storage = storage
    return storage


def _build_funds_api_payload(storage: LocalStorage) -> dict[str, Any]:
    latest_runs = storage.get_latest_data_source_runs()
    all_holdings = storage.get_all_holdings()
    estimates_by_code = storage.get_fund_intraday_estimates_by_code()
    estimate_sources_by_code = {
        code: str(estimate.get("source") or "").strip()
        for code, estimate in estimates_by_code.items()
        if str(estimate.get("source") or "").strip()
    }
    payload = build_holdings_api_payload(
        all_holdings,
        storage.get_fund_market_snapshots_by_code(),
        estimates_by_code,
        estimate_failures_by_code=estimate_failures_from_runs(latest_runs),
        snapshot_failures_by_code=snapshot_failures_from_runs(latest_runs),
        accuracy_by_code=storage.get_accuracy_summaries_by_code(estimate_sources_by_code),
        metadata_by_code=storage.get_all_fund_metadata(),
        official_daily_profits_by_code=storage.get_official_daily_profit_map(),
    )
    payload["data_freshness"] = build_data_freshness_api_payload(latest_runs)
    payload["portfolio_analysis"] = build_portfolio_analysis(payload["rows"])
    payload["market_quotes"] = build_market_quotes_payload(
        storage.get_market_quote_snapshots(),
        latest_runs,
    )
    return payload


def _run_status(success_count: int, failure_count: int) -> str:
    if failure_count == 0:
        return "success"
    return "partial" if success_count else "failed"


def _date_prefix(value: Any) -> str | None:
    text = str(value or "").strip()
    return text[:10] if len(text) >= 10 else None


def _summarize_failures(failures: Sequence[Mapping[str, Any]]) -> str | None:
    if not failures:
        return None
    return "; ".join(
        f"{failure.get('code')}: {failure.get('error')}" for failure in failures[:5]
    )


def _refresh_funds(
    storage: LocalStorage,
    market_provider: Any,
    estimate_provider: Any,
) -> dict[str, Any]:
    started_at = datetime.now().astimezone().isoformat()
    today_date = datetime.now().astimezone().strftime("%Y-%m-%d")
    success_codes: list[str] = []
    estimate_success_codes: list[str] = []
    failures: list[dict[str, str]] = []
    estimate_failures: list[dict[str, str]] = []
    nav_dates: list[str] = []
    estimate_dates: list[str] = []

    holdings = storage.get_all_holdings()
    holding_amount_by_code: dict[str, Any] = {}
    for holding in holdings:
        code = str(holding.get("code") or "").strip()
        if not code or not is_fund_asset(holding):
            continue
        holding_amount_by_code.setdefault(code, holding.get("amount"))
    metadata_by_code = storage.get_all_fund_metadata()
    codes = list(holding_amount_by_code)
    same_day_nav_codes = {
        code
        for code in codes
        if str(
            (metadata_by_code.get(code) or {}).get("nav_disclosure_rule") or ""
        ).strip()
        == RULE_CN_SAME_DAY_NIGHT
    }
    market_data_by_code: dict[str, Any] = {}
    for code in codes:
        try:
            market_data = market_provider.fetch_without_quarterly_holdings(code)
            storage.save_fund_market_snapshot(market_data)
            storage.record_official_daily_profit(
                market_data,
                holding_amount_by_code.get(code),
            )
            market_data_by_code[code] = market_data
            success_codes.append(code)
            if market_data.nav_date:
                nav_dates.append(str(market_data.nav_date)[:10])
        except Exception as exc:
            failures.append({"code": code, "error": str(exc)})

    try:
        batch = estimate_provider.fetch_many(codes)
        batch_estimates = batch.estimates
        batch_failures = batch.failures
        batch_evidence = batch.evidence
    except Exception as exc:
        error = f"estimate provider failed: {exc}"
        batch_estimates = {}
        batch_failures = {code: error for code in codes}
        batch_evidence = {"success_codes_by_source": {}, "failed_codes": codes}
    for code in codes:
        estimate = batch_estimates.get(code)
        if estimate is not None:
            storage.save_fund_intraday_estimate(estimate)
            estimate_date = _date_prefix(estimate.estimated_at)
            if estimate_date == today_date:
                storage.upsert_estimate_daily_sample(
                    estimate,
                    today_date,
                    allow_same_day_target=code in same_day_nav_codes,
                )
            estimate_success_codes.append(code)
            if estimate_date:
                estimate_dates.append(estimate_date)
        else:
            estimate_failures.append(
                {
                    "code": code,
                    "error": batch_failures.get(code, "盘中估值来源未返回该基金"),
                }
            )

    for code, market_data in market_data_by_code.items():
        if (
            market_data.nav_date
            and market_data.daily_return_pct is not None
            and market_data.latest_nav is not None
        ):
            storage.reconcile_accuracy_samples(
                code,
                market_data.latest_nav,
                market_data.daily_return_pct,
                str(market_data.nav_date)[:10],
            )

    completed_at = datetime.now().astimezone().isoformat()
    storage.record_data_source_run(
        source_key="eastmoney_fundmobapi",
        job_type="fund_market_snapshot",
        status=_run_status(len(success_codes), len(failures)),
        started_at=started_at,
        completed_at=completed_at,
        fetched_at=completed_at,
        min_data_date=min(nav_dates) if nav_dates else None,
        max_data_date=max(nav_dates) if nav_dates else None,
        row_count=len(success_codes),
        error_message=_summarize_failures(failures),
        evidence={
            "success_codes": success_codes,
            "failed_codes": [row["code"] for row in failures],
            "failures": failures,
        },
    )
    estimate_evidence = dict(batch_evidence)
    estimate_evidence["failures"] = {
        str(row["code"]): str(row["error"]) for row in estimate_failures
    }
    storage.record_data_source_run(
        source_key="fund_intraday_composite",
        job_type="fund_intraday_estimate",
        status=_run_status(len(estimate_success_codes), len(estimate_failures)),
        started_at=started_at,
        completed_at=completed_at,
        fetched_at=completed_at,
        min_data_date=min(estimate_dates) if estimate_dates else None,
        max_data_date=max(estimate_dates) if estimate_dates else None,
        row_count=len(estimate_success_codes),
        error_message=_summarize_failures(estimate_failures),
        evidence=estimate_evidence,
    )
    return {
        "success": len(success_codes),
        "failure": len(failures),
        "failed_codes": [row["code"] for row in failures],
        "failures": failures,
        "estimate_success": len(estimate_success_codes),
        "estimate_failure": len(estimate_failures),
        "estimate_failed_codes": [row["code"] for row in estimate_failures],
        "estimate_failures": estimate_failures,
    }


def _refresh_market_quotes(storage: LocalStorage, provider: Any) -> dict[str, Any]:
    started_at = datetime.now().astimezone().isoformat()
    try:
        batch = provider.fetch_all()
        quotes = batch.quotes
        failures = dict(batch.failures)
    except Exception as exc:
        quotes = {}
        failures = {symbol: str(exc) for symbol in MARKET_QUOTE_ORDER}

    for quote in quotes.values():
        storage.save_market_quote_snapshot(quote)

    completed_at = datetime.now().astimezone().isoformat()
    failure_rows = [
        {"code": symbol, "error": error}
        for symbol, error in failures.items()
    ]
    quote_dates = [quote.trade_date for quote in quotes.values()]
    storage.record_data_source_run(
        source_key=str(getattr(provider, "name", "market_quote_provider")),
        job_type="market_quote",
        status=_run_status(len(quotes), len(failures)),
        started_at=started_at,
        completed_at=completed_at,
        fetched_at=completed_at,
        min_data_date=min(quote_dates) if quote_dates else None,
        max_data_date=max(quote_dates) if quote_dates else None,
        row_count=len(quotes),
        error_message=_summarize_failures(failure_rows),
        evidence={
            "success_symbols": list(quotes),
            "failures": failures,
        },
    )
    return {
        "quote_success": len(quotes),
        "quote_failure": len(failures),
        "quote_failures": failure_rows,
    }


@router.get("/api/holdings", response_class=JSONResponse)
async def api_holdings(request: Request) -> dict[str, Any]:
    return _build_funds_api_payload(_get_storage(request))


@router.post("/api/holdings", response_class=JSONResponse, status_code=201)
async def api_create_holding(request: Request) -> dict[str, Any]:
    storage = _get_storage(request)
    try:
        data = _holding_data_from_mapping(await _json_payload(request))
        holding_id = storage.add_holding(data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": holding_id}


@router.put("/api/holdings/{holding_id}", response_class=JSONResponse)
async def api_update_holding(request: Request, holding_id: int) -> dict[str, Any]:
    storage = _get_storage(request)
    try:
        data = _holding_data_from_mapping(await _json_payload(request))
        storage.update_holding(holding_id, data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Holding not found") from exc
    return {"id": holding_id}


@router.delete("/api/holdings/{holding_id}", response_class=JSONResponse)
async def api_delete_holding(request: Request, holding_id: int) -> dict[str, Any]:
    try:
        _get_storage(request).delete_holding(holding_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Holding not found") from exc
    return {"id": holding_id, "deleted": True}


@router.get("/api/funds", response_class=JSONResponse)
async def api_funds(request: Request) -> dict[str, Any]:
    return _build_funds_api_payload(_get_storage(request))


@router.get("/api/funds/search", response_class=JSONResponse)
def api_search_funds(
    request: Request,
    q: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(8, ge=1, le=20),
) -> dict[str, Any]:
    keyword = q.strip()
    if not keyword:
        raise HTTPException(status_code=400, detail="搜索关键字不能为空")
    try:
        hits = request.app.state.fund_searcher(keyword, limit=limit)
    except FundDataFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    items: list[dict[str, str]] = []
    for hit in hits:
        if isinstance(hit, FundSearchHit):
            items.append(hit.to_dict())
            continue
        if isinstance(hit, Mapping):
            code = str(hit.get("code") or "").strip()
            name = str(hit.get("name") or "").strip()
            if code and name:
                items.append({"code": code, "name": name})
    return {"query": keyword, "items": items}


@router.get("/api/funds/portfolio-risk", response_class=JSONResponse)
def api_portfolio_risk(
    request: Request,
    range_key: str = Query("1y", alias="range"),
) -> dict[str, Any]:
    if range_key not in FundCurveProvider._PERIOD_DAYS:
        raise HTTPException(status_code=400, detail=f"unsupported performance range: {range_key}")
    storage = _get_storage(request)
    rows = _build_funds_api_payload(storage)["rows"]
    codes = [
        str(row.get("code") or "").strip()
        for row in rows
        if row.get("is_fund_asset") and str(row.get("code") or "").strip()
    ]
    curve_provider = request.app.state.curve_provider

    def fetch_performance(code: str, period: str) -> Mapping[str, Any]:
        return curve_provider.fetch_performance(code, period, benchmark="")

    histories, failures = fetch_nav_histories(codes, fetch_performance, period=range_key)
    return build_portfolio_risk(rows, histories, failures, period=range_key)


@router.get("/api/funds/{code}/performance", response_class=JSONResponse)
def api_fund_performance(
    request: Request,
    code: str,
    range_key: str = Query("3m", alias="range"),
    benchmark: str = Query(""),
) -> dict[str, Any]:
    try:
        return request.app.state.curve_provider.fetch_performance(
            code,
            range_key,
            benchmark=benchmark,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FundDataFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/funds/{code}/intraday-curve", response_class=JSONResponse)
def api_fund_intraday_curve(request: Request, code: str) -> dict[str, Any]:
    try:
        return request.app.state.curve_provider.fetch_intraday(code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FundDataFetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/api/funds/{code}/official-profit", response_class=JSONResponse)
async def api_fund_official_profit(request: Request, code: str) -> dict[str, Any]:
    normalized = code.strip()
    records = _get_storage(request).get_official_daily_profit_records(normalized)
    return build_official_daily_profit_api_payload(normalized, records)


@router.post("/api/funds/refresh", response_class=JSONResponse)
def api_refresh_funds(request: Request, scheduled: bool = False) -> dict[str, Any]:
    return _refresh_all(request, coalesce=not scheduled, scheduled=scheduled)


def _refresh_all(
    request: Request, *, coalesce: bool = False, scheduled: bool = False
) -> dict[str, Any]:
    """Run one full refresh; concurrent callers never overlap upstream requests.

    With ``coalesce`` a caller that arrives during an in-flight refresh waits
    for it and reuses its result instead of fetching again. Holdings writes
    pass ``coalesce=False`` so their refresh always sees the new holdings.
    """
    lock: threading.Lock = request.app.state.refresh_lock
    if coalesce and not lock.acquire(blocking=False):
        with lock:
            last = request.app.state.last_refresh_result
        if last is not None:
            return {
                **last,
                "coalesced": True,
                "monitor": _build_funds_api_payload(_get_storage(request)),
            }
        lock.acquire()
    elif not coalesce:
        lock.acquire()
    try:
        if scheduled:
            slot = collection_slot(datetime.now().astimezone())
            if slot is None:
                return {"skipped": True, "message": "当前不在采集窗口"}
            # 定时采集在锁内参考包括网页刷新在内的记录去重；手动刷新不受槽限制。
            runs = _get_storage(request).get_latest_data_source_runs()
            if any(
                run["job_type"] == "fund_intraday_estimate"
                and run["source_key"] == "fund_intraday_composite"
                and datetime.fromisoformat(run["started_at"]) >= slot
                for run in runs
            ):
                return {"skipped": True, "message": "本时间槽已采集"}
        result = _run_refresh(request)
        request.app.state.last_refresh_result = {
            key: value for key, value in result.items() if key != "monitor"
        }
        return result
    finally:
        lock.release()


def _run_refresh(request: Request) -> dict[str, Any]:
    storage = _get_storage(request)
    result = _refresh_funds(
        storage,
        request.app.state.market_provider,
        request.app.state.estimate_provider,
    )
    result.update(
        _refresh_market_quotes(
            storage,
            request.app.state.market_quote_provider,
        )
    )
    result["has_failure"] = bool(
        result["failure"] or result["estimate_failure"] or result["quote_failure"]
    )
    result["message"] = (
        f"官方净值成功 {result['success']} 只、失败 {result['failure']} 只；"
        f"盘中估算成功 {result['estimate_success']} 只、失败 {result['estimate_failure']} 只；"
        f"市场行情成功 {result['quote_success']} 项、失败 {result['quote_failure']} 项。"
    )
    result["coalesced"] = False
    result["monitor"] = _build_funds_api_payload(storage)
    return result


@router.post("/api/holdings/entry/recognize", response_class=JSONResponse)
async def api_recognize_holdings(
    request: Request,
    images: list[UploadFile] | None = File(None),
) -> dict[str, Any]:
    if not images:
        raise HTTPException(status_code=400, detail="请选择 1 到 5 张持仓截图")
    if len(images) > MAX_IMAGE_COUNT:
        raise HTTPException(status_code=400, detail="一次最多识别 5 张截图")
    recognizer = request.app.state.holdings_recognizer
    if not getattr(recognizer, "configured", True):
        raise HTTPException(status_code=503, detail="未配置持仓识图服务")
    compressed: list[bytes] = []
    try:
        for image in images:
            data = await image.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                raise HoldingsRecognitionError(413, "单张图片不能超过 8 MB")
            compressed.append(compress_image(data))
        return recognize_candidates(compressed, recognizer, request.app.state.fund_searcher)
    except HoldingsRecognitionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    finally:
        for image in images:
            await image.close()


@router.post("/api/holdings/entry/preview", response_class=JSONResponse)
async def api_preview_holdings_entry(request: Request) -> dict[str, Any]:
    try:
        mode, items = validate_entry_request(await _json_payload(request))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return build_entry_preview(items, _get_storage(request).get_all_holdings(), mode)


@router.post("/api/holdings/entry/confirm", response_class=JSONResponse)
async def api_confirm_holdings_entry(request: Request) -> dict[str, Any]:
    payload = await _json_payload(request)
    try:
        mode, items = validate_entry_request(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    storage = _get_storage(request)
    preview = build_entry_preview(items, storage.get_all_holdings(), mode)
    expected_remove_codes = {
        str(row["code"]).strip().casefold()
        for row in preview["rows"]
        if row["action"] == "remove"
    }
    if expected_remove_codes and payload.get("confirm_remove") is not True:
        raise HTTPException(status_code=400, detail="替换全部需要确认待删除持仓")
    supplied_remove_codes = {
        str(code).strip().casefold()
        for code in payload.get("remove_codes", [])
        if str(code).strip()
    }
    if mode == "replace" and supplied_remove_codes != expected_remove_codes:
        raise HTTPException(status_code=409, detail="待删除持仓与当前数据不一致，请重新预览")
    try:
        counts = storage.apply_holdings_entry(items, mode)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="持仓写入失败，已回滚") from exc
    refreshed = _refresh_all(request)
    message = (
        f"已合并更新 {len(items)} 条持仓"
        if mode == "merge"
        else f"已替换 {len(items)} 条持仓"
    )
    return {
        "schema_version": "holdings_entry_confirm.v1",
        "mode": mode,
        "holdings_written": len(items),
        **counts,
        "message": message,
        "refresh": {
            "attempted": True,
            "has_failure": refreshed["has_failure"],
            "message": refreshed["message"],
        },
    }


register_legacy_redirects(router)


def create_app(
    db_path: Path | str = DEFAULT_DB_PATH,
    *,
    storage: LocalStorage | None = None,
    lazy_storage: bool = True,
    market_provider: Any | None = None,
    estimate_provider: Any | None = None,
    market_quote_provider: Any | None = None,
    curve_provider: Any | None = None,
    fund_searcher: Any | None = None,
    holdings_recognizer: Any | None = None,
) -> FastAPI:
    web_app = FastAPI(title="基金估值")
    web_app.state.db_path = Path(db_path)
    web_app.state.storage = storage
    web_app.state.market_provider = market_provider or EastmoneyFundDataProvider()
    web_app.state.estimate_provider = estimate_provider or CompositeFundEstimateProvider()
    web_app.state.market_quote_provider = (
        market_quote_provider or EastmoneyMarketQuoteProvider()
    )
    web_app.state.curve_provider = curve_provider or FundCurveProvider()
    web_app.state.fund_searcher = fund_searcher or search_public_funds
    web_app.state.holdings_recognizer = holdings_recognizer or StructuredHoldingsRecognizer()
    web_app.state.refresh_lock = threading.Lock()
    web_app.state.last_refresh_result = None
    if storage is None and not lazy_storage:
        web_app.state.storage = LocalStorage(web_app.state.db_path)
    web_app.include_router(router)
    index_path = DIST_DIR / "index.html"

    @web_app.post("/api/import", include_in_schema=False)
    @web_app.post("/api/import/preview", include_in_schema=False)
    async def removed_web_csv_import() -> None:
        raise HTTPException(status_code=404, detail="Not Found")

    @web_app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str) -> FileResponse:
        normalized = full_path.strip("/")
        if normalized == "api" or normalized.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        if normalized:
            candidate = (DIST_DIR / normalized).resolve()
            try:
                candidate.relative_to(DIST_DIR.resolve())
            except ValueError as exc:
                raise HTTPException(status_code=404, detail="Not Found") from exc
            if candidate.is_file():
                return FileResponse(candidate)
        if not index_path.is_file():
            raise HTTPException(status_code=500, detail="frontend dist index.html not found")
        return FileResponse(index_path)

    return web_app


app = create_app()
