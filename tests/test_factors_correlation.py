"""Tests for factor-factor correlation."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_trade.factors.correlation import CorrelationResult, correlation_matrix

START = date(2024, 1, 1)
CODES = [f"{i:06d}.SZ" for i in range(1, 21)]
COLUMNS = ["factor_name", "ts_code", "trade_date", "value"]


def _frame(days: int, names: tuple[str, ...], value_of: object) -> pd.DataFrame:
    rows = []
    for i in range(days):
        day = START + timedelta(days=i)
        for code_index, code in enumerate(CODES):
            for name in names:
                value = value_of(name, code_index, i) if callable(value_of) else value_of
                rows.append((name, code, day, float(value)))
    return pd.DataFrame(rows, columns=COLUMNS)


def _pair(matrix: list[list[float]], i: int, j: int) -> float:
    return matrix[i][j]


class TestMatrix:
    def test_is_symmetric_with_unit_diagonal(self) -> None:
        result = correlation_matrix(_frame(5, ("A", "B", "C"), lambda n, c, d: c + d + hash(n) % 3))
        assert result.factors == ["A", "B", "C"]
        for i in range(3):
            assert _pair(result.matrix, i, i) == pytest.approx(1.0)
            for j in range(3):
                assert _pair(result.matrix, i, j) == pytest.approx(_pair(result.matrix, j, i))

    def test_identical_factor_pairs_are_highly_correlated(self) -> None:
        """A and B are the same series; C is noise."""
        rng = np.random.default_rng(7)
        noise = {c: float(rng.normal()) for c in CODES}
        result = correlation_matrix(
            _frame(6, ("A", "B", "C"), lambda n, c, d: noise[CODES[c]] if n != "C" else noise[CODES[c]] * -1 + d)
        )
        index = result.factors.index
        assert _pair(result.matrix, index("A"), index("B")) == pytest.approx(1.0)

    def test_anti_correlated_factors_are_negative(self) -> None:
        result = correlation_matrix(_frame(5, ("A", "B"), lambda n, c, d: c if n == "A" else -c))
        i, j = result.factors.index("A"), result.factors.index("B")
        assert _pair(result.matrix, i, j) == pytest.approx(-1.0)

    def test_values_within_bounds(self) -> None:
        rng = np.random.default_rng(11)
        result = correlation_matrix(_frame(8, ("A", "B", "C"), lambda n, c, d: rng.normal()))
        assert all(-1.0 <= value <= 1.0 for row in result.matrix for value in row)

    def test_single_factor_yields_unit_matrix(self) -> None:
        result = correlation_matrix(_frame(3, ("A",), lambda n, c, d: c))
        assert result.factors == ["A"]
        assert result.matrix == [[1.0]]


class TestDateHandling:
    def test_single_date_range_works(self) -> None:
        result = correlation_matrix(_frame(1, ("A", "B"), lambda n, c, d: c if n == "A" else -c))
        assert len(result.dates) == 1
        assert result.skipped_dates == 0
        i, j = result.factors.index("A"), result.factors.index("B")
        assert _pair(result.matrix, i, j) == pytest.approx(-1.0)

    def test_all_dates_counted(self) -> None:
        result = correlation_matrix(_frame(4, ("A", "B"), lambda n, c, d: c + d))
        assert len(result.dates) == 4
        assert result.skipped_dates == 0

    def test_thin_dates_are_skipped(self) -> None:
        """Days 0-1 carry only 5 stocks; days 2-3 carry all 20."""
        frame = _frame(4, ("A", "B"), lambda n, c, d: c + d)
        thin = set(CODES[5:])
        frame = frame[~frame["ts_code"].isin(thin) | (frame["trade_date"] > START + timedelta(days=1))]

        result = correlation_matrix(frame)
        assert result.skipped_dates == 2
        assert result.dates == [START + timedelta(days=2), START + timedelta(days=3)]

    def test_all_dates_skipped_returns_empty_matrix(self) -> None:
        frame = _frame(3, ("A", "B"), lambda n, c, d: c + d)
        frame = frame[frame["ts_code"] < "000004.SZ"]
        result = correlation_matrix(frame)
        assert isinstance(result, CorrelationResult)
        assert result.matrix == []
        assert result.skipped_dates == 3

    def test_averages_across_dates(self) -> None:
        """Day 0 correlates at -1, day 1 at +1 — the mean must land between them."""
        rows = []
        for d in range(2):
            day = START + timedelta(days=d)
            sign = -1 if d == 0 else 1
            for c, code in enumerate(CODES):
                rows.append(("A", code, day, float(c)))
                rows.append(("B", code, day, float(c * sign)))
        result = correlation_matrix(pd.DataFrame(rows, columns=COLUMNS))
        i, j = result.factors.index("A"), result.factors.index("B")
        assert _pair(result.matrix, i, j) == pytest.approx(0.0)


class TestDegenerateInputs:
    def test_empty_frame(self) -> None:
        result = correlation_matrix(pd.DataFrame(columns=COLUMNS))
        assert result.matrix == []
        assert result.factors == []
        assert result.skipped_dates == 0

    def test_missing_values_do_not_break_the_matrix(self) -> None:
        frame = _frame(3, ("A", "B"), lambda n, c, d: c + d)
        frame.loc[frame.index[:15], "value"] = float("nan")
        result = correlation_matrix(frame)
        assert len(result.matrix) == 2
        assert result.factors == ["A", "B"]

    def test_constant_factor_yields_nan_off_diagonal(self) -> None:
        """Zero variance has no correlation; it must read as unknown, not as 0."""
        result = correlation_matrix(_frame(3, ("A", "B"), lambda n, c, d: 1.0 if n == "A" else c))
        i, j = result.factors.index("A"), result.factors.index("B")
        assert np.isnan(_pair(result.matrix, i, j))
