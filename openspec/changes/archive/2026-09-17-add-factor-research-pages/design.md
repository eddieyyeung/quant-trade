# Design: 因子研究页面（C3）

## Context

C1 抽取了服务层（`fn(params, ctx) -> Result`），C2 建起了运行基础设施（`run` / `run_log` / `artifact` 三表、单 worker 串行队列、统一 `POST /api/runs`、SSE 日志流）与 antd 外壳。`/factors` 分区已在 `web/src/shell/navigation.tsx:33` 声明但 `implemented: false`，路由落在 `Placeholder`。

因子域现状是「半成品 + 空白」：

| 能力 | 现状 |
|---|---|
| 因子计算 | `compute_factors`（8 个手工因子，逐股票 pandas，**不落库**）与 `compute_alpha158`（158 因子，polars 向量化，落 `factor_values`）双轨并存 |
| IC 分析 | `compute_ic_series`（`factors/analysis.py:92`）**逐日期现算**：每个日期 1 次因子查询 + 1 次日线查询 = `2N` 次往返；结果只以 dict 返回，`factor_name` 除日志外无用途，**从不落库** |
| 分层回测 | 不存在。全仓库 `quantile\|分层\|layered` 仅命中 Alpha158 时序算子 `ts_quantile`（`alpha158/operators.py:47`），与截面分组无关 |
| 相关性 | 不存在。无 factor-factor 相关函数 |
| 衰减分析 | 不存在。`decay\|衰减\|long_short\|多空` 零命中 |
| 因子名列表 | `services/queries.py:67` `list_factor_names()` 已存在（`SELECT DISTINCT factor_name FROM factor_values`），但无**逐因子覆盖度** |
| 前端 | `web/src/pages/factors/` 不存在；ECharts 与 `echarts-for-react` 已在 `web/package.json` 声明但 `web/src` 内零引用 |

同时，下游 C6（`migrate-report-and-simulator-ui`）的周报「因子 IC 面板」当前恒空，其 proposal 明确依赖本变更的 `ic_series` 持久化。本变更因此承担两项对外承诺：**IC 序列落库**与**因子名/覆盖度查询接口**。

本变更「迁移到页面」与「从零实现」各占一半：页面部分遵循 C2 已确立的壳与运行 API 约定；分层、相关性、衰减、覆盖度则是从零实现。

## Goals / Non-Goals

**Goals**

- 因子分层（quantile）回测：分组净值、多空组合净值，作为纯统计视图而非可交易组合
- 因子相关性矩阵：按日期截面相关再对日期取均值
- IC / RankIC 序列持久化到 DuckDB，支持多 forward period（衰减分析的数据来源）
- 因子持久化覆盖度查询：逐因子的日期范围与条数
- 因子域四个页面可用：因子库 / IC 分析 / 分层回测 / 相关性
- 因子计算与 IC 计算接入统一运行 API（`kind: factor_compute` / `kind: factor_ic`）

**Non-Goals**

- 不做因子挖掘、自动因子生成
- 不做因子正交化 / 对称正交（相关性矩阵只呈现，不消解）
- 不把分层回测接入自研 A 股回测引擎（见 D5）
- 不改动策略引擎、模型链路、回测引擎
- 不把手工因子的「启用开关」写回 `AppConfig` 或数据库（见 D8）
- 不做增量重算调度（`trigger: scheduled` 仍不产生）

## Decisions

### D1: 分层与相关性作为纯计算模块，服务层只做编排

`factors/quantile.py` 与 `factors/correlation.py` 放纯函数（DataFrame in / DataFrame out，不碰 `RunContext`、不开连接），`services/factor_analysis.py` 负责取数、进度上报、取消检查与结果组装。

**理由**：与 `factors/analysis.py`（`compute_ic` / `compute_forward_returns` 为纯函数）一致；纯函数可直接用构造的 DataFrame 做单测，不需要 DuckDB。C1 的 service-layer spec 要求「调用方只做参数构造与结果序列化」，把领域计算留在 `factors/` 是既有分层。

### D2: IC 序列落库，主键含 `forward_period`

```sql
CREATE TABLE IF NOT EXISTS ic_series (
    factor_name    VARCHAR,
    trade_date     DATE,
    forward_period INTEGER,
    ic             DOUBLE,
    rank_ic        DOUBLE,
    sample_size    INTEGER,
    PRIMARY KEY (factor_name, trade_date, forward_period)
);
```

`forward_period` 进主键而不是固定 5 日：衰减分析（不同持有期下的 IC 变化）正是本页要展示的第四类图，同一因子在不同 horizon 下的 IC 必须共存。`sample_size` 记该日期参与计算的有效股票数——截面样本过小时 IC 噪声大，页面据此标注不可信区间。

**替代方案（未采纳）**：把 IC 序列存进通用 `artifact` 表（`storage=parquet`）。否决原因——周报要按 `(factor, date)` 点查最近 N 周，parquet 每次全读，且 `artifact` 是运行产物的索引不是查询表。

### D3: 新增批量 IC 函数，不改 `compute_ic_series` 的逐日契约

新增 `factors/analysis.py: compute_ic_frame(...)`：一次性取全区间因子值与日线，在内存中逐日算 IC，返回长表 `DataFrame(factor_name, trade_date, forward_period, ic, rank_ic, sample_size)`。既有 `compute_ic_series(store, factor_name, factor_compute_fn, ...)` **签名与语义不变**。

**理由**：`compute_ic_series` 接收 `factor_compute_fn`（单日期闭包），是为「未落库的即时计算因子」设计的——`Alpha158Factor.compute` 缓存未命中时会现算单日。批量路径只对**已落库**因子成立，二者契约不同，强行合并会让桥接路径退化成 N 次全量重算。

两者的 IC 数学共用同一个 `compute_ic`（`analysis.py:63`，要求有效样本 ≥ 10），不重复实现相关系数。

**验证锚点**：对同一 fixture，`compute_ic_frame` 的逐日结果与 `compute_ic_series` 的逐日结果必须一致（同一天同一 forward_period）。这不只是防回归——逐日实现的 `compute_forward_returns` 按**行位置**取 `closes[p]`（`analysis.py:43-58`），批量实现要复刻这一「取 base_date 当日或之后首个交易日为基准」的语义，否则两条路径同一因子会给出不同 IC。

### D4: `services/factor_analysis.py` 为因子分析唯一入口

新增服务函数（均 `fn(params, ctx=RunContext) -> dataclass`）：

| 函数 | 落库 | 说明 |
|---|---|---|
| `compute_factor_ic` | 是 → `ic_series` | 多因子 × 多 forward_period 批量计算并写库 |
| `factor_quantile_backtest` | 否 | 现算分组净值与多空组合 |
| `factor_correlation` | 否 | 现算相关矩阵 |
| `factor_coverage` | 否 | 逐因子持久化覆盖度 |
| `factor_ic_series` | 否（只读） | 读 `ic_series` 并算汇总指标 |
| `factor_ic_decay` | 否（只读） | 读 `ic_series` 并按持有期汇总 |

后两个是**读取**路径：任务 7.3 / 7.4 要求接口返回汇总指标与衰减对比，若把这部分聚合写在 `api/factors.py` 里就是「适配器含领域逻辑」，违反 service-layer spec。计算与读取同放本模块，`services/factor_analysis.py` 仍是因子分析的唯一入口。

`compute_factor_ic` 支持 `start_date` / `end_date` / `factors` / `forward_periods` / `universe`；取消检查置于**因子循环**头部（service-layer spec 点名的检查点位置），进度按因子数上报，每完成一个因子即 `ctx.log` 一条。

分层与相关性不落库：两者的输入只有 `factor_values` 与 `daily_kline`，各是**一次**批量查询加内存 groupby（D3 的批量取数），现算成本与读库成本同阶。落库反而引入「参数组合爆炸」（因子 × 分组数 × 区间）的无效缓存。

### D5: 分层回测不走 A 股回测引擎

`factors/quantile.py` 内部实现截面分组：

1. 交易日切片取因子值，`qcut` 分 N 组（默认 5，支持 10）
2. 组内对下期收益等权平均，得每组每期收益
3. 累乘得净值曲线；多空 = 顶组净值 − 底组净值（收益相减后累乘）

**替代方案（未采纳）**：复用 `backtest/engine.py`。否决原因——引擎是周频循环 + T+1 + 涨跌停 + 费用 + 停牌的**可交易组合**模拟，回答「这么交易能赚多少」；分层回测回答「这个因子的截面区分度有多强」，需要任意调仓频率、满仓、无摩擦、无约束。把研究的统计视图塞进撮合引擎，两者都会被扭曲：加费用则 IC 与分组的理论收益被无关地打折，去费用则引擎的规则开关形同虚设。

代价：分层净值的数字**不可当作策略收益**。页面必须显式标注这一点，避免误读。

### D6: 相关性 = 逐日期截面 Pearson，再对日期取均值

对区间内每个交易日，取两个因子的值做截面 Pearson 相关，得一个日期序列，再对日期取均值作为矩阵元素。支持单日期（区间退化为一天）。

**替代方案（未采纳）**：把所有 `(股票, 日期)` 观测汇成一条序列直接相关。否决原因——混入了时序维度：两个都在时间上缓慢漂移的因子会因共同趋势而显示出虚假高相关。

样本数少于阈值（有效截面对数 < 10，与 `compute_ic` 的门槛一致）的日期跳过；有效日期占比随矩阵返回，页面据此提示可信度。

### D7: 相关矩阵要素上限走 pydantic 校验

因子达 158 个，全量 158×158 热力图不可读，且计算是 O(F²·D)。参数模型对 `factors` 施加 `max_length=50`，超限在**参数校验阶段**返回 422（`ServiceParams` 的 `extra="forbid"` 同一套机制，`api/runs.py:66-71` 已把 `ValidationError` 转 422）。

**理由**：上限是接口契约而非前端约束——前端限制可被绕过，且服务层的既有约定是「非法参数在领域计算开始前拦截」（service-layer spec）。

### D8: 因子库页的「启用」是页面级选择，不写回配置

因子库页展示 158 + 8 个因子，带分类与覆盖度，每行一个勾选。勾选状态存浏览器 `localStorage`，作为 IC / 分层 / 相关性三页因子选择器的默认值。

**替代方案（未采纳）**：新增 `factor_enabled` 表或写回 `config.yaml`。否决原因——`AppConfig` 是仓库内受版本控制的 YAML，服务端改写它会让「配置」变成运行时状态；而新增表在本期没有消费者（策略引擎的 `factor.enabled` 与 Alpha158 因子集是两回事，前者是手工因子白名单）。等真有「按数据库配置驱动策略」的需求时再加表，届时同步迁移。

### D9: 两个任务类型注册，不新增运行 API 路由

`jobs/registry.py` 的 `JOBS` 增加两项：

| kind | params_model | service_fn | artifacts |
|---|---|---|---|
| `factor_compute` | `Alpha158Params` | `compute_alpha158` | `factor_values` 表（行数） |
| `factor_ic` | `FactorICParams` | `compute_factor_ic` | `ic_series` 表（行数） |

`factor_compute` 指向 **Alpha158** 而非手工因子的 `compute_factors`：后者不落库（`services/factors.py:115` docstring 明说 persists nothing），跑完没有产物、页面无从消费，注册成任务只是给一个空转的按钮。手工因子的计算入口保留在服务层，不进运行 API。

**理由**：这是 C2 决策 5 承诺的收益——加任务类型 = 注册表加一行，`POST /api/runs` 的通路、参数校验、SSE 日志、取消、产物登记全部复用。前端只需 `runsApi.submit('factor_compute', {...})` 后跳任务中心（与 `SyncForm.tsx:27-33` 同款）。

### D10: 因子域路由独立成 `api/factors.py`，只读

`create_factors_router(config) -> APIRouter`，前缀 `/api/factors`：

```
GET /api/factors                 因子列表 + 分类 + 覆盖度（分页）
GET /api/factors/ic              读 ic_series（因子 / 日期范围 / forward_period）
GET /api/factors/quantile        现算分层回测
GET /api/factors/correlation     现算相关矩阵
```

写路径（计算任务）一律走 `POST /api/runs`，本路由不含 POST。请求上下文用 `api/data.py:104-117` 的 `_context` 同款写法（每请求自开 `DataStore`，**默认配置而非只读**——DuckDB 同进程内只读与读写混用会抛 `ConnectionException`，这是 C2 实测结论）。路由在 `runtime/app.py:83-86` 处注册，位于 SPA 回退之前。

分页沿用 data-console spec 的约束：因子列表超过一页走服务端分页，前端 `Table` 在超过 100 行时开虚拟滚动。

### D11: ECharts 按需注册，包一层 `web/src/charts/`

新增 `web/src/charts/echarts.ts`（`echarts/core` + 只注册 Line / Bar / Heatmap + Grid / Tooltip / Legend / MarkLine / VisualMap / DataZoom / CanvasRenderer）与 `web/src/charts/EChart.tsx`（薄封装 `echarts-for-react`，统一高度与 `notMerge`）。

**理由**：C2 决策 8 要求按需引入而非全量包；`vite.config.ts` 无 `manualChunks`，全量引入会把整个 ECharts 打进主 chunk。全站图表收敛到一个封装，也保证四个页面的坐标轴/提示框样式一致。

**实现期补充（务必照做）**：封装必须从 **`echarts-for-react/esm/core`** 引入，不能用 `echarts-for-react/lib/core`。`lib/` 是 CJS 且导出挂在 `exports.default` 上，深层路径导入会跳过打包器的 interop，React 收到的是模块命名空间对象而不是组件——页面直接抛 React error #130，图表全白，而 TypeScript 与构建都不会报错（实测踩到，只有浏览器能发现）。

### D12: 四页结构沿用分区内 `Tabs`，翻 `implemented` 开关

`web/src/pages/factors/` 下四页 + `FactorNav.tsx`（照 `pages/data/DataNav.tsx` 的 `Tabs` + `activeKey={location.pathname}`），`navigation.tsx` 把 `/factors` 的 `implemented` 置 `true`，`routes.tsx` 补四条显式路由。

路由：

| 路径 | 页面 |
|---|---|
| `/factors` | 因子库（列表 / 分类 / 覆盖度 / 勾选） |
| `/factors/ic` | IC 分析（RankIC 序列、IC_IR、胜率、衰减） |
| `/factors/quantile` | 分层回测（分组净值、多空净值） |
| `/factors/correlation` | 相关性（热力图） |

数据获取沿用既有手写模式（`useState` + `useEffect` + `inFlight` ref），不引入数据请求库——`web/package.json` 当前无此类依赖，为一个分区引入它与全站风格不一致。客户端封装新增 `web/src/api/factors.ts`，走 `http.ts` 的 `request<T>` / `apiUrl`。

### D13: Alpha158 计算按 90 天分块，使 `factor_compute` 真的可取消

`services/factors.py` 的 `compute_alpha158` 从「一次向量化调用」改为按 90 个日历日分块循环，每块开头检查 `ctx.cancelled()`，逐块落库并上报进度；`Alpha158Result` 增加 `cancelled` 字段。

**为什么在本变更内做**：D9 把 Alpha158 计算注册成了任务类型，而 job-runner spec 明写「服务函数 SHALL 在约定的循环边界检查取消」。注册之前它只是个脚本函数，不可取消无所谓；注册之后界面上的取消按钮点了没反应就是骗人。验证阶段实测确认了这一点：提交后取消返回 202，任务继续跑完。

**为什么分块不改数值**：`compute_alpha158`（`factors/alpha158/compute.py`）每次调用自行向前多取 `MAX_WINDOW * 3`（180 日历日 ≈ 120 交易日）作为 warmup，而最长滚动窗口是 60 交易日——每块的 warmup 都足以覆盖其内部最长窗口，因此分块结果与单次整体计算逐值相等。这是可证伪的，已在 `tests/test_services_alpha158_chunking.py` 中按值断言。

**代价**：每块重复取一次 warmup 数据，总行数略增；块边界的 warmup 重叠会让「已保存行数」略大于理论值（`INSERT OR REPLACE` 保证幂等，无重复行）。取 90 天是在「取消响应速度」与「重复读取量」之间的折中。

**替代方案（未采纳）**：在向量化管线内部插检查点。polars 一次 `group_by` + `rolling_*` 是单个不可中断步骤，没有可插入的循环边界；要做出粒度更细的取消只能把计算拆到单只股票，代价是把向量化优势换掉。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| 分层净值可能被误读为策略收益（无费用、无约束、满仓） | D5：页面标题与说明文案明确标注「统计视图，非可交易组合收益」；spec 中作为 scenario 断言 |
| `factor_coverage` 需对 `factor_values` 全表按 `factor_name` 聚合，千万行级 | DuckDB 列存聚合单次扫描；每次请求算一次不缓存。若实测过慢，退路是在页面上按时效分层（只查最近 N 天），不在本期 |
| 相关矩阵 O(F²·D)，50 因子上限 × 长区间仍可能秒级到分钟级 | D7 上限 50；相关页面默认区间收窄（近 1 年）并要求先选因子再点「计算」，不做自动计算 |
| IC 落库与手工重算产生双份真相 | `ic_series` 是唯一查询来源，页面不提供「现算 IC」入口；重算 = 重跑 `factor_ic` 任务（PK 幂等，`INSERT OR REPLACE`） |
| 批量 IC 与逐日 IC 语义漂移 | D3 的验证锚点：同 fixture 下两条路径逐日结果必须一致，作为测试固化 |
| 158 因子的因子库页返回全量行 | D10 服务端分页 + 前端虚拟滚动，沿用 data-console spec 的既有约束 |
| 新增 ECharts 图表代码进入主 bundle | D11 按需注册；四个页面共用同一封装 |
| `ic_series` 成为 C6 周报 IC 面板的数据源，若 schema 事后变更会波及 C6 | D2 的 `forward_period` 与 `sample_size` 一期即落库，避免 C6 需要加列 |

## Migration Plan

全量 additive，无破坏性变更：

1. `ic_series` 表由 `data/schema.py` 的 `SCHEMA_SQL` 以 `CREATE TABLE IF NOT EXISTS` 加入（仓库无 schema 版本机制，也无 `ALTER TABLE` 用法；新表只能追加，既有表不能原地改）。同时把 `ic_series` 加进 `data/store.py` 的 `TABLE_NAMES` 与 `TABLE_DATE_COLUMNS`，使 `/api/data/status` 与数据总览页自动纳入该表。
2. 新模块（`factors/quantile.py`、`factors/correlation.py`、`services/factor_analysis.py`、`api/factors.py`）全为新增，不改既有函数签名；`factors/analysis.py` 只**新增** `compute_ic_frame`。
3. `JOBS` 新增两项，`data_sync` 不动；`POST /api/runs` 路由零改动。
4. 前端新增 `pages/factors/` 与 `api/factors.ts`，`navigation.tsx` / `routes.tsx` 各改一处。
5. 回滚：删除新模块与 `JOBS` 两行即回到当前状态；`ic_series` 表留在库中不影响其他表（可手工 `DROP`）。

## Open Questions

- `compute_factor_ic` 的默认区间取多长？倾向「最近 1 年」，与相关页面默认一致，待 158 因子全量落库后按实测耗时调整。
- 分层回测的调仓频率默认值？倾向周频（与策略链路一致），但接口保留日频参数，待与 IC 的 forward_period 对齐后再定默认。
- 因子库页是否要展示 Alpha158 之外的 8 个手工因子？倾向展示但标注「未落库、无覆盖度」——它们确实存在且可被策略引用，隐藏会造成「因子集只有 158 个」的误解。
- **本变更范围外但已发现**：`services/factors.py:136` 与 `:198` 调用 `factor_registry.get(name)` 时未传 `ctx.db`，导致每个因子自开一个 `DataStore` 连接（`factor-store-injection` spec 约定应注入）。该缺陷不影响本变更的任何路径（四页均读 `factor_values`，不走注册表），留待独立修复。
