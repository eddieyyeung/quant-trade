"""Test DataStore.get_universe, including the kline-derived fallback."""

import tempfile
from datetime import date, timedelta
from pathlib import Path

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.strategies.registry import strategy_registry


def _build_db(db_path: str) -> DataStore:
    Path(db_path).unlink(missing_ok=True)
    conn = init_db(db_path)
    today = date.today()

    stocks = [
        ("600000.SH", "浦发银行", "main", False),
        ("600519.SH", "贵州茅台", "main", False),
        ("000001.SZ", "平安银行", "main", False),
        ("600666.SH", "*ST测试", "main", True),
    ]
    conn.executemany("INSERT INTO stock_basic (ts_code, name, market, is_st) VALUES (?, ?, ?, ?)", stocks)

    # kline within the 60-day window for 3 stocks (one of them ST)
    recent = today - timedelta(days=10)
    for code in ["600000.SH", "600519.SH", "600666.SH"]:
        conn.execute(
            "INSERT INTO daily_kline VALUES (?, ?, 10, 11, 9, 10.5, 1000, 10000, 1.0, 2.0)",
            [code, recent],
        )
    # 000001.SZ only has stale kline (outside window) — must be excluded
    stale = today - timedelta(days=200)
    conn.execute(
        "INSERT INTO daily_kline VALUES (?, ?, 10, 11, 9, 10.5, 1000, 10000, 1.0, 2.0)",
        ["000001.SZ", stale],
    )
    return DataStore(db_path)


class TestUniverseFallback:
    def test_kline_fallback_without_index_weights(self) -> None:
        """Empty index_weights → universe derived from recent kline, ST excluded."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_db(tmp + "/test.db")

            universe = store.get_universe(["000300.SH"], date.today())

            assert "600000.SH" in universe
            assert "600519.SH" in universe
            # ST excluded
            assert "600666.SH" not in universe
            # stale kline (outside 60-day window) excluded
            assert "000001.SZ" not in universe

    def test_index_weights_preferred_over_fallback(self) -> None:
        """When index weights exist, they win over the kline fallback."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_db(tmp + "/test.db")
            conn = store.conn
            conn.execute(
                "INSERT INTO index_weights VALUES (?, ?, ?, ?, ?)",
                ["000300.SH", "000001.SZ", 0.5, date(2020, 1, 1), None],
            )

            universe = store.get_universe(["000300.SH"], date.today())
            # index weights has only 000001.SZ (not ST) — even with stale kline it wins
            assert universe == ["000001.SZ"]

    def test_no_kline_no_index_returns_empty(self) -> None:
        """Both sources empty → empty universe."""
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_db(tmp + "/test.db")
            # wipe kline
            store.conn.execute("DELETE FROM daily_kline")

            universe = store.get_universe(["000300.SH"], date.today())
            assert universe == []


class TestUniverseOrderIsStable:
    """The returned order decides how tied factor scores break downstream.

    `SELECT DISTINCT` has no defined row order, so without an ORDER BY the same
    query could return the same codes in a different order each call — and a
    different order picks different stocks once scores tie.
    """

    def test_fallback_query_repeats_in_the_same_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_db(tmp + "/test.db")

            first = store.get_universe(["000300.SH"], date.today())
            second = store.get_universe(["000300.SH"], date.today())

            assert first == second
            assert first == sorted(first), "returns codes in ascending order"

    def test_index_constituent_query_repeats_in_the_same_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_db(tmp + "/test.db")
            for code in ["600519.SH", "600000.SH", "000001.SZ"]:
                store.conn.execute(
                    "INSERT INTO index_weights VALUES (?, ?, ?, ?, ?)",
                    ["000300.SH", code, 0.1, date(2020, 1, 1), None],
                )

            first = store.get_universe(["000300.SH"], date.today())
            second = store.get_universe(["000300.SH"], date.today())

            assert first == second
            assert first == ["000001.SZ", "600000.SH", "600519.SH"]

    def test_excluding_st_keeps_the_remaining_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _build_db(tmp + "/test.db")

            filtered = store.get_universe(["000300.SH"], date.today(), filter_st=True)
            unfiltered = store.get_universe(["000300.SH"], date.today(), filter_st=False)

            assert "600666.SH" in unfiltered, "the ST name is only gone from the filtered list"
            assert filtered == [c for c in unfiltered if c != "600666.SH"]

    def test_tied_scores_pick_the_same_names_every_run(self) -> None:
        """The regression this guards: identical factor inputs → identical picks.

        Every stock here has the same price history, so every factor score ties
        and the selection is decided purely by the order the universe arrived
        in. That is exactly the state real data falls into whenever most
        factors come back empty.

        This covers the consequence end to end; the two tests above cover the
        contract directly. All three were confirmed to fail with the `ORDER BY`
        clauses removed.
        """
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            Path(db_path).unlink(missing_ok=True)
            conn = init_db(db_path)

            codes = [f"{i:06d}.SZ" for i in range(1, 21)]
            conn.executemany(
                "INSERT INTO stock_basic (ts_code, name, industry, market, list_date, is_st) "
                "VALUES (?, ?, '制造', 'main', ?, FALSE)",
                [(code, f"股票{i}", date(2015, 1, 1)) for i, code in enumerate(codes)],
            )

            days = [date.today() - timedelta(days=offset) for offset in range(90, 0, -1)]
            conn.executemany("INSERT INTO trade_calendar VALUES (?, ?)", [(d, True) for d in days])
            conn.executemany(
                "INSERT INTO daily_kline VALUES (?, ?, 10, 11, 9, 10.5, 1000, 10000, 1.0, 2.0)",
                [(code, d) for code in codes for d in days],
            )
            conn.executemany(
                "INSERT INTO index_weights VALUES (?, ?, ?, ?, ?)",
                [("000300.SH", code, 0.05, date(2015, 1, 1), None) for code in codes],
            )

            store = DataStore(db_path)
            strategy = strategy_registry.get("factor_ranking", store=store)

            def picks() -> list[str]:
                universe = store.get_universe(["000300.SH"], date.today())
                return sorted(o.ts_code for o in strategy.generate_signals(date.today(), universe, store).orders)

            first, second = picks(), picks()

            assert first, "the strategy should select something from 20 tied candidates"
            assert first == second
