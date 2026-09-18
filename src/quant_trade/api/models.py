"""The model domain's read API.

Read-only, like the factor and backtest routers: a training run is submitted
through ``POST /api/runs`` with ``kind: model_train``, so this router has no
POST at all. Everything here reads results a run already wrote.

The predictions endpoint takes no path. It resolves the file from the artifact
index of the newest successful training run, so a request can only ever read
what a training run itself produced.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import ValidationError

from quant_trade.config import AppConfig
from quant_trade.data.store import DataStore
from quant_trade.models.persistence import get_latest_prediction_source
from quant_trade.services.context import RunContext
from quant_trade.services.model_query import (
    DEFAULT_IMPORTANCE_TOP_N,
    MAX_IMPORTANCE_TOP_N,
    ModelEvaluationParams,
    ModelRunListParams,
    ModelRunSummary,
    model_evaluation,
    model_run_list,
)
from quant_trade.services.models import PredictionRow, PredictParams, predict_for_date

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200
DEFAULT_TOP_N = 15
MAX_TOP_N = 200


def create_models_router(config: AppConfig) -> APIRouter:
    """Build the model-domain routes."""
    router = APIRouter(prefix="/api/models", tags=["models"])

    @router.get("/runs")
    def list_runs(
        limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """Training history, newest first."""
        params = _validated(ModelRunListParams, limit=limit, offset=offset)
        with _context(config) as ctx:
            result = model_run_list(params, ctx)
        return {"total": result.total, "items": [_run_payload(run) for run in result.runs]}

    @router.get("/runs/{run_id}")
    def get_run(
        run_id: str,
        importance_top_n: int = Query(DEFAULT_IMPORTANCE_TOP_N, ge=1, le=MAX_IMPORTANCE_TOP_N),
    ) -> dict[str, Any]:
        """One run's evaluation: metrics, IC series, yearly rollup, importances."""
        params = _validated(ModelEvaluationParams, run_id=run_id, importance_top_n=importance_top_n)
        with _context(config) as ctx:
            result = model_evaluation(params, ctx)
        if not result.found:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"训练运行 {run_id} 不存在")
        return {
            "run_id": result.run_id,
            "status": result.status,
            "start": _iso(result.start),
            "end": _iso(result.end),
            "finished_at": _iso(result.finished_at),
            "metrics": {
                "ic_mean": result.ic_mean,
                "ic_ir": result.ic_ir,
                "ic_positive_ratio": result.ic_positive_ratio,
                "ic_days": result.ic_days,
                "windows_trained": result.windows_trained,
                "prediction_rows": result.prediction_rows,
            },
            "ic_series": [
                {"trade_date": _iso(point.trade_date), "rank_ic": point.rank_ic} for point in result.ic_series
            ],
            "yearly": [
                {
                    "year": year.year,
                    "ic_mean": year.ic_mean,
                    "ic_ir": year.ic_ir,
                    "ic_positive_ratio": year.ic_positive_ratio,
                    "days": year.days,
                }
                for year in result.yearly
            ],
            "importance": [
                {"factor": entry.factor, "importance": entry.importance, "std": entry.std}
                for entry in result.importance
            ],
            "importance_total": result.importance_total,
        }

    @router.get("/predictions")
    def predictions(
        as_of: date | None = None,
        top_n: int = Query(DEFAULT_TOP_N, ge=1, le=MAX_TOP_N),
    ) -> dict[str, Any]:
        """One date's predictions from the newest training run's output.

        An absent source is not an error — it is the state every fresh install
        is in, and the page renders it as "train a model first" rather than as
        a failed request.
        """
        params = _validated(PredictParams, as_of=as_of, top_n=top_n)
        with _context(config) as ctx:
            source = get_latest_prediction_source(ctx.db)
            if source is None:
                return {"available": False, "source": None, "scores": [], "picks": [], "available_dates": []}
            # The path is the one the run recorded, not one the caller supplied.
            result = predict_for_date(params.model_copy(update={"predictions_path": source.path}), ctx)
        return {
            "available": True,
            "source": {
                "run_id": source.run_id,
                "path": source.path,
                "finished_at": _iso(source.finished_at),
            },
            "as_of": _iso(result.as_of),
            "top_n": result.top_n,
            "total_scored": result.total_scored,
            "available_dates": [_iso(value) for value in result.available_dates],
            "picks": [_prediction_payload(row) for row in result.picks],
            "scores": [_prediction_payload(row) for row in result.scores],
        }

    return router


def _prediction_payload(row: PredictionRow) -> dict[str, Any]:
    return {"ts_code": row.ts_code, "score": row.score}


def _run_payload(run: ModelRunSummary) -> dict[str, Any]:
    """One training run as the history list renders it."""
    return {
        "run_id": run.run_id,
        "status": run.status,
        "progress": run.progress,
        "message": run.message,
        "start": _iso(run.start),
        "end": _iso(run.end),
        "requested_start": _iso(run.requested_start),
        "requested_end": _iso(run.requested_end),
        "factor_count": run.factor_count,
        "ic_days": run.ic_days,
        "windows_trained": run.windows_trained,
        "prediction_rows": run.prediction_rows,
        "ic_mean": run.ic_mean,
        "ic_ir": run.ic_ir,
        "ic_positive_ratio": run.ic_positive_ratio,
        "created_at": _iso(run.created_at),
        "finished_at": _iso(run.finished_at),
    }


def _validated(model: type[Any], **fields: Any) -> Any:
    """Build a params object, turning validation errors into 422."""
    try:
        return model(**fields)
    except ValidationError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=json.loads(e.json())) from e


@contextlib.contextmanager
def _context(config: AppConfig) -> Iterator[RunContext]:
    """A per-request context with its own store.

    The store uses the default DuckDB configuration, not read-only: a read-only
    connection alongside the process's read-write one raises at open.
    """
    store = DataStore(config.data.db_path)
    try:
        yield RunContext(run_id="api", config=config, store=store)
    finally:
        store.close()


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
