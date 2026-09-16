"""LightGBM rolling retraining (walk-forward) over Alpha158 features."""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import lightgbm as lgb
import pandas as pd
from loguru import logger

from quant_trade.data.store import DataStore
from quant_trade.models.features import build_feature_matrix

DEFAULT_PARAMS: dict[str, object] = {
    "objective": "regression",
    "metric": "mse",
    "num_leaves": 31,
    "min_data_in_leaf": 20,
    "learning_rate": 0.05,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "verbosity": -1,
    "num_threads": 4,
}


@dataclass
class TrainConfig:
    """Walk-forward training configuration."""

    train_years: float = 8.0
    valid_years: float = 1.0
    predict_months: int = 3
    params: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_PARAMS))
    early_stopping: int = 50
    num_boost_round: int = 1000


def walk_forward_train(
    store: DataStore,
    universe: list[str],
    start: date,
    end: date,
    factors: list[str] | None = None,
    config: TrainConfig | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Rolling retrain: train on the past, predict the next window.

    Returns:
        predictions: DataFrame (ts_code, trade_date, score)
        feature_matrix: the full preprocessed matrix used (for evaluation)
    """
    cfg = config or TrainConfig()
    # Fetch features from before `start` so the first training window is complete
    matrix_start = start - pd.Timedelta(days=int(cfg.train_years * 365) + 30)
    matrix = build_feature_matrix(store, universe, matrix_start, end, factors=factors)
    if matrix.empty:
        logger.error("walk_forward_train: empty feature matrix")
        return pd.DataFrame(), matrix

    feature_cols = [c for c in matrix.columns if c not in ("ts_code", "trade_date", "label")]
    calendar = _sorted_dates(matrix)
    signal_dates = _signal_dates(calendar, start, end, cfg.predict_months)
    if not signal_dates:
        logger.error("walk_forward_train: no signal dates in range")
        return pd.DataFrame(), matrix

    all_preds: list[pd.DataFrame] = []
    for i, sig in enumerate(signal_dates):
        # Training data: strictly before the first signal date, labels
        # must be fully realized inside the training window (no lookahead):
        # a sample at date d has label close[d+2]/close[d+1]-1, so require
        # d + 2 <= cutoff.
        cutoff = _shift_date(calendar, sig, -2)
        label_cutoff = _shift_date(calendar, cutoff, -2)
        train_start = _shift_date(calendar, label_cutoff, -int(cfg.train_years * 252))
        train_mask = (matrix["trade_date"] >= train_start) & (matrix["trade_date"] <= label_cutoff)
        train = matrix[train_mask].dropna(subset=["label"])
        if train.empty:
            logger.warning(f"window {i}: no training samples before {sig}")
            continue

        valid_start = _shift_date(calendar, cutoff, -int(cfg.valid_years * 252))
        valid = train[train["trade_date"] >= valid_start]
        trn = train[train["trade_date"] < valid_start]
        if trn.empty:
            logger.warning(f"window {i}: no training samples before {sig}, skipping")
            continue

        logger.info(
            f"window {i}: signal {sig} | train {trn.shape[0]} samples ({train_start}..{valid_start}) "
            f"| valid {valid.shape[0]}"
        )
        model = lgb.LGBMRegressor(**cfg.params, n_estimators=cfg.num_boost_round)
        model.fit(
            trn[feature_cols],
            trn["label"],
            eval_X=valid[feature_cols].to_numpy(),
            eval_y=valid["label"].to_numpy(),
            callbacks=[lgb.early_stopping(cfg.early_stopping, verbose=False)],
        )

        pred_dates = [d for d in calendar if sig <= d < _next_signal(signal_dates, i)]
        for d in pred_dates:
            day_rows = matrix[matrix["trade_date"] == d]
            if day_rows.empty:
                continue
            day = day_rows.dropna(subset=feature_cols)
            if day.empty:
                continue
            scores = model.predict(day[feature_cols], num_iteration=model.best_iteration_)
            all_preds.append(pd.DataFrame({"ts_code": day["ts_code"], "trade_date": d, "score": scores}))

    predictions = pd.concat(all_preds, ignore_index=True) if all_preds else pd.DataFrame()
    return predictions, matrix


def save_predictions(predictions: pd.DataFrame, path: str | Path) -> None:
    """Persist predictions to parquet."""
    if predictions.empty:
        logger.warning("save_predictions: no predictions to save")
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    predictions.to_parquet(p)


def load_predictions(path: str | Path) -> pd.DataFrame:
    """Load persisted predictions; returns empty DataFrame if missing."""
    p = Path(path)
    if not p.exists():
        logger.warning(f"load_predictions: {p} not found")
        return pd.DataFrame()
    return pd.read_parquet(p)


def _sorted_dates(matrix: pd.DataFrame) -> list[date]:
    return sorted(matrix["trade_date"].unique().tolist())


def _shift_date(calendar: list[date], d: date, offset: int) -> date:
    """Shift a date by ``offset`` positions in the calendar (clamped)."""
    try:
        idx = calendar.index(d)
    except ValueError:
        idx = min(range(len(calendar)), key=lambda i: abs((calendar[i] - d).days))
    return calendar[max(0, min(len(calendar) - 1, idx + offset))]


def _signal_dates(calendar: list[date], start: date, end: date, months: int) -> list[date]:
    """Signal dates: every ``months`` months, on calendar dates in [start, end]."""
    in_range = [d for d in calendar if start <= d <= end]
    if not in_range:
        return []
    step = max(1, int(months * 21))  # ~21 trading days per month
    return in_range[::step]


def _next_signal(signal_dates: list[date], i: int) -> date:
    if i + 1 < len(signal_dates):
        return signal_dates[i + 1]
    # Sentinels far in the future: last window predicts to the data end
    return date(9999, 12, 31)
