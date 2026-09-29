> **状态：待实现。** 依赖 `add-backtest-research-pages`（C4）与 `add-model-research-pages`（C5）均已完成并归档，design / specs / tasks 已补齐。

## Why

收口变更，做两件事：

**一、报告域** —— 周报生成链路上有两个既有缺口，趁迁移一并补上：

1. **IC 面板恒空**：`generate_weekly_report(..., factor_ic_data=None)` 的唯一调用者没传该参数，全仓库无生产者，模板里对应的渲染段永远出不来
2. **交易明细到不了报表**：`trade_log` 从未传入周报，报表有 `total_trades` / `turnover` 指标却看不到具体成交
3. **非周五提示不完整**：`reporter.py:134` 只判断「今天是否周五」，spec 里的「或最近交易日非周五」未实现（周五休市时不会提示）；且 `date.today()` 不可注入，无法按 spec 举例的日期复现，无任何测试
4. **提示文案语义不符**：模板里「信号基于最近周五 {{ signal_date }}」填的是实际 signal_date，非周五运行时它并不是周五

> 第 3、4 项在 `refactor-extract-service-layer` 的独立验证中被发现。该变更只做服务层抽取，未改动 `reporter.py` 的渲染逻辑，故在此承接。

**二、仿真模块前端重建** —— 原 simulator 前端（`web/src/components/` 8 个组件 + `web/src/index.css`，手写 CSS、无组件库）已随 `add-platform-shell`（C2）删除：外壳接管前端入口后它不再可达，且其 `recharts` 依赖与「图表统一用 ECharts」的决策冲突。因此本变更不是迁移而是**重建**，从 git 历史取回交互逻辑，用 Ant Design 重写。

## What Changes

**报告域**
- 新增报告列表页：周报历史、生成时间、数据时效
- 新增报告预览页：内嵌预览 + 下载
- 修复 IC 面板恒空：接入 IC 序列数据源（依赖 C3 的 `ic_series` 持久化）
- 修复交易明细缺失：将 `trade_log` 传入周报模板
- 修复非周五提示：判断依据改为「最近交易日是否为周五」，并将当前日期改为可注入以便测试
- 修正提示文案：`signal_date` 与「最近周五」的语义对齐
- 周报生成接入统一运行 API（`kind: weekly`）

**仿真重建**
- 以 Ant Design 重建会话列表、会话创建、逐周决策、持仓表、策略信号、因子排名、对比视图等页面（原实现见 git 历史 `web/src/components/`，C2 已删除）
- 样式一律走 `web/src/shell/theme.ts` 的 token 与 antd 组件，不新增手写 CSS
- 仿真页面纳入平台导航（`/simulator` 分区当前指向占位页）
- 后端 `simulator/api.py` 会话读写逻辑**不改动**

## Capabilities

### New Capabilities

- `report-browser-ui`: 报告列表、预览、下载
- `simulator-antd-ui`: 仿真模块的 Ant Design 重写

### Modified Capabilities

- `weekly-reporting`: IC 面板数据源接入、交易明细传入、非周五判据修正（修复四个既有缺口）
- `kline-universe-fallback`: 补 `SnapshotBuilder._build_market_overview()` 在指数日线同步后非空的测试覆盖（当前 `tests/test_simulator_snapshot.py` 直接插 kline，绕过了 `sync_index_daily`，该断言无处落地）
- `simulator-web`: 仿真前端从独立应用并入平台导航，原手写样式方案作废

## Impact

**新增**
- `web/src/pages/reports/`、`web/src/pages/simulator/`

**修改**
- `src/quant_trade/signals/reporter.py`：接收并渲染 IC 数据与交易明细
- `src/quant_trade/services/report.py`：组装 `factor_ic_data` 与 `trade_log`

**新增依赖**：无

**验证锚点**
- 周报中的 IC 面板有数据（当前恒空）
- 周报中的交易明细表有内容（当前缺失）
- 在非周五（含周五休市）运行时提示条正确出现，且日期可注入以覆盖该场景
- 仿真模块功能零回归：会话创建、逐周决策、对比报告全部可用（对照 git 历史中的原实现逐项核对）
- `/simulator` 分区不再是占位页，且不新增任何手写 CSS 文件
