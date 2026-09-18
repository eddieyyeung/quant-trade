"""Data sync orchestrator — fetch from sources and write to DuckDB."""

from datetime import date, timedelta

from loguru import logger

from quant_trade.data.sources.akshare_adapter import AkshareAdapter
from quant_trade.data.sources.base import DataSource
from quant_trade.data.sources.tushare_adapter import TushareAdapter
from quant_trade.data.store import DataStore
from quant_trade.services.context import NULL_CONTEXT, RunContext


def get_primary_source(store: DataStore, name: str = "akshare") -> DataSource:
    """Get the primary data source adapter."""
    if name == "akshare":
        return AkshareAdapter()
    if name == "tushare":
        return TushareAdapter()
    raise ValueError(f"Unknown data source: {name}")


def get_backup_sources(store: DataStore, names: list[str]) -> list[DataSource]:
    """Get backup data source adapters. Skips sources whose deps aren't installed."""
    sources: list[DataSource] = []
    for name in names:
        if name == "tushare":
            try:
                import tushare  # noqa: F401  # type: ignore[import-untyped]

                from quant_trade.data.sources.tushare_adapter import TushareAdapter

                sources.append(TushareAdapter())
            except ImportError:
                logger.info("tushare not installed; skipping backup source")
        if name == "baostock":
            try:
                import baostock  # noqa: F401  # type: ignore[import-untyped]

                from quant_trade.data.sources.baostock_adapter import BaostockAdapter

                sources.append(BaostockAdapter())
            except ImportError:
                logger.info("baostock not installed; skipping backup source")
    return sources


def sync_daily_kline(
    store: DataStore,
    adapter: DataSource,
    codes: list[str],
    start: date,
    end: date,
    ctx: RunContext = NULL_CONTEXT,
) -> int:
    """Sync daily kline data. Returns number of rows written.

    Callers driving a long sync should pass a batch of codes and loop, so that
    ``ctx`` gets a cancellation checkpoint between batches.
    """
    if ctx.cancelled():
        ctx.log(f"Sync cancelled before fetching {len(codes)} codes", level="warning")
        return 0

    ctx.log(f"Fetching daily kline for {len(codes)} codes ({start} → {end}) via {adapter.source_name}")
    df = adapter.fetch_daily_kline(codes, start, end)
    if df.empty:
        logger.warning("No daily kline data fetched")
        return 0

    conn = store.conn
    rows = 0
    for _, row in df.iterrows():
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_kline
            (ts_code, trade_date, open, high, low, close, volume, amount, pct_change, turn_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["ts_code"],
                row["trade_date"],
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["volume"],
                row["amount"],
                row["pct_change"],
                row["turn_rate"],
            ],
        )
        rows += 1
    logger.info(f"Synced {rows} daily kline rows")
    return rows


def sync_index_daily(
    store: DataStore,
    adapter: DataSource,
    index_codes: list[str],
) -> int:
    """Sync daily kline for index codes (e.g., 000300.SH benchmark). Returns rows written."""
    if not index_codes:
        return 0
    fetch = getattr(adapter, "fetch_index_daily", None)
    if fetch is None:
        logger.warning(f"{adapter.source_name} does not support index daily kline")
        return 0
    df = fetch(index_codes)
    if df.empty:
        logger.warning("No index daily kline data fetched")
        return 0

    conn = store.conn
    rows = 0
    for _, row in df.iterrows():
        conn.execute(
            """
            INSERT OR REPLACE INTO daily_kline
            (ts_code, trade_date, open, high, low, close, volume, amount, pct_change, turn_rate)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["ts_code"],
                row["trade_date"],
                row["open"],
                row["high"],
                row["low"],
                row["close"],
                row["volume"],
                row["amount"],
                row["pct_change"],
                row["turn_rate"],
            ],
        )
        rows += 1
    logger.info(f"Synced {rows} index daily kline rows for {len(index_codes)} indices")
    return rows


def sync_stock_basic(
    store: DataStore,
    adapter: DataSource,
) -> int:
    """Sync stock basic info. Returns number of stocks written."""
    df = adapter.fetch_stock_basic()
    if df.empty:
        logger.warning("No stock basic data fetched")
        return 0

    conn = store.conn
    rows = 0
    for _, row in df.iterrows():
        conn.execute(
            """
            INSERT OR REPLACE INTO stock_basic
            (ts_code, name, industry, market, list_date, is_st)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                row["ts_code"],
                row["name"],
                row.get("industry", ""),
                row.get("market", ""),
                row.get("list_date"),
                row.get("is_st", False),
            ],
        )
        rows += 1
    logger.info(f"Synced {rows} stock basic records")
    return rows


def sync_financials(
    store: DataStore,
    adapter: DataSource,
    codes: list[str],
) -> int:
    """Sync financial data. Falls back to tushare if primary source returns empty."""
    df = adapter.fetch_financials(codes)
    if df.empty and adapter.source_name != "tushare":
        try:
            import tushare  # noqa: F401  # type: ignore[import-untyped]

            logger.info("Primary source returned no financials; trying tushare fallback")
            tushare_adapter = TushareAdapter()
            df = tushare_adapter.fetch_financials(codes)
        except ImportError:
            logger.warning("tushare not installed; skipping financials fallback")

    if df.empty:
        logger.warning("No financial data fetched")
        return 0

    conn = store.conn
    rows = 0
    for _, row in df.iterrows():
        conn.execute(
            """
            INSERT OR REPLACE INTO financials
            (ts_code, end_date, ann_date, pe, pb, roe, revenue_yoy, profit_yoy, dividend_yield)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["ts_code"],
                row["end_date"],
                row["ann_date"],
                row.get("pe"),
                row.get("pb"),
                row.get("roe"),
                row.get("revenue_yoy"),
                row.get("profit_yoy"),
                row.get("dividend_yield", 0.0),
            ],
        )
        rows += 1
    logger.info(f"Synced {rows} financial records")
    return rows


def sync_trade_calendar(
    store: DataStore,
    adapter: DataSource,
    start: date,
    end: date,
) -> int:
    """Sync trade calendar. Returns number of dates written."""
    df = adapter.fetch_trade_calendar(start, end)
    if df.empty:
        logger.warning("No trade calendar data fetched")
        return 0

    conn = store.conn
    rows = 0
    for _, row in df.iterrows():
        conn.execute(
            "INSERT OR REPLACE INTO trade_calendar (trade_date, is_open) VALUES (?, ?)",
            [row["trade_date"], row["is_open"]],
        )
        rows += 1
    logger.info(f"Synced {rows} trade calendar dates")
    return rows


def sync_all(
    store: DataStore,
    codes: list[str],
    start: date | None = None,
    end: date | None = None,
    primary: str = "akshare",
    include_financials: bool = False,
    ctx: RunContext = NULL_CONTEXT,
) -> dict[str, int]:
    """
    Run a full data sync: stock basic, trade calendar, daily kline, and optionally financials.

    Returns a dict of table_name -> rows_written.
    """
    if end is None:
        end = date.today()
    if start is None:
        # Check if database has data already
        existing = store.get_latest_trade_date()
        # Incremental sync fetches the last 5 days; first sync starts from 2015
        start = existing - timedelta(days=5) if existing else date(2015, 1, 1)

    adapter = get_primary_source(store, primary)
    backups = get_backup_sources(store, ["tushare", "baostock"])

    results: dict[str, int] = {}

    # Stock basic — try primary, then fallback
    ctx.log("Syncing stock basic...")
    rows = sync_stock_basic(store, adapter)
    if rows == 0 and backups:
        rows = sync_stock_basic(store, backups[0])
    results["stock_basic"] = rows

    # Trade calendar
    ctx.log("Syncing trade calendar...")
    rows = sync_trade_calendar(store, adapter, start, end)
    if rows == 0 and backups:
        rows = sync_trade_calendar(store, backups[0], start, end)
    results["trade_calendar"] = rows

    if ctx.cancelled():
        ctx.log("Sync cancelled after calendar step", level="warning")
        results["daily_kline"] = 0
        return results

    # Daily kline — only sync if we have codes
    if codes:
        rows = sync_daily_kline(store, adapter, codes, start, end, ctx=ctx)
        if rows == 0 and backups and not ctx.cancelled():
            try:
                rows = sync_daily_kline(store, backups[0], codes, start, end, ctx=ctx)
            except Exception as e:
                ctx.log(f"Backup daily kline sync failed: {e}", level="warning")
        results["daily_kline"] = rows
    else:
        results["daily_kline"] = 0

    # Financials (optional — slower)
    if include_financials and not ctx.cancelled():
        ctx.log("Syncing financials...")
        rows = sync_financials(store, adapter, codes)
        results["financials"] = rows

    ctx.log(f"Sync complete: {results}")
    return results
