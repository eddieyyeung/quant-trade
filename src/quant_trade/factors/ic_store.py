"""Persistence and querying of IC series in DuckDB.

The ``ic_series`` table is the single source of truth for factor IC read paths:
pages and the weekly report read it instead of recomputing, so the same
``(factor_name, trade_date, forward_period)`` never has two answers.
"""

from datetime import date

import pandas as pd
from loguru import logger

from quant_trade.data.store import DataStore

IC_COLUMNS: list[str] = ["factor_name", "trade_date", "forward_period", "ic", "rank_ic", "sample_size"]
"""Column order of the ``ic_series`` table, used for bulk inserts."""


def save_ic_series(store: DataStore, frame: pd.DataFrame) -> int:
    """Write IC records to ``ic_series`` (INSERT OR REPLACE).

    The primary key ``(factor_name, trade_date, forward_period)`` makes a rerun
    idempotent: recomputing a range overwrites its rows instead of duplicating.

    Args:
        store: DataStore with an open connection.
        frame: Long DataFrame with the columns in :data:`IC_COLUMNS`.

    Returns:
        Number of rows written.
    """
    if frame.empty:
        return 0
    missing = [column for column in IC_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"IC frame is missing columns {missing}")
    # Bulk insert via a registered DataFrame relation — executemany would be
    # orders of magnitude slower across a multi-year, multi-factor range.
    store.conn.register("ic_df", frame[IC_COLUMNS])
    store.conn.execute("INSERT OR REPLACE INTO ic_series SELECT * FROM ic_df")
    store.conn.unregister("ic_df")
    return len(frame)


def get_ic_series(
    store: DataStore,
    factors: list[str],
    start: date,
    end: date,
    forward_period: int | None = None,
) -> pd.DataFrame:
    """Query IC records from ``ic_series``.

    Args:
        factors: Factor names to fetch.
        start/end: Trade date range (inclusive).
        forward_period: Restrict to one holding period; ``None`` returns all.

    Returns:
        Long DataFrame ordered by factor, date and holding period. Empty when
        nothing matches — an unknown factor is not an error.
    """
    if not factors:
        return pd.DataFrame(columns=IC_COLUMNS)
    f_ph = ", ".join(["?"] * len(factors))
    sql = f"""
        SELECT {", ".join(IC_COLUMNS)}
        FROM ic_series
        WHERE factor_name IN ({f_ph})
          AND trade_date >= ? AND trade_date <= ?
    """
    params: list[object] = [*factors, start, end]
    if forward_period is not None:
        sql += " AND forward_period = ?"
        params.append(forward_period)
    sql += " ORDER BY factor_name, trade_date, forward_period"
    try:
        return store.conn.execute(sql, params).df()
    except Exception as e:
        logger.warning(f"get_ic_series query failed: {e}")
        return pd.DataFrame(columns=IC_COLUMNS)
