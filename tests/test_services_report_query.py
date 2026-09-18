"""The report browser's read path.

A report is a run of kind ``weekly`` with an HTML artifact, so these tests seed
run and artifact rows directly: the queries are what is under test, and going
through the job runner would only add the runner to the picture.
"""

from __future__ import annotations

import json
import tempfile
from datetime import date, datetime
from pathlib import Path

import pytest

from quant_trade.config import AppConfig
from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.services import RunContext
from quant_trade.services.report_query import (
    ReportDetailParams,
    ReportListParams,
    report_detail,
    report_html,
    report_list,
    resolve_report_path,
)


def _store(tmp: str) -> DataStore:
    db = f"{tmp}/a.db"
    init_db(db).close()
    return DataStore(db)


def _context(tmp: str, store: DataStore) -> RunContext:
    config = AppConfig()
    config.report.output_dir = tmp
    return RunContext(run_id="t", config=config, store=store)


def _add_run(
    store: DataStore,
    run_id: str,
    *,
    kind: str = "weekly",
    status: str = "ok",
    finished_at: datetime | None = datetime(2026, 7, 24, 16, 0),
    progress: float = 1.0,
) -> None:
    store.conn.execute(
        "INSERT INTO run (run_id, kind, params_json, status, progress, finished_at) VALUES (?, ?, ?, ?, ?, ?)",
        [run_id, kind, "{}", status, progress, finished_at],
    )


def _add_artifact(
    store: DataStore,
    run_id: str,
    ref: str,
    *,
    storage: str = "html",
    meta: dict[str, object] | None = None,
) -> None:
    store.conn.execute(
        "INSERT INTO artifact (artifact_id, run_id, kind, storage, ref, row_count, meta_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [f"{run_id}-a", run_id, "report", storage, ref, None, json.dumps(meta) if meta is not None else None],
    )


class TestReportList:
    def test_lists_newest_first_and_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            for run_id, day in (("r1", 22), ("r2", 24), ("r3", 23)):
                _add_run(store, run_id, finished_at=datetime(2026, 7, day, 16, 0))
                _add_artifact(store, run_id, f"{tmp}/weekly_{day}.html")
            ctx = _context(tmp, store)

            page = report_list(ReportListParams(limit=2, offset=0), ctx)
            assert page.total == 3
            assert [row.run_id for row in page.reports] == ["r2", "r3"]

            second = report_list(ReportListParams(limit=2, offset=2), ctx)
            assert [row.run_id for row in second.reports] == ["r1"]

    def test_meta_carries_freshness_without_opening_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "r1")
            _add_artifact(
                store,
                "r1",
                f"{tmp}/weekly_2026_07_24.html",
                meta={"signal_date": "2026-07-24", "order_count": 5, "trade_count": 12},
            )

            row = report_list(ReportListParams(), _context(tmp, store)).reports[0]
            assert row.signal_date == date(2026, 7, 24)
            assert row.order_count == 5
            assert row.trade_count == 12
            assert row.file_name == "weekly_2026_07_24.html"

    def test_unparseable_meta_lists_without_the_freshness_columns(self) -> None:
        """One malformed historical row must not break the whole page."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "r1")
            store.conn.execute(
                "INSERT INTO artifact (artifact_id, run_id, kind, storage, ref, meta_json) VALUES (?, ?, ?, ?, ?, ?)",
                ["r1-a", "r1", "report", "html", f"{tmp}/x.html", "{not json"],
            )

            row = report_list(ReportListParams(), _context(tmp, store)).reports[0]
            assert row.signal_date is None
            assert row.order_count is None
            assert row.file_name == "x.html"

    def test_other_kinds_and_storages_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "bt", kind="backtest")
            _add_artifact(store, "bt", f"{tmp}/nav.parquet", storage="parquet")
            _add_run(store, "w1")
            _add_artifact(store, "w1", f"{tmp}/weekly.html")

            page = report_list(ReportListParams(), _context(tmp, store))
            assert page.total == 1
            assert page.reports[0].run_id == "w1"


class TestReportDetail:
    def test_unknown_run_is_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)

            detail = report_detail(ReportDetailParams(run_id="nope"), _context(tmp, store))
            assert detail.found is False

    def test_run_without_a_report_artifact_is_not_found(self) -> None:
        """A weekly run that was cancelled has a run row but nothing to show."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "r1", status="cancelled", finished_at=None)

            assert report_detail(ReportDetailParams(run_id="r1"), _context(tmp, store)).found is False

    def test_detail_reports_status_and_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "r1")
            store.conn.execute("UPDATE run SET error = ? WHERE run_id = ?", ["boom", "r1"])
            _add_artifact(store, "r1", f"{tmp}/weekly.html", meta={"signal_date": "2026-07-24"})

            detail = report_detail(ReportDetailParams(run_id="r1"), _context(tmp, store))
            assert detail.found is True
            assert detail.error == "boom"
            assert detail.signal_date == date(2026, 7, 24)


class TestReportHtml:
    def _seeded(self, tmp: str, report_body: str) -> tuple[DataStore, Path]:
        db = f"{tmp}/a.db"
        init_db(db).close()
        store = DataStore(db)
        path = Path(tmp) / "weekly_2026_07_24.html"
        path.write_text(report_body, encoding="utf-8")
        _add_run(store, "r1")
        _add_artifact(store, "r1", str(path))
        return store, path

    def test_reads_the_report_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store, _ = self._seeded(tmp, "<html><body>周报正文</body></html>")
            content = report_html(ReportDetailParams(run_id="r1"), _context(tmp, store))

            assert content.found is True
            assert "周报正文" in content.html
            assert content.file_name == "weekly_2026_07_24.html"

    def test_missing_file_is_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "r1")
            _add_artifact(store, "r1", f"{tmp}/gone.html")

            assert report_html(ReportDetailParams(run_id="r1"), _context(tmp, store)).found is False

    def test_path_outside_the_output_directory_is_refused(self) -> None:
        """``artifact.ref`` came out of the database, so it is untrusted input."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            outside = Path(tmp).parent / "secret.html"
            outside.write_text("do not serve me", encoding="utf-8")
            _add_run(store, "r1")
            _add_artifact(store, "r1", str(outside))

            assert report_html(ReportDetailParams(run_id="r1"), _context(tmp, store)).found is False

    def test_traversal_escaping_the_output_directory_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp) / "reports"
            reports.mkdir()
            secret = Path(tmp) / "secret.html"
            secret.write_text("do not serve me", encoding="utf-8")
            store = _store(tmp)
            _add_run(store, "r1")
            _add_artifact(store, "r1", str(reports / ".." / "secret.html"))

            config = AppConfig()
            config.report.output_dir = str(reports)
            ctx = RunContext(run_id="t", config=config, store=store)
            assert report_html(ReportDetailParams(run_id="r1"), ctx).found is False

    def test_resolve_reports_unknown_run_as_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            config = AppConfig()
            config.report.output_dir = tmp

            assert resolve_report_path(store, "nope", config) is None


class TestParams:
    def test_round_trip_through_json(self) -> None:
        params = ReportListParams(limit=50, offset=10)
        assert ReportListParams.model_validate_json(params.model_dump_json()) == params

    def test_page_size_is_bounded(self) -> None:
        with pytest.raises(ValueError):
            ReportListParams(limit=0)
        with pytest.raises(ValueError):
            ReportListParams(offset=-1)

    def test_run_id_must_not_be_empty(self) -> None:
        with pytest.raises(ValueError):
            ReportDetailParams(run_id="")


class TestRunsWithoutAReportYet:
    """A queued or running weekly run lists before it has produced anything.

    An inner join over artifacts would hide it until it had already finished,
    which is exactly the window where a reader wants to watch its progress.
    """

    def test_running_run_is_listed_with_its_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "running", status="running", finished_at=None, progress=0.4)

            page = report_list(ReportListParams(), _context(tmp, store))

            assert page.total == 1
            row = page.reports[0]
            assert row.run_id == "running"
            assert row.status == "running"
            assert row.progress == pytest.approx(0.4)

    def test_freshness_columns_are_empty_not_zero(self) -> None:
        """0 would read as "this report counted zero trades"."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "running", status="running", finished_at=None)

            row = report_list(ReportListParams(), _context(tmp, store)).reports[0]

            assert row.signal_date is None
            assert row.order_count is None
            assert row.trade_count is None
            assert row.file_name == ""

    def test_preview_is_not_offered_before_there_is_a_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "running", status="running", finished_at=None)
            ctx = _context(tmp, store)

            assert report_detail(ReportDetailParams(run_id="running"), ctx).found is False
            assert report_html(ReportDetailParams(run_id="running"), ctx).found is False

    def test_a_finished_run_without_a_report_still_lists(self) -> None:
        """A cancelled weekly run is a fact worth showing, not a missing row."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "cancelled", status="cancelled", finished_at=None)

            page = report_list(ReportListParams(), _context(tmp, store))
            assert [row.run_id for row in page.reports] == ["cancelled"]
