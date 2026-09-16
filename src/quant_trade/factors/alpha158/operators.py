"""Vectorized rolling operators for Alpha158 factors (polars-based).

Each operator mirrors a qlib expression operator (Mean/Std/Max/Min/Sum/
Quantile/Rank/Corr/Cov/Resi/Slope/Rsquare/IdxMax/IdxMin) as a function
taking polars expressions and returning a polars expression. All rolling
operators require a full window (min_periods == window_size), matching
pandas ``rolling(window).apply`` semantics used by qlib.
"""

import polars as pl


def _time_var(d: int) -> float:
    """Sample variance of the sequence 1..d (ddof=1).

    Used for regression-on-time statistics (Slope / Rsquare / Resi).
    Variance is shift-invariant, so any monotonic row index works.
    """
    return d * (d + 1.0) / 12.0


def _f(x: pl.Expr) -> pl.Expr:
    """Cast to float for boolean/derived expressions."""
    return x.cast(pl.Float64)


def ts_mean(x: pl.Expr, d: int) -> pl.Expr:
    return x.rolling_mean(window_size=d, min_samples=d)


def ts_std(x: pl.Expr, d: int) -> pl.Expr:
    return x.rolling_std(window_size=d, min_samples=d)


def ts_max(x: pl.Expr, d: int) -> pl.Expr:
    return x.rolling_max(window_size=d, min_samples=d)


def ts_min(x: pl.Expr, d: int) -> pl.Expr:
    return x.rolling_min(window_size=d, min_samples=d)


def ts_sum(x: pl.Expr, d: int) -> pl.Expr:
    return x.rolling_sum(window_size=d, min_samples=d)


def ts_quantile(x: pl.Expr, q: float, d: int) -> pl.Expr:
    return x.rolling_quantile(quantile=q, interpolation="linear", window_size=d, min_samples=d)


def ts_rank(x: pl.Expr, d: int) -> pl.Expr:
    """Rank of the last value within the window, scaled to (0, 1].

    qlib ``Rank`` returns ``rankdata(x)[-1] / len(x)``. polars
    ``rolling_rank`` returns the raw rank, so divide by the window size.
    """
    return x.rolling_rank(window_size=d, method="average") / pl.lit(d)


def _cov(a: pl.Expr, b: pl.Expr, d: int) -> pl.Expr:
    """Sample covariance of two series over the trailing window.

    cov(a, b) = (sum(a*b) - sum(a)*sum(b)/d) / (d - 1)
    """
    s_ab = ts_sum(a * b, d)
    s_a = ts_sum(a, d)
    s_b = ts_sum(b, d)
    return (s_ab - s_a * s_b / pl.lit(d)) / pl.lit(d - 1)


def ts_cov(a: pl.Expr, b: pl.Expr, d: int) -> pl.Expr:
    return _cov(a, b, d)


def ts_corr(a: pl.Expr, b: pl.Expr, d: int) -> pl.Expr:
    cov = _cov(a, b, d)
    return cov / (ts_std(a, d) * ts_std(b, d))


def _cov_with_time(x: pl.Expr, t: pl.Expr, d: int) -> pl.Expr:
    """Sample covariance (ddof=1) of x with the row index t over the window.

    cov(x, t) = mean(x*t) - mean(x) * mean(t), scaled to sample covariance;
    mean(t) is computed over the window so it works for any monotonic row
    index (covariance with t is shift-invariant).
    """
    cov_pop = ts_sum(x * t, d) / pl.lit(d) - ts_mean(x, d) * ts_mean(t, d)
    return cov_pop * pl.lit(d) / pl.lit(d - 1)


def ts_beta(x: pl.Expr, t: pl.Expr, d: int) -> pl.Expr:
    """Slope of OLS regression of x on time (1..d), i.e. qlib ``Slope``."""
    return _cov_with_time(x, t, d) / pl.lit(_time_var(d))


def ts_rsqr(x: pl.Expr, t: pl.Expr, d: int) -> pl.Expr:
    """R-squared of OLS regression of x on time, i.e. qlib ``Rsquare``.

    R² = corr(x, t)² where var(t) is constant per window.
    """
    cov_xt = _cov_with_time(x, t, d)
    r2 = (cov_xt / pl.lit(_time_var(d) ** 0.5) / ts_std(x, d)) ** 2
    return r2


def ts_resi(x: pl.Expr, t: pl.Expr, d: int) -> pl.Expr:
    """Std of residuals of OLS regression of x on time, i.e. qlib ``Resi``.

    residual_std = std(x) * sqrt(1 - R²)
    """
    return ts_std(x, d) * (1.0 - ts_rsqr(x, t, d)).sqrt()


def ts_idxmax(x: pl.Expr, d: int) -> pl.Expr:
    """Position of the maximum value within the window (qlib ``IdxMax``)."""
    return x.rolling_map(lambda w: float(w.to_numpy().argmax()), window_size=d, min_samples=d)


def ts_idxmin(x: pl.Expr, d: int) -> pl.Expr:
    """Position of the minimum value within the window (qlib ``IdxMin``)."""
    return x.rolling_map(lambda w: float(w.to_numpy().argmin()), window_size=d, min_samples=d)
