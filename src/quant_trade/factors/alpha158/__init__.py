"""Alpha158 factor library — vectorized port of qlib's Alpha158 feature set."""

from quant_trade.factors.alpha158.compute import compute_alpha158
from quant_trade.factors.alpha158.storage import get_factor_values, save_factor_values
from quant_trade.factors.alpha158.templates import ALPHA158_NAMES

__all__ = [
    "ALPHA158_NAMES",
    "compute_alpha158",
    "get_factor_values",
    "save_factor_values",
]
