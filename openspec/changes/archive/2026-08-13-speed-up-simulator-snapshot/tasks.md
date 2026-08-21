## Tasks

### 1. SnapshotBuilder: add skip_heavy parameter

- [x] Add `skip_heavy: bool = False` parameter to `build_snapshot()` in `src/quant_trade/simulator/snapshot.py`
- [x] When `skip_heavy=True`, skip `_build_factor_ranking()` and `_build_strategy_signals()`, set `factor_ranking=[]`, `strategy_signals=None`, add warning `"因子和策略信号将在首次调仓时计算"`
- [x] When `skip_heavy=False` (default), behavior unchanged

### 2. Upgrade snapshot build logs to INFO

- [x] Change `logger.debug` to `logger.info` in `build_snapshot()` for four stage log messages (cursor/build overview/build portfolio/build factor/build strategy)
- [x] Add `logger.info("Skipping factor ranking and strategy signals (deferred to first step)")` when `skip_heavy=True`

### 3. Simulator.create: pass skip_heavy=True

- [x] In `Simulator.create()` (engine.py line 113), pass `skip_heavy=True` to `snapshot_builder.build_snapshot()`
- [x] Verify that `step()`, `resume()`, and `snapshot()` methods do NOT pass `skip_heavy` (they use default `False`)

### 4. Verify frontend handles empty factor/strategy fields

- [x] Check `Snapshot` dataclass in `types.py` — confirm `factor_ranking` accepts `[]` and `strategy_signals` accepts `None`
- [x] Review `src/quant_trade/templates/simulator.html` — frontend uses `s.factor_ranking || []` and renders `data_warnings`, handles empty/null correctly
- [x] Empty-state UI not needed — warnings already rendered; our deferred message displays via existing warning mechanism

### 5. Test

- [x] Add/update unit test: `test_create_session_lightweight_snapshot` — verify factor_ranking=[] and strategy_signals=None
- [x] Add/update unit test: `test_step_has_full_snapshot` — verify factor_ranking populated and strategy_signals present
- [x] Manual test: create session via API, verify response time < 2s, verify logs visible (实测 26-53ms, 全阶段 INFO 日志可见)
