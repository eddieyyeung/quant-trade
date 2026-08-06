"""Factor abstract base class and type definitions."""

from abc import ABC, abstractmethod
from datetime import date
from enum import StrEnum

import pandas as pd


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
