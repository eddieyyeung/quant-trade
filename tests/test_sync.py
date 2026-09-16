"""Test data sync functions with mocked adapters."""

import tempfile
from datetime import date

import pandas as pd

from quant_trade.data.schema import init_db
from quant_trade.data.store import DataStore
from quant_trade.data.sync import sync_index_daily


class _FakeIndexAdapter:
    """Adapter returning a fixed index-kline DataFrame, no network."""

    source_name = "fake"

    def __init__(self, df: pd.DataFrame) -> None:
        self._df = df

    def fetch_index_daily(self, index_codes: list[str]) -> pd.DataFrame:
        return self._df


class TestSyncIndexDaily:
    def test_writes_index_rows_with_zero_fills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = tmp + "/test.db"
            init_db(db_path)
            store = DataStore(db_path)

            df = pd.DataFrame(
                {
                    "ts_code": ["000300.SH", "000300.SH"],
                    "trade_date": [date(2024, 1, 2), date(2024, 1, 3)],
                    "open": [3500.0, 3510.0],
                    "high": [3520.0, 3530.0],
                    "low": [3490.0, 3500.0],
                    "close": [3515.0, 3525.0],
                    "volume": [1e8, 1.1e8],
                    "amount": [0.0, 0.0],
                    "pct_change": [0.0, 0.0],
                    "turn_rate": [0.0, 0.0],
                }
            )
            rows = sync_index_daily(store, _FakeIndexAdapter(df), ["000300.SH"])
            assert rows == 2

            result = store.conn.execute(
                "SELECT ts_code, trade_date, close, amount, pct_change, turn_rate FROM daily_kline ORDER BY trade_date"
            ).df()
            assert len(result) == 2
            assert result["ts_code"].tolist() == ["000300.SH", "000300.SH"]
            # indices carry no amount/pct_change/turn_rate — filled with 0.0
            assert (result["amount"] == 0.0).all()
            assert (result["pct_change"] == 0.0).all()
            assert (result["turn_rate"] == 0.0).all()
            assert result["close"].tolist() == [3515.0, 3525.0]

    def test_empty_codes_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DataStore(tmp + "/test.db")
            assert sync_index_daily(store, _FakeIndexAdapter(pd.DataFrame()), []) == 0

    def test_unsupported_adapter_returns_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DataStore(tmp + "/test.db")

            class _NoIndexSupport:
                source_name = "no-support"

            assert sync_index_daily(store, _NoIndexSupport(), ["000300.SH"]) == 0
