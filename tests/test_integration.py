"""End-to-end integration test — data → factor → strategy → backtest → report."""

import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

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
            factor = factor_registry.get(fname)
            assert factor is not None, f"Factor {fname} should be registered"
            factor.store = store  # Inject mock DB
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
            factor = factor_registry.get(fname)
            factor.store = store  # Inject mock DB
            vals = factor.compute(latest, universe)
            factor_results[fname] = len(vals)
        assert all(v > 0 for v in factor_results.values()), f"Some factors returned empty: {factor_results}"

        # 2. Strategy
        strategy = strategy_registry.get("factor_ranking")
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
