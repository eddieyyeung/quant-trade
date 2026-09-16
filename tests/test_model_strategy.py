"""Tests for ModelStrategy (model_ranking)."""

import tempfile
from datetime import date

import pandas as pd
import pytest

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.strategies.model_strategy import ModelStrategy
from quant_trade.strategies.registry import strategy_registry

CODES = ["000001.SZ", "600000.SH", "300001.SZ", "600519.SH", "601318.SH"]
SIG_DATE = date(2024, 3, 1)


def _build_store(db_path: str) -> DataStore:
    conn = init_db(db_path)
    conn.executemany(
        "INSERT INTO stock_basic (ts_code, name, industry, market, list_date) VALUES (?, ?, ?, ?, ?)",
        [
            ("000001.SZ", "a", "银行", "main", date(2020, 1, 1)),
            ("600000.SH", "b", "银行", "main", date(2020, 1, 1)),
            ("300001.SZ", "c", "科技", "main", date(2020, 1, 1)),
            ("600519.SH", "d", "消费", "main", date(2020, 1, 1)),
            ("601318.SH", "e", "保险", "main", date(2020, 1, 1)),
        ],
    )
    return DataStore(db_path)


def _write_preds(tmp: str, rows: list[tuple[str, date, float]]) -> str:
    path = f"{tmp}/preds.parquet"
    pd.DataFrame(rows, columns=["ts_code", "trade_date", "score"]).to_parquet(path)
    return path


class TestSignals:
    def test_empty_predictions_returns_empty_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_store(tmp + "/a.db")
            strat = ModelStrategy(predictions_path=f"{tmp}/missing.parquet", store=store)
            sig = strat.generate_signals(SIG_DATE, CODES, store)
            assert sig.orders == []
            assert sig.weights == {}

    def test_missing_date_returns_empty_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_store(tmp + "/a.db")
            path = _write_preds(tmp, [(c, SIG_DATE, 1.0) for c in CODES])
            strat = ModelStrategy(predictions_path=path, store=store)
            sig = strat.generate_signals(date(2024, 3, 2), CODES, store)
            assert sig.orders == []

    def test_top_n_selection_and_weights(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_store(tmp + "/a.db")
            scores = list(zip(CODES, [0.9, 0.1, 0.5, 0.7, 0.3], strict=True))
            path = _write_preds(tmp, [(c, SIG_DATE, s) for c, s in scores])
            strat = ModelStrategy(predictions_path=path, top_n=3, max_industry_weight=1.0, store=store)
            sig = strat.generate_signals(SIG_DATE, CODES, store)

            assert len(sig.orders) == 3
            picked = {o.ts_code for o in sig.orders}
            assert picked == {"000001.SZ", "600519.SH", "300001.SZ"}  # top-3 scores
            assert all(o.target_pct == pytest.approx(1 / 3) for o in sig.orders)
            assert all(o.direction == "BUY" for o in sig.orders)

    def test_industry_constraint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_store(tmp + "/a.db")
            # Two 银行 stocks score highest; cap at 1 name per industry
            rows = [
                (c, SIG_DATE, s)
                for c, s in [
                    ("000001.SZ", 0.9),
                    ("600000.SH", 0.8),
                    ("300001.SZ", 0.7),
                    ("600519.SH", 0.6),
                    ("601318.SH", 0.5),
                ]
            ]
            path = _write_preds(tmp, rows)
            strat = ModelStrategy(predictions_path=path, top_n=3, max_industry_weight=0.34, store=store)
            sig = strat.generate_signals(SIG_DATE, CODES, store)

            picked = [o.ts_code for o in sig.orders]
            banks = [c for c in picked if c in {"000001.SZ", "600000.SH"}]
            assert len(banks) == 1  # only the higher-scoring bank kept


class TestRegistration:
    def test_registered_as_model_ranking(self) -> None:
        strat = strategy_registry.get("model_ranking")
        assert strat is not None
        assert strat.name == "model_ranking"
