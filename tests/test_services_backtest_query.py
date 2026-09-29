"""Tests for the backtest read services."""

import tempfile
from datetime import date, datetime

import pandas as pd
import pytest
from pydantic import ValidationError

from quant_trade.backtest.result_store import (
    build_metric_frame,
    build_nav_frame,
    build_position_frame,
    build_trade_frame,
    save_backtest_result,
)
from quant_trade.config import AppConfig, DataConfig
from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.services import RunContext
from quant_trade.services.backtest_query import (
    MAX_COMPARISON_RUNS,
    BacktestComparisonParams,
    BacktestDetailParams,
    BacktestRunListParams,
    BacktestTradeParams,
    backtest_comparison,
    backtest_detail,
    backtest_run_list,
    backtest_trades,
)


def _store(db_path: str) -> DataStore:
    init_db(db_path).close()
    return DataStore(db_path)


def _context(store: DataStore, db_path: str) -> RunContext:
    return RunContext(run_id="test", config=AppConfig(data=DataConfig(db_path=db_path)), store=store)


def _seed_run(
    store: DataStore,
    run_id: str,
    *,
    started_at: datetime,
    nav: list[float],
    status: str = "ok",
    strategy: str = "factor_ranking",
    trades: int = 3,
) -> None:
    """Write one backtest run the way the service would, through the store."""
    # Submission time is set alongside the start time: the list sorts by
    # submission, and the column default would flatten every seeded row onto
    # the same instant.
    store.conn.execute(
        "INSERT INTO run (run_id, kind, params_json, status, created_at, started_at) "
        "VALUES (?, 'backtest', ?, ?, ?, ?)",
        [run_id, f'{{"strategy": "{strategy}"}}', status, started_at, started_at],
    )
    days = [date(2024, 1, 2 + index) for index in range(len(nav))]
    series = pd.Series(nav, index=days, dtype=float)
    trade_rows = [
        {
            "date": days[min(index, len(days) - 1)].isoformat(),
            "action": "BUY" if index % 2 == 0 else "SELL",
            "ts_code": "600000.SH",
            "shares": 100,
            "price": 10.0,
            "commission": 5.0,
            "stamp_duty": 0.5,
            "transfer_fee": 0.01,
        }
        for index in range(trades)
    ]
    save_backtest_result(
        store,
        run_id,
        nav_frame=build_nav_frame(series),
        trade_frame=build_trade_frame(trade_rows),
        metric_frame=build_metric_frame(
            {"annual_return": 0.18, "sharpe_ratio": 1.2, "cash": 4321.0, "final_value": 1050.0}
        ),
        position_frame=build_position_frame(
            [
                {"ts_code": "600000.SH", "shares": 100, "avg_cost": 9.0, "current_price": 10.0, "market_value": 1000.0},
                {"ts_code": "000001.SZ", "shares": 50, "avg_cost": 20.0, "current_price": 20.0, "market_value": 1000.0},
            ]
        ),
    )


class TestRunList:
    def test_pages_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = tmp + "/a.db"
            store = _store(db)
            _seed_run(store, "older", started_at=datetime(2024, 5, 1), nav=[1.0, 1.05])
            _seed_run(store, "newer", started_at=datetime(2024, 6, 1), nav=[1.0, 1.10, 1.20])

            result = backtest_run_list(BacktestRunListParams(), _context(store, db))
            assert result.total == 2
            assert [run.run_id for run in result.runs] == ["newer", "older"]
            assert result.runs[0].start == date(2024, 1, 2)
            assert result.runs[0].end == date(2024, 1, 4)
            assert result.runs[0].nav_points == 3
            assert result.runs[0].strategy == "factor_ranking"
            assert result.runs[0].metrics["annual_return"] == pytest.approx(0.18)

    def test_page_window(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = tmp + "/a.db"
            store = _store(db)
            for index in range(3):
                _seed_run(store, f"r{index}", started_at=datetime(2024, 1, 1 + index), nav=[1.0, 1.05])

            result = backtest_run_list(BacktestRunListParams(limit=2, offset=2), _context(store, db))
            assert result.total == 3
            assert len(result.runs) == 1
            assert result.offset == 2
            assert result.limit == 2

    def test_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = tmp + "/a.db"
            store = _store(db)
            result = backtest_run_list(BacktestRunListParams(), _context(store, db))
            assert (result.total, result.runs) == (0, [])

    def test_paging_bounds_are_validated(self) -> None:
        with pytest.raises(ValidationError):
            BacktestRunListParams(limit=0)
        with pytest.raises(ValidationError):
            BacktestRunListParams(offset=-1)


class TestDetail:
    def test_assembles_series_metrics_and_positions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = tmp + "/a.db"
            store = _store(db)
            _seed_run(store, "r1", started_at=datetime(2024, 5, 1), nav=[1.0, 1.05, 1.02])

            detail = backtest_detail(BacktestDetailParams(run_id="r1"), _context(store, db))
            assert detail.found is True
            assert detail.status == "ok"
            assert detail.series is not None
            assert detail.series.dates == [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
            assert detail.series.nav == pytest.approx([1.0, 1.05, 1.02])
            assert detail.metrics["sharpe_ratio"] == pytest.approx(1.2)
            assert detail.cash == pytest.approx(4321.0)
            assert detail.total_value == pytest.approx(1050.0)
            # Sorted rather than compared in place: the two seeded holdings have
            # equal market value, so the query's own tiebreak decides the order
            # and this assertion should not re-impose a second one.
            assert sorted(row.ts_code for row in detail.positions) == ["000001.SZ", "600000.SH"]
            assert sum(row.weight for row in detail.positions) == pytest.approx(1.0)

    def test_unknown_run_is_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = tmp + "/a.db"
            store = _store(db)
            detail = backtest_detail(BacktestDetailParams(run_id="nope"), _context(store, db))
            assert detail.found is False
            assert detail.series is None
            assert detail.metrics == {}

    def test_other_run_kinds_are_not_backtests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = tmp + "/a.db"
            store = _store(db)
            store.conn.execute(
                "INSERT INTO run (run_id, kind, params_json, status) VALUES ('sync1', 'data_sync', '{}', 'ok')"
            )
            detail = backtest_detail(BacktestDetailParams(run_id="sync1"), _context(store, db))
            assert detail.found is False

    def test_run_with_no_results_still_reads_back(self) -> None:
        """A failed run exists; the detail page must say so rather than 404."""
        with tempfile.TemporaryDirectory() as tmp:
            db = tmp + "/a.db"
            store = _store(db)
            store.conn.execute(
                "INSERT INTO run (run_id, kind, params_json, status) VALUES ('r1', 'backtest', '{}', 'failed')"
            )
            detail = backtest_detail(BacktestDetailParams(run_id="r1"), _context(store, db))
            assert detail.found is True
            assert detail.status == "failed"
            assert detail.series is not None and detail.series.dates == []
            assert detail.positions == []


class TestTrades:
    def test_pages_trades(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = tmp + "/a.db"
            store = _store(db)
            _seed_run(store, "r1", started_at=datetime(2024, 5, 1), nav=[1.0, 1.05, 1.10], trades=4)

            page = backtest_trades(BacktestTradeParams(run_id="r1", limit=2, offset=1), _context(store, db))
            assert page.total == 4
            assert [row.seq for row in page.trades] == [2, 3]
            assert page.trades[0].trade_date == date(2024, 1, 3)

    def test_trade_page_bounds_are_validated(self) -> None:
        with pytest.raises(ValidationError):
            BacktestTradeParams(run_id="r1", limit=501)
        with pytest.raises(ValidationError):
            BacktestTradeParams(run_id="r1", offset=-1)


class TestComparison:
    def test_returns_curves_in_requested_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = tmp + "/a.db"
            store = _store(db)
            _seed_run(store, "a", started_at=datetime(2024, 5, 1), nav=[1.0, 1.05])
            _seed_run(store, "b", started_at=datetime(2024, 6, 1), nav=[1.0, 0.95, 0.90])

            result = backtest_comparison(BacktestComparisonParams(runs=["b", "a"]), _context(store, db))
            assert [entry.run_id for entry in result.runs] == ["b", "a"]
            assert result.missing == []
            assert result.runs[0].series is not None
            assert result.runs[0].series.nav == pytest.approx([1.0, 0.95, 0.90])

    def test_missing_runs_are_reported_not_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = tmp + "/a.db"
            store = _store(db)
            _seed_run(store, "a", started_at=datetime(2024, 5, 1), nav=[1.0, 1.05])

            result = backtest_comparison(BacktestComparisonParams(runs=["a", "ghost"]), _context(store, db))
            assert [entry.run_id for entry in result.runs] == ["a"]
            assert result.missing == ["ghost"]

    def test_cancelled_run_keeps_its_status_and_truncated_curve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = tmp + "/a.db"
            store = _store(db)
            _seed_run(store, "partial", started_at=datetime(2024, 5, 1), nav=[1.0, 1.05], status="cancelled")

            result = backtest_comparison(BacktestComparisonParams(runs=["partial"]), _context(store, db))
            assert result.runs[0].status == "cancelled"
            assert result.runs[0].series is not None
            assert len(result.runs[0].series.nav) == 2

    def test_run_count_is_capped(self) -> None:
        with pytest.raises(ValidationError):
            BacktestComparisonParams(runs=[f"r{index}" for index in range(MAX_COMPARISON_RUNS + 1)])

    def test_at_least_one_run(self) -> None:
        with pytest.raises(ValidationError):
            BacktestComparisonParams(runs=[])


class TestParamSerialization:
    def test_params_round_trip_through_json(self) -> None:
        for params in (
            BacktestRunListParams(limit=5, offset=10),
            BacktestDetailParams(run_id="r1"),
            BacktestTradeParams(run_id="r1", limit=50, offset=100),
            BacktestComparisonParams(runs=["a", "b"]),
        ):
            restored = type(params).model_validate_json(params.model_dump_json())
            assert restored == params


class _CountingConn:
    """Wraps a DuckDB connection to count round-trips."""

    def __init__(self, inner: object) -> None:
        self._inner = inner
        self.calls = 0

    def execute(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        return self._inner.execute(*args, **kwargs)  # type: ignore[attr-defined]

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class TestQueryCost:
    def test_comparison_cost_does_not_grow_with_the_run_count(self) -> None:
        """One page of comparison is a fixed number of queries, not N+1."""
        with tempfile.TemporaryDirectory() as tmp:
            db = tmp + "/a.db"
            store = _store(db)
            for index in range(3):
                _seed_run(store, f"r{index}", started_at=datetime(2024, 5, 1 + index), nav=[1.0, 1.05, 1.1])
            context = _context(store, db)

            counter = _CountingConn(store.conn)
            store._conn = counter  # type: ignore[assignment]

            backtest_comparison(BacktestComparisonParams(runs=["r0"]), context)
            one_run = counter.calls
            counter.calls = 0
            backtest_comparison(BacktestComparisonParams(runs=["r0", "r1", "r2"]), context)
            three_runs = counter.calls

            assert one_run > 0
            assert three_runs == one_run, f"1 run cost {one_run} queries, 3 runs cost {three_runs}"

    def test_detail_never_runs_the_engine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The read path must not re-run a backtest, however tempting."""
        from quant_trade.backtest import engine

        def _explode(*args: object, **kwargs: object) -> None:
            raise AssertionError("the read path re-ran the backtest")

        monkeypatch.setattr(engine, "run_backtest", _explode)

        with tempfile.TemporaryDirectory() as tmp:
            db = tmp + "/a.db"
            store = _store(db)
            _seed_run(store, "r1", started_at=datetime(2024, 5, 1), nav=[1.0, 1.05, 1.02])

            detail = backtest_detail(BacktestDetailParams(run_id="r1"), _context(store, db))
            assert detail.found is True
            assert detail.series is not None and detail.series.nav
