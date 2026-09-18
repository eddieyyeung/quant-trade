"""Strategy-domain services — signal generation and registry listing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from quant_trade.config import AppConfig
from quant_trade.data.index_weights import get_default_universe
from quant_trade.data.store import DataStore
from quant_trade.services.context import NULL_CONTEXT, RunContext
from quant_trade.services.params import ServiceParams
from quant_trade.strategies.factory import build_strategy
from quant_trade.strategies.registry import strategy_registry
from quant_trade.strategies.signal_store import build_signal_frame, save_strategy_signals

FRIDAY = 4
"""``date.weekday()`` value for Friday — the weekly rebalance signal day."""

FRIDAY_LOOKBACK_DAYS = 21
"""Calendar window searched for the most recent Friday. Wide enough to cover
any market holiday run."""


class SignalParams(ServiceParams):
    """Which strategy to run, for which date and universe."""

    as_of: date | None = None
    """Signal date. ``None`` means the latest trade date in the database."""
    strategy: str | None = None
    """Strategy name. ``None`` means the one named in config."""
    top_n: int | None = None
    """Override for the strategy's holding count."""
    universe: list[str] | None = None
    """Explicit codes. ``None`` means the default index-derived universe."""

    @classmethod
    def config_defaults(cls, config: AppConfig) -> dict[str, object]:
        return {"strategy": config.strategy.name, "top_n": config.strategy.top_n}


class StrategyListParams(ServiceParams):
    """No options; present so every service shares one call shape."""


@dataclass
class SignalOrder:
    """One order in a generated signal set."""

    ts_code: str
    target_pct: float
    direction: str
    reason: str


@dataclass
class SignalSummary:
    """Serializable result of a signal generation run."""

    signal_date: date
    strategy: str
    universe_size: int
    orders: list[SignalOrder] = field(default_factory=list)
    weights: dict[str, float] = field(default_factory=dict)


def most_recent_friday(store: DataStore, as_of: date) -> date | None:
    """The latest trade date on or before ``as_of`` that lands on a Friday.

    Rebalance signals are generated from a Friday close, so running mid-week
    must still key off the previous Friday rather than whatever day it is now.
    Returns ``None`` when the window holds no Friday at all.
    """
    calendar = store.get_calendar(as_of - timedelta(days=FRIDAY_LOOKBACK_DAYS), as_of, open_only=True)
    if calendar.empty:
        return None
    days = [_as_date(value) for value in calendar["trade_date"].tolist()]
    fridays = [d for d in days if d.weekday() == FRIDAY]
    return fridays[-1] if fridays else None


def _as_date(value: object) -> date:
    """Normalise a pandas Timestamp / datetime to a plain ``date``.

    ``get_calendar`` hands back Timestamps, which compare and format fine but
    are not what the service contract promises callers.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raise TypeError(f"expected a date-like value, got {type(value).__name__}")


def generate_strategy_signals(params: SignalParams, ctx: RunContext = NULL_CONTEXT) -> SignalSummary:
    """Run a strategy and return its orders for one date.

    With no explicit date, signals key off the most recent Friday — the weekly
    rebalance anchor — so a mid-week run does not silently use a Tuesday.
    """
    store = ctx.db
    as_of = params.as_of or most_recent_friday(store, date.today()) or store.get_latest_trade_date()
    if as_of is None:
        raise ValueError("Database has no trade dates; run a data sync first")

    strategy = build_strategy(params.strategy, ctx.cfg, store=ctx.db)
    if strategy is None:
        raise ValueError(f"Strategy {params.strategy or ctx.cfg.strategy.name!r} is not registered")
    if params.top_n is not None and hasattr(strategy, "top_n"):
        strategy.top_n = params.top_n

    universe = params.universe or store.get_universe(get_default_universe(), as_of)
    ctx.progress(0.0, f"Generating signals for {len(universe)} stocks")
    result = strategy.generate_signals(as_of, universe, store)
    ctx.progress(1.0, f"Generated {len(result.orders)} orders")

    return SignalSummary(
        signal_date=as_of,
        strategy=strategy.name,
        universe_size=len(universe),
        orders=[
            SignalOrder(ts_code=o.ts_code, target_pct=o.target_pct, direction=o.direction, reason=o.reason)
            for o in result.orders
        ],
        weights=dict(result.weights),
    )


def run_strategy_signals(params: SignalParams, ctx: RunContext = NULL_CONTEXT) -> SignalSummary:
    """Generate signals and persist them under ``ctx.run_id``.

    A separate entry point from :func:`generate_strategy_signals` on purpose.
    That one is called by the weekly report pipeline, which runs under its own
    ``run_id`` — persisting there would write a batch of ``strategy_signal``
    rows keyed to a report run, a set of signals answering no question anyone
    asked and attached to a run that was not a signal generation. Persisting is
    this function's job; that one stays a pure computation.

    With an empty ``run_id`` (``NULL_CONTEXT``, the script and test default) or
    with no orders, the summary is returned and nothing is written. An empty
    result is not worth a row: it would claim the run produced signals of none.
    """
    summary = generate_strategy_signals(params, ctx)
    if not ctx.run_id or not summary.orders:
        return summary

    frame = build_signal_frame(
        [
            {
                "ts_code": order.ts_code,
                "direction": order.direction,
                "target_pct": order.target_pct,
                "reason": order.reason,
            }
            for order in summary.orders
        ],
        summary.signal_date,
        summary.strategy,
    )
    rows = save_strategy_signals(ctx.db, ctx.run_id, frame)
    ctx.log(f"Persisted {rows} signals for {summary.strategy} on {summary.signal_date}")
    return summary


def list_strategies(params: StrategyListParams, ctx: RunContext = NULL_CONTEXT) -> list[str]:
    """Names of every registered strategy."""
    return list(strategy_registry.list_all())
