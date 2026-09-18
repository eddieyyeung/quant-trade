"""Persistence and querying of model evaluation results in DuckDB.

The three ``model_*`` tables are the single source of truth for model read
paths: the evaluation page reads them instead of re-running a walk-forward
training pass, so opening a past run costs a query rather than minutes of
LightGBM.

Model IC lives in its own table rather than in the factor domain's
``ic_series``. That table is keyed by ``factor_name`` and a trained model is not
a factor; reusing it would mean writing ``model:<run_id>`` into the key and
parsing it back out on every read.

Everything here takes and returns plain pandas objects or dicts. The models
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
from quant_trade.runs.models import ArtifactStorage, RunStatus

MODEL_KIND = "model_train"
"""The run kind these tables hold results for."""

IC_COLUMNS: list[str] = ["run_id", "trade_date", "rank_ic"]
"""Column order of ``model_ic_series``, used for bulk inserts."""

IMPORTANCE_COLUMNS: list[str] = ["run_id", "factor", "importance", "std"]
"""Column order of ``model_feature_importance``."""

METRIC_COLUMNS: list[str] = ["run_id", "metric_name", "metric_value"]
"""Column order of ``model_metric``."""

LIST_METRIC_NAMES = ("ic_mean", "ic_ir", "ic_positive_ratio", "ic_days", "windows_trained", "prediction_rows")
"""Metrics a run list row carries. A key/value table can hold anything, so the
list view names the handful it actually renders instead of forwarding the lot."""


@dataclass
class ModelRows:
    """How many rows each result table received, for artifact registration."""

    ic: int = 0
    importance: int = 0
    metrics: int = 0

    @property
    def total(self) -> int:
        """Every row written across the three tables."""
        return self.ic + self.importance + self.metrics


@dataclass
class PredictionSource:
    """Which training run wrote the predictions file a caller is about to read.

    Predictions are keyed by path, not by run: a later training overwrites the
    file. Whoever reads it therefore has to say which training produced it, or
    the page shows one run's picks under another run's name.
    """

    run_id: str
    path: str
    finished_at: datetime | None = None


@dataclass
class ModelRunRow:
    """One row of the training run list.

    ``start`` / ``end`` come from the IC series rather than the submitted
    parameters: an unspecified range is resolved to a default history start and
    the latest trade date, and only days that produced a rankable prediction
    land in the table. The covered window is the honest one.
    """

    run_id: str
    status: str
    progress: float = 0.0
    """How far the run has got. Every other number here is written when the run
    finishes, so for a run still going this is the only one that moves."""
    message: str = ""
    """The run's own line about where it is — "training window 3/8"."""
    start: date | None = None
    end: date | None = None
    ic_days: int = 0
    windows_trained: int = 0
    prediction_rows: int = 0
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)


def build_ic_frame(run_id: str, points: list[tuple[date, float]]) -> pd.DataFrame:
    """Shape ``(date, rank_ic)`` pairs into the ``model_ic_series`` columns."""
    return pd.DataFrame(
        [(run_id, trade_date, float(rank_ic)) for trade_date, rank_ic in points],
        columns=IC_COLUMNS,
    )


def build_importance_frame(run_id: str, importance: pd.DataFrame) -> pd.DataFrame:
    """Shape a ``factor / importance / std`` frame into the table's columns.

    An empty input frame (a run whose every window failed to train) yields an
    empty output frame, which the writer skips rather than inserting nothing.
    """
    if importance.empty:
        return pd.DataFrame(columns=IMPORTANCE_COLUMNS)
    missing = [column for column in ("factor", "importance", "std") if column not in importance.columns]
    if missing:
        raise ValueError(f"importance frame is missing columns {missing}")
    frame = importance[["factor", "importance", "std"]].copy()
    frame.insert(0, "run_id", run_id)
    return frame[IMPORTANCE_COLUMNS]


def build_metric_frame(run_id: str, metrics: dict[str, float]) -> pd.DataFrame:
    """Shape a metric dict into the key/value columns."""
    return pd.DataFrame(
        [(run_id, name, float(value)) for name, value in metrics.items()],
        columns=METRIC_COLUMNS,
    )


def save_model_evaluation(
    store: DataStore,
    run_id: str,
    ic_frame: pd.DataFrame,
    importance_frame: pd.DataFrame,
    metric_frame: pd.DataFrame,
) -> ModelRows:
    """Write one run's evaluation results, replacing any rows it already has.

    The primary keys all start with ``run_id``, so re-running a write for the
    same run overwrites that run's rows instead of duplicating them. A different
    run is a different key, so nothing is ever clobbered across runs.

    Empty frames are skipped and reported as zero rows — a run that produced no
    IC has nothing to say, and a row saying nothing would be worse.

    Args:
        store: DataStore with an open connection.
        run_id: The run these results belong to.
        ic_frame / importance_frame / metric_frame: Frames in the column orders
            of :data:`IC_COLUMNS` / :data:`IMPORTANCE_COLUMNS` /
            :data:`METRIC_COLUMNS`.

    Returns:
        Row counts per table.
    """
    return ModelRows(
        ic=_upsert(store, "model_ic_series", ic_frame, IC_COLUMNS),
        importance=_upsert(store, "model_feature_importance", importance_frame, IMPORTANCE_COLUMNS),
        metrics=_upsert(store, "model_metric", metric_frame, METRIC_COLUMNS),
    )


def _upsert(store: DataStore, table: str, frame: pd.DataFrame, columns: list[str]) -> int:
    """Bulk-insert one frame into one table, registering it as a relation.

    ``executemany`` would be orders of magnitude slower for the thousands of IC
    rows a multi-year run produces, so the frame is registered and inserted in
    one statement. The table name is never caller-supplied.
    """
    if frame.empty:
        return 0
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{table} frame is missing columns {missing}")
    store.conn.register("model_upload", frame[columns])
    try:
        store.conn.execute(f"INSERT OR REPLACE INTO {table} SELECT * FROM model_upload")
    finally:
        store.conn.unregister("model_upload")
    return len(frame)


def get_model_ic_series(store: DataStore, run_id: str) -> pd.DataFrame:
    """A run's daily RankIC, oldest first."""
    frame = _query(
        store,
        "SELECT run_id, trade_date, rank_ic FROM model_ic_series WHERE run_id = ? ORDER BY trade_date",
        [run_id],
        IC_COLUMNS,
    )
    return _with_dates(frame, ["trade_date"])


def get_model_importance(store: DataStore, run_id: str, limit: int | None = None) -> pd.DataFrame:
    """A run's feature importances, highest mean importance first.

    ``limit`` truncates in SQL rather than in the caller: an Alpha158 run has
    one row per factor, and the page draws a top-N chart.
    """
    sql = (
        "SELECT run_id, factor, importance, std FROM model_feature_importance WHERE run_id = ? ORDER BY importance DESC"
    )
    params: list[Any] = [run_id]
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return _query(store, sql, params, IMPORTANCE_COLUMNS)


def count_model_importance(store: DataStore, run_id: str) -> int:
    """How many factors the run ranked, regardless of how many are returned.

    A truncated read cannot report this, and "top 20 of 158" needs the 158.
    """
    frame = _query(
        store,
        "SELECT COUNT(*) AS total FROM model_feature_importance WHERE run_id = ?",
        [run_id],
        ["total"],
    )
    return int(frame["total"].iloc[0]) if not frame.empty else 0


def get_model_metrics(store: DataStore, run_id: str) -> dict[str, float]:
    """A run's summary metrics as a plain dict."""
    return _metrics_by_run(store, [run_id]).get(run_id, {})


_RUN_PAGE_COLUMNS = [
    "run_id",
    "status",
    "progress",
    "message",
    "params_json",
    "created_at",
    "started_at",
    "finished_at",
    "first_date",
    "last_date",
    "ic_days",
]

_RUN_PAGE_SQL = """
    SELECT r.run_id       AS run_id,
           r.status       AS status,
           r.progress     AS progress,
           r.message      AS message,
           r.params_json  AS params_json,
           r.created_at   AS created_at,
           r.started_at   AS started_at,
           r.finished_at  AS finished_at,
           i.first_date   AS first_date,
           i.last_date    AS last_date,
           i.ic_days      AS ic_days
    FROM run r
    LEFT JOIN (
        SELECT run_id, MIN(trade_date) AS first_date, MAX(trade_date) AS last_date, COUNT(*) AS ic_days
        FROM model_ic_series GROUP BY run_id
    ) i ON i.run_id = r.run_id
    WHERE r.kind = ?
"""


def get_model_run(store: DataStore, run_id: str) -> ModelRunRow | None:
    """One training run's metadata, or ``None`` when no such run exists.

    A ``run_id`` belonging to another kind is not a training run, so it reads
    back as absent rather than as a training run with no results.
    """
    page = _query(store, f"{_RUN_PAGE_SQL} AND r.run_id = ?", [MODEL_KIND, run_id], _RUN_PAGE_COLUMNS)
    if page.empty:
        return None
    metrics = _metrics_by_run(store, [run_id]).get(run_id, {})
    return _row_from_record(page.to_dict("records")[0], metrics)


def list_model_runs(store: DataStore, limit: int, offset: int) -> tuple[list[ModelRunRow], int]:
    """One page of training runs, newest first, with their headline metrics.

    Reads the ``run`` table to learn each run's status and timestamps but never
    writes it: ``run`` rows belong to :class:`~quant_trade.runs.store.RunStore`.
    The join to ``model_ic_series`` is a LEFT join so a run that produced no IC
    (no trainable window, a failure) still appears — it happened, and hiding it
    would make a submitted run look like it was never accepted.
    """
    page = _query(
        store,
        f"{_RUN_PAGE_SQL} ORDER BY COALESCE(r.started_at, r.created_at) DESC, r.run_id DESC LIMIT ? OFFSET ?",
        [MODEL_KIND, limit, offset],
        _RUN_PAGE_COLUMNS,
    )
    totals = _query(store, "SELECT COUNT(*) AS total FROM run WHERE kind = ?", [MODEL_KIND], ["total"])
    total = int(totals["total"].iloc[0]) if not totals.empty else 0
    if page.empty:
        return [], total

    records = page.to_dict("records")
    metrics_by_run = _metrics_by_run(store, [str(record["run_id"]) for record in records])
    return [_row_from_record(record, metrics_by_run.get(str(record["run_id"]), {})) for record in records], total


def get_latest_prediction_source(store: DataStore) -> PredictionSource | None:
    """The predictions file written by the newest successful training run.

    Read from the artifact index rather than taken from the caller: a
    client-supplied path would let any request read any parquet file on the
    machine, and the honest answer to "which predictions should I show" is
    already recorded — the newest run of this kind that finished.
    """
    frame = _query(
        store,
        """
        SELECT a.run_id AS run_id, a.ref AS path, r.finished_at AS finished_at
        FROM artifact a
        JOIN run r ON r.run_id = a.run_id
        WHERE a.storage = ? AND r.kind = ? AND r.status = ?
        ORDER BY COALESCE(r.finished_at, r.created_at) DESC, r.run_id DESC
        LIMIT 1
        """,
        [ArtifactStorage.PARQUET.value, MODEL_KIND, RunStatus.OK.value],
        ["run_id", "path", "finished_at"],
    )
    if frame.empty:
        return None
    record = frame.to_dict("records")[0]
    return PredictionSource(
        run_id=str(record["run_id"]),
        path=str(record["path"]),
        finished_at=_as_datetime(record.get("finished_at")),
    )


def _row_from_record(record: dict[Any, Any], metrics: dict[str, float]) -> ModelRunRow:
    """Build a :class:`ModelRunRow` from one joined run/IC record."""
    listed = {name: value for name, value in metrics.items() if name in LIST_METRIC_NAMES}
    return ModelRunRow(
        run_id=str(record["run_id"]),
        status=str(record["status"]),
        progress=float(record["progress"] or 0.0),
        message=str(record.get("message") or ""),
        start=_as_date(record.get("first_date")),
        end=_as_date(record.get("last_date")),
        ic_days=int(record["ic_days"] or 0),
        windows_trained=int(listed.get("windows_trained", 0)),
        prediction_rows=int(listed.get("prediction_rows", 0)),
        created_at=_as_datetime(record.get("created_at")),
        started_at=_as_datetime(record.get("started_at")),
        finished_at=_as_datetime(record.get("finished_at")),
        metrics=listed,
        params=_params_from_json(record.get("params_json")),
    )


def _metrics_by_run(store: DataStore, run_ids: list[str]) -> dict[str, dict[str, float]]:
    """Every metric of every given run, in one query rather than one per run."""
    if not run_ids:
        return {}
    placeholders = ", ".join(["?"] * len(run_ids))
    frame = _query(
        store,
        f"SELECT run_id, metric_name, metric_value FROM model_metric WHERE run_id IN ({placeholders})",
        list(run_ids),
        METRIC_COLUMNS,
    )
    grouped: dict[str, dict[str, float]] = {}
    for record in frame.to_dict("records"):
        grouped.setdefault(str(record["run_id"]), {})[str(record["metric_name"])] = float(record["metric_value"])
    return grouped


def _query(store: DataStore, sql: str, params: list[Any], columns: list[str]) -> pd.DataFrame:
    """Run a read query, degrading to an empty frame (with a warning) on failure."""
    try:
        return store.conn.execute(sql, params).df()
    except Exception as e:  # noqa: BLE001 - a read path must not take the page down
        logger.warning(f"model result query failed: {e}")
        return pd.DataFrame(columns=columns)


def _with_dates(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Normalize date columns so callers compare ``date`` objects, not Timestamps."""
    for column in columns:
        if not frame.empty and column in frame.columns:
            frame[column] = pd.to_datetime(frame[column]).dt.date
    return frame


def _params_from_json(params_json: Any) -> dict[str, Any]:
    """The submitted params as a dict, or empty when the blob is unreadable."""
    if not isinstance(params_json, str):
        return {}
    try:
        loaded = json.loads(params_json)
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _as_date(value: Any) -> date | None:
    """A ``date`` from whatever DuckDB handed back, or ``None``.

    A NULL date coming out of a LEFT JOIN arrives as ``pd.NaT``, which is an
    instance of ``datetime`` — so the missing check has to come first, or a
    NaT flows through ``.date()`` and out as a value no caller expects.
    """
    if _is_missing(value):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _as_datetime(value: Any) -> datetime | None:
    """A ``datetime`` from whatever DuckDB handed back, or ``None``."""
    if _is_missing(value):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _is_missing(value: Any) -> bool:
    """True for ``None``, ``NaN`` and ``NaT`` — the three shapes of "no value"."""
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):  # pragma: no cover - pd.isna on an exotic scalar
        return False
