"""Factor IC (Information Coefficient) analysis tools."""

from collections.abc import Callable
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger
from scipy import stats

from quant_trade.data.store import DataStore


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
    aligned = pd.concat([factor_values, forward_returns], axis=1, join="inner")
    aligned.columns = ["factor", "fwd_ret"]
    aligned = aligned.dropna()

    if len(aligned) < 10:
        return float("nan")

    if method == "pearson":
        return float(stats.pearsonr(aligned["factor"], aligned["fwd_ret"])[0])
    else:
        return float(stats.spearmanr(aligned["factor"], aligned["fwd_ret"])[0])


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

            ic = compute_ic(factor_vals, fwd_ret.set_index("ts_code")[f"fwd_{forward_period}d"], method="pearson")
            rank_ic = compute_ic(factor_vals, fwd_ret.set_index("ts_code")[f"fwd_{forward_period}d"], method="spearman")

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
