"""The strategy domain's read API.

Read-only, like the factor, model, backtest and report routers: a signal
generation is submitted through ``POST /api/runs`` with ``kind: strategy_signals``,
so this router has no POST at all. Everything here reads what a run already
wrote to ``strategy_signal``.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import ValidationError

from quant_trade.config import AppConfig
from quant_trade.data.store import DataStore
from quant_trade.services.context import RunContext
from quant_trade.services.strategies import StrategyListParams, list_strategies
from quant_trade.services.strategy_query import (
    DEFAULT_PAGE_SIZE,
    StrategyRunListParams,
    StrategyRunSummary,
    StrategySignalQueryParams,
    StrategySignalRow,
    strategy_run_list,
    strategy_signals,
)

MAX_PAGE_SIZE = 200


def create_strategies_router(config: AppConfig) -> APIRouter:
    """Build the strategy-domain routes."""
    router = APIRouter(prefix="/api/strategies", tags=["strategies"])

    @router.get("")
    def list_registered() -> dict[str, Any]:
        """Every registered strategy, plus the one the config selects.

        The page's strategy selector is built from this rather than a hardcoded
        list, so a newly registered strategy becomes selectable without a
        frontend change.
        """
        with _context(config) as ctx:
            items = list_strategies(StrategyListParams(), ctx)
        return {"items": items, "default": config.strategy.name}

    @router.get("/runs")
    def list_runs(
        limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """Signal-generation history, newest first."""
        params = _validated(StrategyRunListParams, limit=limit, offset=offset)
        with _context(config) as ctx:
            result = strategy_run_list(params, ctx)
        return {
            "total": result.total,
            "offset": result.offset,
            "limit": result.limit,
            "items": [_run_payload(run) for run in result.runs],
        }

    @router.get("/runs/{run_id}")
    def get_signals(
        run_id: str,
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """One run's signals, in the order the engine emitted them."""
        params = _validated(StrategySignalQueryParams, run_id=run_id, limit=limit, offset=offset)
        with _context(config) as ctx:
            result = strategy_signals(params, ctx)
        if not result.found:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"信号运行 {run_id} 不存在")
        return {
            "run_id": result.run_id,
            "status": result.status,
            "finished_at": _iso(result.finished_at),
            "strategy": result.strategy,
            "signal_date": _iso(result.signal_date),
            "universe_size": result.universe_size,
            "total": result.total,
            "offset": params.offset,
            "limit": params.limit,
            "orders": [_order_payload(order) for order in result.orders],
        }

    return router


def _run_payload(run: StrategyRunSummary) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "status": run.status,
        "created_at": _iso(run.created_at),
        "finished_at": _iso(run.finished_at),
        "progress": run.progress,
        "signal_date": _iso(run.signal_date),
        "strategy": run.strategy,
        "order_count": run.order_count,
        "universe_size": run.universe_size,
    }


def _order_payload(order: StrategySignalRow) -> dict[str, Any]:
    return {
        "seq": order.seq,
        "ts_code": order.ts_code,
        "direction": order.direction,
        "target_pct": order.target_pct,
        "reason": order.reason,
    }


def _validated(model: type[Any], **fields: Any) -> Any:
    """Build a params object, turning validation errors into 422."""
    try:
        return model(**fields)
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=json.loads(e.json())) from e


@contextlib.contextmanager
def _context(config: AppConfig) -> Iterator[RunContext]:
    """A per-request context with its own store.

    The store uses the default DuckDB configuration, not read-only: a read-only
    connection alongside the process's read-write one raises at open.
    """
    store = DataStore(config.data.db_path)
    try:
        yield RunContext(run_id="api", config=config, store=store)
    finally:
        store.close()


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
