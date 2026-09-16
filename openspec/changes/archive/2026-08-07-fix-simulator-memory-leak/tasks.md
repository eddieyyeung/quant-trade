## 1. DuckDB Memory Control

- [x] 1.1 `data/schema.py` `init_db()`: Add `SET memory_limit = '512MB'` and `SET threads = 2` pragmas after connection creation
- [x] 1.2 `data/store.py` `DataStore.get_daily()`: ~~Wrap `execute().df()` with explicit `result.close()`~~ — REVERTED. DuckDB 1.5.4: `result.df()` already materializes and closes result; calling `result.close()` after `df()` closes the PARENT CONNECTION (breaking change in DuckDB API)
- [x] 1.3 `data/store.py` `DataStore.get_financials()`: Same — reverted
- [x] 1.4 `data/store.py` `DataStore.get_universe()`: Same — reverted
- [x] 1.5 `data/store.py` `DataStore.get_calendar()`: Same — reverted
- [x] 1.6 `data/store.py` `DataStore.get_latest_trade_date()`: Same — reverted
- [x] 1.7 `data/store.py` `DataStore.close()`: Add `PRAGMA shrink_memory` before closing connection

## 2. Factor Registry Store Injection

- [x] 2.1 `factors/registry.py` `FactorRegistry.get()`: Add `store: DataStore | None = None` parameter, pass to `cls(store=store)`
- [x] 2.2 `factors/registry.py` `FactorRegistry.get_by_category()`: Add `store` parameter, pass to `cls(store=store)`
- [x] 2.3 `factors/base.py` `Factor.__init__`: Added `store: DataStore | None = None` parameter to base class (required for mypy, subclasses already had it)
- [x] 2.4 Verify all Factor subclasses (`momentum.py`, `value.py`, `quality.py`) accept `store=None` in `__init__` — confirmed, no changes needed

## 3. SnapshotBuilder Integration

- [x] 3.1 `simulator/snapshot.py` `_build_factor_ranking()`: Pass `store=self._store` to both `factor_registry.get()` calls (main loop + fallback)
- [x] 3.2 `simulator/engine.py` `step()`: Strategy receives DataStore via `generate_signals(data=...)` parameter, not constructor — not applicable, skipped

## 4. Verification

- [x] 4.1 Run existing test suite: `uv run pytest tests/ -v --tb=short` (14 passed, 13 pre-existing failures — no regressions)
- [x] 4.2 Run type checking: `uv run mypy src/` (0 new errors, 27 pre-existing)
- [x] 4.3 Memory smoke test: 800 stocks, 30 factor rankings, 9.8s compute time → +21.1 MB delta (down from expected GB-scale before fix)
