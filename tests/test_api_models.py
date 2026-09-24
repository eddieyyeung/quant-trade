"""Model-domain routes: training history, evaluation, predictions."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.models.persistence import (
    MODEL_KIND,
    build_ic_frame,
    build_importance_frame,
    build_metric_frame,
    save_model_evaluation,
)
from quant_trade.runs.models import ArtifactDraft, ArtifactStorage, RunStatus
from quant_trade.runs.store import RunStore
from quant_trade.runtime.app import create_platform_app

START = date(2024, 1, 1)
CODES = [f"{i:06d}.SZ" for i in range(1, 5)]


def _seed_run(store: DataStore, run_id: str, *, status: str = "ok", factors: int = 3) -> None:
    store.conn.execute(
        "INSERT INTO run (run_id, kind, params_json, status, finished_at) VALUES (?, ?, ?, ?, ?)",
        [run_id, MODEL_KIND, '{"start": "2024-02-01", "factors": ["MA20"]}', status, START + timedelta(days=5)],
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
        build_ic_frame(run_id, [(START + timedelta(days=index), 0.01 * (index + 1)) for index in range(3)]),
        build_importance_frame(run_id, importance),
        build_metric_frame(run_id, {"ic_mean": 0.02, "ic_ir": 0.4, "ic_positive_ratio": 1.0, "ic_days": 3.0}),
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "quant.db"
    conn = init_db(str(path))
    days = [START + timedelta(days=index) for index in range(3)]
    conn.executemany("INSERT INTO trade_calendar VALUES (?, TRUE)", [(day,) for day in days])
    # The prediction path resolves its universe from synced kline when no index
    # constituents are stored, so the codes have to exist as data, not just as
    # rows in the predictions file.
    conn.executemany(
        "INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            (code, day, 10.0, 10.0, 10.0, 10.0, 1e6, 1e7, None, None)
            for code in ("600000.SH", "000001.SZ")
            for day in days
        ],
    )
    conn.close()
    return path


@pytest.fixture
def client(db_path: Path) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as tmp:
        config_file = Path(tmp) / "config.yaml"
        config_file.write_text(f"data:\n  db_path: {db_path}\n", encoding="utf-8")
        with TestClient(create_platform_app(str(config_file))) as c:
            yield c


def _seed(db_path: Path, **kwargs: object) -> None:
    store = DataStore(str(db_path))
    try:
        _seed_run(store, "r1", **kwargs)  # type: ignore[arg-type]
    finally:
        store.close()


class TestRunList:
    def test_empty_history(self, client: TestClient) -> None:
        response = client.get("/api/models/runs")
        assert response.status_code == 200
        assert response.json() == {"total": 0, "items": []}

    def test_lists_a_run(self, client: TestClient, db_path: Path) -> None:
        _seed(db_path)
        body = client.get("/api/models/runs").json()

        assert body["total"] == 1
        item = body["items"][0]
        assert item["run_id"] == "r1"
        assert item["start"] == "2024-01-01"
        assert item["end"] == "2024-01-03"
        assert item["requested_start"] == "2024-02-01"
        assert item["factor_count"] == 1
        assert item["ic_days"] == 3
        assert item["ic_mean"] == pytest.approx(0.02)

    def test_running_row_carries_progress(self, client: TestClient, db_path: Path) -> None:
        store = DataStore(str(db_path))
        try:
            store.conn.execute(
                "INSERT INTO run (run_id, kind, params_json, status, progress, message) VALUES (?, ?, ?, ?, ?, ?)",
                ["r1", MODEL_KIND, "{}", "running", 0.3, "Walk-forward training"],
            )
        finally:
            store.close()

        item = client.get("/api/models/runs").json()["items"][0]
        assert item["progress"] == pytest.approx(0.3)
        assert item["message"] == "Walk-forward training"
        assert item["start"] is None, "a running run has no covered window yet"

    def test_rejects_bad_page_size(self, client: TestClient) -> None:
        assert client.get("/api/models/runs?limit=0").status_code == 422
        assert client.get("/api/models/runs?offset=-1").status_code == 422


class TestEvaluation:
    def test_returns_series_yearly_and_importance(self, client: TestClient, db_path: Path) -> None:
        _seed(db_path, factors=5)
        response = client.get("/api/models/runs/r1?importance_top_n=2")
        assert response.status_code == 200
        body = response.json()

        assert body["status"] == "ok"
        assert body["metrics"]["ic_days"] == 3
        assert [point["trade_date"] for point in body["ic_series"]] == ["2024-01-01", "2024-01-02", "2024-01-03"]
        assert [year["year"] for year in body["yearly"]] == [2024]
        assert [entry["factor"] for entry in body["importance"]] == ["F0", "F1"]
        assert body["importance_total"] == 5

    def test_unknown_run_is_404(self, client: TestClient) -> None:
        response = client.get("/api/models/runs/nope")
        assert response.status_code == 404
        assert "nope" in response.json()["detail"]

    def test_run_of_another_kind_is_404(self, client: TestClient, db_path: Path) -> None:
        store = DataStore(str(db_path))
        try:
            store.conn.execute(
                "INSERT INTO run (run_id, kind, params_json, status) VALUES ('b1', 'backtest', '{}', 'ok')"
            )
        finally:
            store.close()
        assert client.get("/api/models/runs/b1").status_code == 404

    def test_rejects_out_of_range_top_n(self, client: TestClient, db_path: Path) -> None:
        _seed(db_path)
        assert client.get("/api/models/runs/r1?importance_top_n=0").status_code == 422
        assert client.get("/api/models/runs/r1?importance_top_n=9999").status_code == 422


class TestPredictions:
    def test_reports_no_source_before_any_training(self, client: TestClient) -> None:
        response = client.get("/api/models/predictions")
        assert response.status_code == 200
        body = response.json()
        assert body["available"] is False
        assert body["source"] is None
        assert body["scores"] == []

    def test_reads_predictions_from_the_newest_run(self, client: TestClient, db_path: Path, tmp_path: Path) -> None:
        predictions_path = tmp_path / "model_ranking.parquet"
        pd.DataFrame(
            {
                "ts_code": ["600000.SH", "000001.SZ"],
                "trade_date": [START, START],
                "score": [0.9, 0.1],
            }
        ).to_parquet(predictions_path)

        runs = RunStore(str(db_path))
        try:
            record = runs.create("model_train", "{}")
            runs.mark_running(record.run_id)
            runs.finish(record.run_id, RunStatus.OK)
            runs.add_artifact(
                record.run_id,
                ArtifactDraft(
                    kind="predictions",
                    storage=ArtifactStorage.PARQUET,
                    ref=str(predictions_path),
                    row_count=2,
                ),
            )
        finally:
            runs.close()

        body = client.get(f"/api/models/predictions?as_of={START.isoformat()}&top_n=1").json()

        assert body["available"] is True
        assert body["source"]["run_id"] == record.run_id
        assert body["source"]["path"] == str(predictions_path)
        assert [row["ts_code"] for row in body["picks"]] == ["600000.SH"]
        assert [row["score"] for row in body["scores"]] == pytest.approx([0.9, 0.1])
        assert body["available_dates"] == [START.isoformat()]

    def test_rejects_out_of_range_top_n(self, client: TestClient) -> None:
        assert client.get("/api/models/predictions?top_n=0").status_code == 422


class TestRouting:
    def test_predictions_is_not_swallowed_by_the_run_path(self, client: TestClient) -> None:
        """``/runs/{run_id}`` must not claim the sibling path."""
        assert client.get("/api/models/predictions").status_code == 200

    def test_unknown_model_path_is_json_404_not_the_spa(self, client: TestClient) -> None:
        response = client.get("/api/models/nope")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
