## Why

回测结果是**全项目最大的读路径缺口**：

- `run_backtest()` 返回 dict，其中 `nav_series` 是 `date` 索引的 `pd.Series`、`portfolio` 是 dataclass 实例、`trade_log` 是 `list[dict]`
- **零持久化** —— DuckDB 七张表没有一张与回测相关。CLI 拿到后打印 8 行指标即丢弃
- 平台化后只剩两条路：按请求重跑（等分钟级，不可接受）或新增结果表。**必须落库**，否则详情页打不开、多 run 对比无从谈起

本变更是平台价值的招牌页面：净值曲线、回撤、绩效指标、持仓、交易明细、归因。

## What Changes

- 新增回测结果持久化：`backtest_nav` / `backtest_trade` / `backtest_metric` / `backtest_position` 四张表，由 run 完成时写入，并在 `artifact` 表中登记
- 新增回测任务表单页：日期区间、策略选择、持仓数覆盖、初始资金、基准
- 新增回测详情页：净值/基准/超额曲线、回撤面积图、绩效指标卡片、持仓明细、交易明细
- 新增多 run 对比页：任意两个（或多个）run 的净值叠加与指标对照
- 回测任务接入统一运行 API（`kind: backtest`），周循环进度上报

## Capabilities

### New Capabilities

- `backtest-result-store`: 回测净值、交易、绩效指标的持久化与查询
- `backtest-research-ui`: 回测提交表单、详情页、多 run 对比页

### Modified Capabilities

- `backtest-engine`: 回测服务在完成时将结果写入结果表并在 `artifact` 登记

## Impact

**新增**
- `src/quant_trade/backtest/result_store.py`
- `src/quant_trade/services/backtest_query.py`
- `web/src/pages/backtest/`（列表 / 提交 / 详情 / 对比）

**修改**
- `src/quant_trade/data/schema.py`：新增三张回测结果表
- `src/quant_trade/services/backtest.py`：完成后写结果表
- `src/quant_trade/api/`：挂载回测域路由

**新增依赖**：无

**表设计要点**
- `backtest_nav`：`(run_id, trade_date)` 主键，含策略净值 / 基准净值 / 回撤
- `backtest_trade`：`(run_id, seq)` 主键，含日期、方向、代码、股数、价格、各项费用
- `backtest_metric`：`(run_id, metric_name)` 主键，键值对形式，避免加指标要改表结构
- `backtest_position`：`(run_id, ts_code)` 主键，期末持仓（引擎只在结束时暴露组合状态）

**ECharts 用途**：净值多线图（brush 缩放，这是选 ECharts 而非其他图表库的主要原因）、回撤面积图、分年度收益柱状图、持仓权重饼图
