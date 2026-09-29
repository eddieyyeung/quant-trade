"""Plain data carriers for the run registry.

These are dataclasses rather than pydantic models: they are read straight out
of DuckDB and serialised by the API layer, so there is nothing to validate on
the way in or out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class RunStatus(StrEnum):
    """Lifecycle of one run.

    ``pending`` → ``running`` → one of the four terminal states. A run never
    leaves a terminal state; re-running means submitting a new run.
    """

    PENDING = "pending"
    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"

    @property
    def is_terminal(self) -> bool:
        """Whether the run has stopped and will not advance further."""
        return self in _TERMINAL


_TERMINAL: frozenset[RunStatus] = frozenset(
    {RunStatus.OK, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INTERRUPTED}
)


class RunTrigger(StrEnum):
    """What caused a run to be submitted.

    ``manual`` is the only value produced today; ``scheduled`` and ``api`` are
    reserved so that introducing a scheduler later does not require backfilling
    historical rows.
    """

    MANUAL = "manual"
    SCHEDULED = "scheduled"
    API = "api"


class ArtifactStorage(StrEnum):
    """Where an artifact's bytes actually live, since ``ref`` alone is ambiguous."""

    TABLE = "table"
    """A dedicated DuckDB table; ``ref`` is the table name."""
    PARQUET = "parquet"
    """A Parquet file; ``ref`` is its path."""
    HTML = "html"
    """A rendered report; ``ref`` is its path."""


@dataclass
class RunRecord:
    """One row of ``run``."""

    run_id: str
    kind: str
    params_json: str
    status: RunStatus
    progress: float = 0.0
    message: str = ""
    trigger: RunTrigger = RunTrigger.MANUAL
    idempotency_key: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


@dataclass
class RunLogLine:
    """One row of ``run_log``. ``seq`` is 1-based and contiguous per run."""

    run_id: str
    seq: int
    message: str
    level: str = "info"
    ts: datetime | None = None


@dataclass
class ArtifactDraft:
    """An artifact about to be registered, before it gets an id and a timestamp."""

    kind: str
    storage: ArtifactStorage
    ref: str
    row_count: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ArtifactRecord:
    """One row of ``artifact``."""

    artifact_id: str
    run_id: str
    kind: str
    storage: ArtifactStorage
    ref: str
    row_count: int | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
