"""Job runner: serial execution, progress reporting, cancellation and recovery."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_trade.backtest.result_store import BacktestRows
from quant_trade.config import AppConfig
from quant_trade.data.store import DataStore
from quant_trade.jobs.registry import JOBS, JobSpec
from quant_trade.jobs.runner import JobRunner
from quant_trade.runs.models import ArtifactDraft, ArtifactStorage, RunStatus
from quant_trade.runs.store import RunStore
from quant_trade.services.backtest import BacktestParams, BacktestResult
from quant_trade.services.context import RunContext
from quant_trade.services.models import TrainResult
from quant_trade.services.params import ServiceParams


@dataclass
class EchoResult:
    """Stand-in for a domain result, with something worth indexing."""

    tables: dict[str, int]


class EchoParams(ServiceParams):
    """Parameters for the fake job used throughout these tests."""

    label: str = "echo"
    steps: int = 1
    fail: bool = False
    hang: bool = False


def _echo(params: EchoParams, ctx: RunContext) -> EchoResult:
    """A service that reports progress, logs, and honours cancellation."""
    for i in range(params.steps):
        if params.hang:
            # Poll rather than sleep once, so a cancel lands promptly.
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline:
                if ctx.cancelled():
                    return EchoResult(tables={"echo": i})
                time.sleep(0.01)
        if ctx.cancelled():
            return EchoResult(tables={"echo": i})
        ctx.progress((i + 1) / params.steps, f"{params.label} {i + 1}/{params.steps}")
        ctx.log(f"{params.label} step {i + 1}")
    if params.fail:
        raise ValueError("boom")
    return EchoResult(tables={"echo": params.steps})


def _echo_artifacts(result: EchoResult) -> list[ArtifactDraft]:
    return [
        ArtifactDraft(kind="table", storage=ArtifactStorage.TABLE, ref=ref, row_count=rows)
        for ref, rows in result.tables.items()
    ]


ECHO_SPEC = JobSpec(kind="echo", params_model=EchoParams, service_fn=_echo, artifacts=_echo_artifacts)


@pytest.fixture(autouse=True)
def _register_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register the fake job for the duration of each test."""
    monkeypatch.setitem(JOBS, "echo", ECHO_SPEC)


@pytest.fixture
def runs(tmp_path: Path) -> Iterator[RunStore]:
    store = RunStore(str(tmp_path / "quant.db"))
    yield store
    store.close()


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    cfg = AppConfig()
    cfg.data.db_path = str(tmp_path / "quant.db")
    return cfg


@pytest.fixture
def runner(config: AppConfig, runs: RunStore) -> Iterator[JobRunner]:
    r = JobRunner(config=config, runs=runs, data_store=DataStore(config.data.db_path))
    r.start()
    yield r
    r.stop()


def _submit(runner: JobRunner, runs: RunStore, **params: object) -> str:
    model = EchoParams(**params)  # type: ignore[arg-type]
    run_id = runs.create("echo", model.model_dump_json()).run_id
    runner.submit(run_id)
    return run_id


def _await_terminal(runs: RunStore, run_id: str, timeout: float = 15.0) -> RunStatus:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = runs.get(run_id)
        if record is not None and record.status.is_terminal:
            return record.status
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} never reached a terminal status")


class TestExecution:
    def test_ok_run_reaches_ok(self, runner: JobRunner, runs: RunStore) -> None:
        run_id = _submit(runner, runs, label="sync", steps=3)
        assert _await_terminal(runs, run_id) is RunStatus.OK

        record = runs.get(run_id)
        assert record is not None
        assert record.started_at is not None
        assert record.finished_at is not None
        assert record.progress == pytest.approx(1.0)
        assert record.message == "sync 3/3"
        assert record.error is None

    def test_progress_is_persisted(self, runner: JobRunner, runs: RunStore) -> None:
        run_id = _submit(runner, runs, label="sync", steps=4)
        _await_terminal(runs, run_id)

        record = runs.get(run_id)
        assert record is not None
        assert record.progress == pytest.approx(1.0)

    def test_logs_are_persisted_in_order(self, runner: JobRunner, runs: RunStore) -> None:
        run_id = _submit(runner, runs, label="sync", steps=3)
        _await_terminal(runs, run_id)

        lines = runs.logs(run_id)
        assert [line.seq for line in lines] == list(range(1, len(lines) + 1))
        assert [line.message for line in lines[1:4]] == ["sync step 1", "sync step 2", "sync step 3"]
        assert lines[0].message == "开始执行 echo"
        assert lines[-1].message == "运行完成"

    def test_artifacts_are_registered_from_the_result(self, runner: JobRunner, runs: RunStore) -> None:
        run_id = _submit(runner, runs, steps=7)
        _await_terminal(runs, run_id)

        artifacts = runs.artifacts(run_id)
        assert [(a.ref, a.row_count) for a in artifacts] == [("echo", 7)]

    def test_params_are_read_from_the_record(self, runner: JobRunner, runs: RunStore) -> None:
        """The worker runs what was persisted, not what a caller holds in memory."""
        run_id = _submit(runner, runs, label="from-record", steps=2)
        _await_terminal(runs, run_id)
        assert runs.get(run_id).message == "from-record 2/2"  # type: ignore[union-attr]


class TestFailure:
    def test_exception_marks_failed_with_type_and_message(self, runner: JobRunner, runs: RunStore) -> None:
        run_id = _submit(runner, runs, steps=1, fail=True)
        assert _await_terminal(runs, run_id) is RunStatus.FAILED

        record = runs.get(run_id)
        assert record is not None
        assert record.error == "ValueError: boom"
        assert record.finished_at is not None

    def test_failure_is_logged(self, runner: JobRunner, runs: RunStore) -> None:
        run_id = _submit(runner, runs, steps=1, fail=True)
        _await_terminal(runs, run_id)
        assert any("运行失败" in line.message and line.level == "error" for line in runs.logs(run_id))

    def test_worker_survives_a_failed_run(self, runner: JobRunner, runs: RunStore) -> None:
        """One bad job must not strand everything queued behind it."""
        _await_terminal(runs, _submit(runner, runs, steps=1, fail=True))
        assert _await_terminal(runs, _submit(runner, runs, steps=1)) is RunStatus.OK

    def test_unknown_kind_fails_the_run(self, runner: JobRunner, runs: RunStore) -> None:
        run_id = runs.create("no_such_kind", "{}").run_id
        runner.submit(run_id)
        assert _await_terminal(runs, run_id) is RunStatus.FAILED
        assert "UnknownRunKind" in runs.get(run_id).error  # type: ignore[union-attr]

    def test_invalid_persisted_params_fail_the_run(self, runner: JobRunner, runs: RunStore) -> None:
        run_id = runs.create("echo", '{"steps": "not-an-int"}').run_id
        runner.submit(run_id)
        assert _await_terminal(runs, run_id) is RunStatus.FAILED
        assert "ValidationError" in runs.get(run_id).error  # type: ignore[union-attr]


class TestSerialQueue:
    def test_second_run_waits_for_the_first(self, runner: JobRunner, runs: RunStore) -> None:
        first = runs.create("echo", EchoParams(steps=1, hang=True).model_dump_json()).run_id
        runner.submit(first)

        # Wait until the first job actually holds the worker before queueing.
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not runner.is_running(first):
            time.sleep(0.01)

        second = _submit(runner, runs, steps=1)
        assert runs.get(second).status is RunStatus.PENDING  # type: ignore[union-attr]

        runner.cancel(first)
        assert _await_terminal(runs, first) is RunStatus.CANCELLED
        assert _await_terminal(runs, second) is RunStatus.OK

    def test_runs_execute_one_at_a_time(self, runner: JobRunner, runs: RunStore) -> None:
        """Overlap would mean two writers on one DuckDB file."""
        concurrent = 0
        peak = 0
        lock = threading.Lock()
        original = _echo

        def counting(params: EchoParams, ctx: RunContext) -> EchoResult:
            nonlocal concurrent, peak
            with lock:
                concurrent += 1
                peak = max(peak, concurrent)
            try:
                return original(params, ctx)
            finally:
                with lock:
                    concurrent -= 1

        JOBS["echo"] = JobSpec(kind="echo", params_model=EchoParams, service_fn=counting)
        try:
            ids = [_submit(runner, runs, steps=2) for _ in range(3)]
            for run_id in ids:
                assert _await_terminal(runs, run_id) is RunStatus.OK
        finally:
            JOBS["echo"] = ECHO_SPEC

        assert peak == 1


class TestCancellation:
    def test_cancel_stops_the_run(self, runner: JobRunner, runs: RunStore) -> None:
        run_id = runs.create("echo", EchoParams(steps=1, hang=True).model_dump_json()).run_id
        runner.submit(run_id)

        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not runner.is_running(run_id):
            time.sleep(0.01)

        assert runner.cancel(run_id) is True
        assert _await_terminal(runs, run_id) is RunStatus.CANCELLED

        record = runs.get(run_id)
        assert record is not None
        assert record.error is None
        assert record.finished_at is not None
        assert any("取消" in line.message for line in runs.logs(run_id))

    def test_cancel_is_rejected_for_a_finished_run(self, runner: JobRunner, runs: RunStore) -> None:
        run_id = _submit(runner, runs, steps=1)
        assert _await_terminal(runs, run_id) is RunStatus.OK
        assert runner.cancel(run_id) is False

    def test_cancel_is_rejected_for_a_queued_run(self, runner: JobRunner, runs: RunStore) -> None:
        """Cancellation applies to the running job, not to whatever is queued."""
        blocker = runs.create("echo", EchoParams(steps=1, hang=True).model_dump_json()).run_id
        runner.submit(blocker)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not runner.is_running(blocker):
            time.sleep(0.01)

        queued = _submit(runner, runs, steps=1)
        assert runner.cancel(queued) is False

        runner.cancel(blocker)
        assert _await_terminal(runs, blocker) is RunStatus.CANCELLED
        assert _await_terminal(runs, queued) is RunStatus.OK


class TestRecovery:
    def test_startup_interrupts_leftover_running_rows(self, config: AppConfig, runs: RunStore, tmp_path: Path) -> None:
        run_id = runs.create("echo", EchoParams().model_dump_json()).run_id
        runs.mark_running(run_id)

        runner = JobRunner(config=config, runs=runs, data_store=DataStore(str(tmp_path / "quant.db")))
        runner.start()
        try:
            record = runs.get(run_id)
            assert record is not None
            assert record.status is RunStatus.INTERRUPTED
            assert record.error
        finally:
            runner.stop()

    def test_recovery_does_not_touch_finished_rows(self, config: AppConfig, runs: RunStore, tmp_path: Path) -> None:
        done = runs.create("echo", EchoParams().model_dump_json()).run_id
        runs.mark_running(done)
        runs.finish(done, RunStatus.OK)

        runner = JobRunner(config=config, runs=runs, data_store=DataStore(str(tmp_path / "quant.db")))
        runner.start()
        try:
            assert runs.get(done).status is RunStatus.OK  # type: ignore[union-attr]
        finally:
            runner.stop()

    def test_interrupted_run_can_be_resubmitted(self, config: AppConfig, runs: RunStore, tmp_path: Path) -> None:
        """Recovery never resumes; the answer to an interrupted run is a fresh one."""
        stale = runs.create("echo", EchoParams(steps=2).model_dump_json()).run_id
        runs.mark_running(stale)

        runner = JobRunner(config=config, runs=runs, data_store=DataStore(str(tmp_path / "quant.db")))
        runner.start()
        try:
            assert runs.get(stale).status is RunStatus.INTERRUPTED  # type: ignore[union-attr]
            retry = runs.create("echo", runs.get(stale).params_json).run_id  # type: ignore[union-attr]
            runner.submit(retry)
            assert _await_terminal(runs, retry) is RunStatus.OK
        finally:
            runner.stop()


def _run_inline(runs: RunStore, service: object, config: AppConfig | None = None) -> str:
    """Drive one job straight through ``_execute``, with no worker thread."""
    JOBS["echo"] = JobSpec(kind="echo", params_model=EchoParams, service_fn=service)  # type: ignore[arg-type]
    run_id = runs.create("echo", EchoParams().model_dump_json()).run_id
    runner = JobRunner(config=config or AppConfig(), runs=runs, data_store=DataStore(runs.db_path))
    runner._execute(run_id)
    return run_id


class TestContextWiring:
    def test_progress_and_log_reach_the_registry(self, runs: RunStore) -> None:
        """The sinks the worker injects are the ones that touch the tables."""

        def service(params: EchoParams, ctx: RunContext) -> EchoResult:
            ctx.progress(0.35, "已完成 350/1000")
            ctx.log("回退到备用源", level="warning")
            return EchoResult(tables={})

        run_id = _run_inline(runs, service)

        record = runs.get(run_id)
        assert record is not None
        assert record.status is RunStatus.OK
        assert record.progress == pytest.approx(0.35)
        assert record.message == "已完成 350/1000"
        assert any(line.message == "回退到备用源" and line.level == "warning" for line in runs.logs(run_id))

    def test_service_receives_a_config_and_a_store(self, runs: RunStore) -> None:
        captured: dict[str, object] = {}

        def service(params: EchoParams, ctx: RunContext) -> EchoResult:
            captured["config"] = ctx.cfg
            captured["store"] = ctx.db
            captured["run_id"] = ctx.run_id
            return EchoResult(tables={})

        config = AppConfig()
        run_id = _run_inline(runs, service, config=config)

        assert captured["config"] is config
        assert isinstance(captured["store"], DataStore)
        assert captured["run_id"] == run_id

    def test_cancelled_is_false_until_asked(self, runs: RunStore) -> None:
        seen: list[bool] = []

        def service(params: EchoParams, ctx: RunContext) -> EchoResult:
            seen.append(ctx.cancelled())
            return EchoResult(tables={})

        _run_inline(runs, service)
        assert seen == [False]

    def test_null_progress_and_log_are_ignored_by_services(self, runs: RunStore) -> None:
        """A service that reports nothing still runs to completion."""
        seen: list[str] = []

        def service(params: EchoParams, ctx: RunContext) -> EchoResult:
            seen.append("ran")
            return EchoResult(tables={})

        assert _run_inline(runs, service)
        assert seen == ["ran"]


class TestRegistry:
    def test_data_sync_is_registered(self) -> None:
        from quant_trade.jobs.registry import get_job, known_kinds
        from quant_trade.services.data import DataSyncParams, sync_market_data

        spec = get_job("data_sync")
        assert spec is not None
        assert spec.params_model is DataSyncParams
        assert spec.service_fn is sync_market_data
        assert "data_sync" in known_kinds()

    def test_unknown_kind_is_none(self) -> None:
        from quant_trade.jobs.registry import get_job

        assert get_job("nope") is None

    def test_data_sync_artifacts_skip_empty_tables(self) -> None:
        from quant_trade.jobs.registry import get_job
        from quant_trade.services.data import DataSyncResult

        spec = get_job("data_sync")
        assert spec is not None and spec.artifacts is not None
        result = DataSyncResult(
            tables={"daily_kline": 912, "financials": 0},
            universe_size=1,
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
        )
        drafts = spec.artifacts(result)
        assert [(d.ref, d.row_count) for d in drafts] == [("daily_kline", 912)]
        assert drafts[0].storage is ArtifactStorage.TABLE

    def test_factor_kinds_are_registered(self) -> None:
        from quant_trade.jobs.registry import get_job, known_kinds
        from quant_trade.services.factor_analysis import FactorICComputeParams, compute_factor_ic
        from quant_trade.services.factors import Alpha158Params, compute_alpha158

        compute = get_job("factor_compute")
        assert compute is not None
        assert compute.params_model is Alpha158Params
        assert compute.service_fn is compute_alpha158

        ic = get_job("factor_ic")
        assert ic is not None
        assert ic.params_model is FactorICComputeParams
        assert ic.service_fn is compute_factor_ic

        assert {"data_sync", "factor_compute", "factor_ic"} <= set(known_kinds())

    def test_factor_compute_artifact_points_at_factor_values(self) -> None:
        from quant_trade.jobs.registry import get_job
        from quant_trade.services.factors import Alpha158Result

        spec = get_job("factor_compute")
        assert spec is not None and spec.artifacts is not None
        drafts = spec.artifacts(
            Alpha158Result(
                start=date(2024, 1, 1), end=date(2024, 1, 31), rows_saved=500, factor_count=158, universe_size=20
            )
        )
        assert [(d.ref, d.row_count) for d in drafts] == [("factor_values", 500)]

    def test_factor_compute_artifact_skipped_when_nothing_saved(self) -> None:
        from quant_trade.jobs.registry import get_job
        from quant_trade.services.factors import Alpha158Result

        spec = get_job("factor_compute")
        assert spec is not None and spec.artifacts is not None
        result = Alpha158Result(
            start=date(2024, 1, 1), end=date(2024, 1, 31), rows_saved=0, factor_count=0, universe_size=0
        )
        assert spec.artifacts(result) == []

    def test_factor_ic_artifact_points_at_ic_series(self) -> None:
        from quant_trade.jobs.registry import get_job
        from quant_trade.services.factor_analysis import FactorICComputeResult

        spec = get_job("factor_ic")
        assert spec is not None and spec.artifacts is not None
        drafts = spec.artifacts(
            FactorICComputeResult(factors=["MA20"], forward_periods=[5], rows_saved=42, completed_factors=1)
        )
        assert [(d.ref, d.row_count) for d in drafts] == [("ic_series", 42)]
        assert drafts[0].storage is ArtifactStorage.TABLE

    def test_factor_ic_artifact_skipped_when_nothing_saved(self) -> None:
        from quant_trade.jobs.registry import get_job
        from quant_trade.services.factor_analysis import FactorICComputeResult

        spec = get_job("factor_ic")
        assert spec is not None and spec.artifacts is not None
        assert spec.artifacts(FactorICComputeResult(rows_saved=0)) == []


class TestFactorJobEndToEnd:
    """The registered factor kinds run through the worker, not just the table."""

    @staticmethod
    def _seed(db_path: str) -> None:
        import numpy as np
        import pandas as pd

        from quant_trade.factors.alpha158.storage import save_factor_values

        store = DataStore(db_path)
        codes = [f"{i:06d}.SZ" for i in range(1, 21)]
        days = [date(2024, 1, 1) + timedelta(days=i) for i in range(40)]
        kline, values = [], []
        for code in codes:
            rng = np.random.default_rng(abs(hash(code)) % 2**32)
            closes = 10 + np.cumsum(rng.normal(0, 0.2, len(days)))
            for i, day in enumerate(days):
                kline.append((code, day, closes[i], closes[i], closes[i], closes[i], 1e6, 1e7, None, None))
                values.append(("MA20", code, day, float(rng.normal())))
        store.conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)", kline)
        store.conn.executemany("INSERT INTO trade_calendar VALUES (?, TRUE)", [(d,) for d in days])
        save_factor_values(store, pd.DataFrame(values, columns=["factor_name", "ts_code", "trade_date", "value"]))

    def test_factor_ic_run_reaches_ok_and_registers_its_artifact(
        self, runner: JobRunner, runs: RunStore, config: AppConfig
    ) -> None:
        from quant_trade.jobs.registry import get_job

        self._seed(config.data.db_path)
        model = get_job("factor_ic")
        assert model is not None
        validated = model.params_model(
            factors=["MA20"],
            universe=[f"{i:06d}.SZ" for i in range(1, 21)],
            forward_periods=[5],
        )
        run_id = runs.create("factor_ic", validated.model_dump_json()).run_id
        runner.submit(run_id)

        assert _await_terminal(runs, run_id, timeout=60.0) is RunStatus.OK
        drafts = runs.artifacts(run_id)
        assert [d.ref for d in drafts] == ["ic_series"]
        assert drafts[0].row_count and drafts[0].row_count > 0

    def test_data_sync_path_is_unaffected(self, runner: JobRunner, runs: RunStore) -> None:
        run_id = _submit(runner, runs, label="sync", steps=2)
        assert _await_terminal(runs, run_id) is RunStatus.OK


class TestBacktestJobEndToEnd:
    """The registered ``backtest`` kind, executed by the real worker."""

    @staticmethod
    def _seed(db_path: str, n_days: int = 500) -> None:
        """A tradeable universe: calendar, members, index weights and prices."""
        store = DataStore(db_path)
        codes = [f"{i:06d}.SZ" for i in range(1, 6)]
        days: list[date] = []
        day = date(2024, 1, 1)
        while len(days) < n_days:
            if day.weekday() < 5:
                days.append(day)
            day += timedelta(days=1)
        store.conn.executemany("INSERT INTO trade_calendar VALUES (?, TRUE)", [(d,) for d in days])
        store.conn.executemany(
            "INSERT INTO stock_basic (ts_code, name, industry, market, list_date) VALUES (?, ?, ?, ?, ?)",
            [(code, code, "ind", "main", date(2020, 1, 1)) for code in codes],
        )
        store.conn.executemany(
            "INSERT INTO index_weights (index_code, ts_code, weight, in_date, out_date) VALUES (?, ?, ?, ?, ?)",
            [("000300.SH", code, 0.2, date(2020, 1, 1), date(2030, 1, 1)) for code in codes],
        )
        rows = []
        for code in codes:
            rng = np.random.default_rng(abs(hash(code)) % 2**32)
            close = 10.0
            for trading_day in days:
                close *= 1 + rng.normal(0, 0.01)
                rows.append((code, trading_day, close, close, close, close, 1e6, 1e7, None, None))
        benchmark = 1.0
        for trading_day in days:
            benchmark *= 1 + 0.0002
            rows.append(("000300.SH", trading_day, benchmark, benchmark, benchmark, benchmark, 1e9, 1e10, None, None))
        store.conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        store.close()

    def test_backtest_run_reaches_ok_and_registers_artifacts(
        self, runner: JobRunner, runs: RunStore, config: AppConfig
    ) -> None:
        from quant_trade.jobs.registry import get_job

        self._seed(config.data.db_path)
        spec = get_job("backtest")
        assert spec is not None
        params = spec.params_model(start=date(2024, 6, 1), end=date(2024, 12, 31))
        run_id = runs.create("backtest", params.model_dump_json()).run_id
        runner.submit(run_id)

        assert _await_terminal(runs, run_id, timeout=120.0) is RunStatus.OK

        drafts = runs.artifacts(run_id)
        assert {draft.ref for draft in drafts} == {"backtest_nav", "backtest_metric"}
        assert all(draft.row_count and draft.row_count > 0 for draft in drafts)
        assert all(draft.meta["strategy"] for draft in drafts)

    def test_results_are_readable_through_the_query_service(
        self, runner: JobRunner, runs: RunStore, config: AppConfig
    ) -> None:
        from quant_trade.services.backtest_query import BacktestDetailParams, backtest_detail

        self._seed(config.data.db_path, n_days=200)
        params = BacktestParams(start=date(2024, 6, 1), end=date(2024, 12, 31))
        run_id = runs.create("backtest", params.model_dump_json()).run_id
        runner.submit(run_id)
        assert _await_terminal(runs, run_id, timeout=120.0) is RunStatus.OK

        store = DataStore(config.data.db_path)
        try:
            detail = backtest_detail(
                BacktestDetailParams(run_id=run_id), RunContext(run_id=run_id, config=config, store=store)
            )
        finally:
            store.close()

        assert detail.found is True
        assert detail.series is not None and detail.series.nav, "the detail page needs a curve"
        assert detail.metrics, "the detail page needs metrics"
        assert detail.cash is not None and detail.total_value is not None

    def test_cancel_keeps_the_weeks_that_ran(self, runner: JobRunner, runs: RunStore, config: AppConfig) -> None:
        from quant_trade.services.backtest_query import BacktestDetailParams, backtest_detail

        self._seed(config.data.db_path)
        params = BacktestParams(start=date(2024, 6, 1), end=date(2025, 12, 31))
        run_id = runs.create("backtest", params.model_dump_json()).run_id
        runner.submit(run_id)

        # Cancel after the first week has been reported, not the moment the run
        # starts: a cancel that beats week one is a valid outcome too, but it
        # would not exercise the guarantee under test — that the weeks which did
        # run are kept.
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline:
            record = runs.get(run_id)
            if record is not None and record.status.is_terminal:
                break
            if record is not None and record.progress > 0.0:
                break
            time.sleep(0.005)
        assert runner.cancel(run_id) is True

        status = _await_terminal(runs, run_id, timeout=120.0)
        assert status is RunStatus.CANCELLED

        record = runs.get(run_id)
        assert record is not None
        assert record.progress < 1.0, "a cancelled backtest must not report full progress"

        store = DataStore(config.data.db_path)
        try:
            detail = backtest_detail(
                BacktestDetailParams(run_id=run_id), RunContext(run_id=run_id, config=config, store=store)
            )
        finally:
            store.close()
        assert detail.found is True
        assert detail.status == "cancelled"
        assert detail.series is not None and detail.series.nav, "completed weeks must survive the cancel"

    def test_other_kinds_are_unaffected(self, runner: JobRunner, runs: RunStore) -> None:
        run_id = _submit(runner, runs, label="sync", steps=2)
        assert _await_terminal(runs, run_id) is RunStatus.OK


class TestBacktestRegistry:
    def test_backtest_kind_is_registered(self) -> None:
        from quant_trade.jobs.registry import get_job, known_kinds
        from quant_trade.services.backtest import BacktestParams, run_backtest_service

        spec = get_job("backtest")
        assert spec is not None
        assert spec.params_model is BacktestParams
        assert spec.service_fn is run_backtest_service
        assert "backtest" in known_kinds()

    def test_artifacts_skip_a_run_that_wrote_nothing(self) -> None:
        from quant_trade.jobs.registry import get_job

        spec = get_job("backtest")
        assert spec is not None and spec.artifacts is not None
        empty = BacktestResult(
            start=date(2024, 1, 1), end=date(2024, 1, 31), strategy="factor_ranking", initial_capital=1e5
        )
        assert spec.artifacts(empty) == []

    def test_artifacts_name_both_tables(self) -> None:
        from quant_trade.jobs.registry import get_job

        spec = get_job("backtest")
        assert spec is not None and spec.artifacts is not None
        result = BacktestResult(
            start=date(2024, 1, 1), end=date(2024, 1, 31), strategy="factor_ranking", initial_capital=1e5
        )
        result.rows_saved = BacktestRows(nav=21, metrics=11, trades=4, positions=15)

        drafts = spec.artifacts(result)
        assert [(draft.ref, draft.row_count) for draft in drafts] == [("backtest_nav", 21), ("backtest_metric", 11)]
        assert drafts[0].meta["strategy"] == "factor_ranking"
        assert drafts[0].meta["start"] == "2024-01-01"

    def test_unknown_kind_is_still_none(self) -> None:
        from quant_trade.jobs.registry import get_job

        assert get_job("no_such_kind") is None


class TestModelTrainRegistry:
    """The training kind is wired up, and its artifacts are per-result."""

    def test_model_train_is_registered(self) -> None:
        from quant_trade.jobs.registry import get_job, known_kinds
        from quant_trade.services.models import TrainParams, train_model

        spec = get_job("model_train")
        assert spec is not None
        assert spec.params_model is TrainParams
        assert spec.service_fn is train_model
        assert "model_train" in known_kinds()

    def test_existing_kinds_are_untouched(self) -> None:
        from quant_trade.jobs.registry import known_kinds

        assert {"data_sync", "factor_compute", "factor_ic", "backtest"} <= set(known_kinds())

    def _result(self, **overrides: object) -> TrainResult:
        result = TrainResult(
            start=date(2024, 1, 1),
            end=date(2024, 6, 30),
            output_path="data/predictions/model_ranking.parquet",
            prediction_rows=120,
            windows_trained=3,
        )
        for name, value in overrides.items():
            setattr(result, name, value)
        return result

    def test_artifacts_name_all_three_outputs(self) -> None:
        from quant_trade.jobs.registry import get_job
        from quant_trade.models.persistence import ModelRows

        spec = get_job("model_train")
        assert spec is not None and spec.artifacts is not None
        result = self._result(rows_saved=ModelRows(ic=40, importance=158, metrics=6))

        drafts = spec.artifacts(result)
        assert [(draft.ref, draft.row_count) for draft in drafts] == [
            ("model_ic_series", 40),
            ("model_feature_importance", 158),
            ("data/predictions/model_ranking.parquet", 120),
        ]
        assert drafts[0].storage is ArtifactStorage.TABLE
        assert drafts[2].storage is ArtifactStorage.PARQUET
        assert drafts[0].meta["windows_trained"] == 3
        assert drafts[0].meta["start"] == "2024-01-01"

    def test_artifacts_skip_what_was_not_produced(self) -> None:
        from quant_trade.jobs.registry import get_job
        from quant_trade.models.persistence import ModelRows

        spec = get_job("model_train")
        assert spec is not None and spec.artifacts is not None

        no_ic = self._result(rows_saved=ModelRows(importance=158, metrics=6))
        assert [draft.ref for draft in spec.artifacts(no_ic)] == [
            "model_feature_importance",
            "data/predictions/model_ranking.parquet",
        ]

    def test_artifacts_empty_when_nothing_was_produced(self) -> None:
        from quant_trade.jobs.registry import get_job

        spec = get_job("model_train")
        assert spec is not None and spec.artifacts is not None
        assert spec.artifacts(self._result(prediction_rows=0)) == []


class TestModelTrainJobEndToEnd:
    """A training run goes through the worker: progress, storage, artifacts.

    The walk-forward pass itself is stubbed — it is minutes of LightGBM and its
    own behaviour is covered elsewhere. Everything downstream of it is real: the
    params are validated by the registry, the service persists under the run id
    the worker supplied, and the artifacts come from the registered mapper.
    """

    @staticmethod
    def _seed(db_path: str) -> None:
        store = DataStore(db_path)
        store.conn.execute("INSERT INTO trade_calendar VALUES (?, TRUE)", [date(2024, 6, 28)])
        store.close()

    @staticmethod
    def _stub(monkeypatch: pytest.MonkeyPatch, predictions: pd.DataFrame) -> None:
        """Replace the walk-forward pass, keeping the return shape real."""
        from quant_trade.models import WalkForwardResult
        from quant_trade.models import train as train_module
        from quant_trade.services.context import NULL_CONTEXT

        def _fake(
            store: DataStore,
            universe: list[str],
            start: date,
            end: date,
            factors: list[str] | None = None,
            config: object = None,
            ctx: RunContext = NULL_CONTEXT,
        ) -> WalkForwardResult:
            ctx.progress(0.5, "training")
            ctx.log("window 1/1")
            return WalkForwardResult(
                predictions=predictions,
                feature_matrix=pd.DataFrame(),
                feature_importance=pd.DataFrame(
                    {"factor": ["MA20", "RSV5"], "importance": [0.7, 0.3], "std": [0.02, 0.01]}
                ),
                windows_trained=1,
            )

        monkeypatch.setattr(train_module, "walk_forward_train", _fake)
        monkeypatch.setattr("quant_trade.services.models.walk_forward_train", _fake)

    def test_run_reaches_ok_and_stores_its_results(
        self, monkeypatch: pytest.MonkeyPatch, runner: JobRunner, runs: RunStore, config: AppConfig, tmp_path: Path
    ) -> None:
        from quant_trade.jobs.registry import get_job

        self._seed(config.data.db_path)
        predictions = pd.DataFrame(
            {"ts_code": ["600000.SH", "000001.SZ"], "trade_date": [date(2024, 6, 28)] * 2, "score": [0.9, 0.1]}
        )
        self._stub(monkeypatch, predictions)

        spec = get_job("model_train")
        assert spec is not None
        params = spec.params_model(
            start=date(2024, 1, 1),
            end=date(2024, 6, 28),
            output_path=str(tmp_path / "model_ranking.parquet"),
        )
        run_id = runs.create("model_train", params.model_dump_json()).run_id
        runner.submit(run_id)

        assert _await_terminal(runs, run_id) is RunStatus.OK
        assert Path(str(tmp_path / "model_ranking.parquet")).is_file()

        store = DataStore(config.data.db_path)
        try:
            importance = store.conn.execute(
                "SELECT COUNT(*) FROM model_feature_importance WHERE run_id = ?", [run_id]
            ).fetchone()
            metrics = store.conn.execute("SELECT COUNT(*) FROM model_metric WHERE run_id = ?", [run_id]).fetchone()
        finally:
            store.close()
        assert importance is not None and importance[0] == 2
        assert metrics is not None and metrics[0] > 0

        # Two artifacts, not three: the stub's labels are empty, so no day had
        # enough ranked names to produce an IC row, and nothing is registered
        # for a table that stayed empty.
        artifacts = runs.artifacts(run_id)
        assert [artifact.ref for artifact in artifacts] == [
            "model_feature_importance",
            str(tmp_path / "model_ranking.parquet"),
        ]
        assert artifacts[0].row_count == 2
        assert artifacts[1].storage is ArtifactStorage.PARQUET

    def test_run_without_predictions_registers_nothing(
        self, monkeypatch: pytest.MonkeyPatch, runner: JobRunner, runs: RunStore, config: AppConfig, tmp_path: Path
    ) -> None:
        from quant_trade.jobs.registry import get_job

        self._seed(config.data.db_path)
        self._stub(monkeypatch, pd.DataFrame(columns=["ts_code", "trade_date", "score"]))

        spec = get_job("model_train")
        assert spec is not None
        params = spec.params_model(
            start=date(2024, 1, 1), end=date(2024, 6, 28), output_path=str(tmp_path / "none.parquet")
        )
        run_id = runs.create("model_train", params.model_dump_json()).run_id
        runner.submit(run_id)

        assert _await_terminal(runs, run_id) is RunStatus.OK
        assert runs.artifacts(run_id) == []
        assert not Path(str(tmp_path / "none.parquet")).exists()


class TestWeeklyJob:
    """The weekly kind: registry entry and the artifact it registers."""

    def test_weekly_is_registered(self) -> None:
        from quant_trade.jobs.registry import get_job
        from quant_trade.services.report import WeeklyReportParams, generate_weekly

        spec = get_job("weekly")
        assert spec is not None
        assert spec.params_model is WeeklyReportParams
        assert spec.service_fn is generate_weekly
        assert spec.artifacts is not None

    def test_artifact_points_at_the_report_file(self) -> None:
        from quant_trade.jobs.registry import get_job
        from quant_trade.services.report import WeeklyReportResult

        spec = get_job("weekly")
        assert spec is not None and spec.artifacts is not None
        drafts = spec.artifacts(
            WeeklyReportResult(
                signal_date=date(2026, 7, 24),
                report_path="reports/weekly_2026_07_24.html",
                order_count=5,
                nav_points=120,
                trade_count=12,
            )
        )

        assert len(drafts) == 1
        assert drafts[0].storage is ArtifactStorage.HTML
        assert drafts[0].ref == "reports/weekly_2026_07_24.html"
        # None, not 0: a report is not a row set, and 0 reads downstream as
        # "this report is empty".
        assert drafts[0].row_count is None
        assert drafts[0].meta == {"signal_date": "2026-07-24", "order_count": 5, "trade_count": 12}

    def test_cancelled_run_registers_no_artifact(self) -> None:
        from quant_trade.jobs.registry import get_job
        from quant_trade.services.report import WeeklyReportResult

        spec = get_job("weekly")
        assert spec is not None and spec.artifacts is not None
        cancelled = WeeklyReportResult(
            signal_date=date(2026, 7, 24), report_path="", order_count=0, nav_points=0, trade_count=0
        )
        assert spec.artifacts(cancelled) == []

    def test_worker_registers_the_report_artifact(
        self, monkeypatch: pytest.MonkeyPatch, runner: JobRunner, runs: RunStore, tmp_path: Path
    ) -> None:
        """The runner and the mapper together, with only the pipeline stubbed."""
        from quant_trade.jobs.registry import get_job
        from quant_trade.services.report import WeeklyReportResult

        report = tmp_path / "weekly_2026_07_24.html"
        report.write_text("<html></html>", encoding="utf-8")

        spec = get_job("weekly")
        assert spec is not None
        stub = JobSpec(
            kind="weekly",
            params_model=spec.params_model,
            service_fn=lambda params, ctx: WeeklyReportResult(
                signal_date=date(2026, 7, 24),
                report_path=str(report),
                order_count=5,
                nav_points=120,
                trade_count=12,
            ),
            artifacts=spec.artifacts,
        )
        monkeypatch.setitem(JOBS, "weekly", stub)

        run_id = runs.create("weekly", spec.params_model().model_dump_json()).run_id
        runner.submit(run_id)

        assert _await_terminal(runs, run_id) is RunStatus.OK
        artifacts = runs.artifacts(run_id)
        assert len(artifacts) == 1
        assert artifacts[0].storage is ArtifactStorage.HTML
        assert artifacts[0].ref == str(report)
        assert artifacts[0].row_count is None
        assert artifacts[0].meta["signal_date"] == "2026-07-24"

    def test_existing_kinds_are_untouched(self) -> None:
        """Registering weekly adds a kind; it must not displace any.

        Checked as a superset rather than an exact list: other tests in this
        module register throwaway kinds in ``JOBS`` and a leaked one would fail
        an equality assertion for a reason that has nothing to do with weekly.
        """
        from quant_trade.jobs.registry import known_kinds

        assert set(known_kinds()) >= {
            "backtest",
            "data_sync",
            "factor_compute",
            "factor_ic",
            "model_train",
            "weekly",
        }


class TestStrategySignalJob:
    """The strategy-signals kind: registry entry and the artifact it registers."""

    def test_registered_against_the_persisting_service(self) -> None:
        """Not the pure one — only the persisting entry point writes rows."""
        from quant_trade.jobs.registry import get_job
        from quant_trade.services.strategies import (
            SignalParams,
            generate_strategy_signals,
            run_strategy_signals,
        )

        spec = get_job("strategy_signals")
        assert spec is not None
        assert spec.params_model is SignalParams
        assert spec.service_fn is run_strategy_signals
        assert spec.service_fn is not generate_strategy_signals
        assert spec.artifacts is not None

    def test_artifact_points_at_the_signal_table(self) -> None:
        from quant_trade.jobs.registry import get_job
        from quant_trade.services.strategies import SignalOrder, SignalSummary

        spec = get_job("strategy_signals")
        assert spec is not None and spec.artifacts is not None
        drafts = spec.artifacts(
            SignalSummary(
                signal_date=date(2026, 7, 24),
                strategy="factor_ranking",
                universe_size=300,
                orders=[SignalOrder("000001.SZ", 0.5, "BUY", "靠前")],
            )
        )

        assert len(drafts) == 1
        assert drafts[0].storage is ArtifactStorage.TABLE
        assert drafts[0].ref == "strategy_signal"
        assert drafts[0].row_count == 1
        assert drafts[0].meta == {
            "strategy": "factor_ranking",
            "signal_date": "2026-07-24",
            "universe_size": 300,
        }

    def test_no_orders_registers_nothing(self) -> None:
        from quant_trade.jobs.registry import get_job
        from quant_trade.services.strategies import SignalSummary

        spec = get_job("strategy_signals")
        assert spec is not None and spec.artifacts is not None
        empty = SignalSummary(signal_date=date(2026, 7, 24), strategy="factor_ranking", universe_size=0)
        assert spec.artifacts(empty) == []

    def test_existing_kinds_are_untouched(self) -> None:
        from quant_trade.jobs.registry import known_kinds

        assert set(known_kinds()) >= {
            "backtest",
            "data_sync",
            "factor_compute",
            "factor_ic",
            "model_train",
            "strategy_signals",
            "weekly",
        }
