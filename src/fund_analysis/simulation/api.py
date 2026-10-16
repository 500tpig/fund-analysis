"""`/api/simulation` 接口：账户、持仓与订单查询，以及暂停、恢复和撤单。

阶段 6 不提供 HTTP 下单入口；订单只由验收命令和后续固定策略通过服务层受理。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any, TypeVar

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from .service import SimulationError, SimulationService
from .store import SimulationConfigError, SimulationStore, StorageBusy, resolve_simulation_path

router = APIRouter(prefix="/api/simulation")
_init_lock = threading.Lock()
T = TypeVar("T")


def _error(status: int, code: str, message: str, fields: list[str] | None = None) -> HTTPException:
    return HTTPException(
        status_code=status, detail={"code": code, "message": message, "fields": fields or []}
    )


def _service(request: Request) -> SimulationService:
    service = request.app.state.simulation_service
    if service is not None:
        return service
    with _init_lock:
        if request.app.state.simulation_service is None:
            try:
                path = resolve_simulation_path(
                    request.app.state.simulation_db_path, request.app.state.db_path
                )
                request.app.state.simulation_service = SimulationService(SimulationStore(path))
            except SimulationConfigError as exc:
                raise _error(503, "simulation_unavailable", str(exc)) from exc
    return request.app.state.simulation_service


def _call(func: Callable[[], T]) -> T:
    try:
        return func()
    except SimulationError as exc:
        raise _error(exc.status, exc.code, exc.message, exc.fields) from exc
    except StorageBusy as exc:
        raise _error(503, "storage_busy", str(exc)) from exc


def _require_key(key: str | None) -> str:
    if not key:
        raise _error(422, "missing_idempotency_key", "写请求需要 Idempotency-Key 请求头", ["Idempotency-Key"])
    return key


async def _expected_version(request: Request) -> object:
    try:
        payload = await request.json()
    except Exception as exc:
        raise _error(422, "invalid_json", "请求体必须是 JSON 对象") from exc
    if not isinstance(payload, dict) or set(payload) != {"expected_version"}:
        raise _error(422, "invalid_request", "请求体只能包含 expected_version", ["expected_version"])
    return payload["expected_version"]


@router.get("/accounts")
def list_accounts(request: Request) -> dict[str, Any]:
    service = _service(request)
    return {"environment": service.store.environment, "items": _call(service.list_accounts)}


@router.post("/accounts", status_code=201)
def create_account(
    request: Request,
    response: Response,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    key = _require_key(idempotency_key)
    service = _service(request)
    account, created = _call(lambda: service.create_account(key))
    if not created:
        response.status_code = 200
    return account


@router.get("/accounts/{account_id}")
def get_account(request: Request, account_id: str) -> dict[str, Any]:
    service = _service(request)
    return _call(lambda: service.account_view(account_id))


@router.get("/accounts/{account_id}/positions")
def get_positions(request: Request, account_id: str) -> dict[str, Any]:
    service = _service(request)
    return {"items": _call(lambda: service.positions_view(account_id))}


@router.get("/accounts/{account_id}/orders")
def get_orders(
    request: Request,
    account_id: str,
    cursor: str | None = Query(None),
    limit: int = Query(20),
    status: str | None = Query(None, description="逗号分隔的订单状态"),
) -> dict[str, Any]:
    service = _service(request)
    statuses = [item.strip() for item in status.split(",") if item.strip()] if status else None
    return _call(
        lambda: service.list_orders(account_id, cursor=cursor, limit=limit, statuses=statuses)
    )


@router.get("/orders/{order_id}")
def get_order(request: Request, order_id: str) -> dict[str, Any]:
    service = _service(request)
    return _call(lambda: service.order_detail(order_id))


@router.post("/orders/{order_id}/cancel")
async def cancel_order(
    request: Request,
    order_id: str,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    key = _require_key(idempotency_key)
    version = await _expected_version(request)
    service = _service(request)
    return await run_in_threadpool(_call, lambda: service.cancel_order(order_id, key, version))


@router.post("/accounts/{account_id}/pause")
async def pause_account(
    request: Request,
    account_id: str,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    key = _require_key(idempotency_key)
    version = await _expected_version(request)
    service = _service(request)
    return await run_in_threadpool(_call, lambda: service.pause(account_id, key, version))


@router.post("/accounts/{account_id}/resume")
async def resume_account(
    request: Request,
    account_id: str,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    key = _require_key(idempotency_key)
    version = await _expected_version(request)
    service = _service(request)
    return await run_in_threadpool(_call, lambda: service.resume(account_id, key, version))
