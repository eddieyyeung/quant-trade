"""Service chain: alpha158 -> train -> predict -> backtest.

Replaces the old CLI chain test. Assertions now read the returned objects
rather than scraping stdout, so the chain is checked on values rather than on
formatted text.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant_trade.config import AppConfig, DataConfig
from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.models.persistence import get_model_ic_series, get_model_importance, get_model_metrics
from quant_trade.services import CancelToken, RunContext
from quant_trade.services.backtest import BacktestParams, run_backtest_service
from quant_trade.services.factors import Alpha158Params, compute_alpha158
from quant_trade.services.models import (
    LightGBMParams,
    PredictParams,
    TrainParams,
    TrainResult,
    _train_config,
    predict_for_date,
    train_model,
)

CODES = ["000001.SZ", "600000.SH", "300001.SZ", "600519.SH", "601318.SH"]
N_DAYS = 500  # ~2 years of trading days: enough for a 1-year validation window

WIDE_CODES = [f"{600000 + index}.SH" for index in range(12)]
"""Enough names per date for RankIC to exist: the ranker skips a date with
fewer than ten merged observations, so the five-name universe above produces an
empty IC series and cannot exercise the persistence path."""

MODEL_TABLES = ("model_feature_importance", "model_ic_series", "model_metric")


def _build_store(db_path: str, codes: list[str] | None = None) -> DataStore:
    codes = codes if codes is not None else CODES
    conn = init_db(db_path)
    days: list[date] = []
    d = date(2024, 1, 1)
    while len(days) < N_DAYS:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    conn.executemany("INSERT INTO trade_calendar VALUES (?, TRUE)", [(x,) for x in days])

    conn.executemany(
        "INSERT INTO stock_basic (ts_code, name, industry, market, list_date) VALUES (?, ?, ?, ?, ?)",
        [(c, f"stock{c}", f"ind{i % 3}", "main", date(2020, 1, 1)) for i, c in enumerate(codes)],
    )
    conn.executemany(
        "INSERT INTO index_weights (index_code, ts_code, weight, in_date, out_date) VALUES (?, ?, ?, ?, ?)",
        [("000300.SH", c, 0.2, date(2020, 1, 1), date(2030, 1, 1)) for c in codes],
    )

    rows = []
    for c in codes:
        rng = np.random.default_rng(hash(c) % 2**32)
        close = 10.0
        for day in days:
            close *= 1 + rng.normal(0, 0.01)
            rows.append((c, day, close * 0.99, close * 1.01, close * 0.98, close, 1e6, 1e7, None, None))
    # Benchmark kline: 000300.SH is a benchmark, not a member of the universe.
    bench = 1.0
    for day in days:
        bench *= 1 + 0.0002
        rows.append(("000300.SH", day, bench, bench, bench, bench, 1e9, 1e10, None, None))
    conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    return DataStore(db_path)


def test_service_chain_end_to_end() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "chain.db")
        store = _build_store(db)
        config = AppConfig(data=DataConfig(db_path=db))
        ctx = RunContext(run_id="chain", config=config, store=store)

        alpha = compute_alpha158(Alpha158Params(start_date=date(2024, 1, 1)), ctx)
        assert alpha.factor_count == 158
        assert alpha.rows_saved > 0

        pred_path = str(Path(tmp) / "model_ranking.parquet")
        trained = train_model(TrainParams(start=date(2024, 2, 1), end=date(2025, 6, 30), output_path=pred_path), ctx)
        assert trained.prediction_rows > 0
        assert trained.windows_trained > 0
        assert Path(pred_path).is_file()

        # Feature importance reaches the caller and is ranked, which the old
        # tuple return made impossible — the matrix was discarded entirely.
        assert trained.feature_importance, "training must report feature importance"
        importances = [f.importance for f in trained.feature_importance]
        assert importances == sorted(importances, reverse=True)
        assert all(f.factor for f in trained.feature_importance)

        preds = pd.read_parquet(pred_path)
        assert set(preds.columns) == {"ts_code", "trade_date", "score"}
        last_day = preds["trade_date"].max()
        if hasattr(last_day, "date"):
            last_day = last_day.date()

        picked = predict_for_date(PredictParams(as_of=last_day, predictions_path=pred_path, top_n=5), ctx)
        assert picked.picks, "predict must return top picks"
        scores = [p.score for p in picked.picks]
        assert scores == sorted(scores, reverse=True)

        config.strategy.name = "model_ranking"
        config.strategy.params = {"predictions_path": pred_path}
        config.strategy.top_n = 5
        backtest = run_backtest_service(BacktestParams(start=date(2024, 6, 1), end=last_day), ctx)

        assert backtest.metrics, "backtest must produce metrics"
        assert backtest.nav, "backtest must produce a NAV series"
        assert backtest.nav[0].date <= last_day
        assert backtest.strategy == "model_ranking"


def test_predict_without_predictions_reports_through_context() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "nopred.db")
        store = _build_store(db)
        config = AppConfig(data=DataConfig(db_path=db))
        logs: list[str] = []
        ctx = RunContext(run_id="nopred", config=config, store=store, log_sink=lambda m, _l: logs.append(m))

        result = predict_for_date(PredictParams(predictions_path=str(Path(tmp) / "nope.parquet")), ctx)

        assert result.picks == []
        assert result.total_scored == 0
        assert any("train a model first" in m for m in logs)


def test_backtest_honours_cancellation() -> None:
    """Cancelling mid-run stops the week loop and says so in the result."""
    from quant_trade.services import CancelToken

    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "cancel.db")
        store = _build_store(db)
        config = AppConfig(data=DataConfig(db_path=db))

        token = CancelToken()
        token.cancel()
        logs: list[str] = []
        ctx = RunContext(
            run_id="cancel",
            config=config,
            store=store,
            cancel_token=token,
            log_sink=lambda m, _l: logs.append(m),
        )

        result = run_backtest_service(BacktestParams(start=date(2024, 6, 1)), ctx)

        assert result.cancelled is True
        assert any("cancelled" in m for m in logs)
        # No week was executed, so no trades were placed...
        assert result.trades == []
        # ...and the NAV series must stop where the work stopped rather than
        # being extended to the end of the requested window.
        assert result.nav == []
        # No NAV means no performance metrics; total_trades is always set.
        assert "total_return" not in result.metrics
        assert result.metrics["total_trades"] == 0.0


def test_backtest_nav_stops_at_the_cancellation_point() -> None:
    """Cancelling mid-run truncates NAV at the last completed week."""
    from quant_trade.services import CancelToken

    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "midcancel.db")
        store = _build_store(db)
        config = AppConfig(data=DataConfig(db_path=db))

        token = CancelToken()
        weeks_seen: list[float] = []

        def _cancel_after_two(pct: float, _msg: str) -> None:
            weeks_seen.append(pct)
            if len(weeks_seen) >= 2:
                token.cancel()

        ctx = RunContext(run_id="mid", config=config, store=store, cancel_token=token, progress_sink=_cancel_after_two)
        result = run_backtest_service(BacktestParams(start=date(2024, 6, 1)), ctx)

        assert result.cancelled is True
        assert result.nav, "weeks ran before the cancel, so some NAV must exist"
        assert result.nav[-1].date < result.end, "NAV must not reach the requested end"


def test_backtest_rejects_unknown_strategy() -> None:
    import pytest

    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "bad.db")
        store = _build_store(db)
        config = AppConfig(data=DataConfig(db_path=db))
        ctx = RunContext(run_id="bad", config=config, store=store)

        with pytest.raises(ValueError, match="not registered"):
            run_backtest_service(BacktestParams(strategy="does_not_exist"), ctx)


class TestBacktestPersistence:
    """A backtest reaches the database only when the context names a run."""

    def test_result_is_persisted_under_the_context_run_id(self) -> None:
        from quant_trade.backtest.result_store import (
            get_backtest_metrics,
            get_backtest_nav,
        )

        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "persist.db")
            store = _build_store(db)
            config = AppConfig(data=DataConfig(db_path=db))
            ctx = RunContext(run_id="run-abc", config=config, store=store)

            result = run_backtest_service(BacktestParams(start=date(2024, 6, 1)), ctx)

            assert result.rows_saved.nav == len(result.nav)
            assert result.rows_saved.nav > 0
            assert len(get_backtest_nav(store, "run-abc")) == len(result.nav)

            persisted = get_backtest_metrics(store, "run-abc")
            assert {name: persisted[name] for name in result.metrics} == result.metrics
            # The closing portfolio state is stored alongside the metrics.
            assert persisted["cash"] == result.cash
            assert persisted["final_value"] == result.total_value

    def test_null_context_persists_nothing(self) -> None:
        from quant_trade.backtest.result_store import list_backtest_runs

        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "nolake.db")
            store = _build_store(db)
            config = AppConfig(data=DataConfig(db_path=db))
            ctx = RunContext(config=config, store=store)  # run_id defaults to ""

            first = run_backtest_service(BacktestParams(start=date(2024, 6, 1)), ctx)
            second = run_backtest_service(BacktestParams(start=date(2024, 7, 1)), ctx)

            assert first.nav and second.nav, "the runs themselves still work"
            assert first.rows_saved.total == 0
            assert second.rows_saved.total == 0
            rows, total = list_backtest_runs(store, limit=10, offset=0)
            assert (rows, total) == ([], 0)
            # Two script runs writing to the same empty key would have had the
            # second silently replace the first; nothing was written at all.
            # Counted per table, not via rows_saved: the point is the state of
            # the database, not the service's own bookkeeping.
            for table in ("backtest_nav", "backtest_trade", "backtest_metric", "backtest_position"):
                count = store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                assert count is not None and count[0] == 0, f"{table} should be untouched"

    def test_empty_backtest_writes_nothing_and_registers_no_artifact(self) -> None:
        """The real empty path, not a hand-built empty frame.

        A window with no trading days returns the engine's ``_empty_result``:
        no NAV, no trades, no metrics — but a real portfolio holding the initial
        cash. Recording that cash is what made this path write metric rows and
        claim an artifact for a run that ran nothing.
        """
        from quant_trade.backtest.result_store import list_backtest_runs
        from quant_trade.jobs.registry import get_job

        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "emptywindow.db")
            store = _build_store(db)
            config = AppConfig(data=DataConfig(db_path=db))
            ctx = RunContext(run_id="run-empty", config=config, store=store)

            result = run_backtest_service(BacktestParams(start=date(2030, 1, 1), end=date(2030, 12, 31)), ctx)

            assert result.nav == []
            assert result.trades == []
            assert result.rows_saved.total == 0
            for table in ("backtest_nav", "backtest_trade", "backtest_metric", "backtest_position"):
                count = store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                assert count is not None and count[0] == 0, f"{table} should have no rows for an empty run"

            # The list still shows the run — it was submitted — but with no curve.
            # The `run` row belongs to RunStore, so it is seeded the way the
            # worker would have left it.
            store.conn.execute(
                "INSERT INTO run (run_id, kind, params_json, status) VALUES ('run-empty', 'backtest', '{}', 'ok')"
            )
            rows, total = list_backtest_runs(store, limit=10, offset=0)
            assert total == 1
            assert rows[0].nav_points == 0
            assert rows[0].metrics == {}

            spec = get_job("backtest")
            assert spec is not None and spec.artifacts is not None
            assert spec.artifacts(result) == [], "a run with no results must not claim artifacts"

    def test_cancelled_before_the_first_week_writes_nothing(self) -> None:
        """`total_trades` is always set, so "metrics is non-empty" is not the test.

        The engine reports `{"total_trades": 0.0}` for a run that was stopped
        before week one. Treating that as a result writes a portfolio snapshot
        for a run with no curve and hands it a metric-table artifact.
        """
        from quant_trade.services import CancelToken

        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "cancel-first.db")
            store = _build_store(db)
            config = AppConfig(data=DataConfig(db_path=db))
            token = CancelToken()
            token.cancel()
            ctx = RunContext(run_id="run-cancel-first", config=config, store=store, cancel_token=token)

            result = run_backtest_service(BacktestParams(start=date(2024, 6, 1)), ctx)

            assert result.cancelled is True
            assert result.nav == []
            assert result.metrics == {"total_trades": 0.0}, "the engine still reports this"
            assert result.rows_saved.total == 0
            for table in ("backtest_nav", "backtest_trade", "backtest_metric", "backtest_position"):
                count = store.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
                assert count is not None and count[0] == 0, f"{table} should be untouched"

    def test_empty_universe_writes_nothing(self) -> None:
        """A calendar with no prices leaves the engine with nothing to trade."""
        from quant_trade.backtest.result_store import list_backtest_runs

        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "nouniverse.db")
            store = _build_store(db)
            store.conn.execute("DELETE FROM daily_kline")
            store.conn.execute("DELETE FROM index_weights")
            config = AppConfig(data=DataConfig(db_path=db))
            ctx = RunContext(run_id="run-empty-universe", config=config, store=store)

            result = run_backtest_service(BacktestParams(start=date(2024, 6, 1), end=date(2024, 12, 31)), ctx)

            assert result.nav == []
            assert result.rows_saved.total == 0
            store.conn.execute(
                "INSERT INTO run (run_id, kind, params_json, status) VALUES (?, 'backtest', '{}', 'ok')",
                ["run-empty-universe"],
            )
            rows, _ = list_backtest_runs(store, limit=10, offset=0)
            assert rows[0].nav_points == 0

    def test_cancelled_run_still_persists_completed_weeks(self) -> None:
        from quant_trade.backtest.result_store import get_backtest_nav
        from quant_trade.services import CancelToken

        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "cancelpersist.db")
            store = _build_store(db)
            config = AppConfig(data=DataConfig(db_path=db))

            token = CancelToken()
            seen: list[float] = []

            def _cancel_after_two(pct: float, _msg: str) -> None:
                seen.append(pct)
                if len(seen) >= 2:
                    token.cancel()

            ctx = RunContext(
                run_id="run-cancel",
                config=config,
                store=store,
                cancel_token=token,
                progress_sink=_cancel_after_two,
            )
            result = run_backtest_service(BacktestParams(start=date(2024, 6, 1)), ctx)

            assert result.cancelled is True
            assert max(seen) < 1.0, "a cancelled run must not report full progress"
            assert result.rows_saved.nav > 0
            persisted = get_backtest_nav(store, "run-cancel")
            assert len(persisted) == len(result.nav)

    def test_top_n_override_does_not_write_back_to_config(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        from quant_trade.services import backtest as backtest_service

        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "topn.db")
            store = _build_store(db)
            config = AppConfig(data=DataConfig(db_path=db))
            config.strategy.top_n = 15
            ctx = RunContext(run_id="run-topn", config=config, store=store)

            captured: dict[str, object] = {}
            real_build = backtest_service.build_strategy

            def _spy(name: str | None, cfg: AppConfig, store: object = None):  # type: ignore[no-untyped-def]
                strategy = real_build(name, cfg, store=store)
                captured["strategy"] = strategy
                return strategy

            monkeypatch.setattr(backtest_service, "build_strategy", _spy)
            run_backtest_service(BacktestParams(start=date(2024, 6, 1), top_n=3), ctx)

            strategy = captured["strategy"]
            assert strategy is not None
            assert strategy.top_n == 3  # type: ignore[attr-defined]
            assert config.strategy.top_n == 15, "the override must not leak into config"


# --- Model evaluation persistence -------------------------------------------
#
# The chain test above runs with a five-name universe, which is too narrow for
# RankIC to exist (the ranker skips dates with fewer than ten observations).
# These tests train once over a wider universe and then read the result tables
# back, so the persistence path is exercised on values rather than on calls.


def _counts(store: DataStore) -> dict[str, int]:
    return {table: store.table_stats(table).rows for table in MODEL_TABLES}


@pytest.fixture(scope="module")
def trained_run() -> Iterator[tuple[DataStore, TrainResult, str]]:
    """One real training run, its store, and the predictions file it wrote."""
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "persist.db")
        store = _build_store(db, WIDE_CODES)
        config = AppConfig(data=DataConfig(db_path=db))
        ctx = RunContext(run_id="persist-1", config=config, store=store)
        compute_alpha158(Alpha158Params(start_date=date(2024, 1, 1)), ctx)
        pred_path = str(Path(tmp) / "model_ranking.parquet")
        trained = train_model(TrainParams(start=date(2024, 3, 1), end=date(2025, 6, 30), output_path=pred_path), ctx)
        yield store, trained, pred_path
        store.close()


def test_training_persists_evaluation_when_run_scoped(trained_run: tuple[DataStore, TrainResult, str]) -> None:
    store, trained, _ = trained_run
    assert trained.ic_days > 0, "the wide fixture must produce rankable days"

    ic = get_model_ic_series(store, "persist-1")
    assert len(ic) == trained.ic_days
    assert list(ic["trade_date"]) == [day for day, _ in trained.ic_series]

    importance = get_model_importance(store, "persist-1")
    assert len(importance) == len(trained.feature_importance)

    metrics = get_model_metrics(store, "persist-1")
    assert metrics["windows_trained"] == trained.windows_trained
    assert metrics["prediction_rows"] == trained.prediction_rows
    assert metrics["ic_days"] == trained.ic_days
    assert metrics["ic_mean"] == pytest.approx(trained.ic_mean)

    assert trained.rows_saved.ic == trained.ic_days
    assert trained.rows_saved.importance == len(trained.feature_importance)


def test_training_without_run_id_persists_nothing(trained_run: tuple[DataStore, TrainResult, str]) -> None:
    """An empty run id is not a key: writing it would let two runs share rows."""
    store, _, _ = trained_run
    before = _counts(store)
    ctx = RunContext(store=store)

    first = train_model(TrainParams(start=date(2024, 3, 1), end=date(2025, 6, 30)), ctx)
    second = train_model(TrainParams(start=date(2024, 3, 1), end=date(2025, 6, 30), predict_months=6), ctx)

    assert first.rows_saved.total == 0
    assert second.rows_saved.total == 0
    assert _counts(store) == before, "nothing may be written under an empty run id"
    assert first.ic_days > 0, "the results are still returned, just not stored"


def test_cancelled_training_keeps_earned_results(trained_run: tuple[DataStore, TrainResult, str]) -> None:
    """Windows that finished are real work; abandoning them would waste it."""
    store, _, _ = trained_run
    token = CancelToken()
    seen: list[float] = []

    def _cancel_after_two(percent: float, _message: str) -> None:
        seen.append(percent)
        if len(seen) >= 2:
            token.cancel()

    ctx = RunContext(run_id="cancel-1", store=store, cancel_token=token, progress_sink=_cancel_after_two)
    # The start is late on purpose: with only ~two years of fixture history, an
    # early signal date has its validation split clamped to the first calendar
    # day, so every training window is empty and a cancel would have nothing to
    # preserve. By 2025 there is enough history behind the signal to train.
    result = train_model(TrainParams(start=date(2025, 3, 1), end=date(2025, 6, 30)), ctx)

    assert result.cancelled is True
    assert result.windows_trained >= 1
    assert max(seen) < 1.0, "a cancelled run must not report itself as finished"
    assert result.rows_saved.ic > 0, "IC from completed windows must be kept"
    assert len(get_model_ic_series(store, "cancel-1")) == result.ic_days


def test_picks_are_the_head_of_the_scores(trained_run: tuple[DataStore, TrainResult, str]) -> None:
    store, trained, pred_path = trained_run
    as_of = trained.ic_series[0][0]
    result = predict_for_date(PredictParams(as_of=as_of, top_n=5, predictions_path=pred_path), RunContext(store=store))

    assert len(result.scores) == result.total_scored > 5
    assert result.picks == result.scores[:5]
    scores = [row.score for row in result.scores]
    assert scores == sorted(scores, reverse=True)


def test_train_config_overrides_only_what_was_given() -> None:
    from quant_trade.models.train import DEFAULT_PARAMS

    defaults = dict(DEFAULT_PARAMS)

    assert _train_config(TrainParams()) is None, "an untouched params object keeps the engine defaults"

    config = _train_config(TrainParams(predict_months=6, lgb_params=LightGBMParams(num_leaves=7)))
    assert config is not None
    assert config.predict_months == 6
    assert config.params["num_leaves"] == 7
    assert config.params["learning_rate"] == defaults["learning_rate"]
    assert defaults == DEFAULT_PARAMS, "overrides must not mutate the module-level defaults"
    assert config.train_years == 8.0, "unset window fields keep their defaults"
