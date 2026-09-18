"""Factor-factor correlation.

Pure computation over one long frame — no store, no context.

The correlation between two factors is taken **per trading day across stocks**
and then averaged over days, rather than pooled over all ``(stock, day)``
observations. Pooling mixes the cross-sectional and time-series dimensions:
two factors that happen to drift together over the period show a high
correlation that says nothing about whether they rank the same stocks today.
"""

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import numpy.typing as npt
import pandas as pd

from quant_trade.factors.analysis import MIN_IC_SAMPLE_SIZE, as_date


@dataclass(frozen=True)
class CorrelationResult:
    """A symmetric correlation matrix plus how much data it rests on.

    ``matrix[i][j]`` is the mean cross-sectional correlation between
    ``factors[i]`` and ``factors[j]``. Empty when no day had a usable
    cross-section.
    """

    factors: list[str] = field(default_factory=list)
    matrix: list[list[float]] = field(default_factory=list)
    dates: list[date] = field(default_factory=list)
    skipped_dates: int = 0


def correlation_matrix(
    factor_values: pd.DataFrame,
    min_sample: int = MIN_IC_SAMPLE_SIZE,
) -> CorrelationResult:
    """Average per-day cross-sectional correlation between every factor pair.

    Args:
        factor_values: Long frame with ``factor_name`` / ``ts_code`` /
            ``trade_date`` / ``value``.
        min_sample: Stocks a day needs before it counts as a usable
            cross-section, and the observations a pair needs to contribute.

    Returns:
        A :class:`CorrelationResult` with a symmetric matrix (unit diagonal)
        ordered as ``factors``. Days with too thin a cross-section are counted
        in ``skipped_dates``; no usable day yields an empty matrix, not an error.
    """
    empty = CorrelationResult()
    if factor_values.empty:
        return empty

    names = sorted(str(name) for name in factor_values["factor_name"].unique())
    if len(names) < 2:
        # One factor correlates with itself; there is nothing to report.
        return CorrelationResult(factors=names, matrix=[[1.0]], dates=[])

    wide = factor_values.pivot_table(
        index=["trade_date", "ts_code"],
        columns="factor_name",
        values="value",
        aggfunc="mean",
    )
    if wide.empty:
        return empty
    wide = wide.reindex(columns=names)

    matrices: list[npt.NDArray[np.float64]] = []
    dates: list[date] = []
    skipped = 0
    for day, section in wide.groupby(level="trade_date", sort=True):
        if len(section) < min_sample:
            skipped += 1
            continue
        pairwise = section.corr(method="pearson", min_periods=min_sample)
        matrices.append(pairwise.to_numpy(dtype=float))
        dates.append(as_date(day))

    if not matrices:
        return CorrelationResult(factors=names, skipped_dates=skipped)

    stacked = np.stack(matrices)
    counts = np.sum(~np.isnan(stacked), axis=0)
    # Cells no day could score stay NaN rather than becoming 0 — "unknown" and
    # "uncorrelated" are different answers and the heatmap shows them differently.
    averaged = np.divide(
        np.nansum(stacked, axis=0),
        counts,
        out=np.full_like(stacked[0], np.nan),
        where=counts > 0,
    )
    np.fill_diagonal(averaged, 1.0)

    return CorrelationResult(
        factors=names,
        matrix=[[float(value) for value in row] for row in averaged],
        dates=dates,
        skipped_dates=skipped,
    )
