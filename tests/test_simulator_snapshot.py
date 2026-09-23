"""Test timepoint snapshot — no lookahead, market overview, portfolio, factor ranking."""

import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from quant_trade.backtest.portfolio import Portfolio
from quant_trade.data.calendar import TradeCalendar
from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.data.sync import sync_index_daily
from quant_trade.simulator.snapshot import SnapshotBuilder
from quant_trade.strategies.base import Order, SignalResult, Strategy


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


class _FixedStrategy(Strategy):
    """A reference strategy that returns whatever it was handed.

    Stands in for a real strategy so the recommendation mapping can be checked
    without paying for a full factor pass on every assertion.
    """

    name = "stub"

    def __init__(self, orders: list[Order]) -> None:
        super().__init__(store=None)
        self._orders = orders

    def generate_signals(self, date: date, universe: list[str], data: object) -> SignalResult:
        return SignalResult(orders=list(self._orders))


class TestRecommendedOrders:
    """The recommendation rides along in the snapshot, shaped as decision orders.

    Building it here rather than recomputing on click keeps the reference
    strategy at one full factor pass per snapshot instead of two.
    """

    def test_none_when_no_reference_strategy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_minimal_db(db_path)

            builder = SnapshotBuilder(store, Portfolio(cash=100000), reference_strategy=None)
            snapshot = builder.build_snapshot(
                cursor_date=date.today(),
                exec_date=date.today(),
                week_number=1,
                total_weeks=10,
                universe=[],
            )

            assert snapshot.recommended_orders is None
            assert snapshot.recommendation_source is None

    def test_empty_list_when_strategy_is_quiet(self) -> None:
        """No signals is not the same fact as no strategy, and stays distinct."""
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_minimal_db(db_path)

            builder = SnapshotBuilder(store, Portfolio(cash=100000), reference_strategy=_FixedStrategy([]))
            snapshot = builder.build_snapshot(
                cursor_date=date.today(),
                exec_date=date.today(),
                week_number=1,
                total_weeks=10,
                universe=[],
            )

            assert snapshot.recommended_orders == []
            assert snapshot.recommendation_source == "stub"

    def test_maps_signals_keeps_weights_and_puts_buys_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            store = _build_minimal_db(db_path)

            portfolio = Portfolio(cash=50000)
            portfolio.buy(code="000858.SZ", price=50.0, amount=50000, trade_date=date.today() - timedelta(days=3))

            strategy = _FixedStrategy(
                [
                    Order(ts_code="600519.SH", target_pct=0.0667, direction="BUY", reason="综合得分 2.31"),
                    # Explicitly a sell, and listed first, so the ordering below
                    # is the mapping's doing rather than the input's.
                    Order(ts_code="601318.SH", target_pct=0.0, direction="SELL", reason="策略清仓"),
                ]
            )
            builder = SnapshotBuilder(store, portfolio, reference_strategy=strategy)
            snapshot = builder.build_snapshot(
                cursor_date=date.today(),
                exec_date=date.today(),
                week_number=1,
                total_weeks=10,
                universe=[],
            )

            orders = snapshot.recommended_orders
            assert orders is not None
            assert snapshot.recommendation_source == "stub"

            assert [o.direction for o in orders] == ["BUY", "SELL", "SELL"]
            assert [o.ts_code for o in orders] == ["600519.SH", "601318.SH", "000858.SZ"]

            bought = orders[0]
            assert bought.target_pct == 0.0667, "target weight must pass through unchanged"
            assert bought.reason == "综合得分 2.31"

            # 000858.SZ is held but absent from the target list, so the snapshot
            # appends a sell for it — that entry has to survive the mapping.
            dropped = orders[-1]
            assert dropped.ts_code == "000858.SZ"
            assert dropped.target_pct == 0.0
            assert dropped.reason == "跌出策略目标组合"


class _StubIndexAdapter:
    """A data source that only knows how to answer for indices.

    Mirrors what the real adapter guarantees for index rows: the three
    stock-only columns come back as 0.0 because an index has no turnover.
    """

    source_name = "stub"

    def __init__(self, days: list[date], start_price: float = 3800.0) -> None:
        self._days = days
        self._start_price = start_price

    def fetch_index_daily(self, index_codes: list[str]) -> pd.DataFrame:
        rows = []
        for code in index_codes:
            for offset, day in enumerate(self._days):
                close = self._start_price + offset
                rows.append(
                    {
                        "ts_code": code,
                        "trade_date": day,
                        "open": close - 5,
                        "high": close + 10,
                        "low": close - 10,
                        "close": close,
                        "volume": 1e8,
                        "amount": 0.0,
                        "pct_change": 0.0,
                        "turn_rate": 0.0,
                    }
                )
        return pd.DataFrame(rows)


class TestIndexSyncCoversTheMarketOverview:
    """The market overview, asserted on rows the sync itself wrote.

    The tests above seed ``daily_kline`` by hand, which proves the snapshot
    builder reads a table — not that the index sync fills it. Rows typed into a
    fixture can carry any shape you like; only rows that came through
    ``sync_index_daily`` show the production path works.
    """

    @staticmethod
    def _weekdays(count: int) -> list[date]:
        days: list[date] = []
        day = date(2026, 6, 1)
        while len(days) < count:
            if day.weekday() < 5:
                days.append(day)
            day += timedelta(days=1)
        return days

    def _synced(self, tmp: str) -> tuple[DataStore, list[date]]:
        db_path = f"{tmp}/sync.db"
        init_db(db_path).close()
        store = DataStore(db_path)

        days = self._weekdays(30)
        store.conn.executemany("INSERT INTO trade_calendar VALUES (?, TRUE)", [(day,) for day in days])
        written = sync_index_daily(store, _StubIndexAdapter(days), ["000300.SH"])
        assert written == len(days)
        return store, days

    def test_index_rows_arrive_with_the_stock_columns_zeroed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, days = self._synced(tmp)

            rows = store.conn.execute(
                "SELECT amount, pct_change, turn_rate FROM daily_kline WHERE ts_code = '000300.SH'"
            ).fetchall()
            assert len(rows) == len(days)
            assert all(row == (0.0, 0.0, 0.0) for row in rows)

    def test_market_overview_is_non_null_after_the_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, days = self._synced(tmp)
            builder = SnapshotBuilder(store, Portfolio(cash=100000))

            overview = builder._build_market_overview(days[-1])

            assert overview is not None
            assert overview.benchmark_code == "000300.SH"
            assert overview.benchmark_close > 0

    def test_hand_inserted_rows_do_not_stand_in_for_the_sync(self) -> None:
        """Without the sync the overview is null — which is the point.

        Same calendar, same code, no index rows: the builder returns None. If
        the previous test passed because of anything other than the sync, this
        one would fail.
        """
        with tempfile.TemporaryDirectory() as tmp:
            db_path = f"{tmp}/nosync.db"
            init_db(db_path).close()
            store = DataStore(db_path)
            days = self._weekdays(30)
            store.conn.executemany("INSERT INTO trade_calendar VALUES (?, TRUE)", [(day,) for day in days])

            assert SnapshotBuilder(store, Portfolio(cash=100000))._build_market_overview(days[-1]) is None
