"""The report domain's read API.

Read-only, like the factor, model and backtest routers: a weekly report is
produced by submitting ``kind: weekly`` to ``POST /api/runs``, so this router
has no POST at all. Everything here reads what a run already wrote.

The list is a join over runs and artifacts, not a scan of the output directory.
A file on disk cannot say when it was produced or which run produced it, and
those are the two questions the report list exists to answer.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from quant_trade.config import AppConfig
from quant_trade.data.store import DataStore
from quant_trade.services.context import RunContext
from quant_trade.services.report_query import (
    ReportDetailParams,
    ReportListParams,
    ReportSummary,
    report_detail,
    report_html,
    report_list,
)

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200


def create_reports_router(config: AppConfig) -> APIRouter:
    """Build the report-domain routes."""
    router = APIRouter(prefix="/api/reports", tags=["reports"])

    @router.get("")
    def list_reports(
        limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        """Weekly report history, newest first."""
        params = _validated(ReportListParams, limit=limit, offset=offset)
        with _context(config) as ctx:
            result = report_list(params, ctx)
        return {
            "total": result.total,
            "offset": result.offset,
            "limit": result.limit,
            "items": [_summary_payload(row) for row in result.reports],
        }

    @router.get("/{run_id}")
    def get_report(run_id: str) -> dict[str, Any]:
        """One report's metadata."""
        params = _validated(ReportDetailParams, run_id=run_id)
        with _context(config) as ctx:
            detail = report_detail(params, ctx)
        if not detail.found:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"周报 {run_id} 不存在")
        return {
            "run_id": detail.run_id,
            "status": detail.status,
            "created_at": _iso(detail.created_at),
            "finished_at": _iso(detail.finished_at),
            "progress": detail.progress,
            "signal_date": _iso(detail.signal_date),
            "order_count": detail.order_count,
            "trade_count": detail.trade_count,
            "file_name": detail.file_name,
            "error": detail.error,
        }

    @router.get("/{run_id}/html")
    def get_report_html(run_id: str, download: bool = Query(False)) -> HTMLResponse:
        """The rendered report, inline for embedding or as a download.

        Served as its own response rather than a JSON string field: the report
        inlines its charts as base64, and shipping it through JSON to reach a
        ``srcDoc`` would mean holding the whole document in script memory twice.
        """
        params = _validated(ReportDetailParams, run_id=run_id)
        with _context(config) as ctx:
            content = report_html(params, ctx)
        if not content.found:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"周报 {run_id} 不存在")

        headers: dict[str, str] = {}
        if download:
            # The report's own filename, not the run id: the download should
            # land as the file the run wrote.
            headers["Content-Disposition"] = f'attachment; filename="{content.file_name}"'
        return HTMLResponse(content=content.html, headers=headers)

    return router


def _summary_payload(row: ReportSummary) -> dict[str, Any]:
    return {
        "run_id": row.run_id,
        "status": row.status,
        "created_at": _iso(row.created_at),
        "finished_at": _iso(row.finished_at),
        "progress": row.progress,
        "signal_date": _iso(row.signal_date),
        "order_count": row.order_count,
        "trade_count": row.trade_count,
        "file_name": row.file_name,
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
