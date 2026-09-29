"""Run context — progress reporting, structured logging and cancellation.

Long-running services receive a :class:`RunContext` so they can report progress
and poll for cancellation without knowing who is listening. Callers that do not
care about either pass :data:`NULL_CONTEXT` (the default on every service
function) and pay nothing.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from quant_trade.config import AppConfig
    from quant_trade.data.store import DataStore


@runtime_checkable
class ProgressSink(Protocol):
    """Receives fractional progress updates from a running service.

    ``pct`` is in ``[0.0, 1.0]``; ``message`` is a short human-readable label.
    """

    def __call__(self, pct: float, message: str) -> None: ...


@runtime_checkable
class LogSink(Protocol):
    """Receives a single log line from a running service."""

    def __call__(self, message: str, level: str) -> None: ...


@dataclass
class CancelToken:
    """Flag a running service polls to learn that it should stop early."""

    _event: threading.Event = field(default_factory=threading.Event)

    def cancel(self) -> None:
        """Request cancellation."""
        self._event.set()

    def is_set(self) -> bool:
        """Whether cancellation has been requested."""
        return self._event.is_set()


@dataclass
class RunContext:
    """Ambient state for one service invocation.

    ``config`` and ``store`` may be omitted only for contexts that never reach a
    service that needs them (:data:`NULL_CONTEXT`); reaching one raises a clear
    error via :attr:`cfg` / :attr:`db` rather than failing on ``None`` later.
    """

    run_id: str = ""
    config: AppConfig | None = None
    store: DataStore | None = None
    progress_sink: ProgressSink | None = None
    log_sink: LogSink | None = None
    cancel_token: CancelToken | None = None

    @property
    def cfg(self) -> AppConfig:
        """The application config, or raise if this context has none."""
        if self.config is None:
            raise RuntimeError("RunContext has no config; pass one to run this service")
        return self.config

    @property
    def db(self) -> DataStore:
        """The data store, or raise if this context has none."""
        if self.store is None:
            raise RuntimeError("RunContext has no store; pass one to run this service")
        return self.store

    def progress(self, pct: float, message: str = "") -> None:
        """Report fractional progress. No-op without a sink."""
        if self.progress_sink is not None:
            self.progress_sink(max(0.0, min(1.0, pct)), message)

    def log(self, message: str, level: str = "info") -> None:
        """Report a log line. No-op without a sink."""
        if self.log_sink is not None:
            self.log_sink(message, level)

    def cancelled(self) -> bool:
        """Whether the caller asked this run to stop."""
        return self.cancel_token is not None and self.cancel_token.is_set()


NULL_CONTEXT = RunContext()
"""Context with no sinks and no cancellation — the default for tests and scripts."""
