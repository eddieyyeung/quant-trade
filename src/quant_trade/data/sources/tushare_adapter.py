"""tushare data source adapter (backup source)."""

from datetime import date
from typing import Any

import pandas as pd
from loguru import logger

from quant_trade.data.sources.base import DataSource


class TushareAdapter(DataSource):
    """Market data from tushare. Requires TUSHARE_TOKEN env var."""

    source_name = "tushare"

    def __init__(self) -> None:
        self._pro: Any | None = None

    def _get_pro(self) -> Any:
        """Lazy-init tushare pro client."""
        if self._pro is None:
            import tushare as ts

            self._pro = ts.pro_api()
        return self._pro

    def fetch_daily_kline(self, ts_codes: list[str], start: date, end: date) -> pd.DataFrame:
        """Fetch daily kline via tushare pro."""
        pro = self._get_pro()
        frames: list[pd.DataFrame] = []
        for code in ts_codes:
            try:
                df = pro.daily(
                    ts_code=code,
                    start_date=start.strftime("%Y%m%d"),
                    end_date=end.strftime("%Y%m%d"),
                )
                if df.empty:
                    continue
                df.rename(
                    columns={
                        "ts_code": "ts_code",
                        "trade_date": "trade_date",
                        "open": "open",
                        "high": "high",
                        "low": "low",
                        "close": "close",
                        "vol": "volume",
                        "amount": "amount",
                        "pct_chg": "pct_change",
                    },
                    inplace=True,
                )
                df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
                df["turn_rate"] = 0.0  # tushare daily doesn't include turn_rate by default
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
            except Exception as e:
                logger.warning(f"tushare fetch failed for {code}: {e}")
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def fetch_stock_basic(self) -> pd.DataFrame:
        """Fetch stock basic info from tushare."""
        pro = self._get_pro()
        try:
            df = pro.stock_basic(
                exchange="",
                list_status="L",
                fields="ts_code,name,industry,market,list_date",
            )
            df["is_st"] = df["name"].str.contains(r"\*?ST", regex=True)
            return df  # type: ignore[no-any-return]
        except Exception as e:
            logger.error(f"tushare fetch_stock_basic failed: {e}")
            return pd.DataFrame()

    def fetch_financials(self, ts_codes: list[str]) -> pd.DataFrame:
        """Fetch key financial indicators from tushare."""
        pro = self._get_pro()
        frames: list[pd.DataFrame] = []
        for code in ts_codes:
            try:
                df = pro.fina_indicator(
                    ts_code=code,
                    period="",
                )
                if df.empty:
                    continue
                df.rename(
                    columns={
                        "end_date": "end_date",
                        "ann_date": "ann_date",
                        "pe": "pe",
                        "pb": "pb",
                        "roe": "roe",
                        "or_yoy": "revenue_yoy",
                        "profit_dedt": "profit_yoy",
                    },
                    inplace=True,
                )
                df["end_date"] = pd.to_datetime(df["end_date"]).dt.date
                df["ann_date"] = pd.to_datetime(df["ann_date"]).dt.date
                df["dividend_yield"] = 0.0
                frames.append(df)
            except Exception as e:
                logger.warning(f"tushare financials failed for {code}: {e}")
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    def fetch_index_weights(self, index_code: str, trade_date: date) -> pd.DataFrame:
        """Fetch index weights from tushare."""
        pro = self._get_pro()
        try:
            df = pro.index_weight(
                index_code=index_code,
                trade_date=trade_date.strftime("%Y%m%d"),
            )
            if df.empty:
                return pd.DataFrame()
            df["in_date"] = trade_date
            df["out_date"] = None
            return df[["index_code", "ts_code", "weight", "in_date", "out_date"]]  # type: ignore[no-any-return]
        except Exception as e:
            logger.warning(f"tushare index_weights failed for {index_code}: {e}")
            return pd.DataFrame()

    def fetch_trade_calendar(self, start: date, end: date) -> pd.DataFrame:
        """Fetch trade calendar from tushare."""
        pro = self._get_pro()
        try:
            df = pro.trade_cal(
                exchange="SSE",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
            if df.empty:
                return pd.DataFrame()
            df["trade_date"] = pd.to_datetime(df["cal_date"]).dt.date
            df["is_open"] = df["is_open"] == 1
            return df[["trade_date", "is_open"]]  # type: ignore[no-any-return]
        except Exception as e:
            logger.error(f"tushare trade_calendar failed: {e}")
            return pd.DataFrame()
