"""Report-domain services — the weekly pipeline and HTML report generation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from quant_trade.data.index_weights import get_default_universe, sync_index_weights
from quant_trade.data.sources.akshare_adapter import AkshareAdapter
from quant_trade.data.sync import sync_all
from quant_trade.services.backtest import BacktestParams, run_backtest_raw
from quant_trade.services.context import NULL_CONTEXT, RunContext
from quant_trade.services.data import DEFAULT_HISTORY_START
from quant_trade.services.factor_analysis import FactorICOverviewParams, factor_ic_overview
from quant_trade.services.params import ServiceParams
from quant_trade.services.strategies import SignalParams, generate_strategy_signals
from quant_trade.signals.reporter import generate_weekly_report, save_report

KLINE_REFRESH_DAYS = 5
"""Trailing window refreshed by the weekly pipeline before generating signals."""

SAMPLE_UNIVERSE_SIZE = 100
"""Codes used when no index constituents resolve and the universe is empty."""


class WeeklyReportParams(ServiceParams):
    """Options for the weekly pipeline."""

    as_of: date | None = None
    """Signal date. ``None`` means the latest trade date."""
    factor_ic_data: list[dict[str, Any]] | None = None
    """An explicit override for the report's IC panel rows.

    ``None`` — the normal case — means the pipeline assembles the panel from
    ``ic_series`` itself, so the section is populated without a caller having to
    produce the rows. Provide a value only to force specific rows.
    """
    output_dir: str | None = None
    """Override for ``config.report.output_dir``."""
    today: date | None = None
    """Run date, used for the report's filename and its \"today\" fields.

    ``None`` means the machine's date. Injectable so a run is reproducible from
    ``params_json`` alone rather than reproducible only on the day it ran.
    """


@dataclass
class WeeklyReportResult:
    """Outcome of a weekly pipeline run."""

    signal_date: date
    report_path: str
    order_count: int
    nav_points: int
    trade_count: int
    tables: dict[str, int] = field(default_factory=dict)
    cancelled: bool = False


def generate_weekly(params: WeeklyReportParams, ctx: RunContext = NULL_CONTEXT) -> WeeklyReportResult:
    """Sync data, generate signals, run the NAV backtest and write the HTML report.

    Never opens a browser: presenting the report is the caller's decision.
    """
    store = ctx.db
    config = ctx.cfg
    adapter = AkshareAdapter()

    ctx.progress(0.0, "Step 1/4: syncing stock basic, calendar and index weights")
    tables = sync_all(
        store,
        [],
        primary=config.data.primary_source,
        include_financials=False,
        ctx=ctx,
    )
    # Index weights must land before the universe can resolve, otherwise a
    # fresh database yields an empty universe.
    sync_index_weights(store, get_default_universe(), date.today(), adapter)

    codes = store.get_universe(get_default_universe(), date.today())
    if not codes:
        ctx.log("No universe codes found; sampling the listed universe instead", level="warning")
        basic = adapter.fetch_stock_basic()
        codes = basic["ts_code"].tolist()[:SAMPLE_UNIVERSE_SIZE] if not basic.empty else []

    latest_kline = store.get_latest_trade_date()
    kline_start = (latest_kline - timedelta(days=KLINE_REFRESH_DAYS)) if latest_kline else DEFAULT_HISTORY_START
    ctx.progress(0.25, f"Syncing daily kline for {len(codes)} codes")
    sync_all(store, codes, start=kline_start, primary=config.data.primary_source, ctx=ctx)

    if ctx.cancelled():
        return _cancelled_result(ctx)

    latest = params.as_of or store.get_latest_trade_date()
    if latest is None:
        raise ValueError("No data available after sync; cannot build a report")

    ctx.progress(0.5, "Step 2/4: generating strategy signals")
    signals = generate_strategy_signals(
        SignalParams(as_of=latest, universe=store.get_universe(get_default_universe(), latest)),
        ctx,
    )

    ctx.progress(0.7, "Step 3/4: computing NAV history")
    backtest = run_backtest_raw(BacktestParams(start=config.backtest.start_date, end=latest), ctx)

    ctx.progress(0.9, "Step 4/4: rendering report")
    signal_dicts = [
        {
            "ts_code": o.ts_code,
            "name": o.ts_code,  # TODO: look up the name from stock_basic
            "target_pct": o.target_pct,
            "direction": o.direction,
            "reason": o.reason,
        }
        for o in signals.orders
    ]
    html = generate_weekly_report(
        backtest.as_reporter_input(latest),
        signal_dicts,
        config,
        portfolio=backtest.portfolio,
        factor_ic_data=params.factor_ic_data if params.factor_ic_data is not None else _factor_ic_rows(latest, ctx),
        today=params.today,
    )
    path = _save(html, config, params.output_dir, params.today)
    ctx.progress(1.0, f"Report written to {path}")

    return WeeklyReportResult(
        signal_date=latest,
        report_path=str(path),
        order_count=len(signals.orders),
        nav_points=len(backtest.nav_series),
        trade_count=len(backtest.trade_log),
        tables=tables,
        cancelled=backtest.cancelled,
    )


def _factor_ic_rows(as_of: date, ctx: RunContext) -> list[dict[str, Any]]:
    """The IC panel rows for this report, read from ``ic_series``.

    A failure here must not cost the whole report. The panel is one section of
    many, and by this point the run has already done all of its expensive work
    — syncing, signalling and backtesting — so an assembly error degrades the
    panel to empty rather than discarding the run.
    """
    try:
        rows = factor_ic_overview(FactorICOverviewParams(as_of=as_of), ctx)
    except Exception as e:
        ctx.log(f"Factor IC panel unavailable: {e}", level="warning")
        return []
    return [{"name": row.name, "ic_weekly": row.ic_weekly, "ic_mean": row.ic_mean, "ic_ir": row.ic_ir} for row in rows]


def _save(html: str, config: Any, output_dir: str | None, today: date | None = None) -> Any:
    if output_dir is None:
        return save_report(html, config, today)
    original = config.report.output_dir
    config.report.output_dir = output_dir
    try:
        return save_report(html, config, today)
    finally:
        config.report.output_dir = original


def _cancelled_result(ctx: RunContext) -> WeeklyReportResult:
    ctx.log("Weekly pipeline cancelled before signals were generated", level="warning")
    latest = ctx.db.get_latest_trade_date()
    return WeeklyReportResult(
        signal_date=latest or date.today(),
        report_path="",
        order_count=0,
        nav_points=0,
        trade_count=0,
        cancelled=True,
    )
