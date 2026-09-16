"""Index constituent stock management."""

from datetime import date
from typing import Any

from loguru import logger

from quant_trade.data.store import DataStore, _min_list_date

# Commonly used A-share indices
CSI300 = "000300.SH"
CSI500 = "000905.SH"
CSI1000 = "000852.SH"


def sync_index_weights(
    store: DataStore,
    index_codes: list[str],
    trade_date: date,
    adapter: Any,
) -> None:
    """Fetch and store index constituent weights from a data adapter."""
    conn = store.conn
    for code in index_codes:
        try:
            df = adapter.fetch_index_weights(code, trade_date)
            if df.empty:
                logger.warning(f"No weight data for {code} on {trade_date}")
                continue

            # Upsert: delete existing records for this index, then insert
            conn.execute(
                "DELETE FROM index_weights WHERE index_code = ? AND in_date = ?",
                [code, trade_date],
            )

            # Insert new records
            for _, row in df.iterrows():
                conn.execute(
                    """
                    INSERT OR REPLACE INTO index_weights
                    (index_code, ts_code, weight, in_date, out_date)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [row["index_code"], row["ts_code"], row["weight"], row["in_date"], row["out_date"]],
                )
            logger.info(f"Synced {len(df)} constituents for {code}")
        except Exception as e:
            logger.warning(f"Failed to sync index {code}: {e}")


def filter_universe(
    store: DataStore,
    codes: list[str],
    as_of: date,
    filter_st: bool = True,
    min_list_days: int = 250,
) -> list[str]:
    """Filter a stock list: remove ST, newly listed, etc."""
    if not codes:
        return []

    placeholders = ", ".join(["?"] * len(codes))
    conditions = [f"sb.ts_code IN ({placeholders})"]
    params: list[object] = list(codes)

    if filter_st:
        conditions.append("sb.is_st = FALSE")
    if min_list_days > 0:
        min_list_date = _min_list_date(store, as_of, min_list_days)
        conditions.append("(sb.list_date IS NULL OR sb.list_date <= ?)")
        params.append(min_list_date)

    sql = f"""
        SELECT sb.ts_code FROM stock_basic sb
        WHERE {" AND ".join(conditions)}
    """
    try:
        df = store.conn.execute(sql, params).df()
        return df["ts_code"].tolist()
    except Exception as e:
        logger.warning(f"filter_universe failed: {e}")
        return codes  # fallback: return unfiltered


def get_default_universe() -> list[str]:
    """Default A-share indices for the platform: CSI300 + CSI500."""
    return [CSI300, CSI500]
