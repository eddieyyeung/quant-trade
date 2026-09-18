"""The single-worker job runner.

Concurrency is fixed at one on purpose. DuckDB has a single writer per file,
and a research workflow is serial anyway — nobody syncs data while a backtest
is running. Serialising costs a short queue wait and buys away every race over
``daily_kline``.
"""

from __future__ import annotations

import queue
import threading
from typing import Any

from loguru import logger

from quant_trade.config import AppConfig
from quant_trade.data.store import DataStore
from quant_trade.jobs.registry import get_job
from quant_trade.runs.models import RunStatus
from quant_trade.runs.store import RunStore
from quant_trade.services.context import CancelToken, RunContext

_POLL_SECONDS = 0.5
"""How long the worker blocks on an empty queue before re-checking for shutdown."""


class JobRunner:
    """Executes queued runs one at a time on a dedicated thread."""

    def __init__(self, config: AppConfig, runs: RunStore, data_store: DataStore | None = None):
        self.config = config
        self.runs = runs
        self.data_store = data_store if data_store is not None else DataStore(config.data.db_path)
        self._queue: queue.Queue[str | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._cancels: dict[str, CancelToken] = {}
        self._lock = threading.Lock()

    # ---- lifecycle ----

    def start(self) -> None:
        """Recover interrupted runs, then begin consuming the queue."""
        recovered = self.runs.mark_interrupted()
        if recovered:
            logger.warning("Marked {} run(s) as interrupted at startup", recovered)
        self._thread = threading.Thread(target=self._loop, name="job-runner", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the worker to finish and wait for it, up to ``timeout``.

        A job still executing is not killed — cancellation is cooperative, so
        the sentinel is only observed once the current run returns.
        """
        thread, self._thread = self._thread, None
        if thread is None:
            return
        self._queue.put(None)
        thread.join(timeout)
        if thread.is_alive():  # pragma: no cover - only when a service ignores cancellation
            logger.warning("Job runner did not stop within {}s", timeout)

    # ---- submission and cancellation ----

    def submit(self, run_id: str) -> None:
        """Queue an already-persisted run for execution."""
        self._queue.put(run_id)

    def cancel(self, run_id: str) -> bool:
        """Request cancellation of a running job.

        Returns ``False`` when the job is not currently executing, which is the
        caller's signal to answer with a conflict rather than a success. The
        request is cooperative: the service stops at its next checkpoint.
        """
        with self._lock:
            token = self._cancels.get(run_id)
        if token is None:
            return False
        token.cancel()
        return True

    def is_running(self, run_id: str) -> bool:
        """Whether this run currently holds the worker."""
        with self._lock:
            return run_id in self._cancels

    # ---- worker ----

    def _loop(self) -> None:
        while True:
            try:
                run_id = self._queue.get(timeout=_POLL_SECONDS)
            except queue.Empty:
                continue
            if run_id is None:
                return
            try:
                self._execute(run_id)
            except Exception:
                # A crash here would kill the worker and strand every later run
                # in `pending`, so nothing escapes this handler.
                logger.exception("Job runner failed while executing run {}", run_id)
            finally:
                self._queue.task_done()

    def _execute(self, run_id: str) -> None:
        record = self.runs.get(run_id)
        if record is None:
            logger.warning("Queued run {} no longer exists; skipping", run_id)
            return

        spec = get_job(record.kind)
        if spec is None:
            self.runs.finish(run_id, RunStatus.FAILED, error=f"UnknownRunKind: {record.kind!r}")
            return

        try:
            params = spec.params_model.model_validate_json(record.params_json)
        except Exception as e:
            self.runs.finish(run_id, RunStatus.FAILED, error=f"{type(e).__name__}: {e}")
            return

        # The token is registered before the status flips to `running`, so a
        # cancel that observes `running` always finds a token to set.
        token = CancelToken()
        with self._lock:
            self._cancels[run_id] = token
        self.runs.mark_running(run_id)
        self.runs.append_log(run_id, f"开始执行 {record.kind}")

        result: Any = None
        try:
            result = spec.service_fn(params, self._context(run_id, token))
        except Exception as e:
            status: RunStatus = RunStatus.FAILED
            error: str | None = f"{type(e).__name__}: {e}"
            self.runs.append_log(run_id, f"运行失败: {error}", level="error")
            logger.exception("Run {} ({}) failed", run_id, record.kind)
        else:
            if token.is_set():
                status, error = RunStatus.CANCELLED, None
                self.runs.append_log(run_id, "运行已被取消，保留已完成部分", level="warning")
            else:
                status, error = RunStatus.OK, None
                self.runs.append_log(run_id, "运行完成")

        if result is not None and spec.artifacts is not None:
            try:
                for draft in spec.artifacts(result):
                    self.runs.add_artifact(run_id, draft)
            except Exception:
                logger.exception("Run {} produced artifacts that could not be registered", run_id)

        self.runs.finish(run_id, status, error=error)
        with self._lock:
            self._cancels.pop(run_id, None)

    def _context(self, run_id: str, token: CancelToken) -> RunContext:
        """A context whose sinks write straight to the run registry.

        Log lines land in ``run_log`` rather than an in-memory subscriber list:
        the SSE endpoint polls that table, so a run's logs stay readable after
        the process that produced them is gone.
        """
        runs = self.runs

        def progress_sink(pct: float, message: str) -> None:
            runs.update_progress(run_id, pct, message)

        def log_sink(message: str, level: str) -> None:
            runs.append_log(run_id, message, level)

        return RunContext(
            run_id=run_id,
            config=self.config,
            store=self.data_store,
            progress_sink=progress_sink,
            log_sink=log_sink,
            cancel_token=token,
        )
