"""Data-domain routes: table stats, universe coverage and the trade calendar."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.runtime.app import create_platform_app
from quant_trade.services.context import NULL_CONTEXT, RunContext
from quant_trade.services.queries import TradeCalendarParams, trade_calendar

TODAY = date.today()


@pytest.fixture
def calendar_days() -> list[date]:
    """Five calendar days anchored on today: two before, today, two after."""
    return [TODAY + timedelta(days=offset) for offset in (-2, -1, 0, 1, 2)]


@pytest.fixture
def db_path(tmp_path: Path, calendar_days: list[date]) -> Path:
    """A database with a handful of calendar days and two stocks."""
    path = tmp_path / "quant.db"
    conn = init_db(str(path))
    # The second day after today is a non-trading day, so the counts differ.
    conn.executemany(
        "INSERT INTO trade_calendar VALUES (?, ?)",
        [(day, day != calendar_days[3]) for day in calendar_days],
    )
    conn.execute("INSERT INTO stock_basic VALUES ('000001.SZ', '平安银行', '银行', '主板', '1991-04-03', FALSE)")
    conn.execute("INSERT INTO stock_basic VALUES ('600000.SH', '浦发银行', '银行', '主板', '1999-11-10', FALSE)")
    conn.execute("INSERT INTO index_weights VALUES ('000300.SH', '000001.SZ', 1.0, '2020-01-01', NULL)")
    conn.execute("INSERT INTO index_weights VALUES ('000300.SH', '600000.SH', 1.0, '2020-01-01', NULL)")
    conn.execute("INSERT INTO daily_kline (ts_code, trade_date, close) VALUES ('000001.SZ', ?, 10.0)", [TODAY])
    conn.close()
    return path


@pytest.fixture
def client(db_path: Path) -> Iterator[TestClient]:
    with tempfile.TemporaryDirectory() as tmp:
        config_file = Path(tmp) / "config.yaml"
        config_file.write_text(f"data:\n  db_path: {db_path}\n", encoding="utf-8")
        with TestClient(create_platform_app(str(config_file))) as c:
            yield c


class TestStatus:
    def test_reports_every_known_table(self, client: TestClient) -> None:
        body = client.get("/api/data/status").json()
        names = [t["table"] for t in body["tables"]]
        assert "daily_kline" in names
        assert "trade_calendar" in names
        assert names == sorted(names)

    def test_table_carries_rows_and_bounds(self, client: TestClient, calendar_days: list[date]) -> None:
        by_name = {t["table"]: t for t in client.get("/api/data/status").json()["tables"]}
        assert by_name["trade_calendar"]["rows"] == 5
        assert by_name["trade_calendar"]["earliest"] == calendar_days[0].isoformat()
        assert by_name["trade_calendar"]["latest"] == calendar_days[-1].isoformat()

    def test_empty_table_has_no_bounds(self, client: TestClient) -> None:
        """A table with no rows must be visibly empty, not a zero with no signal."""
        by_name = {t["table"]: t for t in client.get("/api/data/status").json()["tables"]}
        assert by_name["factor_values"]["rows"] == 0
        assert by_name["factor_values"]["earliest"] is None
        assert by_name["factor_values"]["latest"] is None

    def test_latest_trade_date_and_db_path(self, client: TestClient) -> None:
        body = client.get("/api/data/status").json()
        assert body["db_path"].endswith("quant.db")
        assert body["latest_trade_date"] == TODAY.isoformat()

    def test_universe_size(self, client: TestClient) -> None:
        assert client.get("/api/data/status").json()["universe_size"] == 2


class TestCoverage:
    def test_counts_and_ratio(self, client: TestClient) -> None:
        body = client.get("/api/data/coverage").json()
        assert body["universe_size"] == 2
        assert body["covered"] == 1
        assert body["ratio"] == pytest.approx(0.5)
        assert body["missing"] == ["600000.SH"]
        assert body["missing_total"] == 1

    def test_zero_limit_is_rejected(self, client: TestClient) -> None:
        assert client.get("/api/data/coverage", params={"limit": 0}).status_code == 422

    def test_paging_window_is_reported(self, client: TestClient) -> None:
        body = client.get("/api/data/coverage", params={"offset": 1, "limit": 10}).json()
        assert body["missing"] == []
        assert body["missing_total"] == 1
        assert body["missing_offset"] == 1
        assert body["missing_limit"] == 10

    def test_as_of_is_echoed(self, client: TestClient) -> None:
        body = client.get("/api/data/coverage", params={"as_of": TODAY.isoformat()}).json()
        assert body["as_of"] == TODAY.isoformat()


class TestCalendar:
    def test_no_window_reads_the_current_year(self, client: TestClient, calendar_days: list[date]) -> None:
        """With no window, only days from the current year are queried."""
        body = client.get("/api/data/calendar").json()
        in_year = [day.isoformat() for day in calendar_days if day.year == TODAY.year]
        assert [d["trade_date"] for d in body["days"]] == in_year

    def test_explicit_window(self, client: TestClient, calendar_days: list[date]) -> None:
        body = client.get(
            "/api/data/calendar",
            params={"start": calendar_days[0].isoformat(), "end": calendar_days[-1].isoformat()},
        ).json()
        assert [d["trade_date"] for d in body["days"]] == [day.isoformat() for day in calendar_days]
        assert body["open_days"] == 4
        assert body["closed_days"] == 1

    def test_non_trading_days_are_marked(self, client: TestClient, calendar_days: list[date]) -> None:
        closed = calendar_days[3].isoformat()
        body = client.get("/api/data/calendar", params={"start": closed, "end": closed}).json()
        assert body["days"] == [{"trade_date": closed, "is_open": False}]

    def test_empty_window_echoes_the_request(self, client: TestClient) -> None:
        body = client.get("/api/data/calendar", params={"start": "2020-01-01", "end": "2020-01-31"}).json()
        assert body["days"] == []
        assert body["open_days"] == 0
        # Falls back to the requested window when there is nothing to bound it.
        assert body["start"] == "2020-01-01"
        assert body["end"] == "2020-01-31"


class TestCalendarService:
    def test_reads_the_whole_stored_range_by_default(self, db_path: Path, calendar_days: list[date]) -> None:
        store = DataStore(str(db_path))
        try:
            view = trade_calendar(TradeCalendarParams(), RunContext(store=store))
        finally:
            store.close()

        assert view.start == calendar_days[0]
        assert view.end == calendar_days[-1]
        assert (view.open_days, view.closed_days) == (4, 1)

    def test_no_context_needed_for_params_construction(self) -> None:
        assert TradeCalendarParams(start=date(2024, 1, 1)).start == date(2024, 1, 1)
        assert NULL_CONTEXT.cancelled() is False
