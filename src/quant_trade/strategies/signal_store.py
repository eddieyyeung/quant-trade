"""Persistence and querying of strategy signals in DuckDB.

The ``strategy_signal`` table is the single source of truth for signal read
paths: the strategy pages read it instead of re-running the strategy, so a past
date's signals read back the same after ``factor_values`` has been recomputed.
That matters because signals are a pure function of ``(factor_values, strategy
params)`` and the former is upserted — without a stored copy, "what did the
strategy say last Wednesday" has no stable answer.

Everything here takes and returns plain pandas objects or dicts. The strategies
package must not import :mod:`quant_trade.services` — the service layer imports
this module, not the other way round — so the shaping from the service's result
dataclasses happens at the call site, and this module owns what the table looks
like.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd
from loguru import logger

from quant_trade.data.store import DataStore

SIGNAL_COLUMNS: list[str] = [
    "run_id",
    "seq",
    "trade_date",
    "strategy",
    "ts_code",
    "direction",
    "target_pct",
    "reason",
]
"""Column order of ``strategy_signal``, used for bulk inserts."""

STRATEGY_SIGNAL_KIND = "strategy_signals"
"""The ``run.kind`` these rows belong to."""


@dataclass
class StrategyRunRow:
    """One strategy-signal run as the history list renders it.

    ``signal_date`` / ``strategy_name`` / ``order_count`` come from the signal
    table, not from the artifact's meta: they are derivable from the rows, and
    a second copy of a derivable value is a second thing that can go stale.
    ``universe_size`` is the exception — it is a run-level fact with no home in
    the rows, so it comes from the artifact.
    """

    run_id: str
    status: str
    created_at: datetime | None = None
    finished_at: datetime | None = None
    progress: float = 0.0
    signal_date: date | None = None
    strategy_name: str | None = None
    order_count: int | None = None
    universe_size: int | None = None


def build_signal_frame(orders: list[dict[str, Any]], signal_date: date, strategy: str) -> pd.DataFrame:
    """Shape a run's orders into the ``strategy_signal`` columns.

    ``seq`` is assigned here, contiguously from 1, because order carries
    meaning: the strategy decides whether sells precede buys and the reader
    must see them as the engine emitted them. Letting the database order rows
    would quietly destroy that.
    """
    rows = [
        {
            "seq": position,
            "trade_date": signal_date,
            "strategy": strategy,
            "ts_code": str(order.get("ts_code", "")),
            "direction": str(order.get("direction", "")),
            "target_pct": float(order.get("target_pct", 0.0)),
            "reason": str(order.get("reason", "")),
        }
        for position, order in enumerate(orders, start=1)
    ]
    return pd.DataFrame(rows, columns=[column for column in SIGNAL_COLUMNS if column != "run_id"])


def save_strategy_signals(store: DataStore, run_id: str, frame: pd.DataFrame) -> int:
    """Write one run's signals to ``strategy_signal``. Returns the rows written.

    A rewrite replaces the run's whole row set: the existing rows are deleted
    first, then the frame is inserted. ``INSERT OR REPLACE`` alone would not be
    idempotent — it only replaces rows whose ``(run_id, seq)`` already exists,
    so rewriting a run with *fewer* orders would leave the previous tail behind
    and the run would claim signals it no longer has.
    """
    if frame.empty:
        return 0
    missing = [column for column in SIGNAL_COLUMNS if column != "run_id" and column not in frame.columns]
    if missing:
        raise ValueError(f"strategy_signal frame is missing columns {missing}")

    payload = frame.assign(run_id=run_id)[SIGNAL_COLUMNS]
    relation = "_strategy_signal_insert"
    store.conn.register(relation, payload)
    try:
        store.conn.execute("DELETE FROM strategy_signal WHERE run_id = ?", [run_id])
        store.conn.execute(f"INSERT INTO strategy_signal SELECT * FROM {relation}")
    finally:
        store.conn.unregister(relation)
    return len(payload)


def get_strategy_signals(store: DataStore, run_id: str, limit: int, offset: int) -> pd.DataFrame:
    """One page of a run's signals, in the order the engine emitted them."""
    return _query(
        store,
        f"SELECT {', '.join(SIGNAL_COLUMNS)} FROM strategy_signal WHERE run_id = ? ORDER BY seq LIMIT ? OFFSET ?",
        [run_id, limit, offset],
        SIGNAL_COLUMNS,
    )


def count_strategy_signals(store: DataStore, run_id: str) -> int:
    """How many signals a run produced, for the table's pagination."""
    frame = _query(
        store,
        "SELECT COUNT(*) AS total FROM strategy_signal WHERE run_id = ?",
        [run_id],
        ["total"],
    )
    return int(frame["total"].iloc[0]) if not frame.empty else 0


def get_strategy_run(store: DataStore, run_id: str) -> StrategyRunRow | None:
    """One run's row, or ``None`` when nothing is registered under that id.

    The single-run form of :func:`list_strategy_runs`, sharing its query so the
    two cannot drift. The detail page needs the run's status and timestamps —
    neither of which lives in the signal rows — and its ``universe_size``, which
    is the one signal-adjacent field with no home in the rows either.
    """
    frame = _query(
        store,
        f"{_RUN_PAGE_SQL} AND r.run_id = ? LIMIT 1",
        [STRATEGY_SIGNAL_KIND, run_id],
        _RUN_PAGE_COLUMNS,
    )
    if frame.empty:
        return None
    return _row_from_record(frame.to_dict("records")[0])


def list_strategy_runs(store: DataStore, limit: int, offset: int) -> tuple[list[StrategyRunRow], int]:
    """One page of strategy-signal runs, newest first.

    Reads ``run`` to learn each run's status and timestamps but never writes it:
    ``run`` rows belong to :class:`~quant_trade.runs.store.RunStore`.

    Both joins are LEFT joins, deliberately. A run that has not produced its
    signals yet is still a run the reader submitted and wants to watch, and an
    inner join would hide it until it had already finished. A run that produced
    no orders at all also still happened. Hiding either would make a submitted
    run look like it was never accepted.
    """
    page = _query(
        store,
        f"{_RUN_PAGE_SQL} ORDER BY r.created_at DESC, r.run_id DESC LIMIT ? OFFSET ?",
        [STRATEGY_SIGNAL_KIND, limit, offset],
        _RUN_PAGE_COLUMNS,
    )
    totals = _query(store, "SELECT COUNT(*) AS total FROM run WHERE kind = ?", [STRATEGY_SIGNAL_KIND], ["total"])
    total = int(totals["total"].iloc[0]) if not totals.empty else 0
    if page.empty:
        return [], total
    return [_row_from_record(record) for record in page.to_dict("records")], total


_RUN_PAGE_SQL = """
    WITH signal_agg AS (
        SELECT run_id,
               MIN(trade_date) AS signal_date,
               MAX(strategy)   AS strategy,
               COUNT(*)        AS order_count
        FROM strategy_signal
        GROUP BY run_id
    ),
    latest_artifact AS (
        SELECT run_id, meta_json,
               ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY created_at DESC) AS rn
        FROM artifact
        WHERE storage = 'table' AND ref = 'strategy_signal'
    )
    SELECT r.run_id, r.status, r.progress, r.created_at, r.finished_at,
           a.signal_date, a.strategy, a.order_count, art.meta_json
    FROM run r
    LEFT JOIN signal_agg a ON a.run_id = r.run_id
    LEFT JOIN latest_artifact art ON art.run_id = r.run_id AND art.rn = 1
    WHERE r.kind = ?
"""

_RUN_PAGE_COLUMNS = [
    "run_id",
    "status",
    "progress",
    "created_at",
    "finished_at",
    "signal_date",
    "strategy",
    "order_count",
    "meta_json",
]


def _row_from_record(record: dict[Any, Any]) -> StrategyRunRow:
    """Build a :class:`StrategyRunRow` from one joined run/signal record."""
    return StrategyRunRow(
        run_id=str(record["run_id"]),
        status=str(record["status"]),
        created_at=_as_datetime(record.get("created_at")),
        finished_at=_as_datetime(record.get("finished_at")),
        progress=float(record.get("progress") or 0.0),
        signal_date=_as_date(record.get("signal_date")),
        strategy_name=_as_text(record.get("strategy")),
        order_count=_as_count(record.get("order_count")),
        universe_size=_universe_size(record.get("meta_json")),
    )


def _universe_size(meta_json: Any) -> int | None:
    """The universe size a run recorded, when the meta is legible.

    A row whose meta cannot be parsed still lists — it just lists without this
    column, which beats a page that fails to render over one old row.
    """
    if not isinstance(meta_json, str) or not meta_json:
        return None
    try:
        parsed = json.loads(meta_json)
    except ValueError:
        logger.warning("strategy_signal artifact meta_json is not valid JSON; listing the run without it")
        return None
    if not isinstance(parsed, dict):
        return None
    return _as_count(parsed.get("universe_size"))


def _as_count(value: Any) -> int | None:
    """An integer column that may be absent. ``bool`` is not a count."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == value:
        return int(value)
    return None


def _as_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _as_date(value: Any) -> date | None:
    # ``pd.NaT`` is a ``datetime`` subclass, so it has to be rejected before the
    # isinstance checks or an absent date would come back as NaT rather than
    # None — and NaT would then reach the API as an unserialisable value.
    if value is None or value is pd.NaT or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _as_datetime(value: Any) -> datetime | None:
    if value is None or value is pd.NaT or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return None


def _query(store: DataStore, sql: str, params: list[Any], columns: list[str]) -> pd.DataFrame:
    """Run a read query, degrading to an empty frame (with a warning) on failure."""
    try:
        return store.conn.execute(sql, params).df()
    except Exception as e:
        logger.warning(f"strategy signal query failed: {e}")
        return pd.DataFrame(columns=columns)
