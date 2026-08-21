"""Timepoint data snapshot — aggregates market, portfolio, factor, and strategy data."""

from __future__ import annotations

from datetime import date, timedelta
from typing import cast

import pandas as pd
from loguru import logger

from quant_trade.backtest.portfolio import Portfolio
from quant_trade.data.calendar import TradeCalendar
from quant_trade.data.store import DataStore
from quant_trade.factors.preprocess import preprocess
from quant_trade.factors.registry import registry as factor_registry
from quant_trade.simulator.types import (
    FactorRankItem,
    MarketOverview,
    PortfolioItem,
    Snapshot,
    StrategySignalItem,
)
from quant_trade.strategies.base import SignalResult, Strategy


class SnapshotBuilder:
    """Builds a Snapshot at a given cursor date."""

    def __init__(self, store: DataStore, portfolio: Portfolio, reference_strategy: Strategy | None = None) -> None:
        self._store = store
        self._portfolio = portfolio
        self._ref_strategy = reference_strategy
        self._calendar: TradeCalendar | None = None

    def build_snapshot(
        self,
        cursor_date: date,
        exec_date: date,
        week_number: int,
        total_weeks: int | None,
        universe: list[str],
        skip_heavy: bool = False,
    ) -> Snapshot:
        """Build a complete snapshot.

        When ``skip_heavy=True``, factor ranking and strategy signals are
        skipped (set to [] and None). Use this for initial session creation
        where the portfolio is empty and no decision has been made yet.
        """
        logger.info("Building snapshot: cursor_date={}, universe_size={}", cursor_date, len(universe))

        logger.info("Snapshot: building market overview...")
        market = self._build_market_overview(cursor_date)

        logger.info("Snapshot: building portfolio snapshot...")
        portfolio_items = self._build_portfolio_snapshot(cursor_date)

        all_warnings: list[str] = []

        if skip_heavy:
            logger.info("Skipping factor ranking and strategy signals (deferred to first step)")
            ranking: list[FactorRankItem] = []
            strategy_signals = None
            all_warnings.append("因子和策略信号将在首次调仓时计算")
        else:
            logger.info("Snapshot: building factor ranking...")
            ranking, data_warnings = self._build_factor_ranking(cursor_date, universe)

            logger.info("Snapshot: building strategy signals...")
            strategy_signals = self._build_strategy_signals(cursor_date, universe)

            all_warnings.extend(data_warnings)
            if not strategy_signals and self._ref_strategy is not None:
                all_warnings.append("参考策略信号生成失败或无信号")

        return Snapshot(
            signal_date=cursor_date,
            exec_date=exec_date,
            week_number=week_number,
            total_weeks=total_weeks,
            market=market,
            portfolio=portfolio_items,
            total_value=self._portfolio.total_value,
            cash=self._portfolio.cash,
            factor_ranking=ranking,
            strategy_signals=strategy_signals,
            data_warnings=all_warnings,
        )

    def _build_market_overview(self, cursor_date: date) -> MarketOverview | None:
        """CSI 300 close + weekly return."""
        try:
            df = self._store.get_daily(
                ["000300.SH"],
                cursor_date - timedelta(days=10),
                cursor_date,
                fields=["trade_date", "close"],
            )
            if df.empty:
                return None
            df = df.sort_values("trade_date")
            latest = df.iloc[-1]
            prev_close = df.iloc[-2]["close"] if len(df) >= 2 else latest["close"]
            weekly_return = float(float(latest["close"]) / prev_close - 1) if prev_close > 0 else 0.0

            # Count trading days this week
            monday = cursor_date - timedelta(days=cursor_date.weekday())
            week_dates = df[df["trade_date"] >= pd.Timestamp(monday)]
            trading_days = len(week_dates)

            return MarketOverview(
                benchmark_code="000300.SH",
                benchmark_close=float(latest["close"]),
                benchmark_weekly_return=round(weekly_return, 4),
                trading_days_this_week=trading_days,
            )
        except Exception as e:
            logger.warning(f"Market overview failed: {e}")
            return None

    def _build_portfolio_snapshot(self, cursor_date: date) -> list[PortfolioItem]:
        """Holdings with market value, P&L, weight."""
        items: list[PortfolioItem] = []
        total = self._portfolio.total_value
        if total <= 0:
            return items

        # Get current prices
        codes = list(self._portfolio.holdings.keys())
        prices = self._get_latest_prices(codes, cursor_date) if codes else {}

        for code, h in self._portfolio.holdings.items():
            price = prices.get(code, h.current_price or h.avg_cost)
            market_value = h.shares * price
            pnl_pct = (price - h.avg_cost) / h.avg_cost if h.avg_cost > 0 else 0.0
            weight_pct = market_value / total if total > 0 else 0.0
            items.append(
                PortfolioItem(
                    ts_code=code,
                    shares=h.shares,
                    avg_cost=round(h.avg_cost, 2),
                    current_price=round(price, 2),
                    market_value=round(market_value, 2),
                    pnl_pct=round(pnl_pct, 4),
                    weight_pct=round(weight_pct, 4),
                    buy_date=str(h.buy_date) if h.buy_date else None,
                )
            )
        return items

    def _build_factor_ranking(self, cursor_date: date, universe: list[str]) -> tuple[list[FactorRankItem], set[str]]:
        """Compute composite factor scores → top-30 ranking. Returns (items, warnings)."""
        warnings: set[str] = set()
        factor_names = factor_registry.list_all()
        if not factor_names:
            return [], {"无已注册的因子"}

        factor_scores: dict[str, pd.Series] = {}
        failed_factors: list[str] = []
        total_factors = len(factor_names)
        logger.info("Computing {} factors for {} stocks...", total_factors, len(universe))

        for i, fname in enumerate(factor_names, start=1):
            logger.info("Factor {}/{}: {}...", i, total_factors, fname)
            factor = factor_registry.get(fname, store=self._store)
            if factor is None:
                continue
            try:
                raw = factor.compute(cursor_date, universe)
                if raw.empty:
                    failed_factors.append(fname)
                    continue
                processed = preprocess(raw.reindex(universe), steps=["winsorize", "standardize"])
                factor_scores[fname] = processed
            except Exception:
                failed_factors.append(fname)

        if failed_factors:
            names = ", ".join(failed_factors)
            warnings.add(f"{len(failed_factors)} 个因子计算失败或返回空值: {names}")

        if not factor_scores:
            # Try to provide at least momentum factors
            for fname in ["momentum_20d", "momentum_60d", "ma_deviation"]:
                factor = factor_registry.get(fname, store=self._store)
                if factor is None:
                    continue
                try:
                    raw = factor.compute(cursor_date, universe)
                    if not raw.empty:
                        processed = preprocess(raw.reindex(universe), steps=["winsorize", "standardize"])
                        factor_scores[fname] = processed
                except Exception:
                    pass

        if not factor_scores:
            return [], warnings | {"无可用因子数据"}

        score_df = pd.DataFrame(dict(factor_scores))
        score_df = score_df.dropna(how="all")
        if score_df.empty:
            return [], warnings

        default_weight = 1.0 / len(score_df.columns)
        composite = pd.Series(0.0, index=score_df.index)
        for col in score_df.columns:
            composite = composite.add(score_df[col].fillna(0) * default_weight, fill_value=0)

        ranked = composite.sort_values(ascending=False).head(30)

        logger.debug(
            "Factor ranking complete: {} factors used, {} stocks ranked",
            len(score_df.columns),
            len(ranked),
        )

        items: list[FactorRankItem] = []
        for rank, code in enumerate(ranked.index, start=1):
            code_str = str(code)
            fs: dict[str, float | None] = {}
            for fname in score_df.columns:
                val = cast(float, score_df.at[code_str, fname]) if code_str in score_df.index else None
                if pd.isna(val):
                    fs[fname] = None
                else:
                    fs[fname] = round(float(val), 4)
            score = float(cast(float, ranked.at[code_str]))
            items.append(
                FactorRankItem(
                    rank=rank,
                    ts_code=code_str,
                    composite_score=round(score, 4),
                    factor_scores=fs,
                )
            )

        return items, warnings

    def _build_strategy_signals(self, cursor_date: date, universe: list[str]) -> list[StrategySignalItem] | None:
        """Call reference strategy to get recommended buys/sells."""
        if self._ref_strategy is None:
            return None
        try:
            result: SignalResult = self._ref_strategy.generate_signals(cursor_date, universe, self._store)
            if not result.orders:
                return []
            items: list[StrategySignalItem] = []
            for order in result.orders:
                items.append(
                    StrategySignalItem(
                        ts_code=order.ts_code,
                        target_pct=order.target_pct,
                        direction=order.direction,
                        reason=order.reason,
                    )
                )
            # Also add sells for holdings NOT in the strategy target list
            target_codes = {o.ts_code for o in result.orders if o.direction == "BUY"}
            for code in self._portfolio.holdings:
                if code not in target_codes:
                    items.append(
                        StrategySignalItem(
                            ts_code=code,
                            target_pct=0.0,
                            direction="SELL",
                            reason="跌出策略目标组合",
                        )
                    )
            return items
        except Exception as e:
            logger.warning(f"Strategy signals failed: {e}")
            return None

    def _get_latest_prices(self, codes: list[str], as_of: date) -> dict[str, float]:
        """Get latest close prices ≤ as_of for a list of codes."""
        if not codes:
            return {}
        df = self._store.get_daily(codes, as_of - timedelta(days=10), as_of, fields=["ts_code", "trade_date", "close"])
        if df.empty:
            return {}
        df = df.sort_values("trade_date")
        result: dict[str, float] = {}
        for code in codes:
            code_df = df[df["ts_code"] == code]
            if not code_df.empty:
                result[code] = float(code_df.iloc[-1]["close"])
        return result
