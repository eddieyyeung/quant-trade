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
class WeeklyDeviation:
    """How one week's decision lines up with what was recommended for it.

    Compares the two *target portfolios* — the buy targets each side was aiming
    at — rather than the orders or the resulting holdings. The same portfolio
    can be reached by different orders (two separate buys, a buy then a trim),
    and an order-level comparison would call that a deviation.
    """

    followed: bool
    dropped: list[str]  # recommended, the user skipped
    added: list[str]  # the user added, not recommended


@dataclass
class WeeklyDiff:
    """Per-week comparison of a decision against the week's recommendation."""

    week_number: int
    cursor_date: date
    # None means there was no recommendation to compare against — no reference
    # strategy, or none that produced signals. Distinct from a deviation that
    # happens to be empty, which means "you followed it exactly".
    deviation: WeeklyDeviation | None = None
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
    # Why the strategy line is missing, when it is. A failing strategy must not
    # cost the manual and benchmark lines their report, but it also must not
    # disappear without a word — silence is how the date-type bug hid.
    strategy_error: str | None = None
    html_path: str | None = None
