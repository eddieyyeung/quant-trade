## Why

个人A股量化研究缺少一套从数据到信号到跟踪的完整工具链。现有开源方案（vnpy、zipline等）偏重实时交易，对低频手动执行的研发场景过重。需要搭建一个轻量平台，以日线数据为基础，用回测驱动策略迭代，通过周度信号+模拟盘跟踪积累实盘经验。

## What Changes

- 新增 `data` 模块：DuckDB 持久化存储，akshare 为主数据源，支持日线行情/财务数据/交易日历/股票池管理
- 新增 `factors` 模块：因子基类 + 动量/价值/质量三类因子 + 因子IC/RankIC分析工具
- 新增 `strategies` 模块：策略基类定义统一接口，首期实现多因子打分排名策略
- 增强 `backtest` 模块：A股回测引擎，支持 T+1/涨跌停限制/佣金印花税/停牌处理等规则
- 新增 `signals` 模块：周度 HTML 报告生成，含净值曲线/调仓信号/持仓明细/因子监控
- 增强 `cli` 模块：`quant-trade data sync|factor update|strategy run|backtest run|weekly` 子命令
- 重构 `config.py`：Pydantic 配置模型扩展，覆盖数据源/因子/策略/回测全部参数

## Capabilities

### New Capabilities

- `data-ingestion`: DuckDB 数据存储层，多源行情与财务数据拉取，交易日历，指数成分股股票池管理
- `factor-system`: 可扩展因子计算框架，内置动量/价值/质量因子，因子有效性分析（IC/RankIC/分层回测）
- `strategy-engine`: 统一策略接口，多因子打分排名策略，支持参数化配置和策略注册
- `backtest-engine`: A股规则感知的回测引擎，逐周调仓循环，成本与流动性约束，绩效指标计算
- `weekly-reporting`: 单体 HTML 周报，净值曲线对比图，调仓信号表，当前持仓盈亏，因子IC跟踪

### Modified Capabilities

（无现有能力需修改——项目当前为骨架阶段）

## Impact

- **依赖新增**: `duckdb` (数据存储), `plotly` 或 `matplotlib` (周报图表), `jinja2` (HTML 模板渲染)
- **现有代码影响**:
  - `src/quant_trade/data/` — 从骨架扩展为完整模块
  - `src/quant_trade/backtest/` — 从骨架扩展为完整回测引擎
  - `src/quant_trade/analysis/` — 因子分析逻辑合并入新 `factors` 模块
  - `src/quant_trade/config.py` — 配置模型大幅扩展
  - `src/quant_trade/cli.py` — 从占位符扩展为实际 CLI
  - `pyproject.toml` — 补充 duckdb/jinja2 等依赖
