## Context

当前 `Simulator.create()` 在初始化快照时调用 `SnapshotBuilder.build_snapshot()`，该方法计算四个模块：行情概览、持仓快照、因子排名、策略信号。对于首次创建的空仓位场景，因子排名（6+ 因子 × 5000 股票因子计算）和策略信号（策略引擎生成推荐买卖）完全浪费——用户尚未看到界面，尚无任何决策需求。

约束：不改变 Snapshot 数据结构、不改变 factor/strategy 公开 API、前端已支持空 `factor_ranking` 和 `strategy_signals` 字段。

## Goals / Non-Goals

**Goals:**
- 首次 `create_session` 的快照构建时间从 ~30s 降至 ~1s
- 用户可见快照构建的阶段性进度（INFO 日志）
- `step()` 操作仍包含完整快照（因子排名 + 策略信号），用户决策体验不变

**Non-Goals:**
- 不改变因子计算算法或策略引擎逻辑
- 不引入异步计算或后台任务
- 不修改 DuckDB 内存配置（已在 fix-simulator-memory-leak 中完成）

## Decisions

### 1. SnapshotBuilder 增加 `skip_heavy` 参数

`build_snapshot(cursor_date, ..., skip_heavy: bool = False)` — 当 `skip_heavy=True` 时，跳过 `_build_factor_ranking()` 和 `_build_strategy_signals()`，设置对应字段为 `[]` 和 `None`。

**理由**: 最小侵入性。不改变现有调用方行为（默认 `False`），仅 `create()` 传入 `True`。

### 2. 日志级别: debug → info

`build_snapshot()` 内部四阶段日志从 `logger.debug` 改为 `logger.info`：
- `"Building market overview..."`
- `"Building portfolio snapshot..."`  
- `"Building factor ranking..."`
- `"Building strategy signals..."`

`_build_factor_ranking()` 中已有逐因子 INFO 日志（`"Computing X factors..."`, `"Factor 1/6: momentum_20d..."`），保持不变。

**理由**: Simulator 是交互式工具，用户等待时需要看到进展。这些日志不频繁（每次快照一次），不会造成日志轰炸。

### 3. 前端兼容性

Snapshot 的 `factor_ranking` 字段已定义为 `list[FactorRankItem]`，空列表 `[]` 是合法值。`strategy_signals` 已定义为 `list[StrategySignalItem] | None`，`None` 是合法值。前端需处理这些空值展示（已有 "暂无因子数据" 警告机制）。

**确认**: 查看 `Snapshot` dataclass 定义和 simulator.html 前端代码，确认空值处理路径。

## Risks / Trade-offs

- **前端未适配空值展示** → 风险低。Snapshot 已有 `data_warnings` 字段，且 types.py 中字段类型已支持空列表/None。需前端验证。
- **首次 step() 仍需等待因子计算** → 可接受。用户做第一个决策时等待是合理的，且此时 cursor 已创建，用户已知 session 可正常工作。
