"""Tests for the model-domain read services.

Results are seeded straight into the tables rather than produced by a training
run: these services read what is stored, so what produced the rows is beside the
point, and a walk-forward pass would cost minutes per test.
"""

import tempfile
from collections.abc import Iterator
from datetime import date

import pandas as pd
import pytest
from pydantic import ValidationError

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.models.persistence import (
    MODEL_KIND,
    build_ic_frame,
    build_importance_frame,
    build_metric_frame,
    save_model_evaluation,
)
from quant_trade.services import RunContext
from quant_trade.services.model_query import (
    ModelEvaluationParams,
    ModelRunListParams,
    model_evaluation,
    model_run_list,
)


@pytest.fixture
def store() -> Iterator[DataStore]:
    with tempfile.TemporaryDirectory() as tmp:
        db = f"{tmp}/model.db"
        init_db(db).close()
        opened = DataStore(db)
        yield opened
        opened.close()


def _ic_points(run_id: str) -> list[tuple[date, float]]:
    """Six days spanning two years, so the yearly rollup has something to split."""
    return [
        (date(2023, 12, 29), 0.10),
        (date(2024, 1, 5), 0.20),
        (date(2024, 6, 3), -0.10),
        (date(2024, 12, 2), 0.40),
        (date(2025, 3, 3), -0.20),
        (date(2025, 6, 2), 0.30),
    ]


def _seed(store: DataStore, run_id: str, *, status: str = "ok", factors: int = 4) -> None:
    store.conn.execute(
        "INSERT INTO run (run_id, kind, params_json, status) VALUES (?, ?, ?, ?)",
        [run_id, MODEL_KIND, '{"start": "2024-03-01", "factors": ["MA20", "RSV5"]}', status],
    )
    importance = pd.DataFrame(
        {
            "factor": [f"F{index}" for index in range(factors)],
            "importance": [1.0 - index / 10 for index in range(factors)],
            "std": [0.01] * factors,
        }
    )
    save_model_evaluation(
        store,
        run_id,
        build_ic_frame(run_id, _ic_points(run_id)),
        build_importance_frame(run_id, importance),
        build_metric_frame(
            run_id,
            {
                "ic_mean": 0.1,
                "ic_ir": 0.5,
                "ic_positive_ratio": 0.6,
                "ic_days": 6.0,
                "prediction_rows": 120.0,
                "windows_trained": 3.0,
            },
        ),
    )


class TestRunList:
    def test_lists_runs_with_summaries(self, store: DataStore) -> None:
        _seed(store, "r1")
        result = model_run_list(ModelRunListParams(), RunContext(store=store))

        assert result.total == 1
        summary = result.runs[0]
        assert summary.run_id == "r1"
        assert summary.status == "ok"
        assert summary.start == date(2023, 12, 29)
        assert summary.end == date(2025, 6, 2)
        assert summary.requested_start == date(2024, 3, 1)
        assert summary.factor_count == 2
        assert summary.windows_trained == 3
        assert summary.prediction_rows == 120
        assert summary.ic_mean == pytest.approx(0.1)

    def test_pagination_reports_total(self, store: DataStore) -> None:
        for index in range(3):
            _seed(store, f"r{index}")

        first = model_run_list(ModelRunListParams(limit=2), RunContext(store=store))
        second = model_run_list(ModelRunListParams(limit=2, offset=2), RunContext(store=store))

        assert first.total == 3
        assert len(first.runs) == 2
        assert len(second.runs) == 1

    def test_running_run_reports_progress(self, store: DataStore) -> None:
        store.conn.execute(
            "INSERT INTO run (run_id, kind, params_json, status, progress, message) VALUES (?, ?, ?, ?, ?, ?)",
            ["r1", MODEL_KIND, "{}", "running", 0.25, "Training window 2/8"],
        )
        result = model_run_list(ModelRunListParams(), RunContext(store=store))

        summary = result.runs[0]
        assert summary.progress == pytest.approx(0.25)
        assert summary.message == "Training window 2/8"
        assert summary.ic_days == 0

    def test_empty_history_is_not_an_error(self, store: DataStore) -> None:
        result = model_run_list(ModelRunListParams(), RunContext(store=store))
        assert result.total == 0
        assert result.runs == []


class TestEvaluation:
    def test_assembles_series_and_metrics(self, store: DataStore) -> None:
        _seed(store, "r1")
        result = model_evaluation(ModelEvaluationParams(run_id="r1"), RunContext(store=store))

        assert result.found is True
        assert result.status == "ok"
        assert result.ic_days == 6
        assert [point.trade_date for point in result.ic_series][0] == date(2023, 12, 29)
        assert [point.rank_ic for point in result.ic_series] == pytest.approx([0.10, 0.20, -0.10, 0.40, -0.20, 0.30])
        assert result.ic_mean == pytest.approx(0.1)

    def test_yearly_rollup_matches_a_recompute(self, store: DataStore) -> None:
        _seed(store, "r1")
        result = model_evaluation(ModelEvaluationParams(run_id="r1"), RunContext(store=store))

        assert [year.year for year in result.yearly] == [2023, 2024, 2025]
        buckets: dict[int, list[float]] = {}
        for point in result.ic_series:
            buckets.setdefault(point.trade_date.year, []).append(point.rank_ic)
        for summary in result.yearly:
            values = buckets[summary.year]
            assert summary.days == len(values)
            assert summary.ic_mean == pytest.approx(sum(values) / len(values))
            assert summary.ic_positive_ratio == pytest.approx(sum(1 for value in values if value > 0) / len(values))

    def test_single_day_year_has_no_ic_ir(self, store: DataStore) -> None:
        _seed(store, "r1")
        result = model_evaluation(ModelEvaluationParams(run_id="r1"), RunContext(store=store))
        alone = next(year for year in result.yearly if year.year == 2023)
        assert alone.ic_ir is None, "one observation has no dispersion to divide by"

    def test_importance_is_truncated_but_counted(self, store: DataStore) -> None:
        _seed(store, "r1", factors=6)
        result = model_evaluation(ModelEvaluationParams(run_id="r1", importance_top_n=2), RunContext(store=store))

        assert [entry.factor for entry in result.importance] == ["F0", "F1"]
        assert result.importance_total == 6, "the page needs to say 'top 2 of 6'"

    def test_importance_carries_window_dispersion(self, store: DataStore) -> None:
        _seed(store, "r1")
        result = model_evaluation(ModelEvaluationParams(run_id="r1"), RunContext(store=store))
        assert all(entry.std == pytest.approx(0.01) for entry in result.importance)

    def test_missing_run_is_reported_not_invented(self, store: DataStore) -> None:
        result = model_evaluation(ModelEvaluationParams(run_id="nope"), RunContext(store=store))
        assert result.found is False
        assert result.ic_series == []
        assert result.yearly == []

    def test_run_of_another_kind_is_not_found(self, store: DataStore) -> None:
        store.conn.execute(
            "INSERT INTO run (run_id, kind, params_json, status) VALUES (?, ?, ?, ?)",
            ["b1", "backtest", "{}", "ok"],
        )
        assert model_evaluation(ModelEvaluationParams(run_id="b1"), RunContext(store=store)).found is False

    def test_run_without_results_still_evaluates(self, store: DataStore) -> None:
        store.conn.execute(
            "INSERT INTO run (run_id, kind, params_json, status) VALUES (?, ?, ?, ?)",
            ["r1", MODEL_KIND, "{}", "failed"],
        )
        result = model_evaluation(ModelEvaluationParams(run_id="r1"), RunContext(store=store))
        assert result.found is True
        assert result.ic_series == [] and result.ic_mean is None


class TestParams:
    def test_round_trips_through_json(self) -> None:
        params = ModelEvaluationParams(run_id="r1", importance_top_n=7)
        assert ModelEvaluationParams.model_validate_json(params.model_dump_json()) == params

    def test_rejects_out_of_range_top_n(self) -> None:
        with pytest.raises(ValidationError):
            ModelEvaluationParams(run_id="r1", importance_top_n=0)

    def test_rejects_negative_offset(self) -> None:
        with pytest.raises(ValidationError):
            ModelRunListParams(offset=-1)

    def test_rejects_unknown_fields(self) -> None:
        with pytest.raises(ValidationError):
            ModelRunListParams(limitt=5)  # type: ignore[call-arg]
