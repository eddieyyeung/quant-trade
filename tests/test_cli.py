"""Basic smoke tests for the CLI module."""

import tempfile
from types import SimpleNamespace

from quant_trade.cli import main


def test_main_runs() -> None:
    """CLI entry point should not crash."""
    main()


class TestDataSyncIncludesIndexKline:
    def test_sync_calls_sync_index_daily(self, monkeypatch) -> None:
        """`data sync` Step 2.5 wires index daily kline for default indices."""
        import quant_trade.cli as cli
        from quant_trade.data.store import DataStore

        with tempfile.TemporaryDirectory() as tmp:
            store = DataStore(tmp + "/test.db")
            calls: dict[str, list] = {"index_daily": []}

            class _FakeAdapter:
                source_name = "fake"

                def fetch_stock_basic(self):
                    import pandas as pd

                    return pd.DataFrame()

            def _fake_sync_all(s, codes, **kw):
                return {}

            def _fake_sync_index_weights(s, indices, d, adapter):
                return None

            def _fake_sync_index_daily(s, adapter, indices):
                calls["index_daily"].append(indices)
                return 0

            def _fake_sync_daily_kline(s, adapter, codes, start, end):
                return 0

            monkeypatch.setattr(cli, "DataStore", lambda path: store)
            monkeypatch.setattr(cli, "AkshareAdapter", _FakeAdapter)
            monkeypatch.setattr(cli, "sync_all", _fake_sync_all)
            monkeypatch.setattr(cli, "sync_index_weights", _fake_sync_index_weights)
            monkeypatch.setattr(cli, "sync_index_daily", _fake_sync_index_daily)
            monkeypatch.setattr(cli, "sync_daily_kline", _fake_sync_daily_kline)

            config = SimpleNamespace(
                data=SimpleNamespace(
                    db_path=tmp + "/test.db",
                    primary_source="akshare",
                    backup_sources=[],
                )
            )
            cli._cmd_data("sync", config)  # noqa: SLF001

            assert len(calls["index_daily"]) == 1
            assert calls["index_daily"][0] == ["000300.SH", "000905.SH"]
