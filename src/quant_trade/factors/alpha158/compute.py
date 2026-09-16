"""Batch computation of all Alpha158 factors over a date range and universe."""

from datetime import date, timedelta

import pandas as pd
import polars as pl
from loguru import logger

from quant_trade.data.store import DataStore
from quant_trade.factors.alpha158.templates import all_factors

# Longest rolling window — fetch extra history so the first requested date
# already has a full window.
MAX_WINDOW = 60


def compute_alpha158(
    store: DataStore,
    start: date,
    end: date,
    universe: list[str],
) -> pd.DataFrame:
    """Compute all 158 Alpha158 factors for ``universe`` on [start, end].

    Returns a long DataFrame with columns:
        factor_name, ts_code, trade_date, value
    """
    if not universe:
        logger.warning("compute_alpha158: empty universe")
        return pd.DataFrame(columns=["factor_name", "ts_code", "trade_date", "value"])

    # Fetch enough history for full rolling windows at `start`
    fetch_start = start - timedelta(days=MAX_WINDOW * 3)
    raw = store.get_daily(
        universe,
        fetch_start,
        end,
        fields=["ts_code", "trade_date", "open", "high", "low", "close", "volume", "amount"],
    )
    if raw.empty:
        logger.warning("compute_alpha158: no kline data for the period")
        return pd.DataFrame(columns=["factor_name", "ts_code", "trade_date", "value"])

    df = pl.from_pandas(raw).with_columns((pl.col("amount") / pl.col("volume")).alias("vwap"))

    factors = all_factors()
    # Aggregation yields one list per factor per stock; explode everything
    # except the group key (ts_code stays scalar).
    explode_cols: list[str] = ["trade_date", *factors]
    computed = (
        df.group_by("ts_code", maintain_order=True)
        .agg([pl.col("trade_date")] + [expr.alias(name) for name, expr in factors.items()])
        .explode(explode_cols)
        .select(
            pl.col("ts_code"),
            pl.col("trade_date"),
            *[pl.col(name) for name in factors],
        )
    )

    long_df = (
        computed.filter(pl.col("trade_date") >= start)
        .melt(
            id_vars=["ts_code", "trade_date"],
            variable_name="factor_name",
            value_name="value",
        )
        .drop_nulls(subset=["value"])
    )
    return long_df.to_pandas()
