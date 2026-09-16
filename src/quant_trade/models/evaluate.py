"""Model evaluation: RankIC of predictions vs realized T+2 returns."""

from datetime import date

import pandas as pd
from scipy import stats

from quant_trade.data.store import DataStore
from quant_trade.models.features import build_label


def rank_ic_series(
    store: DataStore,
    universe: list[str],
    predictions: pd.DataFrame,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, object]:
    """RankIC (spearman) per prediction date plus summary metrics.

    Args:
        predictions: DataFrame with ts_code, trade_date, score.
        start/end: optional date range filter on trade_date.

    Returns dict with ic_series (list of (date, rank_ic)), ic_mean, ic_ir,
    ic_positive_ratio.
    """
    if predictions.empty:
        return {"ic_series": [], "ic_mean": float("nan"), "ic_ir": float("nan"), "ic_positive_ratio": float("nan")}

    pred = predictions.copy()
    if start is not None:
        pred = pred[pred["trade_date"] >= start]
    if end is not None:
        pred = pred[pred["trade_date"] <= end]

    if pred.empty:
        return {"ic_series": [], "ic_mean": float("nan"), "ic_ir": float("nan"), "ic_positive_ratio": float("nan")}

    dates = sorted(pred["trade_date"].unique().tolist())
    labels = build_label(store, universe, dates[0], dates[-1])

    ics: list[float] = []
    ic_series: list[tuple[date, float]] = []
    for d in dates:
        day_pred = pred[pred["trade_date"] == d]
        day_label = labels[labels["trade_date"] == d]
        merged = day_pred.merge(day_label, on=["ts_code", "trade_date"], how="inner").dropna()
        if len(merged) < 10:
            continue
        ic = float(stats.spearmanr(merged["score"], merged["label"])[0])
        if pd.isna(ic):
            continue
        ics.append(ic)
        ic_series.append((d, ic))

    if not ics:
        return {"ic_series": [], "ic_mean": float("nan"), "ic_ir": float("nan"), "ic_positive_ratio": float("nan")}

    s = pd.Series(ics)
    return {
        "ic_series": ic_series,
        "ic_mean": float(s.mean()),
        "ic_ir": float(s.mean() / s.std()) if s.std() > 0 else float("nan"),
        "ic_positive_ratio": float((s > 0).mean()),
    }
