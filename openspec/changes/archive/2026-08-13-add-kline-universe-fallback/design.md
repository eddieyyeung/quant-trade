## Context

三个候选方案对比:
1. 同步历史成分股权重 — 被剔除股票无 kline 数据, 因子仍算不出, 需同时回填历史+退市股行情, 工程量大且依赖 tushare 2000 积分权限
2. 全市场回退 (5541 只) — 4700 只无 kline, 浪费查询且排名实际仍是 ~800 只有数据的股票
3. **kline 派生股票池** — 采用: 零外部依赖, 数据驱动, 无生存者偏差

数据现实: kline 覆盖 = 当前中证800成员的全历史 (~800 只/年)。kline 派生池 = 该集合在目标日期的上市子集, 772 只。

## Decisions

### 1. 回退窗口 = 60 日历天

`SELECT DISTINCT ts_code FROM daily_kline WHERE trade_date BETWEEN as_of-60d AND as_of`。停牌 >2 月的股票自然排除 (不可交易, 合理)。

### 2. 指数优先, 回退兜底

指数成分查询非空时不走回退 — 保证当期 (2026) 会话仍用真实指数成分。历史会话自动落到 kline 池。

### 3. 指数行情写入 daily_kline 表

与个股共用一张表 (PRIMARY KEY (ts_code, trade_date) 无冲突), amount/pct_change/turn_rate 填 0.0。不建新表 — 最小侵入, `get_daily()` 直接可用。

### 4. akshare `stock_zh_index_daily` 作为指数行情源

单次调用返回全历史 (2005 至今), 无需 token, 无分页。指数无成交额/换手率字段, 填 0.0。

## Risks / Trade-offs

- **股票池非真实历史指数成分** — 接受。人工决策参考场景足够; 严格指数回放留作后续独立任务 (历史 kline 回填)。
- **60 天窗口边缘**: 长停牌股在窗口内无行情被排除 — 期望行为 (不可交易)。
