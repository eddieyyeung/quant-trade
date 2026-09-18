"""Unified data access layer over DuckDB."""

import contextlib
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import duckdb
import pandas as pd
from loguru import logger

from quant_trade.data.schema import init_db

TABLE_NAMES: frozenset[str] = frozenset(
    {
        "backtest_metric",
        "backtest_nav",
        "backtest_position",
        "backtest_trade",
        "daily_kline",
        "factor_values",
        "financials",
        "ic_series",
        "index_weights",
        "model_feature_importance",
        "model_ic_series",
        "model_metric",
        "simulator_session",
        "stock_basic",
        "strategy_signal",
        "trade_calendar",
    }
)
"""Tables that :meth:`DataStore.table_stats` will introspect. Anything else is rejected."""

TABLE_DATE_COLUMNS: dict[str, str] = {
    # ``backtest_metric`` (key/value) and ``backtest_position`` (one row per
    # holding) have no date column of their own, so they have no bounds to
    # report. Listing them here would send MIN() at a column that is not there.
    "backtest_nav": "trade_date",
    "backtest_trade": "trade_date",
    "daily_kline": "trade_date",
    "factor_values": "trade_date",
    "financials": "end_date",
    "ic_series": "trade_date",
    "index_weights": "in_date",
    # ``model_feature_importance`` (one row per factor) and ``model_metric``
    # (key/value) have no date column, so they have no bounds to report.
    "model_ic_series": "trade_date",
    "stock_basic": "list_date",
    "strategy_signal": "trade_date",
    "trade_calendar": "trade_date",
}
"""Date column per table, used for the earliest/latest bounds. Absent means no bounds."""


@dataclass
class TableStats:
    """Row count and date span of one table."""

    table: str
    rows: int
    earliest: date | None = None
    latest: date | None = None


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


def _configured_db_path() -> str:
    """The database the application config names.

    Resolved with the same rule the app itself uses — ``QUANT_CONFIG``, else the
    default config file — rather than reimplementing path resolution here.
    """
    from quant_trade.config import AppConfig, get_config_path

    return AppConfig.from_yaml(get_config_path()).data.db_path


class DataStore:
    """Unified query interface for all market data stored in DuckDB.

    Prefer passing a path, or better, receiving a store from your caller. The
    no-argument form exists for scripts and one-off use, and it is *not* a
    constant: it resolves the configured database, so what it opens depends on
    the process environment.
    """

    def __init__(self, db_path: str | None = None) -> None:
        if db_path is None:
            db_path = _configured_db_path()
            # Loud on purpose. The failure this guards against produces wrong
            # numbers, not an exception, so without a trace in the log there is
            # nothing to notice — which is how components ended up reading a
            # different database than their caller's for as long as they did.
            logger.opt(depth=1).warning(
                f"DataStore opened without an explicit path; fell back to the configured database "
                f"{db_path!r}. A component should receive its store from the caller: one that opens "
                f"its own connection reads a database its caller did not choose."
            )
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

    def table_stats(self, table: str) -> TableStats:
        """Row count and date span of ``table``.

        Raises:
            ValueError: if ``table`` is not in :data:`TABLE_NAMES`. The name is
                validated against a fixed allowlist rather than escaped, so an
                unexpected value can never reach the SQL string.
        """
        if table not in TABLE_NAMES:
            raise ValueError(f"Unknown table {table!r}; expected one of {sorted(TABLE_NAMES)}")

        rows = self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        row_count = int(rows[0]) if rows else 0

        date_col = TABLE_DATE_COLUMNS.get(table)
        if date_col is None or row_count == 0:
            return TableStats(table=table, rows=row_count)

        bounds = self.conn.execute(f"SELECT MIN({date_col}), MAX({date_col}) FROM {table}").fetchone()
        if bounds is None:
            return TableStats(table=table, rows=row_count)
        return TableStats(table=table, rows=row_count, earliest=bounds[0], latest=bounds[1])

    def all_table_stats(self) -> dict[str, TableStats]:
        """Stats for every known table, keyed by table name."""
        return {name: self.table_stats(name) for name in sorted(TABLE_NAMES)}

    def synced_codes(self) -> set[str]:
        """Codes that have at least one row in ``daily_kline``."""
        try:
            df = self.conn.execute("SELECT DISTINCT ts_code FROM daily_kline").df()
            return set(df["ts_code"].tolist()) if not df.empty else set()
        except Exception:
            return set()

    def close(self) -> None:
        """Close the database connection, releasing buffer memory first."""
        if self._conn is not None:
            with contextlib.suppress(Exception):
                self._conn.execute("PRAGMA shrink_memory")
            self._conn.close()
            self._conn = None
