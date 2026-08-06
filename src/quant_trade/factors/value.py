"""Value factors."""

from datetime import date

import pandas as pd

from quant_trade.data.store import DataStore
from quant_trade.factors.base import Factor, FactorCategory
from quant_trade.factors.registry import register


@register("pb_ratio")
class PBRatio(Factor):
    """Inverse of Price-to-Book ratio (1/PB). Higher = cheaper relative to book."""

    name = "pb_ratio"
    category = FactorCategory.VALUE

    def __init__(self, store: DataStore | None = None):
        self.store = store or DataStore()

    def compute(self, date: date, universe: list[str]) -> pd.Series:
        fin = self.store.get_financials(universe, date)
        if fin.empty:
            return pd.Series(dtype=float)

        results: dict[str, float] = {}
        for _, row in fin.iterrows():
            pb = row.get("pb")
            if pb and pb > 0:
                results[row["ts_code"]] = float(1.0 / pb)
        return pd.Series(results)


@register("pe_ratio")
class PERatio(Factor):
    """Inverse of PE ratio (1/PE), excluding negative PE. Higher = cheaper."""

    name = "pe_ratio"
    category = FactorCategory.VALUE

    def __init__(self, store: DataStore | None = None):
        self.store = store or DataStore()

    def compute(self, date: date, universe: list[str]) -> pd.Series:
        fin = self.store.get_financials(universe, date)
        if fin.empty:
            return pd.Series(dtype=float)

        results: dict[str, float] = {}
        for _, row in fin.iterrows():
            pe = row.get("pe")
            if pe and pe > 0:
                results[row["ts_code"]] = float(1.0 / pe)
        return pd.Series(results)


@register("dividend_yield")
class DividendYield(Factor):
    """Dividend yield (trailing 12-month dividends / market cap)."""

    name = "dividend_yield"
    category = FactorCategory.VALUE

    def __init__(self, store: DataStore | None = None):
        self.store = store or DataStore()

    def compute(self, date: date, universe: list[str]) -> pd.Series:
        fin = self.store.get_financials(universe, date)
        if fin.empty:
            return pd.Series(dtype=float)

        results: dict[str, float] = {}
        for _, row in fin.iterrows():
            dy = row.get("dividend_yield")
            if dy and dy >= 0:
                results[row["ts_code"]] = float(dy)
        return pd.Series(results)
