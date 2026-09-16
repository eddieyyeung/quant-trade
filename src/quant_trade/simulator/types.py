"""Core dataclasses for the simulator module."""

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class OrderRequest:
    """A user-submitted order for one step."""

    ts_code: str
    target_pct: float  # 0.0 = sell all, >0 = buy to this weight
    direction: str = "BUY"  # BUY or SELL


@dataclass
class ExecutedOrder:
    """Result of executing a single order."""

    ts_code: str
    direction: str
    target_pct: float
    shares: int
    price: float
    cost_or_proceeds: float
    reason: str = ""


@dataclass
class PortfolioItem:
    """A single holding line in the snapshot."""

    ts_code: str
    shares: int
    avg_cost: float
    current_price: float
    market_value: float
    pnl_pct: float
    weight_pct: float
    buy_date: str | None = None


@dataclass
class FactorRankItem:
    """One row in the factor ranking table."""

    rank: int
    ts_code: str
    composite_score: float
    factor_scores: dict[str, float | None] = field(default_factory=dict)


@dataclass
class StrategySignalItem:
    """A single recommendation from the reference strategy."""

    ts_code: str
    target_pct: float
    direction: str  # BUY or SELL
    reason: str


@dataclass
class MarketOverview:
    """Market-level snapshot data."""

    benchmark_code: str
    benchmark_close: float
    benchmark_weekly_return: float
    trading_days_this_week: int


@dataclass
class Snapshot:
    """Complete data snapshot at a cursor Friday."""

    signal_date: date
    exec_date: date
    week_number: int
    total_weeks: int | None
    market: MarketOverview | None
    portfolio: list[PortfolioItem]
    total_value: float
    cash: float
    factor_ranking: list[FactorRankItem]
    strategy_signals: list[StrategySignalItem] | None
    data_warnings: list[str] = field(default_factory=list)


@dataclass
class Decision:
    """Record of one weekly decision."""

    decision_number: int
    cursor_date: date
    exec_date: date
    user_orders: list[OrderRequest]
    executed_orders: list[ExecutedOrder]
    notes: str
    snapshot_before: Snapshot | None
    strategy_orders: list[StrategySignalItem] | None
    timestamp: str  # ISO 8601


@dataclass
class StepResult:
    """Result after executing a step (step or skip)."""

    decision: Decision
    cursor_advanced: bool
    next_cursor_date: date
    portfolio_total_value: float
    portfolio_cash: float
    holding_count: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class WeeklyDiff:
    """Per-week comparison of manual vs strategy decisions."""

    week_number: int
    cursor_date: date
    user_holds: list[str]  # codes user held this week
    strategy_holds: list[str]  # codes strategy held this week
    user_only: list[str]
    strategy_only: list[str]
    common: list[str]
    overlap_count: int
    total_user: int
    total_strategy: int
    concentration_warning: str | None = None
    drawdown_warning: str | None = None


@dataclass
class ComparisonResult:
    """Multi-line comparison report."""

    session_id: str
    weeks_completed: int
    nav_manual: list[dict[str, Any]]  # [{trade_date, nav}, ...]
    nav_strategy: list[dict[str, Any]] | None
    nav_benchmark: list[dict[str, Any]] | None
    metrics: dict[str, dict[str, float]]
    weekly_diffs: list[WeeklyDiff]
    html_path: str | None = None
