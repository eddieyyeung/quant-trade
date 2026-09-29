"""Factor quantile (layered) backtest.

Pure computation over two long frames — no store, no context. The result is a
*statistical* view of how well a factor separates the cross-section: equal
weight, fully invested, no costs, no price limits, no suspension handling. It
deliberately does **not** go through :mod:`quant_trade.backtest`, whose job is
simulating a tradable A-share portfolio (T+1, limits, fees). Reading these NAVs
as strategy returns is wrong, and the UI says so.
"""

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from quant_trade.factors.analysis import as_date


@dataclass(frozen=True)
class QuantileSeries:
    """One NAV curve. ``name`` is ``Q1``..``Qn`` (low factor to high) or ``long_short``."""

    name: str
    dates: list[date] = field(default_factory=list)
    values: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class QuantileResult:
    """Group NAVs plus the top-minus-bottom spread.

    Each series starts with a base point of 1.0 on the first rebalance date
    (the level *before* that rebalance's return), then compounds.
    """

    groups: list[QuantileSeries] = field(default_factory=list)
    long_short: QuantileSeries | None = None
    rebalance_dates: list[date] = field(default_factory=list)
    skipped_dates: int = 0


def _assign_groups(values: pd.Series, n_groups: int) -> pd.Series:
    """Split a cross-section into ``n_groups`` balanced buckets, low value first.

    Rank-then-bucket rather than ``qcut``: factor values are routinely tied
    (the same value for many stocks), and ``qcut`` either fails on duplicate
    bin edges or silently produces lopsided groups.
    """
    ranks = values.rank(method="first")
    return ((ranks - 1) * n_groups // len(values)).astype(int)


def quantile_backtest(
    factor_values: pd.DataFrame,
    forward_returns: pd.DataFrame,
    n_groups: int = 5,
) -> QuantileResult:
    """Compute per-group and long-short NAV curves for one factor.

    Args:
        factor_values: Long frame with ``ts_code`` / ``trade_date`` / ``value``.
        forward_returns: Long frame with ``ts_code`` / ``trade_date`` / ``fwd_ret``.
        n_groups: Number of quantile groups (>= 2).

    Returns:
        A :class:`QuantileResult`. Dates whose usable cross-section is smaller
        than ``n_groups`` are skipped and counted in ``skipped_dates``; a
        request with no usable date yields empty groups rather than an error.
    """
    if n_groups < 2:
        raise ValueError(f"n_groups must be at least 2, got {n_groups}")

    empty = QuantileResult()
    if factor_values.empty or forward_returns.empty:
        return empty

    merged = (
        factor_values[["trade_date", "ts_code", "value"]]
        .merge(
            forward_returns[["trade_date", "ts_code", "fwd_ret"]],
            on=["trade_date", "ts_code"],
            how="inner",
        )
        .dropna(subset=["value", "fwd_ret"])
    )
    if merged.empty:
        return empty

    names = [f"Q{i + 1}" for i in range(n_groups)]
    levels: dict[str, list[float]] = {name: [] for name in names}
    spread: list[float] = []
    dates: list[date] = []
    nav: dict[str, float] = dict.fromkeys(names, 1.0)
    long_short_nav = 1.0
    skipped = 0

    for day, section in merged.groupby("trade_date", sort=True):
        if len(section) < n_groups:
            skipped += 1
            continue
        buckets = _assign_groups(section["value"], n_groups)
        means = section.groupby(buckets)["fwd_ret"].mean()

        for index, name in enumerate(names):
            nav[name] *= 1.0 + float(means.get(index, 0.0))
            levels[name].append(nav[name])
        top = float(means.get(n_groups - 1, 0.0))
        bottom = float(means.get(0, 0.0))
        long_short_nav *= 1.0 + (top - bottom)
        spread.append(long_short_nav)
        dates.append(as_date(day))

    if not dates:
        return QuantileResult(skipped_dates=skipped)

    # One base point of 1.0 at the first rebalance date, then one point per
    # rebalance — so every curve starts at 1 and the first date repeats once.
    curve_dates = [dates[0], *dates]
    groups = [QuantileSeries(name=name, dates=curve_dates, values=[1.0, *levels[name]]) for name in names]
    return QuantileResult(
        groups=groups,
        long_short=QuantileSeries(name="long_short", dates=curve_dates, values=[1.0, *spread]),
        rebalance_dates=dates,
        skipped_dates=skipped,
    )
