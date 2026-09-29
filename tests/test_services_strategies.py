"""Strategy-domain services: signal generation and registry listing."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from quant_trade.config import AppConfig
from quant_trade.data.store import DataStore
from quant_trade.services import RunContext
from quant_trade.services import strategies as strategies_service
from quant_trade.services.strategies import (
    SignalParams,
    StrategyListParams,
    generate_strategy_signals,
    list_strategies,
    most_recent_friday,
    run_strategy_signals,
)
from quant_trade.strategies.base import Order, SignalResult
from quant_trade.strategies.signal_store import count_strategy_signals, get_strategy_signals

TRADE_DATE = date(2024, 6, 28)


class _FakeStrategy:
    """Minimal Strategy stand-in, so the service wrapper is what gets tested."""

    name = "fake"
    top_n = 3
    factor_weights: dict[str, float] = {}
    max_industry_weight = 0.3

    def __init__(self) -> None:
        self.calls: list[tuple[date, list[str]]] = []
        self.orders: list[Order] = [Order("000001.SZ", 0.5, "BUY", "得分靠前")]

    def generate_signals(self, signal_date: date, universe: list[str], data: object) -> SignalResult:
        self.calls.append((signal_date, list(universe)))
        return SignalResult(orders=list(self.orders), weights={o.ts_code: o.target_pct for o in self.orders})


@pytest.fixture
def ctx() -> Iterator[RunContext]:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "s.db")
        store = DataStore(db)
        store.conn.execute("INSERT INTO trade_calendar VALUES (?, TRUE)", [TRADE_DATE])
        config = AppConfig()
        config.data.db_path = db
        config.strategy.name = "fake"
        yield RunContext(run_id="sig", config=config, store=store)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> _FakeStrategy:
    strategy = _FakeStrategy()
    monkeypatch.setattr(strategies_service, "build_strategy", lambda *a, **kw: strategy)
    return strategy


class TestGenerateSignals:
    def test_returns_a_serializable_summary(self, ctx: RunContext, fake: _FakeStrategy) -> None:
        summary = generate_strategy_signals(SignalParams(universe=["000001.SZ", "600000.SH"]), ctx)

        assert summary.signal_date == TRADE_DATE
        assert summary.strategy == "fake"
        assert summary.universe_size == 2
        assert len(summary.orders) == 1
        assert summary.orders[0].ts_code == "000001.SZ"
        assert summary.orders[0].direction == "BUY"
        assert summary.orders[0].reason == "得分靠前"
        assert summary.weights == {"000001.SZ": 0.5}

    def test_defaults_to_the_most_recent_friday(self, ctx: RunContext, fake: _FakeStrategy) -> None:
        summary = generate_strategy_signals(SignalParams(universe=["000001.SZ"]), ctx)
        assert summary.signal_date == TRADE_DATE
        assert fake.calls[0][0] == TRADE_DATE

    def test_uses_the_supplied_date(self, ctx: RunContext, fake: _FakeStrategy) -> None:
        generate_strategy_signals(SignalParams(as_of=date(2024, 5, 31), universe=["000001.SZ"]), ctx)
        assert fake.calls[0][0] == date(2024, 5, 31)

    def test_top_n_overrides_the_strategy_default(self, ctx: RunContext, fake: _FakeStrategy) -> None:
        generate_strategy_signals(SignalParams(top_n=7, universe=["000001.SZ"]), ctx)
        assert fake.top_n == 7

    def test_top_n_left_alone_when_not_supplied(self, ctx: RunContext, fake: _FakeStrategy) -> None:
        generate_strategy_signals(SignalParams(universe=["000001.SZ"]), ctx)
        assert fake.top_n == 3

    def test_no_orders_is_not_an_error(self, ctx: RunContext, fake: _FakeStrategy) -> None:
        fake.orders = []
        summary = generate_strategy_signals(SignalParams(universe=["000001.SZ"]), ctx)
        assert summary.orders == []
        assert summary.weights == {}

    def test_reports_progress(self, ctx: RunContext, fake: _FakeStrategy) -> None:
        seen: list[float] = []
        ctx.progress_sink = lambda pct, _msg: seen.append(pct)
        generate_strategy_signals(SignalParams(universe=["000001.SZ"]), ctx)
        assert seen == [0.0, 1.0]

    def test_unknown_strategy_is_rejected(self, ctx: RunContext, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(strategies_service, "build_strategy", lambda *a, **kw: None)
        with pytest.raises(ValueError, match="not registered"):
            generate_strategy_signals(SignalParams(strategy="nope"), ctx)

    def test_empty_database_is_rejected(self, ctx: RunContext, fake: _FakeStrategy) -> None:
        ctx.db.conn.execute("DELETE FROM trade_calendar")
        with pytest.raises(ValueError, match="no trade dates"):
            generate_strategy_signals(SignalParams(), ctx)

    def test_the_strategy_is_built_with_the_context_store(
        self, ctx: RunContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The call site that links the run's store to everything downstream.

        The `fake` fixture replaces `build_strategy` with something that ignores
        its arguments, so the threading it performs is stubbed out in every
        other test here. This asserts the argument itself — and with it the
        store every factor the strategy builds goes on to receive (see
        ``test_strategy_injection.TestNoComponentFallsBackToADefaultStore``).
        """
        received: list[object] = []

        def spy(name: str | None, config: object, store: object = None) -> _FakeStrategy:
            received.append(store)
            return _FakeStrategy()

        monkeypatch.setattr(strategies_service, "build_strategy", spy)
        generate_strategy_signals(SignalParams(universe=["000001.SZ"]), ctx)

        assert received == [ctx.db]


class TestMostRecentFriday:
    def test_returns_none_without_a_friday_in_window(self, ctx: RunContext) -> None:
        # The fixture calendar holds a single Friday, so an earlier anchor
        # window contains none.
        assert most_recent_friday(ctx.db, date(2024, 5, 1)) is None

    def test_returns_the_friday_itself(self, ctx: RunContext) -> None:
        assert most_recent_friday(ctx.db, TRADE_DATE) == TRADE_DATE

    def test_skips_forward_weekdays(self, ctx: RunContext) -> None:
        """A Thursday must key off the *previous* Friday, not the next one."""
        assert most_recent_friday(ctx.db, date(2024, 7, 4)) == TRADE_DATE

    def test_signal_generation_defaults_to_the_friday(self, ctx: RunContext, fake: _FakeStrategy) -> None:
        summary = generate_strategy_signals(SignalParams(universe=["000001.SZ"]), ctx)
        assert summary.signal_date == TRADE_DATE
        assert summary.signal_date.weekday() == 4


class TestListStrategies:
    def test_lists_the_registry(self, ctx: RunContext) -> None:
        names = list_strategies(StrategyListParams(), ctx)
        assert "factor_ranking" in names
        assert "model_ranking" in names


class TestSignalParams:
    def test_config_defaults(self) -> None:
        config = AppConfig()
        config.strategy.name = "model_ranking"
        config.strategy.top_n = 11
        params = SignalParams.from_config(config)
        assert params.strategy == "model_ranking"
        assert params.top_n == 11


class TestRunStrategySignals:
    """The persisting entry point, and the isolation of the pure one."""

    def test_persists_under_the_run_id(self, ctx: RunContext, fake: _FakeStrategy) -> None:
        assert count_strategy_signals(ctx.db, "sig") == 0
        fake.orders = [Order("000001.SZ", 0.5, "BUY", "得分靠前"), Order("000002.SZ", 0.3, "BUY", "次优")]

        summary = run_strategy_signals(SignalParams(), ctx)

        assert len(summary.orders) == 2
        assert count_strategy_signals(ctx.db, "sig") == 2
        frame = get_strategy_signals(ctx.db, "sig", limit=10, offset=0)
        assert frame["ts_code"].tolist() == ["000001.SZ", "000002.SZ"]
        assert frame["strategy"].unique().tolist() == ["fake"]
        assert {stamp.date() for stamp in frame["trade_date"]} == {TRADE_DATE}

    def test_empty_run_id_writes_nothing(self, ctx: RunContext, fake: _FakeStrategy) -> None:
        """The NULL_CONTEXT case — scripts and tests run without a run id."""
        anonymous = RunContext(run_id="", config=ctx.cfg, store=ctx.db)

        summary = run_strategy_signals(SignalParams(), anonymous)

        assert summary.orders, "the result is still returned in full"
        assert count_strategy_signals(ctx.db, "sig") == 0

    def test_no_orders_writes_nothing(self, ctx: RunContext, fake: _FakeStrategy) -> None:
        """An empty result is not worth a row — it would claim signals of none."""
        fake.orders = []

        run_strategy_signals(SignalParams(), ctx)

        assert count_strategy_signals(ctx.db, "sig") == 0

    def test_the_pure_entry_point_never_persists(self, ctx: RunContext, fake: _FakeStrategy) -> None:
        """Even under a non-empty run_id.

        This is the isolation the weekly report depends on: its pipeline calls
        this function while running under the *report's* run id, and persisting
        there would write signals keyed to a run that was not a signal
        generation.
        """
        assert ctx.run_id, "the fixture must supply a run id for this to mean anything"

        summary = generate_strategy_signals(SignalParams(), ctx)

        assert summary.orders
        assert count_strategy_signals(ctx.db, ctx.run_id) == 0

    def test_rewriting_a_run_replaces_its_rows(self, ctx: RunContext, fake: _FakeStrategy) -> None:
        fake.orders = [Order(f"{i:06d}.SZ", 0.1, "BUY", "x") for i in range(4)]
        run_strategy_signals(SignalParams(), ctx)

        fake.orders = [Order("000001.SZ", 0.1, "BUY", "x")]
        run_strategy_signals(SignalParams(), ctx)

        assert count_strategy_signals(ctx.db, "sig") == 1
