## Why

历史回放会话股票池为空: `index_weights` 仅含 2026 年当期快照, `get_universe(as_of=2023-06-02)` 匹配 0 行 → 因子排名空、参考策略无信号。且 `daily_kline` 无指数行情 (000300.SH 0 行) → market overview 返回 null。

## What Changes

- **`get_universe()` kline 回退**: 指数成分查询为空时, 从近 60 天有 kline 的股票派生股票池, 经 ST 过滤后返回。实测 2023-06-02 得到 772 只 (数据驱动的可交易池, 无生存者偏差)
- **指数行情同步**: akshare `fetch_index_daily()` (一次调用拉全历史) + `sync_index_daily()` + `data sync` CLI 接入。CSI300/CSI500 各 ~5600 行入库, market overview 恢复
- **测试**: `tests/test_universe.py` 3 测试 (回退生效/指数优先/双空)

## Capabilities

### New Capabilities
- `kline-universe-fallback`: 无指数成分数据时从 kline 派生股票池; 指数行情同步到 daily_kline

### Modified Capabilities
- `<none>`

## Impact

- `src/quant_trade/data/store.py` — `get_universe()` 回退分支 + `_universe_from_kline()` / `_exclude_st()`
- `src/quant_trade/data/sources/akshare_adapter.py` — `fetch_index_daily()`
- `src/quant_trade/data/sync.py` — `sync_index_daily()`
- `src/quant_trade/cli.py` — `data sync` Step 2.5 指数行情
- `tests/test_universe.py` — 新增
