"""Data-domain services — market data sync and database status."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from pydantic import Field

from quant_trade.config import AppConfig
from quant_trade.data.index_weights import get_default_universe, sync_index_weights
from quant_trade.data.sources.akshare_adapter import AkshareAdapter
from quant_trade.data.store import DataStore, TableStats
from quant_trade.data.sync import (
    get_backup_sources,
    sync_all,
    sync_daily_kline,
    sync_financials,
    sync_index_daily,
)
from quant_trade.services.context import NULL_CONTEXT, RunContext
from quant_trade.services.params import ServiceParams

BATCH_SIZE = 200
"""Codes per kline fetch. Small enough that progress moves and cancel lands promptly."""

DEFAULT_HISTORY_START = date(2015, 1, 1)
"""A first sync pulls full history from here; later syncs are incremental."""

INCREMENTAL_DAYS = 5
"""Trailing window re-fetched on an incremental sync."""


class DataSyncParams(ServiceParams):
    """What to sync and over which window."""

    include_financials: bool = False
    start_date: date | None = None
    """First date to fetch. ``None`` means full history on a fresh database,
    a short trailing window otherwise."""
    end_date: date | None = None
    """Last date to fetch. ``None`` means today."""
    primary_source: str = "akshare"
    backup_sources: list[str] = Field(default_factory=lambda: ["baostock", "tushare"])

    @classmethod
    def config_defaults(cls, config: AppConfig) -> dict[str, object]:
        return {
            "primary_source": config.data.primary_source,
            "backup_sources": list(config.data.backup_sources),
        }


class DataStatusParams(ServiceParams):
    """Parameters for a database status read."""

    universe_as_of: date | None = None
    """Date used to evaluate the universe size. ``None`` means today."""


@dataclass
class DataSyncResult:
    """Outcome of a market data sync."""

    tables: dict[str, int]
    universe_size: int
    start: date
    end: date
    missing: list[str] = field(default_factory=list)
    """Codes that no source could supply, so callers can surface them."""
    cancelled: bool = False


@dataclass
class DataStatus:
    """Snapshot of what the local database currently holds."""

    db_path: str
    latest_trade_date: date | None
    tables: dict[str, TableStats]
    universe_size: int


def sync_market_data(params: DataSyncParams, ctx: RunContext = NULL_CONTEXT) -> DataSyncResult:
    """Sync stock basic, trade calendar, index weights, index kline and daily kline.

    Codes are fetched in batches so that progress advances and
    :meth:`RunContext.cancelled` is honoured between batches rather than only at
    the end of an hours-long run.
    """
    store = ctx.db
    end = params.end_date or date.today()
    start = params.start_date or _resolve_start(store)

    adapter = AkshareAdapter()
    ctx.progress(0.0, "Syncing stock basic & trade calendar")
    tables = sync_all(
        store,
        [],
        start=start,
        end=end,
        primary=params.primary_source,
        include_financials=False,
        ctx=ctx,
    )

    if ctx.cancelled():
        return DataSyncResult(tables=tables, universe_size=0, start=start, end=end, cancelled=True)

    ctx.progress(0.10, "Syncing index weights")
    sync_index_weights(store, get_default_universe(), date.today(), adapter)

    ctx.progress(0.15, "Syncing index daily kline")
    sync_index_daily(store, adapter, get_default_universe())

    ctx.progress(0.20, "Resolving universe")
    codes = store.get_universe(get_default_universe(), date.today())
    if not codes:
        ctx.log("No universe codes found; sampling the listed universe instead", level="warning")
        basic = adapter.fetch_stock_basic()
        codes = basic["ts_code"].tolist()[:200] if not basic.empty else []

    total = len(codes)
    rows = 0
    for offset in range(0, total, BATCH_SIZE):
        if ctx.cancelled():
            ctx.log(f"Cancelled after {offset}/{total} codes", level="warning")
            tables["daily_kline"] = rows
            return DataSyncResult(
                tables=tables,
                universe_size=total,
                start=start,
                end=end,
                missing=codes[offset:],
                cancelled=True,
            )
        batch = codes[offset : offset + BATCH_SIZE]
        rows += sync_daily_kline(store, adapter, batch, start, end, ctx=ctx)
        ctx.progress(
            0.20 + 0.70 * min(1.0, (offset + len(batch)) / total), f"Synced {offset + len(batch)}/{total} codes"
        )
    tables["daily_kline"] = rows

    missing = _missing_codes(store, codes)
    if missing and not ctx.cancelled():
        rows += _fill_gaps(store, ctx, params.backup_sources, missing, start, end)
        tables["daily_kline"] = rows
        missing = _missing_codes(store, codes)

    if params.include_financials and not ctx.cancelled():
        ctx.progress(0.95, "Syncing financials")
        tables["financials"] = sync_financials(store, adapter, codes)

    ctx.progress(1.0, "Sync complete")
    return DataSyncResult(
        tables=tables,
        universe_size=total,
        start=start,
        end=end,
        missing=missing,
        cancelled=ctx.cancelled(),
    )


def data_status(params: DataStatusParams, ctx: RunContext = NULL_CONTEXT) -> DataStatus:
    """Report row counts, date spans and universe size for the local database."""
    store = ctx.db
    as_of = params.universe_as_of or date.today()
    universe = store.get_universe(get_default_universe(), as_of)
    return DataStatus(
        db_path=store.db_path,
        latest_trade_date=store.get_latest_trade_date(),
        tables=store.all_table_stats(),
        universe_size=len(universe),
    )


def _resolve_start(store: DataStore) -> date:
    """Full history on a fresh database, a short trailing window afterwards."""
    latest = store.get_latest_trade_date()
    return latest - timedelta(days=INCREMENTAL_DAYS) if latest else DEFAULT_HISTORY_START


def _missing_codes(store: DataStore, codes: list[str]) -> list[str]:
    synced = store.synced_codes()
    return [c for c in codes if c not in synced]


def _fill_gaps(
    store: DataStore,
    ctx: RunContext,
    backup_names: list[str],
    missing: list[str],
    start: date,
    end: date,
) -> int:
    """Try each backup source for codes the primary source did not supply."""
    rows = 0
    for backup in get_backup_sources(store, backup_names):
        if ctx.cancelled():
            break
        ctx.log(f"Filling {len(missing)} missing codes via {backup.source_name}", level="warning")
        try:
            rows += sync_daily_kline(store, backup, missing, start, end, ctx=ctx)
        except Exception as e:
            ctx.log(f"Backup {backup.source_name} failed: {e}", level="warning")
            continue
        remaining = _missing_codes(store, missing)
        if not remaining:
            break
        missing = remaining
    return rows
