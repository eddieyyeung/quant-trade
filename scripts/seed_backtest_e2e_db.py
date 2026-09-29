"""Seed a scratch database for `scripts/e2e_backtest_pages.mjs`.

Thirty synthetic names over 640 trading days: enough for a real weekly backtest
to run with trades, a benchmark, and a curve long enough to chart. Writes
`.scratch/e2e-backtest/quant.db` and overwrites it if present.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
DB = REPO_ROOT / ".scratch" / "e2e-backtest" / "quant.db"
"""Where `scripts/e2e_backtest_pages.mjs` expects to find it — the same path the
sample config in that file's header points at."""

sys.path.insert(0, str(REPO_ROOT / "src"))

from quant_trade.data.schema import init_db  # noqa: E402
from quant_trade.data.store import DataStore  # noqa: E402

CODES = [f"{i:06d}.SZ" for i in range(1, 31)]
N_DAYS = 640


def main() -> None:
    DB.parent.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    init_db(str(DB)).close()
    store = DataStore(str(DB))

    days: list[date] = []
    day = date(2024, 1, 1)
    while len(days) < N_DAYS:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)

    store.conn.executemany("INSERT INTO trade_calendar VALUES (?, TRUE)", [(d,) for d in days])
    store.conn.executemany(
        "INSERT INTO stock_basic (ts_code, name, industry, market, list_date) VALUES (?, ?, ?, ?, ?)",
        [(code, f"股票{index:02d}", f"行业{index % 4}", "main", date(2018, 1, 1)) for index, code in enumerate(CODES)],
    )
    store.conn.executemany(
        "INSERT INTO index_weights (index_code, ts_code, weight, in_date, out_date) VALUES (?, ?, ?, ?, ?)",
        [("000300.SH", code, 1.0 / len(CODES), date(2018, 1, 1), date(2030, 1, 1)) for code in CODES],
    )

    rows = []
    for code in CODES:
        rng = np.random.default_rng(abs(hash(code)) % 2**32)
        close = float(rng.uniform(8, 40))
        drift = rng.normal(0.0004, 0.0004)
        for trading_day in days:
            close *= 1 + rng.normal(drift, 0.018)
            rows.append((code, trading_day, close * 0.995, close * 1.01, close * 0.99, close, 1e6, 1e7, None, None))
    benchmark = 1000.0
    for trading_day in days:
        benchmark *= 1 + 0.0002
        rows.append(("000300.SH", trading_day, benchmark, benchmark, benchmark, benchmark, 1e9, 1e10, None, None))
    store.conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    store.close()
    print(f"seeded {DB} with {len(CODES)} stocks over {len(days)} trading days")


if __name__ == "__main__":
    main()
