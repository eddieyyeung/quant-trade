"""CLI chain test: factor alpha158 -> model train -> model predict -> backtest."""

import sys
import tempfile
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_trade.cli import _cmd_backtest, _cmd_factor, _cmd_model
from quant_trade.config import AppConfig, DataConfig
from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore

CODES = ["000001.SZ", "600000.SH", "300001.SZ", "600519.SH", "601318.SH"]
N_DAYS = 500  # ~2 years of trading days: enough for a 1-year valid window


def _build_store(db_path: str) -> DataStore:
    conn = init_db(db_path)
    days: list[date] = []
    d = date(2024, 1, 1)
    while len(days) < N_DAYS:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    conn.executemany("INSERT INTO trade_calendar VALUES (?, TRUE)", [(x,) for x in days])

    conn.executemany(
        "INSERT INTO stock_basic (ts_code, name, industry, market, list_date) VALUES (?, ?, ?, ?, ?)",
        [(c, f"stock{c}", f"ind{i % 3}", "main", date(2020, 1, 1)) for i, c in enumerate(CODES)],
    )
    conn.executemany(
        "INSERT INTO index_weights (index_code, ts_code, weight, in_date, out_date) VALUES (?, ?, ?, ?, ?)",
        [("000300.SH", c, 0.2, date(2020, 1, 1), date(2030, 1, 1)) for c in CODES],
    )

    rows = []
    for c in CODES:
        rng = np.random.default_rng(hash(c) % 2**32)
        close = 10.0
        for day in days:
            close *= 1 + rng.normal(0, 0.01)
            rows.append((c, day, close * 0.99, close * 1.01, close * 0.98, close, 1e6, 1e7, None, None))
    # Benchmark kline (000300.SH is not in the stock universe, only benchmark)
    bench = 1.0
    for day in days:
        bench *= 1 + 0.0002
        rows.append(("000300.SH", day, bench, bench, bench, bench, 1e9, 1e10, None, None))
    conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    return DataStore(db_path)


def test_cli_chain_end_to_end(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _build_store(tmp + "/cli.db")
        config = AppConfig(data=DataConfig(db_path=tmp + "/cli.db"))

        # 1. factor alpha158
        monkeypatch.setattr(sys, "argv", ["quant-trade", "factor", "alpha158", "--start", "2024-01-01"])
        _cmd_factor("alpha158", config)
        n_factors = store.conn.execute("SELECT COUNT(DISTINCT factor_name) FROM factor_values").fetchone()[0]
        assert n_factors == 158

        # 2. model train
        pred_path = tmp + "/model_ranking.parquet"
        _cmd_model("train", config, ["--start", "2024-02-01", "--end", "2025-06-30", "--output", pred_path])
        preds = pd.read_parquet(pred_path)
        assert not preds.empty
        assert set(preds.columns) == {"ts_code", "trade_date", "score"}

        # 3. model predict (latest prediction date)
        last_day = preds["trade_date"].max()
        _cmd_model("predict", config, ["--date", last_day.isoformat(), "--output", pred_path])
        out = capsys.readouterr().out
        assert "Predictions for" in out

        # 4. backtest with model_ranking
        config.strategy.name = "model_ranking"
        config.strategy.params = {"predictions_path": pred_path}
        config.strategy.top_n = 5
        _cmd_backtest("run", config, ["--start", "2024-06-01", "--end", last_day.isoformat()])
        out = capsys.readouterr().out
        assert "Total Return" in out


def test_cli_missing_predictions_reports_error(capfd: pytest.CaptureFixture[str]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _build_store(tmp + "/cli.db")
        config = AppConfig(data=DataConfig(db_path=tmp + "/cli.db"))
        _cmd_model("predict", config, ["--output", tmp + "/nope.parquet"])
        captured = capfd.readouterr()
        assert "Run 'quant-trade model train' first" in captured.out + captured.err
