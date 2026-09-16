## Tasks

### 1. get_universe kline fallback

- [x] `store.py`: fallback branch in `get_universe()` + extract `_universe_from_kline()` (60-day window) and `_exclude_st()`

### 2. Index kline sync

- [x] `akshare_adapter.py`: `fetch_index_daily()` via `stock_zh_index_daily`, maps to daily_kline schema (amount/pct_change/turn_rate = 0.0)
- [x] `sync.py`: `sync_index_daily()` with INSERT OR REPLACE
- [x] `cli.py`: `data sync` Step 2.5 wires index daily sync for default indices

### 3. Tests

- [x] `tests/test_universe.py`: 3 tests — kline fallback with ST/stale exclusion, index-weights preference, both-empty
- [x] `tests/test_sync.py`: 3 tests — `sync_index_daily` writes rows with 0.0 fills, empty codes, unsupported adapter (mock adapter, no network)
- [x] `tests/test_cli.py`: 1 test — `data sync` Step 2.5 calls `sync_index_daily` with default indices (monkeypatched sync functions)

### 4. Real data verification

- [x] Sync CSI300+CSI500 index kline: 11217 rows into real DB
- [x] Live API: create 53ms, market non-null (benchmark_close=3861.83), universe 772 stocks, step() factor ranking on 772 stocks works
