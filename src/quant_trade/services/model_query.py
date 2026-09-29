"""Model-domain read services — training history and evaluation of one run.

These are the only entry points the model pages use to read results. They read
the ``model_*`` tables rather than retraining: a walk-forward pass costs minutes,
and the numbers it produced are already stored.

Aggregation lives here rather than in :mod:`quant_trade.api.models` because an
adapter that shapes domain values is domain logic by another name — the
``service-layer`` contract puts it behind a service function.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from pydantic import Field

from quant_trade.models.persistence import (
    ModelRunRow,
    count_model_importance,
    get_model_ic_series,
    get_model_importance,
    get_model_metrics,
    get_model_run,
    list_model_runs,
)
from quant_trade.services.context import NULL_CONTEXT, RunContext
from quant_trade.services.params import ServiceParams

DEFAULT_IMPORTANCE_TOP_N = 20
"""How many factors the evaluation returns by default. Enough to read a chart,
small enough that a 158-factor run does not ship its whole table to draw 20 bars."""

MAX_IMPORTANCE_TOP_N = 200


class ModelRunListParams(ServiceParams):
    """One page of training history."""

    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class ModelEvaluationParams(ServiceParams):
    """Which training run to evaluate, and how much importance to return."""

    run_id: str = Field(min_length=1)
    importance_top_n: int = Field(default=DEFAULT_IMPORTANCE_TOP_N, ge=1, le=MAX_IMPORTANCE_TOP_N)


@dataclass
class ModelRunSummary:
    """One training run as the history list renders it.

    ``start`` / ``end`` are the window the IC series actually covers, not the
    one that was requested — an unspecified range resolves to a default history
    start and the latest trade date, so the two are not the same thing.
    """

    run_id: str
    status: str
    progress: float = 0.0
    """The only number on a row that moves while the run is still going: every
    other one is written when it finishes."""
    message: str = ""
    start: date | None = None
    end: date | None = None
    requested_start: date | None = None
    requested_end: date | None = None
    factor_count: int | None = None
    ic_days: int = 0
    windows_trained: int = 0
    prediction_rows: int = 0
    ic_mean: float | None = None
    ic_ir: float | None = None
    ic_positive_ratio: float | None = None
    created_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass
class ModelRunListResult:
    """A page of training runs plus the total count behind it."""

    total: int
    runs: list[ModelRunSummary] = field(default_factory=list)


@dataclass
class ModelICPoint:
    """One prediction day's RankIC."""

    trade_date: date
    rank_ic: float


@dataclass
class ModelYearSummary:
    """One natural year of the IC series.

    The gauge is the rank correlation, not a portfolio return: what a model's
    picks would have earned is a strategy question, answered by running the
    ``model_ranking`` strategy through the backtest engine. Re-deriving it here
    would give the platform two different numbers called "annual".
    """

    year: int
    ic_mean: float
    ic_ir: float | None
    ic_positive_ratio: float
    days: int


@dataclass
class ModelImportanceEntry:
    """One factor's averaged importance across walk-forward windows."""

    factor: str
    importance: float
    std: float


@dataclass
class ModelEvaluationResult:
    """Everything the evaluation page shows for one training run.

    ``found`` is false when the run id names no training run — a run of another
    kind, or one that never existed. The API layer turns that into a 404 rather
    than rendering a page of zeros for a model that was never trained.
    """

    run_id: str
    found: bool = False
    status: str = ""
    start: date | None = None
    end: date | None = None
    finished_at: datetime | None = None
    ic_mean: float | None = None
    ic_ir: float | None = None
    ic_positive_ratio: float | None = None
    ic_days: int = 0
    windows_trained: int = 0
    prediction_rows: int = 0
    ic_series: list[ModelICPoint] = field(default_factory=list)
    yearly: list[ModelYearSummary] = field(default_factory=list)
    importance: list[ModelImportanceEntry] = field(default_factory=list)
    importance_total: int = 0


def model_run_list(params: ModelRunListParams, ctx: RunContext = NULL_CONTEXT) -> ModelRunListResult:
    """Training history, newest first, with each run's headline numbers."""
    rows, total = list_model_runs(ctx.db, limit=params.limit, offset=params.offset)
    return ModelRunListResult(total=total, runs=[_summary(row) for row in rows])


def model_evaluation(params: ModelEvaluationParams, ctx: RunContext = NULL_CONTEXT) -> ModelEvaluationResult:
    """One training run's evaluation: metrics, IC series, yearly rollup, importances."""
    store = ctx.db
    row = get_model_run(store, params.run_id)
    if row is None:
        return ModelEvaluationResult(run_id=params.run_id, found=False)

    metrics = get_model_metrics(store, params.run_id)
    ic_frame = get_model_ic_series(store, params.run_id)
    points = [
        ModelICPoint(trade_date=trade_date, rank_ic=float(record["rank_ic"]))
        for record in ic_frame.to_dict("records")
        if (trade_date := _as_date(record["trade_date"])) is not None
    ]

    importance_frame = get_model_importance(store, params.run_id, limit=params.importance_top_n)
    importance = [
        ModelImportanceEntry(
            factor=str(record["factor"]),
            importance=float(record["importance"]),
            std=float(record["std"]),
        )
        for record in importance_frame.to_dict("records")
    ]

    return ModelEvaluationResult(
        run_id=params.run_id,
        found=True,
        status=row.status,
        start=row.start,
        end=row.end,
        finished_at=row.finished_at,
        ic_mean=metrics.get("ic_mean"),
        ic_ir=metrics.get("ic_ir"),
        ic_positive_ratio=metrics.get("ic_positive_ratio"),
        ic_days=row.ic_days,
        windows_trained=row.windows_trained,
        prediction_rows=row.prediction_rows,
        ic_series=points,
        yearly=_yearly(points),
        importance=importance,
        importance_total=count_model_importance(store, params.run_id),
    )


def _summary(row: ModelRunRow) -> ModelRunSummary:
    """A stored run row as the list page's shape."""
    return ModelRunSummary(
        run_id=row.run_id,
        status=row.status,
        progress=row.progress,
        message=row.message,
        start=row.start,
        end=row.end,
        requested_start=_as_date(row.params.get("start")),
        requested_end=_as_date(row.params.get("end")),
        factor_count=_factor_count(row.params),
        ic_days=row.ic_days,
        windows_trained=row.windows_trained,
        prediction_rows=row.prediction_rows,
        ic_mean=row.metrics.get("ic_mean"),
        ic_ir=row.metrics.get("ic_ir"),
        ic_positive_ratio=row.metrics.get("ic_positive_ratio"),
        created_at=row.created_at,
        finished_at=row.finished_at,
    )


def _yearly(points: list[ModelICPoint]) -> list[ModelYearSummary]:
    """Group the daily IC series by calendar year.

    Recomputed from the stored series rather than stored per year: the year
    boundaries follow the series, so a run that spans a year end needs no
    bookkeeping, and there is no second copy to drift from the first.
    """
    by_year: dict[int, list[float]] = {}
    for point in points:
        by_year.setdefault(point.trade_date.year, []).append(point.rank_ic)

    summaries: list[ModelYearSummary] = []
    for year in sorted(by_year):
        values = by_year[year]
        mean = statistics.fmean(values)
        deviation = statistics.stdev(values) if len(values) > 1 else 0.0
        summaries.append(
            ModelYearSummary(
                year=year,
                ic_mean=mean,
                ic_ir=(mean / deviation) if deviation > 0 else None,
                ic_positive_ratio=sum(1 for value in values if value > 0) / len(values),
                days=len(values),
            )
        )
    return summaries


def _factor_count(params: dict[str, Any]) -> int | None:
    """How many factors the run asked for; ``None`` when it took the default set."""
    factors = params.get("factors")
    return len(factors) if isinstance(factors, list) else None


def _as_date(value: Any) -> date | None:
    """A ``date`` from a parameter string or a stored value, or ``None``."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None
