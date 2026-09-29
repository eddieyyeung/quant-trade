"""Tests for the ML training pipeline (features, label, walk-forward, IC)."""

import tempfile
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.factors.alpha158 import compute_alpha158, save_factor_values
from quant_trade.models import build_label, rank_ic_series, walk_forward_train
from quant_trade.models.train import TrainConfig, _shift_date

CODES = ["000001.SZ", "600000.SH", "300001.SZ"]


def _build_synthetic(db_path: str, n_days: int = 300) -> DataStore:
    conn = init_db(db_path)
    rows = []
    for c in CODES:
        rng = np.random.default_rng(hash(c) % 2**32)
        closes = 10 + np.cumsum(rng.normal(0, 0.2, n_days))
        vols = rng.uniform(5e5, 2e6, n_days)
        for i in range(n_days):
            d = date(2024, 1, 1) + timedelta(days=i)
            rows.append(
                (c, d, closes[i] * 0.99, closes[i] * 1.01, closes[i] * 0.98, closes[i], vols[i], 1e7, None, None)
            )
    conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    return DataStore(db_path)


def _with_factors(store: DataStore, start: date, end: date) -> None:
    save_factor_values(store, compute_alpha158(store, start, end, CODES))


class TestLabel:
    def test_label_is_t2_return(self) -> None:
        """label = close[t+2]/close[t+1] - 1, aligned per stock."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_synthetic(tmp + "/a.db", n_days=10)
            labels = build_label(store, CODES, date(2024, 1, 1), date(2024, 1, 10))
            k = (
                store.conn.execute("SELECT close FROM daily_kline WHERE ts_code='000001.SZ' ORDER BY trade_date")
                .df()["close"]
                .values
            )
            row0 = labels[(labels.ts_code == "000001.SZ") & (labels.trade_date == date(2024, 1, 1))]
            assert float(row0["label"].iloc[0]) == pytest.approx(k[2] / k[1] - 1, rel=1e-9)

    def test_tail_labels_are_nan(self) -> None:
        """Only the last 2 rows per stock (no t+2) have NaN labels."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_synthetic(tmp + "/a.db", n_days=10)
            labels = build_label(store, CODES, date(2024, 1, 1), date(2024, 1, 10))
            tail = labels[labels.trade_date >= date(2024, 1, 9)]
            assert tail["label"].isna().all()


class TestWalkForward:
    def test_produces_predictions_without_lookahead(self) -> None:
        """Predictions cover only signal dates; train uses pre-cutoff samples."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_synthetic(tmp + "/a.db")
            start, end = date(2024, 2, 1), date(2024, 6, 30)
            # Factors must exist before `start` so the first training window is non-empty
            _with_factors(store, date(2023, 10, 1), end)
            cfg = TrainConfig(train_years=0.2, valid_years=0.1, predict_months=1)
            result = walk_forward_train(store, CODES, start, end, config=cfg)
            preds, matrix = result.predictions, result.feature_matrix

            assert not preds.empty
            assert set(preds.columns) == {"ts_code", "trade_date", "score"}
            assert preds["trade_date"].min() >= start
            assert preds["score"].notna().all()

            # Every training sample's label is realized inside the training
            # window: sample trade_date <= signal - 4 trading days (cutoff at
            # signal-2, label needs d+2 <= cutoff).
            calendar = sorted(matrix["trade_date"].unique().tolist())
            first_signal = sorted(preds["trade_date"].unique())[0]
            label_cutoff = _shift_date(calendar, _shift_date(calendar, first_signal, -2), -2)
            train = matrix[matrix["trade_date"] <= label_cutoff]
            assert train["trade_date"].max() <= label_cutoff

    def test_empty_universe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_synthetic(tmp + "/a.db")
            result = walk_forward_train(store, [], date(2024, 2, 1), date(2024, 3, 1))
            assert result.predictions.empty
            assert result.windows_trained == 0


class TestRankIC:
    def test_ic_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_synthetic(tmp + "/a.db", n_days=60)
            preds = pd.DataFrame(
                {
                    "ts_code": CODES,
                    "trade_date": [date(2024, 2, 1)] * 3,
                    "score": [1.0, 0.0, -1.0],
                }
            )
            ic = rank_ic_series(store, CODES, preds)
            assert isinstance(ic["ic_mean"], float)
            assert "ic_ir" in ic and "ic_positive_ratio" in ic
            assert isinstance(ic["ic_series"], list)

    def test_empty_predictions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_synthetic(tmp + "/a.db")
            ic = rank_ic_series(store, CODES, pd.DataFrame())
            assert ic["ic_series"] == []
            assert np.isnan(ic["ic_mean"])
