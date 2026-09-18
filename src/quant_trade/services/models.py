"""Model-domain services — walk-forward training and prediction lookup."""

from __future__ import annotations

import numbers
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from quant_trade.data.index_weights import get_default_universe
from quant_trade.data.store import DataStore
from quant_trade.models import (
    TrainConfig,
    WalkForwardResult,
    load_predictions,
    rank_ic_series,
    save_predictions,
    walk_forward_train,
)
from quant_trade.models.persistence import (
    ModelRows,
    build_ic_frame,
    build_importance_frame,
    build_metric_frame,
    save_model_evaluation,
)
from quant_trade.models.train import DEFAULT_PARAMS
from quant_trade.services.context import NULL_CONTEXT, RunContext
from quant_trade.services.data import DEFAULT_HISTORY_START
from quant_trade.services.params import ServiceParams

DEFAULT_PREDICTIONS_PATH = "data/predictions/model_ranking.parquet"


class LightGBMParams(BaseModel):
    """The LightGBM knobs a research run may vary.

    Deliberately an explicit field list rather than a ``dict[str, Any]`` passed
    through to ``LGBMRegressor(**params)``. Arbitrary keyword passthrough would
    bypass ``ServiceParams`` validation and let a caller move ``objective`` or
    ``metric``, which are link contracts — the label is a regression target and
    the evaluation is RankIC. Server-side, unexposed keys keep their defaults.
    """

    model_config = ConfigDict(extra="forbid")

    num_leaves: int | None = Field(default=None, ge=2, le=4096)
    min_data_in_leaf: int | None = Field(default=None, ge=1)
    learning_rate: float | None = Field(default=None, gt=0, le=1)
    feature_fraction: float | None = Field(default=None, gt=0, le=1)
    bagging_fraction: float | None = Field(default=None, gt=0, le=1)
    bagging_freq: int | None = Field(default=None, ge=0)


class TrainParams(ServiceParams):
    """Walk-forward training window, features, model hyperparameters and output location."""

    start: date | None = None
    """First signal date. ``None`` means the default history start."""
    end: date | None = None
    """Last signal date. ``None`` means the latest trade date."""
    factors: list[str] | None = None
    """Feature subset. ``None`` means whatever the feature builder defaults to."""
    output_path: str = DEFAULT_PREDICTIONS_PATH
    universe: list[str] | None = None
    train_years: float | None = Field(default=None, gt=0, le=30)
    """Training window length. ``None`` keeps the engine default."""
    valid_years: float | None = Field(default=None, gt=0, le=10)
    """Validation tail inside each training window. ``None`` keeps the default."""
    predict_months: int | None = Field(default=None, ge=1, le=36)
    """How far each trained model predicts before the next window. ``None`` keeps the default."""
    early_stopping: int | None = Field(default=None, ge=1)
    """Early-stopping patience in boosting rounds. ``None`` keeps the default."""
    num_boost_round: int | None = Field(default=None, ge=1)
    """Upper bound on boosting rounds per window. ``None`` keeps the default."""
    lgb_params: LightGBMParams | None = None
    """LightGBM overrides. ``None`` or an unset field keeps the engine default."""


class PredictParams(ServiceParams):
    """Where to read predictions from and which date to show."""

    as_of: date | None = None
    """Prediction date. ``None`` means the latest trade date."""
    top_n: int = 15
    predictions_path: str = DEFAULT_PREDICTIONS_PATH
    universe: list[str] | None = None


@dataclass
class FeatureImportance:
    """One averaged feature importance entry."""

    factor: str
    importance: float
    std: float


@dataclass
class TrainResult:
    """Outcome of a walk-forward training run."""

    start: date
    end: date
    output_path: str
    prediction_rows: int
    windows_trained: int
    feature_importance: list[FeatureImportance] = field(default_factory=list)
    ic_mean: float | None = None
    ic_ir: float | None = None
    ic_positive_ratio: float | None = None
    cancelled: bool = False
    ic_series: list[tuple[date, float]] = field(default_factory=list)
    """Daily RankIC, oldest first. Kept alongside the summary so callers do not
    have to recompute the series the training already produced."""
    ic_days: int = 0
    """How many prediction days had enough samples to rank."""
    rows_saved: ModelRows = field(default_factory=ModelRows)
    """Rows written to the model result tables. All zero under ``NULL_CONTEXT``."""


@dataclass
class PredictionRow:
    """One stock's score for one date."""

    ts_code: str
    score: float


@dataclass
class PredictResult:
    """One date's predictions, read back from the persisted predictions file.

    ``scores`` holds every scored stock — the prediction page draws a
    distribution over all of them — and ``picks`` is its head, so the table and
    the chart can never disagree about what was predicted.
    """

    as_of: date
    predictions_path: str
    top_n: int = 15
    scores: list[PredictionRow] = field(default_factory=list)
    total_scored: int = 0
    available_dates: list[date] = field(default_factory=list)
    """Every date the file holds predictions for. Read from the same frame as
    ``scores``, so the picker cannot offer a date the table cannot fill."""

    @property
    def picks(self) -> list[PredictionRow]:
        """The ``top_n`` highest scores, in descending order."""
        return self.scores[: self.top_n]


def train_model(params: TrainParams, ctx: RunContext = NULL_CONTEXT) -> TrainResult:
    """Train walk-forward, persist predictions and report RankIC and importances.

    When ``ctx.run_id`` is set — a run executed by the job worker — the daily
    RankIC series, the feature importances and the summary metrics are also
    written to the model result tables, so the evaluation page can read a past
    training without repeating it.

    Under the default :data:`~quant_trade.services.context.NULL_CONTEXT` the run
    id is an empty string and **nothing is persisted**: two script calls would
    otherwise write the same empty key and the second would silently overwrite
    the first, leaving two experiments sharing one set of rows. Callers that
    need results stored must pass a context carrying a run id.
    """
    store = ctx.db
    end = params.end or store.get_latest_trade_date()
    if end is None:
        raise ValueError("Database has no trade dates; run a data sync first")
    start = params.start or DEFAULT_HISTORY_START
    universe = params.universe or store.get_universe(get_default_universe(), end)

    ctx.progress(0.0, f"Walk-forward training {start} → {end} on {len(universe)} stocks")
    result: WalkForwardResult = walk_forward_train(
        store, universe, start, end, factors=params.factors, config=_train_config(params), ctx=ctx
    )

    if result.predictions.empty:
        ctx.log("Training produced no predictions", level="warning")
        return TrainResult(
            start=start,
            end=end,
            output_path=params.output_path,
            prediction_rows=0,
            windows_trained=result.windows_trained,
            cancelled=result.cancelled,
        )

    ctx.progress(0.9, "Persisting predictions")
    save_predictions(result.predictions, params.output_path)

    ic = rank_ic_series(store, universe, result.predictions, start, end)
    ic_points = _ic_points(ic)
    # A cancelled run stops mid-window: its remaining work never happened, so
    # reporting 100% would show a task as finished that is not.
    if not result.cancelled:
        ctx.progress(1.0, f"Saved {len(result.predictions)} predictions")

    trained = TrainResult(
        start=start,
        end=end,
        output_path=params.output_path,
        prediction_rows=len(result.predictions),
        windows_trained=result.windows_trained,
        feature_importance=[
            FeatureImportance(factor=str(row.factor), importance=_to_float(row.importance), std=_to_float(row.std))
            for row in result.feature_importance.itertuples(index=False)
        ],
        ic_mean=_as_float(ic.get("ic_mean")),
        ic_ir=_as_float(ic.get("ic_ir")),
        ic_positive_ratio=_as_float(ic.get("ic_positive_ratio")),
        cancelled=result.cancelled,
        ic_series=ic_points,
        ic_days=len(ic_points),
    )

    if ctx.run_id:
        trained.rows_saved = _save_evaluation(store, ctx.run_id, result, trained)
    return trained


def _train_config(params: TrainParams) -> TrainConfig | None:
    """Overlay the parameter object's window and LightGBM settings on the defaults.

    Returns ``None`` when nothing was overridden, so the caller walks the
    default path unchanged. Exposed keys only: an unset field keeps the engine's
    own default rather than being written as an explicit value.
    """
    window = {
        name: value
        for name in ("train_years", "valid_years", "predict_months", "early_stopping", "num_boost_round")
        if (value := getattr(params, name)) is not None
    }
    overrides = params.lgb_params.model_dump(exclude_none=True) if params.lgb_params is not None else {}
    if not window and not overrides:
        return None

    config = TrainConfig(**window)
    config.params = {**DEFAULT_PARAMS, **overrides}
    return config


def _save_evaluation(store: DataStore, run_id: str, result: WalkForwardResult, trained: TrainResult) -> ModelRows:
    """Write one training run's evaluation results.

    Metrics that came back undefined (NaN — too few ranked days to form an
    IC_IR) are dropped rather than written as NaN: the table reads back "no
    value" as NULL, and a NaN would render as a number the model never earned.
    """
    metrics = {
        "ic_mean": trained.ic_mean,
        "ic_ir": trained.ic_ir,
        "ic_positive_ratio": trained.ic_positive_ratio,
        "ic_days": float(trained.ic_days),
        "prediction_rows": float(trained.prediction_rows),
        "windows_trained": float(trained.windows_trained),
    }
    defined = {name: value for name, value in metrics.items() if value is not None}
    return save_model_evaluation(
        store,
        run_id,
        build_ic_frame(run_id, trained.ic_series),
        build_importance_frame(run_id, result.feature_importance),
        build_metric_frame(run_id, defined),
    )


def _ic_points(ic: dict[str, object]) -> list[tuple[date, float]]:
    """The daily RankIC series out of :func:`rank_ic_series`'s result dict."""
    series = ic.get("ic_series")
    if not isinstance(series, list):
        return []
    return [(trade_date, float(rank_ic)) for trade_date, rank_ic in series]


def predict_for_date(params: PredictParams, ctx: RunContext = NULL_CONTEXT) -> PredictResult:
    """Read persisted predictions and return every score for a date, ranked.

    The whole cross-section comes back — the page draws a distribution over it —
    and ``PredictResult.picks`` is its head, so the picks table and the
    distribution chart read from one dataset.
    """
    store = ctx.db
    as_of = params.as_of or store.get_latest_trade_date()
    if as_of is None:
        raise ValueError("Database has no trade dates; run a data sync first")

    path = Path(params.predictions_path)
    predictions = load_predictions(path)
    if predictions.empty:
        ctx.log(f"No predictions at {path}; train a model first", level="warning")
        return PredictResult(as_of=as_of, predictions_path=str(path))

    available = sorted(
        {
            parsed
            for parsed in (_as_date(value) for value in predictions["trade_date"].unique().tolist())
            if parsed is not None
        }
    )
    universe = params.universe or store.get_universe(get_default_universe(), as_of)
    day = predictions[(predictions["trade_date"] == as_of) & (predictions["ts_code"].isin(universe))]
    if day.empty:
        ctx.log(f"No predictions for {as_of}", level="warning")
        return PredictResult(as_of=as_of, predictions_path=str(path), top_n=params.top_n, available_dates=available)

    ranked = day.sort_values("score", ascending=False)
    return PredictResult(
        as_of=as_of,
        predictions_path=str(path),
        top_n=params.top_n,
        scores=[
            PredictionRow(ts_code=str(r.ts_code), score=_to_float(r.score)) for r in ranked.itertuples(index=False)
        ],
        total_scored=len(day),
        available_dates=available,
    )


def _to_float(value: object) -> float:
    """Coerce a pandas/numpy scalar to float, rejecting anything non-numeric."""
    if isinstance(value, numbers.Real):
        return float(value)
    raise TypeError(f"expected a real number, got {type(value).__name__}")


def _as_date(value: object) -> date | None:
    """A ``date`` from a pandas ``Timestamp``, ``datetime`` or ``date``."""
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


def _as_float(value: object) -> float | None:
    """Coerce a metric to float, treating NaN and non-numerics as absent."""
    if isinstance(value, (int, float)) and value == value:  # NaN != NaN
        return float(value)
    return None
