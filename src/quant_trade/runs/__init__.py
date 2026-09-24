"""Run registry — persisted execution records, logs and artifact index.

Every long-running operation on the platform becomes a *run*: a row in ``run``
carrying the parameters it was submitted with, a status, and a ``run_log``
trail. :class:`~quant_trade.runs.store.RunStore` is the only writer of those
tables.
"""

from quant_trade.runs.models import (
    ArtifactDraft,
    ArtifactRecord,
    RunLogLine,
    RunRecord,
    RunStatus,
)
from quant_trade.runs.store import RunStore

__all__ = [
    "ArtifactDraft",
    "ArtifactRecord",
    "RunLogLine",
    "RunRecord",
    "RunStatus",
    "RunStore",
]
