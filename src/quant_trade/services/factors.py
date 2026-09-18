"""Factor-domain services — batch factor computation, Alpha158 and IC analysis."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from quant_trade.config import AppConfig
from quant_trade.data.index_weights import get_default_universe
from quant_trade.factors.analysis import compute_ic_series
from quant_trade.factors.registry import registry as factor_registry
from quant_trade.services.context import NULL_CONTEXT, RunContext
from quant_trade.services.data import DEFAULT_HISTORY_START
from quant_trade.services.params import ServiceParams
from quant_trade.services.queries import FactorNamesParams, list_factor_names

ALPHA158_CHUNK_DAYS = 90
"""Calendar days per Alpha158 computation chunk — the cancellation granularity."""


def _chunk_range(start: date, end: date, size: int) -> list[tuple[date, date]]:
    """Split ``[start, end]`` into contiguous inclusive chunks of ``size`` days."""
    if size < 1:
        raise ValueError(f"chunk size must be positive, got {size}")
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=size - 1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


class FactorComputeParams(ServiceParams):
    """Which factors to compute, for which date and universe."""

    factors: list[str] | None = None
    """Factor names. ``None`` means every factor enabled in config."""
    as_of: date | None = None
    """Signal date. ``None`` means the latest trade date in the database."""
    universe: list[str] | None = None
    """Explicit codes. ``None`` means the default index-derived universe."""

    @classmethod
    def config_defaults(cls, config: AppConfig) -> dict[str, object]:
        return {"factors": list(config.factor.enabled)}


class Alpha158Params(ServiceParams):
    """Window and universe for a full Alpha158 computation."""

    start_date: date | None = None
    """First date to compute. ``None`` means the default history start."""
    end_date: date | None = None
    """Last date to compute. ``None`` means the latest trade date."""
    universe: list[str] | None = None


class FactorICParams(ServiceParams):
    """Factors, dates and horizon for an IC summary."""

    factors: list[str] | None = None
    """Factor names. ``None`` means every factor enabled in config."""
    dates: list[date] | None = None
    """Dates to measure. ``None`` means the latest trade date only."""
    forward_period: int = 5
    universe: list[str] | None = None

    @classmethod
    def config_defaults(cls, config: AppConfig) -> dict[str, object]:
        return {"factors": list(config.factor.enabled)}


class FactorListParams(ServiceParams):
    """Filter for the factor registry listing."""

    category: str | None = None


@dataclass
class FactorComputeResult:
    """Per-factor outcome of a batch computation."""

    as_of: date
    counts: dict[str, int] = field(default_factory=dict)
    """Factor name to number of values produced."""
    failed: dict[str, str] = field(default_factory=dict)
    """Factor name to failure reason."""
    cancelled: bool = False


@dataclass
class Alpha158Result:
    """Outcome of an Alpha158 computation.

    Rows are accumulated across computation chunks, so a cancelled run still
    reports what it managed to persist.
    """

    start: date
    end: date
    rows_saved: int
    factor_count: int
    universe_size: int
    cancelled: bool = False


@dataclass
class FactorICSummary:
    """IC statistics per factor, keyed by factor name."""

    forward_period: int
    dates: list[date]
    results: dict[str, dict[str, Any]]


@dataclass
class FactorInfo:
    """One row in the factor registry listing."""

    name: str
    category: str
    persisted: bool


@dataclass
class FactorListResult:
    """The factor registry, annotated with persistence status."""

    factors: list[FactorInfo]
    persisted_names: list[str]


def compute_factors(params: FactorComputeParams, ctx: RunContext = NULL_CONTEXT) -> FactorComputeResult:
    """Compute each requested factor for one date and persist nothing.

    Factors that fail are reported in :attr:`FactorComputeResult.failed` rather
    than aborting the batch, so one bad factor does not lose the rest.
    """
    store = ctx.db
    as_of = params.as_of or store.get_latest_trade_date()
    if as_of is None:
        raise ValueError("Database has no trade dates; run a data sync first")
    universe = params.universe or store.get_universe(get_default_universe(), as_of)
    names = params.factors if params.factors is not None else list(ctx.cfg.factor.enabled)

    counts: dict[str, int] = {}
    failed: dict[str, str] = {}
    total = len(names)
    ctx.progress(0.0, f"Computing {total} factors for {len(universe)} stocks")

    for index, name in enumerate(names, start=1):
        if ctx.cancelled():
            ctx.log(f"Factor computation cancelled after {index - 1}/{total} factors", level="warning")
            break
        factor = factor_registry.get(name, store=ctx.db)
        if factor is None:
            ctx.log(f"Factor {name!r} is not registered", level="warning")
            failed[name] = "not registered"
        else:
            try:
                counts[name] = len(factor.compute(as_of, universe))
            except Exception as e:
                ctx.log(f"Factor {name} failed: {e}", level="error")
                failed[name] = str(e)
        ctx.progress(index / total if total else 1.0, f"Computed {index}/{total} factors")

    return FactorComputeResult(as_of=as_of, counts=counts, failed=failed, cancelled=ctx.cancelled())


def compute_alpha158(params: Alpha158Params, ctx: RunContext = NULL_CONTEXT) -> Alpha158Result:
    """Compute and persist the full Alpha158 factor set.

    The range is computed in chunks so the run has cancellation boundaries. One
    vectorised call over several years is a single uninterruptible step; per
    chunk the engine re-fetches its own warmup, and since the longest rolling
    window is far shorter than the warmup, chunked values equal unchunked ones
    (asserted in the tests).
    """
    from quant_trade.factors.alpha158 import compute_alpha158 as _compute
    from quant_trade.factors.alpha158 import save_factor_values

    store = ctx.db
    end = params.end_date or store.get_latest_trade_date()
    if end is None:
        raise ValueError("Database has no trade dates; run a data sync first")
    start = params.start_date or DEFAULT_HISTORY_START
    universe = params.universe or store.get_universe(get_default_universe(), end)

    chunks = _chunk_range(start, end, ALPHA158_CHUNK_DAYS)
    total = len(chunks)
    ctx.progress(0.0, f"Computing Alpha158 for {len(universe)} stocks in {total} chunk(s) ({start} → {end})")

    rows_saved = 0
    factors: set[str] = set()
    cancelled = False
    completed = 0
    for index, (chunk_start, chunk_end) in enumerate(chunks, start=1):
        if ctx.cancelled():
            ctx.log(f"Alpha158 cancelled after {completed}/{total} chunks", level="warning")
            cancelled = True
            break
        values = _compute(store, chunk_start, chunk_end, universe)
        if values.empty:
            ctx.log(f"No values in [{chunk_start}, {chunk_end}]; check kline coverage", level="warning")
            continue
        factors.update(str(name) for name in values["factor_name"].unique())
        rows_saved += save_factor_values(store, values)
        completed = index
        ctx.progress(index / total, f"Saved {rows_saved} rows ({index}/{total} chunks)")

    if rows_saved == 0:
        ctx.log("Alpha158 produced no values; check kline coverage", level="warning")
    elif cancelled:
        # A cancelled run is not complete: report the share it actually did.
        ctx.progress(completed / total, f"Cancelled; saved {rows_saved} rows ({completed}/{total} chunks)")
    else:
        ctx.progress(1.0, f"Saved {rows_saved} rows")
    return Alpha158Result(
        start=start,
        end=end,
        rows_saved=rows_saved,
        factor_count=len(factors),
        universe_size=len(universe),
        cancelled=cancelled,
    )


def factor_ic_summary(params: FactorICParams, ctx: RunContext = NULL_CONTEXT) -> FactorICSummary:
    """IC and RankIC statistics per factor over the requested dates."""
    store = ctx.db
    latest = store.get_latest_trade_date()
    if latest is None:
        raise ValueError("Database has no trade dates; run a data sync first")
    dates = params.dates or [latest]
    universe = params.universe or store.get_universe(get_default_universe(), latest)
    names = params.factors if params.factors is not None else list(ctx.cfg.factor.enabled)

    results: dict[str, dict[str, Any]] = {}
    total = len(names)
    for index, name in enumerate(names, start=1):
        if ctx.cancelled():
            ctx.log(f"IC computation cancelled after {index - 1}/{total} factors", level="warning")
            break
        factor = factor_registry.get(name, store=ctx.db)
        if factor is None:
            ctx.log(f"Factor {name!r} is not registered", level="warning")
            continue
        results[name] = compute_ic_series(
            store,
            name,
            lambda d, u, factor=factor: factor.compute(d, u),  # type: ignore[misc]
            universe,
            dates,
            forward_period=params.forward_period,
        )
        ctx.progress(index / total if total else 1.0, f"Computed IC for {index}/{total} factors")

    return FactorICSummary(forward_period=params.forward_period, dates=dates, results=results)


def list_factors(params: FactorListParams, ctx: RunContext = NULL_CONTEXT) -> FactorListResult:
    """List registered factors with their category and persistence status."""
    persisted = set(list_factor_names(FactorNamesParams(), ctx))
    infos: list[FactorInfo] = []
    for name in factor_registry.list_all():
        factor = factor_registry.get(name, store=ctx.db)
        if factor is None:
            continue
        category = factor.category.value
        if params.category is not None and category != params.category:
            continue
        infos.append(FactorInfo(name=name, category=category, persisted=name in persisted))
    return FactorListResult(factors=infos, persisted_names=sorted(persisted))
