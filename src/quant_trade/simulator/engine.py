"""Simulator engine — interactive weekly rebalance loop."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
from loguru import logger

from quant_trade.backtest.portfolio import Portfolio
from quant_trade.backtest.rules import detect_suspended, get_price_limits, is_limit_down, is_limit_up
from quant_trade.data.calendar import TradeCalendar
from quant_trade.data.index_weights import get_default_universe
from quant_trade.data.store import DataStore
from quant_trade.simulator.session import SessionStore
from quant_trade.simulator.snapshot import SnapshotBuilder
from quant_trade.simulator.types import (
    Decision,
    ExecutedOrder,
    OrderRequest,
    StepResult,
    StrategySignalItem,
)
from quant_trade.strategies.base import Strategy
from quant_trade.strategies.registry import strategy_registry


class Simulator:
    """Interactive historical trading simulator."""

    def __init__(
        self,
        store: DataStore | None = None,
        db_path: str | None = None,
        data_dir: str = "data",
    ) -> None:
        """Hold the caller's store, or resolve the configured database.

        ``db_path`` used to default to a literal ``"data/quant.db"``, which made
        a store-less ``Simulator()`` open the repository's own database no
        matter what the process was configured to use. ``None`` now means "ask
        the config", the same rule every other component follows.
        """
        self._store = store or DataStore(db_path)
        self._sessions = SessionStore(self._store.conn, data_dir)

    # ---- Session lifecycle ----

    def create(
        self,
        name: str,
        start_date: date,
        end_date: date | None = None,
        initial_capital: float = 100_000,
        reference_strategy: str | None = None,
    ) -> dict[str, Any]:
        """Create a new simulation session."""
        logger.info(
            "Simulator.create: name={}, start={}, end={}, capital={}",
            name,
            start_date,
            end_date or "latest",
            initial_capital,
        )

        # Resolve end_date
        if end_date is None:
            logger.info("Resolving latest trade date...")
            latest = self._store.get_latest_trade_date(date.today())
            end_date = latest if latest else start_date
            logger.info("End date resolved: {}", end_date)

        # Build calendar and find first Friday
        logger.info("Fetching calendar data: {} → {}", start_date, end_date)
        cal_df = self._store.get_calendar(start_date, end_date)
        logger.info("Calendar data fetched: {} trade dates", len(cal_df))
        if cal_df.empty:
            raise ValueError(f"No trading data between {start_date} and {end_date}")
        calendar = TradeCalendar(cal_df["trade_date"].tolist())
        # Ensure start_date is not before the first available trade date
        actual_start = calendar.next_trade_date(start_date)
        if actual_start is None:
            raise ValueError(f"No trade dates found from {start_date}")
        logger.info("Computing week schedule...")
        weeks = calendar.weeks_between(actual_start, end_date)
        if not weeks:
            raise ValueError(f"No trading weeks found between {actual_start} and {end_date}")

        first_friday = weeks[0][0]
        logger.info("Calendar ready: {} weeks, first_friday={}", len(weeks), first_friday)

        # Initialize portfolio
        portfolio = Portfolio(cash=initial_capital, initial_capital=initial_capital)
        portfolio_json = json.dumps(portfolio.to_dict(), ensure_ascii=False, default=str)

        # Get reference strategy instance
        ref_strategy: Strategy | None = None
        if reference_strategy:
            ref_strategy = strategy_registry.get(reference_strategy, store=self._store)
            if ref_strategy is None:
                logger.warning(f"Reference strategy '{reference_strategy}' not found, running without reference")
            else:
                logger.info("Reference strategy loaded: {}", reference_strategy)

        session_id = self._sessions.create(
            name=name,
            start_date=start_date,
            end_date=end_date,
            initial_capital=initial_capital,
            cursor_date=first_friday,
            portfolio_json=portfolio_json,
            reference_strategy=reference_strategy,
        )

        # Build and return initial snapshot
        logger.info("Building initial snapshot...")
        universe = self._store.get_universe(get_default_universe(), first_friday)
        logger.info("Universe ready: {} stocks", len(universe))
        snapshot_builder = SnapshotBuilder(self._store, portfolio, ref_strategy)
        first_exec_date = weeks[0][1]
        snapshot = snapshot_builder.build_snapshot(
            cursor_date=first_friday,
            exec_date=first_exec_date,
            week_number=1,
            total_weeks=len(weeks),
            universe=universe,
            skip_heavy=True,
        )

        return {
            "session_id": session_id,
            "cursor_date": str(first_friday),
            "total_weeks": len(weeks),
            "reference_strategy": reference_strategy,
            "snapshot": snapshot,
        }

    def resume(self, session_id: str) -> dict[str, Any]:
        """Resume a paused or active session."""
        row = self._sessions.load(session_id)
        if row is None:
            raise ValueError(f"Session {session_id} not found")

        cursor_date = _to_date(row["cursor_date"])
        start_date = _to_date(row["start_date"])
        end_date = _to_date(row["end_date"]) if row["end_date"] else None

        cal_df = self._store.get_calendar(start_date, end_date or date.today())
        calendar = TradeCalendar(cal_df["trade_date"].tolist())
        actual_start = calendar.next_trade_date(start_date) or start_date
        weeks = calendar.weeks_between(actual_start, end_date or date.today())
        next_week_idx = _find_week_index(weeks, cursor_date)
        total_weeks = len(weeks)

        # Rebuild portfolio from JSON
        portfolio = Portfolio.from_dict(json.loads(row["portfolio_json"]))

        # Rebuild reference strategy
        ref_strategy: Strategy | None = None
        ref_name = row.get("reference_strategy")
        if ref_name:
            ref_strategy = strategy_registry.get(str(ref_name), store=self._store)

        universe = self._store.get_universe(get_default_universe(), cursor_date)
        exec_date = weeks[next_week_idx][1] if next_week_idx < len(weeks) else cursor_date
        snapshot_builder = SnapshotBuilder(self._store, portfolio, ref_strategy)
        snapshot = snapshot_builder.build_snapshot(
            cursor_date=cursor_date,
            exec_date=exec_date,
            week_number=next_week_idx + 1,
            total_weeks=total_weeks,
            universe=universe,
        )

        prior = self._sessions.load_decisions(session_id)

        return {
            "session_id": session_id,
            "cursor_date": str(cursor_date),
            "week_number": next_week_idx + 1,
            "total_weeks": total_weeks,
            "portfolio_value": portfolio.total_value,
            "previous_decisions": len(prior),
            "reference_strategy": ref_name,
            "snapshot": snapshot,
        }

    # ---- Operations ----

    def step(self, session_id: str, orders: list[OrderRequest], notes: str = "") -> StepResult:
        """Execute user orders and advance to the next Friday."""
        row = self._sessions.load(session_id)
        if row is None:
            raise ValueError(f"Session {session_id} not found")
        if row["status"] == "completed":
            raise ValueError("Session is already completed")

        cursor_date = _to_date(row["cursor_date"])
        start_date = _to_date(row["start_date"])
        end_date = _to_date(row["end_date"]) if row["end_date"] else None

        cal_df = self._store.get_calendar(start_date, end_date or date.today())
        calendar = TradeCalendar(cal_df["trade_date"].tolist())
        actual_start = calendar.next_trade_date(start_date) or start_date
        weeks = calendar.weeks_between(actual_start, end_date or date.today())
        week_idx = _find_week_index(weeks, cursor_date)
        if week_idx >= len(weeks):
            raise ValueError("Cursor is past the last week")

        signal_date, exec_date = weeks[week_idx]
        portfolio = Portfolio.from_dict(json.loads(row["portfolio_json"]))
        universe = self._store.get_universe(get_default_universe(), cursor_date)

        ref_strategy: Strategy | None = None
        ref_name = row.get("reference_strategy")
        if ref_name:
            ref_strategy = strategy_registry.get(str(ref_name), store=self._store)

        # Build pre-decision snapshot
        snapshot_builder = SnapshotBuilder(self._store, portfolio, ref_strategy)
        prior_decisions = self._sessions.load_decisions(session_id)
        decision_num = len(prior_decisions) + 1
        total_weeks = len(weeks)
        snapshot = snapshot_builder.build_snapshot(
            cursor_date=cursor_date,
            exec_date=exec_date,
            week_number=decision_num,
            total_weeks=total_weeks,
            universe=universe,
        )

        warnings: list[str] = []
        executed: list[ExecutedOrder] = []

        if orders:
            # Get execution prices (Monday open)
            exec_prices = _get_opening_prices(self._store, universe, exec_date)
            limits = get_price_limits(self._store, universe, signal_date)
            suspended = detect_suspended(self._store, universe, exec_date)

            # Update portfolio prices with Friday close before sell checks
            close_prices = _get_close_prices(self._store, universe, signal_date)
            if close_prices:
                portfolio.update_prices(close_prices)

            total_value_before = portfolio.total_value

            # 1. Process SELL orders first
            sell_orders = [o for o in orders if o.direction == "SELL"]
            for order in sell_orders:
                if order.ts_code not in portfolio.holdings:
                    warnings.append(f"{order.ts_code} 仓位不存在，跳过卖出")
                    continue
                if order.ts_code in suspended:
                    warnings.append(f"{order.ts_code} 停牌中，跳过卖出")
                    continue
                price = exec_prices.get(order.ts_code)
                if price is None or price <= 0:
                    warnings.append(f"{order.ts_code} 无开盘价，跳过卖出")
                    continue
                if order.ts_code in limits:
                    ld, _lu = limits[order.ts_code]
                    if is_limit_down(price, ld):
                        warnings.append(f"{order.ts_code} 跌停，跳过卖出")
                        continue
                if not portfolio.can_sell(order.ts_code, exec_date):
                    warnings.append(f"{order.ts_code} T+1限制，跳过卖出")
                    continue

                shares, proceeds = portfolio.sell(
                    code=order.ts_code,
                    price=price,
                    trade_date=exec_date,
                )
                if shares > 0:
                    executed.append(
                        ExecutedOrder(
                            ts_code=order.ts_code,
                            direction="SELL",
                            target_pct=0.0,
                            shares=shares,
                            price=price,
                            cost_or_proceeds=round(proceeds, 2),
                            reason="用户主动减仓",
                        )
                    )

            # Also auto-sell holdings dropped from BUY target list
            target_weights = {o.ts_code: o.target_pct for o in orders if o.direction == "BUY"}
            for code, h in list(portfolio.holdings.items()):
                price = exec_prices.get(code)
                if price is None or price <= 0:
                    continue
                if code in suspended:
                    continue
                if code in limits:
                    ld, _lu = limits[code]
                    if is_limit_down(price, ld):
                        continue
                if not portfolio.can_sell(code, exec_date):
                    continue

                target_pct = target_weights.get(code)
                if target_pct is None:
                    # Drop fully
                    shares, proceeds = portfolio.sell(code=code, price=price, trade_date=exec_date)
                    if shares > 0:
                        executed.append(
                            ExecutedOrder(
                                ts_code=code,
                                direction="SELL",
                                target_pct=0.0,
                                shares=shares,
                                price=price,
                                cost_or_proceeds=round(proceeds, 2),
                                reason="自动清仓: 跌出目标组合",
                            )
                        )
                else:
                    current_value = h.shares * price
                    target_value = target_pct * total_value_before
                    if current_value > target_value * 1.01:
                        target_shares = int(target_value / price / 100) * 100
                        excess = h.shares - target_shares
                        if excess >= 100:
                            shares, proceeds = portfolio.sell(
                                code=code,
                                price=price,
                                shares=excess,
                                trade_date=exec_date,
                            )
                            if shares > 0:
                                executed.append(
                                    ExecutedOrder(
                                        ts_code=code,
                                        direction="SELL",
                                        target_pct=target_pct,
                                        shares=shares,
                                        price=price,
                                        cost_or_proceeds=round(proceeds, 2),
                                        reason="调仓降低超配仓位",
                                    )
                                )

            # 2. Execute BUY orders
            buy_orders = [o for o in orders if o.direction == "BUY"]
            total_after_sells = portfolio.total_value
            for order in buy_orders:
                code = order.ts_code
                if code in suspended:
                    warnings.append(f"{code} 停牌中，跳过买入")
                    continue
                price = exec_prices.get(code)
                if price is None or price <= 0:
                    warnings.append(f"{code} 无开盘价，跳过买入")
                    continue
                if code in limits:
                    _ld, lu = limits[code]
                    if is_limit_up(price, lu):
                        warnings.append(f"{code} 涨停，跳过买入")
                        continue

                target_amount = total_after_sells * order.target_pct
                current_value = 0.0
                if code in portfolio.holdings:
                    current_value = portfolio.holdings[code].shares * price

                if current_value >= target_amount:
                    continue

                buy_amount = min(target_amount - current_value, portfolio.cash * 0.3)
                shares, cost = portfolio.buy(code=code, price=price, amount=buy_amount, trade_date=exec_date)
                if shares > 0:
                    executed.append(
                        ExecutedOrder(
                            ts_code=code,
                            direction="BUY",
                            target_pct=order.target_pct,
                            shares=shares,
                            price=price,
                            cost_or_proceeds=round(cost, 2),
                            reason=order.reason if hasattr(order, "reason") and order.reason else "用户主动建仓",
                        )
                    )
                elif buy_amount > 0:
                    warnings.append(f"{code} 现金不足或最小交易单位不满足")

        # 3. Get strategy signals (shadow mode)
        strategy_orders: list[StrategySignalItem] | None = None
        if ref_strategy:
            try:
                sig_result = ref_strategy.generate_signals(cursor_date, universe, self._store)
                strategy_orders = [
                    StrategySignalItem(
                        ts_code=o.ts_code,
                        target_pct=o.target_pct,
                        direction=o.direction,
                        reason=o.reason,
                    )
                    for o in sig_result.orders
                ]
            except Exception:
                strategy_orders = None

        # 4. Advance cursor to next Friday
        next_week_idx = week_idx + 1
        cursor_advanced = next_week_idx < len(weeks)
        next_cursor = weeks[next_week_idx][0] if cursor_advanced else exec_date

        new_status = "active" if cursor_advanced else "completed"

        # 5. Save
        portfolio_json = json.dumps(portfolio.to_dict(), ensure_ascii=False, default=str)
        self._sessions.save(session_id, next_cursor, portfolio_json, status=new_status)

        decision = Decision(
            decision_number=decision_num,
            cursor_date=cursor_date,
            exec_date=exec_date,
            user_orders=orders,
            executed_orders=executed,
            notes=notes,
            snapshot_before=snapshot,
            strategy_orders=strategy_orders,
            timestamp=datetime.now(UTC).isoformat(),
        )
        self._sessions.append_decision(session_id, decision)

        return StepResult(
            decision=decision,
            cursor_advanced=cursor_advanced,
            next_cursor_date=next_cursor,
            portfolio_total_value=portfolio.total_value,
            portfolio_cash=portfolio.cash,
            holding_count=len(portfolio.holdings),
            warnings=warnings,
        )

    def skip(self, session_id: str) -> StepResult:
        """Skip this week without trading."""
        return self.step(session_id, orders=[], notes="跳过本周调仓")

    def status(self, session_id: str) -> dict[str, Any]:
        """Return current session summary."""
        row = self._sessions.load(session_id)
        if row is None:
            raise ValueError(f"Session {session_id} not found")

        cursor_date = _to_date(row["cursor_date"])
        start_date = _to_date(row["start_date"])
        end_date = _to_date(row["end_date"]) if row["end_date"] else None

        cal_df = self._store.get_calendar(start_date, end_date or date.today())
        calendar = TradeCalendar(cal_df["trade_date"].tolist())
        actual_start = calendar.next_trade_date(start_date) or start_date
        weeks = calendar.weeks_between(actual_start, end_date or date.today())
        week_idx = _find_week_index(weeks, cursor_date)

        portfolio = Portfolio.from_dict(json.loads(row["portfolio_json"]))

        prior = self._sessions.load_decisions(session_id)

        return {
            "session_id": session_id,
            "name": row["name"],
            "status": row["status"],
            "cursor_date": str(cursor_date),
            "week_number": week_idx + 1 if week_idx < len(weeks) else len(weeks),
            "total_weeks": len(weeks),
            "portfolio_value": portfolio.total_value,
            "cash": portfolio.cash,
            "holding_count": len(portfolio.holdings),
            "decision_count": len(prior),
            "reference_strategy": row.get("reference_strategy"),
            "created_at": str(row.get("created_at", "")),
            "updated_at": str(row.get("updated_at", "")),
        }

    def snapshot(self, session_id: str) -> Any:
        """Generate a snapshot at the current cursor position."""
        row = self._sessions.load(session_id)
        if row is None:
            raise ValueError(f"Session {session_id} not found")

        cursor_date = _to_date(row["cursor_date"])
        start_date = _to_date(row["start_date"])
        end_date = _to_date(row["end_date"]) if row["end_date"] else None

        portfolio = Portfolio.from_dict(json.loads(row["portfolio_json"]))

        ref_strategy: Strategy | None = None
        ref_name = row.get("reference_strategy")
        if ref_name:
            ref_strategy = strategy_registry.get(str(ref_name), store=self._store)

        cal_df = self._store.get_calendar(start_date, end_date or date.today())
        calendar = TradeCalendar(cal_df["trade_date"].tolist())
        actual_start = calendar.next_trade_date(start_date) or start_date
        weeks = calendar.weeks_between(actual_start, end_date or date.today())
        week_idx = _find_week_index(weeks, cursor_date)
        exec_date = weeks[week_idx][1] if week_idx < len(weeks) else cursor_date

        universe = self._store.get_universe(get_default_universe(), cursor_date)
        builder = SnapshotBuilder(self._store, portfolio, ref_strategy)
        return builder.build_snapshot(
            cursor_date=cursor_date,
            exec_date=exec_date,
            week_number=week_idx + 1,
            total_weeks=len(weeks),
            universe=universe,
        )


# ---- Helpers ----


def _to_date(d: Any) -> date:
    """Convert various date representations to date.

    pd.Timestamp is a subclass of datetime.datetime, which is a subclass of
    datetime.date — so isinstance checks must handle Timestamp first.
    """
    if isinstance(d, str):
        return date.fromisoformat(d[:10])
    try:
        return pd.Timestamp(d).date()
    except Exception:
        return date.today()


def _find_week_index(weeks: list[tuple[date, date]], cursor_date: date) -> int:
    """Find the index of the week containing cursor_date."""
    clean_weeks: list[tuple[date, date]] = []
    for signal_date, exec_date in weeks:
        clean_weeks.append((_to_date(signal_date), _to_date(exec_date)))
    for i, (signal_date, _exec_date) in enumerate(clean_weeks):
        if signal_date == cursor_date:
            return i
    # Cursor not exactly on a Friday — find the next
    for i, (signal_date, _exec_date) in enumerate(clean_weeks):
        if signal_date >= cursor_date:
            return i
    return len(weeks)


def _get_opening_prices(store: DataStore, codes: list[str], trade_date: date) -> dict[str, float]:
    """Get opening prices for a list of stocks on a given date."""
    if not codes:
        return {}
    placeholders = ", ".join(["?"] * len(codes))
    sql = f"""
        SELECT ts_code, open FROM daily_kline
        WHERE ts_code IN ({placeholders}) AND trade_date = ?
    """
    try:
        df = store.conn.execute(sql, list(codes) + [trade_date]).df()
        return dict(zip(df["ts_code"], df["open"], strict=False))
    except Exception:
        return {}


def _get_close_prices(store: DataStore, codes: list[str], trade_date: date) -> dict[str, float]:
    """Get closing prices for a list of stocks on a given date."""
    if not codes:
        return {}
    placeholders = ", ".join(["?"] * len(codes))
    sql = f"""
        SELECT ts_code, close FROM daily_kline
        WHERE ts_code IN ({placeholders}) AND trade_date = ?
    """
    try:
        df = store.conn.execute(sql, list(codes) + [trade_date]).df()
        return dict(zip(df["ts_code"], df["close"], strict=False))
    except Exception:
        return {}
