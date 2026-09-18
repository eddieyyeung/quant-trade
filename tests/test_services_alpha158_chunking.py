"""Alpha158 service: chunked computation must equal one pass, and cancel between chunks."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

from quant_trade.config import AppConfig
from quant_trade.data.store import DataStore
from quant_trade.factors.alpha158 import compute_alpha158 as raw_compute
from quant_trade.factors.alpha158.storage import get_factor_values
from quant_trade.services import CancelToken, RunContext
from quant_trade.services.factors import ALPHA158_CHUNK_DAYS, Alpha158Params, compute_alpha158

CODES = ["000001.SZ", "600000.SH", "300001.SZ"]
START = date(2024, 1, 1)
N_DAYS = 200  # spans three 90-day chunks


@pytest.fixture
def ctx() -> Iterator[RunContext]:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "a.db")
        store = DataStore(db)
        rows = []
        for code in CODES:
            rng = np.random.default_rng(abs(hash(code)) % 2**32)
            closes = 10 + np.cumsum(rng.normal(0, 0.2, N_DAYS))
            for i in range(N_DAYS):
                day = START + timedelta(days=i)
                rows.append((code, day, closes[i], closes[i], closes[i], closes[i], 1e6, 1e7, None, None))
        store.conn.executemany("INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
        config = AppConfig()
        config.data.db_path = db
        yield RunContext(run_id="a158", config=config, store=store)


def _bounds() -> tuple[date, date]:
    return START, START + timedelta(days=N_DAYS - 1)


class TestEquivalence:
    def test_spanning_multiple_chunks(self, ctx: RunContext) -> None:
        """Chunked values must equal a single uninterrupted computation."""
        start, end = _bounds()
        assert (end - start).days + 1 > 2 * ALPHA158_CHUNK_DAYS, "fixture must span several chunks"

        result = compute_alpha158(Alpha158Params(start_date=start, end_date=end, universe=CODES), ctx)
        assert result.rows_saved > 0
        assert result.cancelled is False

        single = raw_compute(ctx.db, start, end, CODES)
        sampled = ["MA20", "KMID", "STD20"]
        single = single[single["factor_name"].isin(sampled)]
        stored = get_factor_values(ctx.db, sampled, CODES, start, end)
        assert len(stored) == len(single)

        merged = stored.merge(single, on=["factor_name", "ts_code", "trade_date"], suffixes=("_stored", "_single"))
        assert len(merged) == len(single)
        assert np.allclose(merged["value_stored"], merged["value_single"], equal_nan=True)

    def test_single_chunk_range_is_unchanged(self, ctx: RunContext) -> None:
        start = START
        end = START + timedelta(days=30)
        result = compute_alpha158(Alpha158Params(start_date=start, end_date=end, universe=CODES), ctx)
        single = raw_compute(ctx.db, start, end, CODES)
        assert result.rows_saved == len(single)
        assert result.factor_count == single["factor_name"].nunique()

    def test_reports_factor_count_across_chunks(self, ctx: RunContext) -> None:
        start, end = _bounds()
        result = compute_alpha158(Alpha158Params(start_date=start, end_date=end, universe=CODES), ctx)
        assert result.factor_count == 158

    def test_empty_range_saves_nothing(self, ctx: RunContext) -> None:
        start = date(2030, 1, 1)
        logs: list[str] = []
        ctx.log_sink = lambda message, level: logs.append(f"{level}:{message}")
        result = compute_alpha158(
            Alpha158Params(start_date=start, end_date=start + timedelta(days=10), universe=CODES), ctx
        )
        assert result.rows_saved == 0
        assert result.cancelled is False
        assert any("produced no values" in entry for entry in logs)


class TestCancellation:
    def test_cancel_between_chunks_keeps_earlier_rows(self, ctx: RunContext) -> None:
        start, end = _bounds()
        token = CancelToken()
        ctx.cancel_token = token

        # The chunk boundary is announced through progress, not log.
        def progress_sink(pct: float, message: str) -> None:
            if "(1/" in message:
                token.cancel()

        ctx.progress_sink = progress_sink
        result = compute_alpha158(Alpha158Params(start_date=start, end_date=end, universe=CODES), ctx)

        assert result.cancelled is True
        assert result.rows_saved > 0

        # The first chunk's dates are persisted; the last chunk's never got computed.
        first_chunk_end = start + timedelta(days=ALPHA158_CHUNK_DAYS - 1)
        assert not get_factor_values(ctx.db, ["MA20"], CODES, start, first_chunk_end).empty
        assert get_factor_values(ctx.db, ["MA20"], CODES, end, end).empty

    def test_cancelled_run_does_not_report_full_progress(self, ctx: RunContext) -> None:
        """A cancelled run did not finish; 100% would misreport it in the UI."""
        start, end = _bounds()
        token = CancelToken()
        ctx.cancel_token = token
        seen: list[float] = []

        def progress_sink(pct: float, message: str) -> None:
            seen.append(pct)
            if "(1/" in message:
                token.cancel()

        ctx.progress_sink = progress_sink
        result = compute_alpha158(Alpha158Params(start_date=start, end_date=end, universe=CODES), ctx)

        assert result.cancelled is True
        assert seen[-1] < 1.0, f"cancelled run reported {seen[-1]}"

    def test_progress_reaches_one_when_not_cancelled(self, ctx: RunContext) -> None:
        start, end = _bounds()
        seen: list[float] = []
        ctx.progress_sink = lambda pct, message: seen.append(pct)
        compute_alpha158(Alpha158Params(start_date=start, end_date=end, universe=CODES), ctx)
        assert seen[0] == 0.0
        assert max(seen) == pytest.approx(1.0)
