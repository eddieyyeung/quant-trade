## Why

单次 `POST /api/sessions` (create_session) 调用后，后端 Python 进程内存持续上涨至数 GB 不释放。根因：`FactorRegistry.get()` 每次调用创建新 Factor 实例，每个 Factor 实例在 `__init__` 中创建独立 `DataStore`（含独立 DuckDB 连接），导致单次 snapshot 构建产生 6-8 个 DuckDB 连接各自加载全量行情数据到 buffer pool。DuckDB 默认 memory_limit 为系统内存 80%，且 buffer pool 不主动归还 OS。

## What Changes

- **FactorRegistry.get() 支持传入共享 DataStore**，因子实例复用调用方提供的连接
- **DuckDB 连接设置内存上限**（512MB）并限制线程数（2），防止 buffer pool 无限制膨胀
- **DataStore 查询方法显式关闭 Result 对象**，避免中间结果集滞留内存
- **所有 Factor 子类 `__init__` 接受可选 store 参数**并透传（已有此参数，确保路径完整）
- **Breakedge 行为**: 无。Factor 公开 API 不变，`get(name)` 仍可用（默认创建新 DataStore，兼容旧调用方）

## Capabilities

### New Capabilities
- `factor-store-injection`: FactorRegistry 支持通过 `get(name, store=...)` 注入共享 DataStore，因子计算复用同一 DuckDB 连接
- `duckdb-memory-control`: DuckDB 连接初始化时设置 memory_limit 和 threads pragma，大查询后可选调用 shrink_memory

### Modified Capabilities
- `<none>`

## Impact

- `src/quant_trade/factors/registry.py` — `get()` 方法签名加 `store` 参数
- `src/quant_trade/data/store.py` — `__init__`/`init_db` 加 pragma 设置；`get_daily`/`get_financials`/`get_universe`/`get_calendar` 加 `result.close()`
- `src/quant_trade/simulator/snapshot.py` — `_build_factor_ranking()` 传入 `self._store`
- `src/quant_trade/factors/momentum.py` — `__init__` 已有 store 参数，无需更改
- `src/quant_trade/factors/value.py` — 同上
- `src/quant_trade/factors/quality.py` — 同上
