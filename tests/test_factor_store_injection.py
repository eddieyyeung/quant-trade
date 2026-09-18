"""The factor-side scenarios of `factor-store-injection`.

These cover the half of the capability the archived `fix-simulator-memory-leak`
change specified but never tested: that `FactorRegistry.get` hands the store to
the factor it builds, that a factor built without one resolves the configured
database and says so, and that an injected store produces the same values as a
store opened directly on the same file.

The `factor-system` spec's 「实现新因子」 scenario is covered here too, by the
subclass in `_MinimalFactor` — it defines no `__init__`, which is the case that
used to end in an `AttributeError` at the first query.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest
from loguru import logger

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.factors.base import Factor
from quant_trade.factors.registry import registry as factor_registry

FACTOR_NAME = "momentum_20d"


class _MinimalFactor(Factor):
    """A factor written the way the spec tells a developer to write one.

    Implements `compute` and nothing else — no `__init__`, so whatever the base
    class does about the store is what this gets.
    """

    name = "minimal_test_factor"

    def compute(self, date: date, universe: list[str]) -> pd.Series:
        return pd.Series(dict.fromkeys(universe, 1.0))


@pytest.fixture
def warnings() -> Iterator[list[str]]:
    """Capture loguru warnings. loguru does not propagate to `caplog`."""
    records: list[str] = []
    handler = logger.add(lambda message: records.append(str(message)), level="WARNING")
    yield records
    logger.remove(handler)


def _configured(tmp: str, db_path: str) -> str:
    """Write a config file naming ``db_path`` and point `QUANT_CONFIG` at it."""
    config = Path(tmp) / "config.yaml"
    config.write_text(f"data:\n  db_path: {db_path}\n", encoding="utf-8")
    return str(config)


@pytest.fixture
def store() -> Iterator[DataStore]:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "factor.db")
        init_db(db).close()
        yield DataStore(db)


class TestRegistryHandsOverTheStore:
    """`FactorRegistry.get` scenarios — previously covered by nothing."""

    def test_get_with_store_injected(self, store: DataStore) -> None:
        factor = factor_registry.get(FACTOR_NAME, store=store)

        assert factor is not None
        assert factor.store is store

    def test_get_without_a_store_resolves_the_configured_database(
        self, monkeypatch: pytest.MonkeyPatch, warnings: list[str]
    ) -> None:
        """The scenario this change wrote, and had not tested."""
        with tempfile.TemporaryDirectory() as tmp:
            configured = str(Path(tmp) / "configured.db")
            monkeypatch.setenv("QUANT_CONFIG", _configured(tmp, configured))

            factor = factor_registry.get(FACTOR_NAME)

            assert factor is not None
            assert factor.store.db_path == configured
            assert any(configured in message for message in warnings), warnings


class TestFactorWithoutAnInjectedStore:
    """`factor-system` scenarios 因子使用收到的数据访问对象 / 未收到时告警."""

    def test_factor_uses_the_store_it_received(self, store: DataStore) -> None:
        factor = _MinimalFactor(store=store)
        assert factor.store is store

    def test_factor_without_one_warns_and_stays_usable(
        self, monkeypatch: pytest.MonkeyPatch, warnings: list[str]
    ) -> None:
        """Usable, not merely non-crashing — the spec says it keeps working."""
        with tempfile.TemporaryDirectory() as tmp:
            configured = str(Path(tmp) / "configured.db")
            monkeypatch.setenv("QUANT_CONFIG", _configured(tmp, configured))

            factor = _MinimalFactor()

            assert factor.store.db_path == configured
            assert any(configured in message for message in warnings), warnings
            # And it computes: the store it fell back to is a working object.
            result = factor.compute(date(2026, 7, 24), ["000001.SZ"])
            assert list(result.index) == ["000001.SZ"]

    def test_implementing_compute_is_enough_to_be_usable(self) -> None:
        """The 「实现新因子」 scenario: subclass + `compute`, nothing else."""
        factor_registry.register(_MinimalFactor.name)(_MinimalFactor)

        factor = factor_registry.get(_MinimalFactor.name)

        assert factor is not None
        assert factor.name == _MinimalFactor.name


class TestInjectedStoreComputesTheSameValues:
    """`compute result equivalence` — the scenario carried in from the archive."""

    @staticmethod
    def _seeded(tmp: str) -> str:
        """A database with enough history for `momentum_20d` to have a value."""
        db = str(Path(tmp) / "equivalence.db")
        init_db(db).close()
        store = DataStore(db)

        codes = ["000001.SZ", "600000.SH"]
        days: list[date] = []
        day = date(2024, 1, 1)
        while len(days) < 60:
            if day.weekday() < 5:
                days.append(day)
            day += timedelta(days=1)

        store.conn.executemany("INSERT INTO trade_calendar VALUES (?, TRUE)", [(d,) for d in days])
        store.conn.executemany(
            "INSERT INTO stock_basic (ts_code, name, industry, market, list_date) VALUES (?,?,?,?,?)",
            [(code, f"股票{i}", "制造", "main", date(2018, 1, 1)) for i, code in enumerate(codes)],
        )
        rows = []
        for i, code in enumerate(codes):
            close = 10.0 + i
            for d in days:
                close *= 1.001
                rows.append((code, d, close * 0.99, close * 1.01, close * 0.98, close, 1e6, 1e7, None, None))
        store.conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        store.close()
        return db

    def test_injected_store_matches_a_directly_opened_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = self._seeded(tmp)
            universe = ["000001.SZ", "600000.SH"]
            as_of = date(2024, 3, 15)

            injected = factor_registry.get(FACTOR_NAME, store=DataStore(db))
            direct = factor_registry.get(FACTOR_NAME, store=DataStore(db))
            assert injected is not None and direct is not None

            assert injected.compute(as_of, universe).equals(direct.compute(as_of, universe))
