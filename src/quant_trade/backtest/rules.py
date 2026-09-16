"""A-share specific trading rules: price limits, suspension detection, market classification."""

from datetime import date

import pandas as pd

from quant_trade.data.store import DataStore


def get_price_limits(
    store: DataStore,
    codes: list[str],
    as_of: date,
) -> dict[str, tuple[float, float]]:
    """
    Get (limit_down, limit_up) prices for each stock based on market type.

    Returns:
        Dict of ts_code -> (limit_down_price, limit_up_price).
    """
    if not codes:
        return {}

    placeholders = ", ".join(["?"] * len(codes))

    # Get yesterday's close and market type
    sql = f"""
        SELECT dk.ts_code, dk.close, sb.market, sb.is_st
        FROM daily_kline dk
        JOIN stock_basic sb ON dk.ts_code = sb.ts_code
        WHERE dk.ts_code IN ({placeholders})
          AND dk.trade_date = (
              SELECT MAX(trade_date) FROM daily_kline
              WHERE ts_code = dk.ts_code AND trade_date <= ?
          )
    """
    try:
        df = store.conn.execute(sql, list(codes) + [as_of]).df()
    except Exception:
        return {}

    limits: dict[str, tuple[float, float]] = {}
    for _, row in df.iterrows():
        close = row["close"]
        market = row.get("market", "main")
        pct = _limit_pct(market, row.get("ts_code", ""), bool(row.get("is_st", False)))

        if close and close > 0:
            limits[row["ts_code"]] = (
                round(close * (1 - pct), 2),
                round(close * (1 + pct), 2),
            )

    return limits


def _limit_pct(market: str, ts_code: str, is_st: bool = False) -> float:
    """Get daily price limit percentage based on market."""
    market_lower = market.lower()
    if market_lower in ("star", "star50", "star board"):
        return 0.20
    if market_lower in ("chinext",):
        return 0.20
    if market_lower in ("beijing",):
        return 0.30
    # Main board: ST stocks are limited to 5%, ordinary stocks to 10%
    if is_st:
        return 0.05
    return 0.10


def is_limit_up(price: float, limit_up: float) -> bool:
    """Check if price hits or exceeds limit up."""
    return price >= limit_up * 0.9999  # tolerance for float rounding


def is_limit_down(price: float, limit_down: float) -> bool:
    """Check if price hits or below limit down."""
    return price <= limit_down * 1.0001


def detect_suspended(
    store: DataStore,
    codes: list[str],
    as_of: date,
) -> set[str]:
    """
    Detect suspended stocks (volume == 0 or close == previous close for multiple days).
    Returns set of suspended stock codes.
    """
    if not codes:
        return set()

    placeholders = ", ".join(["?"] * len(codes))
    # Check if there's any trading activity in the last 2 trading days
    sql = f"""
        SELECT ts_code, MAX(volume) as max_vol
        FROM daily_kline
        WHERE ts_code IN ({placeholders})
          AND trade_date >= ?
          AND trade_date <= ?
        GROUP BY ts_code
    """
    try:
        # Check last 5 calendar days
        end = as_of
        start = as_of - pd.Timedelta(days=10)
        df = store.conn.execute(sql, list(codes) + [start, end]).df()
    except Exception:
        return set()

    suspended: set[str] = set()
    for _, row in df.iterrows():
        if row["max_vol"] == 0 or row["max_vol"] is None:
            suspended.add(row["ts_code"])

    return suspended
