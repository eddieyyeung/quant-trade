"""Backtest engine — weekly rebalance loop with A-share rules."""

from datetime import date
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from quant_trade.backtest.portfolio import Portfolio
from quant_trade.backtest.rules import (
    detect_suspended,
    get_price_limits,
    is_limit_down,
    is_limit_up,
)
from quant_trade.data.calendar import TradeCalendar
from quant_trade.data.index_weights import get_default_universe
from quant_trade.data.store import DataStore
from quant_trade.strategies.base import Order, Strategy


def run_backtest(
    strategy: Strategy,
    start: date,
    end: date,
    initial_capital: float = 100_000,
    commission_rate: float = 0.00025,
    min_commission: float = 5.0,
    stamp_duty_rate: float = 0.0005,
    transfer_fee_rate: float = 0.00001,
    benchmark_code: str = "000300.SH",
    store: DataStore | None = None,
) -> dict[str, Any]:
    """
    Run a weekly-rebalanced backtest with A-share trading rules.

    Returns a dict with:
        nav_series: pd.Series of daily NAV indexed by trade_date (marked to close)
        benchmark_series: pd.Series of benchmark NAV indexed by trade_date
        trade_log: list of trade dicts
        metrics: dict of performance metrics
        portfolio: final Portfolio state
    """
    ds = store or DataStore()

    # Initialize
    portfolio = Portfolio(cash=initial_capital, initial_capital=initial_capital)
    calendar = TradeCalendar(ds.get_calendar(start, end)["trade_date"].tolist())

    # Get weekly rebalance dates
    weeks = calendar.weeks_between(start, end)
    all_trade_dates = calendar.trade_dates_between(start, end)
    if not weeks or not all_trade_dates:
        logger.error("No trading weeks found in the period")
        return _empty_result(initial_capital)

    # Pre-compute the universe for each rebalance week so daily closing prices
    # can be prefetched once for the whole period.
    weekly_universes: list[list[str]] = []
    all_codes: set[str] = set()
    for signal_date, _ in weeks:
        universe = ds.get_universe(get_default_universe(), signal_date)
        weekly_universes.append(universe)
        all_codes.update(universe)

    if not all_codes:
        logger.error("Empty universe for the whole period")
        return _empty_result(initial_capital)

    # Track NAV and benchmarks
    nav_records: list[dict[str, Any]] = []
    trade_log_all: list[dict[str, Any]] = []
    all_dates_by_idx = {d: i for i, d in enumerate(all_trade_dates)}
    recorded_idx = 0  # exclusive index of the last trade date already recorded

    # Pre-fetch daily closes (for mark-to-market) and benchmark data
    close_map = _load_close_map(ds, sorted(all_codes), start, end)
    benchmark_nav = _fetch_benchmark(ds, benchmark_code, start, end)

    for (signal_date, exec_date), universe in zip(weeks, weekly_universes, strict=False):
        if not universe:
            continue

        # 1. Generate signals on Friday close
        try:
            signals = strategy.generate_signals(signal_date, universe, ds)
        except Exception as e:
            logger.error(f"Strategy failed on {signal_date}: {e}")
            continue

        # 2. Mark to market and record NAV for every trading day up to the
        #    signal date using each day's close. No look-ahead: Monday's
        #    execution prices are not used for Friday's snapshot.
        sig_idx = all_dates_by_idx.get(signal_date)
        record_to = (sig_idx + 1) if sig_idx is not None else recorded_idx
        _record_nav(nav_records, portfolio, all_trade_dates[recorded_idx:record_to], close_map)
        recorded_idx = max(recorded_idx, record_to)

        if not signals.orders:
            continue

        # Skip execution when the trade date falls outside the backtest period
        if exec_date > end:
            continue

        # 3. Get execution prices (Monday open)
        exec_prices = _get_opening_prices(ds, universe, exec_date)

        # 4. Get price limits and suspensions
        limits = get_price_limits(ds, universe, signal_date)
        suspended = detect_suspended(ds, universe, exec_date)

        # 5. Build sell orders: drop names no longer targeted and trim
        #    overweight positions back to their target weight.
        target_weights = {o.ts_code: o.target_pct for o in signals.orders if o.direction == "BUY"}
        all_orders = list(signals.orders)
        total_value_before = portfolio.total_value
        for code, h in list(portfolio.holdings.items()):
            price = exec_prices.get(code)
            if price is None or price <= 0:
                continue
            target_pct = target_weights.get(code)
            if target_pct is None:
                all_orders.append(Order(ts_code=code, target_pct=0.0, direction="SELL", reason="跌出目标组合"))
            else:
                current_value = h.shares * price
                target_value = target_pct * total_value_before
                if current_value > target_value * 1.01:
                    target_shares = int(target_value / price / 100) * 100
                    excess = h.shares - target_shares
                    if excess >= 100:
                        all_orders.append(
                            Order(ts_code=code, target_pct=target_pct, direction="SELL", reason="调仓降低超配仓位")
                        )

        # 6. Execute sell orders first (to free up cash)
        prev_log_len = len(portfolio.trade_log)
        for order in all_orders:
            if order.direction != "SELL":
                continue
            code = order.ts_code
            if code in suspended:
                logger.info(f"{code} suspended on {exec_date}, skip sell")
                continue
            price = exec_prices.get(code)
            if price is None or price <= 0:
                continue
            # Check limit down
            if code in limits:
                ld, lu = limits[code]
                if is_limit_down(price, ld):
                    logger.info(f"{code} limit down at {price}, skip sell")
                    continue
            # T+1 check
            if not portfolio.can_sell(code, exec_date):
                logger.info(f"{code} T+1 restriction, skip sell")
                continue

            shares, proceeds = portfolio.sell(
                code,
                price,
                trade_date=exec_date,
                commission_rate=commission_rate,
                min_commission=min_commission,
                stamp_duty_rate=stamp_duty_rate,
                transfer_fee_rate=transfer_fee_rate,
            )
            if shares > 0:
                logger.debug(f"Sold {shares} {code} @ {price}")

        # 7. Execute buy orders
        # Calculate target allocation
        total_value = portfolio.total_value
        buy_orders = [o for o in signals.orders if o.direction == "BUY"]

        # Compute how much cash to allocate per buy order
        for order in buy_orders:
            code = order.ts_code
            if code in suspended:
                logger.info(f"{code} suspended on {exec_date}, skip buy")
                continue
            price = exec_prices.get(code)
            if price is None or price <= 0:
                continue
            if code in limits:
                ld, lu = limits[code]
                if is_limit_up(price, lu):
                    logger.info(f"{code} limit up at {price}, skip buy")
                    continue

            # Target allocation: order.target_pct of NAV
            target_amount = total_value * order.target_pct
            # Check current holding
            current_value = 0.0
            if code in portfolio.holdings:
                h = portfolio.holdings[code]
                current_value = h.shares * price

            if current_value >= target_amount:
                continue  # Already at target

            buy_amount = min(target_amount - current_value, portfolio.cash * 0.3)  # Cap single position

            shares, cost = portfolio.buy(
                code,
                price,
                buy_amount,
                exec_date,
                commission_rate=commission_rate,
                min_commission=min_commission,
                transfer_fee_rate=transfer_fee_rate,
            )
            if shares > 0:
                logger.debug(f"Bought {shares} {code} @ {price}")

        # 8. Record the execution-date NAV after trades, marked at Monday's close
        _record_nav(nav_records, portfolio, [exec_date], close_map)
        exec_idx = all_dates_by_idx.get(exec_date)
        if exec_idx is not None:
            recorded_idx = max(recorded_idx, exec_idx + 1)

        # Only append the trades newly added this week (trade_log is cumulative)
        trade_log_all.extend(portfolio.trade_log[prev_log_len:])

    # Record remaining NAV
    _record_nav(nav_records, portfolio, all_trade_dates[recorded_idx:], close_map)

    # Build NAV series
    nav_df = pd.DataFrame(nav_records).drop_duplicates(subset=["trade_date"]).set_index("trade_date").sort_index()
    nav_series = nav_df["nav"]

    # Compute metrics
    metrics = compute_metrics(nav_series, benchmark_nav, initial_capital)
    metrics["total_trades"] = len(trade_log_all)

    return {
        "nav_series": nav_series,
        "benchmark_series": benchmark_nav,
        "trade_log": trade_log_all,
        "metrics": metrics,
        "portfolio": portfolio,
    }


def compute_metrics(
    nav: pd.Series,
    benchmark: pd.Series | None = None,
    initial_capital: float = 100_000,
    risk_free_rate: float = 0.03,
) -> dict[str, Any]:
    """Compute performance metrics from a NAV series."""
    if nav.empty:
        return {}

    # Ensure DatetimeIndex for resample
    if not isinstance(nav.index, pd.DatetimeIndex):
        nav = nav.copy()
        nav.index = pd.to_datetime(nav.index)

    returns = nav.pct_change().dropna()
    if returns.empty:
        return {}

    total_return = (nav.iloc[-1] / nav.iloc[0]) - 1

    # Annualize from the elapsed calendar span rather than the observation
    # count, so weekly or irregular NAV series are annualized correctly.
    if len(nav) >= 2:
        span_days = (nav.index[-1] - nav.index[0]).days
        years = max(span_days / 365.25, 1e-9)
        gaps = nav.index.to_series().diff().dt.days.dropna()
        median_gap = float(gaps.median()) if len(gaps) else 7.0
        periods_per_year = 252.0 if median_gap <= 2.0 else 52.0
    else:
        years = 0.0
        periods_per_year = 252.0

    # Annualized return
    annual_return = ((1 + total_return) ** (1 / years) - 1) if years > 0 else 0.0

    # Volatility
    annual_vol = returns.std() * np.sqrt(periods_per_year) if returns.std() > 0 else 0.0

    # Sharpe ratio
    sharpe = ((annual_return - risk_free_rate) / annual_vol) if annual_vol > 0 else 0.0

    # Max drawdown
    cummax = nav.cummax()
    drawdown = (nav - cummax) / cummax
    max_drawdown = drawdown.min()

    # Calmar ratio
    calmar = annual_return / abs(max_drawdown) if max_drawdown and max_drawdown != 0 else 0.0

    # Weekly win rate
    weekly_returns = nav.resample("W").last().pct_change().dropna()
    win_rate = (weekly_returns > 0).mean() if len(weekly_returns) > 0 else 0.0

    # Benchmark comparison
    benchmark_return = 0.0
    excess_return = 0.0
    if benchmark is not None and not benchmark.empty:
        bench_total = (benchmark.iloc[-1] / benchmark.iloc[0]) - 1
        bench_annual = ((1 + bench_total) ** (1 / years) - 1) if years > 0 else 0.0
        benchmark_return = bench_total
        excess_return = annual_return - bench_annual

    # Turnover (approximate from weekly weight changes)
    turnover = 0.0  # Will be populated by the engine if tracking

    return {
        "total_return": round(total_return, 4),
        "annual_return": round(annual_return, 4),
        "annual_volatility": round(annual_vol, 4),
        "sharpe_ratio": round(sharpe, 4),
        "max_drawdown": round(max_drawdown, 4),
        "calmar_ratio": round(calmar, 4),
        "win_rate": round(float(win_rate), 4),
        "benchmark_return": round(benchmark_return, 4),
        "excess_return": round(excess_return, 4),
        "total_trades": 0,  # populated below
        "turnover": round(turnover, 4),
    }


def generate_signals_for_today(
    strategy: Strategy,
    store: DataStore | None = None,
) -> dict[str, Any]:
    """
    Generate signals for the current week (paper trading mode).
    Uses Friday's close if today is after Friday, or the last Friday's close.
    """
    ds = store or DataStore()

    # Find the most recent trading day
    today = date.today()
    latest_trade_date = ds.get_latest_trade_date(today)
    if latest_trade_date is None:
        return {"error": "No trade date data available"}

    calendar = TradeCalendar(ds.get_calendar(latest_trade_date, latest_trade_date)["trade_date"].tolist())
    signal_day = calendar.last_trade_date_of_week(latest_trade_date) or latest_trade_date

    universe = ds.get_universe(get_default_universe(), signal_day)

    signals = strategy.generate_signals(signal_day, universe, ds)

    return {
        "signal_date": str(signal_day),
        "orders": [
            {
                "ts_code": o.ts_code,
                "target_pct": o.target_pct,
                "direction": o.direction,
                "reason": o.reason,
            }
            for o in signals.orders
        ],
        "weights": signals.weights,
        "universe_size": len(universe),
    }


# ---------- internal helpers ----------


def _fetch_benchmark(
    store: DataStore,
    benchmark_code: str,
    start: date,
    end: date,
) -> pd.Series | None:
    """Fetch benchmark price series, normalized to start at 1.0."""
    df = store.get_daily([benchmark_code], start, end, fields=["trade_date", "close"])
    if df.empty:
        return None
    df = df.sort_values("trade_date")
    df["nav"] = df["close"] / df["close"].iloc[0]
    return df.set_index("trade_date")["nav"]


def _get_opening_prices(
    store: DataStore,
    codes: list[str],
    trade_date: date,
) -> dict[str, float]:
    """Get opening prices for a list of stocks on a given date."""
    if not codes:
        return {}
    placeholders = ", ".join(["?"] * len(codes))
    sql = f"""
        SELECT ts_code, open
        FROM daily_kline
        WHERE ts_code IN ({placeholders}) AND trade_date = ?
    """
    try:
        df = store.conn.execute(sql, list(codes) + [trade_date]).df()
        return dict(zip(df["ts_code"], df["open"], strict=False))
    except Exception:
        return {}


def _load_close_map(
    store: DataStore,
    codes: list[str],
    start: date,
    end: date,
) -> dict[date, dict[str, float]]:
    """Load daily closing prices keyed by trade_date -> {ts_code: close}."""
    close_map: dict[date, dict[str, float]] = {}
    if not codes:
        return close_map
    df = store.get_daily(codes, start, end, fields=["trade_date", "ts_code", "close"])
    for row in df.itertuples(index=False):
        d: date
        td = row.trade_date
        d = td.date() if isinstance(td, pd.Timestamp) else td  # type: ignore[assignment]  # itertuples yields Any; runtime is date
        close_map.setdefault(d, {})[str(row.ts_code)] = float(row.close)  # type: ignore[arg-type]
    return close_map


def _record_nav(
    records: list[dict[str, Any]],
    portfolio: Portfolio,
    trade_dates: list[date],
    close_map: dict[date, dict[str, float]],
) -> None:
    """Record one NAV point per trade date, marking holdings at that day's close."""
    for d in trade_dates:
        prices = close_map.get(d)
        if prices:
            portfolio.update_prices(prices)
        records.append(
            {
                "trade_date": d,
                "nav": portfolio.total_value,
                "cash": portfolio.cash,
                "holdings_count": len(portfolio.holdings),
            }
        )


def _empty_result(initial_capital: float) -> dict[str, Any]:
    return {
        "nav_series": pd.Series(),
        "benchmark_series": None,
        "trade_log": [],
        "metrics": {},
        "portfolio": Portfolio(cash=initial_capital, initial_capital=initial_capital),
    }
