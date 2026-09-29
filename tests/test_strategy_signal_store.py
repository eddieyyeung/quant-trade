"""Strategy signal persistence: the table, its writes, and its reads."""

from __future__ import annotations

import json
import tempfile
from datetime import date, datetime

import pytest

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.strategies.signal_store import (
    SIGNAL_COLUMNS,
    STRATEGY_SIGNAL_KIND,
    build_signal_frame,
    count_strategy_signals,
    get_strategy_signals,
    list_strategy_runs,
    save_strategy_signals,
)

SIGNAL_DAY = date(2026, 7, 24)


def _store(tmp: str) -> DataStore:
    db = f"{tmp}/a.db"
    init_db(db).close()
    return DataStore(db)


def _orders(count: int = 3) -> list[dict[str, object]]:
    return [
        {
            "ts_code": f"{index:06d}.SZ",
            "direction": "BUY" if index % 2 else "SELL",
            "target_pct": 0.05 * (index + 1),
            "reason": f"理由{index}",
        }
        for index in range(count)
    ]


def _write(store: DataStore, run_id: str, count: int = 3) -> int:
    frame = build_signal_frame(_orders(count), SIGNAL_DAY, "factor_ranking")
    return save_strategy_signals(store, run_id, frame)


def _add_run(store: DataStore, run_id: str, *, status: str = "ok", created: datetime | None = None) -> None:
    store.conn.execute(
        "INSERT INTO run (run_id, kind, params_json, status, progress, created_at, finished_at) "
        "VALUES (?, ?, '{}', ?, 1.0, ?, ?)",
        [run_id, STRATEGY_SIGNAL_KIND, status, created or datetime(2026, 7, 24, 16, 0), created],
    )


def _add_artifact(store: DataStore, run_id: str, meta: dict[str, object] | None) -> None:
    store.conn.execute(
        "INSERT INTO artifact (artifact_id, run_id, kind, storage, ref, row_count, meta_json) "
        "VALUES (?, ?, 'table', 'table', 'strategy_signal', 3, ?)",
        [f"{run_id}-a", run_id, json.dumps(meta) if meta is not None else None],
    )


class TestTable:
    def test_exists_after_init_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = f"{tmp}/a.db"
            init_db(db).close()
            init_db(db).close()
            store = DataStore(db)

            names = {row[0] for row in store.conn.execute("SHOW TABLES").fetchall()}
            assert "strategy_signal" in names

    def test_columns_match_the_constant(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            described = store.conn.execute("DESCRIBE strategy_signal").fetchall()
            assert [row[0] for row in described] == SIGNAL_COLUMNS

    def test_shows_up_in_table_stats(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            stats = store.table_stats("strategy_signal")
            assert stats.rows == 0
            assert stats.earliest is None and stats.latest is None

    def test_table_stats_reports_the_date_span(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _write(store, "r1")

            stats = store.table_stats("strategy_signal")
            assert stats.rows == 3
            assert stats.earliest == SIGNAL_DAY
            assert stats.latest == SIGNAL_DAY


class TestWrite:
    def test_writes_rows_and_reads_them_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            written = _write(store, "r1")

            assert written == 3
            frame = get_strategy_signals(store, "r1", limit=10, offset=0)
            assert len(frame) == 3
            assert frame["ts_code"].tolist() == ["000000.SZ", "000001.SZ", "000002.SZ"]
            assert frame["strategy"].unique().tolist() == ["factor_ranking"]
            # A DATE column comes back from DuckDB as a Timestamp, so compare
            # the calendar day rather than the type.
            assert {stamp.date() for stamp in frame["trade_date"]} == {SIGNAL_DAY}

    def test_seq_is_contiguous_from_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _write(store, "r1", count=5)

            frame = get_strategy_signals(store, "r1", limit=10, offset=0)
            assert frame["seq"].tolist() == [1, 2, 3, 4, 5]

    def test_order_is_preserved(self) -> None:
        """`seq` follows the engine's order, including a sell-first sequence."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            orders = [
                {"ts_code": "600000.SH", "direction": "SELL", "target_pct": 0.0, "reason": "先卖"},
                {"ts_code": "000001.SZ", "direction": "BUY", "target_pct": 0.1, "reason": "后买"},
            ]
            save_strategy_signals(store, "r1", build_signal_frame(orders, SIGNAL_DAY, "factor_ranking"))

            frame = get_strategy_signals(store, "r1", limit=10, offset=0)
            assert frame["direction"].tolist() == ["SELL", "BUY"]

    def test_rewrite_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _write(store, "r1", count=3)
            _write(store, "r1", count=2)

            assert count_strategy_signals(store, "r1") == 2

    def test_empty_orders_write_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            frame = build_signal_frame([], SIGNAL_DAY, "factor_ranking")

            assert frame.empty
            assert save_strategy_signals(store, "r1", frame) == 0
            assert count_strategy_signals(store, "r1") == 0

    def test_missing_column_is_rejected_by_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            frame = build_signal_frame(_orders(1), SIGNAL_DAY, "factor_ranking").drop(columns=["reason"])

            with pytest.raises(ValueError, match="reason"):
                save_strategy_signals(store, "r1", frame)

    def test_two_runs_do_not_collide(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _write(store, "r1", count=2)
            _write(store, "r2", count=5)

            assert count_strategy_signals(store, "r1") == 2
            assert count_strategy_signals(store, "r2") == 5


class TestRead:
    def test_pagination_walks_the_ordered_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _write(store, "r1", count=5)

            first = get_strategy_signals(store, "r1", limit=2, offset=0)
            second = get_strategy_signals(store, "r1", limit=2, offset=2)
            assert first["seq"].tolist() == [1, 2]
            assert second["seq"].tolist() == [3, 4]

    def test_unknown_run_reads_empty_with_the_right_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            frame = get_strategy_signals(store, "nope", limit=10, offset=0)

            assert frame.empty
            assert list(frame.columns) == SIGNAL_COLUMNS

    def test_count_of_unknown_run_is_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            assert count_strategy_signals(store, "nope") == 0


class TestRunList:
    def test_lists_runs_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "old", created=datetime(2026, 7, 22, 16, 0))
            _add_run(store, "new", created=datetime(2026, 7, 24, 16, 0))
            _write(store, "old")
            _write(store, "new")

            rows, total = list_strategy_runs(store, limit=10, offset=0)
            assert total == 2
            assert [row.run_id for row in rows] == ["new", "old"]

    def test_run_without_signals_yet_is_listed(self) -> None:
        """A queued or running run has not written rows; hiding it is a lie."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "running", status="running")

            rows, total = list_strategy_runs(store, limit=10, offset=0)
            assert total == 1
            assert rows[0].run_id == "running"
            assert rows[0].status == "running"
            # Absent, not zero: 0 would read as "this run produced no signals",
            # which is a different claim from "it has not finished".
            assert rows[0].signal_date is None
            assert rows[0].order_count is None
            assert rows[0].universe_size is None

    def test_derives_summary_from_the_signal_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "r1")
            _write(store, "r1", count=4)

            rows, _ = list_strategy_runs(store, limit=10, offset=0)
            assert rows[0].signal_date == SIGNAL_DAY
            assert rows[0].strategy_name == "factor_ranking"
            assert rows[0].order_count == 4

    def test_universe_size_comes_from_the_artifact(self) -> None:
        """The one field with no home in the rows."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "r1")
            _write(store, "r1")
            _add_artifact(store, "r1", {"strategy": "factor_ranking", "universe_size": 772})

            rows, _ = list_strategy_runs(store, limit=10, offset=0)
            assert rows[0].universe_size == 772

    def test_unparseable_meta_still_lists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "r1")
            _write(store, "r1")
            _add_artifact(store, "r1", None)
            store.conn.execute("UPDATE artifact SET meta_json = '{not json' WHERE run_id = 'r1'")

            rows, _ = list_strategy_runs(store, limit=10, offset=0)
            assert rows[0].universe_size is None
            assert rows[0].order_count == 3, "a bad meta must not cost the derived columns"

    def test_other_kinds_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            store.conn.execute(
                "INSERT INTO run (run_id, kind, params_json, status, progress, created_at) "
                "VALUES ('bt', 'backtest', '{}', 'ok', 1.0, TIMESTAMP '2026-07-24 16:00:00')"
            )
            _add_run(store, "r1")

            rows, total = list_strategy_runs(store, limit=10, offset=0)
            assert total == 1
            assert rows[0].run_id == "r1"

    def test_reading_never_creates_run_rows(self) -> None:
        """``run`` belongs to RunStore; this module must only read it."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "r1")
            _write(store, "r1")
            before = store.conn.execute("SELECT COUNT(*) FROM run").fetchone()
            assert before is not None

            list_strategy_runs(store, limit=10, offset=0)

            after = store.conn.execute("SELECT COUNT(*) FROM run").fetchone()
            assert after == before

    def test_pagination_returns_the_total(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            for index in range(3):
                _add_run(store, f"r{index}", created=datetime(2026, 7, 20 + index, 16, 0))

            rows, total = list_strategy_runs(store, limit=2, offset=0)
            assert len(rows) == 2
            assert total == 3
