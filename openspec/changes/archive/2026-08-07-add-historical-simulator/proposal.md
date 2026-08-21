## Why

现有回测引擎仅支持全自动策略回测，无法让用户以交互方式在历史时点做决策、理解策略信号的分歧来源、或对比"手动决策 vs 策略决策 vs 基准"的表现。需要一个时光机式的交互复盘沙盘，让用户回到任意历史周五，基于当时可观测的全部数据独立做出调仓决策，并随时输出多线对比报告。

## What Changes

- 新增 `quant_trade.simulator` 模块，包含独立的模拟器引擎、时点快照生成、会话持久化、多线对比四大子模块
- 新增 `quant-trade sim` CLI 命令组（start/resume/step/skip/status/compare），用于在终端中交互操作
- 模拟器直接复用现有 `backtest/portfolio.py`（虚拟账户）、`backtest/rules.py`（A股规则）、`data/store.py`（时点数据查询），不修改上述模块
- 新增 FastAPI 后端路由（`simulator/api.py`），为日后的 Web UI 提供 REST API
- 新增 `simulator_session` DuckDB 表，持久化模拟会话（决策记录、持仓快照、参考信号）
- 向后兼容：对现有模块零破坏，CLI 顶层不变

## Capabilities

### New Capabilities

- `simulator-engine`: 模拟器核心引擎。支持 create/resume/step/compare/skip 操作，封装周度循环（周五决策→周一执行），复用现有 A 股交易规则和费用模型
- `timepoint-snapshot`: 时点数据快照。在任意历史周五聚合市场概况、用户持仓盈亏、因子排名 topN、参考策略信号，保证无未来信息（所有查询使用 as_of 约束）
- `session-persistence`: 模拟会话持久化。支持会话创建、暂停（保存完整状态到 DuckDB）、恢复、列表，每次决策和快照都被记录
- `decision-comparison`: 多线对比报告。用户决策 vs 参考策略 vs 基准三条 NAV 曲线、关键指标对比、逐周决策差异详情

### Modified Capabilities

无。新增模块不改变现有 capability 的行为。

## Impact

- **新增目录**: `src/quant_trade/simulator/`（engine.py, snapshot.py, session.py, comparison.py, api.py）
- **新增 CLI**: `quant-trade sim [subcommand]`（cli.py 新增一组子命令）
- **新增 DB 表**: `simulator_session` 存入 `data/quant.db`
- **新增依赖**: FastAPI + uvicorn（Web API 后端）
- **新增模板**: `src/quant_trade/templates/simulator.html`（Phase 2 Web UI）
- **不修改** `backtest/`, `strategies/`, `data/store.py`, `factors/`
