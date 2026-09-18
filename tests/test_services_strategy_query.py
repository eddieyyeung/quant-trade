"""Strategy read services: signal-run history and one run's signals."""

from __future__ import annotations

import json
import tempfile
from datetime import date, datetime

import pytest

from quant_trade.config import AppConfig
from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.services import RunContext
from quant_trade.services.strategy_query import (
    StrategyRunListParams,
    StrategySignalQueryParams,
    strategy_run_list,
    strategy_signals,
)
from quant_trade.strategies.signal_store import (
    STRATEGY_SIGNAL_KIND,
    build_signal_frame,
    save_strategy_signals,
)

SIGNAL_DAY = date(2026, 7, 24)
OTHER_DAY = date(2026, 7, 17)


def _store(tmp: str) -> DataStore:
    db = f"{tmp}/a.db"
    init_db(db).close()
    return DataStore(db)


def _ctx(store: DataStore) -> RunContext:
    config = AppConfig()
    config.strategy.name = "factor_ranking"
    return RunContext(run_id="t", config=config, store=store)


def _add_run(
    store: DataStore,
    run_id: str,
    *,
    status: str = "ok",
    created: datetime | None = None,
    finished: datetime | None = None,
) -> None:
    store.conn.execute(
        "INSERT INTO run (run_id, kind, params_json, status, progress, created_at, finished_at) "
        "VALUES (?, ?, '{}', ?, 1.0, ?, ?)",
        [run_id, STRATEGY_SIGNAL_KIND, status, created or datetime(2026, 7, 24, 16, 0), finished],
    )


def _add_signals(
    store: DataStore,
    run_id: str,
    *,
    count: int = 3,
    signal_date: date = SIGNAL_DAY,
    strategy: str = "factor_ranking",
) -> None:
    orders = [
        {"ts_code": f"{index:06d}.SZ", "direction": "BUY", "target_pct": 0.1, "reason": f"理由{index}"}
        for index in range(count)
    ]
    save_strategy_signals(store, run_id, build_signal_frame(orders, signal_date, strategy))


def _add_artifact(store: DataStore, run_id: str, **meta: object) -> None:
    store.conn.execute(
        "INSERT INTO artifact (artifact_id, run_id, kind, storage, ref, row_count, meta_json) "
        "VALUES (?, ?, 'table', 'table', 'strategy_signal', 3, ?)",
        [f"{run_id}-a", run_id, json.dumps(meta)],
    )


class TestRunList:
    def test_lists_newest_first_and_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            for index, day in enumerate((20, 24, 22)):
                _add_run(store, f"r{index}", created=datetime(2026, 7, day, 16, 0))
                _add_signals(store, f"r{index}")

            ctx = _ctx(store)
            page = strategy_run_list(StrategyRunListParams(limit=2, offset=0), ctx)
            assert page.total == 3
            assert [run.run_id for run in page.runs] == ["r1", "r2"]

    def test_summary_is_derived_from_the_signal_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "r1")
            _add_signals(store, "r1", count=4, signal_date=OTHER_DAY, strategy="model_ranking")

            run = strategy_run_list(StrategyRunListParams(), _ctx(store)).runs[0]
            assert run.signal_date == OTHER_DAY
            assert run.strategy == "model_ranking"
            assert run.order_count == 4

    def test_universe_size_comes_from_the_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "r1")
            _add_signals(store, "r1")
            _add_artifact(store, "r1", strategy="factor_ranking", universe_size=772)

            run = strategy_run_list(StrategyRunListParams(), _ctx(store)).runs[0]
            assert run.universe_size == 772

    def test_run_without_signals_yet_is_listed_with_nothing(self) -> None:
        """Absent, not zero — 0 would claim the run produced no signals."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "running", status="running")

            run = strategy_run_list(StrategyRunListParams(), _ctx(store)).runs[0]
            assert run.status == "running"
            assert run.signal_date is None
            assert run.order_count is None
            assert run.universe_size is None


class TestSignals:
    def test_reads_one_page_in_engine_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "r1")
            _add_signals(store, "r1", count=5)

            result = strategy_signals(StrategySignalQueryParams(run_id="r1", limit=3, offset=0), _ctx(store))

            assert result.found
            assert result.total == 5
            assert [order.seq for order in result.orders] == [1, 2, 3]
            assert result.orders[0].ts_code == "000000.SZ"

    def test_carries_the_run_level_facts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "r1")
            _add_signals(store, "r1", signal_date=OTHER_DAY, strategy="model_ranking")
            _add_artifact(store, "r1", universe_size=300)

            result = strategy_signals(StrategySignalQueryParams(run_id="r1"), _ctx(store))
            assert result.strategy == "model_ranking"
            assert result.signal_date == OTHER_DAY
            assert result.universe_size == 300

    def test_unknown_run_is_not_found(self) -> None:
        """ "No such run" is not the same answer as "a run with no signals"."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            result = strategy_signals(StrategySignalQueryParams(run_id="nope"), _ctx(store))

            assert result.found is False
            assert result.orders == []

    def test_a_finished_run_with_no_orders_is_found_and_empty(self) -> None:
        """It exists and it ran; the answer is "no signals", not "no such run"."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "empty")

            result = strategy_signals(StrategySignalQueryParams(run_id="empty"), _ctx(store))

            assert result.found is True
            assert result.total == 0
            assert result.orders == []
            assert result.status == "ok"

    def test_an_unfinished_run_with_no_orders_is_not_found(self) -> None:
        """Nothing to show yet, and an empty list would claim it selected nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "running", status="running")

            assert strategy_signals(StrategySignalQueryParams(run_id="running"), _ctx(store)).found is False

    def test_offset_walks_the_page(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp)
            _add_run(store, "r1")
            _add_signals(store, "r1", count=4)

            result = strategy_signals(StrategySignalQueryParams(run_id="r1", limit=2, offset=2), _ctx(store))
            assert [order.seq for order in result.orders] == [3, 4]


class TestParams:
    def test_round_trip_through_json(self) -> None:
        params = StrategyRunListParams(limit=50, offset=10)
        assert StrategyRunListParams.model_validate_json(params.model_dump_json()) == params

        query = StrategySignalQueryParams(run_id="r1", limit=10, offset=5)
        assert StrategySignalQueryParams.model_validate_json(query.model_dump_json()) == query

    def test_bounds_are_enforced(self) -> None:
        with pytest.raises(ValueError):
            StrategyRunListParams(limit=0)
        with pytest.raises(ValueError):
            StrategySignalQueryParams(run_id="")
        with pytest.raises(ValueError):
            StrategySignalQueryParams(run_id="r1", offset=-1)
