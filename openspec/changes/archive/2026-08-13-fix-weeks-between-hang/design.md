## Context

旧算法在每轮迭代末尾用 `current = max(current, friday + 5)` 前进。当 `friday < current` (current 落在假期缺口内, `last_trade_date_of_week` 解析到上一周的信号日) 时, `friday + 5 <= current` 成立, current 原地踏步, 死循环 append 重复 tuple。

真实触发: 2023-09-28 (周四, 节前最后交易日) → 2023-10-09 (周一恢复)。current=2023-10-03 时 `last_trade_date_of_week` 返回 09-28, `max(10-03, 09-28+5=10-03)` = 10-03, 不前进。

## Decisions

### 1. 循环不变式: current 严格递增

每轮迭代两分支:
- `friday < current`: 假期缺口, `current = current + (7 - current.weekday())` 跳至下周一, `continue`
- 否则 append `(friday, next_day)`, `current = friday + (7 - friday.weekday())` 锚定下周一

**终止性证明**: append 分支 current 前进 ≥3 天且恒越过 friday; 缺口分支前进 ≥1 天 (缺口 ≤ ~10 天, 有界)。signal day 严格递增 → 无重复周。

**替代方案**: 在旧循环体上打补丁 (`current = max(current + 1day, friday + 5)`)。不采用 — 缺口期间每天迭代都会重复 append 同一周 (9/28, 10/9) 多次, 产生重复周。

### 2. 语义保持

- signal day = 当周最后交易日 (可为周二/周四, 非必然周五)
- exec day = signal 后第一个交易日
- 最后一周无 exec day 时不产出 (与旧行为一致)

## Risks / Trade-offs

- **假期周跨周归属变化** — 接受。旧算法在非死循环路径行为已非确定性 (依赖起始位置), 新算法语义更清晰。
- **调用方无感知** — 返回结构 `list[tuple[date, date]]` 不变。Simulator/backtest 均无改动。
