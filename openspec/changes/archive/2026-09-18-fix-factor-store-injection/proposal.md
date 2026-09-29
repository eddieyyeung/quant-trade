# 修复数据库注入：因子与策略的 store 穿透

## Why

`factor_ranking` 生成信号时会读**两个不同的数据库**：股票池来自配置指定的库，价格与财务因子来自仓库自带的 `data/quant.db`——而后者不是配置里那个路径。

根因有两层，缺一不可：

1. **注册表工厂不吃 store。** `FactorRegistry.get(name, store=None)` 接受一个 store 并透传给因子类，但 `StrategyRegistry.get(name)` 连参数都没有，`cls()` 一调，策略自己的 `self.store = store or DataStore()` 兜底就生效了。`strategies/factor_ranking.py:63` 手里明明有 store，调 `factor_registry.get(fname)` 时没传。
2. **兜底是硬编码的。** `DataStore.__init__(db_path: str = "data/quant.db")`（`data/store.py:87`）写死一个字面量路径，不读 `AppConfig`、不认 `QUANT_CONFIG`。所以任何一处漏传都**静默**落到仓库自带的库，不报错、不警告，结果只是数字不对。

生产环境里两者恰好是同一个文件，这个缺陷因此一直不可见。把 `db_path` 指到别处——换库、跑测试夹具、多环境——信号就会用另一套价格算出来。上一个变更（`add-strategy-research-pages`）的端到端验证正是撞上了这一点：提交的信号生成任务对着夹具库跑，产出却来自仓库库。

## What Changes

**注册表接受并透传 store**
- `StrategyRegistry.get(name, store=None)` 打开 store 参数，与 `FactorRegistry.get` 对齐；`cls()` 改为把 store 交给策略类
- `Strategy` 基类补一个默认 `__init__(self, store=None)`，使「策略可以从调用方接收数据访问对象」成为基类契约而非每个子类各自的心愿
- `build_strategy(name, config, store=None)` 透传；两个调用方（`services/strategies.py:106`、`services/backtest.py:157`）都持有 store，一并传入

**调用点补齐**
- `strategies/factor_ranking.py:63` 把已有的 store 传给 `factor_registry.get`
- `services/factors.py:157,246,268` 三处传 `ctx.db`
- `simulator/` 的四处 `strategy_registry.get(...)` 传 `self._store`（快照构建早已在因子侧这么做，此处是同一件事的另一半）

**兜底不再静默**
- `DataStore(db_path=None)` 解析**配置指定的**库（沿用 `config.get_config_path()` 的既有规则：`QUANT_CONFIG` 环境变量，否则默认配置文件），取代硬编码字面量
- 未显式注入而走到兜底时记一条 warning，使「谁在偷偷开默认库」在日志里可见，而不是只体现在数字上

**顺带**：`openspec/specs/factor-system/spec.md` 的 Purpose 目前是 `TBD` 占位，使 `openspec validate --strict` 全仓见红；本变更既然要改这个 spec，一并补上。

## Capabilities

### New Capabilities

无。本变更的三个 delta 都落在既有能力上。

### Modified Capabilities

- `factor-store-injection`：既有能力，由 `2026-08-07-fix-simulator-memory-leak` 建立，当时只覆盖了 `FactorRegistry.get` 与 `SnapshotBuilder` 两处（即当时唯一传对了的那条路径）。本次补三条新需求：需要数据的组件 SHALL 从调用方接收数据访问对象、未注入时的兜底 SHALL 解析配置而非硬编码路径、隐式兜底 SHALL 留下可观测的痕迹；并修正两条既有场景——「未传 store 时 matching existing behavior」在本变更后不再成立（兜底改为解析配置并告警），「与 `DataStore()` 同库等值」也不再可构造（`DataStore()` 现在解析配置，不能指向任意文件）
- `factor-system`：`因子基类定义` 与 `因子注册表` 两条需求补充「因子从调用方接收数据访问对象」的条款——当前只规定 `compute(date, universe)` 的签名，对数据从哪来只字未提，这正是漏传得以发生的地方
- `strategy-engine`：`策略基类接口` 与 `策略注册表` 两条需求同样补充注入条款。现状描述「接收数据访问对象」但只把它当作 `generate_signals` 的第三个参数，没有约束策略**内部构造的因子**用哪个库——而缺陷恰恰在那里

## Impact

**修改**
- `src/quant_trade/data/store.py`：`DataStore.__init__` 的默认值改为解析配置 + 兜底警告
- `src/quant_trade/strategies/registry.py`：`get` 打开 store 参数
- `src/quant_trade/strategies/base.py`：`Strategy.__init__(store=None)`
- `src/quant_trade/strategies/factory.py`：`build_strategy` 透传
- `src/quant_trade/strategies/factor_ranking.py`：因子工厂调用补 store
- `src/quant_trade/services/strategies.py`、`services/backtest.py`、`services/factors.py`：调用点补 store
- `src/quant_trade/simulator/engine.py`、`simulator/comparison.py`：策略工厂调用补 store
- `src/quant_trade/backtest/engine.py`：兜底调用点核对
- `openspec/specs/factor-system/spec.md`：补 Purpose

**新增依赖**：无。

**验证锚点**
- 把配置指向一个夹具库，跑一次信号生成：全部读数来自该夹具库，仓库自带的 `data/quant.db` 全程不被打开
- 未注入 store 而触发兜底时，日志里有一条 warning
- `simulator/snapshot.py` 既有的注入路径行为不变（它本来就传对了，是回归基线）
- 因子、策略、回测、周报四个域的既有测试全绿——本变更不改变任何计算结果，只改变数据**从哪来**
