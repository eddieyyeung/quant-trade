"""Persistence and querying of Alpha158 factor values in DuckDB."""

from datetime import date

import pandas as pd
from loguru import logger

from quant_trade.data.store import DataStore


def save_factor_values(store: DataStore, values: pd.DataFrame) -> int:
    """Insert factor values into ``factor_values`` (INSERT OR REPLACE).

    Args:
        store: DataStore with an open connection.
        values: Long DataFrame with factor_name, ts_code, trade_date, value.

    Returns:
        Number of rows written.
    """
    if values.empty:
        return 0
    # Bulk insert via a registered DataFrame relation — executemany would be
    # orders of magnitude slower for millions of rows.
    store.conn.register("values_df", values[["factor_name", "ts_code", "trade_date", "value"]])
    store.conn.execute("INSERT OR REPLACE INTO factor_values SELECT * FROM values_df")
    store.conn.unregister("values_df")
    return len(values)


def load_factor_coverage(store: DataStore) -> pd.DataFrame:
    """Per-factor row counts and date span of ``factor_values``.

    One aggregate over the table. Factors with no rows are absent rather than
    present with zeros — the caller distinguishes "not persisted" from
    "persisted but empty", and a zero row would erase that distinction.

    Returns:
        DataFrame with columns ``factor_name``, ``rows``, ``earliest``, ``latest``.
    """
    sql = """
        SELECT factor_name,
               COUNT(*)        AS rows,
               MIN(trade_date) AS earliest,
               MAX(trade_date) AS latest
        FROM factor_values
        GROUP BY factor_name
        ORDER BY factor_name
    """
    try:
        return store.conn.execute(sql).df()
    except Exception as e:
        logger.warning(f"load_factor_coverage query failed: {e}")
        return pd.DataFrame(columns=["factor_name", "rows", "earliest", "latest"])


def get_factor_values(
    store: DataStore,
    factors: list[str],
    universe: list[str],
    start: date,
    end: date,
    wide: bool = False,
) -> pd.DataFrame:
    """Query factor values from ``factor_values``.

    Args:
        factors: Factor names to fetch.
        universe: Stock codes.
        start/end: Trade date range (inclusive).
        wide: If True, return a wide DataFrame (index ts_code+trade_date,
            columns factor_name); otherwise a long DataFrame.

    Returns:
        Long or wide DataFrame. Rows with missing values are kept in wide mode.
    """
    if not factors or not universe:
        return pd.DataFrame()
    f_ph = ", ".join(["?"] * len(factors))
    u_ph = ", ".join(["?"] * len(universe))
    sql = f"""
        SELECT factor_name, ts_code, trade_date, value
        FROM factor_values
        WHERE factor_name IN ({f_ph})
          AND ts_code IN ({u_ph})
          AND trade_date >= ? AND trade_date <= ?
        ORDER BY ts_code, trade_date, factor_name
    """
    params: list[object] = [*factors, *universe, start, end]
    try:
        long_df = store.conn.execute(sql, params).df()
    except Exception as e:
        logger.warning(f"get_factor_values query failed: {e}")
        return pd.DataFrame()

    if not wide or long_df.empty:
        return long_df

    wide_df = long_df.pivot_table(index=["ts_code", "trade_date"], columns="factor_name", values="value")
    wide_df.columns = [str(c) for c in wide_df.columns]
    return wide_df.reset_index()
