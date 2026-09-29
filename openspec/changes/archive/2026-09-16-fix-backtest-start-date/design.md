## Context

调用链：

```
run_backtest(start, end)
  ├─ calendar = TradeCalendar(ds.get_calendar(start, end))   ← 日历中最早日期 >= start
  └─ weeks = calendar.weeks_between(start, end)
                │
                └─ current = start
                   friday = last_trade_date_of_week(current)
                   if friday is None: break            ← 此处退出
                   ...
```

`last_trade_date_of_week(d)` 在「本次日历中不存在 <= d 的交易日」时返回 `None`。当 `d == start` 且 `start` 本身不是交易日时，这个条件必然成立 —— 日历是从 `start` 开始加载的，不可能含有更早的日期。

实测：

```
start        weekday  weeks
2015-01-01   Thu        0     ← AppConfig.backtest.start_date 默认值
2026-01-03   Sat        0
2026-01-04   Sun        0
2026-01-05   Mon       29     ← 仅当 start 恰为交易日
2026-03-07   Sat        0
2026-03-09   Mon       21
```

对照组 —— 模拟盘在同样位置做了归一化：

```python
# simulator/engine.py:76
actual_start = calendar.next_trade_date(start_date)
if actual_start is None:
    raise ValueError(f"No trade dates found from {start_date}")
```

## Goals / Non-Goals

**Goals**

- 起始日落在非交易日时，回测产出正常周期，不再静默为空
- 与模拟盘的起始日语义保持一致

**Non-Goals**

- 不修改 `weeks_between` 的语义
- 不修改 `trade_dates_between`、价格加载或基准加载的区间参数
- 不改变起始日已是交易日时的既有行为（数值完全不变）

## Decisions

### 1. 在 `run_backtest` 内归一化，不修改 `weeks_between`

`weeks_between` 的 `None → break` 在它的契约内是自洽的：`None` 表示「本次日历中没有可用的周」。问题出在调用方传了一个日历覆盖不到的日期。

在该函数里「猜测」调用方意图会把一个纯函数变成有隐含假设的函数，且模拟盘已经在调用方做了归一化 —— 保持两侧对称更清晰。

### 2. 归一化使用 `next_trade_date(start) or start`

```python
actual_start = calendar.next_trade_date(start) or start
weeks = calendar.weeks_between(actual_start, end)
all_trade_dates = calendar.trade_dates_between(actual_start, end)
```

`or start` 保留原值作为兜底：当区间内完全没有交易日时，`weeks_between` 会走原有的空结果分支，由 `_empty_result` 处理并记录日志 —— 与模拟盘 `raise ValueError` 不同，回测的空区间是合法输入（例如回测区间全为停市），不应抛异常。

`trade_dates_between` 一并传入 `actual_start`：两者取值等价（原实现会自行过滤掉 `start` 之前的日期），传归一化后的值使意图显式。

### 3. 不新增「起始日必须是交易日」的校验

也可以选择直接报错。否决原因：`start_date: 2015-01-01` 这类配置是自然的用户意图（「从 2015 年初开始」），把它变成错误会把一个便利默认值变成陷阱。归一化符合直觉。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| 修复后净值曲线从空变为非空，既有周报基线不可比 | 属恢复预期行为；已在 proposal 中显式说明 |
| 起始日归一化后区间缩短数日，绩效指标与「改造前」不可比 | 改造前为空，无有效基线可破坏 |
| 既有测试恰好都以交易日为起点，无法捕获此缺陷 | 新增回归用例显式从周末起步；这是本次变更的主要测试价值 |
