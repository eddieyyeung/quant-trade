"""RunContext semantics: defaults, progress, logging, and cancellation."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest

from quant_trade.config import AppConfig
from quant_trade.data.store import DataStore
from quant_trade.services import NULL_CONTEXT, CancelToken, RunContext
from quant_trade.services.factors import FactorComputeParams, compute_factors


class TestNullContext:
    def test_is_a_run_context(self) -> None:
        assert isinstance(NULL_CONTEXT, RunContext)

    def test_progress_and_log_are_noops(self) -> None:
        NULL_CONTEXT.progress(0.5, "halfway")
        NULL_CONTEXT.log("something", level="error")

    def test_never_cancelled(self) -> None:
        assert NULL_CONTEXT.cancelled() is False

    def test_accessors_raise_without_dependencies(self) -> None:
        with pytest.raises(RuntimeError, match="no config"):
            _ = NULL_CONTEXT.cfg
        with pytest.raises(RuntimeError, match="no store"):
            _ = NULL_CONTEXT.db


class TestSinks:
    def test_progress_is_forwarded_and_clamped(self) -> None:
        seen: list[tuple[float, str]] = []
        ctx = RunContext(run_id="r", progress_sink=lambda p, m: seen.append((p, m)))

        ctx.progress(0.25, "quarter")
        ctx.progress(-1.0, "below")
        ctx.progress(9.0, "above")

        assert seen == [(0.25, "quarter"), (0.0, "below"), (1.0, "above")]

    def test_progress_message_defaults_to_empty(self) -> None:
        seen: list[tuple[float, str]] = []
        ctx = RunContext(run_id="r", progress_sink=lambda p, m: seen.append((p, m)))
        ctx.progress(0.5)
        assert seen == [(0.5, "")]

    def test_log_forwards_level(self) -> None:
        seen: list[tuple[str, str]] = []
        ctx = RunContext(run_id="r", log_sink=lambda m, level: seen.append((m, level)))
        ctx.log("boom", level="error")
        ctx.log("plain")
        assert seen == [("boom", "error"), ("plain", "info")]


class TestCancellation:
    def test_token_propagates(self) -> None:
        token = CancelToken()
        ctx = RunContext(run_id="r", cancel_token=token)
        assert ctx.cancelled() is False
        token.cancel()
        assert ctx.cancelled() is True

    def test_cancel_is_idempotent(self) -> None:
        token = CancelToken()
        token.cancel()
        token.cancel()
        assert token.is_set() is True

    def test_service_stops_when_already_cancelled(self) -> None:
        """A pre-cancelled run must do no work and say so."""
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / "q.db")
            store = DataStore(db)
            # A trade date must exist, otherwise the service rejects the run
            # before it ever reaches the loop whose cancellation we test.
            store.conn.execute("INSERT INTO trade_calendar VALUES (?, TRUE)", [date.today()])
            config = AppConfig()
            config.data.db_path = db

            token = CancelToken()
            token.cancel()
            logs: list[str] = []
            ctx = RunContext(
                run_id="cancelled",
                config=config,
                store=store,
                cancel_token=token,
                log_sink=lambda m, _level: logs.append(m),
            )

            result = compute_factors(FactorComputeParams(factors=["momentum_20d"]), ctx)

            assert result.cancelled is True
            assert result.counts == {}
            assert any("cancelled" in m for m in logs)


class TestServiceParameterContract:
    def test_params_reject_unknown_fields(self) -> None:
        with pytest.raises(Exception, match="extra_forbidden|Extra inputs"):
            FactorComputeParams(nonexistent_field=1)

    def test_params_round_trip_through_json(self) -> None:
        original = FactorComputeParams(factors=["a", "b"], as_of=date(2024, 5, 1))
        restored = FactorComputeParams.model_validate_json(original.model_dump_json())
        assert restored == original

    def test_inverted_date_range_is_rejected(self) -> None:
        """A window whose start is after its end must fail before any work."""
        from pydantic import ValidationError

        from quant_trade.services.backtest import BacktestParams
        from quant_trade.services.data import DataSyncParams
        from quant_trade.services.models import TrainParams

        with pytest.raises(ValidationError, match="must not be after"):
            BacktestParams(start=date(2025, 12, 31), end=date(2020, 1, 1))
        with pytest.raises(ValidationError, match="must not be after"):
            TrainParams(start=date(2025, 12, 31), end=date(2020, 1, 1))
        with pytest.raises(ValidationError, match="must not be after"):
            DataSyncParams(start_date=date(2025, 12, 31), end_date=date(2020, 1, 1))

    def test_equal_start_and_end_is_allowed(self) -> None:
        from quant_trade.services.backtest import BacktestParams

        params = BacktestParams(start=date(2024, 1, 1), end=date(2024, 1, 1))
        assert params.start == params.end

    def test_one_sided_range_is_allowed(self) -> None:
        from quant_trade.services.backtest import BacktestParams

        assert BacktestParams(start=date(2024, 1, 1)).end is None
        assert BacktestParams(end=date(2024, 1, 1)).start is None

    def test_params_fall_back_to_config(self) -> None:
        config = AppConfig()
        config.factor.enabled = ["f1", "f2"]
        params = FactorComputeParams.from_config(config)
        assert params.factors == ["f1", "f2"]

    def test_explicit_values_beat_config(self) -> None:
        config = AppConfig()
        config.factor.enabled = ["from_config"]
        params = FactorComputeParams.from_config(config, factors=["explicit"])
        assert params.factors == ["explicit"]
