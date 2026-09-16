"""Momentum factors."""

from datetime import date

import numpy as np
import pandas as pd

from quant_trade.data.store import DataStore
from quant_trade.factors.base import Factor, FactorCategory
from quant_trade.factors.registry import register


@register("momentum_20d")
class Momentum20d(Factor):
    """20-day log return momentum."""

    name = "momentum_20d"
    category = FactorCategory.MOMENTUM

    def __init__(self, store: DataStore | None = None):
        self.store = store or DataStore()

    def compute(self, date: date, universe: list[str]) -> pd.Series:
        # Need ~25 trading days of history to compute 20-day return
        start = date - pd.Timedelta(days=60)
        df = self.store.get_daily(universe, start, date, fields=["ts_code", "trade_date", "close"])
        if df.empty:
            return pd.Series(dtype=float)

        # For each stock, compute 20-day log return
        results: dict[str, float] = {}
        for code in universe:
            code_df = df[df["ts_code"] == code].sort_values("trade_date")
            if len(code_df) < 21:
                continue
            closes = code_df["close"].values
            if closes[-21] > 0:
                results[code] = float(np.log(closes[-1] / closes[-21]))
        return pd.Series(results)


@register("momentum_60d")
class Momentum60d(Factor):
    """60-day log return momentum."""

    name = "momentum_60d"
    category = FactorCategory.MOMENTUM

    def __init__(self, store: DataStore | None = None):
        self.store = store or DataStore()

    def compute(self, date: date, universe: list[str]) -> pd.Series:
        start = date - pd.Timedelta(days=120)
        df = self.store.get_daily(universe, start, date, fields=["ts_code", "trade_date", "close"])
        if df.empty:
            return pd.Series(dtype=float)

        results: dict[str, float] = {}
        for code in universe:
            code_df = df[df["ts_code"] == code].sort_values("trade_date")
            if len(code_df) < 61:
                continue
            closes = code_df["close"].values
            if closes[-61] > 0:
                results[code] = float(np.log(closes[-1] / closes[-61]))
        return pd.Series(results)


@register("ma_deviation")
class MADeviation(Factor):
    """Close price deviation from 60-day moving average (percent)."""

    name = "ma_deviation"
    category = FactorCategory.MOMENTUM

    def __init__(self, store: DataStore | None = None):
        self.store = store or DataStore()

    def compute(self, date: date, universe: list[str]) -> pd.Series:
        start = date - pd.Timedelta(days=120)
        df = self.store.get_daily(universe, start, date, fields=["ts_code", "trade_date", "close"])
        if df.empty:
            return pd.Series(dtype=float)

        results: dict[str, float] = {}
        for code in universe:
            code_df = df[df["ts_code"] == code].sort_values("trade_date")
            if len(code_df) < 60:
                continue
            closes = np.asarray(code_df["close"].values, dtype=float)
            ma60 = np.mean(closes[-60:])
            if ma60 > 0:
                results[code] = float((closes[-1] - ma60) / ma60)
        return pd.Series(results)
