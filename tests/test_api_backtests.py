"""Backtest-domain routes: run list, detail, trades and comparison."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from quant_trade.backtest.result_store import (
    build_metric_frame,
    build_nav_frame,
    build_position_frame,
    build_trade_frame,
    save_backtest_result,
)
from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.runtime.app import create_platform_app
from quant_trade.services.backtest_query import MAX_COMPARISON_RUNS

START = date(2024, 1, 2)


def _seed(
    store: DataStore,
    run_id: str,
    *,
    nav: list[float],
    started_at: datetime,
    status: str = "ok",
    params_json: str = '{"strategy": "factor_ranking", "top_n": 5}',
) -> None:
    # ``created_at`` is set explicitly: it is the list's sort key, and the
    # column default would give every seeded row the same submission instant.
    store.conn.execute(
        "INSERT INTO run (run_id, kind, params_json, status, created_at, started_at) "
        "VALUES (?, 'backtest', ?, ?, ?, ?)",
        [run_id, params_json, status, started_at, started_at],
    )
    days = [date(2024, 1, 2 + index) for index in range(len(nav))]
    save_backtest_result(
        store,
        run_id,
        nav_frame=build_nav_frame(pd.Series(nav, index=days, dtype=float)),
        trade_frame=build_trade_frame(
            [
                {
                    "date": days[0].isoformat(),
                    "action": "BUY",
                    "ts_code": "600000.SH",
                    "shares": 100,
                    "price": 10.0,
                    "commission": 5.0,
                    "stamp_duty": 0.0,
                    "transfer_fee": 0.01,
                },
                {
                    "date": days[-1].isoformat(),
                    "action": "SELL",
                    "ts_code": "600000.SH",
                    "shares": 100,
                    "price": 11.0,
                    "commission": 5.0,
                    "stamp_duty": 0.55,
                    "transfer_fee": 0.01,
                },
            ]
        ),
        metric_frame=build_metric_frame(
            {"annual_return": 0.31, "sharpe_ratio": 1.8, "max_drawdown": -0.12, "cash": 500.0, "final_value": 1100.0}
        ),
        position_frame=build_position_frame(
            [{"ts_code": "600000.SH", "shares": 100, "avg_cost": 10.0, "current_price": 11.0, "market_value": 1100.0}]
        ),
    )


def _insert_run_with_params(store: DataStore, run_id: str, params: dict[str, str], nav: list[float]) -> None:
    """Seed a backtest run submitted with explicit params, plus its NAV."""
    import json

    store.conn.execute(
        "INSERT INTO run (run_id, kind, params_json, status, created_at, started_at) "
        "VALUES (?, 'backtest', ?, 'ok', TIMESTAMP '2024-06-01 09:00:00', TIMESTAMP '2024-06-01 09:00:01')",
        [run_id, json.dumps(params)],
    )
    days = [date(2024, 1, 2 + index) for index in range(len(nav))]
    save_backtest_result(
        store,
        run_id,
        nav_frame=build_nav_frame(pd.Series(nav, index=days, dtype=float)),
        trade_frame=build_trade_frame([]),
        metric_frame=build_metric_frame({"annual_return": 0.1}),
        position_frame=build_position_frame([]),
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "quant.db"
    init_db(str(path)).close()
    store = DataStore(str(path))
    _seed(store, "run-new", nav=[1.0, 1.05, 1.10], started_at=datetime(2024, 6, 1))
    _seed(store, "run-old", nav=[1.0, 0.95], started_at=datetime(2024, 5, 1), status="cancelled")
    store.close()
    return path


def _config(db_path: Path) -> Path:
    """A config file pointing at ``db_path``, for apps built inside a test."""
    config_file = db_path.parent / f"config-{db_path.stem}.yaml"
    config_file.write_text(f"data:\n  db_path: {db_path}\n", encoding="utf-8")
    return config_file


@pytest.fixture
def client(db_path: Path) -> Iterator[TestClient]:
    with TestClient(create_platform_app(str(_config(db_path)))) as c:
        yield c


class TestRunList:
    def test_lists_runs_newest_first(self, client: TestClient) -> None:
        body = client.get("/api/backtests").json()
        assert body["total"] == 2
        assert [item["run_id"] for item in body["items"]] == ["run-new", "run-old"]

    def test_row_carries_the_actual_covered_window(self, client: TestClient) -> None:
        """The window comes from the NAV series, not from the request params."""
        item = client.get("/api/backtests").json()["items"][0]
        assert item["start"] == START.isoformat()
        assert item["end"] == date(2024, 1, 4).isoformat()
        assert item["nav_points"] == 3
        assert item["strategy"] == "factor_ranking"
        assert item["metrics"]["annual_return"] == pytest.approx(0.31)

    def test_requested_window_does_not_override_the_covered_one(self) -> None:
        """A run asked for a wider window than it covered; the row shows what ran.

        The engine normalises the start to the next trading day and a cancelled
        run stops early, so the requested dates are not the covered ones.
        """
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "window.db"
            init_db(str(db)).close()
            store = DataStore(str(db))
            _seed(
                store,
                "run-narrow",
                nav=[1.0, 1.05, 1.10],
                started_at=datetime(2024, 6, 1),
                params_json='{"strategy": "factor_ranking", "start": "1999-01-01", "end": "2030-12-31"}',
            )
            store.close()

            with TestClient(create_platform_app(str(_config(db)))) as client:
                item = client.get("/api/backtests").json()["items"][0]

            assert item["start"] == START.isoformat(), "start must come from the NAV series"
            assert item["end"] == date(2024, 1, 4).isoformat(), "end must come from the NAV series"

    def test_paging(self, client: TestClient) -> None:
        body = client.get("/api/backtests", params={"limit": 1, "offset": 1}).json()
        assert body["total"] == 2
        assert [item["run_id"] for item in body["items"]] == ["run-old"]

    def test_bad_paging_is_a_client_error(self, client: TestClient) -> None:
        assert client.get("/api/backtests", params={"offset": -1}).status_code == 422
        assert client.get("/api/backtests", params={"limit": 0}).status_code == 422


class TestDetail:
    def test_returns_metrics_series_and_positions(self, client: TestClient) -> None:
        body = client.get("/api/backtests/run-new").json()
        assert body["run_id"] == "run-new"
        assert body["status"] == "ok"
        assert body["metrics"]["sharpe_ratio"] == pytest.approx(1.8)
        assert body["cash"] == pytest.approx(500.0)
        assert body["total_value"] == pytest.approx(1100.0)
        assert body["series"]["nav"] == pytest.approx([1.0, 1.05, 1.10])
        assert len(body["series"]["dates"]) == 3
        assert body["positions"][0]["ts_code"] == "600000.SH"
        assert body["positions"][0]["weight"] == pytest.approx(1.0)

    def test_cancelled_run_reports_its_status(self, client: TestClient) -> None:
        body = client.get("/api/backtests/run-old").json()
        assert body["status"] == "cancelled"
        assert len(body["series"]["nav"]) == 2

    def test_unknown_run_is_404(self, client: TestClient) -> None:
        response = client.get("/api/backtests/nope")
        assert response.status_code == 404


class TestTrades:
    def test_pages_trades(self, client: TestClient) -> None:
        body = client.get("/api/backtests/run-new/trades").json()
        assert body["total"] == 2
        assert [item["seq"] for item in body["items"]] == [1, 2]
        assert body["items"][0]["action"] == "BUY"
        assert body["items"][1]["stamp_duty"] == pytest.approx(0.55)

    def test_page_window(self, client: TestClient) -> None:
        body = client.get("/api/backtests/run-new/trades", params={"limit": 1, "offset": 1}).json()
        assert body["total"] == 2
        assert [item["seq"] for item in body["items"]] == [2]

    def test_bad_paging_is_a_client_error(self, client: TestClient) -> None:
        assert client.get("/api/backtests/run-new/trades", params={"limit": 501}).status_code == 422
        assert client.get("/api/backtests/run-new/trades", params={"offset": -1}).status_code == 422


class TestComparison:
    def test_returns_curves_in_requested_order(self, client: TestClient) -> None:
        body = client.get("/api/backtests/compare", params={"runs": ["run-old", "run-new"]}).json()
        assert [entry["run_id"] for entry in body["runs"]] == ["run-old", "run-new"]
        assert body["runs"][0]["series"]["nav"] == pytest.approx([1.0, 0.95])
        assert body["runs"][1]["start"] == START.isoformat()
        assert body["missing"] == []

    def test_missing_runs_are_reported(self, client: TestClient) -> None:
        body = client.get("/api/backtests/compare", params={"runs": ["run-new", "ghost"]}).json()
        assert [entry["run_id"] for entry in body["runs"]] == ["run-new"]
        assert body["missing"] == ["ghost"]

    def test_too_many_runs_is_a_client_error(self, client: TestClient) -> None:
        many = [f"r{index}" for index in range(MAX_COMPARISON_RUNS + 1)]
        assert client.get("/api/backtests/compare", params={"runs": many}).status_code == 422

    def test_missing_param_is_rejected(self, client: TestClient) -> None:
        assert client.get("/api/backtests/compare").status_code == 422


class TestProgress:
    def test_progress_reaches_the_list_payload(self, client: TestClient) -> None:
        """The row's progress bar reads this field; a dropped pass-through is silent."""
        body = client.get("/api/backtests").json()
        assert all("progress" in item for item in body["items"])

    def test_a_running_run_reports_its_progress(self) -> None:
        """Seeded after startup: the worker rewrites leftover non-terminal rows
        to `interrupted` when it boots, which would hide the state under test."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "running.db"
            init_db(str(db)).close()

            with TestClient(create_platform_app(str(_config(db)))) as client:
                store = DataStore(str(db))
                _seed(store, "run-live", nav=[1.0, 1.05], started_at=datetime(2024, 6, 1), status="running")
                store.conn.execute("UPDATE run SET progress = 0.37 WHERE run_id = ?", ["run-live"])
                store.close()

                item = client.get("/api/backtests").json()["items"][0]

            assert item["status"] == "running"
            assert item["progress"] == pytest.approx(0.37)


class TestResubmission:
    def test_the_same_params_twice_yield_two_result_sets(self) -> None:
        """Rerunning an experiment must not overwrite the earlier one."""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "twice.db"
            init_db(str(db)).close()
            store = DataStore(str(db))
            params = {"strategy": "factor_ranking", "start": "2024-01-01", "end": "2024-06-01"}
            for run_id in ("run-first", "run-second"):
                _insert_run_with_params(store, run_id, params, nav=[1.0, 1.05, 1.10])
            store.close()

            with TestClient(create_platform_app(str(_config(db)))) as client:
                first = client.get("/api/backtests/run-first").json()
                second = client.get("/api/backtests/run-second").json()

            assert first["series"]["nav"] and second["series"]["nav"]
            assert first["run_id"] != second["run_id"]


class TestDataOverview:
    def test_result_tables_appear_in_the_data_status(self, client: TestClient) -> None:
        body = client.get("/api/data/status").json()
        tables = body["tables"]
        names = {row["table"] for row in tables} if isinstance(tables, list) else set(tables)
        for name in ("backtest_nav", "backtest_trade", "backtest_metric", "backtest_position"):
            assert name in names, f"{name} is missing from the data overview"


class TestStrategies:
    """The submit form's option list comes from the server, not the bundle."""

    def test_lists_registered_strategies(self, client: TestClient) -> None:
        body = client.get("/api/backtests/strategies").json()
        assert "factor_ranking" in body["items"]
        assert body["default"] == "factor_ranking"

    def test_is_not_swallowed_by_the_run_id_route(self, client: TestClient) -> None:
        """Declared before `/{run_id}`; the other order would 404 it forever."""
        response = client.get("/api/backtests/strategies")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")


class TestRouting:
    def test_compare_is_not_swallowed_by_the_run_id_route(self, client: TestClient) -> None:
        """``/compare`` must not be read as a run id — declaration order decides."""
        response = client.get("/api/backtests/compare", params={"runs": ["run-new"]})
        assert response.status_code == 200, "the compare route must win over /{run_id}"
        assert "runs" in response.json()

    def test_paths_do_not_fall_through_to_the_spa(self, client: TestClient) -> None:
        response = client.get("/api/backtests")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/json")

    def test_router_exposes_no_write_endpoint(self, client: TestClient) -> None:
        """Starting a backtest is a run; the read router has no POST."""
        assert client.post("/api/backtests").status_code == 405

    def test_backtest_kind_is_submittable_through_the_run_api(self, client: TestClient) -> None:
        before = client.get("/api/runs").json()["total"]
        response = client.post("/api/runs", json={"kind": "backtest", "params": {"start": "2024-06-01"}})

        assert response.status_code == 202
        assert response.json()["kind"] == "backtest"
        assert client.get("/api/runs").json()["total"] == before + 1

    def test_inverted_window_is_rejected_before_a_run_row_is_created(self, client: TestClient) -> None:
        before = client.get("/api/runs").json()["total"]
        response = client.post(
            "/api/runs",
            json={"kind": "backtest", "params": {"start": "2024-06-01", "end": "2024-01-01"}},
        )

        assert response.status_code == 422
        assert client.get("/api/runs").json()["total"] == before, "a rejected submission must leave no trace"
