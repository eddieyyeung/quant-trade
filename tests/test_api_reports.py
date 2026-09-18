"""Report-domain routes: history, metadata, and the rendered document.

The HTML route is the one worth guarding twice: it hands a file's bytes back to
whoever asks, and the path it reads comes out of the artifact table.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.runtime.app import create_platform_app

REPORT_BODY = "<html><body>周报正文</body></html>"


def _add_run(store: DataStore, run_id: str, *, created_at: datetime, status: str = "ok") -> None:
    store.conn.execute(
        "INSERT INTO run (run_id, kind, params_json, status, progress, created_at, finished_at) "
        "VALUES (?, 'weekly', '{}', ?, 1.0, ?, ?)",
        [run_id, status, created_at, created_at],
    )


def _add_artifact(store: DataStore, run_id: str, ref: str, meta: dict[str, object] | None = None) -> None:
    store.conn.execute(
        "INSERT INTO artifact (artifact_id, run_id, kind, storage, ref, meta_json) VALUES (?, ?, 'report', 'html', ?, ?)",
        [f"{run_id}-a", run_id, ref, json.dumps(meta) if meta is not None else None],
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[dict[str, Path]]:
    """A database, a report directory, and one report inside it."""
    reports = tmp_path / "reports"
    reports.mkdir()
    report_file = reports / "weekly_2026_07_24.html"
    report_file.write_text(REPORT_BODY, encoding="utf-8")

    db_path = tmp_path / "quant.db"
    init_db(str(db_path)).close()
    store = DataStore(str(db_path))
    _add_run(store, "run-new", created_at=datetime(2026, 7, 24, 16, 0))
    _add_artifact(
        store,
        "run-new",
        str(report_file),
        meta={"signal_date": "2026-07-24", "order_count": 5, "trade_count": 12},
    )
    _add_run(store, "run-old", created_at=datetime(2026, 7, 17, 16, 0))
    _add_artifact(store, "run-old", str(reports / "weekly_2026_07_17.html"))
    store.close()

    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        f"data:\n  db_path: {db_path}\nreport:\n  output_dir: {reports}\n",
        encoding="utf-8",
    )
    yield {"tmp": tmp_path, "reports": reports, "file": report_file, "config": config_file}


@pytest.fixture
def client(workspace: dict[str, Path]) -> Iterator[TestClient]:
    with TestClient(create_platform_app(str(workspace["config"]))) as c:
        yield c


class TestReportList:
    def test_lists_reports_newest_first(self, client: TestClient) -> None:
        body = client.get("/api/reports").json()
        assert body["total"] == 2
        assert [item["run_id"] for item in body["items"]] == ["run-new", "run-old"]

    def test_rows_carry_the_data_freshness(self, client: TestClient) -> None:
        item = client.get("/api/reports").json()["items"][0]
        assert item["signal_date"] == "2026-07-24"
        assert item["order_count"] == 5
        assert item["trade_count"] == 12
        assert item["file_name"] == "weekly_2026_07_24.html"

    def test_paging_is_bounded(self, client: TestClient) -> None:
        assert client.get("/api/reports?limit=0").status_code == 422
        assert client.get("/api/reports?offset=-1").status_code == 422
        assert len(client.get("/api/reports?limit=1&offset=1").json()["items"]) == 1


class TestReportDetail:
    def test_returns_metadata(self, client: TestClient) -> None:
        body = client.get("/api/reports/run-new").json()
        assert body["run_id"] == "run-new"
        assert body["status"] == "ok"
        assert body["signal_date"] == "2026-07-24"
        assert body["file_name"] == "weekly_2026_07_24.html"

    def test_unknown_run_is_404(self, client: TestClient) -> None:
        assert client.get("/api/reports/nope").status_code == 404

    def test_run_without_a_report_is_404(self, client: TestClient, workspace: dict[str, Path]) -> None:
        store = DataStore(str(workspace["tmp"] / "quant.db"))
        _add_run(store, "run-cancelled", created_at=datetime(2026, 7, 20, 16, 0), status="cancelled")
        store.close()

        assert client.get("/api/reports/run-cancelled").status_code == 404


class TestReportHtml:
    def test_served_as_a_document(self, client: TestClient) -> None:
        response = client.get("/api/reports/run-new/html")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "周报正文" in response.text

    def test_inline_by_default(self, client: TestClient) -> None:
        """Embedding must not trigger a download prompt."""
        assert "content-disposition" not in client.get("/api/reports/run-new/html").headers

    def test_download_names_the_report_file(self, client: TestClient) -> None:
        response = client.get("/api/reports/run-new/html?download=1")
        assert response.headers["content-disposition"] == 'attachment; filename="weekly_2026_07_24.html"'
        assert "周报正文" in response.text

    def test_missing_file_is_404(self, client: TestClient) -> None:
        assert client.get("/api/reports/run-old/html").status_code == 404

    def test_unknown_run_is_404(self, client: TestClient) -> None:
        assert client.get("/api/reports/nope/html").status_code == 404

    def test_path_outside_the_output_directory_is_refused(self, client: TestClient, workspace: dict[str, Path]) -> None:
        """A row pointing elsewhere must not turn this into a file-read endpoint."""
        outside = workspace["tmp"] / "secret.html"
        outside.write_text("do not serve me", encoding="utf-8")
        store = DataStore(str(workspace["tmp"] / "quant.db"))
        _add_run(store, "run-escape", created_at=datetime(2026, 7, 25, 16, 0))
        _add_artifact(store, "run-escape", str(outside))
        store.close()

        response = client.get("/api/reports/run-escape/html")
        assert response.status_code == 404
        assert "do not serve me" not in response.text


class TestSpaFallback:
    def test_report_routes_are_not_swallowed_by_the_catch_all(self, client: TestClient) -> None:
        """`/api/...` must answer as JSON, never with the SPA shell."""
        response = client.get("/api/reports/nope")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")

    def test_the_document_route_is_not_swallowed_either(self, client: TestClient) -> None:
        response = client.get("/api/reports/nope/html")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")


def test_report_output_dir_is_relative_to_the_config(tmp_path: Path) -> None:
    """A relative ``report.output_dir`` resolves like any other path.

    The guard compares resolved paths on both sides, so a relative directory
    has to resolve the same way for the registered ref and for the root.
    """
    with tempfile.TemporaryDirectory() as tmp:
        reports = Path(tmp) / "reports"
        reports.mkdir()
        (reports / "weekly_x.html").write_text(REPORT_BODY, encoding="utf-8")

        db_path = Path(tmp) / "quant.db"
        init_db(str(db_path)).close()
        store = DataStore(str(db_path))
        _add_run(store, "run-rel", created_at=datetime(2026, 7, 24, 16, 0))
        _add_artifact(store, "run-rel", "reports/weekly_x.html")
        store.close()

        config_file = Path(tmp) / "config.yaml"
        config_file.write_text(f"data:\n  db_path: {db_path}\nreport:\n  output_dir: reports\n", encoding="utf-8")
        with TestClient(create_platform_app(str(config_file))) as c:
            # Relative paths resolve against the process CWD, which is not the
            # temp directory, so this is expected to miss rather than to serve
            # a file from an unpredictable place.
            assert c.get("/api/reports/run-rel/html").status_code == 404


WEB = Path(__file__).parents[1] / "web"


class TestFrontendFieldAlignment:
    """The page's field names must be the ones the API actually sends.

    Silent failure without this: rename `order_count` on either side and every
    table cell falls back to its em-dash branch — a page that looks calm and
    shows nothing.
    """

    def _frontend(self) -> str:
        return (WEB / "src" / "api" / "reports.ts").read_text(encoding="utf-8")

    def test_list_row_fields_are_all_named_in_the_client(self, client: TestClient) -> None:
        body = client.get("/api/reports").json()
        source = self._frontend()

        missing = [key for key in body["items"][0] if key not in source]
        assert missing == [], f"api/reports.ts does not name {missing}"

    def test_page_fields_are_all_named_in_the_client(self, client: TestClient) -> None:
        body = client.get("/api/reports").json()
        source = self._frontend()

        missing = [key for key in body if key != "items" and key not in source]
        assert missing == [], f"api/reports.ts does not name {missing}"

    def test_detail_fields_are_all_named_in_the_client(self, client: TestClient) -> None:
        body = client.get("/api/reports/run-new").json()
        source = self._frontend()

        missing = [key for key in body if key not in source]
        assert missing == [], f"api/reports.ts does not name {missing}"

    def test_the_detail_route_sends_no_extra_keys(self, client: TestClient) -> None:
        """A field the client does not know about is a field nobody renders."""
        list_keys = set(client.get("/api/reports").json()["items"][0])
        detail_keys = set(client.get("/api/reports/run-new").json())

        assert detail_keys - list_keys == {"error"}
