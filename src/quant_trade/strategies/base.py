"""Strategy base class and signal types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from quant_trade.data.store import DataStore


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

    def __init__(self, store: DataStore | None = None) -> None:
        """Hold the data store the caller supplied.

        Declared here so "which database does this strategy read" is a property
        of the base class rather than something each subclass has to remember.
        A subclass overriding this MUST keep the ``store`` parameter — the
        registry passes it by keyword, and dropping it fails at instantiation,
        which is the intended outcome: better a loud error than a strategy
        quietly reading a database its caller did not choose.

        ``None`` is permitted for strategies that touch no data; everything else
        should be handed a store rather than open one.
        """
        self.store = store

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
