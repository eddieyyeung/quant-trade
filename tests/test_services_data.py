"""Data-domain service: sync orchestration and status reporting."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

from quant_trade.config import AppConfig
from quant_trade.data.store import DataStore
from quant_trade.services import RunContext
from quant_trade.services import data as data_service
from quant_trade.services.data import (
    DEFAULT_HISTORY_START,
    DataStatusParams,
    DataSyncParams,
    _resolve_start,
    data_status,
    sync_market_data,
)


class _FakeAdapter:
    """Stands in for AkshareAdapter so no network call happens."""

    source_name = "fake"

    def fetch_stock_basic(self) -> pd.DataFrame:
        return pd.DataFrame()


def _adapter_with(codes: list[str]) -> type:
    """An adapter whose stock_basic listing returns ``codes``.

    Used to drive the universe fallback, which is the only path that gives the
    batch loop something to iterate over on an otherwise empty database.
    """

    class _ListingAdapter:
        source_name = "fake"

        def fetch_stock_basic(self) -> pd.DataFrame:
            return pd.DataFrame({"ts_code": codes})

    return _ListingAdapter


def _ctx(tmp: str, store: DataStore) -> RunContext:
    config = AppConfig()
    config.data.db_path = str(Path(tmp) / "test.db")
    config.data.backup_sources = []
    return RunContext(run_id="sync", config=config, store=store)


class TestSyncWiresIndexKline:
    def test_sync_market_data_syncs_index_daily(self, monkeypatch) -> None:
        """The index kline step runs for the default indices, after index weights."""
        with tempfile.TemporaryDirectory() as tmp:
            store = DataStore(str(Path(tmp) / "test.db"))
            calls: dict[str, list] = {"index_daily": [], "index_weights": []}

            monkeypatch.setattr(data_service, "AkshareAdapter", _FakeAdapter)
            monkeypatch.setattr(data_service, "sync_all", lambda *a, **kw: {})
            monkeypatch.setattr(
                data_service,
                "sync_index_weights",
                lambda s, indices, d, adapter: calls["index_weights"].append(list(indices)),
            )
            monkeypatch.setattr(
                data_service,
                "sync_index_daily",
                lambda s, adapter, indices: calls["index_daily"].append(list(indices)) or 0,
            )
            monkeypatch.setattr(data_service, "sync_daily_kline", lambda *a, **kw: 0)

            result = sync_market_data(
                DataSyncParams(start_date=date(2024, 1, 1), end_date=date(2024, 1, 10)),
                _ctx(tmp, store),
            )

            assert calls["index_daily"] == [["000300.SH", "000905.SH"]]
            assert calls["index_weights"] == [["000300.SH", "000905.SH"]]
            assert result.cancelled is False
            assert result.universe_size == 0

    def test_cancellation_before_the_first_batch_stops_early(self, monkeypatch) -> None:
        """A run cancelled up front must not fetch anything."""
        from quant_trade.services import CancelToken

        with tempfile.TemporaryDirectory() as tmp:
            store = DataStore(str(Path(tmp) / "test.db"))
            fetched: list[list[str]] = []

            monkeypatch.setattr(data_service, "AkshareAdapter", _adapter_with(["600000.SH"]))
            monkeypatch.setattr(data_service, "sync_all", lambda *a, **kw: {})
            monkeypatch.setattr(data_service, "sync_index_weights", lambda *a, **kw: None)
            monkeypatch.setattr(data_service, "sync_index_daily", lambda *a, **kw: 0)
            monkeypatch.setattr(
                data_service,
                "sync_daily_kline",
                lambda s, a, codes, start, end, ctx=None: fetched.append(list(codes)) or 0,
            )

            token = CancelToken()
            token.cancel()
            ctx = _ctx(tmp, store)
            ctx.cancel_token = token

            result = sync_market_data(DataSyncParams(start_date=date(2024, 1, 1)), ctx)

            assert result.cancelled is True
            assert fetched == []

    def test_cancellation_mid_loop_returns_a_partial_result(self, monkeypatch) -> None:
        """Cancel from *inside* a batch: the loop must stop and report the remainder.

        This is the checkpoint that matters — a token cancelled before the loop
        is caught by the earlier early-return, and would pass even if the loop
        never checked ``cancelled()`` at all.
        """
        from quant_trade.services import CancelToken

        with tempfile.TemporaryDirectory() as tmp:
            store = DataStore(str(Path(tmp) / "test.db"))
            codes = [f"{600000 + i:06d}.SH" for i in range(6)]
            fetched: list[list[str]] = []
            token = CancelToken()

            monkeypatch.setattr(data_service, "AkshareAdapter", _adapter_with(codes))
            monkeypatch.setattr(data_service, "sync_all", lambda *a, **kw: {})
            monkeypatch.setattr(data_service, "sync_index_weights", lambda *a, **kw: None)
            monkeypatch.setattr(data_service, "sync_index_daily", lambda *a, **kw: 0)
            monkeypatch.setattr(data_service, "BATCH_SIZE", 2)

            def _fetch(_s, _a, batch, _start, _end, ctx=None):  # noqa: ANN001, ANN202
                fetched.append(list(batch))
                token.cancel()  # cancel while the first batch is in flight
                return len(batch)

            monkeypatch.setattr(data_service, "sync_daily_kline", _fetch)

            ctx = _ctx(tmp, store)
            ctx.cancel_token = token
            result = sync_market_data(DataSyncParams(start_date=date(2024, 1, 1)), ctx)

            assert len(fetched) == 1, "the loop must stop after the batch that cancelled"
            assert result.cancelled is True
            assert result.universe_size == 6
            assert result.missing == codes[2:], "codes not yet attempted must be reported"

    def test_gap_fill_falls_back_to_a_backup_source(self, monkeypatch) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DataStore(str(Path(tmp) / "test.db"))
            codes = ["000001.SZ", "600000.SH"]
            logs: list[str] = []

            class _Backup:
                source_name = "backup"

            monkeypatch.setattr(data_service, "AkshareAdapter", _adapter_with(codes))
            monkeypatch.setattr(data_service, "sync_all", lambda *a, **kw: {})
            monkeypatch.setattr(data_service, "sync_index_weights", lambda *a, **kw: None)
            monkeypatch.setattr(data_service, "sync_index_daily", lambda *a, **kw: 0)
            monkeypatch.setattr(data_service, "sync_daily_kline", lambda s, a, batch, start, end, ctx=None: len(batch))
            monkeypatch.setattr(
                data_service,
                "get_backup_sources",
                lambda _s, _names: [_Backup()],
            )

            ctx = _ctx(tmp, store)
            ctx.log_sink = lambda m, _l: logs.append(m)
            result = sync_market_data(DataSyncParams(start_date=date(2024, 1, 1)), ctx)

            # The service uses the parameter object's own backup list, which
            # defaults to the same sources as the config.
            assert any("Filling 2 missing codes via backup" in m for m in logs)
            assert result.missing == codes, "the backup wrote nothing, so nothing was filled"


class TestResolveStart:
    def test_full_history_on_a_fresh_database(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DataStore(str(Path(tmp) / "f.db"))
            assert _resolve_start(store) == DEFAULT_HISTORY_START

    def test_incremental_when_data_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DataStore(str(Path(tmp) / "i.db"))
            store.conn.execute("INSERT INTO trade_calendar VALUES (?, TRUE)", [date(2024, 6, 28)])
            assert _resolve_start(store) == date(2024, 6, 23)


class TestDataStatus:
    def test_reports_the_store_actually_in_use(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = DataStore(str(Path(tmp) / "s.db"))
            status = data_status(DataStatusParams(), _ctx(tmp, store))

            # The store in the context is authoritative, not the config path.
            assert status.db_path == store.db_path
            assert status.db_path.endswith("s.db")
            assert "daily_kline" in status.tables
            assert status.tables["daily_kline"].rows == 0
            assert status.latest_trade_date is None
