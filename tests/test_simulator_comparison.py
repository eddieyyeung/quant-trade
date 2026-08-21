"""Test comparison engine — metrics, weekly diff annotation."""

import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.simulator.comparison import ComparisonEngine
from quant_trade.simulator.engine import Simulator
from quant_trade.simulator.session import SessionStore
from quant_trade.simulator.types import OrderRequest


def _build_db(db_path: str) -> DataStore:
    Path(db_path).unlink(missing_ok=True)
    conn = init_db(db_path)

    today = date.today()
    start = date(today.year - 1, 1, 1)

    trade_dates: list[date] = []
    d = start
    while d <= today:
        if d.weekday() < 5:
            trade_dates.append(d)
        d += timedelta(days=1)

    stocks = [
        ("000300.SH", "沪深300", "金融", "main", start, False),
        ("600519.SH", "茅台", "制造", "main", start, False),
        ("000858.SZ", "五粮液", "制造", "main", start, False),
    ]
    conn.executemany("INSERT INTO stock_basic VALUES (?, ?, ?, ?, ?, ?)", stocks)
    conn.executemany("INSERT INTO trade_calendar VALUES (?, ?)", [(td, True) for td in trade_dates])

    np.random.seed(42)
    kline_rows: list[tuple] = []
    for code, *_ in stocks:
        price = 3000.0 if code == "000300.SH" else 50.0
        for td in trade_dates:
            ret = np.random.normal(0.0005, 0.02)
            price *= 1 + ret
            kline_rows.append((code, td, price * 0.99, price * 1.02, price * 0.98, price, 1e7, price * 1e7, 0.5, 2.0))
    conn.executemany("INSERT INTO daily_kline VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", kline_rows)

    for code in ["600519.SH", "000858.SZ"]:
        conn.execute("INSERT INTO index_weights VALUES (?, ?, ?, ?, ?)", ["000300.SH", code, 0.5, start, None])

    return DataStore(db_path)


BASE = date(date.today().year - 1, 3, 1)


class TestComparison:
    def test_compare_without_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            r = sim.create(name="对比测试", start_date=BASE)
            sid = r["session_id"]

            # Run one step with a buy
            sim.step(sid, [OrderRequest(ts_code="600519.SH", target_pct=0.5, direction="BUY")])

            # Compare
            engine = ComparisonEngine(store, SessionStore(store.conn, tmp))
            result = engine.compare(sid, export_html=False)

            assert result.weeks_completed == 1
            assert "manual" in result.metrics
            assert result.nav_strategy is None  # no ref strategy
            assert result.nav_benchmark is not None
            assert len(result.weekly_diffs) == 1

    def test_concentration_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            r = sim.create(name="集中度测试", start_date=BASE)
            sid = r["session_id"]

            # Buy only 1 stock = concentrated
            sim.step(sid, [OrderRequest(ts_code="600519.SH", target_pct=0.8, direction="BUY")])

            engine = ComparisonEngine(store, SessionStore(store.conn, tmp))
            result = engine.compare(sid)

            assert result.weekly_diffs[0].total_user == 1
            assert result.weekly_diffs[0].concentration_warning is not None
            assert "集中" in result.weekly_diffs[0].concentration_warning

    def test_weekly_diff_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            r = sim.create(name="差异测试", start_date=BASE)
            sid = r["session_id"]

            sim.step(
                sid,
                [
                    OrderRequest(ts_code="600519.SH", target_pct=0.5, direction="BUY"),
                    OrderRequest(ts_code="000858.SZ", target_pct=0.3, direction="BUY"),
                ],
            )

            engine = ComparisonEngine(store, SessionStore(store.conn, tmp))
            result = engine.compare(sid)

            assert result.weekly_diffs[0].total_user == 2
            assert isinstance(result.weekly_diffs[0].common, list)
            assert isinstance(result.weekly_diffs[0].user_only, list)
            assert isinstance(result.weekly_diffs[0].strategy_only, list)
