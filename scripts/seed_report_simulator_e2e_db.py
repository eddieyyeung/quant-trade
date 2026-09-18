"""Seed a scratch database for `scripts/e2e_report_simulator_pages.mjs`.

Three things the report and simulator pages need in order to render something
real:

* a trade calendar, a handful of stocks and a CSI 300 series, so a simulator
  session can be created and its market overview populated;
* rows in ``ic_series``, so the weekly report's IC panel has a producer — the
  defect this change fixes was that nothing ever wrote them;
* a weekly ``run`` plus its HTML artifact, with the report itself rendered by
  the real code path rather than pasted in. That way the browser check reads
  output the pipeline produced, not a fixture pretending to be one.

Writes ``.scratch/e2e-report-sim/`` and overwrites whatever was there.
"""

from __future__ import annotations

import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRATCH = REPO_ROOT / ".scratch" / "e2e-report-sim"
DB = SCRATCH / "quant.db"
"""Where `scripts/e2e_report_simulator_pages.mjs` expects to find it."""

REPORTS = SCRATCH / "reports"
SIGNAL_DAY = date(2026, 7, 24)
"""A Friday, so the report renders without the off-cycle notice."""

sys.path.insert(0, str(REPO_ROOT / "src"))

from quant_trade.config import AppConfig  # noqa: E402
from quant_trade.data.schema import init_db  # noqa: E402
from quant_trade.data.store import DataStore  # noqa: E402
from quant_trade.factors.ic_store import IC_COLUMNS, save_ic_series  # noqa: E402
from quant_trade.services.context import RunContext  # noqa: E402
from quant_trade.services.factor_analysis import FactorICOverviewParams, factor_ic_overview  # noqa: E402
from quant_trade.signals.reporter import generate_weekly_report, save_report  # noqa: E402
from quant_trade.strategies.signal_store import (  # noqa: E402
    STRATEGY_SIGNAL_KIND,
    build_signal_frame,
    save_strategy_signals,
)

CODES = [f"{i:06d}.SZ" for i in range(1, 11)]
FACTORS = ["MA20", "RSI6"]


def _trading_days() -> list[date]:
    """Weekdays from mid-2024 through the report week.

    Long enough to give the simulator a year of history at its cursor and to
    put the report's own date inside the calendar rather than past its end.
    """
    days: list[date] = []
    day = date(2024, 6, 3)
    while day <= SIGNAL_DAY:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def _seed_market(store: DataStore, days: list[date]) -> None:
    store.conn.executemany("INSERT INTO trade_calendar VALUES (?, TRUE)", [(d,) for d in days])
    store.conn.executemany(
        "INSERT INTO stock_basic (ts_code, name, industry, market, list_date) VALUES (?, ?, ?, ?, ?)",
        [
            ("000300.SH", "沪深300", "指数", "main", date(2018, 1, 1)),
            *[(code, f"股票{i:02d}", f"行业{i % 3}", "main", date(2018, 1, 1)) for i, code in enumerate(CODES)],
        ],
    )
    store.conn.executemany(
        "INSERT INTO index_weights (index_code, ts_code, weight, in_date, out_date) VALUES (?, ?, ?, ?, ?)",
        [("000300.SH", code, 1.0 / len(CODES), date(2018, 1, 1), date(2030, 1, 1)) for code in CODES],
    )

    rows = []
    for code in CODES:
        rng = np.random.default_rng(abs(hash(code)) % 2**32)
        close = float(rng.uniform(8, 40))
        for day in days:
            close *= 1 + rng.normal(0.0004, 0.018)
            rows.append((code, day, close * 0.995, close * 1.01, close * 0.99, close, 1e6, 1e7, None, None))
    # Index rows carry the stock-only columns as 0.0, the way the index sync
    # writes them.
    benchmark = 3800.0
    for day in days:
        benchmark *= 1 + 0.0002
        rows.append(("000300.SH", day, benchmark, benchmark * 1.005, benchmark * 0.995, benchmark, 1e9, 1e10, 0.0, 0.0))
    store.conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)", rows)


def _seed_ic(store: DataStore, days: list[date]) -> None:
    """Two factors with a year of daily IC, so the panel has rows to show."""
    rows = []
    for index, factor in enumerate(FACTORS):
        rng = np.random.default_rng(7 + index)
        for day in days[-250:]:
            ic = float(rng.normal(0.01 + index * 0.02, 0.05))
            rows.append((factor, day, 5, ic, ic * 1.1, 300))
    save_ic_series(store, pd.DataFrame(rows, columns=IC_COLUMNS))


def _trade_log() -> list[dict[str, object]]:
    """Fills for the report, including buy rows that carry no stamp duty."""
    trades: list[dict[str, object]] = []
    for offset in range(7):
        trade: dict[str, object] = {
            "date": (SIGNAL_DAY - timedelta(days=offset)).isoformat(),
            "action": "BUY" if offset % 3 else "SELL",
            "ts_code": CODES[offset % len(CODES)],
            "shares": 100 * (offset + 1),
            "price": 12.5 + offset,
            "commission": 5.0,
            "transfer_fee": 0.1,
        }
        if trade["action"] == "SELL":
            trade["stamp_duty"] = 3.2
        trades.append(trade)
    return trades


def _seed_weekly_run(store: DataStore, config: AppConfig, days: list[date]) -> tuple[str, Path]:
    """Render a report through the real pipeline and register it as a run."""
    ctx = RunContext(run_id="seed", config=config, store=store)
    # Explicitly against the scratch database. A default context would resolve
    # the *configured* database — the repository's own ``data/quant.db`` here,
    # since this script runs with no ``QUANT_CONFIG`` — and report on that
    # instead. It would at least say so in the log now, which is how the
    # omission is meant to be caught (see the `factor-store-injection` spec).
    ic_rows = [
        {"name": row.name, "ic_weekly": row.ic_weekly, "ic_mean": row.ic_mean, "ic_ir": row.ic_ir}
        for row in factor_ic_overview(FactorICOverviewParams(as_of=SIGNAL_DAY), ctx)
    ]
    assert ic_rows, "the IC panel would be empty — the defect this change fixes"

    window = [day for day in days if day <= SIGNAL_DAY][-120:]
    nav = pd.Series([100_000 * (1 + 0.0006 * i) for i in range(len(window))], index=window)
    trades = _trade_log()

    html = generate_weekly_report(
        {
            "nav_series": nav,
            "benchmark_series": None,
            "trade_log": trades,
            "metrics": {"total_return": 0.072, "sharpe_ratio": 1.4, "max_drawdown": -0.06, "win_rate": 0.58},
            "portfolio": None,
            "signal_date": SIGNAL_DAY.isoformat(),
        },
        [
            {"ts_code": CODES[0], "name": CODES[0], "target_pct": 0.1, "direction": "BUY", "reason": "得分靠前"},
            {"ts_code": CODES[1], "name": CODES[1], "target_pct": 0.0, "direction": "SELL", "reason": "跌出目标组合"},
        ],
        config,
        factor_ic_data=ic_rows,
        today=SIGNAL_DAY,
    )
    path = save_report(html, config, today=SIGNAL_DAY)

    run_id = "e2e-weekly-report"
    store.conn.execute(
        "INSERT INTO run (run_id, kind, params_json, status, progress, created_at, started_at, finished_at) "
        "VALUES (?, 'weekly', ?, 'ok', 1.0, ?, ?, ?)",
        [run_id, json.dumps({"as_of": SIGNAL_DAY.isoformat()}), SIGNAL_DAY, SIGNAL_DAY, SIGNAL_DAY],
    )
    store.conn.execute(
        "INSERT INTO artifact (artifact_id, run_id, kind, storage, ref, meta_json) "
        "VALUES (?, ?, 'report', 'html', ?, ?)",
        [
            f"{run_id}-artifact",
            run_id,
            str(path),
            json.dumps({"signal_date": SIGNAL_DAY.isoformat(), "order_count": 2, "trade_count": len(trades)}),
        ],
    )
    return run_id, path


def _seed_strategy_signals(store: DataStore) -> str:
    """A finished signal run with orders, for the strategy pages to read.

    Seeded rather than produced by a live run, now for a different reason than
    when this was written. The live run used to come back empty — its factors
    opened ``DataStore()`` instead of the store it was handed, so they read the
    repository's own database and found none of this universe in it. That is
    fixed (`fix-factor-store-injection`), and the browser script now asserts the
    live run's real output.

    What it cannot assert is exact numbers: the seeded prices come from
    ``np.random.default_rng(abs(hash(code)))``, and ``hash`` of a string is
    randomised per process, so the composite scores — and with them the ranking
    — differ between runs. This fixture is the one with counts known in advance,
    which is what the read path wants to be checked against.
    """
    run_id = "e2e-strategy-signals"
    orders = [
        {
            "ts_code": CODES[index],
            "direction": "BUY" if index % 3 else "SELL",
            "target_pct": 0.1 if index % 3 else 0.0,
            "reason": "综合得分靠前" if index % 3 else "跌出目标组合",
        }
        for index in range(6)
    ]
    save_strategy_signals(store, run_id, build_signal_frame(orders, SIGNAL_DAY, "factor_ranking"))

    store.conn.execute(
        "INSERT INTO run (run_id, kind, params_json, status, progress, created_at, started_at, finished_at) "
        "VALUES (?, ?, ?, 'ok', 1.0, ?, ?, ?)",
        [run_id, STRATEGY_SIGNAL_KIND, '{"strategy": "factor_ranking"}', SIGNAL_DAY, SIGNAL_DAY, SIGNAL_DAY],
    )
    store.conn.execute(
        "INSERT INTO artifact (artifact_id, run_id, kind, storage, ref, row_count, meta_json) "
        "VALUES (?, ?, 'table', 'table', 'strategy_signal', ?, ?)",
        [
            f"{run_id}-artifact",
            run_id,
            len(orders),
            json.dumps(
                {"strategy": "factor_ranking", "signal_date": SIGNAL_DAY.isoformat(), "universe_size": len(CODES)}
            ),
        ],
    )
    return run_id


def main() -> None:
    SCRATCH.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    if DB.exists():
        DB.unlink()
    for stale in REPORTS.glob("*.html"):
        stale.unlink()
    # The simulator keeps each session's decision history beside the database
    # (``data_dir`` is the db's parent), so a scratch run's sessions land here.
    # Only ever paths under SCRATCH: the repository's own ``data/simulator``
    # holds real sessions and is none of this script's business.
    for stale in (SCRATCH / "simulator").glob("*"):
        shutil.rmtree(stale, ignore_errors=True)

    init_db(str(DB)).close()
    store = DataStore(str(DB))
    days = _trading_days()
    _seed_market(store, days)
    _seed_ic(store, days)

    config = AppConfig()
    config.data.db_path = str(DB)
    config.report.output_dir = str(REPORTS)
    run_id, path = _seed_weekly_run(store, config, days)
    signal_run_id = _seed_strategy_signals(store)
    store.close()

    print(f"seeded {DB} ({len(days)} trading days)")
    print(f"weekly run {run_id} -> {path}")
    print(f"strategy signal run {signal_run_id} ({len(CODES)} codes)")


if __name__ == "__main__":
    main()
