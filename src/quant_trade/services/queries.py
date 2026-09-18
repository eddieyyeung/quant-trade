"""Query services — the metadata reads that platform pages need.

These replace the bare SQL that used to live in the CLI, so callers get typed
results instead of parsing printed text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from quant_trade.data.index_weights import get_default_universe
from quant_trade.services.context import NULL_CONTEXT, RunContext
from quant_trade.services.params import ServiceParams


class FactorNamesParams(ServiceParams):
    """No options; present so every service shares one call shape."""


class UniverseCoverageParams(ServiceParams):
    """Which universe to measure kline coverage for."""

    universe: list[str] | None = None
    """Explicit codes. ``None`` means the default index-derived universe."""
    as_of: date | None = None
    """Date used to resolve the default universe. ``None`` means today."""


class TradeCalendarParams(ServiceParams):
    """Which slice of the stored calendar to read. Bounds are inclusive."""

    start: date | None = None
    end: date | None = None


@dataclass
class UniverseCoverage:
    """How much of a universe actually has kline data."""

    universe_size: int
    covered: int
    ratio: float
    missing: list[str] = field(default_factory=list)


@dataclass
class CalendarDay:
    """One day in the calendar, and whether the market was open."""

    trade_date: date
    is_open: bool


@dataclass
class TradeCalendarView:
    """The stored trade calendar over a range, with day counts."""

    start: date | None
    end: date | None
    days: list[CalendarDay]
    open_days: int
    closed_days: int


def list_factor_names(params: FactorNamesParams, ctx: RunContext = NULL_CONTEXT) -> list[str]:
    """Factor names that have rows in ``factor_values``."""
    rows = ctx.db.conn.execute("SELECT DISTINCT factor_name FROM factor_values ORDER BY factor_name").fetchall()
    return [str(r[0]) for r in rows]


def universe_coverage(params: UniverseCoverageParams, ctx: RunContext = NULL_CONTEXT) -> UniverseCoverage:
    """Count how many codes in a universe have synced kline data."""
    store = ctx.db
    universe = params.universe
    if universe is None:
        universe = store.get_universe(get_default_universe(), params.as_of or date.today())
    if not universe:
        return UniverseCoverage(universe_size=0, covered=0, ratio=0.0)

    synced = store.synced_codes()
    missing = [c for c in universe if c not in synced]
    covered = len(universe) - len(missing)
    return UniverseCoverage(
        universe_size=len(universe),
        covered=covered,
        ratio=covered / len(universe),
        missing=missing,
    )


def trade_calendar(params: TradeCalendarParams, ctx: RunContext = NULL_CONTEXT) -> TradeCalendarView:
    """Every stored calendar day in ``[start, end]``, trading and non-trading alike."""
    df = ctx.db.get_calendar(params.start or _CALENDAR_FLOOR, params.end or _CALENDAR_CEILING, open_only=False)
    days = [
        CalendarDay(trade_date=_as_date(row.trade_date), is_open=bool(row.is_open))
        for row in df.itertuples(index=False)
    ]
    open_days = sum(1 for day in days if day.is_open)
    return TradeCalendarView(
        start=days[0].trade_date if days else None,
        end=days[-1].trade_date if days else None,
        days=days,
        open_days=open_days,
        closed_days=len(days) - open_days,
    )


_CALENDAR_FLOOR = date(1990, 1, 1)
_CALENDAR_CEILING = date(2100, 12, 31)
"""Open bounds used when a caller does not narrow the calendar window."""


def _as_date(value: Any) -> date:
    """Normalise a DuckDB DATE, which arrives as a pandas ``Timestamp``."""
    return value.date() if isinstance(value, datetime) else value
