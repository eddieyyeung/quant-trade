"""Test simulator engine — create, resume, step, skip."""

import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.simulator.engine import Simulator
from quant_trade.simulator.types import OrderRequest


def _build_engine_db(db_path: str) -> DataStore:
    Path(db_path).unlink(missing_ok=True)
    conn = init_db(db_path)

    today = date.today()
    start = date(today.year - 1, 1, 1)

    # Generate ALL weekdays as trade dates (dense calendar)
    trade_dates: list[date] = []
    d = start
    end_gen = today
    while d <= end_gen:
        if d.weekday() < 5:
            trade_dates.append(d)
        d += timedelta(days=1)

    stock_rows = [
        ("000300.SH", "沪深300", "金融", "main", start, False),
        ("600519.SH", "贵州茅台", "制造", "main", start, False),
        ("000858.SZ", "五粮液", "制造", "main", start, False),
        ("601318.SH", "中国平安", "金融", "main", start, False),
    ]
    conn.executemany("INSERT INTO stock_basic VALUES (?, ?, ?, ?, ?, ?)", stock_rows)
    conn.executemany("INSERT INTO trade_calendar VALUES (?, ?)", [(td, True) for td in trade_dates])

    np.random.seed(42)
    kline_rows: list[tuple] = []
    for code, *_ in stock_rows:
        price = 3000.0 if code == "000300.SH" else float(np.random.uniform(10, 100))
        for td in trade_dates:
            ret = np.random.normal(0.0005, 0.02)
            price *= 1 + ret
            kline_rows.append(
                (
                    code,
                    td,
                    price * 0.99,
                    price * 1.02,
                    price * 0.98,
                    price,
                    np.random.uniform(1e6, 1e8),
                    price * np.random.uniform(1e6, 1e8),
                    np.random.normal(0, 2.0),
                    np.random.uniform(0.5, 5.0),
                )
            )
    conn.executemany("INSERT INTO daily_kline VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", kline_rows)

    for code in ["600519.SH", "000858.SZ", "601318.SH"]:
        conn.execute(
            "INSERT INTO index_weights VALUES (?, ?, ?, ?, ?)",
            ["000300.SH", code, 0.03, start, None],
        )

    store = DataStore(db_path)
    return store


BASE_START = date(date.today().year - 1, 3, 1)


class TestSimulatorEngine:
    def test_create_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_engine_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            result = sim.create(name="测试引擎", start_date=BASE_START)
            assert result["session_id"]
            assert result["cursor_date"]
            assert result["total_weeks"] > 0

    def test_resume_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_engine_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            create_result = sim.create(name="恢复测试", start_date=BASE_START)
            sid = create_result["session_id"]

            resume_result = sim.resume(sid)
            assert resume_result["session_id"] == sid
            assert resume_result["week_number"] == 1
            assert resume_result["portfolio_value"] == 100000

            # resume() uses the full snapshot path (no skip_heavy)
            snap = resume_result["snapshot"]
            assert isinstance(snap.factor_ranking, list)
            assert not any("首次调仓" in w for w in snap.data_warnings)

    def test_skip_advances_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_engine_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            create_result = sim.create(name="跳过测试", start_date=BASE_START)
            sid = create_result["session_id"]
            initial_cursor = create_result["cursor_date"]

            step_result = sim.skip(sid)
            assert step_result.cursor_advanced
            assert step_result.next_cursor_date > date.fromisoformat(initial_cursor)
            assert step_result.decision.decision_number == 1

    def test_step_with_buy_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_engine_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            create_result = sim.create(name="买入测试", start_date=BASE_START)
            sid = create_result["session_id"]

            orders = [OrderRequest(ts_code="600519.SH", target_pct=0.2, direction="BUY")]
            step_result = sim.step(sid, orders, notes="测试买入茅台")

            assert step_result.portfolio_total_value > 0
            assert step_result.decision is not None
            assert step_result.decision.notes == "测试买入茅台"

    def test_status_returns_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_engine_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            create_result = sim.create(name="状态测试", start_date=BASE_START)
            sid = create_result["session_id"]

            status = sim.status(sid)
            assert status["name"] == "状态测试"
            assert status["status"] == "active"
            assert status["portfolio_value"] == 100000
            assert status["decision_count"] == 0

    def test_snapshot_returns_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_engine_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            create_result = sim.create(name="快照测试", start_date=BASE_START)
            sid = create_result["session_id"]

            snap = sim.snapshot(sid)
            assert snap is not None
            assert snap.week_number == 1

    def test_create_session_lightweight_snapshot(self) -> None:
        """Initial snapshot skips factor ranking and strategy signals."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_engine_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            result = sim.create(name="轻量快照测试", start_date=BASE_START)
            snap = result["snapshot"]

            assert snap.factor_ranking == []
            assert snap.strategy_signals is None
            assert any("首次调仓" in w for w in snap.data_warnings)

    def test_step_has_full_snapshot(self) -> None:
        """Step snapshot includes factor ranking (not deferred)."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_engine_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            create_result = sim.create(name="完整快照测试", start_date=BASE_START)
            sid = create_result["session_id"]

            orders = [OrderRequest(ts_code="600519.SH", target_pct=0.2, direction="BUY")]
            step_result = sim.step(sid, orders)
            snap = step_result.decision.snapshot_before

            # factor_ranking should be a list (may be empty in test env without factors)
            assert isinstance(snap.factor_ranking, list)
            # strategy_signals should be None or list (None if no ref strategy set)
            assert snap.strategy_signals is None or isinstance(snap.strategy_signals, list)
            # Deferred warning must NOT be present
            assert not any("首次调仓" in w for w in snap.data_warnings)

    def test_resume_nonexistent_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_engine_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            try:
                sim.resume("bad-id")
                raise AssertionError("Should have raised ValueError")
            except ValueError:
                pass
