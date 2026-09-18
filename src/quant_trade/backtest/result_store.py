"""Persistence and querying of backtest results in DuckDB.

The four ``backtest_*`` tables are the single source of truth for backtest read
paths: the detail and comparison pages read them instead of re-running the
weekly loop, so opening a result never costs a minute of compute.

Everything here takes and returns plain pandas objects or dicts. The backtest
package must not import :mod:`quant_trade.services` — the service layer imports
this module, not the other way round — so the shaping from the service's result
dataclasses happens at the call site, and this module owns what the tables look
like.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd
from loguru import logger

from quant_trade.data.store import DataStore

NAV_COLUMNS: list[str] = ["run_id", "trade_date", "nav", "benchmark", "drawdown"]
TRADE_COLUMNS: list[str] = [
    "run_id",
    "seq",
    "trade_date",
    "action",
    "ts_code",
    "shares",
    "price",
    "commission",
    "stamp_duty",
    "transfer_fee",
]
METRIC_COLUMNS: list[str] = ["run_id", "metric_name", "metric_value"]
POSITION_COLUMNS: list[str] = ["run_id", "ts_code", "shares", "avg_cost", "current_price", "market_value"]

BACKTEST_KIND = "backtest"
"""The ``run.kind`` these result tables belong to."""

LIST_METRIC_NAMES: tuple[str, ...] = (
    "total_return",
    "annual_return",
    "max_drawdown",
    "sharpe_ratio",
    "calmar_ratio",
    "win_rate",
    "total_trades",
)
"""Metrics the run list shows per row. The detail page reads all of them."""

CASH_METRIC_NAME = "cash"
FINAL_VALUE_METRIC_NAME = "final_value"
"""End-of-run cash and total value.

They are not performance metrics — ``compute_metrics`` knows nothing about them
— but the closing portfolio state has to live somewhere, and the metric table is
the only key/value surface a run owns. Kept out of :data:`LIST_METRIC_NAMES`
because neither belongs on a run list row.
"""


@dataclass
class BacktestRows:
    """How many rows each result table received, for artifact registration."""

    nav: int = 0
    trades: int = 0
    metrics: int = 0
    positions: int = 0

    @property
    def total(self) -> int:
        """Every row written across the four tables."""
        return self.nav + self.trades + self.metrics + self.positions


@dataclass
class BacktestRunRow:
    """One row of the backtest run list.

    ``start`` / ``end`` come from the NAV series rather than the submitted
    parameters: the engine normalises the start date to the next trading day,
    so the requested window and the covered window are not the same thing.
    """

    run_id: str
    status: str
    strategy: str | None = None
    start: date | None = None
    end: date | None = None
    nav_points: int = 0
    progress: float = 0.0
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    metrics: dict[str, float] = field(default_factory=dict)


def build_nav_frame(nav: pd.Series, benchmark: pd.Series | None = None) -> pd.DataFrame:
    """Shape a NAV series (and its benchmark) into the ``backtest_nav`` columns.

    The benchmark is left-joined onto the strategy's own dates: index data can
    be shorter than the trading calendar, and dropping those days would cut a
    hole in a curve that is perfectly real. A missing benchmark day stays NaN,
    which DuckDB stores as NULL.
    """
    if nav.empty:
        return pd.DataFrame(columns=["trade_date", "nav", "benchmark", "drawdown"])

    frame = pd.DataFrame({"trade_date": list(nav.index), "nav": [float(v) for v in nav.to_numpy()]})
    if benchmark is not None and not benchmark.empty:
        aligned = benchmark.reindex(nav.index)
        frame["benchmark"] = [float(v) if v == v else None for v in aligned.to_numpy()]
    else:
        frame["benchmark"] = None

    running_peak = nav.cummax()
    drawdown = (nav - running_peak) / running_peak
    frame["drawdown"] = [float(v) if v == v else None for v in drawdown.to_numpy()]
    return frame


def build_trade_frame(trades: list[dict[str, Any]]) -> pd.DataFrame:
    """Shape the engine's trade log into the ``backtest_trade`` columns.

    The engine stores the trade date as a string; the column is a DATE. A row
    whose date cannot be parsed is dropped with a warning rather than failing
    the whole write — the rest of the run's trades are still worth keeping.

    ``seq`` is assigned after the drops, so it stays contiguous from 1: it is
    the trade's position in what was stored, not in what the engine produced.
    """
    rows: list[dict[str, Any]] = []
    for position, trade in enumerate(trades, start=1):
        parsed = _parse_date(trade.get("date"))
        if parsed is None:
            logger.warning(f"Skipping backtest trade #{position} with unparseable date {trade.get('date')!r}")
            continue
        rows.append(
            {
                "seq": len(rows) + 1,
                "trade_date": parsed,
                "action": str(trade.get("action", "")),
                "ts_code": str(trade.get("ts_code", "")),
                "shares": int(trade.get("shares", 0)),
                "price": float(trade.get("price", 0.0)),
                "commission": float(trade.get("commission", 0.0)),
                "stamp_duty": float(trade.get("stamp_duty", 0.0)),
                "transfer_fee": float(trade.get("transfer_fee", 0.0)),
            }
        )
    return pd.DataFrame(rows, columns=[c for c in TRADE_COLUMNS if c != "run_id"])


def build_metric_frame(metrics: dict[str, float]) -> pd.DataFrame:
    """Shape a metrics mapping into the ``backtest_metric`` key/value columns."""
    rows = [{"metric_name": str(name), "metric_value": float(value)} for name, value in metrics.items()]
    return pd.DataFrame(rows, columns=["metric_name", "metric_value"])


def build_position_frame(positions: list[dict[str, Any]]) -> pd.DataFrame:
    """Shape end-of-run holdings into the ``backtest_position`` columns."""
    rows = [
        {
            "ts_code": str(row.get("ts_code", "")),
            "shares": int(row.get("shares", 0)),
            "avg_cost": float(row.get("avg_cost", 0.0)),
            "current_price": float(row.get("current_price", 0.0)),
            "market_value": float(row.get("market_value", 0.0)),
        }
        for row in positions
    ]
    return pd.DataFrame(rows, columns=[c for c in POSITION_COLUMNS if c != "run_id"])


def save_backtest_result(
    store: DataStore,
    run_id: str,
    *,
    nav_frame: pd.DataFrame,
    trade_frame: pd.DataFrame,
    metric_frame: pd.DataFrame,
    position_frame: pd.DataFrame,
) -> BacktestRows:
    """Write one run's results to the four ``backtest_*`` tables.

    The primary keys make a rewrite idempotent: writing the same ``run_id``
    twice replaces its rows instead of duplicating them, which is what a
    cancelled run resumed by a retry needs.

    Args:
        store: DataStore with an open connection.
        run_id: The run these rows belong to; the key of every table.
        nav_frame / trade_frame / metric_frame / position_frame: Frames shaped
            by the ``build_*`` helpers above. Empty frames are skipped.

    Returns:
        Per-table row counts.
    """
    return BacktestRows(
        nav=_upsert(store, "backtest_nav", NAV_COLUMNS, nav_frame, run_id),
        trades=_upsert(store, "backtest_trade", TRADE_COLUMNS, trade_frame, run_id),
        metrics=_upsert(store, "backtest_metric", METRIC_COLUMNS, metric_frame, run_id),
        positions=_upsert(store, "backtest_position", POSITION_COLUMNS, position_frame, run_id),
    )


def _upsert(
    store: DataStore,
    table: str,
    columns: list[str],
    frame: pd.DataFrame,
    run_id: str,
) -> int:
    """Bulk-upsert one frame into one table. Returns the rows written.

    The insert selects positionally, so a frame missing a column would fail with
    a bare ``KeyError`` deep inside the select. Validate first and say which
    column is missing — same contract as ``save_ic_series``.
    """
    if frame.empty:
        return 0
    missing = [column for column in columns if column != "run_id" and column not in frame.columns]
    if missing:
        raise ValueError(f"{table} frame is missing columns {missing}")
    payload = frame.assign(run_id=run_id)[columns]
    relation = f"_backtest_{table}"
    store.conn.register(relation, payload)
    try:
        store.conn.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM {relation}")
    finally:
        store.conn.unregister(relation)
    return len(payload)


def get_backtest_nav(store: DataStore, run_id: str) -> pd.DataFrame:
    """The NAV / benchmark / drawdown series of one run, ordered by date."""
    return _query(
        store,
        f"SELECT {', '.join(NAV_COLUMNS)} FROM backtest_nav WHERE run_id = ? ORDER BY trade_date",
        [run_id],
        NAV_COLUMNS,
    )


def get_backtest_nav_many(store: DataStore, run_ids: list[str]) -> pd.DataFrame:
    """NAV series for several runs in one query, for the comparison page."""
    if not run_ids:
        return pd.DataFrame(columns=NAV_COLUMNS)
    placeholders = ", ".join(["?"] * len(run_ids))
    return _query(
        store,
        f"SELECT {', '.join(NAV_COLUMNS)} FROM backtest_nav "
        f"WHERE run_id IN ({placeholders}) ORDER BY run_id, trade_date",
        list(run_ids),
        NAV_COLUMNS,
    )


def get_backtest_trades(store: DataStore, run_id: str, limit: int, offset: int) -> pd.DataFrame:
    """One page of a run's trades, ordered by ``seq``."""
    return _query(
        store,
        f"SELECT {', '.join(TRADE_COLUMNS)} FROM backtest_trade WHERE run_id = ? ORDER BY seq LIMIT ? OFFSET ?",
        [run_id, limit, offset],
        TRADE_COLUMNS,
    )


def count_backtest_trades(store: DataStore, run_id: str) -> int:
    """How many trades a run has, for the trade table's pagination."""
    frame = _query(store, "SELECT COUNT(*) AS total FROM backtest_trade WHERE run_id = ?", [run_id], ["total"])
    return int(frame["total"].iloc[0]) if not frame.empty else 0


def get_backtest_metrics(store: DataStore, run_id: str) -> dict[str, float]:
    """A run's performance metrics, keyed by metric name."""
    frame = _query(
        store,
        f"SELECT {', '.join(METRIC_COLUMNS)} FROM backtest_metric WHERE run_id = ? ORDER BY metric_name",
        [run_id],
        METRIC_COLUMNS,
    )
    if frame.empty:
        return {}
    return {str(name): float(value) for name, value in zip(frame["metric_name"], frame["metric_value"], strict=True)}


def get_backtest_positions(store: DataStore, run_id: str) -> pd.DataFrame:
    """The end-of-run holdings of one run, largest market value first.

    ``ts_code`` breaks ties: two holdings of equal value have no defined order
    otherwise, so the table could reshuffle between two reads of the same run.
    """
    return _query(
        store,
        f"SELECT {', '.join(POSITION_COLUMNS)} FROM backtest_position "
        "WHERE run_id = ? ORDER BY market_value DESC, ts_code",
        [run_id],
        POSITION_COLUMNS,
    )


_RUN_PAGE_COLUMNS = [
    "run_id",
    "status",
    "params_json",
    "progress",
    "created_at",
    "started_at",
    "finished_at",
    "first_date",
    "last_date",
    "points",
]

_RUN_PAGE_SQL = """
    SELECT r.run_id       AS run_id,
           r.status       AS status,
           r.params_json  AS params_json,
           r.progress     AS progress,
           r.created_at   AS created_at,
           r.started_at   AS started_at,
           r.finished_at  AS finished_at,
           n.first_date   AS first_date,
           n.last_date    AS last_date,
           n.points       AS points
    FROM run r
    LEFT JOIN (
        SELECT run_id, MIN(trade_date) AS first_date, MAX(trade_date) AS last_date, COUNT(*) AS points
        FROM backtest_nav GROUP BY run_id
    ) n ON n.run_id = r.run_id
    WHERE r.kind = ?
"""


def get_backtest_runs(store: DataStore, run_ids: list[str]) -> dict[str, BacktestRunRow]:
    """Several runs' metadata in two queries, keyed by run id.

    The comparison view needs a row per run; asking one at a time would make it
    N+1 queries for something that is a single page's worth of data.
    """
    if not run_ids:
        return {}
    placeholders = ", ".join(["?"] * len(run_ids))
    frame = _query(
        store,
        f"{_RUN_PAGE_SQL} AND r.run_id IN ({placeholders})",
        [BACKTEST_KIND, *run_ids],
        _RUN_PAGE_COLUMNS,
    )
    if frame.empty:
        return {}
    records = frame.to_dict("records")
    metrics_by_run = _metrics_by_run(store, [str(record["run_id"]) for record in records])
    return {
        str(record["run_id"]): _row_from_record(record, metrics_by_run.get(str(record["run_id"]), {}))
        for record in records
    }


def get_backtest_run(store: DataStore, run_id: str) -> BacktestRunRow | None:
    """One backtest run's metadata, or ``None`` when no such backtest exists.

    A ``run_id`` belonging to another kind is not a backtest, so it reads back
    as absent rather than as a backtest with no results.
    """
    frame = _query(store, f"{_RUN_PAGE_SQL} AND r.run_id = ?", [BACKTEST_KIND, run_id], _RUN_PAGE_COLUMNS)
    if frame.empty:
        return None
    record = frame.to_dict("records")[0]
    metrics = _metrics_by_run(store, [run_id]).get(run_id, {})
    return _row_from_record(record, metrics)


def list_backtest_runs(store: DataStore, limit: int, offset: int) -> tuple[list[BacktestRunRow], int]:
    """One page of backtest runs, newest first, with their headline metrics.

    Reads the ``run`` table to learn each run's status and timestamps but never
    writes it: ``run`` rows belong to :class:`~quant_trade.runs.store.RunStore`.
    The join to ``backtest_nav`` is a LEFT join so a run that produced no NAV
    (an empty window, a failure) still appears — it happened, and hiding it
    would make a submitted run look like it was never accepted.
    """
    # Ordered by submission time, which is the column the list shows. Ordering
    # by ``started_at`` would disagree with the visible timestamp for any run
    # that waited in the queue.
    page = _query(
        store,
        f"{_RUN_PAGE_SQL} ORDER BY r.created_at DESC, r.run_id DESC LIMIT ? OFFSET ?",
        [BACKTEST_KIND, limit, offset],
        _RUN_PAGE_COLUMNS,
    )
    totals = _query(store, "SELECT COUNT(*) AS total FROM run WHERE kind = ?", [BACKTEST_KIND], ["total"])
    total = int(totals["total"].iloc[0]) if not totals.empty else 0
    if page.empty:
        return [], total

    records = page.to_dict("records")
    metrics_by_run = _metrics_by_run(store, [str(record["run_id"]) for record in records])
    rows = [
        _row_from_record(record, metrics_by_run.get(str(record["run_id"]), {}), only_listed_metrics=True)
        for record in records
    ]
    return rows, total


def _row_from_record(
    record: dict[Any, Any],
    metrics: dict[str, float],
    *,
    only_listed_metrics: bool = False,
) -> BacktestRunRow:
    """Build a :class:`BacktestRunRow` from one joined run/nav record."""
    if only_listed_metrics:
        metrics = {name: value for name, value in metrics.items() if name in LIST_METRIC_NAMES}
    return BacktestRunRow(
        run_id=str(record["run_id"]),
        status=str(record["status"]),
        strategy=_strategy_from_params(record.get("params_json")),
        start=_as_date(record.get("first_date")),
        end=_as_date(record.get("last_date")),
        nav_points=int(record["points"] or 0),
        progress=float(record.get("progress") or 0.0),
        created_at=_as_datetime(record.get("created_at")),
        started_at=_as_datetime(record.get("started_at")),
        finished_at=_as_datetime(record.get("finished_at")),
        metrics=metrics,
    )


def _metrics_by_run(store: DataStore, run_ids: list[str]) -> dict[str, dict[str, float]]:
    """Every metric of every given run, in one query rather than one per run."""
    if not run_ids:
        return {}
    placeholders = ", ".join(["?"] * len(run_ids))
    frame = _query(
        store,
        f"SELECT run_id, metric_name, metric_value FROM backtest_metric WHERE run_id IN ({placeholders})",
        list(run_ids),
        ["run_id", "metric_name", "metric_value"],
    )
    grouped: dict[str, dict[str, float]] = {}
    for record in frame.to_dict("records"):
        grouped.setdefault(str(record["run_id"]), {})[str(record["metric_name"])] = float(record["metric_value"])
    return grouped


def _query(store: DataStore, sql: str, params: list[Any], columns: list[str]) -> pd.DataFrame:
    """Run a read query, degrading to an empty frame (with a warning) on failure."""
    try:
        return store.conn.execute(sql, params).df()
    except Exception as e:
        logger.warning(f"backtest result query failed: {e}")
        return pd.DataFrame(columns=columns)


def _strategy_from_params(params_json: Any) -> str | None:
    """The strategy an earlier run was submitted with, if it is still legible.

    ``params_json`` is our own dump, so this only fails for a row written before
    the field existed — in which case there is nothing useful to show.
    """
    if not isinstance(params_json, str):
        return None
    try:
        params = json.loads(params_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(params, dict):
        return None
    strategy = params.get("strategy")
    return str(strategy) if strategy else None


def _parse_date(value: Any) -> date | None:
    """Parse an engine trade date, which is an ISO string, into a ``date``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _as_date(value: Any) -> date | None:
    """Normalise a DuckDB date result to a plain ``date``.

    ``pandas.NaT`` reaches the ``isinstance`` branch below — it subclasses
    ``datetime`` — so it has to be filtered first or it survives as a date and
    reports "NaT" everywhere downstream.
    """
    if value is None or value is pd.NaT or (isinstance(value, float) and value != value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _as_datetime(value: Any) -> datetime | None:
    """Normalise a DuckDB timestamp result to a plain ``datetime``."""
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return None
