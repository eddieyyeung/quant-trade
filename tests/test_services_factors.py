"""Factor-domain services: IC summary and registry listing."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from quant_trade.config import AppConfig
from quant_trade.data.store import DataStore
from quant_trade.services import CancelToken, RunContext
from quant_trade.services import factors as factors_service
from quant_trade.services.factors import (
    FactorICParams,
    FactorListParams,
    factor_ic_summary,
    list_factors,
)

TRADE_DATE = date(2024, 6, 28)

IC_STUB: dict[str, Any] = {
    "ic_mean": 0.041,
    "ic_std": 0.02,
    "ic_ir": 2.05,
    "ic_positive_ratio": 0.62,
    "ic_series": [(TRADE_DATE, 0.041)],
    "rank_ic_series": [(TRADE_DATE, 0.038)],
}


@pytest.fixture
def ctx() -> Iterator[RunContext]:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "f.db")
        store = DataStore(db)
        store.conn.execute("INSERT INTO trade_calendar VALUES (?, TRUE)", [TRADE_DATE])
        store.conn.executemany(
            "INSERT INTO factor_values VALUES (?,?,?,?)",
            [("alpha001", "000001.SZ", TRADE_DATE, 0.5), ("alpha002", "000001.SZ", TRADE_DATE, 0.7)],
        )
        config = AppConfig()
        config.data.db_path = db
        config.factor.enabled = ["momentum_20d", "momentum_60d"]
        yield RunContext(run_id="ic", config=config, store=store)


class TestFactorICSummary:
    def test_shape_for_each_requested_factor(self, ctx: RunContext, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(factors_service, "compute_ic_series", lambda *a, **kw: dict(IC_STUB))

        summary = factor_ic_summary(FactorICParams(factors=["momentum_20d"]), ctx)

        assert summary.forward_period == 5
        assert summary.dates == [TRADE_DATE]
        assert summary.results["momentum_20d"]["ic_mean"] == pytest.approx(0.041)
        assert summary.results["momentum_20d"]["ic_series"] == [(TRADE_DATE, 0.041)]

    def test_defaults_to_the_latest_date_only(self, ctx: RunContext, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[list[date]] = []

        def _capture(store: Any, name: str, fn: Any, universe: Any, dates: list[date], **kw: Any) -> dict[str, Any]:
            captured.append(list(dates))
            return dict(IC_STUB)

        monkeypatch.setattr(factors_service, "compute_ic_series", _capture)
        factor_ic_summary(FactorICParams(factors=["momentum_20d"]), ctx)

        assert captured == [[TRADE_DATE]]

    def test_honours_an_explicit_date_list(self, ctx: RunContext, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[list[date]] = []
        monkeypatch.setattr(
            factors_service,
            "compute_ic_series",
            lambda s, n, f, u, dates, **kw: (captured.append(list(dates)), dict(IC_STUB))[1],
        )
        dates = [date(2024, 5, 31), TRADE_DATE]
        summary = factor_ic_summary(FactorICParams(factors=["momentum_20d"], dates=dates), ctx)

        assert captured == [dates]
        assert summary.dates == dates

    def test_forward_period_is_passed_through(self, ctx: RunContext, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[int] = []
        monkeypatch.setattr(
            factors_service,
            "compute_ic_series",
            lambda s, n, f, u, d, forward_period=5: (captured.append(forward_period), dict(IC_STUB))[1],
        )
        summary = factor_ic_summary(FactorICParams(factors=["momentum_20d"], forward_period=20), ctx)

        assert captured == [20]
        assert summary.forward_period == 20

    def test_unregistered_factor_is_skipped_not_fatal(self, ctx: RunContext, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(factors_service, "compute_ic_series", lambda *a, **kw: dict(IC_STUB))
        logs: list[str] = []
        ctx.log_sink = lambda m, _l: logs.append(m)

        summary = factor_ic_summary(FactorICParams(factors=["momentum_20d", "does_not_exist"]), ctx)

        assert set(summary.results) == {"momentum_20d"}
        assert any("not registered" in m for m in logs)

    def test_cancellation_stops_the_batch(self, ctx: RunContext, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        def _record(store: Any, name: str, *a: Any, **kw: Any) -> dict[str, Any]:
            calls.append(name)
            return dict(IC_STUB)

        monkeypatch.setattr(factors_service, "compute_ic_series", _record)
        token = CancelToken()
        ctx.cancel_token = token

        # Cancel after the first factor, and give the batch more to chew on.
        monkeypatch.setattr(
            factors_service,
            "compute_ic_series",
            lambda s, n, *a, **kw: (calls.append(n), token.cancel(), dict(IC_STUB))[2],
        )
        summary = factor_ic_summary(FactorICParams(factors=["momentum_20d", "momentum_60d", "ma_deviation"]), ctx)

        assert calls == ["momentum_20d"]
        assert set(summary.results) == {"momentum_20d"}

    def test_empty_database_is_rejected(self, ctx: RunContext) -> None:
        ctx.db.conn.execute("DELETE FROM trade_calendar")
        with pytest.raises(ValueError, match="no trade dates"):
            factor_ic_summary(FactorICParams(), ctx)


class TestListFactors:
    def test_annotates_persistence_status(self, ctx: RunContext) -> None:
        result = list_factors(FactorListParams(), ctx)

        by_name = {f.name: f for f in result.factors}
        assert "momentum_20d" in by_name
        assert by_name["momentum_20d"].category == "momentum"
        assert by_name["momentum_20d"].persisted is False
        assert result.persisted_names == ["alpha001", "alpha002"]

    def test_category_filter(self, ctx: RunContext) -> None:
        result = list_factors(FactorListParams(category="value"), ctx)
        assert result.factors, "value factors should be registered"
        assert {f.category for f in result.factors} == {"value"}

    def test_unknown_category_yields_nothing(self, ctx: RunContext) -> None:
        assert list_factors(FactorListParams(category="nonexistent"), ctx).factors == []

    def test_persisted_flag_tracks_the_database(self, ctx: RunContext) -> None:
        ctx.db.conn.execute("DELETE FROM factor_values")
        assert list_factors(FactorListParams(), ctx).persisted_names == []
