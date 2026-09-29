"""Job type registry — what each ``run.kind`` means.

Adding a task type means adding one entry here. The run API, the worker and the
frontend all dispatch on ``kind`` through this table, so none of them grows a
per-domain branch.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from quant_trade.runs.models import ArtifactDraft, ArtifactStorage
from quant_trade.services.backtest import BacktestParams, BacktestResult, run_backtest_service
from quant_trade.services.data import DataSyncParams, DataSyncResult, sync_market_data
from quant_trade.services.factor_analysis import (
    FactorICComputeParams,
    FactorICComputeResult,
    compute_factor_ic,
)
from quant_trade.services.factors import Alpha158Params, Alpha158Result, compute_alpha158
from quant_trade.services.models import TrainParams, TrainResult, train_model
from quant_trade.services.params import ServiceParams
from quant_trade.services.report import WeeklyReportParams, WeeklyReportResult, generate_weekly
from quant_trade.services.strategies import SignalParams, SignalSummary, run_strategy_signals

ArtifactMapper = Callable[[Any], list[ArtifactDraft]]
"""Turns a service result into the artifacts a run should register."""


@dataclass(frozen=True)
class JobSpec:
    """Everything the worker needs to execute one kind of run."""

    kind: str
    params_model: type[ServiceParams]
    """Validates the submitted ``params`` before a run record is created."""
    service_fn: Callable[..., Any]
    """Called as ``service_fn(params, ctx)``."""
    artifacts: ArtifactMapper | None = None
    """Optional. Omitted for kinds whose result is not worth indexing."""


def _data_sync_artifacts(result: DataSyncResult) -> list[ArtifactDraft]:
    """One artifact per table the sync actually wrote rows into.

    Tables reported with zero rows are skipped: an artifact is a pointer to
    data that exists, and a pointer to an empty table is noise.
    """
    return [
        ArtifactDraft(kind="table", storage=ArtifactStorage.TABLE, ref=table, row_count=rows)
        for table, rows in sorted(result.tables.items())
        if rows > 0
    ]


def _factor_compute_artifacts(result: Alpha158Result) -> list[ArtifactDraft]:
    """Alpha158 computation writes straight into ``factor_values``."""
    if result.rows_saved <= 0:
        return []
    return [
        ArtifactDraft(
            kind="table",
            storage=ArtifactStorage.TABLE,
            ref="factor_values",
            row_count=result.rows_saved,
            meta={"factor_count": result.factor_count, "universe_size": result.universe_size},
        )
    ]


def _factor_ic_artifacts(result: FactorICComputeResult) -> list[ArtifactDraft]:
    """IC computation writes into ``ic_series``."""
    if result.rows_saved <= 0:
        return []
    return [
        ArtifactDraft(
            kind="table",
            storage=ArtifactStorage.TABLE,
            ref="ic_series",
            row_count=result.rows_saved,
            meta={"factors": result.factors, "forward_periods": result.forward_periods},
        )
    ]


def _backtest_artifacts(result: BacktestResult) -> list[ArtifactDraft]:
    """A backtest writes NAV rows and metric rows; both are worth indexing.

    They answer different questions — "how long is this curve" and "which
    metrics did this run compute" — so neither is a function of the other.
    """
    meta = {"strategy": result.strategy, "start": result.start.isoformat(), "end": result.end.isoformat()}
    drafts: list[ArtifactDraft] = []
    if result.rows_saved.nav > 0:
        drafts.append(
            ArtifactDraft(
                kind="table",
                storage=ArtifactStorage.TABLE,
                ref="backtest_nav",
                row_count=result.rows_saved.nav,
                meta=meta,
            )
        )
    if result.rows_saved.metrics > 0:
        drafts.append(
            ArtifactDraft(
                kind="table",
                storage=ArtifactStorage.TABLE,
                ref="backtest_metric",
                row_count=result.rows_saved.metrics,
                meta=meta,
            )
        )
    return drafts


def _strategy_signal_artifacts(result: SignalSummary) -> list[ArtifactDraft]:
    """The ``strategy_signal`` rows a run wrote.

    ``universe_size`` rides in the meta because it has no home in the table —
    it is a property of the run, not of any order. The other three meta keys
    (strategy, signal_date, and the row count itself) are also derivable from
    the rows, and exist so a reader can see them without opening the table.
    """
    if not result.orders:
        return []
    return [
        ArtifactDraft(
            kind="table",
            storage=ArtifactStorage.TABLE,
            ref="strategy_signal",
            row_count=len(result.orders),
            meta={
                "strategy": result.strategy,
                "signal_date": result.signal_date.isoformat(),
                "universe_size": result.universe_size,
            },
        )
    ]


def _weekly_artifacts(result: WeeklyReportResult) -> list[ArtifactDraft]:
    """The rendered report, when there is one.

    ``row_count`` stays ``None`` rather than ``0``: a report is not a row set,
    and ``0`` reads downstream as "this report is empty" instead of "this
    artifact has no row count". The meta carries what the report list needs to
    show data freshness without opening the file.
    """
    if not result.report_path:
        return []
    return [
        ArtifactDraft(
            kind="report",
            storage=ArtifactStorage.HTML,
            ref=result.report_path,
            row_count=None,
            meta={
                "signal_date": result.signal_date.isoformat(),
                "order_count": result.order_count,
                "trade_count": result.trade_count,
            },
        )
    ]


def _model_train_artifacts(result: TrainResult) -> list[ArtifactDraft]:
    """One artifact per thing a training run produced.

    Three, not one: the daily IC answers "when was the model right", the
    importance table answers "what was it leaning on", and the predictions file
    answers "what did it pick". None is a function of the others. The summary
    scalars in ``model_metric`` are not listed — they are a digest of these, and
    an artifact is a pointer to data, not to a summary of data.
    """
    meta = {
        "windows_trained": result.windows_trained,
        "start": result.start.isoformat(),
        "end": result.end.isoformat(),
    }
    drafts: list[ArtifactDraft] = []
    if result.rows_saved.ic > 0:
        drafts.append(
            ArtifactDraft(
                kind="table",
                storage=ArtifactStorage.TABLE,
                ref="model_ic_series",
                row_count=result.rows_saved.ic,
                meta=meta,
            )
        )
    if result.rows_saved.importance > 0:
        drafts.append(
            ArtifactDraft(
                kind="table",
                storage=ArtifactStorage.TABLE,
                ref="model_feature_importance",
                row_count=result.rows_saved.importance,
                meta=meta,
            )
        )
    if result.prediction_rows > 0:
        drafts.append(
            ArtifactDraft(
                kind="predictions",
                storage=ArtifactStorage.PARQUET,
                ref=result.output_path,
                row_count=result.prediction_rows,
                meta=meta,
            )
        )
    return drafts


JOBS: dict[str, JobSpec] = {
    "data_sync": JobSpec(
        kind="data_sync",
        params_model=DataSyncParams,
        service_fn=sync_market_data,
        artifacts=_data_sync_artifacts,
    ),
    # Points at the Alpha158 computation, not the legacy per-stock
    # ``compute_factors``: that one persists nothing, so a run of it would
    # produce no artifact and leave every factor page with nothing to read.
    "factor_compute": JobSpec(
        kind="factor_compute",
        params_model=Alpha158Params,
        service_fn=compute_alpha158,
        artifacts=_factor_compute_artifacts,
    ),
    "factor_ic": JobSpec(
        kind="factor_ic",
        params_model=FactorICComputeParams,
        service_fn=compute_factor_ic,
        artifacts=_factor_ic_artifacts,
    ),
    "backtest": JobSpec(
        kind="backtest",
        params_model=BacktestParams,
        service_fn=run_backtest_service,
        artifacts=_backtest_artifacts,
    ),
    "model_train": JobSpec(
        kind="model_train",
        params_model=TrainParams,
        service_fn=train_model,
        artifacts=_model_train_artifacts,
    ),
    # Points at ``run_strategy_signals``, not ``generate_strategy_signals``: only
    # the former persists. The latter is what the weekly report pipeline calls,
    # and it must stay a pure computation — see the service's docstring.
    "strategy_signals": JobSpec(
        kind="strategy_signals",
        params_model=SignalParams,
        service_fn=run_strategy_signals,
        artifacts=_strategy_signal_artifacts,
    ),
    "weekly": JobSpec(
        kind="weekly",
        params_model=WeeklyReportParams,
        service_fn=generate_weekly,
        artifacts=_weekly_artifacts,
    ),
}
"""All runnable kinds, keyed by ``kind``. Tests may extend it in place."""


def get_job(kind: str) -> JobSpec | None:
    """The spec for ``kind``, or ``None`` if nothing is registered under it."""
    return JOBS.get(kind)


def known_kinds() -> list[str]:
    """Registered kinds, sorted, for listing in an error message."""
    return sorted(JOBS)
