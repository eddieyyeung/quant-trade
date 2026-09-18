"""WalkForwardResult shape and per-window feature-importance aggregation."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant_trade.models import WalkForwardResult
from quant_trade.models.train import _aggregate_importance, _empty_importance

FACTORS = ["f1", "f2", "f3"]


def _window(*weights: float) -> np.ndarray:
    return np.asarray(weights, dtype=float)


class TestAggregateImportance:
    def test_columns(self) -> None:
        frame = _aggregate_importance(FACTORS, [_window(1, 2, 3)])
        assert list(frame.columns) == ["factor", "importance", "std"]

    def test_mean_across_windows(self) -> None:
        frame = _aggregate_importance(FACTORS, [_window(0.6, 0.3, 0.1), _window(0.4, 0.5, 0.1), _window(0.5, 0.1, 0.4)])
        by_factor = dict(zip(frame["factor"], frame["importance"], strict=True))
        assert by_factor["f1"] == pytest.approx(0.5)
        assert by_factor["f2"] == pytest.approx(0.3)

    def test_std_across_windows(self) -> None:
        frame = _aggregate_importance(FACTORS, [_window(0.6, 0.3, 0.1), _window(0.4, 0.5, 0.1), _window(0.5, 0.1, 0.4)])
        by_factor = dict(zip(frame["factor"], frame["std"], strict=True))
        assert by_factor["f2"] == pytest.approx(np.std([0.3, 0.5, 0.1]))

    def test_sorted_by_mean_descending(self) -> None:
        frame = _aggregate_importance(FACTORS, [_window(0.6, 0.3, 0.1), _window(0.4, 0.5, 0.1)])
        assert list(frame["factor"]) == ["f1", "f2", "f3"]
        means = list(frame["importance"])
        assert means == sorted(means, reverse=True)

    def test_single_window_has_zero_std(self) -> None:
        frame = _aggregate_importance(FACTORS, [_window(0.2, 0.5, 0.3)])
        assert (frame["std"] == 0).all()
        assert frame.iloc[0]["factor"] == "f2"

    def test_no_windows_returns_empty_frame(self) -> None:
        frame = _aggregate_importance(FACTORS, [])
        assert frame.empty
        assert list(frame.columns) == ["factor", "importance", "std"]

    def test_empty_helper_matches_aggregate_shape(self) -> None:
        assert list(_empty_importance().columns) == list(_aggregate_importance(FACTORS, []).columns)


class TestWalkForwardResultShape:
    def test_fields_are_named_not_positional(self) -> None:
        """The old tuple return forced callers to drop the feature matrix."""
        result = WalkForwardResult(
            predictions=pd.DataFrame({"ts_code": ["a"], "trade_date": [None], "score": [1.0]}),
            feature_matrix=pd.DataFrame({"f1": [0.1]}),
            feature_importance=_aggregate_importance(FACTORS, [_window(1, 2, 3)]),
            windows_trained=1,
        )
        assert not result.predictions.empty
        assert not result.feature_matrix.empty
        assert result.windows_trained == 1
        assert result.cancelled is False

    def test_construction_requires_all_three_frames(self) -> None:
        with pytest.raises(TypeError):
            WalkForwardResult()  # type: ignore[call-arg]

    def test_cancelled_defaults_to_false(self) -> None:
        result = WalkForwardResult(pd.DataFrame(), pd.DataFrame(), _empty_importance())
        assert result.cancelled is False
        assert result.windows_trained == 0
