"""Run registry persistence: lifecycle, log ordering, artifacts and paging."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from quant_trade.runs.models import ArtifactDraft, ArtifactStorage, RunStatus, RunTrigger
from quant_trade.runs.store import DEFAULT_INTERRUPTED_ERROR, DEFAULT_NOT_STARTED_ERROR, RunStore


@pytest.fixture
def store(tmp_path: Path) -> Iterator[RunStore]:
    s = RunStore(str(tmp_path / "quant.db"))
    yield s
    s.close()


def _create(store: RunStore, kind: str = "data_sync", **kwargs: object) -> str:
    return store.create(kind, '{"include_financials": false}', **kwargs).run_id  # type: ignore[arg-type]


class TestCreate:
    def test_new_run_is_pending_with_params(self, store: RunStore) -> None:
        record = store.create("data_sync", '{"a": 1}')
        assert record.status is RunStatus.PENDING
        assert record.params_json == '{"a": 1}'
        assert record.trigger is RunTrigger.MANUAL
        assert record.progress == 0.0
        assert record.started_at is None
        assert record.created_at is not None

    def test_params_round_trip(self, store: RunStore) -> None:
        """A record must carry enough to re-run the same job."""

        from quant_trade.services.data import DataSyncParams

        params = DataSyncParams(include_financials=True, start_date="2024-01-01")  # type: ignore[arg-type]
        run_id = store.create("data_sync", params.model_dump_json()).run_id

        reloaded = store.get(run_id)
        assert reloaded is not None
        assert DataSyncParams.model_validate_json(reloaded.params_json) == params

    def test_idempotency_key_is_optional_and_repeatable(self, store: RunStore) -> None:
        """Null keys never collide; a real duplicate is rejected."""
        _create(store)
        _create(store)

        assert store.create("data_sync", "{}", idempotency_key="k1").idempotency_key == "k1"
        with pytest.raises(Exception, match="[Cc]onstraint"):
            store.create("data_sync", "{}", idempotency_key="k1")

    def test_same_key_allowed_under_a_different_kind(self, store: RunStore) -> None:
        store.create("data_sync", "{}", idempotency_key="k1")
        assert store.create("factor_compute", "{}", idempotency_key="k1").kind == "factor_compute"


class TestStatusMachine:
    def test_running_then_ok(self, store: RunStore) -> None:
        run_id = _create(store)
        store.mark_running(run_id)
        running = store.get(run_id)
        assert running is not None and running.status is RunStatus.RUNNING
        assert running.started_at is not None

        store.finish(run_id, RunStatus.OK)
        done = store.get(run_id)
        assert done is not None and done.status is RunStatus.OK
        assert done.finished_at is not None
        assert done.error is None

    def test_failure_records_the_error(self, store: RunStore) -> None:
        run_id = _create(store)
        store.mark_running(run_id)
        store.finish(run_id, RunStatus.FAILED, error="ValueError: boom")

        record = store.get(run_id)
        assert record is not None and record.status is RunStatus.FAILED
        assert record.error == "ValueError: boom"

    def test_non_terminal_finish_is_rejected(self, store: RunStore) -> None:
        run_id = _create(store)
        with pytest.raises(ValueError, match="not a terminal status"):
            store.finish(run_id, RunStatus.RUNNING)

    def test_progress_updates_message(self, store: RunStore) -> None:
        run_id = _create(store)
        store.mark_running(run_id)
        store.update_progress(run_id, 0.35, "已完成 350/1000")

        record = store.get(run_id)
        assert record is not None
        assert record.progress == pytest.approx(0.35)
        assert record.message == "已完成 350/1000"

    def test_terminal_statuses(self) -> None:
        assert {s.value for s in RunStatus} == {
            "pending",
            "running",
            "ok",
            "failed",
            "cancelled",
            "interrupted",
        }
        assert not RunStatus.PENDING.is_terminal
        assert not RunStatus.RUNNING.is_terminal
        assert all(s.is_terminal for s in (RunStatus.OK, RunStatus.FAILED, RunStatus.CANCELLED, RunStatus.INTERRUPTED))


class TestInterruptedRecovery:
    def test_leftover_running_rows_are_interrupted(self, store: RunStore) -> None:
        stale = _create(store)
        store.mark_running(stale)
        finished = _create(store)
        store.mark_running(finished)
        store.finish(finished, RunStatus.OK)

        assert store.mark_interrupted() == 1

        record = store.get(stale)
        assert record is not None and record.status is RunStatus.INTERRUPTED
        assert record.error == DEFAULT_INTERRUPTED_ERROR
        assert record.finished_at is not None
        # A completed run must not be touched.
        done = store.get(finished)
        assert done is not None and done.status is RunStatus.OK

    def test_leftover_queued_rows_are_interrupted(self, store: RunStore) -> None:
        """A queued run lives only in the worker's in-memory queue."""
        queued = _create(store)

        assert store.mark_interrupted() == 1

        record = store.get(queued)
        assert record is not None
        assert record.status is RunStatus.INTERRUPTED
        assert record.error == DEFAULT_NOT_STARTED_ERROR
        assert record.finished_at is not None

    def test_running_and_queued_are_counted_together(self, store: RunStore) -> None:
        running = _create(store)
        store.mark_running(running)
        _create(store)
        _create(store)

        assert store.mark_interrupted() == 3
        assert {r.status for r in store.list_runs()[0]} == {RunStatus.INTERRUPTED}

    def test_no_stale_rows_is_a_no_op(self, store: RunStore) -> None:
        done = _create(store)
        store.finish(done, RunStatus.OK)
        assert store.mark_interrupted() == 0

    def test_interrupted_run_can_be_resubmitted(self, store: RunStore) -> None:
        """Nothing about the original row blocks a fresh run of the same params."""
        run_id = _create(store)
        store.mark_running(run_id)
        store.mark_interrupted()
        original = store.get(run_id)
        assert original is not None

        retry = store.create(original.kind, original.params_json)
        assert retry.run_id != run_id
        assert retry.params_json == original.params_json


class TestLogs:
    def test_seq_is_contiguous_from_one(self, store: RunStore) -> None:
        run_id = _create(store)
        for i in range(5):
            assert store.append_log(run_id, f"line {i}").seq == i + 1

        lines = store.logs(run_id)
        assert [line.seq for line in lines] == [1, 2, 3, 4, 5]
        assert [line.message for line in lines] == [f"line {i}" for i in range(5)]

    def test_level_is_persisted(self, store: RunStore) -> None:
        run_id = _create(store)
        store.append_log(run_id, "回退到备用源", level="warning")
        assert store.logs(run_id)[0].level == "warning"

    def test_logs_are_scoped_per_run(self, store: RunStore) -> None:
        first, second = _create(store), _create(store)
        store.append_log(first, "a")
        store.append_log(second, "b")
        store.append_log(first, "c")

        assert [line.seq for line in store.logs(first)] == [1, 2]
        assert [line.message for line in store.logs(first)] == ["a", "c"]
        assert [line.seq for line in store.logs(second)] == [1]

    def test_after_seq_returns_only_new_lines(self, store: RunStore) -> None:
        run_id = _create(store)
        for i in range(6):
            store.append_log(run_id, f"line {i}")

        tail = store.logs(run_id, after_seq=4)
        assert [line.seq for line in tail] == [5, 6]
        assert store.logs(run_id, after_seq=6) == []

    def test_limit_caps_the_batch(self, store: RunStore) -> None:
        run_id = _create(store)
        for i in range(10):
            store.append_log(run_id, f"line {i}")
        assert len(store.logs(run_id, after_seq=0, limit=3)) == 3


class TestArtifacts:
    def test_register_structured_artifact(self, store: RunStore) -> None:
        run_id = _create(store)
        record = store.add_artifact(
            run_id,
            ArtifactDraft(kind="daily_kline", storage=ArtifactStorage.TABLE, ref="daily_kline", row_count=912),
        )
        assert record.storage is ArtifactStorage.TABLE
        assert record.ref == "daily_kline"
        assert record.row_count == 912
        assert record.artifact_id

    def test_meta_json_round_trips(self, store: RunStore) -> None:
        run_id = _create(store)
        store.add_artifact(
            run_id,
            ArtifactDraft(kind="report", storage=ArtifactStorage.HTML, ref="reports/w.html", meta={"week": "2026-W37"}),
        )
        assert store.artifacts(run_id)[0].meta == {"week": "2026-W37"}

    def test_artifacts_are_scoped_per_run(self, store: RunStore) -> None:
        first, second = _create(store), _create(store)
        store.add_artifact(first, ArtifactDraft(kind="a", storage=ArtifactStorage.TABLE, ref="t1"))
        store.add_artifact(second, ArtifactDraft(kind="b", storage=ArtifactStorage.PARQUET, ref="p.parquet"))

        assert [a.ref for a in store.artifacts(first)] == ["t1"]
        assert [a.ref for a in store.artifacts(second)] == ["p.parquet"]

    def test_row_count_is_optional(self, store: RunStore) -> None:
        """HTML reports have no row count; the column stays null rather than 0."""
        run_id = _create(store)
        record = store.add_artifact(run_id, ArtifactDraft(kind="report", storage=ArtifactStorage.HTML, ref="r.html"))
        assert record.row_count is None
        assert store.artifacts(run_id)[0].row_count is None


class TestPaging:
    def test_newest_first(self, store: RunStore) -> None:
        ids = [_create(store) for _ in range(3)]
        for i, run_id in enumerate(ids):
            store.mark_running(run_id)
            store.update_progress(run_id, 0, f"run {i}")

        listing, total = store.list_runs()
        assert total == 3
        assert [r.run_id for r in listing] == list(reversed(ids))

    def test_pending_runs_still_appear(self, store: RunStore) -> None:
        """A queued run has no started_at and must not sort out of the list."""
        started = _create(store)
        store.mark_running(started)
        queued = _create(store)

        listing, total = store.list_runs()
        assert total == 2
        assert {r.run_id for r in listing} == {started, queued}

    def test_offset_walks_the_pages(self, store: RunStore) -> None:
        ids = [_create(store) for _ in range(5)]
        for run_id in ids:
            store.mark_running(run_id)

        first, total = store.list_runs(limit=2, offset=0)
        second, _ = store.list_runs(limit=2, offset=2)
        third, _ = store.list_runs(limit=2, offset=4)

        assert total == 5
        assert [len(first), len(second), len(third)] == [2, 2, 1]
        seen = [r.run_id for r in first + second + third]
        assert len(set(seen)) == 5

    def test_page_size_is_clamped(self, store: RunStore) -> None:
        _create(store)
        listing, _ = store.list_runs(limit=0, offset=-5)
        assert len(listing) == 1

    def test_get_missing_returns_none(self, store: RunStore) -> None:
        assert store.get("nope") is None

    def test_logs_of_missing_run_are_empty(self, store: RunStore) -> None:
        assert store.logs("nope") == []
        assert store.artifacts("nope") == []
