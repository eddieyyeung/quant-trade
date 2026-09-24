"""Strategy-domain read services — signal-run history and one run's signals.

These are the only entry points the strategy pages use to read results. They
read ``strategy_signal`` rather than re-running the strategy: signals are a pure
function of ``(factor_values, strategy params)`` and the former is upserted, so
re-deriving them would hand back different numbers after any recompute. The
stored rows are the only stable answer to "what did it say that day".

Shaping lives here rather than in :mod:`quant_trade.api.strategies` because an
adapter that reshapes domain values is domain logic by another name — the
``service-layer`` contract puts it behind a service function.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from pydantic import Field

from quant_trade.runs.models import RunStatus
from quant_trade.services.context import NULL_CONTEXT, RunContext
from quant_trade.services.params import ServiceParams
from quant_trade.strategies.signal_store import (
    StrategyRunRow,
    count_strategy_signals,
    get_strategy_run,
    get_strategy_signals,
    list_strategy_runs,
)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200
DEFAULT_SIGNAL_PAGE_SIZE = 100
MAX_SIGNAL_PAGE_SIZE = 500


class StrategyRunListParams(ServiceParams):
    """One page of signal-run history."""

    limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


class StrategySignalQueryParams(ServiceParams):
    """Which run's signals to read, and which page of them."""

    run_id: str = Field(min_length=1)
    limit: int = Field(default=DEFAULT_SIGNAL_PAGE_SIZE, ge=1, le=MAX_SIGNAL_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


@dataclass
class StrategyRunSummary:
    """One signal run as the history list renders it.

    ``signal_date`` / ``strategy`` / ``order_count`` are ``None`` for a run that
    has not produced its signals yet — absent, never 0. Zero would read as "this
    run produced no signals", which is a different claim from "it is still
    going".
    """

    run_id: str
    status: str
    created_at: datetime | None = None
    finished_at: datetime | None = None
    progress: float = 0.0
    signal_date: date | None = None
    strategy: str | None = None
    order_count: int | None = None
    universe_size: int | None = None


@dataclass
class StrategyRunListResult:
    """A page of signal runs plus the total, for server-side paging."""

    total: int = 0
    offset: int = 0
    limit: int = 0
    runs: list[StrategyRunSummary] = field(default_factory=list)


@dataclass
class StrategySignalRow:
    """One order in a generated signal set."""

    seq: int
    ts_code: str
    direction: str
    target_pct: float
    reason: str


@dataclass
class StrategySignalResult:
    """One run's signals, or ``found=False`` when it has none to show.

    ``status`` and ``finished_at`` come from the run rather than the signal
    rows: they say how the run ended, which is what a cancelled run's page has
    to annotate.
    """

    run_id: str
    found: bool = False
    status: str = ""
    finished_at: datetime | None = None
    strategy: str | None = None
    signal_date: date | None = None
    universe_size: int | None = None
    orders: list[StrategySignalRow] = field(default_factory=list)
    total: int = 0


def strategy_run_list(params: StrategyRunListParams, ctx: RunContext = NULL_CONTEXT) -> StrategyRunListResult:
    """One page of signal runs, newest first."""
    rows, total = list_strategy_runs(ctx.db, limit=params.limit, offset=params.offset)
    return StrategyRunListResult(
        total=total,
        offset=params.offset,
        limit=params.limit,
        runs=[_summary(row) for row in rows],
    )


def strategy_signals(params: StrategySignalQueryParams, ctx: RunContext = NULL_CONTEXT) -> StrategySignalResult:
    """One page of a run's signals, in the order the engine emitted them.

    Three outcomes, and the middle one is why this is not simply "no rows means
    not found":

    * unknown ``run_id`` — not found. Nothing to say about a run that is not there.
    * known but not finished — not found. There is no result yet, and an empty
      list would read as "it ran and picked nothing", which is a different claim.
    * known and finished with no orders — **found**, with an empty list. The run
      genuinely selected nothing, and the page has to say so; reporting 404 there
      would tell the reader the run does not exist.
    """
    store = ctx.db
    run = get_strategy_run(store, params.run_id)
    total = count_strategy_signals(store, params.run_id)
    if run is None:
        return StrategySignalResult(run_id=params.run_id, found=False)
    if total == 0 and not RunStatus(run.status).is_terminal:
        return StrategySignalResult(run_id=params.run_id, found=False)

    frame = get_strategy_signals(store, params.run_id, limit=params.limit, offset=params.offset)
    orders = [
        StrategySignalRow(
            seq=int(record["seq"]),
            ts_code=str(record["ts_code"]),
            direction=str(record["direction"]),
            target_pct=float(record["target_pct"]),
            reason=str(record["reason"]),
        )
        for record in frame.to_dict("records")
    ]
    # Strategy and date are on every row, so any row carries them. Status,
    # timestamps and the universe size are not — those come from the run.
    first = frame.iloc[0] if not frame.empty else None
    return StrategySignalResult(
        run_id=params.run_id,
        found=True,
        status=run.status,
        finished_at=run.finished_at,
        strategy=str(first["strategy"]) if first is not None else None,
        signal_date=_as_date(first["trade_date"]) if first is not None else None,
        universe_size=run.universe_size,
        orders=orders,
        total=total,
    )


def _summary(row: StrategyRunRow) -> StrategyRunSummary:
    return StrategyRunSummary(
        run_id=row.run_id,
        status=row.status,
        created_at=row.created_at,
        finished_at=row.finished_at,
        progress=row.progress,
        signal_date=row.signal_date,
        strategy=row.strategy_name,
        order_count=row.order_count,
        universe_size=row.universe_size,
    )


def _as_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None
