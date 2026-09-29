"""DuckDB-backed persistence for runs, their logs and their artifacts.

Every connection here uses DuckDB's default configuration. Opening the same
file read-only while another connection in this process holds it read-write
raises ``ConnectionException: Can't open a connection to same database file
with a different configuration``, so read paths do not get a special mode —
concurrency comes from DuckDB's MVCC instead.
"""

from __future__ import annotations

import contextlib
import json
import threading
import uuid
from collections.abc import Iterator, Sequence
from datetime import datetime
from typing import Any

import duckdb

from quant_trade.data.schema import init_db
from quant_trade.runs.models import (
    ArtifactDraft,
    ArtifactRecord,
    ArtifactStorage,
    RunLogLine,
    RunRecord,
    RunStatus,
    RunTrigger,
)

_RUN_COLUMNS = (
    "run_id, kind, params_json, status, progress, message, trigger, "
    "idempotency_key, created_at, started_at, finished_at, error"
)

_LOG_COLUMNS = "run_id, seq, ts, level, message"

_ARTIFACT_COLUMNS = "artifact_id, run_id, kind, storage, ref, row_count, meta_json, created_at"

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200

DEFAULT_INTERRUPTED_ERROR = "进程在运行期间退出，任务被中断"
"""Written to ``run.error`` by startup recovery for a run that was executing."""

DEFAULT_NOT_STARTED_ERROR = "进程重启前未开始执行，任务未运行"
"""Written to ``run.error`` by startup recovery for a run that was still queued."""


class RunStore:
    """Create, advance and read back run records.

    A single connection is held for the lifetime of the instance and guarded by
    a lock, because DuckDB connections are not safe to share across threads —
    the worker thread and the HTTP threads both reach this object.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        # Opened eagerly: an unusable database path should fail where the store
        # is constructed (platform startup), not on the first run that touches it.
        self._conn: duckdb.DuckDBPyConnection = init_db(db_path)
        self._lock = threading.Lock()

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        return self._conn

    def close(self) -> None:
        """Close the connection, releasing buffer memory first."""
        with contextlib.suppress(Exception):
            self._conn.execute("PRAGMA shrink_memory")
        self._conn.close()

    @contextlib.contextmanager
    def _cursor(self) -> Iterator[duckdb.DuckDBPyConnection]:
        with self._lock:
            yield self._conn

    # ---- run ----

    def create(
        self,
        kind: str,
        params_json: str,
        *,
        trigger: RunTrigger = RunTrigger.MANUAL,
        idempotency_key: str | None = None,
        run_id: str | None = None,
    ) -> RunRecord:
        """Insert a ``pending`` run and return it."""
        new_id = run_id or uuid.uuid4().hex
        with self._cursor() as conn:
            conn.execute(
                "INSERT INTO run (run_id, kind, params_json, status, trigger, idempotency_key) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [new_id, kind, params_json, RunStatus.PENDING.value, trigger.value, idempotency_key],
            )
        record = self.get(new_id)
        if record is None:  # pragma: no cover - the insert above just succeeded
            raise RuntimeError(f"run {new_id} vanished immediately after insert")
        return record

    def get(self, run_id: str) -> RunRecord | None:
        """The run with this id, or ``None``."""
        with self._cursor() as conn:
            row = conn.execute(f"SELECT {_RUN_COLUMNS} FROM run WHERE run_id = ?", [run_id]).fetchone()
        return _to_run(row) if row else None

    def list_runs(self, limit: int = DEFAULT_PAGE_SIZE, offset: int = 0) -> tuple[list[RunRecord], int]:
        """A page of runs, newest first, plus the total row count.

        Ordering falls back to ``created_at`` for runs that have not started,
        which would otherwise sort as NULL and drop off the first page.
        """
        limit = max(1, min(limit, MAX_PAGE_SIZE))
        offset = max(0, offset)
        with self._cursor() as conn:
            total_row = conn.execute("SELECT COUNT(*) FROM run").fetchone()
            rows = conn.execute(
                f"SELECT {_RUN_COLUMNS} FROM run "
                "ORDER BY COALESCE(started_at, created_at) DESC, run_id DESC LIMIT ? OFFSET ?",
                [limit, offset],
            ).fetchall()
        total = int(total_row[0]) if total_row else 0
        return [_to_run(r) for r in rows], total

    def mark_running(self, run_id: str) -> None:
        """Move a run to ``running`` and stamp its start time."""
        self._update(run_id, status=RunStatus.RUNNING, started_at=datetime.now())

    def update_progress(self, run_id: str, progress: float, message: str = "") -> None:
        """Record how far along a run is."""
        with self._cursor() as conn:
            conn.execute("UPDATE run SET progress = ?, message = ? WHERE run_id = ?", [progress, message, run_id])

    def finish(self, run_id: str, status: RunStatus, error: str | None = None) -> None:
        """Move a run to a terminal state.

        Raises:
            ValueError: if ``status`` is not terminal — a half-finished run is
                indistinguishable from an interrupted one, so it is rejected
                rather than written.
        """
        if not status.is_terminal:
            raise ValueError(f"{status.value!r} is not a terminal status")
        self._update(run_id, status=status, finished_at=datetime.now(), error=error)

    def mark_interrupted(
        self,
        error: str = DEFAULT_INTERRUPTED_ERROR,
        not_started_error: str = DEFAULT_NOT_STARTED_ERROR,
    ) -> int:
        """Rewrite every leftover non-terminal row as ``interrupted``.

        Called once at worker startup, and covers both states a run can be
        stranded in when a process dies: ``running`` (it was executing) and
        ``pending`` (it was sitting in the in-memory queue, which does not
        survive a restart). Either one left alone would read as "still coming"
        forever, with nothing left to advance it.

        Nothing is resumed. A hard-killed domain function had no chance to
        clean up, and a queued run is no longer queued — so re-running means
        submitting again.
        """
        now = datetime.now()
        with self._cursor() as conn:
            stale = conn.execute(
                "SELECT COUNT(*) FROM run WHERE status IN (?, ?)",
                [RunStatus.RUNNING.value, RunStatus.PENDING.value],
            ).fetchone()
            count = int(stale[0]) if stale else 0
            if count == 0:
                return 0
            for status, message in ((RunStatus.RUNNING, error), (RunStatus.PENDING, not_started_error)):
                conn.execute(
                    "UPDATE run SET status = ?, finished_at = ?, error = ? WHERE status = ?",
                    [RunStatus.INTERRUPTED.value, now, message, status.value],
                )
        return count

    def _update(
        self,
        run_id: str,
        *,
        status: RunStatus | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        error: str | None = None,
    ) -> None:
        assignments: list[str] = []
        params: list[Any] = []
        if status is not None:
            assignments.append("status = ?")
            params.append(status.value)
        if started_at is not None:
            assignments.append("started_at = ?")
            params.append(started_at)
        if finished_at is not None:
            assignments.append("finished_at = ?")
            params.append(finished_at)
        if error is not None:
            assignments.append("error = ?")
            params.append(error)
        if not assignments:
            return
        params.append(run_id)
        with self._cursor() as conn:
            conn.execute(f"UPDATE run SET {', '.join(assignments)} WHERE run_id = ?", params)

    # ---- run_log ----

    def append_log(self, run_id: str, message: str, level: str = "info") -> RunLogLine:
        """Append one log line, assigning the next contiguous ``seq``."""
        with self._cursor() as conn:
            row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM run_log WHERE run_id = ?", [run_id]).fetchone()
            seq = int(row[0]) if row else 1
            ts = datetime.now()
            conn.execute(
                "INSERT INTO run_log (run_id, seq, ts, level, message) VALUES (?, ?, ?, ?, ?)",
                [run_id, seq, ts, level, message],
            )
        return RunLogLine(run_id=run_id, seq=seq, message=message, level=level, ts=ts)

    def logs(self, run_id: str, after_seq: int = 0, limit: int | None = None) -> list[RunLogLine]:
        """Log lines for a run in ``seq`` order, optionally only the new ones."""
        sql = f"SELECT {_LOG_COLUMNS} FROM run_log WHERE run_id = ? AND seq > ? ORDER BY seq"
        params: list[Any] = [run_id, after_seq]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._cursor() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_to_log(r) for r in rows]

    # ---- artifact ----

    def add_artifact(self, run_id: str, draft: ArtifactDraft) -> ArtifactRecord:
        """Register one artifact produced by a run."""
        artifact_id = uuid.uuid4().hex
        created_at = datetime.now()
        meta_json = json.dumps(draft.meta, ensure_ascii=False) if draft.meta else None
        with self._cursor() as conn:
            conn.execute(
                "INSERT INTO artifact (artifact_id, run_id, kind, storage, ref, row_count, meta_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    artifact_id,
                    run_id,
                    draft.kind,
                    draft.storage.value,
                    draft.ref,
                    draft.row_count,
                    meta_json,
                    created_at,
                ],
            )
        return ArtifactRecord(
            artifact_id=artifact_id,
            run_id=run_id,
            kind=draft.kind,
            storage=draft.storage,
            ref=draft.ref,
            row_count=draft.row_count,
            meta=draft.meta,
            created_at=created_at,
        )

    def add_artifacts(self, run_id: str, drafts: Sequence[ArtifactDraft]) -> list[ArtifactRecord]:
        """Register several artifacts for one run."""
        return [self.add_artifact(run_id, draft) for draft in drafts]

    def artifacts(self, run_id: str) -> list[ArtifactRecord]:
        """All artifacts registered for a run, oldest first."""
        with self._cursor() as conn:
            rows = conn.execute(
                f"SELECT {_ARTIFACT_COLUMNS} FROM artifact WHERE run_id = ? ORDER BY created_at, artifact_id",
                [run_id],
            ).fetchall()
        return [_to_artifact(r) for r in rows]


def _to_run(row: tuple[Any, ...]) -> RunRecord:
    return RunRecord(
        run_id=str(row[0]),
        kind=str(row[1]),
        params_json=str(row[2]),
        status=RunStatus(row[3]),
        progress=float(row[4]) if row[4] is not None else 0.0,
        message=str(row[5] or ""),
        trigger=RunTrigger(row[6]),
        idempotency_key=row[7],
        created_at=row[8],
        started_at=row[9],
        finished_at=row[10],
        error=row[11],
    )


def _to_log(row: tuple[Any, ...]) -> RunLogLine:
    return RunLogLine(run_id=str(row[0]), seq=int(row[1]), ts=row[2], level=str(row[3]), message=str(row[4]))


def _to_artifact(row: tuple[Any, ...]) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_id=str(row[0]),
        run_id=str(row[1]),
        kind=str(row[2]),
        storage=ArtifactStorage(row[3]),
        ref=str(row[4]),
        row_count=int(row[5]) if row[5] is not None else None,
        meta=json.loads(row[6]) if row[6] else {},
        created_at=row[7],
    )
