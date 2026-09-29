"""Strategy-domain routes: the registry, signal-run history, and one run's signals."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.runtime.app import create_platform_app
from quant_trade.strategies.signal_store import STRATEGY_SIGNAL_KIND, build_signal_frame, save_strategy_signals

SIGNAL_DAY = date(2026, 7, 24)


def _add_run(store: DataStore, run_id: str, *, created: datetime, status: str = "ok") -> None:
    store.conn.execute(
        "INSERT INTO run (run_id, kind, params_json, status, progress, created_at, finished_at) "
        "VALUES (?, ?, '{}', ?, 1.0, ?, ?)",
        [run_id, STRATEGY_SIGNAL_KIND, status, created, created],
    )


def _add_signals(store: DataStore, run_id: str, *, count: int = 3, strategy: str = "factor_ranking") -> None:
    orders = [
        {"ts_code": f"{index:06d}.SZ", "direction": "BUY", "target_pct": 0.1, "reason": f"理由{index}"}
        for index in range(count)
    ]
    save_strategy_signals(store, run_id, build_signal_frame(orders, SIGNAL_DAY, strategy))


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    db_path = tmp_path / "quant.db"
    init_db(str(db_path)).close()
    store = DataStore(str(db_path))
    _add_run(store, "run-new", created=datetime(2026, 7, 24, 16, 0))
    _add_signals(store, "run-new", count=3)
    store.conn.execute(
        "INSERT INTO artifact (artifact_id, run_id, kind, storage, ref, row_count, meta_json) "
        "VALUES ('run-new-a', 'run-new', 'table', 'table', 'strategy_signal', 3, ?)",
        [json.dumps({"strategy": "factor_ranking", "signal_date": "2026-07-24", "universe_size": 772})],
    )
    _add_run(store, "run-old", created=datetime(2026, 7, 17, 16, 0))
    _add_signals(store, "run-old", count=2, strategy="model_ranking")
    # Finished, and selected nothing. It exists, so its detail is served rather
    # than 404'd — that is what lets the page say 无信号 instead of 不存在.
    _add_run(store, "run-empty", created=datetime(2026, 7, 23, 16, 0))
    # A run that never produced signals. Seeded as ``pending``, but the job
    # runner's startup recovery rewrites every leftover non-terminal row, so
    # by the time a request is served this reads ``interrupted``.
    _add_run(store, "run-stranded", created=datetime(2026, 7, 25, 16, 0), status="pending")
    store.close()

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"data:\n  db_path: {db_path}\n", encoding="utf-8")
    with TestClient(create_platform_app(str(config_file))) as c:
        yield c


class TestRegistry:
    def test_lists_registered_strategies(self, client: TestClient) -> None:
        body = client.get("/api/strategies").json()
        assert "factor_ranking" in body["items"]
        assert "model_ranking" in body["items"]

    def test_reports_the_configured_default(self, client: TestClient) -> None:
        body = client.get("/api/strategies").json()
        assert body["default"]
        assert body["default"] in body["items"]

    def test_has_no_write_endpoint(self, client: TestClient) -> None:
        """Signals are submitted through the unified run API, not here."""
        assert client.post("/api/strategies", json={}).status_code == 405


class TestRunList:
    def test_lists_runs_newest_first(self, client: TestClient) -> None:
        body = client.get("/api/strategies/runs").json()
        assert body["total"] == 4
        assert [item["run_id"] for item in body["items"]] == [
            "run-stranded",
            "run-new",
            "run-empty",
            "run-old",
        ]

    def test_rows_carry_the_derived_summary(self, client: TestClient) -> None:
        item = next(row for row in client.get("/api/strategies/runs").json()["items"] if row["run_id"] == "run-new")
        assert item["signal_date"] == "2026-07-24"
        assert item["strategy"] == "factor_ranking"
        assert item["order_count"] == 3
        assert item["universe_size"] == 772

    def test_run_without_signals_lists_with_nulls(self, client: TestClient) -> None:
        """Absent, not zero: 0 would claim the run produced no signals.

        The status reads ``interrupted`` rather than ``pending`` because
        startup recovery rewrites every non-terminal row — a live
        non-terminal run cannot be observed through the app at all. That
        case is covered at the service layer, where no runner is running.
        """
        item = next(
            row for row in client.get("/api/strategies/runs").json()["items"] if row["run_id"] == "run-stranded"
        )
        assert item["status"] == "interrupted"
        assert item["signal_date"] is None
        assert item["order_count"] is None
        assert item["universe_size"] is None

    def test_paging_is_bounded(self, client: TestClient) -> None:
        assert client.get("/api/strategies/runs?limit=0").status_code == 422
        assert client.get("/api/strategies/runs?offset=-1").status_code == 422
        assert len(client.get("/api/strategies/runs?limit=1&offset=1").json()["items"]) == 1


class TestSignals:
    def test_returns_orders_in_engine_order(self, client: TestClient) -> None:
        body = client.get("/api/strategies/runs/run-new").json()
        assert body["strategy"] == "factor_ranking"
        assert body["signal_date"] == "2026-07-24"
        assert body["universe_size"] == 772
        assert body["total"] == 3
        assert [order["seq"] for order in body["orders"]] == [1, 2, 3]
        assert body["orders"][0]["reason"] == "理由0"

    def test_pages(self, client: TestClient) -> None:
        body = client.get("/api/strategies/runs/run-old?limit=1&offset=1").json()
        assert body["total"] == 2
        assert [order["seq"] for order in body["orders"]] == [2]

    def test_unknown_run_is_404(self, client: TestClient) -> None:
        assert client.get("/api/strategies/runs/nope").status_code == 404

    def test_a_finished_run_with_no_orders_is_served_empty(self, client: TestClient) -> None:
        """A run that ran and selected nothing is found; it just has no orders.

        Answering 404 here would tell the reader the run does not exist, and the
        page would render 不存在 for a run they can see in the history list.
        """
        response = client.get("/api/strategies/runs/run-empty")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 0
        assert body["orders"] == []
        assert body["status"] == "ok"

    def test_out_of_range_page_size_is_422(self, client: TestClient) -> None:
        assert client.get("/api/strategies/runs/run-new?limit=0").status_code == 422
        assert client.get("/api/strategies/runs/run-new?limit=9999").status_code == 422


class TestSpaFallback:
    def test_strategy_routes_are_not_swallowed(self, client: TestClient) -> None:
        """`/api/...` must answer as JSON, never with the SPA shell."""
        for path in ("/api/strategies/runs/nope", "/api/strategies/nope"):
            response = client.get(path)
            assert response.status_code == 404
            assert response.headers["content-type"].startswith("application/json")


def test_empty_database_answers_honestly(tmp_path: Path) -> None:
    """A fresh install lists no runs rather than failing."""
    db_path = tmp_path / "fresh.db"
    init_db(str(db_path)).close()
    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"data:\n  db_path: {db_path}\n", encoding="utf-8")

    with tempfile.TemporaryDirectory(), TestClient(create_platform_app(str(config_file))) as c:
        assert c.get("/api/strategies/runs").json() == {"total": 0, "offset": 0, "limit": 20, "items": []}
        assert c.get("/api/strategies/runs/nope").status_code == 404


WEB = Path(__file__).parents[1] / "web"


class TestFrontendAlignment:
    """The page's field names must be the ones the API actually sends.

    Silent failure without this: rename `order_count` on either side and every
    cell falls back to its em-dash branch — a page that looks calm and shows
    nothing. Nothing else in the suite reads `api/strategies.ts`.
    """

    def _frontend(self) -> str:
        return (WEB / "src" / "api" / "strategies.ts").read_text(encoding="utf-8")

    def test_registry_fields_are_named_in_the_client(self, client: TestClient) -> None:
        body = client.get("/api/strategies").json()
        source = self._frontend()
        missing = [key for key in body if key not in source]
        assert missing == [], f"api/strategies.ts does not name {missing}"

    def test_run_row_fields_are_named_in_the_client(self, client: TestClient) -> None:
        row = client.get("/api/strategies/runs").json()["items"][0]
        source = self._frontend()
        missing = [key for key in row if key not in source]
        assert missing == [], f"api/strategies.ts does not name {missing}"

    def test_signal_page_fields_are_named_in_the_client(self, client: TestClient) -> None:
        body = client.get("/api/strategies/runs/run-new").json()
        source = self._frontend()
        missing = [key for key in body if key != "orders" and key not in source]
        assert missing == [], f"api/strategies.ts does not name {missing}"

    def test_order_fields_are_named_in_the_client(self, client: TestClient) -> None:
        order = client.get("/api/strategies/runs/run-new").json()["orders"][0]
        source = self._frontend()
        missing = [key for key in order if key not in source]
        assert missing == [], f"api/strategies.ts does not name {missing}"
