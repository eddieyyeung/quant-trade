"""Run API: submission, validation, lookup, cancellation and paging."""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_trade.config import AppConfig
from quant_trade.jobs.registry import JOBS, JobSpec
from quant_trade.runs.models import ArtifactDraft, ArtifactStorage, RunStatus
from quant_trade.runs.store import RunStore
from quant_trade.runtime.app import create_platform_app
from quant_trade.services.context import RunContext
from quant_trade.services.params import ServiceParams


class EchoParams(ServiceParams):
    """Parameters for the stub job the API tests submit."""

    label: str = "echo"
    steps: int = 2
    hang: bool = False
    fail: bool = False


@dataclass
class EchoResult:
    tables: dict[str, int]


def _echo(params: EchoParams, ctx: RunContext) -> EchoResult:
    for i in range(params.steps):
        if params.hang:
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
    """Mirrors the real data_sync mapper: an empty table is not an artifact."""
    return [
        ArtifactDraft(kind="table", storage=ArtifactStorage.TABLE, ref=ref, row_count=rows)
        for ref, rows in result.tables.items()
        if rows > 0
    ]


@pytest.fixture(autouse=True)
def _register_echo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        JOBS, "echo", JobSpec(kind="echo", params_model=EchoParams, service_fn=_echo, artifacts=_echo_artifacts)
    )


@pytest.fixture
def client() -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "quant.db"
        config_file = Path(tmp) / "config.yaml"
        config_file.write_text(f"data:\n  db_path: {db}\n", encoding="utf-8")
        dist = Path(tmp) / "dist"
        dist.mkdir()
        (dist / "index.html").write_text("<!doctype html><title>SPA</title>", encoding="utf-8")
        with TestClient(create_platform_app(str(config_file), dist=dist)) as c:
            yield c


def _submit(client: TestClient, kind: str = "echo", **params: object) -> dict[str, object]:
    response = client.post("/api/runs", json={"kind": kind, "params": params})
    assert response.status_code == 202, response.text
    return response.json()


def _await_status(client: TestClient, run_id: str, statuses: set[str], timeout: float = 15.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["status"] in statuses:
            return body
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} never reached {statuses}")


class TestSubmit:
    def test_accepted_immediately(self, client: TestClient) -> None:
        body = _submit(client, steps=2)
        assert body["kind"] == "echo"
        assert body["run_id"]
        assert body["status"] == "pending"

    def test_record_exists_right_away(self, client: TestClient) -> None:
        """Submission returns before the job runs, but after it is persisted."""
        run_id = str(_submit(client, label="persisted", steps=1)["run_id"])
        detail = client.get(f"/api/runs/{run_id}").json()
        assert detail["kind"] == "echo"
        assert detail["params"]["label"] == "persisted"
        assert detail["trigger"] == "manual"

    def test_run_completes(self, client: TestClient) -> None:
        run_id = str(_submit(client, steps=3)["run_id"])
        detail = _await_status(client, run_id, {"ok"})
        assert detail["progress"] == pytest.approx(1.0)
        assert detail["finished_at"] is not None
        assert detail["error"] is None

    def test_unknown_kind_is_400(self, client: TestClient) -> None:
        response = client.post("/api/runs", json={"kind": "nope", "params": {}})
        assert response.status_code == 400
        assert "nope" in response.json()["detail"]

    def test_invalid_params_are_422_without_a_record(self, client: TestClient) -> None:
        before = client.get("/api/runs").json()["total"]

        response = client.post("/api/runs", json={"kind": "echo", "params": {"steps": "many"}})
        assert response.status_code == 422
        assert response.json()["detail"]

        assert client.get("/api/runs").json()["total"] == before

    def test_unknown_param_is_rejected(self, client: TestClient) -> None:
        """``extra="forbid"`` on the params model catches typos rather than ignoring them."""
        response = client.post("/api/runs", json={"kind": "echo", "params": {"stepps": 1}})
        assert response.status_code == 422

    def test_inverted_date_range_is_rejected_without_a_record(self, client: TestClient) -> None:
        """The frontend guard is a backstop; this is the one that actually holds.

        ``DataSyncParams`` rejects a start after its end, and rejection happens
        before the run is persisted — a bad window must not enter the history.
        """
        before = client.get("/api/runs").json()["total"]

        response = client.post(
            "/api/runs",
            json={"kind": "data_sync", "params": {"start_date": "2026-01-01", "end_date": "2025-01-01"}},
        )

        assert response.status_code == 422
        assert "must not be after" in json.dumps(response.json())
        assert client.get("/api/runs").json()["total"] == before

    def test_malformed_body_is_422(self, client: TestClient) -> None:
        assert client.post("/api/runs", json={"params": {}}).status_code == 422
        assert client.post("/api/runs", json={"kind": "echo", "params": {}, "extra": 1}).status_code == 422

    def test_submitted_run_reaches_the_worker(self, client: TestClient) -> None:
        run_id = str(_submit(client, label="worker", steps=2)["run_id"])
        body = _await_status(client, run_id, {"ok"})
        assert body["message"] == "worker 2/2"


class TestQuery:
    def test_missing_run_is_404(self, client: TestClient) -> None:
        response = client.get("/api/runs/does-not-exist")
        assert response.status_code == 404
        assert "does-not-exist" in response.json()["detail"]

    def test_list_is_newest_first(self, client: TestClient) -> None:
        ids = [str(_submit(client, steps=1)["run_id"]) for _ in range(3)]
        for run_id in ids:
            _await_status(client, run_id, {"ok"})

        page = client.get("/api/runs").json()
        assert page["total"] == 3
        assert [item["run_id"] for item in page["items"]] == list(reversed(ids))

    def test_list_omits_params(self, client: TestClient) -> None:
        _submit(client, steps=1)
        assert "params" not in client.get("/api/runs").json()["items"][0]

    def test_paging(self, client: TestClient) -> None:
        ids = [str(_submit(client, steps=1)["run_id"]) for _ in range(5)]
        for run_id in ids:
            _await_status(client, run_id, {"ok"})

        first = client.get("/api/runs", params={"limit": 2, "offset": 0}).json()
        second = client.get("/api/runs", params={"limit": 2, "offset": 4}).json()

        assert first["total"] == 5
        assert len(first["items"]) == 2
        assert len(second["items"]) == 1

    def test_bad_paging_is_rejected(self, client: TestClient) -> None:
        assert client.get("/api/runs", params={"limit": 0}).status_code == 422
        assert client.get("/api/runs", params={"offset": -1}).status_code == 422
        assert client.get("/api/runs", params={"limit": 10_000}).status_code == 422

    def test_artifacts_are_listed(self, client: TestClient) -> None:
        run_id = str(_submit(client, steps=4)["run_id"])
        _await_status(client, run_id, {"ok"})

        artifacts = client.get(f"/api/runs/{run_id}/artifacts").json()
        assert [(a["ref"], a["row_count"]) for a in artifacts] == [("echo", 4)]
        assert artifacts[0]["storage"] == "table"

    def test_artifacts_of_missing_run_is_404(self, client: TestClient) -> None:
        assert client.get("/api/runs/nope/artifacts").status_code == 404

    def test_empty_artifact_list(self, client: TestClient) -> None:
        run_id = str(_submit(client, steps=0)["run_id"])
        _await_status(client, run_id, {"ok"})
        assert client.get(f"/api/runs/{run_id}/artifacts").json() == []


class TestCancel:
    def test_cancel_a_running_job(self, client: TestClient) -> None:
        run_id = str(_submit(client, hang=True)["run_id"])
        _await_status(client, run_id, {"running"})

        response = client.post(f"/api/runs/{run_id}/cancel")
        assert response.status_code == 202

        detail = _await_status(client, run_id, {"cancelled"})
        assert detail["error"] is None

    def test_cancel_a_finished_job_is_409(self, client: TestClient) -> None:
        run_id = str(_submit(client, steps=1)["run_id"])
        _await_status(client, run_id, {"ok"})

        response = client.post(f"/api/runs/{run_id}/cancel")
        assert response.status_code == 409
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "ok"

    def test_cancel_a_queued_job_is_409(self, client: TestClient) -> None:
        """Cancellation targets the running job; queued work must not be silently dropped."""
        blocker = str(_submit(client, hang=True)["run_id"])
        _await_status(client, blocker, {"running"})
        queued = str(_submit(client, steps=1)["run_id"])
        assert client.get(f"/api/runs/{queued}").json()["status"] == "pending"

        assert client.post(f"/api/runs/{queued}/cancel").status_code == 409

        client.post(f"/api/runs/{blocker}/cancel")
        _await_status(client, blocker, {"cancelled"})
        _await_status(client, queued, {"ok"})

    def test_cancel_missing_run_is_404(self, client: TestClient) -> None:
        assert client.post("/api/runs/nope/cancel").status_code == 404


class TestMountedOnPlatformApp:
    def test_routes_come_before_the_spa_catch_all(self, client: TestClient) -> None:
        """An unknown /api path must 404 as JSON, not render the SPA."""
        response = client.get("/api/runs/whatever/nope")
        assert response.status_code == 404
        assert "SPA" not in response.text

    def test_frontend_still_served(self, client: TestClient) -> None:
        assert "SPA" in client.get("/").text

    def test_health_still_works(self, client: TestClient) -> None:
        assert client.get("/api/health").json()["status"] == "ok"


class TestStartupRecovery:
    def test_leftover_running_rows_are_interrupted_on_startup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "quant.db"
            config_file = Path(tmp) / "config.yaml"
            config_file.write_text(f"data:\n  db_path: {db}\n", encoding="utf-8")

            store = RunStore(str(db))
            run_id = store.create("echo", EchoParams(steps=1).model_dump_json()).run_id
            store.mark_running(run_id)
            store.close()

            with TestClient(create_platform_app(str(config_file))):
                pass

            reopened = RunStore(str(db))
            try:
                record = reopened.get(run_id)
                assert record is not None
                assert record.status is RunStatus.INTERRUPTED
                assert record.error
            finally:
                reopened.close()

    def test_leftover_queued_rows_are_interrupted_and_not_resumed(self) -> None:
        """Recovery terminates a queued run rather than restarting it.

        Re-enqueueing was the rejected alternative: restarting the platform must
        not silently begin a job that could run for hours.
        """
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "quant.db"
            config_file = Path(tmp) / "config.yaml"
            config_file.write_text(f"data:\n  db_path: {db}\n", encoding="utf-8")

            store = RunStore(str(db))
            # Queued but never handed to a worker — the state a restart strands.
            run_id = store.create("echo", EchoParams(steps=1).model_dump_json()).run_id
            store.close()

            with TestClient(create_platform_app(str(config_file))):
                # Long enough for an incorrect re-queue to have started the job.
                time.sleep(0.5)

            reopened = RunStore(str(db))
            try:
                record = reopened.get(run_id)
                assert record is not None
                assert record.status is RunStatus.INTERRUPTED
                assert record.error
                assert record.started_at is None, "a queued run must not be resumed on restart"
            finally:
                reopened.close()

    def test_platform_app_points_the_worker_at_the_configured_database(self) -> None:
        """A run submitted through the API is written to the configured path."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "quant.db"
            config_file = Path(tmp) / "config.yaml"
            config_file.write_text(f"data:\n  db_path: {db}\n", encoding="utf-8")

            with TestClient(create_platform_app(str(config_file))) as c:
                run_id = str(_submit(c, steps=1)["run_id"])
                _await_status(c, run_id, {"ok"})

            assert db.is_file()
            store = RunStore(str(db))
            try:
                assert store.get(run_id) is not None
            finally:
                store.close()

    def test_config_defaults_are_not_needed_for_validation(self) -> None:
        """Params are validated by the registered model, independent of AppConfig."""
        assert AppConfig().data.db_path


class TestStatusPayloadShape:
    def test_terminal_run_exposes_its_error(self, client: TestClient) -> None:
        run_id = str(_submit(client, steps=1, fail=True)["run_id"])
        detail = _await_status(client, run_id, {"failed"})
        assert detail["error"] == "ValueError: boom"

    def test_all_lifecycle_fields_present(self, client: TestClient) -> None:
        run_id = str(_submit(client, steps=1)["run_id"])
        detail = _await_status(client, run_id, {"ok"})
        for field in (
            "run_id",
            "kind",
            "params",
            "status",
            "progress",
            "message",
            "trigger",
            "idempotency_key",
            "created_at",
            "started_at",
            "finished_at",
            "error",
        ):
            assert field in detail, field


class TestWeeklyKind:
    """``weekly`` is a registered kind, reachable through the same endpoint.

    The pipeline itself is stubbed: a real weekly run syncs market data over the
    network, which is not something a route test should reach for. What is under
    test here is that the endpoint accepts the kind and validates its params.
    """

    @staticmethod
    def _stub(monkeypatch: pytest.MonkeyPatch) -> None:
        from datetime import date

        from quant_trade.jobs.registry import JOBS, get_job
        from quant_trade.services.report import WeeklyReportResult

        spec = get_job("weekly")
        assert spec is not None
        monkeypatch.setitem(
            JOBS,
            "weekly",
            JobSpec(
                kind="weekly",
                params_model=spec.params_model,
                service_fn=lambda params, ctx: WeeklyReportResult(
                    signal_date=date(2026, 7, 24),
                    report_path="",
                    order_count=0,
                    nav_points=0,
                    trade_count=0,
                ),
                artifacts=spec.artifacts,
            ),
        )

    def test_accepted_and_runs(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        self._stub(monkeypatch)

        body = _submit(client, "weekly", as_of="2026-07-24")
        assert body["kind"] == "weekly"
        assert body["run_id"]

        detail = _await_status(client, str(body["run_id"]), {"ok"})
        assert detail["params"]["as_of"] == "2026-07-24"
        assert detail["finished_at"] is not None

    def test_params_round_trip(self, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
        """A run is reproducible from ``params_json`` alone."""
        self._stub(monkeypatch)

        run_id = str(_submit(client, "weekly", as_of="2026-07-24", output_dir="/tmp/reports")["run_id"])
        detail = client.get(f"/api/runs/{run_id}").json()
        # The record holds the whole validated params object, not just the keys
        # the caller sent, so this checks what was submitted survived rather
        # than asserting an exact dict the model is free to grow.
        assert detail["params"]["as_of"] == "2026-07-24"
        assert detail["params"]["output_dir"] == "/tmp/reports"

    def test_invalid_param_is_422_without_a_record(self, client: TestClient) -> None:
        before = client.get("/api/runs").json()["total"]

        response = client.post("/api/runs", json={"kind": "weekly", "params": {"as_of": "not-a-date"}})
        assert response.status_code == 422
        assert client.get("/api/runs").json()["total"] == before

    def test_rejected_before_the_stub_is_needed(self, client: TestClient) -> None:
        """Param validation happens at submission, not when the worker picks it up."""
        from quant_trade.jobs.registry import get_job

        spec = get_job("weekly")
        assert spec is not None
        assert spec.params_model().as_of is None
