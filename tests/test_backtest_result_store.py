"""Tests for backtest result persistence and querying."""

import itertools
import tempfile
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
import pytest

from quant_trade.backtest.result_store import (
    build_metric_frame,
    build_nav_frame,
    build_position_frame,
    build_trade_frame,
    count_backtest_trades,
    get_backtest_metrics,
    get_backtest_nav,
    get_backtest_nav_many,
    get_backtest_positions,
    get_backtest_trades,
    list_backtest_runs,
    save_backtest_result,
)
from quant_trade.data.schema import init_db
from quant_trade.data.store import TABLE_NAMES, DataStore

BACKTEST_TABLES = ("backtest_metric", "backtest_nav", "backtest_position", "backtest_trade")


def _store(db_path: str) -> DataStore:
    init_db(db_path).close()
    return DataStore(db_path)


def _nav_series(values: list[float]) -> pd.Series:
    index = [date(2024, 1, day) for day in range(2, 2 + len(values))]
    return pd.Series(values, index=index, dtype=float)


def _trade(day: int, action: str = "BUY", code: str = "600000.SH") -> dict[str, Any]:
    return {
        "date": date(2024, 1, day).isoformat(),
        "action": action,
        "ts_code": code,
        "shares": 100,
        "price": 10.0,
        "commission": 5.0,
        "stamp_duty": 0.5 if action == "SELL" else 0.0,
        "transfer_fee": 0.01,
    }


def _save(store: DataStore, run_id: str, nav: pd.Series, benchmark: pd.Series | None = None) -> None:
    save_backtest_result(
        store,
        run_id,
        nav_frame=build_nav_frame(nav, benchmark),
        trade_frame=build_trade_frame([_trade(3), _trade(4, action="SELL")]),
        metric_frame=build_metric_frame({"annual_return": 0.21, "sharpe_ratio": 1.4, "total_trades": 2.0}),
        position_frame=build_position_frame(
            [{"ts_code": "600000.SH", "shares": 100, "avg_cost": 9.5, "current_price": 10.0, "market_value": 1000.0}]
        ),
    )


_SUBMISSIONS = itertools.count()


def _insert_run(store: DataStore, run_id: str, kind: str = "backtest", status: str = "ok") -> None:
    """One ``run`` row.

    ``created_at`` is stamped in call order: it is the list's sort key, and the
    column default would tie every row seeded within the same second.
    """
    submitted = datetime(2024, 1, 1) + timedelta(seconds=next(_SUBMISSIONS))
    store.conn.execute(
        "INSERT INTO run (run_id, kind, params_json, status, created_at) VALUES (?, ?, ?, ?, ?)",
        [run_id, kind, '{"strategy": "factor_ranking"}', status, submitted],
    )


class TestSchema:
    def test_tables_exist_after_init(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            names = {
                row[0] for row in store.conn.execute("SELECT table_name FROM information_schema.tables").fetchall()
            }
            missing = [table for table in BACKTEST_TABLES if table not in names]
            assert missing == [], f"init_db did not create {missing}"

    def test_init_is_idempotent(self) -> None:
        """Re-running the schema must not fail or drop what is already there."""
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp + "/a.db"
            store = _store(path)
            store.conn.execute(
                "INSERT INTO backtest_metric (run_id, metric_name, metric_value) VALUES ('r1', 'sharpe_ratio', 1.5)"
            )
            store.close()
            init_db(path).close()

            reopened = DataStore(path)
            rows = reopened.conn.execute("SELECT COUNT(*) FROM backtest_metric").fetchone()
            assert rows is not None and rows[0] == 1

    def test_tables_are_registered_for_data_status(self) -> None:
        assert set(BACKTEST_TABLES) <= TABLE_NAMES

    def test_empty_tables_report_zero_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            for table in BACKTEST_TABLES:
                stats = store.table_stats(table)
                assert stats.rows == 0, f"{table} should start empty"

    def test_metric_table_has_no_date_bounds(self) -> None:
        """It is a key/value table, so there is no date column to bound."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            store.conn.execute(
                "INSERT INTO backtest_metric (run_id, metric_name, metric_value) VALUES ('r1', 'annual_return', 0.2)"
            )
            stats = store.table_stats("backtest_metric")
            assert stats.rows == 1
            assert stats.earliest is None
            assert stats.latest is None

    def test_populated_tables_without_a_date_column_still_report(self) -> None:
        """A table with rows takes the bounds path, so a wrong date column raises.

        ``table_stats`` returns early on an empty table, which is why only a
        populated one catches a date column that does not exist.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            store.conn.execute(
                "INSERT INTO backtest_position (run_id, ts_code, shares, avg_cost, current_price, market_value) "
                "VALUES ('r1', '600000.SH', 100, 9.0, 10.0, 1000.0)"
            )
            stats = store.table_stats("backtest_position")
            assert stats.rows == 1
            assert stats.earliest is None
            assert stats.latest is None

    def test_all_table_stats_covers_every_registered_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            store.conn.execute(
                "INSERT INTO backtest_metric (run_id, metric_name, metric_value) VALUES ('r1', 'sharpe_ratio', 1.5)"
            )
            store.conn.execute(
                "INSERT INTO backtest_position (run_id, ts_code, shares, avg_cost, current_price, market_value) "
                "VALUES ('r1', '600000.SH', 100, 9.0, 10.0, 1000.0)"
            )
            stats = store.all_table_stats()
            assert set(BACKTEST_TABLES) <= set(stats)

    def test_unknown_table_is_still_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            with pytest.raises(ValueError, match="Unknown table"):
                store.table_stats("backtest_nope")


class TestDateBounds:
    def test_nav_bounds_come_from_trade_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            store.conn.execute(
                "INSERT INTO backtest_nav (run_id, trade_date, nav, benchmark, drawdown) VALUES "
                "('r1', DATE '2024-01-02', 1.0, 1.0, 0.0), "
                "('r1', DATE '2024-01-05', 1.1, 1.02, 0.0)"
            )
            stats = store.table_stats("backtest_nav")
            assert stats.rows == 2
            assert stats.earliest == date(2024, 1, 2)
            assert stats.latest == date(2024, 1, 5)


class TestSaveAndRead:
    def test_nav_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            _save(store, "r1", _nav_series([1.0, 1.05, 1.02]))

            out = get_backtest_nav(store, "r1")
            assert len(out) == 3
            assert out["nav"].tolist() == pytest.approx([1.0, 1.05, 1.02])
            # peak 1.05, so the third day is 1.02/1.05 - 1
            assert out["drawdown"].tolist() == pytest.approx([0.0, 0.0, -0.0285714], abs=1e-6)

    def test_trades_round_trip_with_parsed_dates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            _save(store, "r1", _nav_series([1.0, 1.05]))

            trades = get_backtest_trades(store, "r1", limit=10, offset=0)
            assert len(trades) == 2
            assert trades["seq"].tolist() == [1, 2]
            assert trades["action"].tolist() == ["BUY", "SELL"]
            assert count_backtest_trades(store, "r1") == 2

    def test_metrics_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            _save(store, "r1", _nav_series([1.0, 1.05]))
            assert get_backtest_metrics(store, "r1") == {
                "annual_return": 0.21,
                "sharpe_ratio": 1.4,
                "total_trades": 2.0,
            }

    def test_positions_have_a_stable_order(self) -> None:
        """Equal market values must not let the table reshuffle between reads."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            save_backtest_result(
                store,
                "r1",
                nav_frame=build_nav_frame(_nav_series([1.0, 1.05])),
                trade_frame=build_trade_frame([]),
                metric_frame=build_metric_frame({}),
                position_frame=build_position_frame(
                    [
                        {"ts_code": code, "shares": 100, "avg_cost": 1.0, "current_price": 10.0, "market_value": 1000.0}
                        for code in ("600000.SH", "000001.SZ", "300001.SZ")
                    ]
                ),
            )

            first = get_backtest_positions(store, "r1")["ts_code"].tolist()
            second = get_backtest_positions(store, "r1")["ts_code"].tolist()
            assert first == second
            assert first == sorted(first), "ties fall back to the code"

    def test_positions_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            _save(store, "r1", _nav_series([1.0, 1.05]))

            positions = get_backtest_positions(store, "r1")
            assert len(positions) == 1
            assert positions["ts_code"].tolist() == ["600000.SH"]
            assert positions["market_value"].tolist() == pytest.approx([1000.0])

    def test_rewriting_the_same_run_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            _save(store, "r1", _nav_series([1.0, 1.05, 1.10]))
            # Different values, so "replaces" is distinguishable from "ignores
            # the second write" — identical frames would pass either way.
            _save(store, "r1", _nav_series([1.0, 2.00, 3.00]))

            assert len(get_backtest_nav(store, "r1")) == 3
            assert get_backtest_nav(store, "r1")["nav"].tolist() == pytest.approx([1.0, 2.0, 3.0])
            assert count_backtest_trades(store, "r1") == 2
            assert len(get_backtest_positions(store, "r1")) == 1

    def test_two_runs_do_not_overwrite_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            _save(store, "r1", _nav_series([1.0, 1.05]))
            _save(store, "r2", _nav_series([1.0, 0.90]))

            assert get_backtest_nav(store, "r1")["nav"].tolist() == pytest.approx([1.0, 1.05])
            assert get_backtest_nav(store, "r2")["nav"].tolist() == pytest.approx([1.0, 0.90])

    def test_many_runs_read_in_one_query(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            _save(store, "r1", _nav_series([1.0, 1.05]))
            _save(store, "r2", _nav_series([1.0, 0.95]))

            frame = get_backtest_nav_many(store, ["r1", "r2"])
            assert sorted(frame["run_id"].unique().tolist()) == ["r1", "r2"]
            assert len(frame) == 4

    def test_trades_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            _save(store, "r1", _nav_series([1.0, 1.05]))

            second = get_backtest_trades(store, "r1", limit=1, offset=1)
            assert second["seq"].tolist() == [2]

    def test_unknown_run_reads_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            assert get_backtest_nav(store, "nope").empty
            assert get_backtest_metrics(store, "nope") == {}
            assert get_backtest_trades(store, "nope", limit=10, offset=0).empty
            assert get_backtest_positions(store, "nope").empty


class TestFrameBuilding:
    def test_benchmark_gap_becomes_null_not_a_dropped_row(self) -> None:
        """Index data shorter than the calendar must not delete NAV rows."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            nav = _nav_series([1.0, 1.05, 1.10])
            benchmark = pd.Series([1.0, 1.02], index=[date(2024, 1, 2), date(2024, 1, 3)], dtype=float)
            save_backtest_result(
                store,
                "r1",
                nav_frame=build_nav_frame(nav, benchmark),
                trade_frame=build_trade_frame([]),
                metric_frame=build_metric_frame({}),
                position_frame=build_position_frame([]),
            )

            out = get_backtest_nav(store, "r1")
            assert len(out) == 3, "the third NAV row must survive a missing benchmark day"
            assert out["nav"].tolist() == pytest.approx([1.0, 1.05, 1.10])
            assert out["benchmark"].tolist()[:2] == pytest.approx([1.0, 1.02])
            assert pd.isna(out["benchmark"].tolist()[2])

    def test_a_frame_missing_a_column_is_named(self) -> None:
        """Positional insert would raise a bare KeyError deep in the select."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            incomplete = build_nav_frame(_nav_series([1.0, 1.05])).drop(columns=["drawdown"])
            with pytest.raises(ValueError, match="missing columns"):
                save_backtest_result(
                    store,
                    "r1",
                    nav_frame=incomplete,
                    trade_frame=build_trade_frame([]),
                    metric_frame=build_metric_frame({}),
                    position_frame=build_position_frame([]),
                )

    def test_empty_inputs_write_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            rows = save_backtest_result(
                store,
                "r1",
                nav_frame=build_nav_frame(pd.Series(dtype=float)),
                trade_frame=build_trade_frame([]),
                metric_frame=build_metric_frame({}),
                position_frame=build_position_frame([]),
            )
            assert rows.total == 0
            assert get_backtest_nav(store, "r1").empty

    def test_unparseable_trade_date_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            trades = [_trade(3), {**_trade(4), "date": "not-a-date"}, _trade(5)]
            save_backtest_result(
                store,
                "r1",
                nav_frame=build_nav_frame(_nav_series([1.0, 1.05])),
                trade_frame=build_trade_frame(trades),
                metric_frame=build_metric_frame({}),
                position_frame=build_position_frame([]),
            )

            out = get_backtest_trades(store, "r1", limit=10, offset=0)
            assert len(out) == 2, "only the bad row should be dropped"
            assert count_backtest_trades(store, "r1") == 2
            # seq describes the stored trades, not the engine's list: a gap
            # would break "seq is contiguous from 1" and the pagination keys
            # derived from it.
            assert out["seq"].tolist() == [1, 2]


class TestRunList:
    def test_lists_backtest_runs_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            _insert_run(store, "r1")
            _insert_run(store, "r2")
            _save(store, "r1", _nav_series([1.0, 1.05, 1.10]))
            _save(store, "r2", _nav_series([1.0, 0.95]))

            rows, total = list_backtest_runs(store, limit=10, offset=0)
            assert total == 2
            assert {row.run_id for row in rows} == {"r1", "r2"}
            by_id = {row.run_id: row for row in rows}
            assert by_id["r1"].start == date(2024, 1, 2)
            assert by_id["r1"].end == date(2024, 1, 4)
            assert by_id["r1"].nav_points == 3
            assert by_id["r1"].strategy == "factor_ranking"
            assert by_id["r1"].status == "ok"

    def test_only_the_list_metrics_are_returned(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            _insert_run(store, "r1")
            _save(store, "r1", _nav_series([1.0, 1.05]))

            rows, _ = list_backtest_runs(store, limit=10, offset=0)
            assert set(rows[0].metrics) == {"annual_return", "sharpe_ratio", "total_trades"}

    def test_other_kinds_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            _insert_run(store, "r1")
            _insert_run(store, "sync", kind="data_sync")
            _save(store, "r1", _nav_series([1.0, 1.05]))

            rows, total = list_backtest_runs(store, limit=10, offset=0)
            assert total == 1
            assert [row.run_id for row in rows] == ["r1"]

    def test_run_without_nav_still_appears(self) -> None:
        """A failed or empty backtest was still submitted; hiding it would lie."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            _insert_run(store, "r1", status="failed")

            rows, total = list_backtest_runs(store, limit=10, offset=0)
            assert total == 1
            assert rows[0].status == "failed"
            assert rows[0].start is None
            assert rows[0].nav_points == 0

    def test_ordering_follows_submission_not_start(self) -> None:
        """The list shows 提交时间, so that is the column the order must match."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            # submitted first, started last — the two keys disagree on purpose
            _insert_run(store, "submitted-first")
            _insert_run(store, "submitted-second")
            store.conn.execute(
                "UPDATE run SET created_at = TIMESTAMP '2024-05-01 09:00:00' WHERE run_id = ?", ["submitted-first"]
            )
            store.conn.execute(
                "UPDATE run SET created_at = TIMESTAMP '2024-05-02 09:00:00' WHERE run_id = ?", ["submitted-second"]
            )
            store.conn.execute(
                "UPDATE run SET started_at = TIMESTAMP '2024-05-09 09:00:00' WHERE run_id = ?", ["submitted-first"]
            )
            store.conn.execute(
                "UPDATE run SET started_at = TIMESTAMP '2024-05-03 09:00:00' WHERE run_id = ?", ["submitted-second"]
            )

            rows, _ = list_backtest_runs(store, limit=10, offset=0)
            assert [row.run_id for row in rows] == ["submitted-second", "submitted-first"]

    def test_progress_is_carried_so_a_running_row_can_show_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            _insert_run(store, "r1", status="running")
            store.conn.execute("UPDATE run SET progress = 0.42 WHERE run_id = ?", ["r1"])

            rows, _ = list_backtest_runs(store, limit=10, offset=0)
            assert rows[0].progress == pytest.approx(0.42)
            assert rows[0].status == "running"

    def test_paging(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            for index in range(3):
                _insert_run(store, f"r{index}")
                _save(store, f"r{index}", _nav_series([1.0, 1.05]))

            page, total = list_backtest_runs(store, limit=2, offset=0)
            assert total == 3
            assert len(page) == 2

    def test_listing_writes_no_run_rows(self) -> None:
        """``result_store`` reads ``run`` for status; ``RunStore`` owns those rows."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            _insert_run(store, "r1")
            _save(store, "r1", _nav_series([1.0, 1.05]))
            before = store.conn.execute("SELECT COUNT(*) FROM run").fetchone()

            list_backtest_runs(store, limit=10, offset=0)
            get_backtest_nav(store, "r1")
            get_backtest_metrics(store, "r1")

            after = store.conn.execute("SELECT COUNT(*) FROM run").fetchone()
            assert before == after
