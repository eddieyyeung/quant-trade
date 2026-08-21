## Context

当前 Simulator API 在 `create_session` 时调用 `SnapshotBuilder._build_factor_ranking()`，该函数遍历所有已注册因子（~6-8 个），通过 `factor_registry.get(name)` 获取实例。`get()` 每次调用 `cls()` 创建新实例，而每个 Factor 的 `__init__` 在未传入 store 时自动创建 `DataStore()`——即新建独立 DuckDB 连接。6 个因子 = 6 个独立 DuckDB 连接，各自加载相同的全量 kline 数据页到各自的 buffer pool。

DuckDB 默认 `memory_limit` 为系统物理内存的 80%，且 buffer pool 不主动归还 OS。调用结束后 Python GC 回收 Factor/DataStore 对象，但 DuckDB buffer 内存不立即释放。

约束：不改变 Factor 公开 API、不改变现有调用方的行为、不需要数据库迁移。

## Goals / Non-Goals

**Goals:**
- `FactorRegistry.get()` 支持可选 `store` 参数，使因子计算复用同一 DuckDB 连接
- DuckDB 连接初始化时设置合理的内存上限和线程数
- 查询方法显式关闭 DuckDB Result 对象，及时释放中间结果
- 单次 `create_session` 内存峰值从 ~2GB+ 降到 ~500MB 以内

**Non-Goals:**
- 不引入连接池或 DuckDB 连接管理框架
- 不改变因子计算结果（数学等价）
- 不修改策略引擎或回测引擎
- 不处理 matplotlib 相关的次要泄漏（影响远小于 DuckDB 连接问题）

## Decisions

### 1. FactorRegistry.get() 加可选 store 参数

`get(name, store=None)` — 调用方传入共享 DataStore，Factor 构造时透传。不传则行为不变（向后兼容）。

**替代方案**: 在 FactorRegistry 中缓存实例。不采用——因子实例可能持有状态，缓存引入生命周期管理复杂度。

### 2. DuckDB pragma 设置在 init_db 中集中管理

`SET memory_limit = '512MB'` 和 `SET threads = 2` 在 `init_db()` 中统一执行。值通过常量定义，后续可调。

512MB 选择依据：单连接 512MB + OS overhead 上限 ~1GB，远低于系统 16-32GB 总量，留有充足余量。

**替代方案**: 在线程/请求级别动态调整。不采用——pragma 是连接级别的，动态调整增加复杂度无实质收益。

### 3. Result.close() 在 DataStore 查询方法中

每个 `execute().df()` 调用改为 `result = execute(); df = result.df(); result.close()`。DuckDB Python API 中 `result.close()` 立即释放 C++ 侧 Result 对象。

**替代方案**: 依赖 GC / `__del__`。不采用——Python GC 时机不确定，可能导致短时间多个大 Result 共存。

### 4. shrink_memory 不自动调用

`PRAGMA shrink_memory` 是重型操作（扫描整个 buffer pool），不作为每次查询后的默认行为。仅在 `DataStore.close()` 或显式方法中调用。

**理由**: 512MB 上限已将峰值可控，频繁 shrink 反而消耗 CPU 且阻塞查询。

## Risks / Trade-offs

- **512MB 上限可能影响大批量查询** → 风险低。当前查询最大数据集 ~5000 stocks × 120 days = 600K rows，远在 512MB 内存内
- **threads=2 降低查询并行度** → 可接受。API 服务场景下 DuckDB 查询非 CPU 密集型，减少线程降低上下文切换和内存开销
- **Result.close() 增加调用链复杂度** → 低风险。封装在 DataStore 方法内部，调用方无感知
- **因子接收 store=None 时行为不变** → 确保向后兼容。`__init__` 中 `self.store = store or DataStore()` 逻辑保留
