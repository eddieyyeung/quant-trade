"""Strategy base class and signal types."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class Order:
    """A single trade order."""

    ts_code: str
    target_pct: float  # Target portfolio weight (0.0 to 1.0)
    direction: str = "BUY"  # BUY or SELL
    reason: str = ""


@dataclass
class SignalResult:
    """Strategy output — list of orders and target weights."""

    orders: list[Order] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class Strategy(ABC):
    """Abstract base for all trading strategies."""

    name: str = "base"

    @abstractmethod
    def generate_signals(
        self,
        date: date,
        universe: list[str],
        data: Any,
    ) -> SignalResult:
        """
        Generate trading signals for the given date and universe.

        Args:
            date: Signal date (typically Friday).
            universe: List of stock codes in the investable universe.
            data: DataStore instance for data access.

        Returns:
            SignalResult with orders and target weights.
        """
        ...
