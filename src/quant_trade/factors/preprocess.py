"""Factor preprocessing pipeline: winsorization, standardization, neutralization."""

import numpy as np
import pandas as pd


def winsorize_mad(series: pd.Series, n_mad: float = 3.0) -> pd.Series:
    """Winsorize using Median Absolute Deviation. Clips values beyond n_mad * MAD from median."""
    median = series.median()
    mad = (series - median).abs().median()
    if mad == 0:
        return series
    upper = median + n_mad * mad
    lower = median - n_mad * mad
    return series.clip(lower, upper)


def fill_na_median(series: pd.Series) -> pd.Series:
    """Fill NaN values with the series median."""
    return series.fillna(series.median())


def standardize(series: pd.Series) -> pd.Series:
    """Z-score standardization: (x - mean) / std."""
    std = series.std()
    if std == 0 or pd.isna(std):
        return pd.Series(0.0, index=series.index)
    return (series - series.mean()) / std


def neutralize_industry(
    series: pd.Series,
    industry_map: dict[str, str],
) -> pd.Series:
    """
    Industry neutralization via dummy variable regression.
    Returns residuals after regressing factor values on industry dummies.
    """
    if series.empty:
        return series

    # Build dummy matrix
    industries = [industry_map.get(code, "未知") for code in series.index]
    unique_inds = sorted(set(industries))
    if len(unique_inds) <= 1:
        return series

    dummy_df = pd.DataFrame(index=series.index)
    for ind in unique_inds[1:]:  # drop first category
        dummy_df[ind] = [1 if i == ind else 0 for i in industries]

    # OLS: factor ~ industry dummies
    X = np.column_stack(
        [np.ones(len(series))] + [np.asarray(dummy_df[col].values, dtype=float) for col in dummy_df.columns]
    )
    y = np.asarray(series.values, dtype=float)

    try:
        beta = np.linalg.lstsq(X, y, rcond=None)[0]
        predicted = X @ beta
        residuals = y - predicted
        return pd.Series(residuals + series.mean(), index=series.index)  # add back mean
    except np.linalg.LinAlgError:
        return series


def preprocess(
    series: pd.Series,
    steps: list[str],
    industry_map: dict[str, str] | None = None,
) -> pd.Series:
    """
    Apply a sequence of preprocessing steps to factor values.

    Args:
        series: Raw factor values indexed by ts_code.
        steps: List of step names: 'winsorize', 'fill_na', 'standardize', 'neutralize'.
        industry_map: Dict of ts_code -> industry name. Required for 'neutralize'.

    Returns:
        Processed Series.
    """
    result = series.copy()
    for step in steps:
        if step == "winsorize":
            result = winsorize_mad(result)
        elif step == "fill_na":
            result = fill_na_median(result)
        elif step == "standardize":
            result = standardize(result)
        elif step == "neutralize":
            if industry_map is None:
                raise ValueError("industry_map is required for neutralization")
            result = neutralize_industry(result, industry_map)
        else:
            raise ValueError(f"Unknown preprocessing step: {step}")
    return result
