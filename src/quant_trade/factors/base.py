"""Factor abstract base class and type definitions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from quant_trade.data.store import DataStore


class FactorCategory(StrEnum):
    MOMENTUM = "momentum"
    VALUE = "value"
    QUALITY = "quality"
    TECHNICAL = "technical"


class Factor(ABC):
    """Base class for all factors.

    Subclasses must implement ``compute(date, universe)`` and set ``name`` and ``category``.
    """

    name: str = ""
    category: FactorCategory = FactorCategory.MOMENTUM

    def __init__(self, store: DataStore | None = None) -> None:
        """Hold the data-access object the caller supplied.

        A factor that receives none resolves the configured database, and says
        so in the log — the same fallback every other component uses. Without
        this a subclass that simply implements ``compute`` (the pattern the
        `factor-system` spec documents) got ``store = None`` and failed at the
        first query with an ``AttributeError`` rather than a usable object.

        The connection is lazy, so constructing a factor nobody queries opens
        no file. Subclasses that define their own ``__init__`` must set
        ``self.store`` themselves; the concrete factors all do, with the same
        ``store or DataStore()`` shape.
        """
        if store is None:
            # Imported here, not at module scope: `data.store` reaches back
            # into the factor package only lazily, and a module-level import
            # would make the cycle load-order dependent.
            from quant_trade.data.store import DataStore as _DataStore

            store = _DataStore()
        self.store = store

    @abstractmethod
    def compute(self, date: date, universe: list[str]) -> pd.Series:
        """
        Compute factor values for all stocks in universe on a given date.

        Args:
            date: The signal date (last trading day, typically Friday).
            universe: List of ts_code strings.

        Returns:
            pd.Series with ts_code as index and factor value as values.
            Missing stocks are omitted (NaN dropped).
        """
        ...

    def __repr__(self) -> str:
        return f"Factor({self.name}, {self.category.value})"
