"""Weekly report service: pipeline wiring and absence of UI side effects."""

from __future__ import annotations

import inspect
import re
import tempfile
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from quant_trade.config import AppConfig
from quant_trade.data.store import DataStore
from quant_trade.services import RunContext
from quant_trade.services import report as report_service
from quant_trade.services.backtest import RawBacktest
from quant_trade.services.factor_analysis import FactorICOverviewRow
from quant_trade.services.report import WeeklyReportParams, generate_weekly
from quant_trade.services.strategies import SignalOrder, SignalSummary


def _fake_backtest() -> RawBacktest:
    return RawBacktest(
        start=date(2024, 1, 1),
        end=date(2024, 6, 28),
        strategy="factor_ranking",
        initial_capital=100_000.0,
        nav_series=pd.Series([1.0, 1.1], index=[date(2024, 6, 27), date(2024, 6, 28)]),
        benchmark_series=None,
        trade_log=[{"date": "2024-06-28", "action": "BUY", "ts_code": "000001.SZ", "shares": 100, "price": 10.0}],
        metrics={"total_return": 0.1},
        portfolio=SimpleNamespace(holdings={}, cash=1.0, total_value=1.0),
    )


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub every external call so the pipeline's own wiring is what's tested."""
    captured: dict[str, Any] = {}

    class _FakeAdapter:
        source_name = "fake"

        def fetch_stock_basic(self) -> pd.DataFrame:
            return pd.DataFrame()

    monkeypatch.setattr(report_service, "AkshareAdapter", _FakeAdapter)
    monkeypatch.setattr(report_service, "sync_all", lambda *a, **kw: {"stock_basic": 1})
    monkeypatch.setattr(report_service, "sync_index_weights", lambda *a, **kw: None)
    monkeypatch.setattr(report_service, "run_backtest_raw", lambda *a, **kw: _fake_backtest())
    monkeypatch.setattr(
        report_service,
        "generate_strategy_signals",
        lambda *a, **kw: SignalSummary(
            signal_date=date(2024, 6, 28),
            strategy="factor_ranking",
            universe_size=3,
            orders=[SignalOrder("000001.SZ", 0.5, "BUY", "得分靠前")],
            weights={},
        ),
    )

    def _capture_report(result: Any, signals: Any, config: Any, **kwargs: Any) -> str:
        captured["result"] = result
        captured["signals"] = signals
        captured["kwargs"] = kwargs
        return "<html>ok</html>"

    monkeypatch.setattr(report_service, "generate_weekly_report", _capture_report)
    monkeypatch.setattr(
        report_service,
        "save_report",
        lambda html, config, today=None: Path(config.report.output_dir) / "weekly_test.html",
    )
    # The panel is assembled from ic_series, so the assembly is stubbed here:
    # this file tests the pipeline's wiring, not the IC digests themselves.
    monkeypatch.setattr(
        report_service,
        "factor_ic_overview",
        lambda *a, **kw: [
            FactorICOverviewRow(name="momentum_20d", ic_weekly=0.03, ic_mean=0.04, ic_ir=0.8, sample_days=5)
        ],
    )
    return captured


def _ctx(tmp: str, store: DataStore) -> RunContext:
    config = AppConfig()
    config.data.db_path = str(Path(tmp) / "q.db")
    config.report.output_dir = tmp
    return RunContext(run_id="weekly", config=config, store=store)


def test_pipeline_reaches_the_reporter(wired: dict[str, Any]) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = DataStore(str(Path(tmp) / "q.db"))
        store.conn.execute("INSERT INTO trade_calendar VALUES (?, TRUE)", [date(2024, 6, 28)])

        result = generate_weekly(WeeklyReportParams(), _ctx(tmp, store))

        assert result.report_path.endswith("weekly_test.html")
        assert result.order_count == 1
        assert result.nav_points == 2
        assert result.trade_count == 1
        assert result.cancelled is False

        assert "nav_series" in wired["result"]
        assert wired["result"]["signal_date"] == "2024-06-28"
        assert wired["signals"][0]["ts_code"] == "000001.SZ"


def test_explicit_factor_ic_rows_win_over_the_assembly(wired: dict[str, Any]) -> None:
    """An override is used as given; the assembled rows do not overwrite it."""
    rows = [{"name": "momentum_20d", "ic_weekly": 0.03, "ic_mean": 0.04, "ic_ir": 0.8}]
    with tempfile.TemporaryDirectory() as tmp:
        store = DataStore(str(Path(tmp) / "q.db"))
        store.conn.execute("INSERT INTO trade_calendar VALUES (?, TRUE)", [date(2024, 6, 28)])

        generate_weekly(WeeklyReportParams(factor_ic_data=rows), _ctx(tmp, store))

        # Equality, not identity: pydantic validates (and so copies) list fields.
        assert wired["kwargs"]["factor_ic_data"] == rows


def test_factor_ic_panel_is_assembled_by_default(wired: dict[str, Any]) -> None:
    """The panel is populated without a caller having to produce the rows.

    It used to be forwarded as ``None`` and the template's `{% if factor_ic %}`
    then rendered nothing, so the section was dead in every report ever made.
    """
    with tempfile.TemporaryDirectory() as tmp:
        store = DataStore(str(Path(tmp) / "q.db"))
        store.conn.execute("INSERT INTO trade_calendar VALUES (?, TRUE)", [date(2024, 6, 28)])

        generate_weekly(WeeklyReportParams(), _ctx(tmp, store))

        assert wired["kwargs"]["factor_ic_data"] == [
            {"name": "momentum_20d", "ic_weekly": 0.03, "ic_mean": 0.04, "ic_ir": 0.8}
        ]


def test_trade_log_reaches_the_reporter(wired: dict[str, Any]) -> None:
    """The engine's fills travel inside the result dict, not a separate argument.

    ``as_reporter_input`` already puts ``trade_log`` there; the reporter reads it
    from the dict so there is one source for it rather than two.
    """
    with tempfile.TemporaryDirectory() as tmp:
        store = DataStore(str(Path(tmp) / "q.db"))
        store.conn.execute("INSERT INTO trade_calendar VALUES (?, TRUE)", [date(2024, 6, 28)])

        generate_weekly(WeeklyReportParams(), _ctx(tmp, store))

        assert wired["result"]["trade_log"][0]["ts_code"] == "000001.SZ"
        assert wired["result"]["trade_log"][0]["action"] == "BUY"


def test_report_date_is_forwarded_for_reproducibility(wired: dict[str, Any]) -> None:
    """The run date is a parameter, so a run replays off its own day."""
    with tempfile.TemporaryDirectory() as tmp:
        store = DataStore(str(Path(tmp) / "q.db"))
        store.conn.execute("INSERT INTO trade_calendar VALUES (?, TRUE)", [date(2024, 6, 28)])

        generate_weekly(WeeklyReportParams(), _ctx(tmp, store))
        assert wired["kwargs"]["today"] is None

        generate_weekly(WeeklyReportParams(today=date(2026, 7, 22)), _ctx(tmp, store))
        assert wired["kwargs"]["today"] == date(2026, 7, 22)


def test_service_never_opens_a_browser() -> None:
    """Presenting the report is the caller's decision, not the service's."""
    source = inspect.getsource(report_service)
    assert "webbrowser" not in source
    assert "open(" not in source.replace("def open", "")


def test_the_pipeline_never_persists_strategy_signals() -> None:
    """The weekly path must not write ``strategy_signal``.

    The pipeline calls ``generate_strategy_signals`` while running under the
    *report's* run id. If persistence were ever wired into that path, every
    report would write a batch of signals keyed to a run that was not a signal
    generation — rows answering no question anyone asked, whose provenance
    would be a report.

    Asserted against the source because the pipeline's own test stubs the
    signal function out; a stubbed call cannot show what the real one writes.
    """
    source = inspect.getsource(report_service)
    # ``\b`` around the table name: the pipeline legitimately imports
    # ``generate_strategy_signals``, and that identifier contains
    # ``strategy_signal`` as a substring. The table name stands alone.
    assert re.search(r"\bstrategy_signal\b", source) is None, "report.py must not touch the strategy_signal table"
    for forbidden in ("save_strategy_signals", "build_signal_frame", "run_strategy_signals"):
        assert forbidden not in source, f"report.py must not call {forbidden}"
