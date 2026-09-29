"""Tests for the factor quantile (layered) backtest."""

import inspect
from datetime import date, timedelta

import pandas as pd
import pytest

from quant_trade.factors import quantile as quantile_module
from quant_trade.factors.quantile import QuantileResult, quantile_backtest

START = date(2024, 1, 1)
CODES = [f"{i:06d}.SZ" for i in range(1, 21)]
FWD_COLUMNS = ["trade_date", "ts_code", "fwd_ret"]
VAL_COLUMNS = ["trade_date", "ts_code", "value"]


def _factor(days: int, value_of: object) -> pd.DataFrame:
    rows = []
    for i in range(days):
        day = START + timedelta(days=i)
        for index, code in enumerate(CODES):
            rows.append((day, code, value_of(index, i) if callable(value_of) else value_of))
    return pd.DataFrame(rows, columns=VAL_COLUMNS)


def _returns(days: int, ret_of: object) -> pd.DataFrame:
    rows = []
    for i in range(days):
        day = START + timedelta(days=i)
        for index, code in enumerate(CODES):
            rows.append((day, code, ret_of(index, i) if callable(ret_of) else ret_of))
    return pd.DataFrame(rows, columns=FWD_COLUMNS)


class TestGroupNav:
    def test_each_group_starts_at_one(self) -> None:
        result = quantile_backtest(_factor(5, lambda i, _d: float(i)), _returns(5, 0.01), n_groups=5)
        assert len(result.groups) == 5
        for group in result.groups:
            assert group.values[0] == pytest.approx(1.0)

    def test_group_names_are_ordered_low_to_high(self) -> None:
        result = quantile_backtest(_factor(3, lambda i, _d: float(i)), _returns(3, 0.01), n_groups=5)
        assert [group.name for group in result.groups] == ["Q1", "Q2", "Q3", "Q4", "Q5"]

    def test_dates_align_with_values(self) -> None:
        result = quantile_backtest(_factor(6, lambda i, _d: float(i)), _returns(6, 0.01), n_groups=5)
        for group in result.groups:
            assert len(group.dates) == len(group.values)
        assert len(result.rebalance_dates) == 6

    def test_perfect_factor_ranks_groups_monotonically(self) -> None:
        """When the factor ranks stocks exactly by their return, Q5 must beat Q1."""
        result = quantile_backtest(
            _factor(10, lambda i, _d: float(i)),
            _returns(10, lambda i, _d: (i - 9.5) / 200.0),
            n_groups=5,
        )
        levels = [group.values[-1] for group in result.groups]
        assert levels[-1] > 1.0 > levels[0]
        assert levels == sorted(levels)

    def test_group_count_is_configurable(self) -> None:
        result = quantile_backtest(_factor(4, lambda i, _d: float(i)), _returns(4, 0.01), n_groups=10)
        assert len(result.groups) == 10

    def test_nav_compounds_across_dates(self) -> None:
        """Every group sees the same return, so 5 periods of 1% ends at 1.01**5."""
        result = quantile_backtest(_factor(5, lambda i, _d: float(i)), _returns(5, 0.01), n_groups=5)
        assert result.groups[0].values[-1] == pytest.approx(1.01**5)


class TestLongShort:
    def test_long_short_is_top_minus_bottom(self) -> None:
        result = quantile_backtest(
            _factor(8, lambda i, _d: float(i)),
            _returns(8, lambda i, _d: i / 100.0),
            n_groups=5,
        )
        assert result.long_short is not None
        assert result.long_short.name == "long_short"
        assert result.long_short.values[-1] > 1.0

    def test_flat_factor_gives_flat_spread(self) -> None:
        """No dispersion in returns means no long-short gain."""
        result = quantile_backtest(_factor(5, lambda i, _d: float(i)), _returns(5, 0.0), n_groups=5)
        assert result.long_short is not None
        assert result.long_short.values[-1] == pytest.approx(1.0)

    def test_long_short_starts_at_one(self) -> None:
        result = quantile_backtest(_factor(5, lambda i, _d: float(i)), _returns(5, 0.01), n_groups=5)
        assert result.long_short is not None
        assert result.long_short.values[0] == pytest.approx(1.0)


class TestDegenerateInputs:
    def test_small_cross_section_is_skipped_and_counted(self) -> None:
        result = quantile_backtest(
            _factor(5, lambda i, _d: float(i)),
            _returns(5, 0.01),
            n_groups=25,
        )
        assert result.groups == []
        assert result.long_short is None
        assert result.skipped_dates == 5

    def test_partially_usable_range_reports_skips(self) -> None:
        """Days 0-4 have 20 names, days 5-9 have only 3 — too few for 5 groups."""
        factor = _factor(10, lambda i, _d: float(i))
        forward = _returns(10, 0.01)
        thin_dates = {START + timedelta(days=i) for i in range(5, 10)}
        factor = factor[~factor["trade_date"].isin(thin_dates) | (factor["ts_code"] < "000003.SZ")]
        result = quantile_backtest(factor, forward, n_groups=5)
        assert result.skipped_dates == 5
        assert result.rebalance_dates == [START + timedelta(days=i) for i in range(5)]

    def test_empty_inputs_return_empty_result(self) -> None:
        result = quantile_backtest(pd.DataFrame(columns=VAL_COLUMNS), pd.DataFrame(columns=FWD_COLUMNS))
        assert isinstance(result, QuantileResult)
        assert result.groups == []
        assert result.long_short is None
        assert result.skipped_dates == 0

    def test_no_overlapping_dates_returns_empty(self) -> None:
        factor = _factor(3, lambda i, _d: float(i))
        forward = _returns(3, 0.01)
        forward = forward.assign(trade_date=forward["trade_date"] + timedelta(days=365))
        result = quantile_backtest(factor, forward, n_groups=5)
        assert result.groups == []

    def test_rows_with_missing_values_are_dropped(self) -> None:
        factor = _factor(3, lambda i, _d: float(i))
        factor.loc[factor.index[:10], "value"] = float("nan")
        result = quantile_backtest(factor, _returns(3, 0.01), n_groups=5)
        assert len(result.groups) == 5

    def test_single_group_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least 2"):
            quantile_backtest(_factor(3, 0.5), _returns(3, 0.01), n_groups=1)


class TestStatisticsNotTrading:
    """The layered view must stay free of trading-engine concerns."""

    def test_does_not_reference_the_backtest_engine(self) -> None:
        """The docstring says why the engine is not used; the code must not reach for it."""
        body = inspect.getsource(quantile_module).replace(quantile_module.__doc__ or "", "")
        assert "quant_trade.backtest" not in body

    def test_exposes_no_cost_or_constraint_parameters(self) -> None:
        params = set(inspect.signature(quantile_backtest).parameters)
        assert params == {"factor_values", "forward_returns", "n_groups"}
