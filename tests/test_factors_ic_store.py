"""Tests for IC series persistence."""

import tempfile
from datetime import date

import pandas as pd
import pytest

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.factors.ic_store import IC_COLUMNS, get_ic_series, save_ic_series


def _store(db_path: str) -> DataStore:
    init_db(db_path).close()
    return DataStore(db_path)


def _frame(rows: list[tuple[str, date, int, float, float, int]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=IC_COLUMNS)


class TestSave:
    def test_writes_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            written = save_ic_series(
                store,
                _frame(
                    [
                        ("MA20", date(2024, 1, 2), 5, 0.03, 0.04, 300),
                        ("MA20", date(2024, 1, 3), 5, -0.01, -0.02, 298),
                    ]
                ),
            )
            assert written == 2
            out = get_ic_series(store, ["MA20"], date(2024, 1, 1), date(2024, 1, 31))
            assert len(out) == 2
            assert out["ic"].tolist() == pytest.approx([0.03, -0.01])
            assert out["sample_size"].tolist() == [300, 298]

    def test_empty_frame_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            assert save_ic_series(store, pd.DataFrame(columns=IC_COLUMNS)) == 0
            assert get_ic_series(store, ["MA20"], date(2024, 1, 1), date(2024, 1, 31)).empty

    def test_missing_column_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            with pytest.raises(ValueError, match="missing columns"):
                save_ic_series(store, pd.DataFrame([{"factor_name": "MA20"}]))

    def test_holding_periods_coexist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            save_ic_series(
                store,
                _frame([("MA20", date(2024, 1, 2), p, 0.01 * p, 0.02 * p, 300) for p in (1, 5, 10, 20)]),
            )
            out = get_ic_series(store, ["MA20"], date(2024, 1, 1), date(2024, 1, 31))
            assert sorted(out["forward_period"].tolist()) == [1, 5, 10, 20]

    def test_rerun_overwrites_same_key(self) -> None:
        """Rerunning a range replaces rows instead of duplicating them."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            key = ("MA20", date(2024, 1, 2), 5)
            save_ic_series(store, _frame([(*key, 0.03, 0.04, 300)]))
            save_ic_series(store, _frame([(*key, 0.09, 0.08, 250)]))

            out = get_ic_series(store, ["MA20"], date(2024, 1, 1), date(2024, 1, 31))
            assert len(out) == 1
            assert out["ic"].iloc[0] == pytest.approx(0.09)
            assert out["sample_size"].iloc[0] == 250


class TestQuery:
    def test_filters_by_factor_and_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            save_ic_series(
                store,
                _frame(
                    [
                        ("MA20", date(2024, 1, 2), 5, 0.03, 0.04, 300),
                        ("STD20", date(2024, 1, 2), 5, -0.05, -0.06, 300),
                        ("MA20", date(2024, 2, 2), 5, 0.07, 0.08, 300),
                    ]
                ),
            )
            out = get_ic_series(store, ["MA20"], date(2024, 1, 1), date(2024, 1, 31))
            assert len(out) == 1
            assert out["factor_name"].unique().tolist() == ["MA20"]

    def test_filters_by_holding_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            save_ic_series(store, _frame([("MA20", date(2024, 1, 2), p, 0.01, 0.02, 300) for p in (5, 20)]))
            out = get_ic_series(store, ["MA20"], date(2024, 1, 1), date(2024, 1, 31), forward_period=20)
            assert out["forward_period"].tolist() == [20]

    def test_unknown_factor_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            out = get_ic_series(store, ["NOPE"], date(2024, 1, 1), date(2024, 1, 31))
            assert out.empty
            assert list(out.columns) == IC_COLUMNS

    def test_no_factors_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            assert get_ic_series(store, [], date(2024, 1, 1), date(2024, 1, 31)).empty

    def test_ordered_by_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _store(tmp + "/a.db")
            save_ic_series(
                store,
                _frame(
                    [("MA20", d, 5, 0.01, 0.02, 300) for d in (date(2024, 1, 5), date(2024, 1, 2), date(2024, 1, 3))]
                ),
            )
            out = get_ic_series(store, ["MA20"], date(2024, 1, 1), date(2024, 1, 31))
            # DuckDB hands dates back as Timestamps; compare on the date part.
            assert out["trade_date"].dt.date.tolist() == [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 5)]
