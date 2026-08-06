"""Strategy registry — decorator-based strategy registration."""

from collections.abc import Callable

from quant_trade.strategies.base import Strategy


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

    def get(self, name: str) -> Strategy | None:
        """Get a strategy instance by name."""
        cls = self._strategies.get(name)
        if cls is None:
            return None
        return cls()

    def list_all(self) -> list[str]:
        """List all registered strategy names."""
        return list(self._strategies.keys())


strategy_registry = StrategyRegistry()
register_strategy = strategy_registry.register
