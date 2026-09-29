"""Build a configured strategy instance from application config."""

from __future__ import annotations

from typing import TYPE_CHECKING

from quant_trade.config import AppConfig
from quant_trade.strategies.base import Strategy
from quant_trade.strategies.registry import strategy_registry

if TYPE_CHECKING:
    from quant_trade.data.store import DataStore

CONFIG_ATTRIBUTES = ("top_n", "factor_weights", "max_industry_weight")
"""Strategy attributes filled from ``StrategyConfig`` when the strategy has them."""


def build_strategy(name: str | None, config: AppConfig, store: DataStore | None = None) -> Strategy | None:
    """Instantiate a registered strategy with config-driven parameters applied.

    Returns ``None`` when no strategy is registered under the resolved name.

    ``store`` is threaded through to the strategy. Callers that hold one should
    pass it: a strategy left to open its own connection reads a database the
    caller did not choose, which is invisible until the numbers are wrong.
    """
    strategy = strategy_registry.get(name or config.strategy.name, store=store)
    if strategy is None:
        return None

    for attr in CONFIG_ATTRIBUTES:
        if hasattr(strategy, attr):
            setattr(strategy, attr, getattr(config.strategy, attr))

    predictions_path = config.strategy.params.get("predictions_path")
    if predictions_path and hasattr(strategy, "predictions_path"):
        strategy.predictions_path = predictions_path

    return strategy
