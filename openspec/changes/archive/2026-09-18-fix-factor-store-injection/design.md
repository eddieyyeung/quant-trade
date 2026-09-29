# Design: 因子与策略的 store 穿透

## Context

信号生成链路上，组件读取的数据库与配置指定的数据库可以是两个不同的文件，而没有任何东西会提示这一点。

**缺陷现场**（`add-strategy-research-pages` 的端到端验证撞出来的）：

```
generate_strategy_signals (services/strategies.py:94)
  ├─ store.get_universe(...)            ← ctx.db，配置指定的库   ✓
  └─ strategy.generate_signals(as_of, universe, store)
       └─ factor_registry.get(fname)    ← 不传 store   ✗
            └─ cls(store=None)
                 └─ self.store = store or DataStore()
                      └─ DataStore()    ← 硬编码 "data/quant.db"
                           ↑ 仓库自带的库，不是配置里那个
```

**两层根因，各自独立：**

| 层 | 位置 | 症状 |
|---|---|---|
| 注册表不吃 store | `strategies/registry.py:24` `def get(self, name: str)` | `cls()` 无从接收，策略只能自己兜底 |
| 兜底硬编码 | `data/store.py:87` `db_path: str = "data/quant.db"` | 漏传时静默落到字面量路径，不读配置 |

第二层才是让第一层长期不可见的那个：**如果兜底会报错或警告，漏传当场就会被发现**。所以本变更同时修两层，而不是只把已知的调用点补一遍——只补调用点，下一个新策略、新因子还会重演。

**现状盘点**（13 处 `store or DataStore(...)` 兜底；本变更实施复审时补上了 D1 初稿漏掉的 `simulator/engine.py` 一行）：

| 位置 | 传 store 了吗 |
|---|---|
| `simulator/snapshot.py:165,185` | ✓ `store=self._store`（**唯一做对的，也是本变更的回归基线**） |
| `strategies/factor_ranking.py:63` | ✗ 手里有 store 却没传 |
| `services/factors.py:157,246,268` | ✗ |
| `strategies/factory.py:18` | ✗ 且 `StrategyRegistry.get` 根本不收 |
| `simulator/engine.py:94,157,212,489`、`simulator/comparison.py:144` | ✗ |
| `backtest/engine.py:48` | 有 `store or DataStore()` 兜底 |
| `simulator/engine.py:36` | ✗ 且**另有一处硬编码字面量** `db_path: str = "data/quant.db"`——D1 初稿漏列，复审时发现并修复（改为 `db_path: str \| None = None`） |

兜底点分布在因子（`momentum.py`×3、`value.py`×3、`quality.py`×2、`alpha158/bridge.py`）、策略（`factor_ranking.py`、`model_strategy.py`）、回测（`engine.py`）三处。

**`build_strategy` 只有两个调用方**（`services/strategies.py:106`、`services/backtest.py:157`），两者都持有 store，改造成本很低。测试里没有任何裸 `DataStore()`，所以改默认值对现有套件的影响面接近零。

## Goals / Non-Goals

**Goals**

- 信号生成全链路只读一个库：配置指定的那个
- 漏传 store 时**可观测**，而不是体现为数字不对
- 把「需要数据的组件从调用方接收数据访问对象」写成基类契约，而不是每个子类各自记得
- 建立一条回归基线：配置指向夹具库时，仓库自带的库全程不被打开

**Non-Goals**

- **不把 `store` 改成必填参数。** 十二处兜底全部改成必填会让每个单测都得构造一个 store，改动量与收益不成比例。本变更让它**不再静默**，把「必填化」留给后续变更（见 Open Questions）
- 不改任何因子的**算法**，不改任何策略的选股逻辑。本变更只改数据**从哪来**
- 不动 `simulator/snapshot.py` 的既有注入路径——它已经是对的
- 不引入依赖注入框架
- 不解决「同一个进程里开多个 `DataStore`」的资源问题（那是 `duckdb-memory-control` 的事）

## Decisions

### D1: 两层一起修，不只补调用点（**未与用户确认，按推荐执行**）

**先说清楚**：开工前把「兜底怎么办」的岔口摆出来问过，用户直接要求快进。这里取的是我当时给出的推荐——**透传 + 让兜底不再静默**——并附上被否的替代方案。

只补调用点是治标：今天把 `factor_ranking` 和 `services/factors` 补上，明天新加一个策略忘了传，同样的静默错误立刻重演。真正的缺陷不是「某几个地方忘了传」，是「忘了传没有任何后果，除了算错」。

**替代方案 A（未采纳）**：只补调用点，兜底原样保留。改动最小，但把地雷留在原地。
**替代方案 B（未采纳）**：把 `store` 改成必填，删掉所有兜底。最诚实，但十二处兜底、每个单测都要构造 store，改动量数倍于本变更，且与「本变更不改变任何计算结果」的约束相冲。见 Open Questions。

### D2: `DataStore(db_path=None)` 解析配置，取代硬编码字面量

```python
def __init__(self, db_path: str | None = None) -> None:
    """...
    ``None`` resolves the configured database — the same rule the app uses
    (``QUANT_CONFIG``, else the default config file) — rather than a literal
    path. A component that forgot to receive a store then reads the *right*
    database instead of a silently different one.
    """
```

沿用 `config.get_config_path()` 的既有规则（`src/quant_trade/config.py:90-97`：`QUANT_CONFIG` 环境变量，否则默认配置文件），不发明第二套解析。

**为什么是解析配置而不是抛错**：抛错会在进程启动路径上炸掉一堆与本次改动无关的调用点（任何模块级构造都会失败），而本变更的约束是「不改变任何计算结果」。解析配置让**结果正确**，警告让**问题可见**——两件事分开做，各自都能在不破坏别处的前提下完成。

**代价（明写在 spec 里）**：`DataStore()` 从此有环境依赖——它的含义取决于进程的环境变量。这是把一个隐式的错误换成一个隐式的正确；遗留的隐性仍在，由 D3 的警告和 Open Questions 里的必填化共同处理。

### D3: 兜底时记 warning，把隐性变可见

走到兜底分支时输出一条 warning，内容含解析出的路径与调用来源（`stacklevel` 指向调用方）。

**理由**：本缺陷之所以潜伏至今，不是因为代码难改，而是因为**没有任何信号**。一条 warning 让它在日志里自己现身，而不必等某天有人注意到数字不对。这也是本变更唯一「新增可观测性」的地方——其余全是接线。

**不记 info 或 debug**：info 会被日常运行淹没，而这正是需要刺眼的事情。**不抛异常**：见 D2 的代价说明。

### D4: 注册表对齐，策略基类补默认构造

```python
# strategies/registry.py —— 与 FactorRegistry.get(name, store=None) 对齐
def get(self, name: str, store: DataStore | None = None) -> Strategy | None:
    cls = self._strategies.get(name)
    if cls is None:
        return None
    return cls(store=store)
```

```python
# strategies/base.py —— 把「策略可以接收数据访问对象」写进基类
class Strategy(ABC):
    def __init__(self, store: DataStore | None = None) -> None:
        self.store = store
```

`FactorRegistry.get` 早已是这个形状，策略侧只是补齐。参数一律用关键字传（`store=store`）。

**子类兼容**：现有两个策略（`factor_ranking`、`model_strategy`）的 `__init__` 都已接受 `store`，无需改动。未声明 store 的子类会在实例化时 `TypeError`——**这是期望的行为**：与其静默拿到一个错误的库，不如当场说不出来。

### D5: `build_strategy` 加参数而不是新函数

```python
def build_strategy(name: str | None, config: AppConfig, store: DataStore | None = None) -> Strategy | None:
```

两个调用方都持有 store（`ctx.db` / `store`），直接传。签名向后兼容（新参数有默认值），不新增第二个工厂函数——一个就够，多一个只会让人猜该用哪个。

### D6: 端到端回归锚点

本变更的验收不能只看单测：缺陷的本质是「对着 A 库跑、结果来自 B 库」，而这在两边数据都齐全的单测里看不出来。

因此加一条**端到端断言**：配置指向夹具库，跑一次信号生成，断言仓库自带的 `data/quant.db` 全程未被打开。做法是指定路径不可读（临时改名或指向一个不存在的文件），使任何落回默认库的尝试当场失败。

这条断言的价值在于它**独立于实现**：无论将来是透传、是必填、还是别的机制，只要还有组件偷偷开默认库，它就会红。

**实施时的两处修正**（初稿写的是「把默认路径指向一个不存在的位置」，落地时改成了猴补）：

1. 机制改为猴补 `quant_trade.data.store._configured_db_path` 使其抛错，而不是指向一个不存在的文件。指向不存在的路径只能抓住「读到了不存在的库」，而猴补能抓住**任何一次没有显式 store 的构造**——包括那些随后会成功打开某个真实文件的。覆盖面更宽。
2. 代价是覆盖面**变窄**在另一处：猴补只能拦住经过 `_configured_db_path` 的落回，拦不住写死在别处的字面量。`simulator/engine.py:36` 的 `db_path: str = "data/quant.db"` 就是这么漏掉的（复审时才发现）——它把路径直接交给 `DataStore(db_path)`，绕过了兜底函数。
3. 该断言对**「开了默认库」敏感，对「注入了一个错的库」不敏感**：夹具下 `orders: 0`，两种情况下都是 0。要覆盖后者需要另一个断言（比对产出与夹具数据），本变更没有加。

### D7: 能力并入既有的 `factor-store-injection`（**用户已确认**）

本变更原本新建能力 `store-injection`。实施到 spec 阶段时发现 `2026-08-07-fix-simulator-memory-leak` 已经建过 `factor-store-injection`，覆盖的正是同一件事（组件从调用方接收数据访问对象），且它有一条场景在本变更后不再成立：

> `get` without store … "SHALL create its own DataStore internally, **matching existing behavior**"

兜底不再「matching existing behavior」——它改为解析配置并告警。并存两个能力会让同一件事有两处说法，且其中一处是错的。

**决定（用户选定）**：delta 目录改名为 `factor-store-injection`，三条新要求仍为 ADDED，另以 MODIFIED 修正上述场景，以及「与 `DataStore()` 同库等值」那条（`DataStore()` 现在解析配置，不再能指向任意文件，该场景的原文已不可构造）。归档后只剩一个能力。

### D8: 兜底的边界——可解析时不中断，不可解析时失败（**复审时补充**）

初稿只写了「兜底不抛异常」，实施后复审发现 `DataStore()` 在缺少 `config/default.yaml` 的工作目录下会抛 `FileNotFoundError`，与那条场景直接冲突。

两个方向：让代码在配置缺失时退回某个字面量（**否决**——那正是本变更要根除的行为），或者把场景的边界写清楚（**采纳**）。兜底不抛异常的理由是「不打断一个本可以正常工作的调用方」；当连该用哪个库都无从确定时，这个理由不成立，此时静默挑一个正是缺陷本身。

代价写进 spec：`DataStore()` 从此要求配置文件可解析。这是把一个隐式的错换成一次显式的失败。

## Risks / Trade-offs

- **[`DataStore()` 有环境依赖]** 含义取决于 `QUANT_CONFIG`，不再是常量。→ D2 明写代价；D3 的 warning 让每次隐式使用都留痕；Open Questions 记下必填化这条最终出路。
- **[子类 TypeError]** 未来新增的策略若不接受 `store`，实例化即报错。→ 这是 D4 刻意选的：比静默读错库好。注册表测试会当场发现。
- **[改动横跨四个域]** 因子、策略、回测、周报都碰。→ 每一步都是「把已有的 store 往下传」，不改任何算法；四个域各有既有测试兜底，且本变更声称不改变任何计算结果。
- **[`backtest/engine.py:48` 的兜底语义]** `.py:48` 的 `ds = store or DataStore()` 在回测域，本变更核对它的调用方是否传了 store，未传的补上；行为不变。
- **[e2e 依赖真实库的既有问题]** `scripts/e2e_report_simulator_pages.mjs` 里那条「真实运行产出不可断言」的注释描述的正是本缺陷。修完它应当能改成对真实运行断言——**那是本变更的验收信号之一**，若仍不可断言，说明还有漏网。

## Migration Plan

1. `DataStore` 默认值 + warning（D2/D3），单测可独立验证
2. 注册表与基类（D4）、`build_strategy`（D5）
3. 各调用点补齐（因子、策略、服务、仿真、回测）
4. 端到端回归锚点（D6）
5. 全量回归：四个域的既有测试 + 浏览器脚本

**回滚**：改动集中在 `DataStore.__init__` 与几处 `get(...)` 的参数上，逐文件可回退；没有任何 schema 变更或数据迁移。把 `DataStore.__init__` 的默认值改回字面量即回到原状（并重新带回本缺陷）。

## Open Questions

- **`store` 最终是否应改为必填？** 本变更让兜底不再静默，但隐式默认仍然存在。彻底的做法是删掉兜底、把 `DataStore` 的构造收敛到一处（进程启动时），所有组件只接收不构造。那是一个更大的变更，且与 DuckDB 同进程多连接的约束（`CLAUDE.md`）相关——值得单独设计。
- **`backtest/engine.py:48` 的兜底该不该一并去掉？** 回测引擎是函数式入口，调用方是否必然持有 store 需要逐个核对；本变更先核对并补齐，不扩大范围。

**实施中撞出的既有缺陷（建议另立变更）**：`data/store.py:208`（`get_universe`）、`:234`（`_universe_from_kline`）与 `:322` 三处股票池查询是 `SELECT DISTINCT ts_code` 且没有 `ORDER BY`，DuckDB 每次进程返回的行序都不同（实测 801 只股票，三次运行三种顺序）。下游 `factor_ranking` 的 `sort_values` 在并列分数上按该顺序破平、`_apply_industry_constraint` 按该顺序截断，于是**同一份代码连跑两次可以给出不同的选股与回测结果**（实测同一模式两次：`total_return` 0.2693 与 -0.1152，持仓从 002821.SZ 变成 002841.SZ）。

这直接打击了本变更「不改变任何计算结果」这条声称的**可验证性**：在 801 只股票的池子上，任何前后比对都会被乱序淹没——`Risks` 里把 6.6 当作验收信号的打算因此只对固定股票池成立。本变更用「固定股票池」绕开它完成了比对（改动前后订单逐值一致），但缺陷本身不在本变更范围内。`scripts/smoke_ml_pipeline.py:49` 的 `np.random.default_rng(hash(c))` 是同类问题（`hash` 按进程随机化，用它做种子等于每次都是新数据）。建议的修法是查询补 `ORDER BY`、并列分数用稳定的破平规则。
