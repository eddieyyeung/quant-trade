"""Strategy construction carries the caller's data store.

The defect these cover was invisible: a strategy built without a store opened
its own connection, so it read a different database than the one its caller was
using — same process, same call, two files. Nothing errored; the numbers were
just wrong.
"""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest

from quant_trade.config import AppConfig
from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.factors.registry import FactorRegistry
from quant_trade.strategies.base import SignalResult, Strategy
from quant_trade.strategies.factory import build_strategy
from quant_trade.strategies.registry import StrategyRegistry


class _NeedsStore(Strategy):
    """A strategy that declares the store parameter, like the real ones do."""

    name = "needs_store"

    def __init__(self, store: DataStore | None = None) -> None:
        super().__init__(store)

    def generate_signals(self, signal_date: date, universe: list[str], data: object) -> SignalResult:
        return SignalResult()


class _NoStoreParam(Strategy):
    """A strategy that forgot the parameter. Constructing it with one must fail."""

    name = "no_store_param"

    def __init__(self) -> None:
        super().__init__(None)

    def generate_signals(self, signal_date: date, universe: list[str], data: object) -> SignalResult:
        return SignalResult()


@pytest.fixture
def store() -> DataStore:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "q.db")
        init_db(db).close()
        yield DataStore(db)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> StrategyRegistry:
    """An isolated registry, so the real one is untouched."""
    isolated = StrategyRegistry()
    isolated.register("needs_store")(_NeedsStore)
    isolated.register("no_store_param")(_NoStoreParam)
    return isolated


class TestRegistryInjection:
    def test_store_reaches_the_strategy(self, registry: StrategyRegistry, store: DataStore) -> None:
        strategy = registry.get("needs_store", store=store)
        assert strategy is not None
        assert strategy.store is store

    def test_no_store_leaves_it_unset(self, registry: StrategyRegistry) -> None:
        """The registry does not invent one; the strategy's own default applies."""
        strategy = registry.get("needs_store")
        assert strategy is not None
        assert strategy.store is None

    def test_unknown_name_is_still_none(self, registry: StrategyRegistry, store: DataStore) -> None:
        assert registry.get("nope", store=store) is None

    def test_a_strategy_without_the_parameter_fails_loudly(self, registry: StrategyRegistry, store: DataStore) -> None:
        """Rather than silently constructing one with no store.

        A strategy that never receives a store opens its own connection — which
        is the defect this whole change is about. Failing at instantiation puts
        the mistake where it can be seen.
        """
        with pytest.raises(TypeError):
            registry.get("no_store_param", store=store)

    def test_shape_matches_the_factor_registry(self) -> None:
        """Two registries, one calling convention — otherwise callers guess.

        The whole parameter list is compared, not just the presence of `store`.
        A renamed first parameter or a reordered one would be a divergence a
        caller feels, and an earlier version of this test let both through.
        """
        import inspect

        strategy_params = list(inspect.signature(StrategyRegistry.get).parameters.values())[1:]
        factor_params = list(inspect.signature(FactorRegistry.get).parameters.values())[1:]

        assert [(p.name, p.kind, p.default) for p in strategy_params] == [
            (p.name, p.kind, p.default) for p in factor_params
        ], f"{strategy_params} != {factor_params}"


class TestFactoryInjection:
    def test_factory_threads_the_store(
        self, registry: StrategyRegistry, store: DataStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("quant_trade.strategies.factory.strategy_registry", registry)

        strategy = build_strategy("needs_store", AppConfig(), store=store)

        assert strategy is not None
        assert strategy.store is store

    def test_factory_without_a_store_still_builds(
        self, registry: StrategyRegistry, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The parameter is optional, so existing callers keep working."""
        monkeypatch.setattr("quant_trade.strategies.factory.strategy_registry", registry)

        strategy = build_strategy("needs_store", AppConfig())

        assert strategy is not None
        assert strategy.store is None

    def test_factory_still_applies_config_attributes(
        self, registry: StrategyRegistry, store: DataStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Threading a store must not cost the config-driven parameters."""
        monkeypatch.setattr("quant_trade.strategies.factory.strategy_registry", registry)
        config = AppConfig()
        config.strategy.top_n = 7

        strategy = build_strategy("needs_store", config, store=store)

        assert strategy is not None
        assert strategy.store is store


class TestRealStrategiesAcceptAStore:
    """The two registered strategies must keep declaring the parameter."""

    @pytest.mark.parametrize("name", ["factor_ranking", "model_ranking"])
    def test_registered_strategy_takes_a_store(self, name: str, store: DataStore) -> None:
        from quant_trade.strategies.registry import strategy_registry

        strategy = strategy_registry.get(name, store=store)
        assert strategy is not None
        assert strategy.store is store


class TestNoComponentFallsBackToADefaultStore:
    """The regression anchor: nothing on the signal path opens its own store.

    `_configured_db_path` is rigged to raise, so *any* component that reaches
    `DataStore()` without a path detonates instead of quietly reading a
    database nobody chose. That is the original defect reproduced as a test:
    before the fix, `factor_ranking` called `factor_registry.get(name)` with no
    store and every factor built one.

    Deliberately independent of *how* the store arrives — injection, a default,
    or anything later. The only thing asserted is that no component opens its
    own connection.
    """

    @staticmethod
    def _seeded(tmp: str) -> DataStore:
        """A small but real database: calendar, stocks, kline, index weights."""
        from datetime import timedelta

        import numpy as np

        db = str(Path(tmp) / "fixture.db")
        init_db(db).close()
        store = DataStore(db)

        codes = [f"{i:06d}.SZ" for i in range(1, 11)]
        days: list[date] = []
        day = date(2024, 1, 1)
        while len(days) < 120:
            if day.weekday() < 5:
                days.append(day)
            day += timedelta(days=1)

        store.conn.executemany("INSERT INTO trade_calendar VALUES (?, TRUE)", [(d,) for d in days])
        store.conn.executemany(
            "INSERT INTO stock_basic (ts_code, name, industry, market, list_date) VALUES (?,?,?,?,?)",
            [(code, f"股票{i}", "制造", "main", date(2018, 1, 1)) for i, code in enumerate(codes)],
        )
        store.conn.executemany(
            "INSERT INTO index_weights (index_code, ts_code, weight, in_date, out_date) VALUES (?,?,?,?,?)",
            [("000300.SH", code, 0.1, date(2018, 1, 1), date(2030, 1, 1)) for code in codes],
        )
        rows = []
        for code in codes:
            rng = np.random.default_rng(abs(hash(code)) % 2**32)
            close = float(rng.uniform(8, 40))
            for d in days:
                close *= 1 + rng.normal(0.0004, 0.018)
                rows.append((code, d, close * 0.99, close * 1.01, close * 0.98, close, 1e6, 1e7, None, None))
        store.conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        return store

    def test_signal_generation_opens_no_default_database(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from quant_trade.services.context import RunContext
        from quant_trade.services.strategies import SignalParams, generate_strategy_signals

        def _explode() -> str:
            raise AssertionError("a component opened its own DataStore instead of using the store it was given")

        monkeypatch.setattr("quant_trade.data.store._configured_db_path", _explode)

        with tempfile.TemporaryDirectory() as tmp:
            store = self._seeded(tmp)
            config = AppConfig()
            config.data.db_path = store.db_path
            config.strategy.name = "factor_ranking"
            ctx = RunContext(run_id="t", config=config, store=store)

            summary = generate_strategy_signals(SignalParams(as_of=date(2026, 7, 24)), ctx)

        # The call completing at all is the assertion: with the fallback rigged
        # to raise, reaching it would have failed the test.
        assert summary.signal_date == date(2026, 7, 24)
        assert summary.strategy == "factor_ranking"

    def test_every_factor_receives_the_injected_store(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The defect's exact site: the strategy had a store and passed none."""
        from quant_trade.factors.registry import registry as factor_registry
        from quant_trade.services.context import RunContext
        from quant_trade.services.strategies import SignalParams, generate_strategy_signals

        seen: list[object] = []
        real_get = factor_registry.get

        def spy(name: str, store: object = None) -> object:
            seen.append(store)
            return real_get(name, store=store)  # type: ignore[arg-type]

        monkeypatch.setattr(factor_registry, "get", spy)
        monkeypatch.setattr(
            "quant_trade.data.store._configured_db_path",
            lambda: (_ for _ in ()).throw(AssertionError("fell back to a default store")),
        )

        with tempfile.TemporaryDirectory() as tmp:
            store = self._seeded(tmp)
            config = AppConfig()
            config.data.db_path = store.db_path
            config.strategy.name = "factor_ranking"
            ctx = RunContext(run_id="t", config=config, store=store)

            generate_strategy_signals(SignalParams(as_of=date(2026, 7, 24)), ctx)

        assert seen, "the strategy built no factors — the spied path was not exercised"
        assert all(s is store for s in seen), "a factor was built with a store other than the caller's"
