"""Bridge a single Alpha158 factor into the legacy Factor interface."""

from datetime import date

import pandas as pd
from loguru import logger

from quant_trade.data.store import DataStore
from quant_trade.factors.alpha158.compute import compute_alpha158
from quant_trade.factors.alpha158.storage import get_factor_values
from quant_trade.factors.base import Factor, FactorCategory


class Alpha158Factor(Factor):
    """Single Alpha158 factor exposed via ``Factor.compute(date, universe)``.

    Reads from ``factor_values`` when available, otherwise computes on the
    fly for the requested date. Usable directly with ``compute_ic_series``.
    """

    category = FactorCategory.TECHNICAL

    def __init__(self, name: str, store: DataStore | None = None):
        if name not in _valid_names():
            raise ValueError(f"Unknown Alpha158 factor: {name}")
        self.name = name
        self.store = store or DataStore()

    def compute(self, date: date, universe: list[str]) -> pd.Series:
        vals = get_factor_values(self.store, [self.name], universe, date, date)
        if vals.empty:
            logger.debug(f"{self.name}: cache miss, computing on the fly for {date}")
            out = compute_alpha158(self.store, date, date, universe)
            vals = out[out["factor_name"] == self.name]
        if vals.empty:
            return pd.Series(dtype=float)
        return vals.set_index("ts_code")["value"].rename(self.name)


def get_alpha158_factor(name: str, store: DataStore | None = None) -> Alpha158Factor:
    """Factory for an Alpha158Factor instance."""
    return Alpha158Factor(name, store=store)


def _valid_names() -> set[str]:
    from quant_trade.factors.alpha158.templates import ALPHA158_NAMES

    return set(ALPHA158_NAMES)
