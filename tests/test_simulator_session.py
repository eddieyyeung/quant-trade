"""Test session persistence — DuckDB CRUD + JSON decision log."""

import json
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path

from quant_trade.data.schema import init_db
from quant_trade.simulator.session import SessionStore
from quant_trade.simulator.types import Decision, ExecutedOrder, OrderRequest


def _make_store(tmp: str) -> SessionStore:
    conn = init_db(tmp + "/test.db")
    return SessionStore(conn, data_dir=tmp)


def _make_decision(num: int = 1) -> Decision:
    return Decision(
        decision_number=num,
        cursor_date=date(2024, 3, 15),
        exec_date=date(2024, 3, 18),
        user_orders=[OrderRequest(ts_code="600519.SH", target_pct=0.15, direction="BUY")],
        executed_orders=[
            ExecutedOrder(
                ts_code="600519.SH",
                direction="BUY",
                target_pct=0.15,
                shares=100,
                price=1720.0,
                cost_or_proceeds=172000.0,
                reason="测试买入",
            )
        ],
        notes="测试备注",
        snapshot_before=None,
        strategy_orders=None,
        timestamp=datetime.now(UTC).isoformat(),
    )


class TestSessionStore:
    def test_create_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            sid = store.create(
                name="测试会话",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
                initial_capital=100000,
                cursor_date=date(2024, 1, 5),
                portfolio_json='{"cash": 100000, "holdings": {}}',
            )
            assert sid

            row = store.load(sid)
            assert row is not None
            assert row["name"] == "测试会话"
            assert row["status"] == "active"

    def test_save_updates_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            sid = store.create(
                name="更新测试",
                start_date=date(2024, 1, 1),
                end_date=date(2024, 12, 31),
                initial_capital=100000,
                cursor_date=date(2024, 1, 5),
                portfolio_json='{"cash": 100000}',
            )
            new_portfolio = '{"cash": 90000, "holdings": {"600519.SH": {"shares": 100}}}'
            store.save(sid, cursor_date=date(2024, 3, 15), portfolio_json=new_portfolio, status="paused")

            row = store.load(sid)
            assert row is not None
            assert row["status"] == "paused"
            assert "600519.SH" in row["portfolio_json"]

    def test_list_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            store.create("A", date(2024, 1, 1), date(2024, 6, 30), 100000, date(2024, 1, 5), "{}")
            store.create("B", date(2023, 1, 1), date(2023, 12, 31), 50000, date(2023, 1, 6), "{}")
            store.create("C", date(2024, 6, 1), date(2024, 12, 31), 200000, date(2024, 6, 7), "{}")

            all_sessions = store.list_sessions()
            assert len(all_sessions) == 3

            # Pause middle session and filter
            row = store.load(all_sessions[1]["id"])
            if row:
                store.save(all_sessions[1]["id"], row["cursor_date"], row["portfolio_json"], status="paused")

            paused = store.list_sessions(status="paused")
            assert len(paused) == 1

    def test_delete_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            sid = store.create("删除测试", date(2024, 1, 1), date(2024, 12, 31), 100000, date(2024, 1, 5), "{}")
            assert store.load(sid) is not None

            store.delete(sid)
            assert store.load(sid) is None

    def test_append_and_load_decisions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            sid = store.create("决策测试", date(2024, 1, 1), date(2024, 12, 31), 100000, date(2024, 1, 5), "{}")

            d1 = _make_decision(1)
            d2 = _make_decision(2)

            store.append_decision(sid, d1)
            store.append_decision(sid, d2)

            loaded = store.load_decisions(sid)
            assert len(loaded) == 2
            assert loaded[0].decision_number == 1
            assert loaded[1].decision_number == 2

    def test_decision_json_is_valid_array(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            sid = store.create("JSON测试", date(2024, 1, 1), date(2024, 12, 31), 100000, date(2024, 1, 5), "{}")

            d = _make_decision()
            store.append_decision(sid, d)

            path = Path(tmp) / "simulator" / sid / "decisions.json"
            assert path.exists()
            content = json.loads(path.read_text(encoding="utf-8"))
            assert isinstance(content, list)
            assert len(content) == 1

    def test_load_nonexistent_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _make_store(tmp)
            assert store.load("nonexistent-id") is None
            assert store.load_decisions("nonexistent-id") == []
