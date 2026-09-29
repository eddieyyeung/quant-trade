"""Backtest read services — run list, detail, trades and multi-run comparison.

Every function here reads the ``backtest_*`` result tables; none of them runs a
backtest. That is the point: opening a saved result must not cost another weekly
loop, and a page that re-ran the engine could disagree with the numbers another
page shows for the same run.

The SQL lives in :mod:`quant_trade.backtest.result_store`; this module decides
what to fetch, applies paging limits, and assembles the shapes callers render.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd
from pydantic import Field

from quant_trade.backtest.result_store import (
    CASH_METRIC_NAME,
    FINAL_VALUE_METRIC_NAME,
    BacktestRunRow,
    count_backtest_trades,
    get_backtest_metrics,
    get_backtest_nav,
    get_backtest_nav_many,
    get_backtest_positions,
    get_backtest_run,
    get_backtest_runs,
    get_backtest_trades,
    list_backtest_runs,
)
from quant_trade.services.context import NULL_CONTEXT, RunContext
from quant_trade.services.params import ServiceParams

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200
DEFAULT_TRADE_PAGE_SIZE = 100
MAX_TRADE_PAGE_SIZE = 500
MAX_COMPARISON_RUNS = 8
"""Beyond this the overlaid curves stop being readable and the payload stops
being cheap; the limit is a contract, not a UI preference."""


class BacktestRunListParams(ServiceParams):
    """Paging for the backtest run list."""

    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


class BacktestDetailParams(ServiceParams):
    """Which run to read."""

    run_id: str


class BacktestTradeParams(ServiceParams):
    """Which run's trades to read, and which page of them."""

    run_id: str
    limit: int = Field(default=DEFAULT_TRADE_PAGE_SIZE, ge=1, le=MAX_TRADE_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


class BacktestComparisonParams(ServiceParams):
    """The runs to overlay."""

    runs: list[str] = Field(min_length=1, max_length=MAX_COMPARISON_RUNS)


@dataclass
class BacktestRunSummary:
    """One run as the list page shows it."""

    run_id: str
    status: str
    strategy: str | None = None
    start: date | None = None
    end: date | None = None
    nav_points: int = 0
    progress: float = 0.0
    """How far the run has got, so a running row can show it rather than only
    refreshing silently."""
    created_at: datetime | None = None
    finished_at: datetime | None = None
    metrics: dict[str, float] = field(default_factory=dict)


@dataclass
class BacktestRunListResult:
    """One page of backtest runs."""

    total: int = 0
    offset: int = 0
    limit: int = 0
    runs: list[BacktestRunSummary] = field(default_factory=list)


@dataclass
class BacktestSeries:
    """One run's curves, as parallel lists ready for a chart.

    ``benchmark`` and ``drawdown`` may hold ``None`` where the source data has
    gaps; a missing index day does not remove the strategy's own point.
    """

    run_id: str
    dates: list[date] = field(default_factory=list)
    nav: list[float] = field(default_factory=list)
    benchmark: list[float | None] = field(default_factory=list)
    drawdown: list[float | None] = field(default_factory=list)


@dataclass
class BacktestPositionRow:
    """One end-of-run holding, with its share of the invested book."""

    ts_code: str
    shares: int
    avg_cost: float
    current_price: float
    market_value: float
    weight: float


@dataclass
class BacktestTradeRow:
    """One executed trade."""

    seq: int
    trade_date: date
    action: str
    ts_code: str
    shares: int
    price: float
    commission: float
    stamp_duty: float
    transfer_fee: float


@dataclass
class BacktestTradeResult:
    """One page of a run's trades."""

    run_id: str
    total: int = 0
    offset: int = 0
    limit: int = 0
    trades: list[BacktestTradeRow] = field(default_factory=list)


@dataclass
class BacktestDetailResult:
    """Everything the detail page needs about one run.

    ``found`` is False when no backtest exists under the requested ``run_id``;
    the API layer turns that into a 404 rather than rendering an empty curve.
    """

    run_id: str
    found: bool = False
    status: str = ""
    strategy: str | None = None
    start: date | None = None
    end: date | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    series: BacktestSeries | None = None
    positions: list[BacktestPositionRow] = field(default_factory=list)
    cash: float | None = None
    total_value: float | None = None


@dataclass
class BacktestComparisonEntry:
    """One run's contribution to the comparison view."""

    run_id: str
    status: str
    strategy: str | None = None
    start: date | None = None
    end: date | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    series: BacktestSeries | None = None


@dataclass
class BacktestComparisonResult:
    """Several runs' curves and metrics, in the order they were requested."""

    runs: list[BacktestComparisonEntry] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    """Requested run ids that have no persisted results."""


def backtest_run_list(params: BacktestRunListParams, ctx: RunContext = NULL_CONTEXT) -> BacktestRunListResult:
    """One page of backtest runs, newest first."""
    rows, total = list_backtest_runs(ctx.db, limit=params.limit, offset=params.offset)
    return BacktestRunListResult(
        total=total,
        offset=params.offset,
        limit=params.limit,
        runs=[_summary(row) for row in rows],
    )


def backtest_detail(params: BacktestDetailParams, ctx: RunContext = NULL_CONTEXT) -> BacktestDetailResult:
    """One run's metadata, metrics, curves and closing positions."""
    store = ctx.db
    row = get_backtest_run(store, params.run_id)
    if row is None:
        return BacktestDetailResult(run_id=params.run_id, found=False)

    metrics = get_backtest_metrics(store, params.run_id)
    return BacktestDetailResult(
        run_id=params.run_id,
        found=True,
        status=row.status,
        strategy=row.strategy,
        start=row.start,
        end=row.end,
        created_at=row.created_at,
        finished_at=row.finished_at,
        metrics=metrics,
        series=_series(params.run_id, get_backtest_nav(store, params.run_id)),
        positions=_positions(get_backtest_positions(store, params.run_id)),
        cash=metrics.get(CASH_METRIC_NAME),
        total_value=metrics.get(FINAL_VALUE_METRIC_NAME),
    )


def backtest_trades(params: BacktestTradeParams, ctx: RunContext = NULL_CONTEXT) -> BacktestTradeResult:
    """One page of a run's trade log, oldest first."""
    store = ctx.db
    frame = get_backtest_trades(store, params.run_id, limit=params.limit, offset=params.offset)
    return BacktestTradeResult(
        run_id=params.run_id,
        total=count_backtest_trades(store, params.run_id),
        offset=params.offset,
        limit=params.limit,
        trades=[
            BacktestTradeRow(
                seq=int(record["seq"]),
                trade_date=_as_date(record["trade_date"]),
                action=str(record["action"]),
                ts_code=str(record["ts_code"]),
                shares=int(record["shares"]),
                price=float(record["price"]),
                commission=float(record["commission"]),
                stamp_duty=float(record["stamp_duty"]),
                transfer_fee=float(record["transfer_fee"]),
            )
            for record in frame.to_dict("records")
        ],
    )


def backtest_comparison(params: BacktestComparisonParams, ctx: RunContext = NULL_CONTEXT) -> BacktestComparisonResult:
    """Several runs' curves and metrics, read in one pass.

    Order follows the request so the caller controls the legend, and a run with
    no persisted NAV is reported in ``missing`` instead of silently vanishing
    from the comparison.
    """
    store = ctx.db
    nav = get_backtest_nav_many(store, params.runs)
    by_run: dict[str, list[dict[Any, Any]]] = {}
    for record in nav.to_dict("records"):
        by_run.setdefault(str(record["run_id"]), []).append(record)

    # Metadata for every run in one pass rather than two queries per run; the
    # curves are already a single query for the same reason.
    rows = get_backtest_runs(store, params.runs)

    result = BacktestComparisonResult()
    for run_id in params.runs:
        records = by_run.get(run_id, [])
        row = rows.get(run_id)
        if not records or row is None:
            result.missing.append(run_id)
            continue
        result.runs.append(
            BacktestComparisonEntry(
                run_id=run_id,
                status=row.status,
                strategy=row.strategy,
                start=row.start,
                end=row.end,
                metrics=dict(row.metrics),
                series=_series(run_id, pd.DataFrame(records)),
            )
        )
    return result


def _summary(row: BacktestRunRow) -> BacktestRunSummary:
    return BacktestRunSummary(
        run_id=row.run_id,
        status=row.status,
        strategy=row.strategy,
        start=row.start,
        end=row.end,
        nav_points=row.nav_points,
        progress=row.progress,
        created_at=row.created_at,
        finished_at=row.finished_at,
        metrics=dict(row.metrics),
    )


def _series(run_id: str, frame: pd.DataFrame) -> BacktestSeries:
    """Turn NAV rows into parallel lists, preserving NULLs as ``None``."""
    if frame.empty:
        return BacktestSeries(run_id=run_id)
    frame = frame.sort_values("trade_date")
    return BacktestSeries(
        run_id=run_id,
        dates=[_as_date(value) for value in frame["trade_date"]],
        nav=[float(value) for value in frame["nav"]],
        benchmark=[_float_or_none(value) for value in frame["benchmark"]],
        drawdown=[_float_or_none(value) for value in frame["drawdown"]],
    )


def _positions(frame: pd.DataFrame) -> list[BacktestPositionRow]:
    """Closing holdings with each one's share of the invested book.

    Weights are relative to the holdings' own market value rather than the
    account total: the pie answers "how is the invested book spread", and cash
    is shown separately as a figure.
    """
    if frame.empty:
        return []
    records = frame.to_dict("records")
    total = sum(float(record["market_value"] or 0.0) for record in records)
    return [
        BacktestPositionRow(
            ts_code=str(record["ts_code"]),
            shares=int(record["shares"]),
            avg_cost=float(record["avg_cost"]),
            current_price=float(record["current_price"]),
            market_value=float(record["market_value"]),
            weight=(float(record["market_value"] or 0.0) / total) if total > 0 else 0.0,
        )
        for record in records
    ]


def _float_or_none(value: object) -> float | None:
    """A float, or ``None`` for the NULL a missing benchmark day becomes."""
    if value is None or value is pd.NaT:
        return None
    number = float(value)  # type: ignore[arg-type]
    return None if number != number else number


def _as_date(value: object) -> date:
    """Normalise a DuckDB date / pandas Timestamp to a plain ``date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
