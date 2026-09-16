"""Feature matrix and label construction for ML training."""

from datetime import date

import pandas as pd
from loguru import logger

from quant_trade.data.store import DataStore
from quant_trade.factors.alpha158.storage import get_factor_values
from quant_trade.factors.preprocess import fill_na_median, standardize, winsorize_mad

LABEL_COLS = ["label"]


def build_label(store: DataStore, universe: list[str], start: date, end: date) -> pd.DataFrame:
    """Build T+2 close-to-close returns: close[t+2] / close[t+1] - 1.

    Uses the trade calendar via per-stock row order (rows are consecutive
    trading days). Samples missing t+1 or t+2 get NaN labels.
    """
    if not universe:
        return pd.DataFrame(columns=["ts_code", "trade_date", "label"])
    kline = store.get_daily(universe, start, end, fields=["ts_code", "trade_date", "close"])
    if kline.empty:
        return pd.DataFrame(columns=["ts_code", "trade_date", "label"])
    # Normalize trade_date to datetime.date objects for consistent comparisons
    kline["trade_date"] = pd.to_datetime(kline["trade_date"]).dt.date
    label_df = kline.sort_values(["ts_code", "trade_date"]).assign(
        label=lambda df: df.groupby("ts_code")["close"].shift(-2) / df.groupby("ts_code")["close"].shift(-1) - 1
    )[["ts_code", "trade_date", "label"]]
    return label_df


def build_feature_matrix(
    store: DataStore,
    universe: list[str],
    start: date,
    end: date,
    factors: list[str] | None = None,
    preprocess: bool = True,
) -> pd.DataFrame:
    """Build the training feature matrix.

    Wide table of Alpha158 factors (index ts_code + trade_date), joined with
    the T+2 label. Cross-sectional preprocessing (3-MAD winsorize, median
    fill, z-score) is applied per trade date.
    """
    if factors is None:
        from quant_trade.factors.alpha158.templates import ALPHA158_NAMES

        factors = ALPHA158_NAMES
    wide = get_factor_values(store, list(factors), universe, start, end, wide=True)
    if wide.empty:
        logger.warning("build_feature_matrix: no factor values available")
        return pd.DataFrame()
    wide["trade_date"] = pd.to_datetime(wide["trade_date"]).dt.date
    labels = build_label(store, universe, start, end)
    if labels.empty:
        logger.warning("build_feature_matrix: no label data available")
        return pd.DataFrame()

    matrix = wide.merge(labels, on=["ts_code", "trade_date"], how="inner")
    feature_cols = [c for c in wide.columns if c not in ("ts_code", "trade_date")]
    if preprocess:
        matrix = _cross_sectional_preprocess(matrix, feature_cols)
    return matrix


def _cross_sectional_preprocess(df: pd.DataFrame, feature_cols: list[str]) -> pd.DataFrame:
    """Apply winsorize(3 MAD) -> median fill -> z-score per trade date.

    ``groupby().transform`` applies each step per cross-section and aligns
    results back to the original rows.
    """
    out = df.copy()
    for col in feature_cols:
        out[col] = out.groupby("trade_date")[col].transform(winsorize_mad)
        out[col] = out.groupby("trade_date")[col].transform(fill_na_median)
        out[col] = out.groupby("trade_date")[col].transform(standardize)
    return out
