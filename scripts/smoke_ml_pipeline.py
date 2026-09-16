"""E2E smoke test: alpha158 -> train -> predict -> strategy on synthetic data.

Run: uv run python scripts/smoke_ml_pipeline.py
"""

import tempfile
from datetime import date, timedelta

import numpy as np

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.factors.alpha158 import compute_alpha158, save_factor_values
from quant_trade.models import rank_ic_series, walk_forward_train
from quant_trade.strategies.model_strategy import ModelStrategy

N_STOCKS = 20
N_DAYS = 800  # ~3.2 years of trading days


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = f"{tmp}/smoke.db"
        conn = init_db(db)
        store = DataStore(db)

        # Synthetic calendar: weekdays
        days: list[date] = []
        d = date(2021, 1, 1)
        while len(days) < N_DAYS:
            if d.weekday() < 5:
                days.append(d)
            d += timedelta(days=1)
        conn.executemany("INSERT INTO trade_calendar VALUES (?, TRUE)", [(x,) for x in days])

        codes = [f"{i:06d}.SH" for i in range(N_STOCKS)]
        conn.executemany(
            "INSERT INTO stock_basic (ts_code, name, industry, market, list_date) VALUES (?, ?, ?, ?, ?)",
            [(c, f"stock{i}", f"ind{i % 5}", "main", date(2020, 1, 1)) for i, c in enumerate(codes)],
        )
        conn.executemany(
            "INSERT INTO index_weights (index_code, ts_code, weight, in_date, out_date) VALUES (?, ?, ?, ?, ?)",
            [("000300.SH", c, 0.05, date(2020, 1, 1), date(2030, 1, 1)) for c in codes],
        )

        # Synthetic kline with momentum signal so the model has something to learn
        rows = []
        for c in codes:
            rng = np.random.default_rng(hash(c) % 2**32)
            close = 10.0
            drift = rng.normal(0, 0.001)  # per-stock drift = learnable signal
            for _i, day in enumerate(days):
                close *= 1 + drift + rng.normal(0, 0.01)
                vol = rng.uniform(5e5, 2e6)
                rows.append((c, day, close * 0.99, close * 1.01, close * 0.98, close, vol, vol * 10, None, None))
        conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)", rows)

        # 1. alpha158
        calc_start = days[60]
        values = compute_alpha158(store, calc_start, days[-1], codes)
        print(f"alpha158: {values['factor_name'].nunique()} factors, {len(values)} rows")
        n = save_factor_values(store, values)
        print(f"saved {n} rows")

        # 2. walk-forward train
        preds, _ = walk_forward_train(store, codes, calc_start, days[-1])
        print(f"predictions: {len(preds)} rows, {preds['trade_date'].nunique()} dates")

        # 3. rank IC
        ic = rank_ic_series(store, codes, preds)
        print(f"rankIC mean={ic['ic_mean']:.4f} icir={ic['ic_ir']:.2f} pos={ic['ic_positive_ratio']:.1%}")

        # 4. strategy
        pred_path = f"{tmp}/model_ranking.parquet"
        preds.to_parquet(pred_path)
        strat = ModelStrategy(predictions_path=pred_path, top_n=5, store=store)
        sig_date = sorted(preds["trade_date"].unique())[10]
        sig = strat.generate_signals(sig_date, codes, store)
        print(f"signal at {sig_date}: {len(sig.orders)} orders, first={sig.orders[0].ts_code if sig.orders else None}")


if __name__ == "__main__":
    main()
