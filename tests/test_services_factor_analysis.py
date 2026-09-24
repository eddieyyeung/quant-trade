"""Factor-analysis services: IC persistence, layered backtest, correlation, coverage."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from quant_trade.config import AppConfig
from quant_trade.data.store import DataStore
from quant_trade.factors.alpha158.storage import save_factor_values
from quant_trade.factors.ic_store import get_ic_series
from quant_trade.services import CancelToken, RunContext
from quant_trade.services.factor_analysis import (
    MAX_CORRELATION_FACTORS,
    FactorCorrelationParams,
    FactorCoverageParams,
    FactorICComputeParams,
    FactorQuantileParams,
    compute_factor_ic,
    factor_correlation,
    factor_coverage,
    factor_quantile_backtest,
)

START = date(2024, 1, 1)
N_DAYS = 80
CODES = [f"{i:06d}.SZ" for i in range(1, 21)]
FACTORS = ["MA20", "STD20", "KMID"]


class _CountingConn:
    """Counts round-trips so a test can assert query count is independent of input size."""

    def __init__(self, conn: object) -> None:
        self._real = conn
        self.calls = 0

    def execute(self, *args: object, **kwargs: object) -> object:
        self.calls += 1
        return self._real.execute(*args, **kwargs)  # type: ignore[attr-defined]


class _CountingStore:
    """DataStore proxy whose ``conn`` counts round-trips.

    ``get_daily`` is overridden because delegating through ``__getattr__`` would
    run it on the real store's connection, leaving that query uncounted.
    """

    def __init__(self, store: DataStore) -> None:
        self._store = store
        self.conn = _CountingConn(store.conn)

    def get_daily(self, *args: object, **kwargs: object) -> pd.DataFrame:
        self.conn.calls += 1
        return self._store.get_daily(*args, **kwargs)  # type: ignore[arg-type]

    def __getattr__(self, name: str) -> object:
        return getattr(self._store, name)


def _populate(store: DataStore) -> None:
    kline, values = [], []
    for code in CODES:
        rng = np.random.default_rng(abs(hash(code)) % 2**32)
        closes = 10 + np.cumsum(rng.normal(0, 0.2, N_DAYS))
        for i in range(N_DAYS):
            day = START + timedelta(days=i)
            kline.append((code, day, closes[i], closes[i], closes[i], closes[i], 1e6, 1e7, None, None))
            for name in FACTORS:
                values.append((name, code, day, float(rng.normal())))
    store.conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)", kline)
    store.conn.executemany(
        "INSERT INTO trade_calendar VALUES (?, TRUE)",
        [(START + timedelta(days=i),) for i in range(N_DAYS)],
    )
    save_factor_values(
        store,
        pd.DataFrame(values, columns=["factor_name", "ts_code", "trade_date", "value"]),
    )


@pytest.fixture
def ctx() -> Iterator[RunContext]:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "f.db")
        store = DataStore(db)
        _populate(store)
        config = AppConfig()
        config.data.db_path = db
        yield RunContext(run_id="fa", config=config, store=store)


class TestComputeFactorIC:
    def test_persists_ic_rows(self, ctx: RunContext) -> None:
        result = compute_factor_ic(FactorICComputeParams(factors=["MA20"], universe=CODES), ctx)
        assert result.rows_saved > 0
        assert result.completed_factors == 1

        stored = get_ic_series(ctx.db, ["MA20"], START, START + timedelta(days=N_DAYS), forward_period=5)
        assert not stored.empty
        assert stored["sample_size"].min() >= 10

    def test_every_holding_period_is_stored(self, ctx: RunContext) -> None:
        compute_factor_ic(FactorICComputeParams(factors=["MA20"], universe=CODES, forward_periods=[1, 5, 20]), ctx)
        stored = get_ic_series(ctx.db, ["MA20"], START, START + timedelta(days=N_DAYS))
        assert sorted(stored["forward_period"].unique()) == [1, 5, 20]

    def test_unknown_factor_warns_and_saves_nothing(self, ctx: RunContext) -> None:
        """A real factor alongside an unknown one: the unknown one logs and is skipped."""
        logs: list[str] = []
        ctx.log_sink = lambda message, level: logs.append(f"{level}:{message}")
        result = compute_factor_ic(FactorICComputeParams(factors=["MA20", "NOPE"], universe=CODES), ctx)
        assert result.completed_factors == 1
        assert any("produced no IC rows" in entry for entry in logs)

    def test_range_without_any_values_warns_once(self, ctx: RunContext) -> None:
        logs: list[str] = []
        ctx.log_sink = lambda message, level: logs.append(f"{level}:{message}")
        result = compute_factor_ic(
            FactorICComputeParams(
                factors=["MA20"],
                universe=CODES,
                start_date=date(2030, 1, 1),
                end_date=date(2030, 2, 1),
            ),
            ctx,
        )
        assert result.rows_saved == 0
        assert any("No factor values" in entry for entry in logs)

    def test_reports_progress(self, ctx: RunContext) -> None:
        seen: list[tuple[float, str]] = []
        ctx.progress_sink = lambda pct, message: seen.append((pct, message))
        compute_factor_ic(FactorICComputeParams(factors=["MA20", "STD20"], universe=CODES), ctx)
        assert seen[0][0] == 0.0
        assert max(pct for pct, _ in seen) == pytest.approx(1.0)

    def test_cancel_keeps_finished_factors(self, ctx: RunContext) -> None:
        token = CancelToken()
        ctx.cancel_token = token

        def log_sink(message: str, level: str) -> None:
            if message.startswith("Computed IC for MA20"):
                token.cancel()

        ctx.log_sink = log_sink
        result = compute_factor_ic(FactorICComputeParams(factors=["MA20", "STD20"], universe=CODES), ctx)

        assert result.cancelled is True
        assert result.completed_factors == 1
        # The finished factor's rows survive the cancellation.
        assert not get_ic_series(ctx.db, ["MA20"], START, START + timedelta(days=N_DAYS)).empty
        assert get_ic_series(ctx.db, ["STD20"], START, START + timedelta(days=N_DAYS)).empty

    def test_range_without_values_returns_empty(self, ctx: RunContext) -> None:
        result = compute_factor_ic(
            FactorICComputeParams(
                factors=["MA20"],
                universe=CODES,
                start_date=date(2030, 1, 1),
                end_date=date(2030, 2, 1),
            ),
            ctx,
        )
        assert result.rows_saved == 0

    def test_requires_at_least_one_factor(self) -> None:
        with pytest.raises(ValidationError):
            FactorICComputeParams(factors=[])


class TestQuantileBacktest:
    def test_returns_group_and_long_short_series(self, ctx: RunContext) -> None:
        result = factor_quantile_backtest(FactorQuantileParams(factor="MA20", universe=CODES), ctx)
        assert len(result.series) == 5
        assert result.long_short is not None
        assert result.rebalance_dates
        for series in result.series:
            assert series.values[0] == pytest.approx(1.0)

    def test_group_count_is_configurable(self, ctx: RunContext) -> None:
        result = factor_quantile_backtest(FactorQuantileParams(factor="MA20", universe=CODES, n_groups=10), ctx)
        assert len(result.series) == 10

    def test_unknown_factor_returns_empty_with_warning(self, ctx: RunContext) -> None:
        logs: list[str] = []
        ctx.log_sink = lambda message, level: logs.append(f"{level}:{message}")
        result = factor_quantile_backtest(FactorQuantileParams(factor="NOPE", universe=CODES), ctx)
        assert result.series == []
        assert result.long_short is None
        assert any("no values" in entry for entry in logs)

    def test_query_count_does_not_grow_with_range(self, ctx: RunContext) -> None:
        """One query for factor values plus one for kline, whatever the window."""
        store = _CountingStore(ctx.db)
        ctx.store = store  # type: ignore[assignment]

        factor_quantile_backtest(FactorQuantileParams(factor="MA20", universe=CODES), ctx)
        short = store.conn.calls

        store.conn.calls = 0
        factor_quantile_backtest(
            FactorQuantileParams(
                factor="MA20", universe=CODES, start_date=date(2024, 1, 20), end_date=date(2024, 3, 10)
            ),
            ctx,
        )
        assert short == store.conn.calls == 2


class TestCorrelation:
    def test_returns_square_matrix(self, ctx: RunContext) -> None:
        result = factor_correlation(FactorCorrelationParams(factors=["MA20", "STD20"], universe=CODES), ctx)
        assert result.factors == ["MA20", "STD20"]
        assert len(result.matrix) == 2
        assert result.date_count > 0

    def test_factor_count_limit_is_enforced_before_any_computation(self, ctx: RunContext) -> None:
        with pytest.raises(ValidationError):
            FactorCorrelationParams(factors=[f"F{i}" for i in range(MAX_CORRELATION_FACTORS + 1)], universe=CODES)

    def test_exactly_at_the_limit_is_accepted(self) -> None:
        params = FactorCorrelationParams(factors=[f"F{i}" for i in range(MAX_CORRELATION_FACTORS)], universe=CODES)
        assert len(params.factors) == MAX_CORRELATION_FACTORS

    def test_unscorable_pair_is_null_not_nan(self, ctx: RunContext) -> None:
        """A zero-variance factor has no correlation; NaN would be invalid JSON."""
        import json

        ctx.db.conn.execute("DELETE FROM factor_values WHERE factor_name = 'MA20'")
        # Re-add MA20 as a constant, so its cross-section has zero variance.
        ctx.db.conn.execute(
            "INSERT INTO factor_values SELECT 'MA20', ts_code, trade_date, 1.0 "
            "FROM factor_values WHERE factor_name = 'STD20'"
        )
        result = factor_correlation(FactorCorrelationParams(factors=["MA20", "STD20"], universe=CODES), ctx)
        # Starlette renders with allow_nan=False, so this is what the API would do.
        json.dumps(result.matrix, allow_nan=False)
        assert result.matrix[0][1] is None

    def test_requires_at_least_two_factors(self) -> None:
        with pytest.raises(ValidationError):
            FactorCorrelationParams(factors=["MA20"])

    def test_query_count_does_not_grow_with_factors_or_range(self, ctx: RunContext) -> None:
        store = _CountingStore(ctx.db)
        ctx.store = store  # type: ignore[assignment]

        factor_correlation(FactorCorrelationParams(factors=["MA20", "STD20"], universe=CODES), ctx)
        small = store.conn.calls

        store.conn.calls = 0
        factor_correlation(
            FactorCorrelationParams(
                factors=FACTORS, universe=CODES, start_date=date(2024, 1, 20), end_date=date(2024, 3, 10)
            ),
            ctx,
        )
        assert small == store.conn.calls

    def test_no_usable_date_warns_and_returns_empty(self, ctx: RunContext) -> None:
        """Every cross-section thinner than the minimum sample: warn, do not raise."""
        logs: list[str] = []
        ctx.log_sink = lambda message, level: logs.append(f"{level}:{message}")
        ctx.db.conn.execute("DELETE FROM factor_values WHERE ts_code > '000003.SZ'")

        result = factor_correlation(FactorCorrelationParams(factors=["MA20", "STD20"], universe=CODES), ctx)

        assert result.matrix == []
        assert result.date_count == 0
        assert any("cross-section wide enough" in entry for entry in logs)


class TestCoverage:
    def test_reports_persisted_factors(self, ctx: RunContext) -> None:
        result = factor_coverage(FactorCoverageParams(), ctx)
        names = {entry.name for entry in result.entries}
        assert names == set(FACTORS)
        entry = next(entry for entry in result.entries if entry.name == "MA20")
        assert entry.rows == len(CODES) * N_DAYS
        assert entry.earliest == START
        assert entry.latest == START + timedelta(days=N_DAYS - 1)

    def test_unpersisted_factor_is_absent_not_zero(self, ctx: RunContext) -> None:
        ctx.db.conn.execute("DELETE FROM factor_values WHERE factor_name = 'KMID'")
        result = factor_coverage(FactorCoverageParams(), ctx)
        assert "KMID" not in {entry.name for entry in result.entries}


class TestParams:
    def test_round_trip_through_json(self) -> None:
        for params in (
            FactorICComputeParams(factors=["MA20"], universe=CODES, forward_periods=[5]),
            FactorQuantileParams(factor="MA20", n_groups=10),
            FactorCorrelationParams(factors=["MA20", "STD20"]),
            FactorCoverageParams(),
        ):
            restored = type(params).model_validate_json(params.model_dump_json())
            assert restored == params

    def test_inverted_range_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not be after"):
            FactorQuantileParams(factor="MA20", start_date=date(2024, 5, 1), end_date=date(2024, 1, 1))

    def test_unknown_field_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FactorQuantileParams(factor="MA20", n_group=5)  # type: ignore[call-arg]
