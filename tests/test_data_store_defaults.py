"""What a `DataStore` with no path opens, and whether it says so.

The defect these cover produced wrong numbers rather than errors: a component
that never received a store opened a database nobody chose, silently. So the
tests are about two things — the fallback resolves the *configured* path, and
reaching it leaves a trace.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from loguru import logger

from quant_trade.config import AppConfig
from quant_trade.data.store import DataStore


def _capture_warnings(records: list[str]) -> int:
    """Start recording loguru warnings into ``records``; returns the sink id."""
    return logger.add(lambda message: records.append(str(message)), level="WARNING")


@pytest.fixture
def warnings() -> Iterator[list[str]]:
    """Capture loguru warnings. loguru does not propagate to `caplog`."""
    records: list[str] = []
    handler = _capture_warnings(records)
    yield records
    logger.remove(handler)


@pytest.fixture
def component_warnings() -> Iterator[list[str]]:
    """The same, for tests that build real components rather than a DataStore."""
    records: list[str] = []
    handler = _capture_warnings(records)
    yield records
    logger.remove(handler)


def _config_pointing_at(tmp: str, db_path: str) -> Path:
    """A config file whose `data.db_path` is ``db_path``."""
    config = Path(tmp) / "config.yaml"
    config.write_text(f"data:\n  db_path: {db_path}\n", encoding="utf-8")
    return config


class TestFallbackPath:
    def test_resolves_the_configured_database(self, monkeypatch: pytest.MonkeyPatch, warnings: list[str]) -> None:
        """Not a literal. This is the whole point of the change."""
        with tempfile.TemporaryDirectory() as tmp:
            configured = str(Path(tmp) / "configured.db")
            monkeypatch.setenv("QUANT_CONFIG", str(_config_pointing_at(tmp, configured)))

            assert DataStore().db_path == configured

    def test_follows_the_same_rule_the_app_uses(self, monkeypatch: pytest.MonkeyPatch, warnings: list[str]) -> None:
        """`QUANT_CONFIG` moves it, exactly as it moves the process's database."""
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setenv("QUANT_CONFIG", str(_config_pointing_at(tmp, "/tmp/first.db")))
            assert DataStore().db_path == "/tmp/first.db"

            monkeypatch.setenv("QUANT_CONFIG", str(_config_pointing_at(tmp, "/tmp/second.db")))
            assert DataStore().db_path == "/tmp/second.db"

    def test_explicit_path_ignores_the_config(self, monkeypatch: pytest.MonkeyPatch, warnings: list[str]) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setenv("QUANT_CONFIG", str(_config_pointing_at(tmp, "/tmp/from-config.db")))

            assert DataStore("/tmp/explicit.db").db_path == "/tmp/explicit.db"


class TestVisibility:
    def test_fallback_warns_and_names_the_path(self, monkeypatch: pytest.MonkeyPatch, warnings: list[str]) -> None:
        """A warning that does not say *which* database is useless."""
        with tempfile.TemporaryDirectory() as tmp:
            configured = str(Path(tmp) / "configured.db")
            monkeypatch.setenv("QUANT_CONFIG", str(_config_pointing_at(tmp, configured)))

            DataStore()

            assert len(warnings) == 1, warnings
            assert configured in warnings[0]
            assert "without an explicit path" in warnings[0]

    def test_explicit_path_is_silent(self, warnings: list[str]) -> None:
        """The normal path must not be noisy, or the warning becomes wallpaper."""
        DataStore("/tmp/quiet.db")
        assert warnings == []

    def test_injecting_into_a_real_component_is_silent(self, component_warnings: list[str]) -> None:
        """The scenario is about a component, so it is a component that gets tested.

        `DataStore("/tmp/quiet.db")` never touched one, so it stayed green even
        if every component's injection was removed.
        """
        from quant_trade.factors.registry import registry as factor_registry
        from quant_trade.strategies.factory import build_strategy

        with tempfile.TemporaryDirectory() as tmp:
            store = DataStore(str(Path(tmp) / "component.db"))

            factor = factor_registry.get("momentum_20d", store=store)
            strategy = build_strategy("factor_ranking", AppConfig(), store=store)

            assert factor is not None and strategy is not None
            assert component_warnings == [], component_warnings

    def test_unresolvable_config_fails_rather_than_picking_a_database(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The deliberate limit of "the fallback does not raise".

        It does not raise in order to keep a caller running when the *database*
        is knowable. When even that is not — no `QUANT_CONFIG`, no config file
        relative to the working directory — silently choosing one is the exact
        behaviour this change removed, so it fails instead.
        """
        monkeypatch.delenv("QUANT_CONFIG", raising=False)
        monkeypatch.chdir(tmp_path)

        with pytest.raises(FileNotFoundError):
            DataStore()

    def test_fallback_still_works(self, monkeypatch: pytest.MonkeyPatch, warnings: list[str]) -> None:
        """It warns; it does not refuse. A script must keep running."""
        with tempfile.TemporaryDirectory() as tmp:
            configured = str(Path(tmp) / "configured.db")
            monkeypatch.setenv("QUANT_CONFIG", str(_config_pointing_at(tmp, configured)))

            store = DataStore()
            assert store.db_path == configured
            # And it is usable: the connection is lazy, so opening is what
            # would fail if the fallback had resolved something unusable.
            assert store.conn is not None
            store.close()
