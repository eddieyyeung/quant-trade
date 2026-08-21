"""Abstract base class for data source adapters."""

from abc import ABC, abstractmethod
from datetime import date

import pandas as pd


class DataSource(ABC):
    """Interface for market data adapters. All sources return DataFrames with the same columns."""

    source_name: str = "base"

    @abstractmethod
    def fetch_daily_kline(self, ts_codes: list[str], start: date, end: date) -> pd.DataFrame:
        """
        Fetch daily OHLCV data.
        Returns DataFrame with columns: ts_code, trade_date, open, high, low, close,
        volume, amount, pct_change, turn_rate
        """
        ...

    @abstractmethod
    def fetch_stock_basic(self) -> pd.DataFrame:
        """
        Fetch basic stock info for all A-share stocks.
        Returns DataFrame with columns: ts_code, name, industry, market, list_date
        """
        ...

    @abstractmethod
    def fetch_financials(self, ts_codes: list[str]) -> pd.DataFrame:
        """
        Fetch latest financial data.
        Returns DataFrame with columns: ts_code, end_date, ann_date, pe, pb, roe,
        revenue_yoy, profit_yoy, dividend_yield
        """
        ...

    @abstractmethod
    def fetch_index_weights(self, index_code: str, trade_date: date) -> pd.DataFrame:
        """
        Fetch constituent stocks of an index on a given date.
        Returns DataFrame with columns: index_code, ts_code, weight
        """
        ...

    @abstractmethod
    def fetch_trade_calendar(self, start: date, end: date) -> pd.DataFrame:
        """
        Fetch trade calendar.
        Returns DataFrame with columns: trade_date, is_open
        """
        ...
