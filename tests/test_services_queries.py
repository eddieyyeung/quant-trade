"""Query services: table metadata, factor names, and universe coverage."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from quant_trade.config import AppConfig
from quant_trade.data.schema import init_db
from quant_trade.data.store import TABLE_NAMES, DataStore
from quant_trade.services import RunContext
from quant_trade.services.queries import (
    FactorNamesParams,
    UniverseCoverageParams,
    list_factor_names,
    universe_coverage,
)

CODES = ["000001.SZ", "600000.SH", "300001.SZ"]


@pytest.fixture
def ctx() -> Iterator[RunContext]:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "q.db")
        conn = init_db(db)
        conn.execute("INSERT INTO trade_calendar VALUES (?, TRUE)", [date(2024, 3, 1)])
        conn.executemany(
            "INSERT INTO stock_basic (ts_code, name, industry, market, list_date) VALUES (?,?,?,?,?)",
            [(c, f"s{c}", "ind", "main", date(2020, 1, 1)) for c in CODES],
        )
        conn.executemany(
            "INSERT INTO daily_kline VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(c, date(2024, 3, 1), 1.0, 1.0, 1.0, 1.0, 1e6, 1e7, None, None) for c in CODES[:2]],
        )
        conn.executemany(
            "INSERT INTO factor_values VALUES (?,?,?,?)",
            [
                ("alpha001", CODES[0], date(2024, 3, 1), 0.5),
                ("alpha002", CODES[0], date(2024, 3, 1), 0.7),
                ("alpha001", CODES[1], date(2024, 3, 1), 0.2),
            ],
        )

        config = AppConfig()
        config.data.db_path = db
        yield RunContext(run_id="q", config=config, store=DataStore(db))


class TestTableStats:
    def test_counts_and_date_span(self, ctx: RunContext) -> None:
        stats = ctx.db.table_stats("daily_kline")
        assert stats.rows == 2
        assert stats.earliest == date(2024, 3, 1)
        assert stats.latest == date(2024, 3, 1)

    def test_empty_table_has_no_span(self, ctx: RunContext) -> None:
        stats = ctx.db.table_stats("financials")
        assert stats.rows == 0
        assert stats.earliest is None
        assert stats.latest is None

    @pytest.mark.parametrize("bad", ["", "nope", "daily_kline; DROP TABLE daily_kline", "../etc/passwd"])
    def test_rejects_names_outside_the_allowlist(self, ctx: RunContext, bad: str) -> None:
        with pytest.raises(ValueError, match="Unknown table"):
            ctx.db.table_stats(bad)

    def test_table_survives_a_rejected_probe(self, ctx: RunContext) -> None:
        """A rejected name must not have executed anything."""
        with pytest.raises(ValueError):
            ctx.db.table_stats("daily_kline; DROP TABLE daily_kline")
        assert ctx.db.table_stats("daily_kline").rows == 2

    def test_all_table_stats_covers_every_allowlisted_table(self, ctx: RunContext) -> None:
        assert set(ctx.db.all_table_stats()) == set(TABLE_NAMES)


class TestListFactorNames:
    def test_returns_sorted_distinct_names(self, ctx: RunContext) -> None:
        assert list_factor_names(FactorNamesParams(), ctx) == ["alpha001", "alpha002"]

    def test_empty_when_nothing_persisted(self, ctx: RunContext) -> None:
        ctx.db.conn.execute("DELETE FROM factor_values")
        assert list_factor_names(FactorNamesParams(), ctx) == []


class TestUniverseCoverage:
    def test_reports_missing_codes(self, ctx: RunContext) -> None:
        coverage = universe_coverage(UniverseCoverageParams(universe=CODES), ctx)
        assert coverage.universe_size == 3
        assert coverage.covered == 2
        assert coverage.missing == ["300001.SZ"]
        assert coverage.ratio == pytest.approx(2 / 3)

    def test_full_coverage(self, ctx: RunContext) -> None:
        coverage = universe_coverage(UniverseCoverageParams(universe=CODES[:2]), ctx)
        assert coverage.missing == []
        assert coverage.ratio == 1.0

    def test_empty_universe(self, ctx: RunContext) -> None:
        coverage = universe_coverage(UniverseCoverageParams(universe=[]), ctx)
        assert coverage.universe_size == 0
        assert coverage.ratio == 0.0
