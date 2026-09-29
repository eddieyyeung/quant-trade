"""Factor-analysis services — IC persistence, layered backtest, correlation, coverage.

This module is the boundary: it decides which data to fetch, reports progress,
checks for cancellation, and turns the compute layer's frames into plain rows.
The maths lives in :mod:`quant_trade.factors`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd
from loguru import logger
from pydantic import Field

from quant_trade.data.index_weights import get_default_universe
from quant_trade.data.store import DataStore
from quant_trade.factors.alpha158.storage import get_factor_values, load_factor_coverage
from quant_trade.factors.analysis import as_date, compute_forward_return_frame, compute_ic_frame
from quant_trade.factors.correlation import correlation_matrix
from quant_trade.factors.ic_store import get_ic_series, save_ic_series
from quant_trade.factors.quantile import QuantileSeries, quantile_backtest
from quant_trade.services.context import NULL_CONTEXT, RunContext
from quant_trade.services.data import DEFAULT_HISTORY_START
from quant_trade.services.params import ServiceParams

MAX_CORRELATION_FACTORS = 50
"""Above this the matrix stops being readable and the compute stops being quick."""

MAX_QUANTILE_GROUPS = 20
DEFAULT_FORWARD_PERIODS: tuple[int, ...] = (1, 5, 10, 20)


class FactorICComputeParams(ServiceParams):
    """Factors, window and holding periods for an IC computation that persists."""

    factors: list[str] = Field(min_length=1)
    """Persisted factor names — IC is read from ``factor_values``, not recomputed."""
    start_date: date | None = None
    """First base date. ``None`` means the default history start."""
    end_date: date | None = None
    """Last base date. ``None`` means the latest trade date."""
    forward_periods: list[int] = Field(default_factory=lambda: list(DEFAULT_FORWARD_PERIODS))
    """Holding periods in trading days; each is stored as its own IC series."""
    universe: list[str] | None = None


class FactorQuantileParams(ServiceParams):
    """One factor, one window, one grouping."""

    factor: str
    start_date: date | None = None
    end_date: date | None = None
    n_groups: int = Field(default=5, ge=2, le=MAX_QUANTILE_GROUPS)
    forward_period: int = Field(default=5, ge=1)
    universe: list[str] | None = None


class FactorCorrelationParams(ServiceParams):
    """The factor set to correlate over a window."""

    factors: list[str] = Field(min_length=2, max_length=MAX_CORRELATION_FACTORS)
    start_date: date | None = None
    end_date: date | None = None
    universe: list[str] | None = None


class FactorCoverageParams(ServiceParams):
    """No filters — coverage is a property of what is stored."""


class FactorICQueryParams(ServiceParams):
    """Read back a persisted IC series for one factor and holding period."""

    factor: str
    start_date: date | None = None
    """First date to read. ``None`` means the default history start."""
    end_date: date | None = None
    """Last date to read. ``None`` means today."""
    forward_period: int = Field(default=5, ge=1)


class FactorICOverviewParams(ServiceParams):
    """Window for a per-factor IC digest over every factor that has IC rows."""

    as_of: date | None = None
    """Last date to read. ``None`` means today."""
    lookback_days: int = Field(default=365, ge=1)
    """How far back the window reaches. The mean and IR are computed over it."""
    forward_period: int = Field(default=5, ge=1)


@dataclass
class FactorICPoint:
    """One day of a persisted IC series."""

    trade_date: date
    ic: float | None = None
    rank_ic: float | None = None
    sample_size: int = 0


@dataclass
class FactorICSeriesResult:
    """A persisted IC series with its summary statistics.

    Read straight from ``ic_series`` — the read path never recomputes IC, so a
    factor cannot show a different history in two places.
    """

    factor: str = ""
    forward_period: int = 0
    start: date | None = None
    end: date | None = None
    points: list[FactorICPoint] = field(default_factory=list)
    ic_mean: float | None = None
    ic_std: float | None = None
    ic_ir: float | None = None
    ic_positive_ratio: float | None = None


@dataclass
class FactorICOverviewRow:
    """One factor's IC digest, shaped for the weekly report's IC panel.

    ``ic_weekly`` is the most recent day's IC rather than a calendar week's:
    IC is computed per trading day and the holding period is not a week, so
    imposing a calendar boundary would invent an edge the data does not have.
    """

    name: str
    ic_weekly: float | None = None
    ic_mean: float | None = None
    ic_ir: float | None = None
    sample_days: int = 0


@dataclass
class FactorICDecayEntry:
    """One holding period's worth of IC, for the decay comparison."""

    forward_period: int
    ic_mean: float | None = None
    ic_positive_ratio: float | None = None
    count: int = 0


@dataclass
class FactorICDecayResult:
    """IC by holding period — how fast a factor's edge fades."""

    factor: str = ""
    start: date | None = None
    end: date | None = None
    entries: list[FactorICDecayEntry] = field(default_factory=list)


@dataclass
class FactorICComputeResult:
    """Outcome of an IC computation, including how much of it landed in the table."""

    factors: list[str] = field(default_factory=list)
    forward_periods: list[int] = field(default_factory=list)
    start: date | None = None
    end: date | None = None
    rows_saved: int = 0
    completed_factors: int = 0
    universe_size: int = 0
    cancelled: bool = False


@dataclass
class FactorQuantileResult:
    """Layered NAV curves for one factor."""

    factor: str = ""
    n_groups: int = 0
    forward_period: int = 0
    series: list[QuantileSeries] = field(default_factory=list)
    long_short: QuantileSeries | None = None
    rebalance_dates: list[date] = field(default_factory=list)
    skipped_dates: int = 0
    cancelled: bool = False


@dataclass
class FactorCorrelationResult:
    """Correlation matrix plus the amount of data behind it.

    Cells are ``None`` where no pair could be scored — a zero-variance factor
    has no correlation, and the response has to be valid JSON.
    """

    factors: list[str] = field(default_factory=list)
    matrix: list[list[float | None]] = field(default_factory=list)
    date_count: int = 0
    skipped_dates: int = 0
    cancelled: bool = False


@dataclass
class FactorCoverageEntry:
    """Persisted span of one factor."""

    name: str
    rows: int
    earliest: date | None = None
    latest: date | None = None


@dataclass
class FactorCoverageResult:
    """Per-factor persistence coverage. Unpersisted factors are simply absent."""

    entries: list[FactorCoverageEntry] = field(default_factory=list)


def _as_date(value: object) -> date | None:
    """``as_date`` with a ``None`` passthrough — coverage spans may be empty."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return as_date(value)


def _as_float(value: object) -> float | None:
    """NaN-tolerant float, so a stored NULL round-trips as ``null`` not ``NaN``."""
    if value is None:
        return None
    number = float(value)  # type: ignore[arg-type]
    return None if math.isnan(number) else number


def _as_int(value: object, default: int = 0) -> int:
    """Whole-number field that may be NULL in the table."""
    if value is None:
        return default
    number = float(value)  # type: ignore[arg-type]
    return default if math.isnan(number) else int(number)


def _resolve(params: ServiceParams, ctx: RunContext) -> tuple[DataStore, date, date, list[str]]:
    """Shared window/universe resolution for the factor-analysis services."""
    store = ctx.db
    end = getattr(params, "end_date", None) or store.get_latest_trade_date()
    if end is None:
        raise ValueError("Database has no trade dates; run a data sync first")
    start = getattr(params, "start_date", None) or DEFAULT_HISTORY_START
    universe = getattr(params, "universe", None) or store.get_universe(get_default_universe(), end)
    return store, start, end, universe


def _dates_with_values(frame: pd.DataFrame) -> list[date]:
    """Base dates to score, taken from the data rather than the calendar.

    A date the factor has no values for cannot produce a result either way, so
    deriving the list here saves a calendar round-trip and keeps the queries at
    one per data source.
    """
    if frame.empty:
        return []
    return sorted({as_date(day) for day in frame["trade_date"].unique()})


def compute_factor_ic(params: FactorICComputeParams, ctx: RunContext = NULL_CONTEXT) -> FactorICComputeResult:
    """Compute IC / RankIC across factors and holding periods, and persist them.

    Factors are looped one at a time so cancellation lands on a factor boundary
    and finished factors keep their rows; each factor's whole date range is
    fetched in one batch, which is where the old per-date query storm used to be.
    """
    store, start, end, universe = _resolve(params, ctx)
    periods = sorted(set(params.forward_periods))
    ctx.progress(0.0, f"Computing IC for {len(params.factors)} factors over [{start}, {end}]")

    values = get_factor_values(store, params.factors, universe, start, end)
    if values.empty:
        ctx.log(f"No factor values in [{start}, {end}]; nothing to compute", level="warning")
        return FactorICComputeResult(
            factors=params.factors,
            forward_periods=periods,
            start=start,
            end=end,
            universe_size=len(universe),
            cancelled=ctx.cancelled(),
        )

    dates = _dates_with_values(values)
    rows_saved = 0
    completed: list[str] = []
    total = len(params.factors)

    for index, name in enumerate(params.factors, start=1):
        if ctx.cancelled():
            ctx.log(f"IC computation cancelled after {len(completed)}/{total} factors", level="warning")
            break
        frame = compute_ic_frame(store, [name], universe, dates, periods)
        written = save_ic_series(store, frame)
        rows_saved += written
        if frame.empty:
            ctx.log(f"Factor {name!r} produced no IC rows (thin cross-section or missing values)", level="warning")
        else:
            completed.append(name)
        ctx.log(f"Computed IC for {name}: {written} rows")
        ctx.progress(index / total, f"Computed IC for {index}/{total} factors")

    return FactorICComputeResult(
        factors=params.factors,
        forward_periods=periods,
        start=start,
        end=end,
        rows_saved=rows_saved,
        completed_factors=len(completed),
        universe_size=len(universe),
        cancelled=ctx.cancelled(),
    )


def factor_quantile_backtest(params: FactorQuantileParams, ctx: RunContext = NULL_CONTEXT) -> FactorQuantileResult:
    """Group the cross-section by factor value and compound each group's return.

    A statistical view of factor separation, not a tradable portfolio: equal
    weight, no costs, no price limits. See :mod:`quant_trade.factors.quantile`.
    """
    store, start, end, universe = _resolve(params, ctx)
    ctx.progress(0.0, f"Fetching {params.factor} over [{start}, {end}]")
    values = get_factor_values(store, [params.factor], universe, start, end)
    if values.empty:
        ctx.log(f"Factor {params.factor!r} has no values in [{start}, {end}]", level="warning")
        return FactorQuantileResult(
            factor=params.factor, n_groups=params.n_groups, forward_period=params.forward_period
        )

    dates = _dates_with_values(values)
    ctx.progress(0.5, f"Computing forward returns for {len(dates)} dates")
    forward = compute_forward_return_frame(store, universe, dates, params.forward_period)

    result = quantile_backtest(values, forward, n_groups=params.n_groups)
    if not result.groups:
        ctx.log("No date had a cross-section wide enough to group", level="warning")
    ctx.progress(1.0, f"Layered backtest done over {len(result.rebalance_dates)} rebalances")
    return FactorQuantileResult(
        factor=params.factor,
        n_groups=params.n_groups,
        forward_period=params.forward_period,
        series=result.groups,
        long_short=result.long_short,
        rebalance_dates=result.rebalance_dates,
        skipped_dates=result.skipped_dates,
        cancelled=ctx.cancelled(),
    )


def factor_correlation(params: FactorCorrelationParams, ctx: RunContext = NULL_CONTEXT) -> FactorCorrelationResult:
    """Mean cross-sectional correlation between every pair of the given factors."""
    store, start, end, universe = _resolve(params, ctx)
    ctx.progress(0.0, f"Fetching {len(params.factors)} factors over [{start}, {end}]")
    values = get_factor_values(store, params.factors, universe, start, end)
    if values.empty:
        ctx.log(f"No factor values in [{start}, {end}]; nothing to correlate", level="warning")
        return FactorCorrelationResult(factors=sorted(params.factors))

    result = correlation_matrix(values)
    if not result.matrix:
        ctx.log("No date had a cross-section wide enough to correlate", level="warning")
    ctx.progress(1.0, f"Correlated {len(result.factors)} factors over {len(result.dates)} dates")
    return FactorCorrelationResult(
        factors=result.factors,
        # NaN is not valid JSON and Starlette rejects it; an unscorable pair is
        # "unknown", which is what null means to the client.
        matrix=[[_as_float(cell) for cell in row] for row in result.matrix],
        date_count=len(result.dates),
        skipped_dates=result.skipped_dates,
        cancelled=ctx.cancelled(),
    )


def factor_coverage(params: FactorCoverageParams, ctx: RunContext = NULL_CONTEXT) -> FactorCoverageResult:
    """Report how much of each factor is actually persisted.

    Factors absent from the result are not stored at all — the caller must not
    read a missing entry as "stored, zero rows".
    """
    frame = load_factor_coverage(ctx.db)
    entries = [
        FactorCoverageEntry(
            name=str(record["factor_name"]),
            rows=int(record["rows"]),
            earliest=_as_date(record["earliest"]),
            latest=_as_date(record["latest"]),
        )
        for record in frame.to_dict("records")
    ]
    return FactorCoverageResult(entries=entries)


def _ic_summary(values: pd.Series) -> tuple[float | None, float | None, float | None, float | None]:
    """Mean/std/IR/positive-ratio of a series of ICs, tolerating gaps.

    Returns four ``None``s when nothing is usable, so a factor with no data
    reads as "no data" rather than as zeros.
    """
    clean = values.dropna()
    if clean.empty:
        return None, None, None, None
    mean = float(clean.mean())
    std = float(clean.std(ddof=0))
    return mean, std, (mean / std if std > 0 else None), float((clean > 0).sum() / len(clean))


def factor_ic_series(params: FactorICQueryParams, ctx: RunContext = NULL_CONTEXT) -> FactorICSeriesResult:
    """Read a persisted IC series plus its summary statistics.

    An unknown factor or an uncovered window yields an empty series rather than
    an error — "not computed yet" is a normal state for this page.
    """
    end = params.end_date or date.today()
    start = params.start_date or DEFAULT_HISTORY_START
    frame = get_ic_series(ctx.db, [params.factor], start, end, forward_period=params.forward_period)
    points = [
        FactorICPoint(
            trade_date=as_date(record["trade_date"]),
            ic=_as_float(record["ic"]),
            rank_ic=_as_float(record["rank_ic"]),
            sample_size=_as_int(record["sample_size"]),
        )
        for record in frame.to_dict("records")
    ]
    means = _ic_summary(frame["ic"]) if not frame.empty else (None, None, None, None)
    return FactorICSeriesResult(
        factor=params.factor,
        forward_period=params.forward_period,
        start=start,
        end=end,
        points=points,
        ic_mean=means[0],
        ic_std=means[1],
        ic_ir=means[2],
        ic_positive_ratio=means[3],
    )


def factor_ic_overview(params: FactorICOverviewParams, ctx: RunContext = NULL_CONTEXT) -> list[FactorICOverviewRow]:
    """One IC digest per factor that has rows in ``ic_series``.

    Deliberately one read for all factors rather than a loop of
    ``factor_ic_series`` calls: ``get_ic_series`` already takes a list of names,
    and the per-factor read path pays one query only because it shows a single
    factor at a time. Summary statistics come from ``_ic_summary`` so this digest
    and the IC-analysis page never disagree about the same rows.

    A factor with no rows in the window is skipped rather than returned blank —
    an empty row would render as a factor that looks like it has no edge.
    """
    end = params.as_of or date.today()
    start = end - timedelta(days=params.lookback_days)
    names = _factors_with_ic(ctx.db)
    if not names:
        return []
    frame = get_ic_series(ctx.db, names, start, end, forward_period=params.forward_period)
    if frame.empty:
        return []

    rows: list[FactorICOverviewRow] = []
    for name, group in frame.groupby("factor_name", sort=True):
        ordered = group.sort_values("trade_date")
        mean, _std, ir, _positive = _ic_summary(ordered["ic"])
        rows.append(
            FactorICOverviewRow(
                name=str(name),
                ic_weekly=_as_float(ordered.iloc[-1]["ic"]),
                ic_mean=mean,
                ic_ir=ir,
                sample_days=int(ordered["ic"].notna().sum()),
            )
        )
    return rows


def _factors_with_ic(store: DataStore) -> list[str]:
    """Factor names that actually have IC rows.

    Not ``list_factor_names``: that reads ``factor_values`` and would hand back
    factors whose IC was never computed, which the panel would show as blank.
    """
    try:
        records = store.conn.execute("SELECT DISTINCT factor_name FROM ic_series ORDER BY factor_name").fetchall()
    except Exception as e:
        logger.warning(f"ic_series factor name query failed: {e}")
        return []
    return [str(record[0]) for record in records]


def factor_ic_decay(params: FactorICQueryParams, ctx: RunContext = NULL_CONTEXT) -> FactorICDecayResult:
    """IC mean per holding period for one factor — how fast its edge fades."""
    end = params.end_date or date.today()
    start = params.start_date or DEFAULT_HISTORY_START
    frame = get_ic_series(ctx.db, [params.factor], start, end)
    entries: list[FactorICDecayEntry] = []
    if not frame.empty:
        for period, group in frame.groupby("forward_period", sort=True):
            mean, _std, _ir, positive = _ic_summary(group["ic"])
            entries.append(
                FactorICDecayEntry(
                    forward_period=_as_int(period),
                    ic_mean=mean,
                    ic_positive_ratio=positive,
                    count=int(group["ic"].notna().sum()),
                )
            )
    return FactorICDecayResult(factor=params.factor, start=start, end=end, entries=entries)
