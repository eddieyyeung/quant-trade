"""The weekly report's per-factor IC digest.

The digest exists so the report's IC panel has a producer. What matters is that
it reads ``ic_series`` once for every factor rather than once per factor, and
that its numbers are the same ones the IC-analysis page would compute from the
same rows.
"""

from __future__ import annotations

import tempfile
from datetime import date

import pandas as pd
import pytest

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.factors.ic_store import IC_COLUMNS, save_ic_series
from quant_trade.services import RunContext, factor_analysis
from quant_trade.services.factor_analysis import (
    FactorICOverviewParams,
    FactorICOverviewRow,
    factor_ic_overview,
)


def _store(db_path: str) -> DataStore:
    init_db(db_path).close()
    return DataStore(db_path)


def _ctx(store: DataStore) -> RunContext:
    return RunContext(run_id="t", store=store)


def _seed(store: DataStore, rows: list[tuple[str, date, int, float, float, int]]) -> None:
    save_ic_series(store, pd.DataFrame(rows, columns=IC_COLUMNS))


class TestFactorICOverview:
    def test_one_read_covers_every_factor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A per-factor loop would be N round trips for one panel.

        Wrapped at the read function rather than at the connection: DuckDB's
        ``execute`` is a read-only attribute and cannot be swapped out, and the
        claim worth pinning is "one call carrying every factor", not the SQL
        text it sends.
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(f"{tmp}/a.db")
            _seed(
                store,
                [
                    ("MA20", date(2024, 6, 3), 5, 0.03, 0.04, 300),
                    ("MA20", date(2024, 6, 4), 5, -0.01, -0.02, 298),
                    ("RSI6", date(2024, 6, 3), 5, 0.05, 0.06, 300),
                    ("RSI6", date(2024, 6, 4), 5, 0.07, 0.08, 300),
                ],
            )
            calls: list[list[str]] = []
            real = factor_analysis.get_ic_series

            def counting(
                store_: DataStore,
                factors: list[str],
                start: date,
                end: date,
                forward_period: int | None = None,
            ) -> pd.DataFrame:
                calls.append(list(factors))
                return real(store_, factors, start, end, forward_period=forward_period)

            monkeypatch.setattr(factor_analysis, "get_ic_series", counting)
            rows = factor_ic_overview(FactorICOverviewParams(as_of=date(2024, 6, 30)), _ctx(store))

            assert calls == [["MA20", "RSI6"]]
            assert [row.name for row in rows] == ["MA20", "RSI6"]

    def test_weekly_ic_is_the_latest_day_not_a_calendar_week(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(f"{tmp}/a.db")
            _seed(
                store,
                [
                    ("MA20", date(2024, 6, 3), 5, 0.03, 0.04, 300),
                    ("MA20", date(2024, 6, 4), 5, -0.015, -0.02, 298),
                ],
            )
            rows = factor_ic_overview(FactorICOverviewParams(as_of=date(2024, 6, 30)), _ctx(store))

            assert rows[0].ic_weekly == pytest.approx(-0.015)
            assert rows[0].sample_days == 2

    def test_mean_and_ir_match_the_shared_summary(self) -> None:
        values = [0.03, -0.01, 0.05]
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(f"{tmp}/a.db")
            _seed(
                store,
                [
                    ("MA20", date(2024, 6, day), 5, value, value, 300)
                    for day, value in zip((3, 4, 5), values, strict=True)
                ],
            )
            rows = factor_ic_overview(FactorICOverviewParams(as_of=date(2024, 6, 30)), _ctx(store))

            series = pd.Series(values)
            assert rows[0].ic_mean == pytest.approx(float(series.mean()))
            assert rows[0].ic_ir == pytest.approx(float(series.mean() / series.std(ddof=0)))

    def test_window_excludes_older_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(f"{tmp}/a.db")
            _seed(
                store,
                [
                    ("MA20", date(2020, 1, 2), 5, 0.9, 0.9, 300),
                    ("MA20", date(2024, 6, 4), 5, 0.02, 0.02, 300),
                ],
            )
            rows = factor_ic_overview(FactorICOverviewParams(as_of=date(2024, 6, 30), lookback_days=30), _ctx(store))

            assert rows[0].sample_days == 1
            assert rows[0].ic_weekly == pytest.approx(0.02)

    def test_forward_period_filters_the_rows(self) -> None:
        """Holding periods are separate series; mixing them would average apples."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(f"{tmp}/a.db")
            _seed(
                store,
                [
                    ("MA20", date(2024, 6, 4), 1, 0.5, 0.5, 300),
                    ("MA20", date(2024, 6, 4), 5, 0.02, 0.02, 300),
                ],
            )
            rows = factor_ic_overview(FactorICOverviewParams(as_of=date(2024, 6, 30), forward_period=5), _ctx(store))

            assert rows[0].sample_days == 1
            assert rows[0].ic_weekly == pytest.approx(0.02)

    def test_empty_table_yields_no_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(f"{tmp}/a.db")
            assert factor_ic_overview(FactorICOverviewParams(), _ctx(store)) == []

    def test_factor_without_ic_is_not_returned_blank(self) -> None:
        """A factor with rows in factor_values but none in ic_series is omitted.

        Returning it with nulls would render as a factor with no edge, which is
        a different claim from "its IC was never computed".
        """
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(f"{tmp}/a.db")
            store.conn.execute(
                "INSERT INTO factor_values VALUES (?, ?, ?, ?)",
                ["MA20", "600519.SH", date(2024, 6, 4), 1.0],
            )
            assert factor_ic_overview(FactorICOverviewParams(), _ctx(store)) == []

    def test_a_factor_whose_rows_are_all_null_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(f"{tmp}/a.db")
            store.conn.execute(
                "INSERT INTO ic_series VALUES (?, ?, ?, NULL, NULL, 0)",
                ["MA20", date(2024, 6, 4), 5],
            )
            rows = factor_ic_overview(FactorICOverviewParams(), _ctx(store))

            assert rows == []

    def test_params_round_trip_through_json(self) -> None:
        params = FactorICOverviewParams(as_of=date(2024, 6, 30), lookback_days=90, forward_period=10)
        assert FactorICOverviewParams.model_validate_json(params.model_dump_json()) == params

    def test_row_defaults_are_all_optional_but_the_name(self) -> None:
        row = FactorICOverviewRow(name="MA20")
        assert (row.ic_weekly, row.ic_mean, row.ic_ir, row.sample_days) == (None, None, None, 0)
