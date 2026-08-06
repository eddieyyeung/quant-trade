"""Factor registry — decorator-based factor registration."""

from collections.abc import Callable

from quant_trade.factors.base import Factor


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

    def get(self, name: str) -> Factor | None:
        """Get a factor instance by name. Returns None if not found."""
        cls = self._factors.get(name)
        if cls is None:
            return None
        return cls()

    def list_all(self) -> list[str]:
        """List all registered factor names."""
        return list(self._factors.keys())

    def get_by_category(self, category: str) -> list[Factor]:
        """Get all factor instances in a category."""
        result: list[Factor] = []
        for cls in self._factors.values():
            if cls.category.value == category:
                result.append(cls())
        return result


# Global singleton
registry = FactorRegistry()
register = registry.register
