## Why

`get_universe` 的 `SELECT DISTINCT ts_code` 没有 `ORDER BY`，同一个股票池两次调用返回**顺序不同**的同一个集合。这个顺序往下传染到因子评分、排序、top-N 截断与行业上限，于是**同一天、同一份数据，两次调用选出不同的股票**。

实测（沪深300 回退到 kline 推导的 772 只，2023-06-02）：

```
universe size: 772 772 | same order: False | same set: True
call A picks: (002230.SZ, 002602.SZ, 300475.SZ, 688322.SH)
call B picks: (002230.SZ, 002602.SZ, 300475.SZ, 688322.SH)   ← 传同一份有序 universe 时稳定
call C picks: (002558.SZ, 002602.SZ, 300476.SZ, 688475.SH)   ← reversed(universe) 全变
```

暴露它的是一次「一键采纳推荐」：用户在界面上看到推荐组合 A，点执行，`step` 内部**重算**出组合 B 存进决策记录，于是「完全照做」被记成「有偏离」（实测 `dropped`/`added` 各两只）。仿真只是最先撞上它的地方——回测、因子排名、模型训练选样本，全都在这个不稳定的顺序上。

并列分数越多，顺序的影响越大。当周 8 个因子里 5 个返回空，剩下三只股票的分数彼此相同，排序顺序直接决定谁进 top-N。

## What Changes

- `DataStore.get_universe` 的成分股查询 SHALL 按股票代码排序后再返回。
- `DataStore._universe_from_kline` 的回退查询 SHALL 同样排序。
- 股票池接口的契约里写明返回顺序是稳定的（同参数、同数据 → 同一顺序），使下游的 top-N 截断与并列打破可复现。

**BREAKING**: 无接口签名变化。但**并列处的选股结果会变**——已经落库的回测结果、因子 IC、模型训练集在并列位置可能与重跑不一致，属于修正而非回归。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `data-ingestion`: 指数成分股股票池的返回顺序从「未定义」改为「稳定」。

## Impact

- `src/quant_trade/data/store.py` — `get_universe`、`_universe_from_kline` 两个 SQL
- `tests/test_universe.py` — 同参数重复调用返回同一顺序
- 下游全部受益、全部零改动：`factors`、`strategies`、`backtest`、`simulator`
- 不改动：`_exclude_st`（已保序）、`factor_ranking._apply_industry_constraint`（给定入参顺序即确定）、任何排序算法本身
