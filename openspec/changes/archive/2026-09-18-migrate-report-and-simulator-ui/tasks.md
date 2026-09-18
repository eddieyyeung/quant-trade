## 1. 因子 IC 面板的数据源

- [x] 1.1 `services/factor_analysis.py` 新增 `FactorICOverviewParams`（`as_of: date | None`、`lookback_days: int = 365`（`ge=1`）、`forward_period: int = 5`（`ge=1`））与 `FactorICOverviewRow`（`name` / `ic_weekly` / `ic_mean` / `ic_ir` / `sample_days`，后四项可空）
- [x] 1.2 实现 `factor_ic_overview(params, ctx) -> list[FactorICOverviewRow]`：因子名取 `SELECT DISTINCT factor_name FROM ic_series`（**不是** `list_factor_names`，那个读 `factor_values` 会带出没有 IC 的因子）；用**一次** `get_ic_series(store, factors, start, end, forward_period)` 取回长表，按 `factor_name` 分组；汇总复用同文件既有的 `_ic_summary`（`:371`），保证与因子分区 IC 分析页口径一致
- [x] 1.3 `ic_weekly` 取该因子窗口内**最后一行**的 `ic`，不引入日历周边界（design D1）；窗口内无行时四个统计量为 `None`、`sample_days` 为 0 并跳过该因子
- [x] 1.4 `services/report.py` 的 `generate_weekly` 在 `params.factor_ic_data` 为 `None` 时调用 `factor_ic_overview` 装配面板行（按 `FactorICOverviewRow` 字段映射为模板要的 `name` / `ic_weekly` / `ic_mean` / `ic_ir`），非 `None` 时按原样透传；装配失败不阻断报告生成，记 `ctx.log` 警告并传空列表
- [x] 1.5 更新 `WeeklyReportParams.factor_ic_data` 的 docstring：从「唯一入口」改写为「覆盖项，默认由管线装配」（当前 docstring 写着渲染路径是死的，与新行为矛盾）
- [x] 1.6 调整 `tests/test_services_report.py` 的两条既有断言到新语义：`test_factor_ic_channel_is_forwarded_verbatim` 保留并确认显式值仍原样透传；`test_default_factor_ic_is_none` 改为断言「未提供时 kwargs 中的 IC 数据来自装配而非 `None`」；`wired` fixture 需同时打桩 `factor_ic_overview`
- [x] 1.7 新建 `tests/test_services_factor_ic_overview.py`：多因子一次读取（断言只发一次 `get_ic_series`，可用计数桩）、`ic_weekly` 取最后一行、`ic_mean` / `ic_ir` 与 `_ic_summary` 复算一致、`ic_series` 为空时返回空列表且不抛异常、未知因子被跳过、参数 `model_dump_json` 往返
- [x] 1.8 在 `services/__init__.py` 的 `TYPE_CHECKING` 块、`__all__`、`_MODULE_BY_NAME` 三处登记 `FactorICOverviewParams` / `FactorICOverviewRow` / `factor_ic_overview`

## 2. 交易明细进入周报

- [x] 2.1 `signals/reporter.py` 的 `generate_weekly_report` 读 `result.get("trade_log", [])` 放进上下文键 `trades`（**不新增同名参数**——`as_reporter_input()` 已把 `trade_log` 放进传进来的 result 字典，加参数会造出两条真相，design D2）
- [x] 2.2 模块级常量 `TRADE_ROWS_SHOWN = 50`，上下文同时带出 `trade_total`（全部笔数）与 `trade_shown`（实际展示数）；展示按日期**倒序**取前 50 笔
- [x] 2.3 每行归一为模板可直接渲染的形状：`date` / `action` / `ts_code` / `shares` / `price` / `fee`（`commission + transfer_fee + stamp_duty`，用 `.get(..., 0.0)` 取值——BUY 行没有 `stamp_duty` 键，`backtest/portfolio.py:121-131`）
- [x] 2.4 `templates/weekly_report.html.j2` 新增「交易明细」区块（放在「当前持仓」之后、「因子表现跟踪」之前），表头为日期/方向/代码/股数/价格/费用；区块用 `{% if trades %}` 门控；标题标注「共 {{ trade_total }} 笔」并在超出时说明只展示最近 `{{ trade_shown }}` 笔
- [x] 2.5 模板**不渲染换手率**：引擎在 `backtest/engine.py:352` 把 `turnover` 恒置为 `0.0` 且无回填路径，渲染它等于给出一个恒假数字（design D2）。若需展示成交规模，只用 `metrics.total_trades`
- [x] 2.6 在 `tests/test_services_report.py` 补 `test_trade_log_reaches_the_reporter`：`_fake_backtest()` 的 `trade_log` 行进入 reporter 收到的 result 字典

> **实现期偏离**：截断断言（`trade_total` 全长、`trades` 长度 50、按日期倒序）落在 `tests/test_signals_reporter.py` 而非此处。该文件的 `wired` fixture 把 `generate_weekly_report` 整体打桩，只捕获 kwargs——截断发生在被桩掉的函数**内部**，在那里断言等于断言桩自己。截断是对真实模板渲染的断言，放到了渲染测试里。
- [x] 2.7 新建 `tests/test_signals_reporter.py`（当前无此文件）：`result` 无 `trade_log` 键时区块不渲染且不抛异常；BUY 行缺 `stamp_duty` 时费用列按 0 渲染；模板渲染出的 HTML 含交易明细表头与总笔数文案

## 3. 非周五判据与文案

- [x] 3.1 `signals/reporter.py` 的 `generate_weekly_report` 新增 `today: date | None = None`（默认 `date.today()`），替换 `:131` 的裸调用；`today` 决定 `report_date` / `updated_at` / 下次调仓日
- [x] 3.2 判据从 `today.weekday() == 4` 改为**信号日期**的星期：把 `result["signal_date"]` 解析成 `date`（缺失或不可解析时回落到 `today`），上下文键 `is_friday` 改名 `is_rebalance_day`，取值 `signal_date.weekday() == FRIDAY`；`FRIDAY = 4` 定义在 `signals/reporter.py` 本地

> **实现期偏离**：原计划从 `services/strategies.py` 导入 `FRIDAY`，实为本地定义。`date.weekday() == 4` 是 Python 的星期五，不是领域常量——「周五调仓」这条**策略**才定义在 `services/strategies.py`。渲染模块是 `signals/` 的叶子，从服务层取一个整数会把 `services → signals → services` 的依赖方向倒过来（运行时无环，但架构上反了）。本地定义附注了策略侧才是该约定的归属。
- [x] 3.3 `save_report` 新增 `today: date | None = None`，替换 `:185` 的裸调用，使输出文件名可复现（design D4）；`services/report.py` 的 `_save` 一并透传
- [x] 3.4 `templates/weekly_report.html.j2` 的提示文案（`:50`）改为 `⚠️ 非调仓日运行 — 信号基于 {{ signal_date }} 数据，仅供参考`，去掉不成立的「最近周五」；CSS 块 `:14-16` 的条件同步改为 `is_rebalance_day`
- [x] 3.5 确认**不改**信号锚点：`generate_weekly` 仍传 `as_of=latest`，非周五运行的信号、持仓与回测区间与改动前逐值相同（design D3；这是「只改文案」方案的核心约束，改错了就是行为变更）
- [x] 3.6 在 `tests/test_signals_reporter.py` 补：周三场景下提示条出现且文案含实际信号日期、且**不含**「最近周五」字样；最近交易日为周五时提示条不出现；周五休市（最近交易日为周四）时提示条出现；注入 `today` 与信号日期后结果与该机器当天真实日期无关；同参数在非周五与周五运行时信号相关渲染逐值相同
- [x] 3.7 在 `tests/test_signals_reporter.py` 补：`save_report` 注入 `today=2026-07-22` 时文件名恰为 `weekly_2026_07_22.html`

## 4. 周报接入统一运行接口

- [x] 4.1 `jobs/registry.py` 的 `JOBS` 注册 `weekly` → `WeeklyReportParams` / `generate_weekly`，位置在 `model_train` 之后
- [x] 4.2 新增产物映射 `_weekly_artifacts(result: WeeklyReportResult)`：`result.report_path` 为空（取消）时返回 `[]`；否则一条 `ArtifactDraft(kind="report", storage=ArtifactStorage.HTML, ref=result.report_path, row_count=None, meta={...})`，`meta` 含 `signal_date`（ISO 字符串）/ `order_count` / `trade_count`。`row_count` 用 `None` **不是** 0——报告不是行集，0 会被读成「这份报告是空的」（design D6）
- [x] 4.3 确认 `meta` **不放** `next_rebalance`：该值由 reporter 内部按 `today` 算出，服务层不知道它，为一个展示字段把计算拆两处会造出漂移（design D6）
- [x] 4.4 在 `tests/test_jobs_runner.py` 补：提交 `kind: weekly` 后运行状态流转到 `ok`、登记一条 `storage=html` 的产物且 `meta` 三键齐备、`row_count` 为 `None`；被取消的运行不登记产物；既有五个 kind 不受影响
- [x] 4.5 确认 `POST /api/runs` 对 `kind: weekly` 返回 202 与 `run_id`（路由无需改动，`api/runs.py` 的 docstring 本就写着 report 是既定类型）；在 `tests/test_api_runs.py` 补一条以 `kind: weekly` 提交的用例

## 5. 报告读取服务与 HTTP 路由

- [x] 5.1 新建 `src/quant_trade/services/report_query.py`：`ReportListParams`（`limit` / `offset`，带边界，照 `model_query.py` 的写法）与 `ReportDetailParams`（`run_id`）
- [x] 5.2 定义结果 dataclass：`ReportSummary`（`run_id` / `status` / `finished_at` / `signal_date` / `order_count` / `trade_count` / `progress`）、`ReportListResult`（含 `total`）、`ReportDetail`（在 summary 之上加 `created_at` / `error` / `file_name`）；`run_id` 不存在时以未找到标记表达（由 API 层转 404）
- [x] 5.3 实现 `report_list(params, ctx)`：`run`（`kind = 'weekly'`）JOIN `artifact`（`storage = 'html'`），按 `finished_at` 倒序、服务端分页并返回总数；`meta` 解析失败时该行三个展示字段留空而非抛异常
- [x] 5.4 实现 `report_detail(params, ctx)` 与 `report_html(params, ctx) -> str`；后者读取文件内容
- [x] 5.5 实现路径守卫 `_resolve_report_path(store, run_id, config) -> Path | None`：取 artifact 的 `ref`，`Path(ref).resolve()` 必须 `is_relative_to(Path(config.report.output_dir).resolve())`，否则返回 `None`（design D7，与 `runtime/app.py` SPA 回退里已有的 `is_relative_to` 是同一道防线）；文件不存在同样返回 `None`
- [x] 5.6 在 `services/__init__.py` 的三处登记新增公开名称
- [x] 5.7 新建 `src/quant_trade/api/reports.py`：`create_reports_router(config) -> APIRouter`，前缀 `/api/reports`，只读不含 POST；`GET /api/reports`（分页 + 总数）、`GET /api/reports/{run_id}`（未找到 404）、`GET /api/reports/{run_id}/html`（`HTMLResponse`；带 `download` 查询参数时加 `Content-Disposition: attachment; filename="<报告文件名>"`）
- [x] 5.8 请求上下文用与 `api/models.py` / `api/factors.py` 同款的 `_context`（每请求自开 `DataStore`）
- [x] 5.9 在 `runtime/app.py` 的 `include_router` 段挂载报告路由，位置在 SPA 回退之前
- [x] 5.10 新建 `tests/test_services_report_query.py`：列表分页与倒序、`meta` 缺键时字段留空、未登记的 `run_id` 返回未找到、`ref` 指向输出目录之外时返回未找到、`ref` 文件不存在时返回未找到、正常路径能读回 HTML、参数 `model_dump_json` 往返
- [x] 5.11 新建 `tests/test_api_reports.py`：列表分页、详情、404、`/html` 返回 `text/html`、带下载参数时含 `Content-Disposition` 且文件名为报告文件名、路径越界返回 404、`/api/reports/*` 不打到 SPA 回退

## 6. 前端接口封装

- [x] 6.1 新建 `web/src/api/reports.ts`：基于 `http.ts` 的 `request<T>`，导出 `reportsApi`（`list` / `detail` / `htmlUrl`），类型与后端返回字段逐一对齐（snake_case）；`htmlUrl` 用 `apiUrl()` 构造，供 `iframe src` 与下载链接共用
- [x] 6.2 新建 `web/src/api/simulator.ts`：导出 `simulatorApi`，覆盖 `listSessions` / `createSession`（**POST 用查询参数、无请求体**，与后端一致）/ `getSession` / `step` / `skip` / `compare` / `deleteSession`；类型定义在本文件内（仓库约定：类型随 `api` 模块存放）
- [x] 6.3 `createSession` 的参数对象在序列化时丢弃 `undefined` 与空字符串，使「留空即用后端默认」成立；`*_pct` 字段按小数比例传值，不在客户端乘除 100
- [x] 6.4 确认未新增任何 `.css` / `.less` / `.scss` 文件

## 7. 报告分区页面

- [x] 7.1 `web/src/shell/navigation.tsx` 把 `/reports` 的 `implemented` 置 `true`；`web/src/shell/routes.tsx` 在 SECTIONS 占位循环之前补 `/reports` 与 `/reports/:runId` 两条显式路由
- [x] 7.2 新建 `web/src/pages/reports/List.tsx`：antd `Table`，列为状态（`Tag` + `STATUS_LABELS`）、生成时间（`formatTime`）、数据时效（`signal_date`）、调仓信号数、成交笔数、进度（非终态行用 `Progress`，终态展示占位符）、操作（预览链接）；服务端分页；非终态行存在时按 `LIVE_POLL_MS` 轮询并用 `inFlight` ref 防重入（照 `pages/backtest/List.tsx` 的写法）
- [x] 7.3 `List.tsx` 的错误/空态按既有约定分三支且**失败分支在前**：`listFailure !== null` → `Alert type="error"` + 重试；`!loading && total === 0` → `Alert type="info"` 文案「尚无报告」并说明报告由周报任务生成、手工放入输出目录的历史文件不在列表中（design D5）；否则渲染表格
- [x] 7.4 未产出报告的行（`status` 非 `ok`）不提供可用的预览入口
- [x] 7.5 新建 `web/src/pages/reports/Preview.tsx`：`useParams` 取 `runId`，并行请求 `detail`；用 `<iframe src={reportsApi.htmlUrl(runId)}>` 内嵌预览——**不用 `srcDoc`**，报告内联 base64 图表、体积可观，塞进 JSON 再交给 `srcDoc` 等于读进 JS 内存两遍（design D7）
- [x] 7.6 `Preview.tsx` 头部标注运行标识、生成时间、数据时效与状态；取消的运行标注为取消而非完成；提供下载入口（`<a href={reportsApi.htmlUrl(runId, {download: true})}>`）
- [x] 7.7 `Preview.tsx` 覆盖未找到与加载失败两个分支，各带返回报告列表的入口；用 `useEffect(..., [runId])` 重置状态，避免 React Router 复用组件实例时上一个报告的内容留在新 URL 下（照 `pages/backtest/Detail.tsx:167-176` 的写法）
- [x] 7.8 确认预览页**不发起任何周报生成请求**（`runsApi` 不出现在该文件里，照 `models/Evaluate.tsx` 的既有断言）

## 8. 仿真重建：会话列表与创建

- [x] 8.1 `web/src/shell/navigation.tsx` 把 `/simulator` 的 `implemented` 置 `true`；`web/src/shell/routes.tsx` 在占位循环之前补 `/simulator` 与 `/simulator/:sessionId` 两条显式路由
- [x] 8.2 新建 `web/src/pages/simulator/CreateSession.tsx`：antd `Form` + `Input` / `DatePicker` / `InputNumber` / `Select`，字段为名称、起始日期、结束日期（可选）、初始资金、参考策略（「无」选项对应不发送该参数）；提交期间 `loading` 阻止重复提交；成功后 `navigate('/simulator/' + session_id)`；失败用 `message.error` 且表单保持可编辑
- [x] 8.3 新建 `web/src/pages/simulator/SessionList.tsx`：antd `Table`，列为标识（截断展示）、名称、状态（`Tag`，`active` / `paused` / `completed` 三色，**未知状态回落为明确值而非空白**）、当前日期、参考策略、操作（查看 / 删除）；空态给出「无会话」的 `Alert` 与创建入口，SHALL NOT 渲染空表格
- [x] 8.4 删除用 `Modal.confirm` 二次确认；取消不发请求；成功后就地移除该行；失败用 `message.error` 且**不移除该行**（把失败的删除当成成功是数据不一致）
- [x] 8.5 把 `CreateSession` 与 `SessionList` 同屏组合（与重建前一致）

> **实现期偏离**：没有新增 `index.tsx` 包装页——`SessionList.tsx` 自己就是那个分区页（创建表单 + 历史列表），路由直接指向它。多一层包装只为放一个 `<Flex>`，而文件名与重建前的组件名保持一一对应，正是 13.8 逐项核对时需要的对照关系。创建成功后跳转到该会话的决策台，因此列表无需在此刷新（返回时会重新挂载）。

## 9. 仿真重建：决策台

- [x] 9.1 新建 `web/src/pages/simulator/PortfolioTable.tsx`（持仓，`pnl_pct` 按正负着色、`weight_pct` 与 `pnl_pct` 乘 100 显示、现价缺失展示占位符而非 0、空仓给出说明）与 `FactorRanking.tsx`（排名 / 代码 / 综合得分保留两位小数、无数据给说明）
- [x] 9.2 新建 `web/src/pages/simulator/StrategySignals.tsx`：**保留 `null` 与 `[]` 的三态区分**——`null` 展示「无参考策略」、`[]` 展示「本周无策略信号」、非空渲染表格（这是重建中最容易被压平成一种空态的行为，design D9）；方向用 `Tag` 区分
- [x] 9.3 新建 `web/src/pages/simulator/DecisionForm.tsx`：解析函数保留旧语义——买入按 `,` 再按 `:` 切分、百分比**除以 100** 转为小数比例、格式不完整的项**静默跳过**（design D9，不改既有行为）；卖出为逗号分隔的裸代码、展开为 `target_pct: 0` 的卖出单；**买入先于卖出**输出；格式提示放在 `placeholder` 上

> **实现期偏离**：`parseOrders` 抽到同目录的 `orders.ts`。一是纯逻辑与组件混在一个文件会让该文件无法 fast refresh（oxlint 的 `only-export-components` 直接报出）；二是它单独成文件后，DSL 的语义可以脱离 React 被断言。行为逐条保留，包括「多余冒号只取前两段」这个旧实现的边角。**另外**：`Number(pct) / 100` 在旧实现里对非数字百分比会算出 `NaN` 并发出 `{"target_pct": null}`，服务端 `float(None)` 会 500；本实现把非有限值并入「格式不完整」一并跳过——与既有规则同类，且不改变任何合法输入的结果。
- [x] 9.4 `DecisionForm` 无订单且无备注时 `Modal.confirm` 二次确认；跳过本周同样二次确认
- [x] 9.5 `DecisionForm` 的成交回执用 `notification` 而非 `message`（原实现是 `alert` 拼多行：`成交 N 笔` + 逐笔 + 警告行，`message` 是单行单例会截断并互相覆盖，design D9）；成功后清空三项输入并触发父级重新加载；失败展示错误且**保留用户输入**
- [x] 9.6 新建 `web/src/pages/simulator/SessionDetail.tsx`：概览项（基准点位与周涨跌、组合市值、现金、已决策次数），基准相关项在 `snapshot.market` 为 `null` 时**不渲染**（不以 0 冒充缺失基准）；数据警告**逐条**展示；组合 `PortfolioTable` / `FactorRanking` / `StrategySignals` / `DecisionForm`
- [x] 9.7 `SessionDetail` 用 `Segmented`（决策 / 对比）在页内切换，`ComparisonView` 懒挂载（首次切到对比才请求），**切换不重新请求会话详情**（design D10）
- [x] 9.8 `DecisionForm` 的 `onExecuted` 回调触发重新加载快照，使周次、持仓与组合市值反映推进后的状态
- [x] 9.9 `SessionDetail` 覆盖加载失败与会话不存在两个分支，各带返回列表的入口；用 `useEffect(..., [sessionId])` 重置状态

## 10. 仿真重建：对比视图

- [x] 10.1 新建 `web/src/pages/simulator/ComparisonView.tsx`：请求 `simulatorApi.compare(sessionId)`，用 `EChart` 绘制三条净值曲线；`nav_manual` / `nav_strategy` / `nav_benchmark` 按 `trade_date` 合并为「一个日期一行」的数组（旧实现的合并逻辑与图表库无关，直接搬运），按日期字符串排序
- [x] 10.2 **不引入 `rebase`**：仿真的三条序列由 `ComparisonEngine` 全部归一化到 1.0，本来就同轴可比；照搬回测详情页的 `rebase()` 会把已归一化的序列再除一次（design D11）
- [x] 10.3 `nav_strategy` / `nav_benchmark` 为 `null` 时对应曲线不绘制，手动盘曲线始终绘制；横向缩放复用已注册的 `DataZoomComponent`
- [x] 10.4 指标区按主体（手动 / 策略 / 基准）分组展示累计收益、年化收益、夏普比率、最大回撤、周胜率；**最大回撤不按正负着色**（幅度而非盈亏）；缺失值展示占位符；未识别的指标键回落到原键名而非留空
- [x] 10.5 逐周差异表展示周次、日期、双方独有、共同持有与警告；代码列表 join 后用占位符兜底；`html_path` 以 `<code>` 展示
- [x] 10.6 确认未新增任何 `.css` / `.less` / `.scss`，且未引入 `recharts` 或全量 `echarts`

## 11. 指数日线同步的测试补强

- [x] 11.1 在 `tests/test_simulator_snapshot.py` 新增用例：以桩适配器驱动 `sync_index_daily(store, adapter, ["000300.SH"])` 填充 `daily_kline`，再调 `SnapshotBuilder._build_market_overview(cursor)`，断言返回非 `None` 且 `benchmark_code == "000300.SH"`、`benchmark_close` 非 0
- [x] 11.2 断言该路径写入的指数行 `amount` / `pct_change` / `turn_rate` 为 `0.0`（与既有的 `_build_minimal_db` 手工插值（非零）形成对照），并在用例 docstring 写明：手工插行证明不了同步，覆盖必须走同步入口（spec 的 `Market overview covered through the sync path`）
- [x] 11.3 保留既有的 `_build_minimal_db` 与四条用例不变——它们覆盖的是快照自身的组装逻辑，仍然有效；只有 `_build_market_overview` 的非空断言被新用例接走
- [x] 11.4 确认既有用例里 `len(dates) < 10` 的提前返回不掩盖新用例：新用例不依赖该日历构造，短路时不应静默通过

## 12. 前端契约测试

- [x] 12.1 在 `tests/test_ui_shell_contract.py` 的 `SECTION_LABELS` 之外补 `TestReportsSection`：`navigation.tsx` 的 `/reports` 为 `implemented: true`、`routes.tsx` 含 `/reports` 与 `/reports/:runId`、`pages/reports` 含 `List` 与 `Preview`、无新增样式表、`Preview.tsx` 不含 `runsApi`（预览不触发生成）、`Preview.tsx` 用 `iframe` 而非 `srcDoc`
- [x] 12.2 补 `TestSimulatorSection`：`navigation.tsx` 的 `/simulator` 为 `implemented: true`、`routes.tsx` 含 `/simulator` 与 `/simulator/:sessionId`、`pages/simulator` 含八个组件、无新增样式表、未引入 `recharts`、`orders.ts` 含 `/ 100`（比例换算）与买入先于卖出的顺序、`StrategySignals.tsx` 同时含「无参考策略」与「本周无策略信号」、`ComparisonView.tsx` 不含 `rebase`

> **实现期偏离**：`/ 100` 与买入先于卖出的断言落在 `orders.ts` 而非 `DecisionForm.tsx`——`parseOrders` 被抽成独立模块（见 9.3）。另补了若干条实现期才看清的契约：删除失败不得移除行、未知会话状态不得渲染空白、创建请求不得发送空参数、共享请求模块不得被改动。
- [x] 12.3 确认 `tests/test_simulator_api.py::TestFrontendContract` 的两条断言仍然通过：`web/vite.config.ts` 的 `9333` / `localhost:9555` / `/api` 与 `web/src/api/http.ts` 的 `import.meta.env.VITE_API_BASE || '/api'` 均未被改动（新客户端 `api/simulator.ts` 建立在其之上，不动 `http.ts`）

## 13. 验证

- [x] 13.1 `uv run pytest` 全绿
- [x] 13.2 `uv run ruff check` / `uv run ruff format --check` / `uv run mypy src` 通过
- [x] 13.3 `cd web && npm run build` 通过，`npm run lint`（oxlint）无错误
- [x] 13.4 端到端（报告）：在任务中心以 `kind: weekly` 提交一次周报 → 进度按四个阶段推进、日志实时追加 → 任务详情出现一条 `storage=html` 的产物
- [x] 13.5 端到端（报告）：先跑一次 `kind: factor_ic` 让 `ic_series` 有行，再跑周报 → 打开报告 HTML，因子 IC 面板**有数据**（改动前恒空），交易明细表**有内容**（改动前缺失）
- [x] 13.6 端到端（报告）：打开报告列表 → 每行含状态、生成时间、数据时效与信号/成交笔数；进入预览 → 内嵌渲染、图表可见、下载得到 `weekly_YYYY_MM_DD.html`
- [x] 13.7 端到端（提示条）：以注入的非周五日期生成一次报告 → 提示条出现且文案含实际信号日期、不含「最近周五」；以周五日期生成 → 提示条不出现
- [x] 13.8 端到端（仿真零回归）：对照 `HEAD` 的八个旧组件逐项核对——创建会话、列出会话、删除会话、逐周决策（买入 `代码:百分比`、卖出裸代码、备注）、跳过本周、持仓表、因子排名、策略信号三态、对比视图三线曲线与逐周差异，全部可用
- [x] 13.9 端到端（仿真推进）：执行一次调仓 → 周次推进、持仓与组合市值更新；提交空操作 → 二次确认出现
- [x] 13.10 端到端（导航）：两个分区入口进入真实页面而非占位页；直接刷新 `/reports/{run_id}` 与 `/simulator/{session_id}` 均不 404
- [x] 13.11 确认全仓无新增 `.css` / `.less` / `.scss`，且 `recharts` 零命中
- [x] 13.12 `openspec validate migrate-report-and-simulator-ui --strict` 通过

## 备注

- 报告的**内容**在非周五运行时与改动前逐值相同——本变更只改判据与文案，不改信号锚点。核对方式是同一信号日期在两种注入日期下渲染出的信号表、持仓表与指标卡逐值一致（任务 3.6）。
- `factor_ic_data` 从「唯一入口」降级为「覆盖项」，两份既有测试断言的是旧语义。任务 1.6 显式列出它们，不静默改断言。
- 仿真前端无组件测试框架，第 8–10 组的渲染只能靠第 12 组的源码契约断言加 13.8 的浏览器逐项核对。要把渲染变成回归测试需要为前端引入测试框架，那是独立变更（与 C4/C5 的备注一致）。
- 报告列表只覆盖本次变更后由任务生成的周报，仓库里已有的 `reports/weekly_2026_08_05.html` 不会出现。这是 design D5 已确认的取舍，空态文案需说明原因。

## 验证期修正（`/opsx:verify` 发现并已修）

两份对抗式审计（报告域 49 个场景、仿真域 48 个场景）各跑一遍，逐场景核对实现与测试。以下五类问题在验证期修掉，均已重跑受影响套件：

1. **进行中的周报不可见（报告域审计评为首位）** —— `report_query.py` 用内连接连 `artifact`，而产物在服务返回之后才登记，于是 `pending` / `running` 的周报在跑完前根本不在列表里：`List.tsx` 的进度列与轮询是死代码，spec 的「进行中的周报任务」无法被观察到。已改为**左外连接**，运行记录为主表；无产物时数据时效与两个计数列为空而非 0。新增 `TestRunsWithoutAReportYet` 四条，`report-browser-ui` 的列表需求措辞与 design D5 同步更正。

2. **预览入口的判据错了** —— `List.tsx` 以 `status === 'ok'` 作为是否给预览链接的条件，而 spec 的条件是「未产出报告的行」。一个产出报告后**被取消**的运行因此没有入口，`Preview.tsx` 的「取消于」分支不可达。已改为以 `file_name` 为准。

3. **仿真创建失败只剩 3 秒 toast** —— 重建把旧的常驻内联错误块丢了，而 spec 要求「页面展示该错误的信息」。已补回常驻 `Alert`，与 toast 并存。

4. **五条源码契约断言形同虚设**（仿真域审计逐条点名）：
   - `assert "notification" in source` —— 被 `useApp()` 解构本身满足，把回执改用单行的 `message` 也照样通过。改为断言 `notification.info(` 且 `message.info(` 不存在。
   - 信号两态只断言两个字符串存在 —— 交换分支仍然通过。改为断言分支顺序。
   - `"/ 100"` 同时匹配 `"/ 1000"` —— 改用词边界正则。
   - 基准项只要求存在一个 `{market && (` —— 两个 gate 少一个仍通过。改为断言计数为 2。
   - 已补：卖单 `target_pct: 0`、格式不完整的买入项被跳过、报告预览与仿真创建的错误分支。

5. **两处文档与代码不符** —— `PortfolioTable` 的 `pct()` 注释声称有缺失值回退（实际没有）；`weekly-reporting` 的「生成完整周报」写着「因子计算」这一步，而管线里没有它（空库上会生成因子层为空的报告且没有任何提示）。均已按实际行为改写。

另新增 `TestFrontendFieldAlignment`：此前没有任何断言把 `web/src/api/reports.ts` 的字段名与 `api/reports.py` 的响应键绑起来，任一侧改名都会让页面安静地退化成一片「—」。

**未修、留作已知项**：仿真域 48 个场景里 21 个只有可读代码、没有任何断言；报告预览的 IC 颜色（现已有测试）之外仍有若干容器级断言。要把前端渲染变成真正的回归测试需要引入 JS 测试框架，是独立变更。
