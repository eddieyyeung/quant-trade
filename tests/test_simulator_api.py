"""Test simulator web API — FastAPI endpoints per simulator-web spec."""

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.simulator.api import create_app

BASE_START = date(date.today().year - 1, 3, 1)


def _build_engine_db(db_path: str) -> DataStore:
    """Seed temp DuckDB with minimal market data (same shape as engine tests)."""
    Path(db_path).unlink(missing_ok=True)
    conn = init_db(db_path)

    today = date.today()
    start = date(today.year - 1, 1, 1)
    trade_dates: list[date] = []
    d = start
    while d <= today:
        if d.weekday() < 5:
            trade_dates.append(d)
        d += timedelta(days=1)

    stock_rows = [
        ("000300.SH", "沪深300", "金融", "main", start, False),
        ("600519.SH", "贵州茅台", "制造", "main", start, False),
        ("000858.SZ", "五粮液", "制造", "main", start, False),
        ("601318.SH", "中国平安", "金融", "main", start, False),
    ]
    conn.executemany("INSERT INTO stock_basic VALUES (?, ?, ?, ?, ?, ?)", stock_rows)
    conn.executemany("INSERT INTO trade_calendar VALUES (?, ?)", [(td, True) for td in trade_dates])

    np.random.seed(42)
    kline_rows: list[tuple] = []
    for code, *_ in stock_rows:
        price = 3000.0 if code == "000300.SH" else float(np.random.uniform(10, 100))
        for td in trade_dates:
            ret = np.random.normal(0.0005, 0.02)
            price *= 1 + ret
            kline_rows.append(
                (
                    code,
                    td,
                    price * 0.99,
                    price * 1.02,
                    price * 0.98,
                    price,
                    np.random.uniform(1e6, 1e8),
                    price * np.random.uniform(1e6, 1e8),
                    np.random.normal(0, 2.0),
                    np.random.uniform(0.5, 5.0),
                )
            )
    conn.executemany("INSERT INTO daily_kline VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", kline_rows)

    for code in ["600519.SH", "000858.SZ", "601318.SH"]:
        conn.execute(
            "INSERT INTO index_weights VALUES (?, ?, ?, ?, ?)",
            ["000300.SH", code, 0.03, start, None],
        )

    return DataStore(db_path)


@pytest.fixture()
def client(tmp_path: Path):
    """Build app + TestClient against a seeded temp DB."""
    db_path = str(tmp_path / "test.db")
    _build_engine_db(db_path)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"data:\n  db_path: {db_path}\n", encoding="utf-8")
    app = create_app(config_path=str(cfg))
    with TestClient(app) as c:
        yield c


def _create_session(client: TestClient, **kwargs) -> dict:
    resp = client.post("/api/sessions", params={"name": "API测试", **kwargs})
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestSessionCRUD:
    def test_create_session(self, client) -> None:
        result = _create_session(client, start_date=str(BASE_START))
        assert result["session_id"]
        assert result["cursor_date"]
        assert result["total_weeks"] > 0
        assert "snapshot" in result
        snap = result["snapshot"]
        assert snap["signal_date"] and snap["total_value"] > 0

    def test_list_sessions_no_portfolio_json(self, client) -> None:
        _create_session(client, start_date=str(BASE_START))
        resp = client.get("/api/sessions", headers={"Origin": "http://localhost:9333"})
        assert resp.headers["access-control-allow-origin"] == "*"  # CORS: any origin in dev
        rows = resp.json()
        assert len(rows) == 1
        assert all("portfolio_json" not in r for r in rows)
        assert all(isinstance(r.get("start_date"), str) for r in rows)

    def test_get_session(self, client) -> None:
        sid = _create_session(client, start_date=str(BASE_START))["session_id"]
        detail = client.get(f"/api/sessions/{sid}").json()
        assert detail["session_id"] == sid
        assert "week_number" in detail
        assert "portfolio_value" in detail
        assert "previous_decisions" in detail
        assert "snapshot" in detail

    def test_session_404(self, client) -> None:
        resp = client.get("/api/sessions/nope")
        assert resp.status_code == 404
        resp2 = client.get("/api/sessions/nope/status")
        assert resp2.status_code == 404

    def test_delete_session(self, client) -> None:
        sid = _create_session(client, start_date=str(BASE_START))["session_id"]
        assert client.delete(f"/api/sessions/{sid}").json() == {"deleted": sid}
        assert client.get(f"/api/sessions/{sid}").status_code == 404


class TestWeekAdvance:
    def test_skip(self, client) -> None:
        sid = _create_session(client, start_date=str(BASE_START))["session_id"]
        before = client.get(f"/api/sessions/{sid}").json()
        result = client.post(f"/api/sessions/{sid}/skip").json()
        assert result["cursor_advanced"] is True
        assert result["next_cursor_date"] > before["cursor_date"]
        assert "portfolio_total_value" in result

    def test_step_empty_orders(self, client) -> None:
        sid = _create_session(client, start_date=str(BASE_START))["session_id"]
        before = client.get(f"/api/sessions/{sid}").json()
        result = client.post(
            f"/api/sessions/{sid}/step",
            json={"orders": [], "notes": "空仓观望"},
        ).json()
        assert result["cursor_advanced"] is True
        assert result["next_cursor_date"] > before["cursor_date"]
        assert result["executed_orders"] == []
        assert "decision_number" in result

    def test_step_invalid_session_400(self, client) -> None:
        resp = client.post("/api/sessions/nope/step", json={"orders": []})
        assert resp.status_code == 400

    def test_skip_invalid_session_400(self, client) -> None:
        resp = client.post("/api/sessions/nope/skip")
        assert resp.status_code == 400


class TestCompare:
    def test_compare_structure(self, client) -> None:
        sid = _create_session(client, start_date=str(BASE_START))["session_id"]
        result = client.get(f"/api/sessions/{sid}/compare").json()
        assert result["weeks_completed"] == 0
        for key in ("nav_manual", "nav_benchmark", "metrics", "weekly_diffs", "html_path"):
            assert key in result

    def test_compare_404(self, client) -> None:
        assert client.get("/api/sessions/nope/compare").status_code == 404


class TestFrontendContract:
    """Frontend dev wiring per simulator-web spec (no JS test infra — assert source contract)."""

    def test_vite_proxy_and_port(self) -> None:
        cfg = (Path(__file__).parents[1] / "web" / "vite.config.ts").read_text(encoding="utf-8")
        assert "port: 9333" in cfg
        assert "target: 'http://localhost:9555'" in cfg
        assert "'/api'" in cfg

    def test_api_base_env_override(self) -> None:
        src = (Path(__file__).parents[1] / "web" / "src" / "api" / "http.ts").read_text(encoding="utf-8")
        assert "import.meta.env.VITE_API_BASE || '/api'" in src
