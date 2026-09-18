"""The unified run API.

Every long task — sync, factor computation, backtest, report — is submitted to
``POST /api/runs`` with a ``kind`` and a params object. Adding a task type means
adding a registry entry, not a route.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from quant_trade.jobs.registry import get_job, known_kinds
from quant_trade.jobs.runner import JobRunner
from quant_trade.runs.models import ArtifactRecord, RunRecord
from quant_trade.runs.store import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, RunStore


class RunRequest(BaseModel):
    """Body of ``POST /api/runs``."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    params: dict[str, Any] = Field(default_factory=dict)


class RunAccepted(BaseModel):
    """What a caller gets back the moment a run is queued."""

    run_id: str
    kind: str
    status: str


class RunPage(BaseModel):
    """One page of run history."""

    total: int
    items: list[dict[str, Any]]


def create_runs_router(runs: RunStore, runner: JobRunner) -> APIRouter:
    """Build the run routes bound to a store and a worker."""
    router = APIRouter(prefix="/api/runs", tags=["runs"])

    @router.post("", status_code=status.HTTP_202_ACCEPTED)
    def submit_run(request: RunRequest) -> RunAccepted:
        """Validate and queue a run, returning immediately.

        Validation happens before the record is written, so a rejected
        submission leaves no trace in the run history.
        """
        spec = get_job(request.kind)
        if spec is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"未知的任务类型 {request.kind!r}；已注册：{', '.join(known_kinds()) or '（无）'}",
            )

        try:
            params = spec.params_model.model_validate(request.params)
        except ValidationError as e:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=json.loads(e.json()),
            ) from e

        record = runs.create(request.kind, params.model_dump_json())
        runner.submit(record.run_id)
        return RunAccepted(run_id=record.run_id, kind=record.kind, status=record.status.value)

    @router.get("")
    def list_runs(
        limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
        offset: int = Query(0, ge=0),
    ) -> RunPage:
        """Runs newest first."""
        page, total = runs.list_runs(limit=limit, offset=offset)
        return RunPage(total=total, items=[_run_payload(r, with_params=False) for r in page])

    @router.get("/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        """One run's status, progress and the params it was submitted with."""
        return _run_payload(_require_run(runs, run_id), with_params=True)

    @router.get("/{run_id}/artifacts")
    def list_artifacts(run_id: str) -> list[dict[str, Any]]:
        """Everything this run registered, oldest first."""
        _require_run(runs, run_id)
        return [_artifact_payload(a) for a in runs.artifacts(run_id)]

    @router.post("/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
    def cancel_run(run_id: str) -> dict[str, Any]:
        """Ask a running job to stop at its next checkpoint."""
        record = _require_run(runs, run_id)
        if not runner.cancel(run_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"运行当前状态为 {record.status.value}，不可取消",
            )
        return {"run_id": run_id, "cancelling": True}

    return router


def _require_run(runs: RunStore, run_id: str) -> RunRecord:
    record = runs.get(run_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"运行 {run_id} 不存在")
    return record


def _run_payload(record: RunRecord, *, with_params: bool) -> dict[str, Any]:
    """A run as JSON. ``params`` is omitted from list pages to keep them small."""
    payload: dict[str, Any] = {
        "run_id": record.run_id,
        "kind": record.kind,
        "status": record.status.value,
        "progress": record.progress,
        "message": record.message,
        "trigger": record.trigger.value,
        "idempotency_key": record.idempotency_key,
        "created_at": _iso(record.created_at),
        "started_at": _iso(record.started_at),
        "finished_at": _iso(record.finished_at),
        "error": record.error,
    }
    if with_params:
        payload["params"] = _load_params(record.params_json)
    return payload


def _artifact_payload(record: ArtifactRecord) -> dict[str, Any]:
    return {
        "artifact_id": record.artifact_id,
        "run_id": record.run_id,
        "kind": record.kind,
        "storage": record.storage.value,
        "ref": record.ref,
        "row_count": record.row_count,
        "meta": record.meta,
        "created_at": _iso(record.created_at),
    }


def _load_params(params_json: str) -> Any:
    try:
        return json.loads(params_json)
    except json.JSONDecodeError:  # pragma: no cover - params_json is always our own dump
        return params_json


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
