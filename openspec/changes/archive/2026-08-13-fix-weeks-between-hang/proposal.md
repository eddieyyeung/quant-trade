## Why

`TradeCalendar.weeks_between()` 在节假日缺口处死循环: 当周最后交易日落于 `current` 之前 (如 2023 国庆 9/28 → 10/9), `current = max(current, friday + 5)` 不再前进, 每轮迭代重复 append 同一 (signal, exec) tuple。实测真实日历 20000+ 次迭代卡死, weeks 列表无限增长导致内存 ~100MB/s 上升, 直至 OOM。`create_session` 请求永久挂起 (日志停在 "Computing week schedule...")。

## What Changes

- **`weeks_between` 重写**: 每轮迭代要么 append 一周并跳至下周一, 要么跳过假期缺口 — `current` 严格递增, 循环保证终止
- **消除 `self._sorted.index(friday) + 1` 的 IndexError 隐患** (friday 为最后交易日时越界)
- **回归测试**: 5 个测试覆盖密集日历、国庆缺口复现、缺口内起始日期、无重复周、末尾无执行日

## Capabilities

### New Capabilities
- `<none>`

### Modified Capabilities
- `simulator-engine`: 周调度在含假期缺口的真实日历上必须终止且无重复周

## Impact

- `src/quant_trade/data/calendar.py` — `weeks_between()` 重写
- `tests/test_calendar.py` — 新增 5 测试
