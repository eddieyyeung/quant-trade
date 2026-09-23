## Why

对比视图的策略侧已经静默失效了，而且没有任何表面症状。「一键采纳推荐」刚落地，反馈闭环正要靠这个页面回答「我剔掉的那只后来怎样」，结果它答不出来：策略净值线不画，逐周差异表的「策略独有 / 共同」两列永远是 `—`。

三个独立缺陷叠在一起，且都被 `except Exception` 吞掉：

1. `comparison._to_date` 把 `isinstance(d, date)` 放在最前，而 `pd.Timestamp` 是 `datetime.date` 的子类 —— Timestamp 原样穿过，喂进 `run_backtest` 后在 `exec_date > end` 处抛 `Cannot compare Timestamp with datetime.date`，被捕获后返回 `(None, {})`。
2. `_run_strategy_shadow` 的 `return nav, {}` —— `strategy_holds_by_week` 从来没有被填过，回测成功与否都一样。
3. 差异表口径本身站不住：手动侧取的是「本周买入的代码」（`executed_orders` 里的 BUY），字段名却叫 `user_holds`；即便前两条修好，也是拿「本周买入」比「周末持仓」。

## What Changes

- 修 `comparison._to_date`：删掉这份有缺陷的拷贝，复用已被验证的 `quant_trade.data.calendar._to_date`（先判 `pd.Timestamp`）。
- 策略回测失败不再静默降级：失败原因随对比结果返回，页面可见。其余两条净值线仍然照常返回——一个策略跑不起来不该让整份报告 500。
- 逐周差异表更换数据源：从「持仓重叠 vs 影子回测」改为「**你的决策 vs 当周推荐给你的人**」，数据取自每个 `Decision` 已同时保存的 `user_orders` 与 `strategy_orders`。回答的是「你采纳了多少、剔掉了什么、额外加了什么」。
- 删除恒空的「策略独有 / 共同」两列。一个永远显示 `—` 的列会被读成「本周没有差异」，这是主动误导。
- 新列按目标组合比较，不按订单比较：同一组合可以有多种下单方式，订单级比较会把「分两笔买」误判成偏离。

**BREAKING**: `GET /api/sessions/{id}/compare` 的 `weekly_diffs` 条目结构改变（字段换名 / 增删）。前端与后端同仓同时改，无外部消费方。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `simulator-web`: 对比报告 API 的 `weekly_diffs` 结构改为决策偏离口径，并新增策略回测失败原因的返回字段。
- `simulator-antd-ui`: 对比视图的逐周差异表列改变；策略回测失败时展示原因。

## Impact

- `src/quant_trade/simulator/comparison.py` — `_to_date`、`_run_strategy_shadow`、`_build_weekly_diffs`、`ComparisonResult`
- `src/quant_trade/simulator/types.py` — `WeeklyDiff` 字段、`ComparisonResult` 新增失败字段
- `src/quant_trade/simulator/api.py` — `compare` 端点序列化
- `web/src/api/simulator.ts`、`web/src/pages/simulator/ComparisonView.tsx`
- `tests/test_simulator_comparison.py`、`tests/test_simulator_api.py`
- 不改动：`run_backtest` 本身、`Portfolio`、影子回测净值口径（策略净值线修好 A 之后即恢复）
