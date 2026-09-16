"""Strategy module public API."""

# Import strategy implementations to trigger @register_strategy
from quant_trade.strategies import factor_ranking as _fr  # noqa: F401
from quant_trade.strategies import model_strategy as _ms  # noqa: F401
from quant_trade.strategies.base import Order, SignalResult, Strategy
from quant_trade.strategies.registry import StrategyRegistry, register_strategy, strategy_registry

__all__ = [
    "Strategy",
    "Order",
    "SignalResult",
    "StrategyRegistry",
    "register_strategy",
    "strategy_registry",
]
