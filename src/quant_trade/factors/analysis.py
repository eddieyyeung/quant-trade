"""Factor IC (Information Coefficient) analysis tools."""

from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd
from loguru import logger
from scipy import stats

from quant_trade.data.store import DataStore
from quant_trade.factors.alpha158.storage import get_factor_values
from quant_trade.factors.ic_store import IC_COLUMNS


def compute_forward_returns(
    store: DataStore,
    universe: list[str],
    base_date: date,
    periods: list[int] | None = None,
) -> pd.DataFrame:
    """
    Compute forward returns for a set of stocks.

    Args:
        store: DataStore instance.
        universe: List of ts_codes.
        base_date: The signal date.
        periods: Forward periods in trading days (default: [5, 20]).

    Returns:
        DataFrame with columns: ts_code, fwd_5d, fwd_20d
    """
    if periods is None:
        periods = [5, 20]

    max_period = max(periods)
    end = base_date + pd.Timedelta(days=max_period * 2)
    df = store.get_daily(universe, base_date, end, fields=["ts_code", "trade_date", "close"])
    if df.empty:
        return pd.DataFrame()

    results: list[dict[str, Any]] = []
    for code in universe:
        code_df = df[df["ts_code"] == code].sort_values("trade_date")
        if len(code_df) < 2:
            continue
        closes = code_df["close"].values
        base_close = closes[0]
        if base_close <= 0:
            continue

        row: dict[str, str | float] = {"ts_code": code}
        for p in periods:
            if len(closes) > p and closes[p] > 0:
                row[f"fwd_{p}d"] = np.log(closes[p] / base_close)
            else:
                row[f"fwd_{p}d"] = np.nan
        results.append(row)

    return pd.DataFrame(results)


MIN_IC_SAMPLE_SIZE = 10
"""Fewer aligned observations than this makes a correlation meaningless."""


def _align(factor_values: pd.Series, forward_returns: pd.Series) -> pd.DataFrame:
    """Inner-join factor values and forward returns on ts_code, dropping NaNs.

    The single place the IC join lives — both the per-date and the batch path
    go through it so a factor cannot get two different answers.
    """
    aligned = pd.concat([factor_values, forward_returns], axis=1, join="inner")
    aligned.columns = ["factor", "fwd_ret"]
    return aligned.dropna()


def compute_ic(
    factor_values: pd.Series,
    forward_returns: pd.Series,
    method: str = "pearson",
) -> float:
    """
    Compute Information Coefficient between factor values and forward returns.

    Args:
        factor_values: Series indexed by ts_code.
        forward_returns: Series indexed by ts_code.
        method: 'pearson' for IC, 'spearman' for RankIC.

    Returns:
        IC value (float), or NaN if insufficient data.
    """
    aligned = _align(factor_values, forward_returns)
    if len(aligned) < MIN_IC_SAMPLE_SIZE:
        return float("nan")
    return _correlate(aligned, method)


def _correlate(aligned: pd.DataFrame, method: str) -> float:
    """Correlation of an already-aligned frame. Assumes the sample is large enough."""
    if method == "pearson":
        return float(stats.pearsonr(aligned["factor"], aligned["fwd_ret"])[0])
    return float(stats.spearmanr(aligned["factor"], aligned["fwd_ret"])[0])


def compute_ic_pair(factor_values: pd.Series, forward_returns: pd.Series) -> tuple[float, float, int]:
    """Compute IC and RankIC from one alignment.

    Returns:
        (ic, rank_ic, sample_size), both coefficients NaN when the aligned
        sample is too small. Aligning once and taking both coefficients keeps
        the batch path from re-joining the same pair per method.
    """
    aligned = _align(factor_values, forward_returns)
    if len(aligned) < MIN_IC_SAMPLE_SIZE:
        return float("nan"), float("nan"), len(aligned)
    return _correlate(aligned, "pearson"), _correlate(aligned, "spearman"), len(aligned)


def compute_ic_series(
    store: DataStore,
    factor_name: str,
    factor_compute_fn: Callable[[date, list[str]], pd.Series],
    universe: list[str],
    dates: list[date],
    forward_period: int = 5,
) -> dict[str, Any]:
    """
    Compute IC series for a factor across multiple dates.

    Returns a dict with:
        ic_mean: Mean IC
        ic_std: Std of IC
        ic_ir: Information Ratio (IC mean / IC std)
        ic_positive_ratio: Fraction of periods with positive IC
        ic_series: List of (date, ic) tuples
        rank_ic_series: List of (date, rank_ic) tuples
    """
    ic_list: list[float] = []
    rank_ic_list: list[float] = []
    ic_by_date: list[tuple[date, float]] = []
    rank_ic_by_date: list[tuple[date, float]] = []

    for d in dates:
        try:
            factor_vals = factor_compute_fn(d, universe)
            fwd_ret = compute_forward_returns(store, universe, d, periods=[forward_period])
            if fwd_ret.empty or factor_vals.empty:
                continue

            ic, rank_ic, _ = compute_ic_pair(factor_vals, fwd_ret.set_index("ts_code")[f"fwd_{forward_period}d"])

            if not np.isnan(ic):
                ic_list.append(ic)
                ic_by_date.append((d, ic))
            if not np.isnan(rank_ic):
                rank_ic_list.append(rank_ic)
                rank_ic_by_date.append((d, rank_ic))
        except Exception as e:
            logger.warning(f"IC computation failed for {factor_name} on {d}: {e}")

    if not ic_list:
        return {
            "ic_mean": float("nan"),
            "ic_std": float("nan"),
            "ic_ir": float("nan"),
            "ic_positive_ratio": float("nan"),
            "ic_series": [],
            "rank_ic_series": [],
        }

    ic_arr = np.array(ic_list)
    return {
        "ic_mean": float(np.mean(ic_arr)),
        "ic_std": float(np.std(ic_arr)),
        "ic_ir": float(np.mean(ic_arr) / np.std(ic_arr)) if np.std(ic_arr) > 0 else float("nan"),
        "ic_positive_ratio": float(np.sum(ic_arr > 0) / len(ic_arr)),
        "ic_series": ic_by_date,
        "rank_ic_series": rank_ic_by_date,
    }


def _forward_returns_for(
    ordinals_by_code: dict[str, tuple[npt.NDArray[np.int64], npt.NDArray[np.float64]]],
    base_date: date,
    period: int,
) -> pd.Series:
    """Forward log returns for one base date, replicating :func:`compute_forward_returns`.

    Same semantics as the per-date implementation, which queries a window of
    ``base_date + 2 * period`` calendar days and indexes the result by row
    position: the base is the first trading day on or after ``base_date``, and
    the target is the ``period``-th row after it *within that window*. The
    window bound matters — a stock with a long suspension can have a row at
    that offset only beyond the window, and the per-date path would drop it.
    """
    limit = base_date.toordinal() + period * 2
    base_ordinal = base_date.toordinal()
    out: dict[str, float] = {}
    for code, (ordinals, closes) in ordinals_by_code.items():
        first = int(np.searchsorted(ordinals, base_ordinal, side="left"))
        if first >= len(ordinals):
            continue
        # Rows still inside the per-date query window.
        last = int(np.searchsorted(ordinals, limit, side="right"))
        if last - first <= period:
            continue
        base_close = closes[first]
        target_close = closes[first + period]
        if base_close <= 0 or target_close <= 0:
            continue
        out[code] = float(np.log(target_close / base_close))
    return pd.Series(out, dtype=float)


def _kline_arrays(
    store: DataStore, universe: list[str], start: date, end: date
) -> dict[str, tuple[npt.NDArray[np.int64], npt.NDArray[np.float64]]]:
    """Fetch kline once and lay it out as per-stock ordinals/closes arrays."""
    df = store.get_daily(universe, start, end, fields=["ts_code", "trade_date", "close"])
    if df.empty:
        return {}
    df = df.assign(ordinal=pd.to_datetime(df["trade_date"]).dt.date.map(date.toordinal))
    arrays: dict[str, tuple[npt.NDArray[np.int64], npt.NDArray[np.float64]]] = {}
    for code, group in df.groupby("ts_code", sort=False):
        ordered = group.sort_values("ordinal")
        arrays[str(code)] = (
            ordered["ordinal"].to_numpy(dtype=np.int64),
            ordered["close"].to_numpy(dtype=np.float64),
        )
    return arrays


def as_date(value: Any) -> date:
    """Normalise a DuckDB DATE, which arrives as a pandas ``Timestamp``."""
    return value.date() if isinstance(value, datetime) else value


def _factor_series_by_date(factor_long: pd.DataFrame) -> dict[tuple[str, date], pd.Series]:
    """Index long factor rows by ``(factor_name, trade_date)`` for O(1) date lookup.

    Keys are normalised to ``datetime.date``: DuckDB hands DATE columns back as
    pandas ``Timestamp``s, and the callers index this map with plain dates.
    """
    series: dict[tuple[str, date], pd.Series] = {}
    if factor_long.empty:
        return series
    for (name, day), group in factor_long.groupby(["factor_name", "trade_date"], sort=False):
        series[(str(name), as_date(day))] = pd.Series(
            group["value"].to_numpy(dtype=float), index=group["ts_code"].to_numpy()
        )
    return series


def compute_forward_return_frame(
    store: DataStore,
    universe: list[str],
    dates: list[date],
    period: int,
) -> pd.DataFrame:
    """Forward returns for many base dates in one kline query.

    Batch counterpart of :func:`compute_forward_returns` for a single holding
    period, with the same row-offset semantics (see :func:`_forward_returns_for`).

    Returns:
        Long DataFrame with columns ``trade_date``, ``ts_code``, ``fwd_ret``;
        empty when no date could be scored.
    """
    columns = ["trade_date", "ts_code", "fwd_ret"]
    if not universe or not dates:
        return pd.DataFrame(columns=columns)

    start = min(dates)
    end = max(dates) + timedelta(days=period * 2)
    ordinals_by_code = _kline_arrays(store, universe, start, end)
    if not ordinals_by_code:
        return pd.DataFrame(columns=columns)

    rows: list[tuple[date, str, float]] = []
    for day in dates:
        for code, value in _forward_returns_for(ordinals_by_code, day, period).items():
            rows.append((day, str(code), float(value)))
    if not rows:
        return pd.DataFrame(columns=columns)
    frame = pd.DataFrame(rows, columns=columns)
    # datetime64, matching what a read of factor_values returns — callers merge
    # the two frames on trade_date and object-dtype dates would not match.
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    return frame


def compute_ic_frame(
    store: DataStore,
    factors: list[str],
    universe: list[str],
    dates: list[date],
    forward_periods: list[int] | None = None,
) -> pd.DataFrame:
    """Compute IC / RankIC for many factors and holding periods in one pass.

    Unlike :func:`compute_ic_series`, which takes a per-date compute callback
    for factors that are not persisted, this path reads ``factor_values``
    directly and issues exactly **two** queries regardless of how many factors,
    dates or holding periods are asked for: one for factor values, one for
    kline. The IC maths and the sample-size threshold are shared with the
    per-date path, so the two agree day by day.

    Args:
        store: DataStore instance.
        factors: Persisted factor names.
        universe: Stock codes.
        dates: Base (signal) dates.
        forward_periods: Holding periods in trading days (default: [5]).

    Returns:
        Long DataFrame with the columns of :data:`~quant_trade.factors.ic_store.IC_COLUMNS`.
        Dates that cannot be scored are omitted rather than emitted as NaN rows.
    """
    periods = sorted(forward_periods) if forward_periods else [5]
    empty = pd.DataFrame(columns=IC_COLUMNS)
    if not factors or not universe or not dates:
        return empty

    start = min(dates)
    # Two calendar days per trading day, matching compute_forward_returns' window.
    end = max(dates) + timedelta(days=max(periods) * 2)
    factor_long = get_factor_values(store, factors, universe, start, end)
    factor_series = _factor_series_by_date(factor_long)
    if not factor_series:
        return empty

    ordinals_by_code = _kline_arrays(store, universe, start, end)
    if not ordinals_by_code:
        return empty

    rows: list[tuple[str, date, int, float, float, int]] = []
    for period in periods:
        for day in dates:
            forward = _forward_returns_for(ordinals_by_code, day, period)
            if forward.empty:
                continue
            for name in factors:
                values = factor_series.get((name, day))
                if values is None:
                    continue
                ic, rank_ic, sample_size = compute_ic_pair(values, forward)
                if np.isnan(ic) and np.isnan(rank_ic):
                    continue
                rows.append((name, day, period, ic, rank_ic, sample_size))

    if not rows:
        return empty
    frame = pd.DataFrame(rows, columns=IC_COLUMNS).sort_values(
        ["factor_name", "trade_date", "forward_period"], ignore_index=True
    )
    # Emitted as datetime64 rather than bare ``date`` objects so the frame
    # matches what reading the table back gives, and callers can use ``.dt``.
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    return frame
