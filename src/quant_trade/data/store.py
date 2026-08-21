"""Unified data access layer over DuckDB."""

import contextlib
from datetime import date, timedelta
from typing import Any

import duckdb
import pandas as pd
from loguru import logger

from quant_trade.data.schema import init_db


def _min_list_date(store: "DataStore", as_of: date, min_list_days: int) -> date:
    """
    Return the trade date ``min_list_days`` trading days before ``as_of``.

    Stocks must be listed on or before this date to be eligible. If the
    calendar does not have enough history, falls back to ``as_of`` so the
    filter degrades gracefully on short/sample databases.
    """
    if min_list_days <= 0:
        return as_of
    cal = store.get_calendar(date(2005, 1, 1), as_of, open_only=True)
    date_list: list[date] = cal["trade_date"].tolist() if not cal.empty else []
    if len(date_list) > min_list_days:
        return date_list[-min_list_days]
    return as_of


class DataStore:
    """Unified query interface for all market data stored in DuckDB."""

    def __init__(self, db_path: str = "data/quant.db"):
        self.db_path = db_path
        self._conn: duckdb.DuckDBPyConnection | None = None

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = init_db(self.db_path)
        return self._conn

    def get_daily(
        self,
        ts_codes: list[str],
        start: date,
        end: date,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """
        Fetch daily kline data from DuckDB.
        """
        if not ts_codes:
            return pd.DataFrame()
        cols = ", ".join(fields) if fields else "*"
        placeholders = ", ".join(["?"] * len(ts_codes))
        sql = f"""
            SELECT {cols}
            FROM daily_kline
            WHERE ts_code IN ({placeholders})
              AND trade_date >= ?
              AND trade_date <= ?
            ORDER BY ts_code, trade_date
        """
        params: list[Any] = list(ts_codes) + [start, end]
        try:
            return self.conn.execute(sql, params).df()
        except Exception as e:
            logger.warning(f"get_daily query failed: {e}")
            return pd.DataFrame()

    def get_financials(
        self,
        ts_codes: list[str],
        as_of: date,
    ) -> pd.DataFrame:
        """
        Fetch latest financial data as of a given date, respecting ann_date
        to avoid look-ahead bias.
        """
        if not ts_codes:
            return pd.DataFrame()
        placeholders = ", ".join(["?"] * len(ts_codes))
        sql = f"""
            SELECT f.*
            FROM financials f
            JOIN (
                SELECT ts_code, MAX(end_date) AS max_end
                FROM financials
                WHERE ts_code IN ({placeholders})
                  AND ann_date <= ?
                GROUP BY ts_code
            ) latest
            ON f.ts_code = latest.ts_code AND f.end_date = latest.max_end
        """
        params: list[Any] = list(ts_codes) + [as_of]
        try:
            return self.conn.execute(sql, params).df()
        except Exception as e:
            logger.warning(f"get_financials query failed: {e}")
            return pd.DataFrame()

    def get_universe(
        self,
        index_codes: list[str],
        as_of: date,
        filter_st: bool = True,
        min_list_days: int = 250,
    ) -> list[str]:
        """
        Get stock universe from index constituents.

        Stocks listed fewer than ``min_list_days`` trading days before ``as_of``
        are excluded (recent IPOs are not investable).

        When no index constituents match (e.g., historical sessions before
        index weights were synced), falls back to stocks with kline data in
        the trailing 60 days — the data-driven tradable universe.
        """
        if not index_codes:
            return []

        min_list_date = _min_list_date(self, as_of, min_list_days)
        placeholders = ", ".join(["?"] * len(index_codes))
        sql = f"""
            SELECT DISTINCT iw.ts_code
            FROM index_weights iw
            JOIN stock_basic sb ON iw.ts_code = sb.ts_code
            WHERE iw.index_code IN ({placeholders})
              AND iw.in_date <= ?
              AND (iw.out_date IS NULL OR iw.out_date > ?)
              AND (sb.list_date IS NULL OR sb.list_date <= ?)
        """
        params: list[Any] = list(index_codes) + [as_of, as_of, min_list_date]
        try:
            df = self.conn.execute(sql, params).df()
            codes: list[str] = df["ts_code"].tolist() if not df.empty else []
            if not codes:
                logger.info(
                    "No index constituents for {}; falling back to kline-derived universe",
                    as_of,
                )
                codes = self._universe_from_kline(as_of)
            return self._exclude_st(codes) if filter_st and codes else codes
        except Exception as e:
            logger.warning(f"get_universe query failed: {e}")
            return []

    def _universe_from_kline(self, as_of: date, lookback_days: int = 60) -> list[str]:
        """Derive universe from stocks with kline data in the trailing window."""
        sql = """
            SELECT DISTINCT ts_code FROM daily_kline
            WHERE trade_date >= ? AND trade_date <= ?
        """
        try:
            df = self.conn.execute(sql, [as_of - timedelta(days=lookback_days), as_of]).df()
            return df["ts_code"].tolist() if not df.empty else []
        except Exception as e:
            logger.warning(f"_universe_from_kline query failed: {e}")
            return []

    def _exclude_st(self, codes: list[str]) -> list[str]:
        """Remove ST stocks from a code list."""
        if not codes:
            return codes
        placeholders = ", ".join(["?"] * len(codes))
        st_sql = f"""
            SELECT ts_code FROM stock_basic
            WHERE ts_code IN ({placeholders}) AND is_st = TRUE
        """
        try:
            st_df = self.conn.execute(st_sql, codes).df()
            st_set = set(st_df["ts_code"].tolist())
            return [c for c in codes if c not in st_set]
        except Exception as e:
            logger.warning(f"_exclude_st query failed: {e}")
            return codes

    def get_calendar(self, start: date, end: date, open_only: bool = True) -> pd.DataFrame:
        """Fetch trade calendar."""
        sql = """
            SELECT trade_date, is_open
            FROM trade_calendar
            WHERE trade_date >= ? AND trade_date <= ?
        """
        params: list[Any] = [start, end]
        if open_only:
            sql += " AND is_open = TRUE"
        sql += " ORDER BY trade_date"
        try:
            return self.conn.execute(sql, params).df()
        except Exception as e:
            logger.warning(f"get_calendar query failed: {e}")
            return pd.DataFrame()

    def get_latest_trade_date(self, before: date | None = None) -> date | None:
        """Get the most recent trading day."""
        if before is None:
            before = date.today()
        sql = """
            SELECT MAX(trade_date) FROM trade_calendar
            WHERE trade_date <= ? AND is_open = TRUE
        """
        try:
            row = self.conn.execute(sql, [before]).fetchone()
            return row[0] if row else None
        except Exception:
            return None

    def close(self) -> None:
        """Close the database connection, releasing buffer memory first."""
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.execute("PRAGMA shrink_memory")
            self._conn.close()
            self._conn = None
