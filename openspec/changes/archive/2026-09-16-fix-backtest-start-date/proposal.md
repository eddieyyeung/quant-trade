## Why

回测在起始日期不是交易日时**静默返回空结果**。

`run_backtest` 用 `get_calendar(start, end)` 构建交易日历，因此日历中不存在任何早于 `start` 的日期。而 `weeks_between` 的第一步是 `last_trade_date_of_week(start)` —— 当 `start` 落在周末或节假日时该调用返回 `None`，`weeks_between` 视为「没有更多交易日」直接 `break`，返回 0 周。回测随即走 `_empty_result` 分支，`metrics` 为空字典。

失败是无声的：CLI 用 `metrics.get("total_return", 0)` 打印，显示为 `0.00%` 而非报错；HTML 周报只是渲染出一张空白净值图。

`AppConfig.backtest.start_date` 默认值为 `2015-01-01`（元旦，非交易日），因此 `quant-trade backtest run` 与 `quant-trade weekly` 在默认配置下**从未产出过有效回测**。

模拟盘没有这个问题 —— `Simulator` 在计算周程前先做了 `actual_start = calendar.next_trade_date(start_date)`。回测漏掉了这一步。

## What Changes

- `backtest/engine.py`：在计算周度调仓日程前，将 `start` 归一化到该日或之后的第一个交易日，与 `simulator/engine.py` 的既有做法保持一致
- 新增回归测试：起始日期落在非交易日时，回测周期与净值序列均非空

行为变化：修复后回测与周报将产出真实净值曲线（此前为空）。这不是新功能，是恢复预期行为。

## Capabilities

### New Capabilities

<!-- 无 -->

### Modified Capabilities

- `backtest-engine`: 新增「回测起始日归一化」需求 —— 起始日落在非交易日时 SHALL 归一化到其后第一个交易日，而非产生空回测

## Impact

**受影响代码**
- `src/quant_trade/backtest/engine.py`（`run_backtest` 周程计算）
- `tests/test_integration.py`（新增非交易日起始的回归用例）

**恢复的行为**
- `quant-trade backtest run`（默认 `start_date=2015-01-01`）产出真实绩效指标
- `quant-trade weekly` 周报的净值曲线、回撤、持仓明细不再为空
- `simulator/comparison.py` 中「手动 vs 策略 vs 基准」对比的策略净值不再为空

**不受影响**
- 归因到模拟盘路径 —— 它已做归一化
- `weeks_between` 本身无需修改，其「`None` 即停止」的语义在日历范围内是自洽的
