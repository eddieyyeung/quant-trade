"""akshare data source adapter."""

import time
from datetime import date

import pandas as pd
from loguru import logger

from quant_trade.data.sources.base import DataSource

RETRIES = 3
RETRY_DELAY = 1.0  # seconds between retries
REQUEST_DELAY = 0.3  # seconds between stocks to avoid rate-limiting


class AkshareAdapter(DataSource):
    """Market data from akshare (East Money upstream)."""

    source_name = "akshare"

    def fetch_daily_kline(self, ts_codes: list[str], start: date, end: date) -> pd.DataFrame:
        """Fetch daily kline data using akshare."""
        import akshare as ak

        frames: list[pd.DataFrame] = []
        failed = 0
        for i, code in enumerate(ts_codes):
            if i > 0:
                time.sleep(REQUEST_DELAY)

            ok = False
            for attempt in range(RETRIES):
                try:
                    raw = ak.stock_zh_a_hist(
                        symbol=code.split(".")[0],
                        period="daily",
                        start_date=start.strftime("%Y%m%d"),
                        end_date=end.strftime("%Y%m%d"),
                        adjust="hfq",
                    )
                    if raw.empty:
                        ok = True
                        break
                    raw.rename(
                        columns={
                            "日期": "trade_date",
                            "开盘": "open",
                            "最高": "high",
                            "最低": "low",
                            "收盘": "close",
                            "成交量": "volume",
                            "成交额": "amount",
                            "涨跌幅": "pct_change",
                            "换手率": "turn_rate",
                        },
                        inplace=True,
                    )
                    raw["ts_code"] = code
                    raw["trade_date"] = pd.to_datetime(raw["trade_date"]).dt.date
                    frames.append(
                        raw[
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
                    ok = True
                    break
                except Exception:
                    if attempt < RETRIES - 1:
                        time.sleep(RETRY_DELAY)

            if not ok:
                failed += 1

        if failed > 0:
            logger.warning(f"akshare daily kline: {failed}/{len(ts_codes)} stocks failed (rate-limited by upstream)")

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def fetch_index_daily(self, index_codes: list[str]) -> pd.DataFrame:
        """Fetch full daily kline history for index codes (e.g., '000300.SH').

        Uses akshare's stock_zh_index_daily, which returns the complete
        history in one call. Indices have no amount/pct_change/turn_rate —
        those columns are filled with 0.0.
        """
        import akshare as ak

        frames: list[pd.DataFrame] = []
        for i, code in enumerate(index_codes):
            if i > 0:
                time.sleep(REQUEST_DELAY)
            num, exchange = code.split(".")
            symbol = f"{exchange.lower()}{num}"
            for attempt in range(RETRIES):
                try:
                    raw = ak.stock_zh_index_daily(symbol=symbol)
                    if raw.empty:
                        break
                    raw.rename(
                        columns={
                            "date": "trade_date",
                            "open": "open",
                            "high": "high",
                            "low": "low",
                            "close": "close",
                            "volume": "volume",
                        },
                        inplace=True,
                    )
                    raw["ts_code"] = code
                    raw["trade_date"] = pd.to_datetime(raw["trade_date"]).dt.date
                    raw["amount"] = 0.0
                    raw["pct_change"] = 0.0
                    raw["turn_rate"] = 0.0
                    frames.append(
                        raw[
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
                    break
                except Exception:
                    if attempt < RETRIES - 1:
                        time.sleep(RETRY_DELAY)

        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def fetch_stock_basic(self) -> pd.DataFrame:
        """Fetch all A-share stock basic info."""
        import akshare as ak

        try:
            raw = ak.stock_info_a_code_name()
            raw.rename(
                columns={"code": "ts_code", "name": "name"},
                inplace=True,
            )
            # Standardize ts_code format: 000001.SZ / 600519.SH
            raw["ts_code"] = raw["ts_code"].apply(_normalize_code)
            raw["market"] = raw["ts_code"].apply(_infer_market)
            raw["industry"] = ""
            raw["list_date"] = None
            raw["is_st"] = raw["name"].str.contains(r"\*?ST", regex=True)
            return raw[["ts_code", "name", "industry", "market", "list_date", "is_st"]]  # type: ignore[no-any-return]
        except Exception as e:
            logger.error(f"akshare fetch_stock_basic failed: {e}")
            return pd.DataFrame()

    def fetch_financials(self, ts_codes: list[str]) -> pd.DataFrame:
        """
        Fetch financial data. akshare doesn't offer a clean bulk financial endpoint,
        so this is a best-effort stub. Primary financial data should come from tushare.
        """
        logger.info("akshare financials not fully supported; use tushare for financial data")
        return pd.DataFrame()

    def fetch_index_weights(self, index_code: str, trade_date: date) -> pd.DataFrame:
        """Fetch index constituent stocks."""
        import akshare as ak

        try:
            raw = ak.index_stock_cons_weight_csindex(symbol=index_code.split(".")[0])
            if raw.empty:
                return pd.DataFrame()
            raw["index_code"] = index_code
            raw["ts_code"] = raw["成分券代码"].apply(_normalize_code)
            raw["weight"] = raw["权重"].astype(float)
            raw["in_date"] = trade_date
            raw["out_date"] = None
            return raw[["index_code", "ts_code", "weight", "in_date", "out_date"]]  # type: ignore[no-any-return]
        except Exception as e:
            logger.warning(f"akshare fetch_index_weights failed for {index_code}: {e}")
            return pd.DataFrame()

    def fetch_trade_calendar(self, start: date, end: date) -> pd.DataFrame:
        """Fetch A-share trade calendar."""
        import akshare as ak

        try:
            raw = ak.tool_trade_date_hist_sina()
            raw["trade_date"] = pd.to_datetime(raw["trade_date"]).dt.date
            raw = raw[(raw["trade_date"] >= start) & (raw["trade_date"] <= end)]
            raw["is_open"] = True
            return raw[["trade_date", "is_open"]]  # type: ignore[no-any-return]
        except Exception as e:
            logger.error(f"akshare fetch_trade_calendar failed: {e}")
            return pd.DataFrame()


def _normalize_code(raw_code: str) -> str:
    """Normalize raw stock code to '000001.SZ' / '600519.SH' format."""
    code = str(raw_code).strip()
    # Already normalized
    if "." in code:
        return code
    # 6-digit code — infer exchange
    if code.startswith(("0", "3")):
        return f"{code}.SZ"
    if code.startswith(("6", "9")):
        return f"{code}.SH"
    if code.startswith(("4", "8")):
        return f"{code}.BJ"
    return code


def _infer_market(ts_code: str) -> str:
    """Infer market category from ts_code."""
    suffix = ts_code.split(".")[-1] if "." in ts_code else ts_code[:2]
    if suffix in ("SH", "sh"):
        code_num = int(ts_code.split(".")[0])
        if code_num >= 688000:
            return "star"  # 科创板
        return "main"
    if suffix in ("SZ", "sz"):
        code_num = int(ts_code.split(".")[0])
        if code_num >= 300000:
            return "chinext"  # 创业板
        return "main"
    if suffix in ("BJ", "bj"):
        return "beijing"
    return "unknown"
