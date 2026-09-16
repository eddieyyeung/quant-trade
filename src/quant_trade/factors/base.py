"""Factor abstract base class and type definitions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING, Any

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
        """Initialize factor with optional shared DataStore."""
        self.store: Any = store

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
