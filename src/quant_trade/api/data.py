"""Read-only data-domain routes.

These are thin adapters over ``quant_trade.services``: each one builds a params
object and a context, calls one service, and serialises the result. The
long-running operation of this domain — the sync — is not here; it goes through
``POST /api/runs`` like every other task.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from datetime import date
from typing import Any

from fastapi import APIRouter, Query

from quant_trade.config import AppConfig
from quant_trade.data.store import DataStore
from quant_trade.services.context import RunContext
from quant_trade.services.data import DataStatusParams, data_status
from quant_trade.services.queries import (
    TradeCalendarParams,
    UniverseCoverageParams,
    trade_calendar,
    universe_coverage,
)

DEFAULT_MISSING_PAGE = 100
"""Missing-code page size. The list can run to the whole universe."""


def create_data_router(config: AppConfig) -> APIRouter:
    """Build the data-domain routes."""
    router = APIRouter(prefix="/api/data", tags=["data"])

    @router.get("/status")
    def get_status(universe_as_of: date | None = None) -> dict[str, Any]:
        """Row counts, date spans and universe size for every market data table."""
        with _context(config) as ctx:
            status = data_status(DataStatusParams(universe_as_of=universe_as_of), ctx)
        return {
            "db_path": status.db_path,
            "latest_trade_date": _iso(status.latest_trade_date),
            "universe_size": status.universe_size,
            "tables": [
                {
                    "table": stats.table,
                    "rows": stats.rows,
                    "earliest": _iso(stats.earliest),
                    "latest": _iso(stats.latest),
                }
                for stats in status.tables.values()
            ],
        }

    @router.get("/coverage")
    def get_coverage(
        as_of: date | None = None,
        limit: int = Query(DEFAULT_MISSING_PAGE, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """How much of a universe has synced kline data.

        The missing codes are paged: a fresh database can be missing the whole
        universe, and the response must stay bounded.
        """
        with _context(config) as ctx:
            coverage = universe_coverage(UniverseCoverageParams(as_of=as_of), ctx)
        return {
            "as_of": _iso(as_of or date.today()),
            "universe_size": coverage.universe_size,
            "covered": coverage.covered,
            "ratio": coverage.ratio,
            "missing_total": len(coverage.missing),
            "missing": coverage.missing[offset : offset + limit],
            "missing_offset": offset,
            "missing_limit": limit,
        }

    @router.get("/calendar")
    def get_calendar(
        start: date | None = None,
        end: date | None = None,
    ) -> dict[str, Any]:
        """The trade calendar over a window, defaulting to the current year."""
        today = date.today()
        window_start = start or date(today.year, 1, 1)
        window_end = end or date(today.year, 12, 31)

        with _context(config) as ctx:
            view = trade_calendar(TradeCalendarParams(start=window_start, end=window_end), ctx)
        return {
            "start": _iso(view.start or window_start),
            "end": _iso(view.end or window_end),
            "open_days": view.open_days,
            "closed_days": view.closed_days,
            "days": [{"trade_date": _iso(day.trade_date), "is_open": day.is_open} for day in view.days],
        }

    return router


@contextlib.contextmanager
def _context(config: AppConfig) -> Iterator[RunContext]:
    """A context over a per-request store.

    Each request opens its own connection, in DuckDB's default configuration.
    Read paths deliberately do not use a read-only connection: opening the same
    file read-only while another connection in this process holds it
    read-write raises ``ConnectionException``, so concurrency comes from MVCC.
    """
    store = DataStore(config.data.db_path)
    try:
        yield RunContext(run_id="api", config=config, store=store)
    finally:
        store.close()


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None
