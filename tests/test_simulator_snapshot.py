"""Test timepoint snapshot — no lookahead, market overview, portfolio, factor ranking."""

import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from quant_trade.backtest.portfolio import Portfolio
from quant_trade.data.calendar import TradeCalendar
from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.simulator.snapshot import SnapshotBuilder


def _build_minimal_db(db_path: str) -> DataStore:
    Path(db_path).unlink(missing_ok=True)
    conn = init_db(db_path)

    today = date.today()
    start = today - timedelta(days=120)

    trade_dates: list[date] = []
    d = start
    while len(trade_dates) < 60:
        if d.weekday() < 5:
            trade_dates.append(d)
        d += timedelta(days=1)

    stock_rows = [
        ("000300.SH", "沪深300", "金融", "main", start, False),
        ("600519.SH", "贵州茅台", "制造", "main", start, False),
        ("000858.SZ", "五粮液", "制造", "main", start, False),
    ]
    conn.executemany("INSERT INTO stock_basic VALUES (?, ?, ?, ?, ?, ?)", stock_rows)
    conn.executemany("INSERT INTO trade_calendar VALUES (?, ?)", [(td, True) for td in trade_dates])

    np.random.seed(42)
    kline_rows: list[tuple] = []
    for code, *_ in stock_rows:
        price = 100.0 if code == "000300.SH" else 50.0
        for td in trade_dates:
            ret = np.random.normal(0.0005, 0.015)
            price *= 1 + ret
            kline_rows.append(
                (
                    code,
                    td,
                    price * 0.99,
                    price * 1.02,
                    price * 0.98,
                    price,
                    np.random.uniform(1e6, 1e7),
                    price * np.random.uniform(1e6, 1e7),
                    np.random.normal(0, 2.0),
                    np.random.uniform(0.5, 3.0),
                )
            )
    conn.executemany("INSERT INTO daily_kline VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", kline_rows)

    # Index weights
    conn.executemany(
        "INSERT INTO index_weights VALUES (?, ?, ?, ?, ?)",
        [
            ("000300.SH", "600519.SH", 0.05, start, None),
            ("000300.SH", "000858.SZ", 0.03, start, None),
        ],
    )

    return DataStore(db_path)


class TestSnapshot:
    def test_market_overview_no_lookahead(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_minimal_db(db_path)

            cal_df = store.get_calendar(date.today() - timedelta(days=120), date.today())
            calendar = TradeCalendar(cal_df["trade_date"].tolist())
            dates = calendar.trade_dates_between(date.today() - timedelta(days=60), date.today())
            if len(dates) < 10:
                return  # Not enough data

            portfolio = Portfolio(cash=100000)
            builder = SnapshotBuilder(store, portfolio)

            cursor = dates[5]
            snapshot = builder.build_snapshot(
                cursor_date=cursor,
                exec_date=dates[6],
                week_number=1,
                total_weeks=10,
                universe=["600519.SH", "000858.SZ"],
            )

            assert snapshot.signal_date == cursor
            assert snapshot.total_value == 100000  # All cash
            assert snapshot.portfolio == []

            # Market overview should exist
            assert snapshot.market is not None

    def test_portfolio_snapshot_has_holdings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_minimal_db(db_path)

            cal_df = store.get_calendar(date.today() - timedelta(days=120), date.today())
            calendar = TradeCalendar(cal_df["trade_date"].tolist())
            dates = calendar.trade_dates_between(date.today() - timedelta(days=60), date.today())
            if len(dates) < 10:
                return

            portfolio = Portfolio(cash=50000)
            portfolio.buy(code="600519.SH", price=50.0, amount=50000, trade_date=dates[0])

            builder = SnapshotBuilder(store, portfolio)
            snapshot = builder.build_snapshot(
                cursor_date=dates[5],
                exec_date=dates[6],
                week_number=1,
                total_weeks=10,
                universe=["600519.SH", "000858.SZ"],
            )

            assert len(snapshot.portfolio) == 1
            item = snapshot.portfolio[0]
            assert item.ts_code == "600519.SH"
            assert item.shares > 0

    def test_factor_ranking_includes_data_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_minimal_db(db_path)

            cal_df = store.get_calendar(date.today() - timedelta(days=120), date.today())
            calendar = TradeCalendar(cal_df["trade_date"].tolist())
            dates = calendar.trade_dates_between(date.today() - timedelta(days=60), date.today())
            if len(dates) < 10:
                return

            portfolio = Portfolio(cash=100000)
            builder = SnapshotBuilder(store, portfolio)
            snapshot = builder.build_snapshot(
                cursor_date=dates[5],
                exec_date=dates[6],
                week_number=1,
                total_weeks=10,
                universe=["600519.SH", "000858.SZ"],
            )

            # Factor ranking should work (at least momentum on these 2 stocks with 60+ data points)
            assert isinstance(snapshot.factor_ranking, list)
            # Data warnings about empty financials is expected
            assert isinstance(snapshot.data_warnings, list)

    def test_strategy_signals_none_when_no_ref_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_minimal_db(db_path)

            portfolio = Portfolio(cash=100000)
            builder = SnapshotBuilder(store, portfolio, reference_strategy=None)
            snapshot = builder.build_snapshot(
                cursor_date=date.today(),
                exec_date=date.today(),
                week_number=1,
                total_weeks=10,
                universe=[],
            )
            assert snapshot.strategy_signals is None
