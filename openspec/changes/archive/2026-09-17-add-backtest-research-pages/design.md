# Design: 回测研究页面（C4）

## Context

C1 抽取了服务层（`fn(params, ctx) -> Result`），C2 建起运行基础设施（`run` / `run_log` / `artifact` 三表、单 worker 串行队列、统一 `POST /api/runs`、SSE 日志流）与 antd 外壳，C3 补齐了因子域四个页面。`/backtest` 分区已在 `web/src/shell/navigation.tsx:36` 声明但 `implemented: false`，路由落在 `Placeholder`。

回测域的现状是「引擎齐备、通路全无」：

| 能力 | 现状 |
|---|---|
| 引擎 | `backtest/engine.py` 周循环，T+1、涨跌停、停牌、佣金/印花税/过户费齐备；**逐周** `ctx.progress`（engine.py:98）与**周首** `ctx.cancelled()`（engine.py:94-96）已接入 |
| 服务 | `services/backtest.py:197` `run_backtest_service` 已存在并已由 C1 导出；返回 `BacktestResult`（`nav` / `benchmark` / `drawdown` / `trades` / `holdings` / `metrics` / `cash` / `total_value` / `cancelled`），**字段已是纯行**，无 pandas 对象 |
| 持久化 | **零**。`SCHEMA_SQL`（`data/schema.py:10`）八张表无一张与回测相关；结果只活在调用方内存里，进程一退就没了 |
| 任务类型 | `JOBS`（`jobs/registry.py:84`）三项：`data_sync` / `factor_compute` / `factor_ic`，无 `backtest` |
| 前端 | `web/src/pages/backtest/` 不存在，`web/src/api/backtests.ts` 不存在，全仓 `web/src` 内 backtest 仅两处非功能命中（导航项与因子分层页文案） |

因此本变更不是「把已有数据接上页面」，而是**先把结果落库，再建页面**。若不落库，详情页只有两条路：按请求重跑（周循环 + 全区间取数，分钟级）或永远打不开。

两个约束先摆明，它们决定了后面多条决策：

1. `BacktestResult` 已经完成「引擎内部形状 → 纯行」的转换（`services/backtest.py:1-7` 的模块 docstring 明说这是本模块的职责）。落库层因此不需要新的转换层，只需要写入与读回。
2. 落库的主键只能来自 `ctx.run_id`——服务函数不接收 run 标识。而 `NULL_CONTEXT.run_id` 是空串（`services/context.py:61,97`），脚本与测试默认走这条路。

## Goals / Non-Goals

**Goals**

- 回测结果持久化：净值 / 基准 / 回撤序列、交易明细、绩效指标、期末持仓
- 回测提交入口接入统一运行 API（`kind: backtest`），复用进度上报、实时日志、取消、产物登记
- 三个页面：提交与历史列表、详情（净值曲线 / 回撤 / 指标卡 / 持仓 / 交易明细）、多 run 对比
- 详情页与对比页**不重跑回测**：所有数字来自结果表

**Non-Goals**

- 不做逐日持仓快照（引擎只在结束时暴露 `portfolio.holdings`，见 D1）
- 不做参数扫描 / 批量回测 / 自动寻优
- 不做结果的清理与保留策略（`backtest_*` 表只增不删）
- 不生成 HTML 周报（`signals/reporter.py` 的迁移是 C6 的范围）
- 不做任意策略参数覆盖（见 D10）
- 不改动回测引擎的任何交易规则与数值行为

## Decisions

### D1: 四张结果表，持仓单独成表

```sql
CREATE TABLE IF NOT EXISTS backtest_nav (
    run_id      VARCHAR,
    trade_date  DATE,
    nav         DOUBLE,
    benchmark   DOUBLE,
    drawdown    DOUBLE,
    PRIMARY KEY (run_id, trade_date)
);

CREATE TABLE IF NOT EXISTS backtest_trade (
    run_id       VARCHAR,
    seq          INTEGER,
    trade_date   DATE,
    action       VARCHAR,
    ts_code      VARCHAR,
    shares       INTEGER,
    price        DOUBLE,
    commission   DOUBLE,
    stamp_duty   DOUBLE,
    transfer_fee DOUBLE,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS backtest_metric (
    run_id       VARCHAR,
    metric_name  VARCHAR,
    metric_value DOUBLE,
    PRIMARY KEY (run_id, metric_name)
);

CREATE TABLE IF NOT EXISTS backtest_position (
    run_id        VARCHAR,
    ts_code       VARCHAR,
    shares        INTEGER,
    avg_cost      DOUBLE,
    current_price DOUBLE,
    market_value  DOUBLE,
    PRIMARY KEY (run_id, ts_code)
);
```

proposal 只列了三张表。**持仓补成第四张**：`BacktestResult.holdings` 是 `list[HoldingRecord]`（`services/backtest.py:84-91`），每个元素带 `ts_code`，塞进 `backtest_metric` 会退化成 `metric_name = "holding:600000.SH"` 这种把实体编码进键名的写法，页面上要反解字符串才能排序与筛选。单独成表的代价是四行 DDL。

`backtest_metric` 用键值对而非定列：`compute_metrics`（engine.py:275）返回的指标是字典（`total_return` / `annual_return` / `annual_volatility` / `sharpe_ratio` / `max_drawdown` / `calmar_ratio` / `win_rate` / `benchmark_return` / `excess_return` / `turnover`，加 engine 覆盖的 `total_trades`），加一个指标就该只改计算函数，不该改表结构。页面上「指标卡」按固定顺序渲染已知指标名，未知指标降级为普通行，不丢弃。

**两个写入期的形状差异必须处理**，否则会写错或直接抛异常：

- `trade_log` 的每一项 `date` 字段在引擎里是**字符串**（`portfolio.py` 买卖时写入 `date` 为 `str`，`services/backtest.py:309` 的 `_to_trade` 也按 `str` 取），写进 `trade_date DATE` 列前要解析回日期。解析失败的行记 warning 并跳过，不整批失败。
- 空回测（无交易日 / 空股票池）走 `_empty_result`（engine.py:435-443），`metrics` 是**空字典**且 `nav_series` 是空 `Series`。此时四张表都不写行，产物登记也随之跳过（既有 mapper 已按 `row_count > 0` 过滤）。

**替代方案（未采纳）**：把净值序列存成 parquet 并在 `artifact` 表登记。否决原因同 C3 决策 D2——`artifact` 是运行产物的索引不是查询表，对比页要按 `run_id` 点查多条曲线，每次全读 parquet 不合理。

### D2: 回测元数据不单独成表，从 `run` 与净值区间推导

策略名、实际起止日、初始资金、取消与否都不再落一张 `backtest_run` 表：

| 展示项 | 来源 |
|---|---|
| 策略名 / 请求参数 | `run.params_json`（`GET /api/backtests` 返回时解析） |
| 实际起始日 / 结束日 | `backtest_nav` 的 `MIN(trade_date)` / `MAX(trade_date)` |
| 是否取消 | `run.status == 'cancelled'` |
| 提交时间 | `run.created_at` |
| 期末现金 / 期末总市值 | `backtest_metric` 的 `cash` / `final_value` 两行 |

**理由**：请求参数与实际起止日**不是一回事**——`run_backtest_raw` 会把起始日归一化到其后第一个交易日（`backtest-engine` spec 的「回测起始日归一化」），把请求区间抄进结果表就成了第二份可能过期的真相。真正的事实是净值序列覆盖了哪些日期，页面上显示的也就该是它。

**替代方案（未采纳）**：新建 `backtest_run` 汇总表存 strategy / start / end / cancelled。否决原因：四列里三列是别处已有的信息的副本，同步成本换不来任何查询收益。

### D3: 落库以 `ctx.run_id` 为键，`run_id` 为空时不落库

`run_backtest_service` 在 `ctx.run_id` 非空时写入四张表，为空时按原样返回内存结果、不写库、不报错。

**理由**：`NULL_CONTEXT.run_id` 是空串，脚本与单测默认用它（`service-layer` spec 的「无接收器的默认上下文」要求这条路径必须正常完成）。若照写，一次脚本调用会往库里塞一批 `run_id = ''` 的行，且第二次调用与第一次的主键完全相同——`INSERT OR REPLACE` 会静默覆盖，两次实验的结果互相顶掉，看起来「成功」而数据已经串了。宁可写不进去也不能写错。

**代价**：脚本调用者若期待落库，会静默不生效。缓解方式写在 `run_backtest_service` 的 docstring 里，并在 `run_id` 为空时 `ctx.log(..., level="info")` 说明本次未落库（`NULL_CONTEXT` 本身没有 sink，该日志对脚本不可见，所以 docstring 是主要出口）。

**替代方案（未采纳）**：新增 `persist: bool` 参数或 `persist_run_id: str | None` 字段。否决原因：`run_id` 已经是上下文里的事实，再引入一个平行的标识符意味着两者可能不一致。

### D4: 注册 `kind: backtest`，零新增运行 API 路由

`JOBS` 增加一项：

| kind | params_model | service_fn | artifacts |
|---|---|---|---|
| `backtest` | `BacktestParams` | `run_backtest_service` | `backtest_nav` 表（行数）+ `backtest_metric` 表（行数） |

`POST /api/runs` 的通路、参数校验（`extra="forbid"` + 422）、SSE 日志、取消、产物登记全部复用（`api/runs.py:51-75`）。前端提交走 `runsApi.submit('backtest', {...})`。

产物登记两条而非一条：净值行数回答「这条曲线有多少点」，指标行数回答「这次运行算了哪些指标」，两者都不是对方的函数。`ArtifactDraft` 的 `meta` 记 `strategy` / `start` / `end`，使产物列表页不必反解 `params_json` 就能看出这是哪次回测。

**理由**：引擎早已在周循环上报进度并在周首检查取消（`backtest-engine` spec 的「回测过程上报进度」「回测过程可取消」），服务层也已是现成的，注册一行就得到一个可提交、可取消、可看日志的任务——这正是 C2 决策 5 承诺的收益。

### D5: 完成与取消都落库；修正取消时仍上报 100% 进度

取消的回测保留已跑出的净值与交易（`BacktestResult.cancelled = True`），不落库才是浪费——那些周已经真实跑完了。

同时修正 `services/backtest.py:181`：`ctx.progress(1.0, "Backtest complete")` 当前**无条件**执行，取消时也把进度推到 100%。这与 C3 在 `compute_alpha158` 上发现并修掉的是同一类缺陷（任务 11.9），既然后果一样（取消的任务显示为已完成），修法也一样：取消路径上报截止处的完成度或不覆盖进度。

**为什么在本变更内做**：注册成任务类型之前，进度条只有脚本在读；注册之后它就是界面上的承诺。

### D6: 读取路径独立成 `services/backtest_query.py`

新增四个只读服务（均 `fn(params, ctx=RunContext) -> dataclass`）：

| 函数 | 说明 |
|---|---|
| `backtest_run_list` | 回测运行列表：run 状态 + 策略 + 实际区间 + 关键指标，服务端分页 |
| `backtest_detail` | 单个 run 的元信息 + 指标 + 净值序列 + 期末持仓 |
| `backtest_trades` | 交易明细，服务端分页 |
| `backtest_comparison` | 多个 run 的净值叠加与指标对照 |

**理由**：若把聚合写在 `api/backtests.py` 里就是「适配器含领域逻辑」，违反 `service-layer` spec 的「适配器不含领域逻辑」。

**与 C3 的差异（验证阶段澄清）**：C3 把因子域的**计算**与**读取**放进同一个 `services/factor_analysis.py`；本领域不是。回测的计算入口是 C1 就已存在的 `services/backtest.py`（`run_backtest_service`），读取服务因此另立一个只读模块，而不是把两者合并改写 C1 的既有模块。命名（`*Params` / `*Result` dataclass / `MAX_*` 常量）与 C3 保持一致，差异只在模块边界，且这个边界更贴 service-layer 的「一个领域一个入口」——回测域的两个模块合起来仍是唯一入口。

SQL 放在 `backtest/result_store.py`（对应 C3 的 `factors/ic_store.py`），服务层只做编排。

**列表视图读 `run` 表的说明**：`backtest_run_list` 需要 `run.status` 与 `run.created_at`，唯一来源是 `run` 表。`result_store` 对 `run` 表**只读不写**——写入 `run` 行的职责仍完全属于 `RunStore`。若不这么做，就只能让 API 层把 `RunStore` 的结果与结果表拼起来，那正是 service-layer spec 禁止的。

**替代方案（未采纳）**：给 `RunStore.list_runs` 加 `kind` 过滤，前端先取回测 run 再逐个拉详情。否决原因：列表页要显示每个 run 的年化/回撤，逐个拉就是 N+1 次请求；而这些指标本来就在 `backtest_metric` 里，一次 JOIN 即可。

### D7: `api/backtests.py` 只读，`compare` 路由先于 `{run_id}` 注册

`create_backtests_router(config) -> APIRouter`，前缀 `/api/backtests`：

```
GET /api/backtests                  回测运行列表（分页）
GET /api/backtests/strategies       已注册策略名（提交表单的选项）
GET /api/backtests/compare          多 run 净值叠加与指标对照
GET /api/backtests/{run_id}         单个 run 的指标 / 净值 / 持仓
GET /api/backtests/{run_id}/trades  交易明细（分页）
```

写路径（发起回测）一律走 `POST /api/runs`，本路由不含 POST。

**`/strategies` 是实现期补上的**：提交表单要一份策略清单，而全仓没有暴露 `list_strategies` 的路由。放在本域下而不是新开一个路由文件，是因为回测表单是它唯一的消费者；前端硬编码策略名会在下一个策略注册时静默漂移。它同样必须排在 `/{run_id}` 之前。

**`/compare` 与 `/strategies` 必须声明在 `/{run_id}` 之前**：FastAPI 按声明顺序匹配，若 `{run_id}` 在前，这两个路径会被当作 run_id 吃掉，返回 404「运行 compare 不存在」。这条不是风格问题，是会让页面永远打不开的顺序约束，因此写进任务与测试。

请求上下文用与 `api/factors.py:249-261` 同款的 `_context`（每请求自开 `DataStore`，**默认配置而非只读**——DuckDB 同进程内只读与读写混用会抛 `ConnectionException`）。路由在 `runtime/app.py` 注册，位置在 SPA 回退之前。

### D8: 对比 run 数上限与交易明细分页上限走 pydantic 校验

`BacktestComparisonParams.runs` 施加 `min_length=1` 与 `max_length=8`，超限在参数校验阶段返回 422（`api/factors.py:234-246` 的 `_validated` 同款）。

**理由**：上限是接口契约而非前端约束（同 C3 决策 D7）。八条净值曲线叠加已经到可读性的边界，再多就该换一种呈现方式，而不是把图画得更花。

交易明细分页参数走 `limit ≤ 500` 的 `Query` 约束（`api/factors.py:49` 同款），`MIN/MAX` 页码由服务端夹紧，负 offset 直接 422。

### D9: 前端三页 + 分区内 `Tabs`，图表按需注册补 `PieChart`

`web/src/pages/backtest/` 下三个页面 + `BacktestNav.tsx`（照 `pages/factors/FactorNav.tsx` 的 `Tabs` + `activeKey={location.pathname}`），`navigation.tsx:36` 的 `implemented` 置 `true`，`routes.tsx` 补三条显式路由：

| 路径 | 页面 |
|---|---|
| `/backtest` | 提交表单 + 历史列表 |
| `/backtest/:runId` | 详情：净值/基准/超额曲线、回撤面积图、指标卡、期末持仓、交易明细 |
| `/backtest/compare` | 多 run 净值叠加与指标对照 |

`web/src/charts/echarts.ts` 当前按需注册了 Line / Bar / Heatmap，**需补 `PieChart`**（持仓权重饼图）。`DataZoom` 组件 C3 已注册，本次直接复用；proposal 把缩放列为选 ECharts 的主要理由，详情页与对比页的净值图都开 `inside` + `slider` 两种缩放。回撤面积图与超额图不需要新注册——Line 加 `areaStyle` 或 `markLine` 即可。

客户端封装新增 `web/src/api/backtests.ts`，走 `http.ts` 的 `request<T>`；数据获取沿用既有手写模式（`useState` + `useEffect` + `inFlight` ref），不引入数据请求库。样式全部来自 antd 组件与 `shell/theme.ts` token，不新增任何 `.css` / `.less` / `.scss`。

**实现期注意（C3 实测踩到，务必照做）**：图表的共享封装必须从 `echarts-for-react/esm/core` 引入。`lib/` 是 CJS，深层路径导入会跳过打包器的 interop，React 收到模块命名空间对象而非组件，页面直接抛 React error #130 且 TypeScript 与构建都不报错。本变更沿用 C3 已建好的 `web/src/charts/EChart.tsx`，不重复这个坑。

### D10: 提交表单只覆盖 `top_n`，不做任意策略参数覆盖

`BacktestParams` 新增 `top_n: int | None`（`ge=1`），`run_backtest_raw` 在 `build_strategy` 之后以 `SignalParams` 同款写法应用到策略（`services/strategies.py:108-109`）。

**理由**：`build_strategy`（`strategies/factory.py:13`）把 `CONFIG_ATTRIBUTES` 从配置写进策略实例。要覆盖就出现了「参数的真相在 config 还是在表单」的问题——这是 C3 决策 D8 拒绝把因子启用状态写回配置的同一问题。`top_n` 是唯一在 `SignalParams` 已有先例、且语义明确（持仓只数）的覆盖项，把它单列成字段即可。

**替代方案（未采纳）**：`strategy_params: dict[str, Any]` 直接 `setattr`。否决原因：那是一条第 3 方（前端）到策略实例的任意属性写路径，可以覆盖 `predictions_path` 这类路径字段，且不受 `ServiceParams` 校验保护。要支持任意覆盖，正确的做法是为每个策略定义参数模型，不在本期。

### D12: 详情页的两条曲线按期初归一后再画

引擎返回的两条序列**单位不同**：策略净值是账户的绝对市值（含初始资金，默认 10 万），基准是已归一化到 1.0 的价格指数（`_fetch_benchmark`，engine.py:359-366）。直接画在同一坐标轴上，基准会被压成贴着 0 的一条直线，而「超额收益 = 策略 − 基准」算出来的其实就是账户余额本身。

因此页面在绘制前把两条序列各自除以自己的首个非空值，换算成「期初 = 1」的增长曲线，超额为两条归一曲线的差。坐标轴名写作「净值（期初 = 1）」。

**验证阶段实测踩到**：首版未归一时基准线不可见、「超额」曲线的量级是 10 万而非 ±0.1，文字断言与构建都不会发现——只有看图才知道。

**对比页同样要归一**（首版本决策写的是「对比页不受影响」，那句是错的）：提交表单暴露了初始资金，一次 20 万起始的运行在绝对市值轴上会整体高于 10 万起始的运行，与谁跑得好无关。对比页据此一并改为按期初归一。这正是对抗式复核抓出来的——同一条决策的前后两半自相矛盾。

### D11: 净值表存基准与回撤两列，写入时左连接

`backtest_nav` 一行三值：`nav`（策略净值）、`benchmark`（基准净值）、`drawdown`（回撤比例）。

- **基准**不可从策略净值推导（`_fetch_benchmark` 单独取指数收盘并归一化到 1.0，engine.py:359-366），必须存。
- **回撤**理论上可由 `nav` 现算（`services/backtest.py:296-301` 的 `cummax` 三行），但详情页与对比页都要画它。存一列 `DOUBLE` 的成本远低于两条路径算出的回撤在某天不一致的风险——那会让同一张图上回撤面积与净值曲线互相矛盾。

**写入按净值日期左连接**：基准序列与净值序列都由交易日历驱动，正常情形逐日对齐，但指数数据缺失时基准会短一截。此时**丢弃整行是错的**（策略净值是真的），正确做法是保留行、`benchmark` 留 NULL，页面断线显示而不是把这一天从净值曲线上抹掉。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| 详情页被误读为「实时结果」，实际是入库时的快照 | 页面标注数据生成时间（`run.finished_at`）；区间以净值序列覆盖范围为准（D2），不显示请求参数里的区间 |
| `backtest_nav` 行数随区间线性增长（十年日频约 2500 行/run） | 行数与 `daily_kline` 同阶，DuckDB 列存点查单 run 是毫秒级；对比页限制 run 数上限 8（D8） |
| 交易明细可能上万行，一次返回会拖垮页面 | 交易明细走服务端分页（`backtest_trades`），前端 `Table` 超过 100 行开虚拟滚动，沿用 data-console spec 的既有约束 |
| `ctx.run_id` 为空时静默不落库，脚本调用者可能误判 | D3：docstring 明写；任务中要求测试覆盖「空 run_id 不写库且正常返回」 |
| `result_store` 读 `run` 表跨越了 runs 域 | D6：只读、且仅用于列表视图；写入 `run` 仍只属 `RunStore`。任务中要求测试断言不产生 `run` 行 |
| 取消的回测落库后，净值曲线在区间中途截断 | 页面按 `run.status` 显示「已取消」标签并标注曲线截止处（`cancelled` 场景写入 spec） |
| `compare` 被 `{run_id}` 吃掉导致路由 404 | D7：声明顺序写成任务，并有专门的 API 测试（C3 的 `test_api_factors.py` 已有「不打到 SPA 回退」同类断言） |
| 四张新表让「数据总览」页多出四行无意义的空表 | 四张表都注册进 `TABLE_NAMES`；只有真有日期列的 `backtest_nav` / `backtest_trade` 注册进 `TABLE_DATE_COLUMNS`，另两张缺省即无边界。无数据时 `table_stats` 自然返回 0 行 |
| 列表页按 `kind` 过滤 `run` 表可能全表扫 | 不加新索引：`backtest_metric` 的 PK 前缀已覆盖单 run 点查，列表按 `run.created_at` 排序走既有 `run_created_at` 索引，回测 run 的绝对数量是百级 |

## Migration Plan

全量 additive，无破坏性变更：

1. `data/schema.py` 的 `SCHEMA_SQL` 追加四张 `CREATE TABLE IF NOT EXISTS`（仓库无 schema 版本机制，也无 `ALTER TABLE` 用法；新表只能追加）。同时把四张表加入 `data/store.py` 的 `TABLE_NAMES`，`backtest_nav` / `backtest_trade` 加入 `TABLE_DATE_COLUMNS`（日期列 `trade_date`），使 `/api/data/status` 与数据总览页自动纳入。`backtest_metric`（键值对）与 `backtest_position`（一持仓一行）**没有日期列**，不注册——注册了会让 `table_stats` 对有行的表执行 `MIN()` 一个不存在的列，`/api/data/status` 直接 500（验证阶段实测踩到，已补测试覆盖）。
2. 新增 `backtest/result_store.py`、`services/backtest_query.py`、`api/backtests.py` 三个模块，均为新增；既有函数签名只**新增**字段与默认参数（`BacktestParams.top_n`），不改既有字段语义。
3. `JOBS` 新增一项，既有三项不动；`POST /api/runs` 路由零改动。
4. 前端新增 `pages/backtest/` 与 `api/backtests.ts`，`navigation.tsx` / `routes.tsx` / `charts/echarts.ts` 各改一处。
5. 回滚：删除新模块与 `JOBS` 一行即回到当前状态；四张表留在库中不影响其他表（可手工 `DROP`）。

## 验证阶段修正（verify 后补记）

`/opsx:verify` 的对抗式复核（三个独立读者按场景逐条比对代码与测试）发现并修掉了以下偏差，全部已在代码、测试与 spec 中同步：

| 问题 | 修正 |
|---|---|
| **空结果仍写 2 行 `backtest_metric`**（`cash` / `final_value` 被无条件并入），并据此登记产物——违反 spec 的「空结果不写行」 | `_metric_values()` 仅在 `raw.metrics` 非空时才补期末组合状态；补真实空路径测试（窗口落在日历之外） |
| 交易明细丢弃坏日期后 `seq` 出现空洞，spec 要求「从 1 连续递增」 | `seq` 改为在过滤后重新编号 |
| 列表排序用 `COALESCE(started_at, created_at)`，与 spec 的「按提交时间倒序」及页面展示的「提交时间」列不符 | 改为 `ORDER BY created_at DESC` |
| 列表不展示进行中运行的进度，spec 要求展示 | `run.progress` 一路透出到行（`BacktestRunRow` → `BacktestRunSummary` → API → 表格列） |
| 详情页把取消时刻标为「完成于」 | 取消时标注为「取消于」 |
| 详情页请求失败（非 404）只弹 toast，随后 `return null` 留下空白页 | 增加持久错误态与重试入口 |
| 空列表 / 空交易仍渲染空表格，spec 明确 `SHALL NOT 渲染空表格` | 两处都改为空态说明（与「期末空仓」一致） |
| 对比页未体现「不按同一区间直接比大小」，且不同初始资金的运行在绝对市值轴上不可比 | 曲线按期初归一；对照表增加「覆盖交易日」列；取消行加说明性 Tooltip；表外保留显式提示 |
| 对比页对每个 run 各发两条查询（3 个 run 共 7 次 SQL） | 新增 `get_backtest_runs()` 批量取元信息，对比路径降为常数次查询 |
| `_upsert` 缺列时抛裸 `KeyError`，与 `ic_store.save_ic_series` 的具名 `ValueError` 不一致 | 补上同款列名校验 |
| `api/backtests.py` 的 `_iso_time` 与 `_iso` 函数体逐字节相同 | 合并为一个 `_iso`，签名放宽为 `date \| datetime` |

**未改代码、改 spec 措辞的两处**（实现更好，spec 写得过头）：

- 「对比 SHALL NOT 在页面加载时自动发起」——从列表页带着已选运行跳转进来**是**用户动作，据此实现为「带参数即触发，裸开不算」。spec 已改写以区分这两种进入方式。
- 「策略净值、基准净值与超额收益的多线曲线」——超额与净值量级不同，同轴绘制不可读，实现为两张图。spec 已改写。

### 第二轮复核补记

第二轮对抗式复核（重跑场景映射 + 逐条读浏览器脚本）又发现一处 CRITICAL 残留与若干覆盖缺口，均已修正：

- **空路径判定改用「是否产出净值或成交」**。第一轮把门放在「`metrics` 是否非空」上，但引擎无条件写入 `total_trades`（engine.py:263），因此「第一周前取消」这条路径的 `metrics` 是 `{"total_trades": 0.0}`——非空，门没拦住，照样写 3 行指标并登记产物。三种空路径（空区间、空股票池、第一周前取消）现在统一由 `_produced_results()` 判定，依据是净值序列或成交记录是否真的存在。
- **浏览器验证不再是丢弃物**。原先它只活在 `.scratch/`（gitignored）。现已移入 `scripts/e2e_backtest_pages.mjs` 与 `scripts/seed_backtest_e2e_db.py`，头部写明前置条件，以及它**看不到**的东西（ECharts 把坐标轴画进 canvas，故「是否按期初归一」只能由源码契约与截图确认）。
- **补上此前无测试的路径**：空股票池分支、对比查询次数不随 run 数增长、读取路径不重跑引擎、同参数重跑产出两份结果、数据总览含回测表名、列表载荷的 `progress` 透传。
- **前端三处缺陷**：`runId` 变化时未重置状态（React Router 复用实例，会把上一个 run 的曲线留在新 URL 下）；列表与交易明细把「请求失败」显示成「没有数据」；无基准时「基准收益 / 超额收益」显示引擎留下的 `0.0`。

## Open Questions

- 列表页默认展示多少条？倾向 20 条一页，与 `RunStore.DEFAULT_PAGE_SIZE` 对齐，待实现时确认。
- 对比页是否要做「同一策略不同参数」的语义分组（比如按 `top_n` 自动配对）？倾向不做——对比的语义应由用户勾选决定，自动分组会引入猜测。
- 持仓权重饼图在持仓数很多时（默认 `top_n = 15`）是否需要「其余合并为其他」？15 块饼图尚可读，倾向直接全画，等实测再定。
- **本变更范围外但已发现**：`signals/reporter.py` 的周报渲染仍接收 `RunContext` 之外的引擎原生输出（`RawBacktest.as_reporter_input`，`services/backtest.py:116`）。本变更落库后，C6 可以直接从结果表取数生成周报，届时 `as_reporter_input` 是否还需要保留，留待 C6 决定。
