"""baostock data source adapter — free, stable, no registration required."""

import contextlib
import time
from datetime import date
from typing import Any

import pandas as pd
from loguru import logger

from quant_trade.data.sources.base import DataSource

BATCH_SIZE = 30  # stocks per login session
BATCH_DELAY = 3.0  # seconds between batches
MAX_RETRIES = 3


class BaostockAdapter(DataSource):
    """Market data from baostock (http://baostock.com). Free, stable, no token needed."""

    source_name = "baostock"

    def fetch_daily_kline(self, ts_codes: list[str], start: date, end: date) -> pd.DataFrame:
        """Fetch daily kline in batches to avoid connection drops."""
        import baostock as bs

        all_frames: list[pd.DataFrame] = []
        total = len(ts_codes)

        for batch_start in range(0, total, BATCH_SIZE):
            batch = ts_codes[batch_start : batch_start + BATCH_SIZE]
            batch_frames = _fetch_batch(bs, batch, start, end)
            all_frames.extend(batch_frames)

            done = min(batch_start + BATCH_SIZE, total)
            logger.info(f"baostock progress: {done}/{total} stocks")
            if batch_start + BATCH_SIZE < total:
                time.sleep(BATCH_DELAY)

        if not all_frames:
            return pd.DataFrame()
        result = pd.concat(all_frames, ignore_index=True)
        logger.info(f"baostock fetched {len(result)} rows for {result['ts_code'].nunique()} stocks")
        return result

    def fetch_stock_basic(self) -> pd.DataFrame:
        """Fetch all A-share stock basic info from baostock."""
        import baostock as bs

        bs.login()
        try:
            rs = bs.query_stock_basic(code_name="")
            if rs.error_code != "0":
                logger.error(f"baostock stock_basic failed: {rs.error_msg}")
                return pd.DataFrame()
            rows = _read_all_rows(rs)
            if not rows:
                return pd.DataFrame()
            df = pd.DataFrame(rows, columns=rs.fields)
            df.rename(columns={"code": "ts_code", "code_name": "name", "ipoDate": "list_date"}, inplace=True)
            df["ts_code"] = df["ts_code"].apply(_normalize_baostock_code)
            df["market"] = df["ts_code"].apply(_infer_market_from_bs)
            df["industry"] = ""
            df["is_st"] = df["type"] == "2"
            df["list_date"] = pd.to_datetime(df["list_date"], errors="coerce").dt.date
            return df[["ts_code", "name", "industry", "market", "list_date", "is_st"]]
        finally:
            bs.logout()

    def fetch_financials(self, ts_codes: list[str]) -> pd.DataFrame:
        logger.info("baostock financials not supported; use tushare for financial data")
        return pd.DataFrame()

    def fetch_index_weights(self, index_code: str, trade_date: date) -> pd.DataFrame:
        return pd.DataFrame()

    def fetch_trade_calendar(self, start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame()


def _fetch_batch(bs: Any, codes: list[str], start: date, end: date) -> list[pd.DataFrame]:
    """Fetch one batch of stocks with a fresh baostock session."""
    for retry in range(MAX_RETRIES):
        try:
            bs.login()
            frames: list[pd.DataFrame] = []
            for code in codes:
                bs_code = _to_baostock_code(code)
                try:
                    rs = bs.query_history_k_data_plus(
                        bs_code,
                        "date,open,high,low,close,volume,amount,pctChg,turn",
                        start_date=start.strftime("%Y-%m-%d"),
                        end_date=end.strftime("%Y-%m-%d"),
                        frequency="d",
                        adjustflag="2",
                    )
                    if rs.error_code != "0":
                        continue
                    rows = _read_all_rows(rs)
                    if not rows:
                        continue
                    df = pd.DataFrame(rows, columns=rs.fields)
                    df.rename(
                        columns={
                            "date": "trade_date",
                            "open": "open",
                            "high": "high",
                            "low": "low",
                            "close": "close",
                            "volume": "volume",
                            "amount": "amount",
                            "pctChg": "pct_change",
                            "turn": "turn_rate",
                        },
                        inplace=True,
                    )
                    df["ts_code"] = code
                    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
                    for col in ["open", "high", "low", "close", "volume", "amount", "pct_change", "turn_rate"]:
                        df[col] = pd.to_numeric(df[col], errors="coerce")
                    frames.append(
                        df[
                            [
                                "ts_code",
                                "trade_date",
                                "open",
                                "high",
                                "low",
                                "close",
                                "volume",
                                "amount",
                                "pct_change",
                                "turn_rate",
                            ]
                        ]
                    )
                except Exception:
                    continue
            bs.logout()
            return frames
        except Exception as e:
            logger.warning(f"baostock batch failed (retry {retry + 1}/{MAX_RETRIES}): {e}")
            with contextlib.suppress(Exception):
                bs.logout()
            time.sleep(5.0)
    return []


def _read_all_rows(rs: Any) -> list[list[Any]]:
    """Read all rows from a baostock result set."""
    rows: list[list[Any]] = []
    while rs.next():
        rows.append(rs.get_row_data())
    return rows


def _to_baostock_code(ts_code: str) -> str:
    parts = ts_code.split(".")
    code = parts[0]
    suffix = parts[1].lower() if len(parts) == 2 else "sz"
    return f"{suffix}.{code}"


def _normalize_baostock_code(bs_code: str) -> str:
    parts = bs_code.split(".")
    return f"{parts[1]}.{parts[0].upper()}"


def _infer_market_from_bs(ts_code: str) -> str:
    code_num = int(ts_code.split(".")[0])
    suffix = ts_code.split(".")[-1]
    if suffix == "SH":
        return "star" if code_num >= 688000 else "main"
    if suffix == "SZ":
        return "chinext" if code_num >= 300000 else "main"
    return "unknown"
