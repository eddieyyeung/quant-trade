"""Simulator module — interactive historical quantitative trading sandbox."""

from quant_trade.simulator.engine import Simulator
from quant_trade.simulator.types import ComparisonResult, Decision, Snapshot, StepResult

__all__ = [
    "Simulator",
    "Decision",
    "Snapshot",
    "StepResult",
    "ComparisonResult",
]
