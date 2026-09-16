"""Quality factors."""

from datetime import date

import pandas as pd

from quant_trade.data.store import DataStore
from quant_trade.factors.base import Factor, FactorCategory
from quant_trade.factors.registry import register


@register("roe_ttm")
class ROETTM(Factor):
    """Return on Equity (TTM). Higher = more profitable."""

    name = "roe_ttm"
    category = FactorCategory.QUALITY

    def __init__(self, store: DataStore | None = None):
        self.store = store or DataStore()

    def compute(self, date: date, universe: list[str]) -> pd.Series:
        fin = self.store.get_financials(universe, date)
        if fin.empty:
            return pd.Series(dtype=float)

        results: dict[str, float] = {}
        for _, row in fin.iterrows():
            roe = row.get("roe")
            if roe is not None:
                results[row["ts_code"]] = float(roe)
        return pd.Series(results)


@register("revenue_yoy")
class RevenueYoY(Factor):
    """Revenue year-over-year growth rate."""

    name = "revenue_yoy"
    category = FactorCategory.QUALITY

    def __init__(self, store: DataStore | None = None):
        self.store = store or DataStore()

    def compute(self, date: date, universe: list[str]) -> pd.Series:
        fin = self.store.get_financials(universe, date)
        if fin.empty:
            return pd.Series(dtype=float)

        results: dict[str, float] = {}
        for _, row in fin.iterrows():
            rev = row.get("revenue_yoy")
            if rev is not None:
                results[row["ts_code"]] = float(rev)
        return pd.Series(results)
