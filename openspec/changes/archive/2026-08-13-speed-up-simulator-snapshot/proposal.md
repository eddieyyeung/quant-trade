## Why

`POST /api/sessions` (create_session) 耗时数十秒且内存持续上升。根因有二：(1) 首次快照构建时加载全量因子数据和策略信号，对空仓位/无决策场景完全浪费；(2) 快照构建过程中所有进度日志均为 DEBUG 级别，用户看不到进展，误以为"卡死"。

## What Changes

- **初始快照跳过因子排名和策略信号**：首次 `create_session` 的快照不调用 `_build_factor_ranking()` 和 `_build_strategy_signals()`，这些计算延迟到用户第一次 `step()` 时。空仓位的因子排名对用户决策无参考价值，策略信号在用户做第一个决策前也无意义。
- **快照构建日志升级为 INFO**：`SnapshotBuilder.build_snapshot()` 内部各阶段日志从 `logger.debug` 改为 `logger.info`，让用户可见进度（"正在构建行情概览..."、"正在计算因子排名 3/6..."）。
- **因子计算进度日志降噪**：`_build_factor_ranking` 中的逐因子日志保留 INFO 级别，确保用户看到实际进展。

## Capabilities

### New Capabilities
- `deferred-snapshot-computation`: 首次会话快照延迟计算因子排名和策略信号，在 `step()` 时按需触发

### Modified Capabilities
- `simulator-engine`: 首次 `create_session` 返回的 snapshot 中 `factor_ranking` 和 `strategy_signals` 字段可为空，要求前端处理空值展示

## Impact

- `src/quant_trade/simulator/snapshot.py` — `build_snapshot()` 日志级别升级；可选跳过因子/策略计算
- `src/quant_trade/simulator/engine.py` — `create()` 传入 `skip_heavy=True` 延迟重型计算
