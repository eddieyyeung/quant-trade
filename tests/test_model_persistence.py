"""Tests for model evaluation result persistence and querying."""

import tempfile
from collections.abc import Iterator
from datetime import date

import pandas as pd
import pytest

from quant_trade.data.schema import init_db
from quant_trade.data.store import TABLE_DATE_COLUMNS, TABLE_NAMES, DataStore
from quant_trade.models.persistence import (
    IMPORTANCE_COLUMNS,
    MODEL_KIND,
    build_ic_frame,
    build_importance_frame,
    build_metric_frame,
    get_model_ic_series,
    get_model_importance,
    get_model_metrics,
    get_model_run,
    list_model_runs,
    save_model_evaluation,
)

MODEL_TABLES = ("model_feature_importance", "model_ic_series", "model_metric")


@pytest.fixture
def db_path() -> Iterator[str]:
    with tempfile.TemporaryDirectory() as tmp:
        yield f"{tmp}/quant.db"


def _store(db_path: str) -> DataStore:
    init_db(db_path).close()
    return DataStore(db_path)


def _ic_points(days: int = 3) -> list[tuple[date, float]]:
    return [(date(2024, 3, day), 0.01 * day) for day in range(4, 4 + days)]


def _importance() -> pd.DataFrame:
    return pd.DataFrame({"factor": ["MA20", "RSV5", "STD20"], "importance": [0.5, 0.3, 0.2], "std": [0.01, 0.02, 0.03]})


def _metrics() -> dict[str, float]:
    return {"ic_mean": 0.031, "ic_ir": 0.42, "ic_positive_ratio": 0.55, "ic_days": 3.0, "windows_trained": 2.0}


def _save(store: DataStore, run_id: str, days: int = 3) -> None:
    save_model_evaluation(
        store,
        run_id,
        build_ic_frame(run_id, _ic_points(days)),
        build_importance_frame(run_id, _importance()),
        build_metric_frame(run_id, _metrics()),
    )


def _insert_run(
    store: DataStore,
    run_id: str,
    kind: str = MODEL_KIND,
    status: str = "ok",
    progress: float = 1.0,
    message: str = "",
) -> None:
    store.conn.execute(
        "INSERT INTO run (run_id, kind, params_json, status, progress, message) VALUES (?, ?, ?, ?, ?, ?)",
        [run_id, kind, '{"predict_months": 3}', status, progress, message],
    )


class TestSchema:
    """The three result tables exist, are idempotent and are introspectable."""

    def test_tables_exist(self, db_path: str) -> None:
        store = _store(db_path)
        try:
            names = {row[0] for row in store.conn.execute("SHOW TABLES").fetchall()}
        finally:
            store.close()
        assert set(MODEL_TABLES) <= names

    def test_init_is_idempotent(self, db_path: str) -> None:
        init_db(db_path).close()
        store = _store(db_path)
        try:
            for table in MODEL_TABLES:
                assert store.table_stats(table).rows == 0
        finally:
            store.close()

    def test_tables_are_registered(self) -> None:
        assert set(MODEL_TABLES) <= TABLE_NAMES

    def test_empty_tables_have_no_date_bounds(self, db_path: str) -> None:
        store = _store(db_path)
        try:
            for table in MODEL_TABLES:
                stats = store.table_stats(table)
                assert stats.rows == 0
                assert stats.earliest is None and stats.latest is None
        finally:
            store.close()

    def test_only_ic_series_has_a_date_column(self) -> None:
        assert TABLE_DATE_COLUMNS.get("model_ic_series") == "trade_date"
        assert "model_feature_importance" not in TABLE_DATE_COLUMNS
        assert "model_metric" not in TABLE_DATE_COLUMNS

    def test_unknown_table_still_rejected(self, db_path: str) -> None:
        store = _store(db_path)
        try:
            with pytest.raises(ValueError):
                store.table_stats("model_nope")
        finally:
            store.close()

    def test_populated_ic_series_reports_bounds(self, db_path: str) -> None:
        store = _store(db_path)
        try:
            _save(store, "r1")
            stats = store.table_stats("model_ic_series")
        finally:
            store.close()
        assert stats.rows == 3
        assert stats.earliest == date(2024, 3, 4)
        assert stats.latest == date(2024, 3, 6)


class TestSaveAndRead:
    """Everything written comes back, and only once."""

    def test_ic_series_round_trips(self, db_path: str) -> None:
        store = _store(db_path)
        try:
            _save(store, "r1")
            frame = get_model_ic_series(store, "r1")
        finally:
            store.close()
        assert list(frame["trade_date"]) == [date(2024, 3, 4), date(2024, 3, 5), date(2024, 3, 6)]
        assert list(frame["rank_ic"]) == pytest.approx([0.04, 0.05, 0.06])

    def test_importance_round_trips(self, db_path: str) -> None:
        store = _store(db_path)
        try:
            _save(store, "r1")
            frame = get_model_importance(store, "r1")
        finally:
            store.close()
        assert list(frame["factor"]) == ["MA20", "RSV5", "STD20"]
        assert list(frame["importance"]) == pytest.approx([0.5, 0.3, 0.2])
        assert list(frame["std"]) == pytest.approx([0.01, 0.02, 0.03])

    def test_metrics_round_trip(self, db_path: str) -> None:
        store = _store(db_path)
        try:
            _save(store, "r1")
            metrics = get_model_metrics(store, "r1")
        finally:
            store.close()
        assert metrics == pytest.approx(
            {"ic_mean": 0.031, "ic_ir": 0.42, "ic_positive_ratio": 0.55, "ic_days": 3.0, "windows_trained": 2.0}
        )

    def test_rewriting_same_run_is_idempotent(self, db_path: str) -> None:
        store = _store(db_path)
        try:
            _save(store, "r1")
            _save(store, "r1")
            counts = {table: store.table_stats(table).rows for table in MODEL_TABLES}
        finally:
            store.close()
        assert counts == {"model_ic_series": 3, "model_feature_importance": 3, "model_metric": 5}

    def test_rows_are_scoped_per_run(self, db_path: str) -> None:
        """A second run must not overwrite the first: different key, different rows."""
        store = _store(db_path)
        try:
            _save(store, "r1", days=3)
            _save(store, "r2", days=1)
            first = get_model_ic_series(store, "r1")
            second = get_model_ic_series(store, "r2")
        finally:
            store.close()
        assert len(first) == 3
        assert len(second) == 1

    def test_empty_frames_write_nothing(self, db_path: str) -> None:
        store = _store(db_path)
        try:
            rows = save_model_evaluation(
                store,
                "r1",
                build_ic_frame("r1", []),
                build_importance_frame("r1", pd.DataFrame()),
                build_metric_frame("r1", {}),
            )
            counts = {table: store.table_stats(table).rows for table in MODEL_TABLES}
        finally:
            store.close()
        assert rows.total == 0
        assert counts == {"model_ic_series": 0, "model_feature_importance": 0, "model_metric": 0}

    def test_missing_column_is_rejected(self, db_path: str) -> None:
        store = _store(db_path)
        try:
            broken = pd.DataFrame({"factor": ["MA20"], "importance": [0.5]})
            with pytest.raises(ValueError, match="missing columns"):
                save_model_evaluation(
                    store,
                    "r1",
                    build_ic_frame("r1", _ic_points(1)),
                    broken,
                    build_metric_frame("r1", {}),
                )
        finally:
            store.close()

    def test_empty_importance_input_yields_typed_empty_frame(self) -> None:
        frame = build_importance_frame("r1", pd.DataFrame())
        assert list(frame.columns) == IMPORTANCE_COLUMNS

    def test_importance_limit_truncates_by_mean(self, db_path: str) -> None:
        store = _store(db_path)
        try:
            _save(store, "r1")
            frame = get_model_importance(store, "r1", limit=2)
        finally:
            store.close()
        assert list(frame["factor"]) == ["MA20", "RSV5"]

    def test_missing_run_reads_back_empty(self, db_path: str) -> None:
        store = _store(db_path)
        try:
            assert get_model_ic_series(store, "nope").empty
            assert get_model_importance(store, "nope").empty
            assert get_model_metrics(store, "nope") == {}
            assert get_model_run(store, "nope") is None
        finally:
            store.close()

    def test_model_ic_does_not_touch_factor_ic_table(self, db_path: str) -> None:
        """The factor domain's ``ic_series`` is a different table with a different key."""
        store = _store(db_path)
        try:
            before = store.table_stats("ic_series").rows
            _save(store, "r1")
            after = store.table_stats("ic_series").rows
        finally:
            store.close()
        assert before == after == 0


class TestRunList:
    """The list view joins ``run`` read-only and reports the covered window."""

    def test_lists_newest_first_with_metrics(self, db_path: str) -> None:
        store = _store(db_path)
        try:
            _insert_run(store, "r1")
            _insert_run(store, "r2")
            _save(store, "r1")
            _save(store, "r2")
            rows, total = list_model_runs(store, limit=10, offset=0)
        finally:
            store.close()
        assert total == 2
        assert {row.run_id for row in rows} == {"r1", "r2"}
        row = next(r for r in rows if r.run_id == "r1")
        assert row.status == "ok"
        assert row.start == date(2024, 3, 4)
        assert row.end == date(2024, 3, 6)
        assert row.ic_days == 3
        assert row.windows_trained == 2
        assert row.metrics["ic_mean"] == pytest.approx(0.031)
        assert row.params == {"predict_months": 3}

    def test_pagination_reports_total(self, db_path: str) -> None:
        store = _store(db_path)
        try:
            for index in range(3):
                _insert_run(store, f"r{index}")
            page, total = list_model_runs(store, limit=2, offset=0)
            second, _ = list_model_runs(store, limit=2, offset=2)
        finally:
            store.close()
        assert total == 3
        assert len(page) == 2
        assert len(second) == 1

    def test_run_of_another_kind_is_not_a_model_run(self, db_path: str) -> None:
        store = _store(db_path)
        try:
            _insert_run(store, "b1", kind="backtest")
            rows, total = list_model_runs(store, limit=10, offset=0)
        finally:
            store.close()
        assert rows == [] and total == 0
        assert get_model_run(store, "b1") is None

    def test_running_row_reports_its_progress(self, db_path: str) -> None:
        """Every other number on a running row is empty; progress is the one that moves."""
        store = _store(db_path)
        try:
            _insert_run(store, "r1", status="running", progress=0.42, message="Training window 3/8")
            rows, _ = list_model_runs(store, limit=10, offset=0)
        finally:
            store.close()
        assert rows[0].progress == pytest.approx(0.42)
        assert rows[0].message == "Training window 3/8"
        assert rows[0].windows_trained == 0, "results land only when the run ends"

    def test_finished_row_carries_full_progress(self, db_path: str) -> None:
        store = _store(db_path)
        try:
            _insert_run(store, "r1")
            _save(store, "r1")
            rows, _ = list_model_runs(store, limit=10, offset=0)
        finally:
            store.close()
        assert rows[0].progress == pytest.approx(1.0)

    def test_run_without_results_still_listed(self, db_path: str) -> None:
        """A run that produced nothing happened; hiding it would look like a lost submission."""
        store = _store(db_path)
        try:
            _insert_run(store, "r1", status="failed")
            rows, total = list_model_runs(store, limit=10, offset=0)
        finally:
            store.close()
        assert total == 1
        assert rows[0].start is None and rows[0].ic_days == 0

    def test_listing_writes_no_run_rows(self, db_path: str) -> None:
        """``persistence`` reads ``run`` for status; ``RunStore`` owns those rows."""
        store = _store(db_path)
        try:
            _insert_run(store, "r1")
            _save(store, "r1")
            before = store.conn.execute("SELECT COUNT(*) FROM run").fetchone()
            list_model_runs(store, limit=10, offset=0)
            get_model_run(store, "r1")
            get_model_ic_series(store, "r1")
            after = store.conn.execute("SELECT COUNT(*) FROM run").fetchone()
        finally:
            store.close()
        assert before == after
