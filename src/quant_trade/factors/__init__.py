"""Factor module public API."""

# Import factor implementations to trigger @register side effects
from quant_trade.factors import momentum as _momentum  # noqa: F401
from quant_trade.factors import quality as _quality  # noqa: F401
from quant_trade.factors import value as _value  # noqa: F401
from quant_trade.factors.analysis import compute_forward_returns, compute_ic, compute_ic_series
from quant_trade.factors.base import Factor, FactorCategory
from quant_trade.factors.preprocess import neutralize_industry, preprocess, standardize, winsorize_mad
from quant_trade.factors.registry import FactorRegistry, register, registry

__all__ = [
    "Factor",
    "FactorCategory",
    "FactorRegistry",
    "register",
    "registry",
    "preprocess",
    "standardize",
    "winsorize_mad",
    "neutralize_industry",
    "compute_ic",
    "compute_ic_series",
    "compute_forward_returns",
]
