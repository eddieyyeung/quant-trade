# Design: 报告域收口与仿真前端重建（C6）

## Context

C1 抽出服务层（`src/quant_trade/cli.py` 已删除），C2 建起运行基础设施与 antd 外壳（`run` / `run_log` / `artifact` 三表、单 worker 串行队列、统一 `POST /api/runs`、SSE 日志流），C3–C5 补齐因子、回测、模型三个域的服务与页面。外壳声明了八个分区，其中 `策略` / `仿真` / `报告` 仍是 `implemented: false`，路由落在 `Placeholder`。

本变更收口其中两个分区，并把报告域链路上积压的四个缺口一并补上。

**报告域现状**——链路齐备但通路只开一半：

| 能力 | 现状 |
|---|---|
| IC 面板 | 模板 `weekly_report.html.j2:146-166` 有完整渲染块，`{% if factor_ic %}` 门控。但 `WeeklyReportParams.factor_ic_data` 默认 `None`，全仓库无生产者（其 docstring 明写「The rendering path for these is currently dead」），`"factor_ic": factor_ic_data or []` 永远渲染不出东西 |
| 交易明细 | `as_reporter_input()`（`services/backtest.py:135-144`）**已经**把 `trade_log` 放进 result 字典，`generate_weekly` 也把它算成 `trade_count`（`services/report.py:129`）。但 `generate_weekly_report` 从不读 `result["trade_log"]`，上下文里没有这个键，模板里也没有任何交易区块 |
| 非周五判据 | `reporter.py:134` 只判断 `date.today().weekday() == 4`。spec 要求的「或最近交易日非周五」未实现：周五休市时 `is_friday` 为真，提示被吞掉 |
| 文案语义 | 模板 `:50` 写「信号基于最近周五 {{ signal_date }}」，填入的是 `result["signal_date"]`，即**实际信号日**。`generate_weekly` 显式传 `as_of=latest`（`services/report.py:95-98`），绕过了 `generate_strategy_signals` 里的 `most_recent_friday` 回退（`services/strategies.py:101`），所以周三运行时它真的是周三，文案是假的 |
| 可测性 | `reporter.py:131` 与 `save_report:185` 各调一次 `date.today()`，无注入点。`tests/` 中 `is_friday` 零命中，四个缺口全部无覆盖 |
| 任务类型 | `JOBS`（`jobs/registry.py`）五项：`data_sync` / `factor_compute` / `factor_ic` / `backtest` / `model_train`，无周报 |
| 读取端口 | `src/quant_trade/api/` 无 `reports.py`；`ArtifactStorage.HTML`（`runs/models.py:61-62`，「A rendered report; `ref` is its path」）已存在但无人使用 |

**仿真域现状**——后端完整，前端为零。`src/quant_trade/simulator/`（`api.py` / `engine.py` / `session.py` / `snapshot.py` / `comparison.py` / `types.py`）在 `runtime/app.py:83` 以 `create_simulator_app(config_path).router` 挂载，路由与字段齐备。原前端（8 个组件 + `index.css` + `types/index.ts` + `api/client.ts`）在 C2 随外壳接管入口被删除——**删除是已 staged 未提交的**，11 个文件全部完整存在于 `HEAD`（`f505630`），`git show HEAD:web/src/components/<file>` 可取回。全仓 `web/` 里只剩 `navigation.tsx:37` 一处 `simulator` 字样。

三个约束先摆明：

1. **仿真后端不改动。** `simulator/api.py` 的会话读写逻辑、路由与字段一律不动，本变更是纯前端重建。
2. **仿真域没有服务层。** `grep -rn "simulator" src/quant_trade/services/` 零命中——它是唯一直接由路由调引擎类的域。本变不改变这一点（改它要重排 `Simulator` / `SessionStore` / `ComparisonEngine` 的职责，是独立变更）。
3. **样式一律走 antd 与 `shell/theme.ts`。** 全仓 `web/src` 无任何 `.css` / `.less` / `.scss`，唯一样式表引入是 `main.tsx:7` 的 `antd/dist/reset.css`。

## Goals / Non-Goals

**Goals**

- 修复报告域四个缺口：IC 面板接入已落库的 `ic_series`、交易明细进入模板、非周五判据改为「最近交易日是否为周五」、提示文案与 `signal_date` 语义对齐
- 让这四个缺口可测：日期可注入，一次运行能复现 spec 举的周三场景
- 周报生成接入统一运行 API（`kind: weekly`），复用进度、实时日志、取消与产物登记；产物以 `ArtifactStorage.HTML` 登记
- 报告分区三页：列表（含数据时效）、内嵌预览、下载
- 以 antd 重建仿真分区：会话列表与创建、逐周决策台（持仓 / 因子排名 / 策略信号 / 决策表单）、对比视图
- 补上 `SnapshotBuilder._build_market_overview()` 经真实指数同步路径的测试覆盖

**Non-Goals**

- 不修 `metrics["turnover"]`。引擎在 `backtest/engine.py:339` 把它硬编码为 `0.0` 且从不回填，本变更**不渲染**这个字段（渲染一个恒为 0 的换手率就是造假），修它是回测引擎的事
- 不补周报底部缺失的「信号生成时间」。`weekly-reporting` 的 `周报底部元信息` 要求四项、模板只有三项（`数据更新` / `下次调仓` / `初始资金`），这是第五个缺口，不在 proposal 列举的四个之内，留给独立变更
- 不改 `generate_strategy_signals` 的 `most_recent_friday` 回退，不改 `generate_weekly` 的信号锚点（见 D3）
- 不做报告列表的历史文件回溯（见 D5）
- 不做仿真的多会话对比、会话导出、会话重命名
- 不给仿真域补服务层（见约束 2）
- 不引入前端组件测试框架——仓库没有，加它是独立变更；沿用现有的源码契约测试 + 浏览器人工核对

## Decisions

### D1: IC 面板一次多因子读 + 服务层聚合，新增 `factor_ic_overview`

模板要的是**每因子一行**的 `{name, ic_weekly, ic_mean, ic_ir}`，而现有读取端口 `factor_ic_series`（`services/factor_analysis.py:385`）**一次只服务一个因子、一个 `forward_period`**，且返回结构里没有 `ic_weekly`。用它拼面板意味着 N 次调用加一次 reshape。

因此新增一个聚合读服务：

```python
class FactorICOverviewParams(ServiceParams):
    as_of: date | None = None          # None 表示最近交易日
    lookback_days: int = Field(default=365, ge=1)
    forward_period: int = Field(default=5, ge=1)

@dataclass
class FactorICOverviewRow:
    name: str
    ic_weekly: float | None            # 最近一个有 IC 的交易日
    ic_mean: float | None              # 窗口内 ic 均值
    ic_ir: float | None                # ic_mean / ic_std(ddof=0)
    sample_days: int

def factor_ic_overview(params, ctx) -> list[FactorICOverviewRow]
```

实现走 `get_ic_series(store, factors, start, end, forward_period)`——注意这个函数本来就接受**因子列表**（`factors/ic_store.py:45`），一次调用即可取回全部因子的长表，再按 `factor_name` 分组聚合。因子名来自 `SELECT DISTINCT factor_name FROM ic_series`，**不是** `list_factor_names`（那个读 `factor_values`，会带出没有 IC 的因子）。

汇总口径复用 `services/factor_analysis.py:371` 的 `_ic_summary`，使 IC 面板与因子分区 IC 分析页对同一份数据给出同一组数字。

**`ic_weekly` 的口径**：最近一个有 IC 的交易日那一行的 `ic`，不是自然周窗口。理由：spec 的 `因子 IC 跟踪表` 举例「momentum_20d 本周 IC = -0.015」，而 IC 按交易日逐日算，`forward_period` 是持有期不是日历周；取最近一日与「本周」的意图一致，且不引入一个「周」的边界定义（A 股周内还有假期）。**代价**：周三跑周报时「本周 IC」实际上是周三或周二的值。这一点在页面上靠 `sample_days` 与报告日期体现，不额外造一个字段。

### D2: 交易明细直取 `result["trade_log"]`，只渲染最近 N 笔，不渲染换手率

`generate_weekly_report` **不新增 `trade_log` 参数**。`as_reporter_input()` 已经把 `trade_log` 放进传进来的 `result` 字典（`services/backtest.py:140`），reporter 读 `result.get("trade_log", [])` 即可。加一个同名参数会造出两条真相——dict 里一份、参数一份。

**截断**：`run_backtest_raw(BacktestParams(start=config.backtest.start_date, end=latest))` 跑的是**整个回测区间**，`trade_log` 是自 `config.backtest.start_date` 起的累计成交，不是本周成交。一份 8 年区间的回测会有数千行。模板渲染最近 `TRADE_ROWS_SHOWN = 50` 笔（按日期倒序），并在区块标题里标注「共 N 笔，展示最近 50 笔」。

**不渲染 `turnover`**：`compute_metrics` 把它设为 `0.0`（`engine.py:352`）且没有任何回填路径。渲染它就是让报表声称「本次换手率为 0」——一个看起来正常、实则恒假的数字。区块只展示 `metrics.total_trades`。

**费用列**：`portfolio.trade_log` 的 BUY 行有 `commission` / `transfer_fee`，SELL 行才有 `stamp_duty`（`backtest/portfolio.py:121-131, 171-182`）。模板用 `f.get('stamp_duty', 0.0)` 取，缺失即 0。

**`data/` 目录下的 `reports/` 不受影响**：报告写入路径与命名（`weekly_YYYY_MM_DD.html`）不变。

### D3: 非周五判据改为「最近交易日是否为周五」，文案去掉「最近周五」（已确认）

判据：

```python
is_rebalance_day = signal_date.weekday() == FRIDAY   # FRIDAY = 4
```

`signal_date` 取 `result["signal_date"]`（`as_reporter_input` 写入的 `str(date)`）解析成 `date`。这同时覆盖了提案里的两个子问题：周五休市时最近交易日是周四，判据为假，提示正常出现。

文案改为：

```
⚠️ 非调仓日运行 — 信号基于 {{ signal_date }} 数据，仅供参考
```

**为什么只改文案而不把管线锚到 `most_recent_friday`**（已与用户确认）：`generate_weekly` 显式传 `as_of=latest`，信号确实按最近交易日计算。把管线改成锚定最近周五会让文案成立，但会改变非周五运行时**报告的全部内容**（信号、持仓、回测区间），这是 proposal 四个缺口之外的行为变更。只改文案则报告内容零变化，缺陷（文案声称的日期与实际不符）被消除。**代价**：周报不再暗示「信号来自周度锚点」；需要这一点的读者靠底部的「下次调仓」判断自己在周内的位置。

**替代方案（未采纳）**：保留文案、把 `most_recent_friday(latest)` 传给模板。否决原因：那会让报表显示一个**没有参与本次计算**的日期——用一个假日期替换另一个假日期。

### D4: 日期可注入

`generate_weekly_report(..., today: date | None = None)` 与 `save_report(html, config, today: date | None = None)`，默认 `date.today()`。

`today` 决定 `report_date` / `updated_at` / 下次调仓日 / **输出文件名**。不可注入时，spec 的「周三跑周报预览」场景无法复现，测试只能断言「提示条存在」而不能断言其日期正确。注入后两者都可断言。

`signal_date` **不来自** `today`——它是 `result["signal_date"]`，来自服务实际使用的 `latest`。两个日期不同正是这个提示存在的原因。

### D5: 报告列表读 run + artifact，不扫描目录（已确认）

`GET /api/reports` 的数据源是 `run`（`kind = 'weekly'`）与 `artifact`（`storage = 'html'`）的连接结果，而不是 `config.report.output_dir` 的目录扫描。

**连接是左外连接，运行记录是主表**（实现期补定）：刚入队或正在运行的周报还没有产物，内连接会把它在跑完之前整个藏起来——而那正是读者想看着进度条的窗口，也会让列表里的进度分支变成死代码。产物只负责提供数据时效与两个计数；没有产物时这三列为空，不是 0（0 会被读成「这份报告零成交」）。详情与预览仍按内连接语义处理：没有报告可看就是未找到。

| 展示项 | 来源 |
|---|---|
| 报告标识 | `run.run_id` |
| 生成时间 | `run.finished_at` |
| 运行状态 | `run.status` |
| 数据时效 | `artifact.meta["signal_date"]`（D6 写入） |
| 调仓信号数 / 成交笔数 | `artifact.meta["order_count"]` / `["trade_count"]` |
| 请求参数 | `run.params_json`（`as_of` / `output_dir`） |

**代价（明写在页面上）**：仓库里已有的 `reports/weekly_2026_08_05.html` 这类文件没有对应的 run 记录，**不会**出现在列表中。这与 C5 决策 D3「宁可页面说清它给的是哪一份，也不要让选择器冒充」同构：列表列的是**被登记过的运行**，不是磁盘上的文件。页面空态因此说明「周报由任务中心生成，历史手工生成的文件不在列表中」，而不是让用户以为报告丢了。

**替代方案（未采纳）**：扫描 `report.output_dir`。可以让全部历史文件可见，但元数据只能从文件名（`weekly_YYYY_MM_DD.html`）解析，得不到调仓数与成交笔数，且与运行基础设施无关联——「生成时间」会退化成文件 mtime，那是拷贝时间不是生成时间。

### D6: 周报作为 `kind: weekly` 登记 HTML 产物

```python
JOBS["weekly"] = JobSpec(
    kind="weekly",
    params_model=WeeklyReportParams,
    service_fn=generate_weekly,
    artifacts=_weekly_artifacts,
)
```

产物映射：

```python
def _weekly_artifacts(result: WeeklyReportResult) -> list[ArtifactDraft]:
    if not result.report_path:
        return []                      # 取消的运行没有报告
    return [ArtifactDraft(
        kind="report",
        storage=ArtifactStorage.HTML,
        ref=result.report_path,
        row_count=None,                # 报告不是行集，行数为 None 而非 0
        meta={
            "signal_date": result.signal_date.isoformat(),
            "order_count": result.order_count,
            "trade_count": result.trade_count,
        },
    )]
```

`row_count=None` 而非 `0`：`RunDetail` 的产物表已有 `formatCount(null)` 渲染成 `—` 的路径（`utils/format.ts`），而 `0` 会读成「这份报告是空的」。

`meta` 里的 `next_rebalance` **不放**：它由 reporter 内部按 `today` 算出，服务层不知道它；为了一个展示字段把计算拆成两处会造出漂移的可能。它在报告 HTML 里，需要就打开看。

`WeeklyReportParams.factor_ic_data` 保留但降级为**覆盖项**：默认由管线自动装配（D1），显式给出时按原样透传。既有的 `test_factor_ic_channel_is_forwarded_verbatim` 与 `test_default_factor_ic_is_none` 因此需要相应调整——后者断言 `None` 会被转发，而新行为是 `None` 触发装配。

### D7: 预览走 `GET /api/reports/{run_id}/html`，下载加 `?download=1`

新路由（挂在平台 app 上，`runtime/app.py` 的 SPA 回退之前）：

- `GET /api/reports` → 列表（服务端分页）
- `GET /api/reports/{run_id}` → 报告元信息，未登记返回 404
- `GET /api/reports/{run_id}/html` → `HTMLResponse`，内嵌预览用 `iframe src`
- `GET /api/reports/{run_id}/html?download=1` → 同内容 + `Content-Disposition: attachment; filename="weekly_YYYY_MM_DD.html"`

**内嵌预览不用 `srcDoc`**：那份 HTML 是内联 base64 图表的自包含文档，几百 KB 起步，塞进 JSON 字符串再交给 `srcDoc` 等于把它读进 JS 内存两遍。`iframe src` 让浏览器直接流式渲染，且 `download` 属性与 `Content-Disposition` 都能作用在同一个 URL 上。

**路径限定**：`artifact.ref` 来自数据库，理论上可被写成任意路径。读取前 SHALL 用 `Path(ref).resolve()` 与 `Path(config.report.output_dir).resolve()` 比对，不满足 `is_relative_to` 一律 404。这与 `runtime/app.py` 的 SPA 回退里已有的 `candidate.is_relative_to(dist_dir.resolve())` 是同一道防线。

服务层落在 `services/report_query.py`（`ReportListParams` / `report_list` / `ReportDetailParams` / `report_detail` / `report_html`），与 `model_query.py` / `backtest_query.py` 同构——读取端口一律在服务层，路由不写 SQL。

### D8: 仿真前端不做分区 Tabs

回测、模型分区各有多个**分区级**页面，因此各有 `XxxNav`。仿真只有一个分区级页面（`/simulator` 同时承载创建表单与会话列表，与旧实现一致），详情页由列表行进入。`BacktestNav` 的注释已经写明这类详情页「deliberately not a tab」——它的 `activeKey` 匹配不到任何 tab，标签栏会空白。

因此 `web/src/pages/simulator/` 不建 `SimulatorNav`，`routes.tsx` 只加两条显式路由：

```tsx
<Route path="/simulator" element={<SessionList />} />
<Route path="/simulator/:sessionId" element={<SessionDetail />} />
```

### D9: 决策表单保留 `CODE:PCT` DSL 与「先买后卖」

取回的 `parseOrders`（`HEAD:web/src/components/DecisionForm.tsx`）是整份旧代码里最值得逐字保留的逻辑：

- 买入：逗号分隔的 `代码:百分比`，按 `,` 再按 `:` 切分，数字 **除以 100** 变成 `target_pct` 小数，`direction: 'BUY'`
- 卖出：逗号分隔的裸代码列表，展开为 `{ts_code, target_pct: 0, direction: 'SELL'}`
- 买入**先于**卖出输出（顺序影响引擎的现金判断）

重建时改为解析后再构造，但语义三条不变。原实现在 `Form.Item` 层面无法表达「静默丢弃格式错误的项」，本变更**保留静默丢弃**并把格式提示放在 `placeholder` 上——把校验移进 `Form.Item` 规则会改变「部分合法输入能提交」这一既有行为，超出重建范围。

空提交（无订单无备注）保留 `Modal.confirm` 二次确认。

**成交回执用 `notification` 而非 `message`**：原实现是 `alert` 拼多行文本（`成交 N 笔` + 每笔一行 + 警告行），`message` 是单行单例，会截断成一条并互相覆盖。

### D10: 对比视图用页内切换，不做独立路由

`SessionDetail` 内用 `Segmented`（`决策` / `对比`）切换，与旧实现的 `view: 'detail' | 'compare'` 状态一一对应。

**理由**：旧实现在切换时不重新拉取会话详情，只挂载 `ComparisonView`。给对比开一条 `/simulator/:sessionId/compare` 路由会让每次切换都重挂 `SessionDetail`、重新请求 `GET /api/sessions/{id}`——一个纯粹的视图切换变成一次网络往返。对比结果本身由 `ComparisonView` 自己的 effect 缓存。

**代价**：对比视图没有可分享的 URL。本次重建以「零回归」为准，不引入新能力。

### D11: 仿真图表走 `EChart` 封装，且不 rebase

`ComparisonView` 原用 recharts（已随 `package.json` 移除）。重建改用 `web/src/charts/EChart.tsx`，复用已注册的 `LineChart` / `GridComponent` / `TooltipComponent` / `LegendComponent` / `DataZoomComponent`——**不需要新增 `echarts.use` 条目**。

旧实现把三条 `nav_*` 序列按 `trade_date` 合并成「一个日期一行、列名是中文标签」的数组（`手动` / `策略` / `基准`），图表库无关，直接搬运。

**不 rebase**：回测详情页需要 `rebase()` 是因为策略净值是账户绝对市值而基准是归一化指数，两者单位不同（design D12 of C4）。仿真的 `nav_manual` / `nav_strategy` / `nav_benchmark` 由 `ComparisonEngine` 全部归一化到 1.0（`simulator/comparison.py` 的 `_mark_nav` / `_build_manual_nav`），本来就同轴可比。照搬回测的 `rebase` 会把已经归一化的序列再除一次。

## Risks / Trade-offs

- **[交易明细长度]** 8 年区间的回测有数千笔成交，全量渲染会让报告体积和打开时间失控。→ 截断到最近 50 笔并在标题标注总数；报告仍是自包含单文件。
- **[`ic_weekly` 的名义与实体]** 叫「本周 IC」但取的是最近有 IC 的交易日。→ 在 D1 写明；页面上靠 `sample_days` 与报告日期让读者自行判断，不新增字段。
- **[历史周报不可见]** D5 的列表只覆盖本次变更后生成的周报。→ 空态文案明说原因，不让用户以为文件丢了。
- **[路径穿越]** `artifact.ref` 是数据库里的字符串，被写入恶意值即可读取任意文件。→ D7 的 `is_relative_to` 校验，并有对应 scenario 与测试。
- **[`factor_ic_data` 语义变更]** 该字段从「唯一入口」变成「覆盖项」，两份既有测试断言的是旧语义。→ 在 tasks 里显式列出这两条测试的调整，不静默改断言。
- **[仿真前端零组件测试]** 仓库无前端测试框架，重建的 8 个组件只能靠源码契约断言 + 浏览器逐页核对。→ 沿用 C4/C5 的做法：`tests/test_ui_shell_contract.py` 断言状态与约定存在，渲染本身在实现期用无头浏览器核对，并在 tasks 里列为验收项。
- **[`ComparisonView` 的 metrics 键]** 旧实现按 `data.metrics` 的键直接取中文标签，未知键回落为原键名。后端返回的是 `manual` / `strategy` / `benchmark`。→ 保留回落分支，不假设键集合。

## Migration Plan

1. 报告域后端（IC 装配、交易明细、判据与文案、日期注入、`kind: weekly`、读取服务与路由）先行，`uv run pytest` 全绿后可独立验证：跑一次 `kind: weekly` 任务，在任务详情看到一条 `storage=html` 的产物。
2. 报告分区前端随后，依赖步骤 1 的四个接口。
3. 仿真前端与后端无关，可并行；重建以旧文件逐项对照为准。
4. `kline-universe-fallback` 的测试补强独立，不阻塞其他步骤。

**回滚**：全部改动是新增文件 + 三个既有文件的局部修改（`signals/reporter.py`、`services/report.py`、`jobs/registry.py`）加两个前端注册点（`navigation.tsx` 的 `implemented`、`routes.tsx` 的路由）。把 `navigation.tsx` 的 `/reports` 与 `/simulator` 改回 `implemented: false`、从 `routes.tsx` 移除对应路由，两个分区即回到占位页；后端新增的服务与路由是纯增量，不挂载即不生效。

## Open Questions

- 周报底部缺的「信号生成时间」（`weekly-reporting` 的 `周报底部元信息` 要求四项、模板只有三项）不在本变更范围。补它需要先定「信号生成时间」与「数据最后更新时间」的区别——目前模板的 `updated_at` 是报告生成时刻，两个语义在实现里是同一个值。留给独立变更。
- `metrics["turnover"]` 恒为 `0.0` 是引擎的既有缺陷。本变更选择不渲染它；是否回填、按什么口径（单边/双边、按成交额/按市值）需要回测域单独决策。
