"""Backtest-domain services — run a weekly backtest and return a serializable result.

The engine works in pandas Series indexed by ``date``. That is convenient inside
the engine and awkward everywhere else — a JSON encoder, a database writer and a
chart all want plain rows. This module is the boundary: it converts once, so no
caller has to know the engine's internal shapes.

It is also where a run's results reach the database: :func:`run_backtest_service`
writes the four ``backtest_*`` tables whenever the context carries a ``run_id``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd
from pydantic import Field

from quant_trade.backtest.engine import run_backtest
from quant_trade.backtest.result_store import (
    CASH_METRIC_NAME,
    FINAL_VALUE_METRIC_NAME,
    BacktestRows,
    build_metric_frame,
    build_nav_frame,
    build_position_frame,
    build_trade_frame,
    save_backtest_result,
)
from quant_trade.config import AppConfig
from quant_trade.services.context import NULL_CONTEXT, RunContext
from quant_trade.services.params import ServiceParams
from quant_trade.strategies.factory import build_strategy


class BacktestParams(ServiceParams):
    """Window, strategy and costs for one backtest run."""

    start: date | None = None
    end: date | None = None
    strategy: str | None = None
    initial_capital: float | None = None
    benchmark: str | None = None
    universe: list[str] | None = None
    top_n: int | None = Field(default=None, ge=1)
    """Override for how many stocks the strategy holds. ``None`` uses config."""

    @classmethod
    def config_defaults(cls, config: AppConfig) -> dict[str, object]:
        return {
            "start": config.backtest.start_date,
            "strategy": config.strategy.name,
            "initial_capital": config.backtest.initial_capital,
            "benchmark": config.backtest.benchmark,
            "top_n": config.strategy.top_n,
        }


@dataclass
class NavPoint:
    """One point on a daily series."""

    date: date
    value: float


@dataclass
class TradeRecord:
    """One executed trade."""

    date: str
    action: str
    ts_code: str
    shares: int
    price: float
    commission: float
    stamp_duty: float
    transfer_fee: float


@dataclass
class HoldingRecord:
    """One position at the end of the backtest."""

    ts_code: str
    shares: int
    avg_cost: float
    current_price: float
    market_value: float


@dataclass
class BacktestResult:
    """A backtest outcome in plain rows, ready to JSON-encode or persist."""

    start: date
    end: date
    strategy: str
    initial_capital: float
    metrics: dict[str, float] = field(default_factory=dict)
    nav: list[NavPoint] = field(default_factory=list)
    benchmark: list[NavPoint] = field(default_factory=list)
    drawdown: list[NavPoint] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)
    holdings: list[HoldingRecord] = field(default_factory=list)
    cash: float = 0.0
    total_value: float = 0.0
    cancelled: bool = False
    rows_saved: BacktestRows = field(default_factory=BacktestRows)
    """Rows this run wrote to the ``backtest_*`` tables; all zeros when it did not persist."""


@dataclass
class RawBacktest:
    """The engine's native output, pandas objects included.

    Callers that need to serialize should use :func:`run_backtest_service`
    instead; this exists for the HTML reporter, which plots a pandas Series
    directly and is rewritten in a later change.
    """

    start: date
    end: date
    strategy: str
    initial_capital: float
    nav_series: pd.Series
    benchmark_series: pd.Series | None
    trade_log: list[dict[str, Any]]
    metrics: dict[str, float]
    portfolio: Any
    cancelled: bool = False

    def as_reporter_input(self, signal_date: date | None = None) -> dict[str, Any]:
        """Shape the engine dict that ``generate_weekly_report`` expects."""
        return {
            "nav_series": self.nav_series,
            "benchmark_series": self.benchmark_series,
            "trade_log": self.trade_log,
            "metrics": self.metrics,
            "portfolio": self.portfolio,
            "signal_date": str(signal_date or self.end),
        }


def run_backtest_raw(params: BacktestParams, ctx: RunContext = NULL_CONTEXT) -> RawBacktest:
    """Run a weekly backtest and return the engine's output untouched."""
    store = ctx.db
    config = ctx.cfg

    end = params.end or store.get_latest_trade_date()
    if end is None:
        raise ValueError("Database has no trade dates; run a data sync first")
    start = params.start or config.backtest.start_date

    strategy = build_strategy(params.strategy, config, store=store)
    if strategy is None:
        raise ValueError(f"Strategy {params.strategy or config.strategy.name!r} is not registered")
    if params.top_n is not None and hasattr(strategy, "top_n"):
        strategy.top_n = params.top_n

    ctx.progress(0.0, f"Running backtest {start} → {end}")
    raw = run_backtest(
        strategy=strategy,
        start=start,
        end=end,
        initial_capital=params.initial_capital or config.backtest.initial_capital,
        commission_rate=config.backtest.commission_rate,
        min_commission=config.backtest.min_commission,
        stamp_duty_rate=config.backtest.stamp_duty_rate,
        transfer_fee_rate=config.backtest.transfer_fee_rate,
        benchmark_code=params.benchmark or config.backtest.benchmark,
        store=store,
        ctx=ctx,
    )
    # A cancelled run stops where the work stopped. Reporting 100% here would
    # tell the job centre — and the progress bar a user is watching — that the
    # full window was covered.
    if not raw.get("cancelled", False):
        ctx.progress(1.0, "Backtest complete")

    return RawBacktest(
        start=start,
        end=end,
        strategy=strategy.name,
        initial_capital=params.initial_capital or config.backtest.initial_capital,
        nav_series=raw["nav_series"],
        benchmark_series=raw["benchmark_series"],
        trade_log=list(raw["trade_log"]),
        metrics={k: float(v) for k, v in raw["metrics"].items()},
        portfolio=raw["portfolio"],
        cancelled=bool(raw.get("cancelled", False)),
    )


def run_backtest_service(params: BacktestParams, ctx: RunContext = NULL_CONTEXT) -> BacktestResult:
    """Run a weekly backtest, convert the output into plain rows, and persist it.

    Persistence is keyed on ``ctx.run_id``, so it happens only when the run was
    submitted through the job runner. Under :data:`NULL_CONTEXT` — the default
    for scripts and tests — the result is returned without being written: every
    such call would otherwise claim the same empty ``run_id``, and a second call
    would silently overwrite the first one's rows.

    A cancelled run persists what it produced up to the cancellation point;
    those weeks really ran, and discarding them would waste the work.
    """
    raw = run_backtest_raw(params, ctx)
    portfolio = raw.portfolio
    holdings = [
        HoldingRecord(
            ts_code=h.ts_code,
            shares=h.shares,
            avg_cost=h.avg_cost,
            current_price=h.current_price,
            market_value=h.current_price * h.shares,
        )
        for h in portfolio.holdings.values()
    ]
    result = BacktestResult(
        start=raw.start,
        end=raw.end,
        strategy=raw.strategy,
        initial_capital=raw.initial_capital,
        metrics=raw.metrics,
        nav=to_nav_points(raw.nav_series),
        benchmark=to_nav_points(raw.benchmark_series),
        drawdown=_drawdown_points(raw.nav_series),
        trades=[_to_trade(t) for t in raw.trade_log],
        holdings=holdings,
        cash=float(portfolio.cash),
        total_value=float(portfolio.total_value),
        cancelled=raw.cancelled,
    )

    if not ctx.run_id:
        ctx.log("No run_id in context; backtest result was not persisted")
        return result

    if not _produced_results(raw):
        # Nothing ran. Writing the portfolio snapshot here would give an empty
        # run rows in `backtest_metric` and an artifact claiming it produced a
        # metric table — see `_produced_results`.
        ctx.log("Backtest produced no results; nothing was persisted")
        return result

    result.rows_saved = save_backtest_result(
        ctx.db,
        ctx.run_id,
        nav_frame=build_nav_frame(raw.nav_series, raw.benchmark_series),
        trade_frame=build_trade_frame(list(raw.trade_log)),
        metric_frame=build_metric_frame(_metric_values(raw.metrics, portfolio)),
        position_frame=build_position_frame(
            [
                {
                    "ts_code": holding.ts_code,
                    "shares": holding.shares,
                    "avg_cost": holding.avg_cost,
                    "current_price": holding.current_price,
                    "market_value": holding.market_value,
                }
                for holding in holdings
            ]
        ),
    )
    ctx.log(f"Saved backtest result: {result.rows_saved.total} rows")
    return result


def _produced_results(raw: RawBacktest) -> bool:
    """Whether a run has anything worth persisting at all.

    Three ways to end up with nothing: a window with no trading days, an empty
    universe, and a cancel that lands before the first week. In all three the
    portfolio is still a real object, so the temptation is to record its cash.

    Gating on ``metrics`` is not enough — the engine sets ``total_trades``
    unconditionally (engine.py:263), so a cancelled run reports
    ``{"total_trades": 0.0}``. That is a non-empty dict describing a run with no
    curve, and writing it hands the run an artifact claiming it produced a
    metric table. The presence of a NAV series is the thing that actually
    distinguishes "ran and produced results" from "ran and produced nothing".
    """
    return not raw.nav_series.empty or bool(raw.trade_log)


def _metric_values(metrics: dict[str, float], portfolio: Any) -> dict[str, float]:
    """What goes into ``backtest_metric``.

    The closing portfolio state rides along: it is not a performance metric, but
    a run has no other key/value surface to record it on. Callers only reach
    this for a run that produced results — see :func:`_produced_results`.
    """
    return {
        **metrics,
        CASH_METRIC_NAME: float(portfolio.cash),
        FINAL_VALUE_METRIC_NAME: float(portfolio.total_value),
    }


def to_nav_points(series: pd.Series | None) -> list[NavPoint]:
    """Convert a date-indexed Series into rows, tolerating ``None`` and NaN."""
    if series is None or series.empty:
        return []
    return [
        NavPoint(date=idx, value=float(value))
        for idx, value in series.items()
        if isinstance(idx, date) and value == value
    ]


def _drawdown_points(series: pd.Series) -> list[NavPoint]:
    """Fractional drawdown from the running peak, per date."""
    if series.empty:
        return []
    running_peak = series.cummax()
    drawdown = (series - running_peak) / running_peak
    return [
        NavPoint(date=idx, value=float(value))
        for idx, value in drawdown.items()
        if isinstance(idx, date) and value == value
    ]


def _to_trade(raw: dict[str, Any]) -> TradeRecord:
    return TradeRecord(
        date=str(raw.get("date", "")),
        action=str(raw.get("action", "")),
        ts_code=str(raw.get("ts_code", "")),
        shares=int(raw.get("shares", 0)),
        price=float(raw.get("price", 0.0)),
        commission=float(raw.get("commission", 0.0)),
        stamp_duty=float(raw.get("stamp_duty", 0.0)),
        transfer_fee=float(raw.get("transfer_fee", 0.0)),
    )
