"""End-to-end integration test — data → factor → strategy → backtest → report."""

import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_trade.backtest.engine import run_backtest
from quant_trade.backtest.portfolio import Portfolio
from quant_trade.config import AppConfig
from quant_trade.data.calendar import TradeCalendar
from quant_trade.data.index_weights import get_default_universe
from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.factors.registry import registry as factor_registry
from quant_trade.signals.reporter import generate_weekly_report
from quant_trade.strategies.registry import strategy_registry


def _build_mock_db(db_path: str, n_stocks: int = 50, n_days: int = 500) -> DataStore:
    """Build a DuckDB database with realistic mock data."""
    # DuckDB can't connect to an existing empty file — use a fresh path in a temp dir
    Path(db_path).unlink(missing_ok=True)
    conn = init_db(db_path)

    today = date.today()
    start = today - timedelta(days=n_days * 2)

    # Generate trade dates (weekdays only)
    trade_dates: list[date] = []
    d = start
    while len(trade_dates) < n_days:
        if d.weekday() < 5:
            trade_dates.append(d)
        d += timedelta(days=1)

    # Mock stock basic
    stock_rows: list[tuple] = []
    for i in range(n_stocks):
        code = f"{600000 + i:06d}.SH" if i < n_stocks // 2 else f"{300000 - n_stocks // 2 + i:06d}.SZ"
        stock_rows.append((code, f"股票{i}", "制造", "main", start, False))
    conn.executemany(
        "INSERT INTO stock_basic VALUES (?, ?, ?, ?, ?, ?)",
        stock_rows,
    )

    # Mock trade calendar
    cal_rows: list[tuple] = [(td, True) for td in trade_dates]
    conn.executemany("INSERT INTO trade_calendar VALUES (?, ?)", cal_rows)

    # Mock daily kline: random walk with drift
    np.random.seed(42)
    kline_rows: list[tuple] = []
    for code, *_ in stock_rows:
        price = np.random.uniform(5, 50)
        for td in trade_dates:
            ret = np.random.normal(0.0005, 0.02)  # slight positive drift
            price *= 1 + ret
            high = price * np.random.uniform(1.0, 1.03)
            low = price * np.random.uniform(0.97, 1.0)
            vol = np.random.uniform(1e6, 1e8)
            amt = vol * price
            pct = np.random.normal(0, 2.0)
            tr = np.random.uniform(0.5, 5.0)
            kline_rows.append((code, td, price / 1.01, high, low, price, vol, amt, pct, tr))
    conn.executemany(
        "INSERT INTO daily_kline VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        kline_rows,
    )

    # Mock financials
    fin_rows: list[tuple] = []
    for code, *_ in stock_rows:
        for quarter in range(8):
            end_d = today - timedelta(days=90 * quarter)
            ann_d = end_d + timedelta(days=30)
            fin_rows.append(
                (
                    code,
                    end_d,
                    ann_d,
                    np.random.uniform(5, 30),  # pe
                    np.random.uniform(0.5, 5),  # pb
                    np.random.uniform(5, 25),  # roe
                    np.random.uniform(-20, 50),  # revenue_yoy
                    np.random.uniform(-30, 60),  # profit_yoy
                    np.random.uniform(0, 5),  # dividend_yield
                )
            )
    conn.executemany(
        "INSERT INTO financials VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        fin_rows,
    )

    # Mock index weights
    idx_rows: list[tuple] = []
    for code, *_ in stock_rows:
        idx_rows.append(("000300.SH", code, 1.0 / n_stocks, start, None))
        idx_rows.append(("000905.SH", code, 1.0 / n_stocks, start, None))
    conn.executemany(
        "INSERT INTO index_weights VALUES (?, ?, ?, ?, ?)",
        idx_rows,
    )

    conn.close()
    return DataStore(db_path)


def test_data_to_factor_pipeline():
    """Test: mock DB → DataStore queries → factor computation."""
    tmpdir = tempfile.mkdtemp()
    db_path = str(Path(tmpdir) / "test.db")

    try:
        store = _build_mock_db(db_path, n_stocks=20, n_days=100)
        today = date.today()
        universe = store.get_universe(get_default_universe(), today)
        assert len(universe) > 0, "Universe should not be empty"

        # Test DataStore queries (query a date range inside mock data)
        mid_date = today - timedelta(days=60)
        daily = store.get_daily(["600000.SH"], mid_date - timedelta(days=30), mid_date)
        assert not daily.empty, "Daily kline should return data"

        fin = store.get_financials(["600000.SH"], today)
        assert not fin.empty, "Financials should return data"

        cal = store.get_calendar(mid_date - timedelta(days=30), mid_date)
        assert not cal.empty, "Calendar should return dates"

        # Test factor computation
        latest = store.get_latest_trade_date()
        assert latest is not None

        for fname in ["momentum_20d", "pb_ratio", "roe_ttm"]:
            # Through the registry, not by assigning `.store` afterwards: the
            # attribute poke was what the store-injection change removed, and
            # leaving it here would re-normalise the pattern.
            factor = factor_registry.get(fname, store=store)
            assert factor is not None, f"Factor {fname} should be registered"
            vals = factor.compute(latest, universe)
            assert isinstance(vals, pd.Series), f"Factor {fname} should return Series"
            assert len(vals) > 0, f"Factor {fname} returned empty Series"
    finally:
        shutil.rmtree(tmpdir)


def test_strategy_to_backtest_pipeline():
    """Test: factor → strategy signals → backtest → metrics."""
    tmpdir = tempfile.mkdtemp()
    db_path = str(Path(tmpdir) / "test.db")

    try:
        store = _build_mock_db(db_path, n_stocks=30, n_days=180)
        # Use a start date within the mock data range (~120 cal days from end)
        start_data = store.get_calendar(date.today() - timedelta(days=140), date.today())
        start = start_data["trade_date"].iloc[min(10, len(start_data) - 1)]
        if hasattr(start, "date"):
            start = start.date()

        calendar = TradeCalendar(store.get_calendar(start, date.today())["trade_date"].tolist())
        signal_date = calendar.last_trade_date_of_week(date.today() - timedelta(days=14))
        assert signal_date is not None
        universe = store.get_universe(get_default_universe(), signal_date)

        # Strategy
        strategy = strategy_registry.get("factor_ranking")
        assert strategy is not None
        strategy.top_n = 10
        signals = strategy.generate_signals(signal_date, universe, store)
        assert len(signals.orders) > 0, "Should generate buy orders"
        for o in signals.orders:
            assert o.ts_code != ""
            assert o.target_pct > 0
            assert o.direction in ("BUY", "SELL")

        # Backtest
        result = run_backtest(
            strategy=strategy,
            start=start,
            end=date.today(),
            initial_capital=100_000,
            store=store,
        )
        assert "metrics" in result
        metrics = result["metrics"]
        assert "total_return" in metrics
        assert "sharpe_ratio" in metrics
        assert "max_drawdown" in metrics
        assert isinstance(metrics["total_return"], float)

        # NAV series
        nav = result.get("nav_series")
        assert nav is not None and not nav.empty, "NAV series should not be empty"

        # Portfolio
        portfolio = result.get("portfolio")
        assert portfolio is not None
        assert portfolio.initial_capital == 100_000
    finally:
        shutil.rmtree(tmpdir)


def _weekend_before(d: date) -> date:
    """Step back from ``d`` until landing on a Saturday or Sunday."""
    while d.weekday() < 5:
        d -= timedelta(days=1)
    return d


def _mock_db_with_start(tmpdir: str) -> tuple[DataStore, date]:
    """Build a mock database and return it with a trade date inside its range."""
    store = _build_mock_db(str(Path(tmpdir) / "test.db"), n_stocks=20, n_days=180)
    dates = store.get_calendar(date.today() - timedelta(days=140), date.today())["trade_date"]
    start = dates.iloc[min(10, len(dates) - 1)]
    if hasattr(start, "date"):
        start = start.date()
    return store, start


def test_backtest_starting_on_non_trading_day():
    """Regression: a start date on a weekend must not yield an empty backtest.

    The calendar is loaded from ``start``, so it never holds an earlier date. An
    un-normalised start therefore left the week scan without an anchor, and it
    silently returned zero weeks — and therefore no metrics and no NAV at all.
    """
    tmpdir = tempfile.mkdtemp()
    try:
        store, trade_date = _mock_db_with_start(tmpdir)
        weekend_start = _weekend_before(trade_date)
        assert weekend_start.weekday() >= 5, "fixture must start on a weekend"

        strategy = strategy_registry.get("factor_ranking")
        assert strategy is not None
        strategy.top_n = 5

        result = run_backtest(
            strategy=strategy,
            start=weekend_start,
            end=date.today(),
            initial_capital=100_000,
            store=store,
        )

        assert result["metrics"], "weekend start must still produce metrics"
        total_return = result["metrics"]["total_return"]
        assert total_return == total_return, "total_return must not be NaN"

        nav = result["nav_series"]
        assert not nav.empty, "NAV series must not be empty for a weekend start"
        assert nav.index[0] >= weekend_start
    finally:
        shutil.rmtree(tmpdir)


def test_backtest_starting_on_trading_day_unchanged():
    """A start date that is already a trade date must be used as-is."""
    tmpdir = tempfile.mkdtemp()
    try:
        store, trade_date = _mock_db_with_start(tmpdir)

        strategy = strategy_registry.get("factor_ranking")
        assert strategy is not None
        strategy.top_n = 5

        result = run_backtest(
            strategy=strategy,
            start=trade_date,
            end=date.today(),
            initial_capital=100_000,
            store=store,
        )

        nav = result["nav_series"]
        assert not nav.empty
        assert nav.index[0] == trade_date, "a trading-day start must not be shifted"
    finally:
        shutil.rmtree(tmpdir)


def test_backtest_window_without_trade_dates():
    """A window with no trading days returns an empty result rather than raising."""
    tmpdir = tempfile.mkdtemp()
    try:
        store, _ = _mock_db_with_start(tmpdir)

        strategy = strategy_registry.get("factor_ranking")
        assert strategy is not None

        future = date.today() + timedelta(days=10)
        result = run_backtest(
            strategy=strategy,
            start=future,
            end=future + timedelta(days=10),
            initial_capital=100_000,
            store=store,
        )

        assert result["metrics"] == {}
        assert result["nav_series"].empty
    finally:
        shutil.rmtree(tmpdir)


HOLIDAYS = [date(2025, 1, 1), date(2025, 10, 1)]


@pytest.mark.parametrize("holiday", HOLIDAYS)
def test_backtest_starting_on_a_holiday(holiday: date) -> None:
    """A holiday start normalises to the next trade date instead of yielding nothing.

    The mock calendar is weekdays-only, so the holiday is carved out of it to
    reproduce a real market closure.
    """
    tmpdir = tempfile.mkdtemp()
    try:
        store, _ = _mock_db_with_start(tmpdir)
        store.conn.execute("DELETE FROM trade_calendar WHERE trade_date = ?", [holiday])

        strategy = strategy_registry.get("factor_ranking")
        assert strategy is not None
        strategy.top_n = 5

        calendar = TradeCalendar(store.get_calendar(holiday, date.today())["trade_date"].tolist())
        expected_start = calendar.next_trade_date(holiday)
        assert expected_start is not None and expected_start != holiday

        result = run_backtest(
            strategy=strategy,
            start=holiday,
            end=date.today(),
            initial_capital=100_000,
            store=store,
        )

        assert result["metrics"], "a holiday start must still produce metrics"
        nav = result["nav_series"]
        assert not nav.empty
        assert nav.index[0] == expected_start
    finally:
        shutil.rmtree(tmpdir)


def test_default_config_start_produces_a_valid_backtest() -> None:
    """The shipped default start (2015-01-01, a holiday) must yield a real backtest.

    The default being a non-trading day is exactly what made every default-
    configured run silently empty, so it is worth pinning.
    """
    from quant_trade.services import RunContext
    from quant_trade.services.backtest import BacktestParams, run_backtest_service

    tmpdir = tempfile.mkdtemp()
    try:
        store, _ = _mock_db_with_start(tmpdir)
        config = AppConfig()
        config.data.db_path = store.db_path
        config.backtest.start_date = date(2025, 1, 1)
        store.conn.execute("DELETE FROM trade_calendar WHERE trade_date = ?", [config.backtest.start_date])

        params = BacktestParams.from_config(config)
        assert params.start == config.backtest.start_date, "start must come from config"

        ctx = RunContext(run_id="default", config=config, store=store)
        result = run_backtest_service(params, ctx)

        assert result.metrics, "the default config must produce metrics"
        assert result.nav, "the default config must produce a NAV series"
        assert result.nav[0].date > config.backtest.start_date
    finally:
        shutil.rmtree(tmpdir)


def test_backtest_uses_the_same_start_rule_as_the_simulator() -> None:
    """Both normalise a non-trading-day start to the same day.

    Simulator computes ``calendar.next_trade_date(start)``; the backtest must
    land on that same date, or the two disagree about which week a run begins
    in. Pins the two together so changing one side alone breaks the test.
    """
    tmpdir = tempfile.mkdtemp()
    try:
        store, trade_date = _mock_db_with_start(tmpdir)
        weekend_start = _weekend_before(trade_date)

        calendar = TradeCalendar(store.get_calendar(weekend_start, date.today())["trade_date"].tolist())
        simulator_start = calendar.next_trade_date(weekend_start)  # Simulator's rule
        assert simulator_start is not None

        strategy = strategy_registry.get("factor_ranking")
        assert strategy is not None
        strategy.top_n = 5

        result = run_backtest(
            strategy=strategy,
            start=weekend_start,
            end=date.today(),
            initial_capital=100_000,
            store=store,
        )

        assert result["nav_series"].index[0] == simulator_start
    finally:
        shutil.rmtree(tmpdir)


def test_report_generation():
    """Test: backtest result → HTML report generation."""
    config = AppConfig()

    result = {
        "nav_series": pd.Series(
            [1.0, 1.01, 1.02, 1.015, 1.03, 1.04],
            index=pd.date_range(date.today() - timedelta(days=30), periods=6, freq="W"),
        ),
        "benchmark_series": pd.Series(
            [1.0, 1.005, 1.01, 1.008, 1.015, 1.02],
            index=pd.date_range(date.today() - timedelta(days=30), periods=6, freq="W"),
        ),
        "metrics": {
            "total_return": 0.04,
            "annual_return": 0.15,
            "sharpe_ratio": 1.2,
            "max_drawdown": -0.05,
            "win_rate": 0.55,
            "excess_return": 0.03,
        },
        "signal_date": date.today().strftime("%Y-%m-%d"),
    }

    signals = [
        {"ts_code": "600519.SH", "name": "贵州茅台", "target_pct": 0.067, "direction": "BUY", "reason": "综合得分 92"},
        {"ts_code": "000858.SZ", "name": "五粮液", "target_pct": 0.0, "direction": "SELL", "reason": "得分跌出 Top-15"},
    ]

    html = generate_weekly_report(result, signals, config)
    assert len(html) > 0
    assert "量化周报" in html
    assert "600519.SH" in html
    assert "000858.SZ" in html
    assert "92" in html


def test_portfolio_save_load():
    """Test: Portfolio JSON persistence."""
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = Path(f.name)

    try:
        p = Portfolio(cash=100_000, initial_capital=100_000)
        p.buy("600519.SH", 15.0, 30000.0, date.today())  # 2000 shares @ ¥15
        p.update_prices({"600519.SH": 16.0})

        p.save(path)
        assert path.exists()

        loaded = Portfolio.load(path)
        assert loaded is not None
        assert loaded.cash < 100_000
        assert "600519.SH" in loaded.holdings
        assert loaded.holdings["600519.SH"].shares > 0
    finally:
        path.unlink(missing_ok=True)


def test_full_pipeline():
    """End-to-end: data → factor → strategy → backtest → report with mock data."""
    tmpdir = tempfile.mkdtemp()
    db_path = str(Path(tmpdir) / "test.db")

    try:
        store = _build_mock_db(db_path, n_stocks=20, n_days=150)

        latest = store.get_latest_trade_date()
        universe = store.get_universe(get_default_universe(), latest)
        assert len(universe) > 0

        # Find backtest date range within mock data
        cal_dates = store.get_calendar(date.today() - timedelta(days=365), date.today())["trade_date"].tolist()
        bt_start = cal_dates[30].date() if hasattr(cal_dates[30], "date") else cal_dates[30]
        bt_end = cal_dates[-1].date() if hasattr(cal_dates[-1], "date") else cal_dates[-1]

        # 1. Factors
        factor_results: dict[str, int] = {}
        for fname in ["momentum_20d", "momentum_60d", "pb_ratio", "roe_ttm"]:
            factor = factor_registry.get(fname, store=store)
            assert factor.store is store, f"Factor {fname} ignored the injected store"
            vals = factor.compute(latest, universe)
            factor_results[fname] = len(vals)
        assert all(v > 0 for v in factor_results.values()), f"Some factors returned empty: {factor_results}"

        # 2. Strategy
        strategy = strategy_registry.get("factor_ranking", store=store)
        strategy.top_n = 8
        signals = strategy.generate_signals(latest, universe, store)
        assert len(signals.orders) > 0

        # 3. Backtest
        result = run_backtest(
            strategy=strategy,
            start=bt_start,
            end=bt_end,
            initial_capital=100_000,
            store=store,
        )
        assert result["nav_series"] is not None
        metrics = result["metrics"]
        assert abs(metrics["total_return"]) < 10  # sanity: no 1000x returns in mock data

        # 4. Report
        config = AppConfig()
        signal_dicts = [
            {
                "ts_code": o.ts_code,
                "name": o.ts_code,
                "target_pct": o.target_pct,
                "direction": o.direction,
                "reason": o.reason,
            }
            for o in signals.orders
        ]
        html = generate_weekly_report(result, signal_dicts, config, portfolio=result.get("portfolio"))
        assert len(html) > 1000  # should be a substantial HTML file
        assert "净值曲线" in html or "策略" in html
    finally:
        shutil.rmtree(tmpdir)
