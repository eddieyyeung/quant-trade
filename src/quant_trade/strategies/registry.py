"""Strategy registry — decorator-based strategy registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from quant_trade.strategies.base import Strategy

if TYPE_CHECKING:
    from quant_trade.data.store import DataStore


class StrategyRegistry:
    """Global registry for Strategy classes, keyed by name."""

    def __init__(self) -> None:
        self._strategies: dict[str, type[Strategy]] = {}

    def register(self, name: str) -> Callable[[type[Strategy]], type[Strategy]]:
        """Decorator to register a strategy class under a given name."""

        def decorator(cls: type[Strategy]) -> type[Strategy]:
            cls.name = name
            self._strategies[name] = cls
            return cls

        return decorator

    def get(self, name: str, store: DataStore | None = None) -> Strategy | None:
        """Get a strategy instance by name, optionally with a data store.

        The store is passed as a keyword, and the same shape as
        :meth:`FactorRegistry.get`. Without it a strategy falls back to its own
        default connection — which is a different database from the caller's,
        and the reason this parameter exists.
        """
        cls = self._strategies.get(name)
        if cls is None:
            return None
        return cls(store=store)

    def list_all(self) -> list[str]:
        """List all registered strategy names."""
        return list(self._strategies.keys())


strategy_registry = StrategyRegistry()
register_strategy = strategy_registry.register
