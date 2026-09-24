"""Test comparison engine — metrics, weekly diff annotation."""

import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_trade.data.calendar import _to_date as calendar_to_date
from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.simulator.comparison import ComparisonEngine, _deviation, _target_portfolio, _to_date
from quant_trade.simulator.engine import Simulator
from quant_trade.simulator.session import SessionStore
from quant_trade.simulator.types import OrderRequest, StrategySignalItem, WeeklyDiff
from quant_trade.strategies.base import Order, SignalResult, Strategy
from quant_trade.strategies.registry import register_strategy


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


@register_strategy("comparison-stub")
class _StubStrategy(Strategy):
    """A reference strategy with a fixed opinion, so the shadow line is checkable."""

    def generate_signals(self, date: date, universe: list[str], data: object) -> SignalResult:
        return SignalResult(orders=[Order(ts_code="600519.SH", target_pct=0.5, direction="BUY", reason="stub 买入")])


class TestDateNormalisation:
    """Timestamps must not survive into the engines.

    `pd.Timestamp` subclasses `datetime.date`, so an `isinstance(d, date)` check
    written first lets one through — which is how `run_backtest` came to compare
    a `date` against a `Timestamp` and blow up inside the weekly loop.
    """

    def test_timestamp_becomes_a_plain_date(self) -> None:
        assert _to_date(pd.Timestamp("2023-06-01")) == date(2023, 6, 1)
        assert not isinstance(_to_date(pd.Timestamp("2023-06-01")), pd.Timestamp)

    def test_comparison_reuses_the_tested_helper(self) -> None:
        """Three copies of this function existed; the comparison one was wrong."""
        assert _to_date is calendar_to_date

    def test_date_passes_through(self) -> None:
        assert _to_date(date(2023, 6, 1)) == date(2023, 6, 1)


class TestTargetPortfolio:
    """Only positive-weight buys define the portfolio a week was aiming at."""

    def test_sells_are_not_part_of_the_target(self) -> None:
        orders = [
            OrderRequest(ts_code="600519.SH", target_pct=0.5, direction="BUY"),
            OrderRequest(ts_code="000858.SZ", target_pct=0.0, direction="SELL"),
        ]
        assert _target_portfolio(orders) == {"600519.SH"}

    def test_zero_weight_buy_drops_out(self) -> None:
        orders = [OrderRequest(ts_code="600519.SH", target_pct=0.0, direction="BUY")]
        assert _target_portfolio(orders) == set()


class TestDeviation:
    def test_followed_exactly(self) -> None:
        orders = [OrderRequest(ts_code="600519.SH", target_pct=0.5, direction="BUY")]
        signals = [StrategySignalItem(ts_code="600519.SH", target_pct=0.5, direction="BUY", reason="stub")]

        deviation = _deviation(orders, signals)

        assert deviation is not None
        assert deviation.followed is True
        assert deviation.dropped == []
        assert deviation.added == []

    def test_partial_adoption(self) -> None:
        orders = [
            OrderRequest(ts_code="600519.SH", target_pct=0.5, direction="BUY"),
            OrderRequest(ts_code="601318.SH", target_pct=0.2, direction="BUY"),
        ]
        signals = [
            StrategySignalItem(ts_code="600519.SH", target_pct=0.5, direction="BUY", reason="stub"),
            StrategySignalItem(ts_code="000858.SZ", target_pct=0.5, direction="BUY", reason="stub"),
        ]

        deviation = _deviation(orders, signals)

        assert deviation is not None
        assert deviation.followed is False
        assert deviation.dropped == ["000858.SZ"]
        assert deviation.added == ["601318.SH"]

    def test_weight_change_alone_is_not_a_deviation(self) -> None:
        """Same names, different sizing — the portfolio is the same, so it follows."""
        orders = [OrderRequest(ts_code="600519.SH", target_pct=0.9, direction="BUY")]
        signals = [StrategySignalItem(ts_code="600519.SH", target_pct=0.5, direction="BUY", reason="stub")]

        deviation = _deviation(orders, signals)

        assert deviation is not None
        assert deviation.followed is True

    def test_no_recommendation_stays_none(self) -> None:
        """No reference strategy is not the same fact as following one."""
        assert _deviation([], None) is None


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

            assert result.weekly_diffs[0].concentration_warning is not None
            assert "集中" in result.weekly_diffs[0].concentration_warning

    def test_strategy_nav_is_produced(self) -> None:
        """The shadow backtest used to blow up on a Timestamp and vanish silently."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            r = sim.create(name="策略线测试", start_date=BASE, reference_strategy="comparison-stub")
            sid = r["session_id"]
            sim.step(sid, [OrderRequest(ts_code="600519.SH", target_pct=0.5, direction="BUY")])

            engine = ComparisonEngine(store, SessionStore(store.conn, tmp))
            result = engine.compare(sid)

            assert result.strategy_error is None
            assert result.nav_strategy, "the strategy line should have points"
            assert "strategy" in result.metrics

    def test_shadow_backtest_failure_is_reported_not_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A broken backtest costs its own line, not the whole report.

        The failure has to escape `run_backtest` to land here — that is the
        shape the date-type bug took, and it is why the strategy line vanished
        with nothing on screen to say so.
        """
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            r = sim.create(name="策略失败测试", start_date=BASE, reference_strategy="comparison-stub")
            sid = r["session_id"]
            sim.step(sid, [OrderRequest(ts_code="600519.SH", target_pct=0.5, direction="BUY")])

            def _explode(*args: object, **kwargs: object) -> None:
                raise RuntimeError("回测炸了")

            monkeypatch.setattr("quant_trade.simulator.comparison.run_backtest", _explode)

            engine = ComparisonEngine(store, SessionStore(store.conn, tmp))
            result = engine.compare(sid)

            assert result.nav_strategy is None
            assert result.strategy_error is not None
            assert "回测炸了" in result.strategy_error
            # The other two lines are still there.
            assert result.nav_manual
            assert result.nav_benchmark

    def test_unregistered_strategy_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            r = sim.create(name="未知策略测试", start_date=BASE, reference_strategy="no-such-strategy")
            sid = r["session_id"]
            sim.step(sid, [OrderRequest(ts_code="600519.SH", target_pct=0.5, direction="BUY")])

            engine = ComparisonEngine(store, SessionStore(store.conn, tmp))
            result = engine.compare(sid)

            assert result.nav_strategy is None
            assert result.strategy_error is not None
            assert "no-such-strategy" in result.strategy_error

    def test_weekly_diff_follows_the_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            r = sim.create(name="跟随测试", start_date=BASE, reference_strategy="comparison-stub")
            sid = r["session_id"]
            # The stub recommends exactly this.
            sim.step(sid, [OrderRequest(ts_code="600519.SH", target_pct=0.5, direction="BUY")])

            engine = ComparisonEngine(store, SessionStore(store.conn, tmp))
            result = engine.compare(sid)

            deviation = result.weekly_diffs[0].deviation
            assert deviation is not None
            assert deviation.followed is True

    def test_weekly_diff_records_a_split_from_the_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            r = sim.create(name="偏离测试", start_date=BASE, reference_strategy="comparison-stub")
            sid = r["session_id"]
            # Stub says 600519.SH only; the user swaps it for 000858.SZ.
            sim.step(sid, [OrderRequest(ts_code="000858.SZ", target_pct=0.5, direction="BUY")])

            engine = ComparisonEngine(store, SessionStore(store.conn, tmp))
            result = engine.compare(sid)

            deviation = result.weekly_diffs[0].deviation
            assert deviation is not None
            assert deviation.followed is False
            assert deviation.dropped == ["600519.SH"]
            assert deviation.added == ["000858.SZ"]

    def test_drawdown_warning_fires_on_a_bad_week(self) -> None:
        """The annotation had no test at all — nothing asserted it ever fires."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_db(tmp + "/test.db")
            engine = ComparisonEngine(store, SessionStore(store.conn, tmp))

            diffs = [
                WeeklyDiff(week_number=1, cursor_date=date(2023, 6, 2)),
                WeeklyDiff(week_number=2, cursor_date=date(2023, 6, 9)),
                WeeklyDiff(week_number=3, cursor_date=date(2023, 6, 16)),
            ]
            nav = [
                {"trade_date": "2023-06-02", "nav": 100_000.0},
                {"trade_date": "2023-06-09", "nav": 80_000.0},  # -20%
                {"trade_date": "2023-06-16", "nav": 82_000.0},  # +2.5%
            ]

            engine._annotate_extremes(diffs, nav)

            assert diffs[0].drawdown_warning is None
            assert diffs[1].drawdown_warning is not None
            assert "周回撤" in diffs[1].drawdown_warning
            assert diffs[2].drawdown_warning is None

    def test_exported_report_matches_the_api_deviation(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """The HTML table has to carry the same three columns the page shows."""
        monkeypatch.chdir(tmp_path)  # the export writes to a relative `reports/`

        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            r = sim.create(name="导出测试", start_date=BASE, reference_strategy="comparison-stub")
            sid = r["session_id"]
            # The stub recommends 600519.SH; the user takes a different name instead.
            sim.step(sid, [OrderRequest(ts_code="000858.SZ", target_pct=0.5, direction="BUY")])

            engine = ComparisonEngine(store, SessionStore(store.conn, tmp))
            result = engine.compare(sid, export_html=True)

            assert result.html_path is not None
            html = Path(result.html_path).read_text(encoding="utf-8")

        for header in ("完全跟随", "你剔除", "你额外加"):
            assert header in html
        # Week 1 dropped the recommended name — it belongs in that column.
        assert "600519.SH" in html

    def test_weekly_diff_without_a_recommendation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_db(db_path)
            sim = Simulator(store=store, db_path=db_path, data_dir=tmp)

            r = sim.create(name="无策略测试", start_date=BASE)
            sid = r["session_id"]
            sim.step(sid, [OrderRequest(ts_code="600519.SH", target_pct=0.5, direction="BUY")])

            engine = ComparisonEngine(store, SessionStore(store.conn, tmp))
            result = engine.compare(sid)

            assert result.weekly_diffs[0].deviation is None
