"""Read-only backtest-domain routes.

Thin adapters over ``quant_trade.services``: build a params object, call one
service, serialise the result. Starting a backtest — a long task — goes through
``POST /api/runs`` with ``kind: backtest`` like every other task, so this router
has no POST at all.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import ValidationError

from quant_trade.config import AppConfig
from quant_trade.data.store import DataStore
from quant_trade.services.backtest_query import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_TRADE_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MAX_TRADE_PAGE_SIZE,
    BacktestComparisonParams,
    BacktestDetailParams,
    BacktestRunListParams,
    BacktestTradeParams,
    backtest_comparison,
    backtest_detail,
    backtest_run_list,
    backtest_trades,
)
from quant_trade.services.context import RunContext
from quant_trade.services.strategies import StrategyListParams, list_strategies


def create_backtests_router(config: AppConfig) -> APIRouter:
    """Build the backtest-domain routes."""
    router = APIRouter(prefix="/api/backtests", tags=["backtests"])

    @router.get("")
    def get_backtests(
        limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """Backtest runs, newest first."""
        params = _validated(BacktestRunListParams, limit=limit, offset=offset)
        with _context(config) as ctx:
            result = backtest_run_list(params, ctx)
        return {
            "total": result.total,
            "offset": result.offset,
            "limit": result.limit,
            "items": [_summary_payload(run, config) for run in result.runs],
        }

    @router.get("/strategies")
    def get_strategies() -> dict[str, Any]:
        """Registered strategy names — the submit form's option list.

        It lives under the backtest domain because the backtest form is its only
        consumer; hard-coding the names in the frontend would drift the moment
        a strategy is registered or renamed.
        """
        with _context(config) as ctx:
            names = list_strategies(StrategyListParams(), ctx)
        return {"items": names, "default": config.strategy.name}

    # Declared before ``/{run_id}``: FastAPI matches in declaration order, so
    # the other way round would swallow "compare" as a run id and 404 forever.
    @router.get("/compare")
    def get_comparison(
        runs: Annotated[list[str], Query(description="Run ids to compare, repeated")],
    ) -> dict[str, Any]:
        """Several runs' curves and metrics, read in one request."""
        params = _validated(BacktestComparisonParams, runs=runs)
        with _context(config) as ctx:
            result = backtest_comparison(params, ctx)
        return {
            "runs": [
                {
                    "run_id": entry.run_id,
                    "status": entry.status,
                    "strategy": entry.strategy or config.strategy.name,
                    "start": _iso(entry.start),
                    "end": _iso(entry.end),
                    "metrics": entry.metrics,
                    "series": _series_payload(entry.series),
                }
                for entry in result.runs
            ],
            "missing": result.missing,
        }

    @router.get("/{run_id}")
    def get_detail(run_id: str) -> dict[str, Any]:
        """One run's metrics, curves and closing positions."""
        params = _validated(BacktestDetailParams, run_id=run_id)
        with _context(config) as ctx:
            detail = backtest_detail(params, ctx)
        if not detail.found:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"回测 {run_id} 不存在",
            )
        return {
            "run_id": detail.run_id,
            "status": detail.status,
            "strategy": detail.strategy or config.strategy.name,
            "start": _iso(detail.start),
            "end": _iso(detail.end),
            "created_at": _iso(detail.created_at),
            "finished_at": _iso(detail.finished_at),
            "metrics": detail.metrics,
            "cash": detail.cash,
            "total_value": detail.total_value,
            "series": _series_payload(detail.series),
            "positions": [
                {
                    "ts_code": row.ts_code,
                    "shares": row.shares,
                    "avg_cost": row.avg_cost,
                    "current_price": row.current_price,
                    "market_value": row.market_value,
                    "weight": row.weight,
                }
                for row in detail.positions
            ],
        }

    @router.get("/{run_id}/trades")
    def get_trades(
        run_id: str,
        limit: int = Query(DEFAULT_TRADE_PAGE_SIZE, ge=1, le=MAX_TRADE_PAGE_SIZE),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """One page of a run's trade log, oldest first."""
        params = _validated(BacktestTradeParams, run_id=run_id, limit=limit, offset=offset)
        with _context(config) as ctx:
            result = backtest_trades(params, ctx)
        return {
            "run_id": result.run_id,
            "total": result.total,
            "offset": result.offset,
            "limit": result.limit,
            "items": [
                {
                    "seq": trade.seq,
                    "trade_date": _iso(trade.trade_date),
                    "action": trade.action,
                    "ts_code": trade.ts_code,
                    "shares": trade.shares,
                    "price": trade.price,
                    "commission": trade.commission,
                    "stamp_duty": trade.stamp_duty,
                    "transfer_fee": trade.transfer_fee,
                }
                for trade in result.trades
            ],
        }

    return router


def _summary_payload(run: Any, config: AppConfig) -> dict[str, Any]:
    """One list row.

    ``strategy`` falls back to the configured name when the run recorded none:
    a submission that omits it means "whatever config says", and an empty cell
    would read as "unknown strategy" rather than "the default one".
    """
    return {
        "run_id": run.run_id,
        "status": run.status,
        "strategy": run.strategy or config.strategy.name,
        "start": _iso(run.start),
        "end": _iso(run.end),
        "nav_points": run.nav_points,
        "progress": run.progress,
        "created_at": _iso(run.created_at),
        "finished_at": _iso(run.finished_at),
        "metrics": run.metrics,
    }


def _series_payload(series: Any) -> dict[str, Any] | None:
    """One run's curves as parallel lists, ready for a chart."""
    if series is None:
        return None
    return {
        "run_id": series.run_id,
        "dates": [_iso(day) for day in series.dates],
        "nav": series.nav,
        "benchmark": series.benchmark,
        "drawdown": series.drawdown,
    }


def _validated(model: type[Any], **fields: Any) -> Any:
    """Build a params object, turning a validation failure into a 422."""
    try:
        return model(**fields)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=json.loads(e.json()),
        ) from e


@contextlib.contextmanager
def _context(config: AppConfig) -> Iterator[RunContext]:
    """A context over a per-request store.

    DuckDB's default configuration, not read-only: a read-only connection
    alongside the process's read-write one raises ``ConnectionException``.
    """
    store = DataStore(config.data.db_path)
    try:
        yield RunContext(run_id="api", config=config, store=store)
    finally:
        store.close()


def _iso(value: date | datetime | None) -> str | None:
    """A date or timestamp as ISO 8601, or None. Both serialise the same way."""
    return value.isoformat() if value is not None else None
