"""Alpha158 factor templates — ported from qlib expression definitions.

Groups (qlib ``Alpha158DL.get_feature_config`` defaults: kbar, price,
rolling — the volume group is NOT part of the default config):
- KBAR: 9 single-period OHLC shape factors
- PRICE: 4 price/close ratios (window 0)
- ROLLING: 29 templates x windows [5, 10, 20, 30, 60] = 145 factors

Total: 158. Templates are functions from column expressions to a factor
expression, matching the design decision to use function composition
instead of a string DSL.
"""

from collections.abc import Callable

import polars as pl

from quant_trade.factors.alpha158 import operators as ops

ROLLING_WINDOWS: list[int] = [5, 10, 20, 30, 60]
_EPS = 1e-12

# Columns available in the per-stock group frame
C = {
    "open": pl.col("open"),
    "high": pl.col("high"),
    "low": pl.col("low"),
    "close": pl.col("close"),
    "volume": pl.col("volume"),
    "vwap": pl.col("vwap"),
}
T = pl.int_range(1, pl.len() + 1).cast(pl.Float64)  # time index for regressions


def kbar_factors() -> dict[str, pl.Expr]:
    """9 KBAR factors (single-period OHLC shape)."""
    o, h, lo, c = C["open"], C["high"], C["low"], C["close"]
    rng = h - lo + _EPS
    return {
        "KMID": (c - o) / o,
        "KLEN": (h - lo) / o,
        "KMID2": (c - o) / rng,
        "KUP": (h - pl.max_horizontal(o, c)) / o,
        "KUP2": (h - pl.max_horizontal(o, c)) / rng,
        "KLOW": (pl.min_horizontal(o, c) - lo) / o,
        "KLOW2": (pl.min_horizontal(o, c) - lo) / rng,
        "KSFT": (2 * c - h - lo) / o,
        "KSFT2": (2 * c - h - lo) / rng,
    }


def price_factors() -> dict[str, pl.Expr]:
    """4 PRICE factors: field/close at window 0."""
    return {
        "OPEN0": C["open"] / C["close"],
        "HIGH0": C["high"] / C["close"],
        "LOW0": C["low"] / C["close"],
        "VWAP0": C["vwap"] / C["close"],
    }


def volume_factors() -> dict[str, pl.Expr]:
    """5 VOLUME factors: Ref(volume, d) / (volume + eps), d in 0..4.

    Note: not part of the default Alpha158 config (kbar/price/rolling
    only); kept for completeness.
    """
    v = C["volume"]
    out: dict[str, pl.Expr] = {"VOLUME0": v / (v + _EPS)}
    for d in range(1, 5):
        out[f"VOLUME{d}"] = v.shift(d) / (v + _EPS)
    return out


def _rolling_templates() -> dict[str, Callable[[int], pl.Expr]]:
    """28 rolling templates keyed by qlib name; call with window d."""
    c, h, lo, v = C["close"], C["high"], C["low"], C["volume"]
    close_ret = c / c.shift(1)  # close[t]/close[t-1]
    vol_ret = v / v.shift(1)
    close_diff = c - c.shift(1)
    vol_diff = v - v.shift(1)
    up_down = ops._f(c > c.shift(1))

    return {
        "ROC": lambda d: c.shift(d) / c,
        "MA": lambda d: ops.ts_mean(c, d) / c,
        "STD": lambda d: ops.ts_std(c, d) / c,
        "BETA": lambda d: ops.ts_beta(c, T, d) / c,
        "RSQR": lambda d: ops.ts_rsqr(c, T, d),
        "RESI": lambda d: ops.ts_resi(c, T, d) / c,
        "MAX": lambda d: ops.ts_max(h, d) / c,
        "MIN": lambda d: ops.ts_min(lo, d) / c,
        "QTLU": lambda d: ops.ts_quantile(c, 0.8, d) / c,
        "QTLD": lambda d: ops.ts_quantile(c, 0.2, d) / c,
        "RANK": lambda d: ops.ts_rank(c, d),
        "RSV": lambda d: (c - ops.ts_min(lo, d)) / (ops.ts_max(h, d) - ops.ts_min(lo, d) + _EPS),
        "IMAX": lambda d: ops.ts_idxmax(h, d) / pl.lit(d),
        "IMIN": lambda d: ops.ts_idxmin(lo, d) / pl.lit(d),
        "IMXD": lambda d: (ops.ts_idxmax(h, d) - ops.ts_idxmin(lo, d)) / pl.lit(d),
        "CORR": lambda d: ops.ts_corr(c, (v + 1).log(), d),
        "CORD": lambda d: ops.ts_corr(close_ret, (vol_ret + 1).log(), d),
        "CNTP": lambda d: ops.ts_mean(up_down, d),
        "CNTN": lambda d: ops.ts_mean(ops._f(c < c.shift(1)), d),
        "CNTD": lambda d: ops.ts_mean(up_down, d) - ops.ts_mean(ops._f(c < c.shift(1)), d),
        "SUMP": lambda d: (
            ops.ts_sum(pl.max_horizontal(close_diff, pl.lit(0.0)), d) / (ops.ts_sum(close_diff.abs(), d) + _EPS)
        ),
        "SUMN": lambda d: (
            ops.ts_sum(pl.max_horizontal(-close_diff, pl.lit(0.0)), d) / (ops.ts_sum(close_diff.abs(), d) + _EPS)
        ),
        "SUMD": lambda d: (
            (
                ops.ts_sum(pl.max_horizontal(close_diff, pl.lit(0.0)), d)
                - ops.ts_sum(pl.max_horizontal(-close_diff, pl.lit(0.0)), d)
            )
            / (ops.ts_sum(close_diff.abs(), d) + _EPS)
        ),
        "VMA": lambda d: ops.ts_mean(v, d) / (v + _EPS),
        "VSTD": lambda d: ops.ts_std(v, d) / (v + _EPS),
        "WVMA": lambda d: ops.ts_std((close_ret - 1).abs() * v, d) / (ops.ts_mean((close_ret - 1).abs() * v, d) + _EPS),
        "VSUMP": lambda d: (
            ops.ts_sum(pl.max_horizontal(vol_diff, pl.lit(0.0)), d) / (ops.ts_sum(vol_diff.abs(), d) + _EPS)
        ),
        "VSUMN": lambda d: (
            ops.ts_sum(pl.max_horizontal(-vol_diff, pl.lit(0.0)), d) / (ops.ts_sum(vol_diff.abs(), d) + _EPS)
        ),
        "VSUMD": lambda d: (
            (
                ops.ts_sum(pl.max_horizontal(vol_diff, pl.lit(0.0)), d)
                - ops.ts_sum(pl.max_horizontal(-vol_diff, pl.lit(0.0)), d)
            )
            / (ops.ts_sum(vol_diff.abs(), d) + _EPS)
        ),
    }


def all_factors() -> dict[str, pl.Expr]:
    """All 158 Alpha158 factor expressions, keyed by factor name.

    Matches the qlib default config: kbar (9) + price (4) + rolling
    (29 x 5 = 145). The volume group is excluded.
    """
    factors = kbar_factors()
    factors.update(price_factors())
    for name, template in _rolling_templates().items():
        for d in ROLLING_WINDOWS:
            factors[f"{name}{d}"] = template(d)
    return factors


ALPHA158_NAMES: list[str] = list(all_factors())
