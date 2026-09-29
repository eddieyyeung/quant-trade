"""Tests for batch IC computation."""

import inspect
import tempfile
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.factors.alpha158.storage import get_factor_values, save_factor_values
from quant_trade.factors.analysis import compute_ic_frame, compute_ic_series
from quant_trade.factors.ic_store import IC_COLUMNS

CODES = [f"{i:06d}.SZ" for i in range(1, 21)]
START = date(2024, 1, 1)
N_DAYS = 80
FACTORS = ("MA20", "STD20")


def _build(db_path: str) -> DataStore:
    """20 stocks over 80 consecutive days, plus two persisted factors."""
    conn = init_db(db_path)
    kline, values = [], []
    for code in CODES:
        rng = np.random.default_rng(abs(hash(code)) % 2**32)
        closes = 10 + np.cumsum(rng.normal(0, 0.2, N_DAYS))
        for i in range(N_DAYS):
            day = START + timedelta(days=i)
            kline.append((code, day, closes[i], closes[i], closes[i], closes[i], 1e6, 1e7, None, None))
            values.append(("MA20", code, day, float(rng.normal())))
            values.append(("STD20", code, day, float(rng.normal())))
    conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)", kline)
    store = DataStore(db_path)
    save_factor_values(
        store,
        pd.DataFrame(values, columns=["factor_name", "ts_code", "trade_date", "value"]),
    )
    return store


def _trading_days(n: int, offset: int = 30) -> list[date]:
    return [START + timedelta(days=offset + i) for i in range(n)]


class _CountingConn:
    """Counts round-trips so a test can assert query count is independent of range."""

    def __init__(self, conn: object) -> None:
        self._conn = conn
        self.calls = 0

    def execute(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        return self._conn.execute(*args, **kwargs)  # type: ignore[attr-defined]


class _CountingStore:
    """DataStore proxy that counts both direct queries and connection round-trips."""

    def __init__(self, store: DataStore) -> None:
        self._store = store
        self.conn = _CountingConn(store.conn)

    def get_daily(self, *args: object, **kwargs: object) -> pd.DataFrame:
        self.conn.calls += 1
        return self._store.get_daily(*args, **kwargs)  # type: ignore[arg-type]


class TestQueryCount:
    def test_two_queries_regardless_of_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build(tmp + "/a.db")
            counting = _CountingStore(store)
            short = compute_ic_frame(counting, ["MA20"], CODES, _trading_days(3), [5])  # type: ignore[arg-type]
            short_calls = counting.conn.calls

            counting.conn.calls = 0
            long = compute_ic_frame(counting, ["MA20"], CODES, _trading_days(40), [5])  # type: ignore[arg-type]
            long_calls = counting.conn.calls

            assert not short.empty and not long.empty
            assert short_calls == long_calls == 2, "one factor-values query + one kline query"

    def test_query_count_independent_of_factor_and_period_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build(tmp + "/a.db")
            counting = _CountingStore(store)
            compute_ic_frame(counting, list(FACTORS), CODES, _trading_days(10), [1, 5, 20])  # type: ignore[arg-type]
            assert counting.conn.calls == 2


class TestFrameShape:
    def test_one_row_per_factor_date_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build(tmp + "/a.db")
            days = _trading_days(5)
            out = compute_ic_frame(store, ["MA20"], CODES, days, [5])
            assert list(out.columns) == IC_COLUMNS
            assert out["factor_name"].unique().tolist() == ["MA20"]
            assert out["forward_period"].unique().tolist() == [5]
            assert len(out) == len(days)

    def test_multiple_factors_and_periods(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build(tmp + "/a.db")
            days = _trading_days(4)
            out = compute_ic_frame(store, list(FACTORS), CODES, days, [1, 5, 20])
            assert sorted(out["factor_name"].unique()) == ["MA20", "STD20"]
            assert sorted(out["forward_period"].unique()) == [1, 5, 20]
            assert len(out) == 2 * 3 * len(days)

    def test_values_are_coefficients_and_sample_size_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build(tmp + "/a.db")
            out = compute_ic_frame(store, ["MA20"], CODES, _trading_days(5), [5])
            assert out["ic"].between(-1, 1).all()
            assert out["rank_ic"].between(-1, 1).all()
            assert out["sample_size"].between(10, len(CODES)).all()

    def test_sorted_by_factor_date_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build(tmp + "/a.db")
            days = _trading_days(4)
            out = compute_ic_frame(store, list(FACTORS), CODES, days, [1, 5])
            keyed = list(zip(out["factor_name"], out["trade_date"], out["forward_period"], strict=True))
            assert keyed == sorted(keyed)


class TestDegenerateInputs:
    def test_no_factors_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build(tmp + "/a.db")
            out = compute_ic_frame(store, [], CODES, _trading_days(5), [5])
            assert out.empty
            assert list(out.columns) == IC_COLUMNS

    def test_unknown_factor_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build(tmp + "/a.db")
            out = compute_ic_frame(store, ["NOPE"], CODES, _trading_days(5), [5])
            assert out.empty

    def test_no_universe_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build(tmp + "/a.db")
            assert compute_ic_frame(store, ["MA20"], [], _trading_days(5), [5]).empty

    def test_no_dates_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build(tmp + "/a.db")
            assert compute_ic_frame(store, ["MA20"], CODES, [], [5]).empty

    def test_dates_without_factor_values_are_skipped(self) -> None:
        """A date the factor has no values for is omitted, not emitted as NaN."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _build(tmp + "/a.db")
            days = [*_trading_days(3), START + timedelta(days=N_DAYS + 5)]
            out = compute_ic_frame(store, ["MA20"], CODES, days, [5])
            assert len(out) == 3
            assert (out["trade_date"].dt.date == days[-1]).sum() == 0

    def test_thin_cross_section_is_dropped(self) -> None:
        """Fewer than 10 usable stocks cannot produce a coefficient."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _build(tmp + "/a.db")
            out = compute_ic_frame(store, ["MA20"], CODES[:5], _trading_days(5), [5])
            assert out.empty


class TestMatchesPerDatePath:
    """The batch path must agree with the per-date path day by day."""

    def test_ic_and_rank_ic_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build(tmp + "/a.db")
            days = _trading_days(15)

            def compute_one(day: date, universe: list[str]) -> pd.Series:
                frame = get_factor_values(store, ["MA20"], universe, day, day)
                return frame.set_index("ts_code")["value"]

            per_date = compute_ic_series(store, "MA20", compute_one, CODES, days, forward_period=5)
            batch = compute_ic_frame(store, ["MA20"], CODES, days, [5])
            by_date = batch.set_index("trade_date")

            for day, ic in per_date["ic_series"]:
                assert by_date.loc[pd.Timestamp(day), "ic"] == pytest.approx(ic)
            for day, rank_ic in per_date["rank_ic_series"]:
                assert by_date.loc[pd.Timestamp(day), "rank_ic"] == pytest.approx(rank_ic)
            assert len(per_date["ic_series"]) == len(batch)

    def test_matches_for_longer_period(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build(tmp + "/a.db")
            days = _trading_days(10)

            def compute_one(day: date, universe: list[str]) -> pd.Series:
                frame = get_factor_values(store, ["MA20"], universe, day, day)
                return frame.set_index("ts_code")["value"]

            per_date = compute_ic_series(store, "MA20", compute_one, CODES, days, forward_period=20)
            batch = compute_ic_frame(store, ["MA20"], CODES, days, [20]).set_index("trade_date")
            for day, ic in per_date["ic_series"]:
                assert batch.loc[pd.Timestamp(day), "ic"] == pytest.approx(ic)


class TestPerDateContractUnchanged:
    def test_signature_is_stable(self) -> None:
        params = list(inspect.signature(compute_ic_series).parameters)
        assert params == ["store", "factor_name", "factor_compute_fn", "universe", "dates", "forward_period"]

    def test_result_keys_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build(tmp + "/a.db")
            result = compute_ic_series(
                store,
                "MA20",
                lambda day, universe: get_factor_values(store, ["MA20"], universe, day, day).set_index("ts_code")[
                    "value"
                ],
                CODES,
                _trading_days(3),
                forward_period=5,
            )
            assert set(result) == {
                "ic_mean",
                "ic_std",
                "ic_ir",
                "ic_positive_ratio",
                "ic_series",
                "rank_ic_series",
            }

    def test_empty_dates_returns_nan_scalars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build(tmp + "/a.db")
            result = compute_ic_series(store, "MA20", lambda d, u: pd.Series(dtype=float), CODES, [])
            assert np.isnan(result["ic_mean"])
            assert result["ic_series"] == []
