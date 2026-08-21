"""Factor registry — decorator-based factor registration."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from quant_trade.factors.base import Factor

if TYPE_CHECKING:
    from quant_trade.data.store import DataStore


class FactorRegistry:
    """Global registry of Factor classes, keyed by name."""

    def __init__(self) -> None:
        self._factors: dict[str, type[Factor]] = {}

    def register(self, name: str) -> Callable[[type[Factor]], type[Factor]]:
        """Decorator: register a Factor subclass under ``name``."""

        def decorator(cls: type[Factor]) -> type[Factor]:
            cls.name = name
            self._factors[name] = cls
            return cls

        return decorator

    def get(self, name: str, store: DataStore | None = None) -> Factor | None:
        """Get a factor instance by name. Returns None if not found."""
        cls = self._factors.get(name)
        if cls is None:
            return None
        return cls(store=store)

    def list_all(self) -> list[str]:
        """List all registered factor names."""
        return list(self._factors.keys())

    def get_by_category(self, category: str, store: DataStore | None = None) -> list[Factor]:
        """Get all factor instances in a category."""
        result: list[Factor] = []
        for cls in self._factors.values():
            if cls.category.value == category:
                result.append(cls(store=store))
        return result


# Global singleton
registry = FactorRegistry()
register = registry.register
