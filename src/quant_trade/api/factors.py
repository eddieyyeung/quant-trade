"""Read-only factor-domain routes.

Thin adapters over ``quant_trade.services``: build a params object, call one
service, serialise the result. Every write in this domain — computing factors,
computing IC — goes through ``POST /api/runs`` like every other task, so this
router has no POST at all.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import ValidationError

from quant_trade.config import AppConfig
from quant_trade.data.store import DataStore
from quant_trade.services.context import RunContext
from quant_trade.services.factor_analysis import (
    FactorCorrelationParams,
    FactorCoverageParams,
    FactorICQueryParams,
    FactorQuantileParams,
    factor_correlation,
    factor_coverage,
    factor_ic_decay,
    factor_ic_series,
    factor_quantile_backtest,
)
from quant_trade.services.factors import FactorListParams, list_factors

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200
ALPHA158_CATEGORY = "alpha158"
"""Category label for persisted factors, which live outside the Factor registry."""


def create_factors_router(config: AppConfig) -> APIRouter:
    """Build the factor-domain routes."""
    router = APIRouter(prefix="/api/factors", tags=["factors"])

    @router.get("")
    def get_factors(
        category: str | None = None,
        limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """The factor catalogue: registered factors plus everything persisted.

        Paged on the server — the Alpha158 set alone is 158 rows and the union
        with the registry is not bounded by anything the client knows.
        """
        with _context(config) as ctx:
            listed = list_factors(FactorListParams(category=None), ctx)
            coverage = factor_coverage(FactorCoverageParams(), ctx)

        spans = {entry.name: entry for entry in coverage.entries}
        rows: dict[str, dict[str, Any]] = {}
        for info in listed.factors:
            span = spans.get(info.name)
            rows[info.name] = {
                "name": info.name,
                "category": info.category,
                "persisted": span is not None,
                "rows": span.rows if span else 0,
                "earliest": _iso(span.earliest if span else None),
                "latest": _iso(span.latest if span else None),
            }
        for name, span in spans.items():
            if name in rows:
                continue
            rows[name] = {
                "name": name,
                "category": ALPHA158_CATEGORY,
                "persisted": True,
                "rows": span.rows,
                "earliest": _iso(span.earliest),
                "latest": _iso(span.latest),
            }

        ordered = [rows[name] for name in sorted(rows)]
        if category is not None:
            ordered = [row for row in ordered if row["category"] == category]
        return {
            "total": len(ordered),
            "offset": offset,
            "limit": limit,
            "items": ordered[offset : offset + limit],
        }

    @router.get("/coverage")
    def get_coverage() -> dict[str, Any]:
        """Per-factor persistence coverage. Unpersisted factors are absent, not zero."""
        with _context(config) as ctx:
            coverage = factor_coverage(FactorCoverageParams(), ctx)
        return {
            "total": len(coverage.entries),
            "items": [
                {
                    "name": entry.name,
                    "rows": entry.rows,
                    "earliest": _iso(entry.earliest),
                    "latest": _iso(entry.latest),
                }
                for entry in coverage.entries
            ],
        }

    @router.get("/ic")
    def get_ic(
        factor: str,
        start: date | None = None,
        end: date | None = None,
        forward_period: int = Query(5, ge=1),
    ) -> dict[str, Any]:
        """A persisted IC series with its summary. Never recomputes IC."""
        params = _validated(
            FactorICQueryParams, factor=factor, start_date=start, end_date=end, forward_period=forward_period
        )
        with _context(config) as ctx:
            result = factor_ic_series(params, ctx)
        return {
            "factor": result.factor,
            "forward_period": result.forward_period,
            "start": _iso(result.start),
            "end": _iso(result.end),
            "count": len(result.points),
            "summary": {
                "ic_mean": result.ic_mean,
                "ic_std": result.ic_std,
                "ic_ir": result.ic_ir,
                "ic_positive_ratio": result.ic_positive_ratio,
            },
            "series": [
                {
                    "trade_date": _iso(point.trade_date),
                    "ic": point.ic,
                    "rank_ic": point.rank_ic,
                    "sample_size": point.sample_size,
                }
                for point in result.points
            ],
        }

    @router.get("/ic/decay")
    def get_ic_decay(
        factor: str,
        start: date | None = None,
        end: date | None = None,
    ) -> dict[str, Any]:
        """IC mean per holding period — the decay comparison."""
        params = _validated(FactorICQueryParams, factor=factor, start_date=start, end_date=end)
        with _context(config) as ctx:
            decay = factor_ic_decay(params, ctx)
        return {
            "factor": decay.factor,
            "start": _iso(decay.start),
            "end": _iso(decay.end),
            "items": [
                {
                    "forward_period": entry.forward_period,
                    "ic_mean": entry.ic_mean,
                    "ic_positive_ratio": entry.ic_positive_ratio,
                    "count": entry.count,
                }
                for entry in decay.entries
            ],
        }

    @router.get("/quantile")
    def get_quantile(
        factor: str,
        start: date | None = None,
        end: date | None = None,
        n_groups: int = Query(5, ge=2, le=20),
        forward_period: int = Query(5, ge=1),
    ) -> dict[str, Any]:
        """Layered NAV curves for one factor, computed on demand."""
        params = _validated(
            FactorQuantileParams,
            factor=factor,
            start_date=start,
            end_date=end,
            n_groups=n_groups,
            forward_period=forward_period,
        )
        with _context(config) as ctx:
            result = factor_quantile_backtest(params, ctx)
        return {
            "factor": result.factor,
            "n_groups": result.n_groups,
            "forward_period": result.forward_period,
            "rebalance_count": len(result.rebalance_dates),
            "skipped_dates": result.skipped_dates,
            "groups": [_series_payload(series) for series in result.series],
            "long_short": _series_payload(result.long_short),
        }

    @router.get("/correlation")
    def get_correlation(
        factors: Annotated[list[str], Query(description="Factor names, repeated")],
        start: date | None = None,
        end: date | None = None,
    ) -> dict[str, Any]:
        """Mean cross-sectional correlation matrix, computed on demand."""
        params = _validated(FactorCorrelationParams, factors=factors, start_date=start, end_date=end)
        with _context(config) as ctx:
            result = factor_correlation(params, ctx)
        return {
            "factors": result.factors,
            "matrix": result.matrix,
            "date_count": result.date_count,
            "skipped_dates": result.skipped_dates,
        }

    return router


def _series_payload(series: Any) -> dict[str, Any] | None:
    """One NAV curve as parallel date/value lists, ready for a line chart."""
    if series is None:
        return None
    return {
        "name": series.name,
        "dates": [_iso(day) for day in series.dates],
        "values": series.values,
    }


def _validated(model: type[Any], **fields: Any) -> Any:
    """Build a params object, turning a validation failure into a 422.

    The services already reject bad input before touching the database; without
    this the rejection would surface as a 500 instead of a client error.
    """
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

    Same reasoning as the data routes: DuckDB's default configuration, because a
    read-only connection alongside the process's read-write one raises
    ``ConnectionException``. Concurrency comes from MVCC instead.
    """
    store = DataStore(config.data.db_path)
    try:
        yield RunContext(run_id="api", config=config, store=store)
    finally:
        store.close()


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None
