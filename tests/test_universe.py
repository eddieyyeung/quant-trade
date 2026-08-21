"""Test DataStore.get_universe, including the kline-derived fallback."""

import tempfile
from datetime import date, timedelta
from pathlib import Path

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore


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
