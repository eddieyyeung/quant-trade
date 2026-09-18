# Design: 策略研究页面

## Context

平台八个分区已建七个（数据 / 因子 / 模型 / 回测 / 仿真 / 报告 / 任务中心），`/strategies` 是最后一个 `implemented: false`。策略域的后端早就完整：

| 能力 | 现状 |
|---|---|
| 注册表 | `strategies/registry.py` 的 `@register_strategy`，已注册 `factor_ranking`（`factor_ranking.py:15`）与 `model_ranking`（`model_strategy.py:15`） |
| 清单 | `services/strategies.py:128` 的 `list_strategies()`，返回已注册策略名 |
| 信号 | `services/strategies.py:94` 的 `generate_strategy_signals(params, ctx) -> SignalSummary`，返回 `signal_date` / `strategy` / `universe_size` / `orders` / `weights` |
| 日期语义 | `most_recent_friday(store, as_of)`（`:66`）——`as_of` 为空时回退到最近的**开市**周五，注释写明「mid-week must still key off the previous Friday」 |
| 规范 | `openspec/specs/strategy-engine/spec.md`，五条需求（基类接口 / SignalResult / 多因子打分排名 / 注册表 / 参数化配置） |
| 路由 | **不存在**。`api/` 下只有 data / factors / backtests / models / reports / runs / log_stream |
| 页面 | **不存在**。`web/src/pages/` 下只有 backtest / data / factors / jobs / models / reports / simulator |

信号目前被两个消费者**顺带**产生，都不是可查记录：

- 周报：`services/report.py:102` 调 `generate_strategy_signals`，把订单转成 dict 交给模板烘进 HTML
- 仿真：`simulator/snapshot.py` 的 `_build_strategy_signals` 自己算一遍，写进会话的 `decisions.json`

于是「某策略在某天给出什么信号」这个问题今天只能靠生成一整份周报再打开 HTML 来回答。而且 `factor_values` 是 `INSERT OR REPLACE` 的（`factors/alpha158/storage.py`），重算会覆盖——不落库的话，「回看上周三的信号」在数据被重算之后根本无法兑现。

**一个先决约束**：`generate_strategy_signals` 是全项目的共用入口，周报管线在跑。任何加在它身上的行为都会同时改到周报。本设计因此把它**完全不动**。

## Goals / Non-Goals

**Goals**

- 信号按 `run_id` 落库，使任意历史日期的信号可回看，不依赖 `factor_values` 还在不在
- 接入统一运行接口（`kind: strategy_signals`），复用进度、实时日志、取消与产物登记
- 策略分区两页：策略清单（含发起信号生成与历史）、单次信号详情
- `generate_strategy_signals` 的签名与行为零改动，周报管线不受影响

**Non-Goals**

- 不改 `generate_strategy_signals`、不改 `most_recent_friday` 的回退逻辑
- 不给仿真域换血统：`_build_strategy_signals` 继续自己算（它是快照构建的一部分，有 `skip_heavy` 语义），不去调新服务
- 不落 `SignalSummary.weights`（见 D3）
- 不做策略参数的可视化编辑。`SignalParams` 已经能覆盖 `as_of` / `strategy` / `top_n` / `universe`，但策略自己的参数（如 `config.strategy.params`）仍走配置文件；在这一版里暴露它们等于在没有参数 schema 的前提下发明一个表单
- 不做策略间的信号对比（回测分区已有通用的多 run 对比形态）
- 不做信号的收益回测——那是回测分区的事，且它读的是 `backtest_*` 表

## Decisions

### D1: 信号落库，而不是每次重算（**未与用户确认，按推荐执行**）

**先说清楚**：这个变更的两个岔口（同步读 vs 走运行；落不落库）在开工前问过但没得到答复，用户直接要求快进。这里取的是我当时给出的推荐，并附上被拒的替代方案。若不同意，改 `design.md` 与两份 spec 即可，成本很低——`api` 与页面形状基本不变。

落库。理由：

1. **可回看是这个页面存在的理由。** 不落库，页面就只是「现在算一遍给你看」，那和已有的周报信号表、仿真策略建议没有区别，不值得一个新分区。
2. **`factor_values` 会被重算覆盖。** 信号是 `(factor_values, 策略参数)` 的纯函数，而前者的历史随时可能被一次重算改掉。把派生结果冻下来，是唯一能让「上周三的策略说了什么」有确定答案的做法。
3. **与仓库一贯做法一致。** 回测结果（`backtest_*`）、模型评估（`model_*`）、因子 IC（`ic_series`）全部落库，页面对应的文档明写「reads them instead of re-running」。`backtest/result_store.py` 的模块 docstring 第一段就是这个立场。

**替代方案（未采纳）**：页面同步读、不落库。省掉一张表、一个持久化模块、一个产物映射与读取服务，量级大约减半。否决原因：它回答不了「回看」，而 `factor_values` 的可覆盖性让这个缺陷不是理论上的。

### D2: 一张 `strategy_signal` 表，形状照 `backtest_trade`

```sql
CREATE TABLE IF NOT EXISTS strategy_signal (
    run_id     VARCHAR,
    seq        INTEGER,
    trade_date DATE,
    strategy   VARCHAR,
    ts_code    VARCHAR,
    direction  VARCHAR,
    target_pct DOUBLE,
    reason     VARCHAR,
    PRIMARY KEY (run_id, seq)
);
```

`(run_id, seq)` 做主键、`seq` 由写入端连续编号，与 `backtest_trade`（`data/schema.py:140-152`）逐字同构。两张表存的是同一类东西——一串有序的、属于某次运行的交易意图——没有理由长得不一样。

`strategy` 冗余在每一行上，而不是另开一张 run 级元信息表：它在一行里只有一个值，而读取路径（单次运行的全部信号、运行列表的每行）都要用它，JOIN 回 `run.params_json` 解析出策略名是更差的取法。

新表 SHALL 由 `SCHEMA_SQL` 以 `CREATE TABLE IF NOT EXISTS` 建立，SHALL NOT 依赖 `ALTER TABLE`；SHALL 同时加入 `store.py` 的 `TABLE_NAMES` 与 `TABLE_DATE_COLUMNS`（日期列 `trade_date`），使数据总览页显示它的行数与日期跨度。

### D3: `generate_strategy_signals` 不动，落库走新的 `run_strategy_signals`

```python
# services/strategies.py —— 既有函数，签名与行为一律不变
def generate_strategy_signals(params: SignalParams, ctx: RunContext = NULL_CONTEXT) -> SignalSummary: ...

# 新增：在其之上落库
def run_strategy_signals(params: SignalParams, ctx: RunContext = NULL_CONTEXT) -> SignalSummary:
    """Generate signals and persist them under ``ctx.run_id``."""
```

**为什么不直接在 `generate_strategy_signals` 里按 `ctx.run_id` 落库**（像 `train_model` 与 `run_backtest_service` 那样）：那个函数被周报管线调用（`services/report.py:102`），而它跑在**周报自己的 run_id** 下。`train_model` 那样的写法会让每次生成周报都往 `strategy_signal` 里写一批以周报 run_id 为键的行——数据总览里多出一堆来历不明的信号，而它们不对应任何一次「信号生成」任务。周报要的是渲染，不是一条可查记录。

因此落库是**独立入口**的职责。任务类型注册指向 `run_strategy_signals`，`generate_strategy_signals` 保持纯净。

`weights` 不落库：它是目标组合权重（`{ts_code: weight}`），而每一行的 `target_pct` 已经表达了同一个意思的订单侧视图。落两份会让「组合应该长什么样」有两个可能不一致的答案，而**没有任何读取路径需要 `weights`**——页面展示的是订单表。真要组合权重，从订单的 `target_pct` 推。

**落库以 `ctx.run_id` 为键，为空时不落库**：与 `train_model` / `run_backtest_service` 同一条约定。`NULL_CONTEXT.run_id` 是空串，脚本与单测默认走这条路；写不进去也比写错好。空信号集（区间内无交易日、股票池为空）SHALL 不写行。

### D4: 任务类型 `kind: strategy_signals`，产物指向 `strategy_signal`

```python
JOBS["strategy_signals"] = JobSpec(
    kind="strategy_signals",
    params_model=SignalParams,
    service_fn=run_strategy_signals,
    artifacts=_strategy_signal_artifacts,
)
```

参数模型直接复用 `SignalParams`——它已经是这次运行需要的全部输入（`as_of` / `strategy` / `top_n` / `universe`），且 `params_json` 单独就能复现一次运行。

产物映射：

```python
def _strategy_signal_artifacts(result: SignalSummary) -> list[ArtifactDraft]:
    if not result.orders:
        return []
    return [ArtifactDraft(
        kind="table",
        storage=ArtifactStorage.TABLE,
        ref="strategy_signal",
        row_count=len(result.orders),
        meta={
            "strategy": result.strategy,
            "signal_date": result.signal_date.isoformat(),
            "universe_size": result.universe_size,
        },
    )]
```

`universe_size` 放进 `meta` 是因为**它不在表里**：它是运行级事实，行上无从推导。其余三项（策略名、信号日期、信号条数）都能从表里推出来，列表接口 SHALL 从表推导而非从 meta 抄——同 C4 决策 D2 与 C5 决策 D2 的理由，meta 是给「不打开表就能看一眼」用的，不是第二份真相。

未产出信号的运行不登记产物（与 `_weekly_artifacts` 的空 `report_path` 同一条规则）。

### D5: 读取服务与路由

新建 `services/strategy_query.py`，与 `model_query.py` / `backtest_query.py` 同构：

```python
class StrategyRunListParams(ServiceParams):
    limit: int = Field(default=20, ge=1, le=200)
    offset: int = Field(default=0, ge=0)

class StrategySignalQueryParams(ServiceParams):
    run_id: str = Field(min_length=1)
    limit: int = Field(default=DEFAULT_SIGNAL_PAGE_SIZE, ge=1, le=MAX_SIGNAL_PAGE_SIZE)
    offset: int = Field(default=0, ge=0)

@dataclass
class StrategyRunSummary:  run_id, status, strategy, signal_date, order_count, universe_size, progress, created_at, finished_at
@dataclass
class StrategyRunListResult: total, offset, limit, runs
@dataclass
class StrategySignalRow:   seq, ts_code, direction, target_pct, reason
@dataclass
class StrategySignalResult: run_id, found, strategy, signal_date, universe_size, orders, total
```

列表的 JOIN 形状照 `services/backtest_query.py` 的 `backtest_run_list`：`run`（`kind = 'strategy_signals'`）**左外连接**产物的 `meta_json`，再聚合 `strategy_signal` 出 `MIN(trade_date)` / `COUNT(*)` / `MAX(strategy)`。

**左外连接，不是内连接**——这是从上一个变更的缺陷里学到的：产物是在服务返回**之后**才登记的（`jobs/runner.py:148-156`），内连接会让 `pending` / `running` 的运行在跑完之前整个消失，而那正是读者想看着进度条的窗口。运行记录是主表，产物只补 `universe_size`。

路由（只读，无 POST）：

- `GET /api/strategies` → `{items: [...], default: "..."}`，照 `api/backtests.py` 的 `strategies()` 端点形状
- `GET /api/strategies/runs` → 分页历史
- `GET /api/strategies/runs/{run_id}` → 单次信号，未登记 404

`GET /api/strategies` 与 `GET /api/strategies/runs` 的路径不冲突（静态段优先），无需像 `/backtest/compare` 那样操心声明顺序。

### D6: 分区两页，不做详情页的顶部标签

```
/strategies                 策略清单：已注册策略 + 发起信号生成 + 历史运行表
/strategies/signals/:runId  单次信号：摘要 + 调仓信号表
```

`StrategyNav` 两个标签（策略 / 信号），`activeKey={location.pathname}`，与 `BacktestNav` / `ModelNav` 同款。**信号详情页刻意不作为标签**——它由历史行进入，`activeKey` 匹配不到任何标签，标签栏会空白（`BacktestNav.tsx` 的注释已写明这条）。

信号表按后端返回顺序展示，不重排：引擎给出的订单次序是有意义的（先卖后买之类的语义由策略决定），前端排序会毁掉它。

### D7: 股票池默认取指数成分，可覆盖

`SignalParams.universe` 为空时，`generate_strategy_signals` 已经回退到 `get_default_universe()` 推导的股票池（`services/strategies.py:111`）。页面因此把「股票池」做成一个可选的多选框：留空即用默认，填了就覆盖。

不引入「股票池规模」的独立输入——`top_n` 已经表达了持仓数，再加一个池子大小只会让两个数字互相矛盾。

### D8: 页面显示的信号日期是引擎实际用的那个

`most_recent_friday` 的回退意味着用户选周三、引擎按上一个**开市**周五出信号。页面 SHALL 展示引擎返回的 `signal_date`，SHALL NOT 展示用户选的日期——两者不同时，展示后者是在骗人。这与周报「非周五提示」的修正是同一条教训：描述数据，不描述输入。

## Risks / Trade-offs

- **[D1 未与用户确认]** 落库让变更量级约翻倍。→ 决策写在此处并在 proposal 的 Capabilities 里显式列为新增能力；改回同步读只需删掉 `strategy-signal-persistence` spec 与对应代码，页面形状不变。
- **[表会随时间单调增长]** 每次信号生成写 N 行。→ 与 `backtest_trade` / `factor_values` 同一量级，本变更不引入新的保留策略；真要清理是数据管理的事。
- **[新表未被任何既有页面看见]** 加了表却不在数据总览显示，会让人以为没写上。→ D2 明确要求登记进 `TABLE_NAMES` 与 `TABLE_DATE_COLUMNS`，并有对应 scenario。
- **[周报回归]** 本变更最大的风险是不小心改了 `generate_strategy_signals` 的行为。→ D3 把它完全隔离；tasks 里有一条显式断言「周报生成不写 `strategy_signal`」。
- **[空信号集]** 停牌或池子为空时 `orders` 为空。→ 不写行、不登记产物，页面展示「无信号」空态而不是空表格。

## Migration Plan

1. 表结构与持久化模块（`schema.py` / `store.py` / `signal_store.py`）先行，单测可独立验证
2. `run_strategy_signals` 与任务类型注册随后——此时可以跑一次真实任务，在任务详情看到产物
3. 读取服务与路由
4. 前端两页与导航注册
5. 契约测试与端到端核对

**回滚**：全部是新增文件，加四处既有文件的局部修改（`schema.py` 加一张表、`store.py` 两处登记、`jobs/registry.py` 一个 kind、`runtime/app.py` 一行挂载）与两个前端注册点。把 `navigation.tsx` 的 `/strategies` 改回 `implemented: false`、从 `routes.tsx` 移除两条路由，分区即回到占位页；后端新增部分是纯增量，不挂载即不生效。`strategy_signal` 表留着不影响任何既有路径。

## Open Questions

- 策略自身的参数（`config.strategy.params`，如 `factor_ranking` 的因子子集与权重）目前只能改配置文件。要让页面能调，先得给策略参数一个可枚举的 schema——那是 `strategy-engine` 的「策略参数化配置」需求要展开的事，本变更不碰。
- 信号与仿真的关系：仿真自己算一份信号写进 `decisions.json`。两份实现迟早会漂。统一它们是独立变更（要处理 `skip_heavy` 与快照构建的时序），本变更只记下这个事实。
