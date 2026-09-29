"""Report-domain read services — the weekly report browser's only read path.

A report is a run of kind ``weekly`` that registered an HTML artifact, so the
list is a join rather than a scan of the output directory. A file on disk says
nothing about when it was produced or what it was produced from, and answering
"how fresh is this?" is the whole point of the page.

The path guard lives here rather than in the router: ``artifact.ref`` is a
string that came out of the database, so it is untrusted input like any other,
and deciding which paths are readable is domain logic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import Field

from quant_trade.config import AppConfig
from quant_trade.data.store import DataStore
from quant_trade.services.context import NULL_CONTEXT, RunContext
from quant_trade.services.params import ServiceParams

REPORT_KIND = "weekly"
"""The run kind a weekly report is submitted under."""

REPORT_STORAGE = "html"
"""The artifact storage a rendered report is registered with."""

MAX_PAGE_SIZE = 200


class ReportListParams(ServiceParams):
    """One page of report history."""

    limit: int = Field(default=20, ge=1, le=MAX_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)


class ReportDetailParams(ServiceParams):
    """Which report to read."""

    run_id: str = Field(min_length=1)


@dataclass
class ReportSummary:
    """One report as the list renders it.

    ``signal_date`` and the two counts come from the artifact's meta rather
    than from the file, so the list can show data freshness without opening a
    self-contained HTML document that inlines its charts as base64.
    """

    run_id: str
    status: str
    created_at: datetime | None = None
    finished_at: datetime | None = None
    progress: float = 0.0
    signal_date: date | None = None
    order_count: int | None = None
    trade_count: int | None = None
    file_name: str = ""


@dataclass
class ReportListResult:
    """A page of reports plus the total, for server-side paging."""

    total: int = 0
    offset: int = 0
    limit: int = 0
    reports: list[ReportSummary] = field(default_factory=list)


@dataclass
class ReportDetail:
    """One report's metadata, or ``found=False`` when nothing is registered."""

    run_id: str
    found: bool = False
    status: str = ""
    created_at: datetime | None = None
    finished_at: datetime | None = None
    progress: float = 0.0
    signal_date: date | None = None
    order_count: int | None = None
    trade_count: int | None = None
    file_name: str = ""
    error: str | None = None


@dataclass
class ReportContent:
    """A report's rendered HTML, or ``found=False`` when it cannot be served."""

    run_id: str
    found: bool = False
    html: str = ""
    file_name: str = ""


_LIST_SQL = """
    WITH latest_artifact AS (
        SELECT run_id, ref, meta_json,
               ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY created_at DESC) AS rn
        FROM artifact
        WHERE storage = ?
    )
    SELECT r.run_id, r.status, r.progress, r.created_at, r.finished_at,
           a.ref, a.meta_json
    FROM run r
    LEFT JOIN latest_artifact a ON a.run_id = r.run_id AND a.rn = 1
    WHERE r.kind = ?
    ORDER BY COALESCE(r.finished_at, r.created_at) DESC
    LIMIT ? OFFSET ?
"""

# Outer join on purpose: the run is the source, the artifact only supplies the
# freshness columns. An inner join hides a queued or running report until it
# has already finished, which is exactly when a reader wants to see its
# progress — and it would leave the list's progress branch unreachable.
_COUNT_SQL = """
    SELECT COUNT(*)
    FROM run r
    WHERE r.kind = ?
"""

_DETAIL_SQL = """
    WITH latest_artifact AS (
        SELECT run_id, ref, meta_json,
               ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY created_at DESC) AS rn
        FROM artifact
        WHERE storage = ?
    )
    SELECT r.run_id, r.status, r.progress, r.created_at, r.finished_at, r.error,
           a.ref, a.meta_json
    FROM run r
    JOIN latest_artifact a ON a.run_id = r.run_id AND a.rn = 1
    WHERE r.kind = ? AND r.run_id = ?
"""


def _meta(meta_json: object) -> dict[str, Any]:
    """The artifact's meta as a dict.

    A row whose meta cannot be parsed still lists — it just lists without the
    freshness columns, which is better than a page that fails to render because
    one historical row was written before the meta shape settled.
    """
    if not isinstance(meta_json, str) or not meta_json:
        return {}
    try:
        parsed = json.loads(meta_json)
    except ValueError:
        logger.warning("artifact meta_json is not valid JSON; listing the report without it")
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _meta_date(meta: dict[str, Any], key: str) -> date | None:
    raw = meta.get(key)
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _meta_count(meta: dict[str, Any], key: str) -> int | None:
    raw = meta.get(key)
    # ``bool`` is an ``int``; a stray True here is a malformed meta, not a count.
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def _summary_fields(
    run_id: object,
    status: object,
    progress: object,
    created_at: object,
    finished_at: object,
    ref: object,
    meta_json: object,
) -> ReportSummary:
    """Assemble one row's display fields from its columns.

    Takes columns rather than a row tuple because the list and detail queries
    select different column sets; slicing one into the other's shape is how the
    ``error`` column silently became a ``ref``.
    """
    meta = _meta(meta_json)
    return ReportSummary(
        run_id=str(run_id),
        status=str(status),
        created_at=created_at,  # type: ignore[arg-type]
        finished_at=finished_at,  # type: ignore[arg-type]
        progress=float(progress or 0.0),  # type: ignore[arg-type]
        signal_date=_meta_date(meta, "signal_date"),
        order_count=_meta_count(meta, "order_count"),
        trade_count=_meta_count(meta, "trade_count"),
        # Empty, not the string "None": a run with no artifact yet has no file.
        file_name=Path(str(ref)).name if ref else "",
    )


def report_list(params: ReportListParams, ctx: RunContext = NULL_CONTEXT) -> ReportListResult:
    """One page of weekly reports, newest first."""
    store = ctx.db
    total = int(store.conn.execute(_COUNT_SQL, [REPORT_KIND]).fetchone()[0])  # type: ignore[index]
    rows = store.conn.execute(_LIST_SQL, [REPORT_STORAGE, REPORT_KIND, params.limit, params.offset]).fetchall()
    return ReportListResult(
        total=total,
        offset=params.offset,
        limit=params.limit,
        reports=[_summary_fields(*row) for row in rows],
    )


def report_detail(params: ReportDetailParams, ctx: RunContext = NULL_CONTEXT) -> ReportDetail:
    """One report's metadata. ``found=False`` when the run registered no report."""
    row = ctx.db.conn.execute(_DETAIL_SQL, [REPORT_STORAGE, REPORT_KIND, params.run_id]).fetchone()
    if row is None:
        return ReportDetail(run_id=params.run_id, found=False)
    run_id, status, progress, created_at, finished_at, error, ref, meta_json = row
    summary = _summary_fields(run_id, status, progress, created_at, finished_at, ref, meta_json)
    return ReportDetail(
        run_id=summary.run_id,
        found=True,
        status=summary.status,
        created_at=summary.created_at,
        finished_at=summary.finished_at,
        progress=summary.progress,
        signal_date=summary.signal_date,
        order_count=summary.order_count,
        trade_count=summary.trade_count,
        file_name=summary.file_name,
        error=error,
    )


def report_html(params: ReportDetailParams, ctx: RunContext = NULL_CONTEXT) -> ReportContent:
    """A report's rendered HTML, read from the path its artifact registered."""
    path = resolve_report_path(ctx.db, params.run_id, ctx.cfg)
    if path is None:
        return ReportContent(run_id=params.run_id, found=False)
    try:
        html = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning(f"report file unreadable at {path}: {e}")
        return ReportContent(run_id=params.run_id, found=False)
    return ReportContent(run_id=params.run_id, found=True, html=html, file_name=path.name)


def resolve_report_path(store: DataStore, run_id: str, config: AppConfig) -> Path | None:
    """The report file for a run, or ``None`` when it must not be served.

    Two refusals, both deliberate. The path has to sit under the configured
    report directory — ``artifact.ref`` came out of the database, and a row
    pointing at ``/etc/passwd`` must not turn this into a file-read endpoint.
    And the file has to exist: a registered artifact whose bytes are gone is a
    missing report, not a 500.
    """
    row = store.conn.execute(
        """
        SELECT ref FROM artifact
        WHERE run_id = ? AND storage = ?
        ORDER BY created_at DESC
        LIMIT 1
        """,
        [run_id, REPORT_STORAGE],
    ).fetchone()
    if row is None or row[0] is None:
        return None

    try:
        candidate = Path(str(row[0])).resolve()
        root = Path(config.report.output_dir).resolve()
    except OSError:
        return None

    if not candidate.is_relative_to(root):
        logger.warning(f"refusing to serve report outside {root}: {candidate}")
        return None
    return candidate if candidate.is_file() else None
