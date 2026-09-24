## 1. 回测结果数据模型

- [x] 1.1 在 `data/schema.py` 的 `SCHEMA_SQL` 追加四张表：`backtest_nav(run_id, trade_date, nav, benchmark, drawdown)`、`backtest_trade(run_id, seq, trade_date, action, ts_code, shares, price, commission, stamp_duty, transfer_fee)`、`backtest_metric(run_id, metric_name, metric_value)`、`backtest_position(run_id, ts_code, shares, avg_cost, current_price, market_value)`，主键分别为 `(run_id, trade_date)` / `(run_id, seq)` / `(run_id, metric_name)` / `(run_id, ts_code)`
- [x] 1.2 将四张表加入 `data/store.py` 的 `TABLE_NAMES`；只有真有日期列的 `backtest_nav` / `backtest_trade` 加入 `TABLE_DATE_COLUMNS`（`trade_date`），`backtest_metric` 与 `backtest_position` 不注册
  - 验证阶段修正：`backtest_position` 无 `trade_date` 列，注册后 `table_stats` 对有行的表执行 `MIN(trade_date)` 抛 `BinderException`，`/api/data/status` 500。已补 `test_populated_tables_without_a_date_column_still_report`
- [x] 1.3 新建 `tests/test_backtest_result_store.py` 的表结构部分：`init_db` 后四张表存在、重复初始化幂等、`table_stats` 对空表返回 0 行且 `backtest_metric` 无日期边界、未知表名仍被拒绝

## 2. 结果存取模块

- [x] 2.1 新建 `src/quant_trade/backtest/result_store.py`：定义 `NAV_COLUMNS` / `TRADE_COLUMNS` / `METRIC_COLUMNS` / `POSITION_COLUMNS` 四个列序常量（照 `factors/ic_store.py` 的 `IC_COLUMNS` 写法）
- [x] 2.2 实现 `save_backtest_result(store, run_id, nav_frame, trade_frame, metric_frame, position_frame) -> BacktestRows`，写入走 `store.conn.register` + `INSERT OR REPLACE` 批量插入；空 DataFrame 跳过并计 0 行
- [x] 2.3 净值写入按净值日期左连接基准序列（索引对齐后 `reindex`），基准缺失的日期写 NULL；`drawdown` 列由净值序列的 `cummax` 一次算出
- [x] 2.4 交易明细写入前把引擎的字符串日期解析为 `date`；解析失败的行记 `logger.warning` 后跳过，不中断整批写入
- [x] 2.5 实现读取函数 `get_backtest_nav` / `get_backtest_trades` / `get_backtest_metrics` / `get_backtest_positions`，均为参数化 SQL，查询失败返回空 DataFrame 并 `logger.warning`（照 `get_ic_series`）
- [x] 2.6 实现 `list_backtest_runs(store, limit, offset) -> tuple[list[BacktestRunRow], int]`，JOIN `run`（只读）与 `backtest_metric`（透视关键指标），按 `COALESCE(started_at, created_at) DESC` 排序并返回总数
- [x] 2.7 在 `tests/test_backtest_result_store.py` 补：写入后读回逐值相等、同 `run_id` 重写幂等、基准缺失写 NULL、空输入不写行、非法日期行被跳过、`list_backtest_runs` 不产生 `run` 行（只读断言）

## 3. 回测服务改造

- [x] 3.1 `services/backtest.py` 的 `BacktestParams` 新增 `top_n: int | None`（`ge=1`）
- [x] 3.2 `run_backtest_raw` 在 `build_strategy` 之后按 `SignalParams` 同款写法应用 `top_n`（仅当策略有该属性）
- [x] 3.3 `BacktestResult` 新增 `rows_saved: BacktestRows` 字段，由 `run_backtest_service` 填充
- [x] 3.4 `run_backtest_service` 在 `ctx.run_id` 非空时把结果转为四个 DataFrame 并调用 `save_backtest_result`；为空时不写库、正常返回，并在 docstring 中写明该行为
- [x] 3.5 修正取消路径的进度上报：取消时 SHALL NOT 把进度推为 1.0（`services/backtest.py:156` 的 `ctx.progress(1.0, "Backtest complete")` 改为仅在未取消时执行）
- [x] 3.6 在 `tests/test_services_pipeline.py` 补：带 `run_id` 的上下文落库行数与内容、`NULL_CONTEXT` 不落库、两次 `NULL_CONTEXT` 调用互不覆盖、`top_n` 覆盖生效且不回写配置、取消后进度小于 1.0、取消后已跑周次的净值仍落库

## 4. 回测结果读取服务

- [x] 4.1 新建 `src/quant_trade/services/backtest_query.py`：`BacktestRunListParams`（`limit` / `offset`）、`BacktestDetailParams`（`run_id`）、`BacktestTradeParams`（`run_id` / `limit` / `offset`）、`BacktestComparisonParams`（`runs: list[str]`，`min_length=1` / `max_length=8`），均继承 `ServiceParams`
- [x] 4.2 定义结果 dataclass：`BacktestRunSummary` / `BacktestRunListResult`（含 `total`）、`BacktestDetailResult`（元信息 + 指标 + 净值序列 + 持仓）、`BacktestTradeResult`（含 `total`）、`BacktestComparisonResult`（多条净值 + 指标对照）
- [x] 4.3 实现 `backtest_run_list(params, ctx)`：读 `list_backtest_runs`，实际起止日取自 `backtest_nav` 的 MIN/MAX，状态取自 `run`
- [x] 4.4 实现 `backtest_detail(params, ctx)`：`run_id` 不存在时返回未找到的标记（由 API 层转 404），存在时组装指标 / 净值 / 持仓
- [x] 4.5 实现 `backtest_trades(params, ctx)` 与 `backtest_comparison(params, ctx)`（后者按 runs 顺序返回各条净值与指标）
- [x] 4.6 在 `services/__init__.py` 的 `TYPE_CHECKING` 块、`__all__`、`_MODULE_BY_NAME` 三处登记新增的公开名称
- [x] 4.7 新建 `tests/test_services_backtest_query.py`：列表分页与倒序、详情组装、不存在的 run_id、交易明细分页、对比上限 8 报校验错、对比顺序与请求一致、参数 `model_dump_json` 往返

## 5. 任务类型注册

- [x] 5.1 在 `jobs/registry.py` 的 `JOBS` 注册 `backtest` → `BacktestParams` / `run_backtest_service`
- [x] 5.2 新增产物映射 `_backtest_artifacts(result)`：净值行数与指标行数各登记一条 `ArtifactDraft`，`meta` 记 `strategy` / `start` / `end`；行数为 0 时不登记
- [x] 5.3 在 `tests/test_jobs_runner.py` 补：提交 `kind: backtest` 后 `run` 状态流转到 `ok`、四张表出现该 `run_id` 的行、产物登记两条；取消路径状态为 `cancelled` 且已完成部分保留；既有三个 kind 不受影响

## 6. 回测域 HTTP 路由

- [x] 6.1 新建 `src/quant_trade/api/backtests.py`：`create_backtests_router(config) -> APIRouter`，前缀 `/api/backtests`，只读、不含 POST
- [x] 6.2 `GET /api/backtests` 返回运行列表（服务端分页 + 总数）
- [x] 6.3 `GET /api/backtests/compare` **声明在 `/{run_id}` 之前**，接收重复的 `runs` 查询参数
  - 实现期补充：新增 `GET /api/backtests/strategies`（同样声明在 `/{run_id}` 之前），供提交表单取策略清单——仓库里没有暴露 `list_strategies` 的路由，前端硬编码会在策略注册时漂移。design D7 已同步
- [x] 6.4 `GET /api/backtests/{run_id}` 返回指标 / 净值 / 持仓，未找到返回 404
- [x] 6.5 `GET /api/backtests/{run_id}/trades` 返回交易明细分页（`limit` 上限 500、`offset` 非负）
- [x] 6.6 请求上下文用与 `api/factors.py` 同款的 `_context`（每请求自开 `DataStore`，默认配置而非只读）
- [x] 6.7 在 `runtime/app.py` 挂载回测路由，位置在 SPA 回退之前
- [x] 6.8 新建 `tests/test_api_backtests.py`：列表分页、详情、404、交易明细分页、对比多 run、对比路径**不被 `{run_id}` 吃掉**、`runs` 超过上限返回 422、负 offset 返回 422、`/api/backtests` 不打到 SPA 回退

## 7. 前端接口封装与图表

- [x] 7.1 新建 `web/src/api/backtests.ts`：基于 `http.ts` 的 `request<T>`，封装列表、详情、交易明细、对比四个接口，类型与后端返回字段逐一对齐（snake_case）
- [x] 7.2 在 `web/src/charts/echarts.ts` 补注册 `PieChart`（持仓权重饼图）与 `DataZoom` 组件（净值曲线缩放）；回撤面积图用既有 Line + `areaStyle`，不新增注册
- [x] 7.3 在 `tests/test_ui_shell_contract.py` 补回测分区的契约断言：`/backtest` 的 `implemented` 为 `true`、`routes.tsx` 含三条显式回测路由、回测页面未引入全量 ECharts、未新增样式表文件

## 8. 回测分区页面

- [x] 8.1 新建 `web/src/pages/backtest/BacktestNav.tsx`：照 `pages/factors/FactorNav.tsx` 的 `Tabs` + `activeKey={location.pathname}` 写法，三个页面标签
- [x] 8.2 新建 `web/src/pages/backtest/List.tsx`：提交表单（区间 `RangePicker`、策略选择、初始资金、基准、持仓数）+ 历史运行表（状态、策略、实际区间、关键指标），服务端分页、超 100 行开虚拟滚动，行内提供详情与对比选择
- [x] 8.3 提交走 `runsApi.submit('backtest', {...})`，成功后提示并 `navigate('/jobs/' + run.run_id)`；策略选项来自 `GET /api/factors` 之外的既有策略接口（无则复用服务层列表接口），不硬编码
- [x] 8.4 新建 `web/src/pages/backtest/Detail.tsx`：净值/基准多线图（带 `dataZoom`）、回撤面积图、指标卡（`Statistic`）、期末持仓表 + 权重饼图、交易明细分页表；顶部标注运行状态与数据生成时间
  - 验证阶段修正：策略净值是绝对市值、基准已归一化到 1.0，直接同轴绘制会把基准压成 0 附近的直线。两条序列改为各自除以首个非空值后再画，超期为两条归一曲线之差（design D12）
- [x] 8.5 详情页空态：无持仓（期末空仓）、无成交、运行不存在（未找到说明 + 返回列表入口）
- [x] 8.6 新建 `web/src/pages/backtest/Compare.tsx`：运行多选 + 显式计算按钮 + 净值叠加曲线 + 指标对照表；超过运行数上限时提交前提示；已取消的运行在对照表中标注
- [x] 8.7 `web/src/shell/navigation.tsx` 把 `/backtest` 的 `implemented` 置 `true`；`web/src/shell/routes.tsx` 补 `/backtest`、`/backtest/compare`、`/backtest/:runId` 三条显式路由（`compare` 声明在 `:runId` 之前）
- [x] 8.8 确认未新增任何 `.css` / `.less` / `.scss` 文件，样式全部来自 antd 组件与 `shell/theme.ts` token

## 9. 验证

- [x] 9.1 `uv run pytest` 全绿
  - 496 passed
- [x] 9.2 `uv run ruff check` / `uv run ruff format --check` / `uv run mypy src` 通过
  - ruff check / ruff format --check / mypy src 全部通过
- [x] 9.3 `cd web && npm run build` 通过，`npx oxlint` 无错误
  - `npm run build` 通过（tsc -b + vite build），`npx oxlint` 无输出
- [x] 9.4 端到端：提交一次回测 → 任务详情进度按周推进、日志实时追加 → 完成后数据总览页出现四张回测结果表的行数与日期范围
  - 页面提交 2024-06-01~2025-06-30（持仓数 5）→ 跳转 `/jobs/<id>` → `status=ok`、`progress=1.0`；`/api/data/status` 四张回测表均有行（nav 281 / trade 141 / metric 13 / position 5）；产物登记 `backtest_nav` 与 `backtest_metric` 两条
- [x] 9.5 端到端：打开详情页 → 净值与基准曲线、回撤面积图、指标卡、期末持仓、交易明细均有数据；缩放生效
  - 详情页断言 23 项全通过：状态/策略/生成时间、10 个指标卡、三张图（净值与基准、超额、回撤）、期末持仓表 + 权重饼图、交易明细分页；未知 run_id 显示未找到并可返回列表
- [x] 9.6 端到端：在对比页选择两次运行 → 叠加曲线与指标对照表渲染，且页面加载时未发起请求
  - 直接打开 `/backtest/compare` 未发起对比请求；`?runs=a,b` 与页内显式选择两条路径均渲染叠加曲线与对照表
- [x] 9.7 端到端：提交后取消 → `run.status` 为 `cancelled`、进度未被推到 100%、已完成周次的净值可在详情页看到
  - 全区间回测提交后在任务中心取消：`status=cancelled`、`progress<1.0`；详情页标注已取消、曲线止于取消日、已完成周次的净值保留（曲线早于请求结束日）
- [x] 9.8 端到端：`/backtest/compare` 直接刷新不 404，且请求未落到 SPA 回退
  - `/backtest/compare` 直接刷新正常渲染，API 路径未落到 SPA 回退
- [x] 9.9 `openspec validate add-backtest-research-pages --strict` 通过
  - `openspec validate add-backtest-research-pages --strict` 通过

## 10. 验证阶段修正（verify 后新增）

对抗式复核（三个独立读者逐条比对 spec 场景与代码）发现 1 条 CRITICAL 与 10 条 WARNING，全部修正：

- [x] 10.1 修正空结果写行：`_metric_values()` 仅在 `raw.metrics` 非空时才补 `cash` / `final_value`，使空回测四张表均无行、产物登记为空
- [x] 10.2 补真实空路径测试 `test_empty_backtest_writes_nothing_and_registers_no_artifact`（窗口落在日历之外，而非手工构造空 frame）
- [x] 10.3 修正交易明细 `seq` 空洞：丢弃坏日期后重新编号，保证「从 1 连续递增」
- [x] 10.4 列表排序改为按 `created_at`（提交时间）倒序，与 spec 措辞及页面「提交时间」列一致
- [x] 10.5 `progress` 从 `run` 表一路透出到列表行（`BacktestRunRow` → `BacktestRunSummary` → API → 表格进度列）
- [x] 10.6 详情页取消运行时时间标签改为「取消于」
- [x] 10.7 详情页增加持久错误态与重试入口，请求失败不再只剩空白页
- [x] 10.8 空列表与空交易明细改为空态说明，不再渲染只有表头的空表格
- [x] 10.9 对比页曲线按期初归一，新增「覆盖交易日」列，取消行加说明性 Tooltip
- [x] 10.10 新增 `get_backtest_runs()` 批量取元信息，对比路径由每 run 两次查询降为常数次
- [x] 10.11 `_upsert` 补列名校验，缺列时报具名 `ValueError`（对齐 `ic_store.save_ic_series`）
- [x] 10.12 合并 `api/backtests.py` 中重复的 `_iso_time` 与 `_iso`
- [x] 10.13 强化弱断言：last-writer-wins 用不同值验证、`NULL_CONTEXT` 逐表计数、请求区间与净值区间冲突、`seq` 连续性
- [x] 10.14 补 `/api/backtests/strategies` 的端点测试（含不被 `{run_id}` 吃掉）
- [x] 10.15 spec 改写两处过头措辞：「带参数跳转即触发」与「超额为独立图表」
- [x] 10.16 补前端契约断言：期初归一、进度列、空态、取消时间标签
- [x] 10.17 design 补记 D6 与 C3 的模块边界差异，并新增「验证阶段修正」小节
- [x] 10.18 回撤坐标轴标签由 `toFixed(0)` 改为 `toFixed(1)`：回撤跨度只有几个百分点，取整后每个刻度都显示为「-0%」，轴不可读
- [x] 10.19 修正后重跑验证：`uv run pytest` 全绿、`ruff check` / `ruff format --check` / `mypy` 通过、`npm run build` + `npx oxlint` 通过、`openspec validate --strict` 通过
- [x] 10.20 无头浏览器端到端复验（fresh DB）：39 项断言全通过——表单提交与跳转、列表进度列与实际区间、详情期初归一与指标卡、对比带参数触发与「覆盖交易日」列、取消后「取消于」标签与部分净值保留、路由刷新不 404
  - 附注：断言 ECharts 轴标签需要改用 `aria-label`——坐标轴文字画在 canvas 上，`textContent` 读不到；「是否按期初归一」由源码契约测试与截图共同确认

## 11. 第二轮验证修正（verify 后新增）

第二轮对抗式复核（三个独立读者重跑场景映射，并把浏览器验证脚本逐条读了一遍）发现 1 条 CRITICAL 残留、5 条覆盖缺口与 3 个前端缺陷：

- [x] 11.1 修正 CRITICAL 残留：取消发生在第一周之前时，引擎仍会写入 `total_trades`，使 `metrics` 非空，绕过 10.1 的门。改为按「是否产出净值或成交」判定（`_produced_results`），三种空路径（空区间 / 空股票池 / 第一周前取消）统一不落库
- [x] 11.2 补 `test_cancelled_before_the_first_week_writes_nothing`：断言 `metrics == {"total_trades": 0.0}` 仍然成立，但四张表零行
- [x] 11.3 补 `test_empty_universe_writes_nothing`：覆盖此前无测试的 `engine.py` 空股票池分支
- [x] 11.4 补 `test_a_running_run_reports_its_progress` 与列表载荷的 `progress` 断言——此前只有 store 层与源码 grep，服务层与 API 的透传可静默回归
- [x] 11.5 补 `test_comparison_cost_does_not_grow_with_the_run_count`：用计数连接包住 `DataStore`，断言 1 个 run 与 3 个 run 的查询次数相等（此前 N+1 重写不会被任何测试发现）
- [x] 11.6 补 `test_detail_never_runs_the_engine`：monkeypatch `run_backtest` 使其抛异常，证明读取路径确实不重跑
- [x] 11.7 补 `test_the_same_params_twice_yield_two_result_sets` 与数据集总览的 `backtest_*` 表名断言
- [x] 11.8 详情页在 `runId` 变化时重置全部状态：React Router 复用组件实例，否则下一个 run 加载失败时，上一个 run 的曲线会留在新 URL 下
- [x] 11.9 列表与交易明细区分「请求失败」与「没有数据」：失败时展示错误与重试，不再把后端错误呈现为「尚无回测」/「无成交」
- [x] 11.10 无基准序列时「基准收益」「超额收益」展示为 `—`，不再显示引擎留下的 `0.0`（读起来像基准持平）
- [x] 11.11 对比页告警文案与 spec 一致：「覆盖区间不同，指标不具可比性」
- [x] 11.12 删除 `has_backtest_result`：`src/` 内无调用者（存在性判定用 `run` 记录），只被测试引用；相应 spec 场景改写为「不属于回测任务的 run_id 返回未找到 / 有运行记录但无结果仍可打开」
- [x] 11.13 强化前端契约断言：对比页必须**调用** rebase（不只是定义）、进度列的终态分支、取消时间标签是三元而非常量、错误态文案、分区 Tabs 的 activeKey
- [x] 11.14 浏览器验证脚本从 `.scratch/`（gitignored，会被其他会话清掉）移入 `scripts/e2e_backtest_pages.mjs` + `scripts/seed_backtest_e2e_db.py`，头部写明前置条件与「canvas 断言看不到的东西」
- [x] 11.15 修正脚本自身的三处问题：`check('...', true)` 恒真断言、提交表单填写顺序竞态（编辑 RangePicker 会重挂载数字输入框）、取消断言未覆盖「第一周前取消所以没有曲线」这一合法结果
- [x] 11.16 修正后复跑：fresh DB 39/39、已填充 DB 连跑 4 次 37/37 全绿
- [x] 11.17 design 补记：D12 结尾「对比页不受影响」与修正表自相矛盾（对比页确实已归一），一并改正；5 处过期行号按当前代码更新

## 12. 归档后补记

归档动作完成后、跑最终门禁时，`test_assembles_series_metrics_and_positions` 出现一次间歇性失败（同一测试在归档前已偶发一次）。定位为真缺陷而非测试问题：

- [x] 12.1 `get_backtest_positions` 的 `ORDER BY market_value DESC` 没有并列判定：两笔市值相同的持仓顺序未定义，同一次运行的详情页在两次读取之间可能换序。补 `ts_code` 作为并列判定
- [x] 12.2 补 `test_positions_have_a_stable_order`：三笔等市值持仓，断言两次读取顺序一致且按代码升序
- [x] 12.3 原测试改为比较排序后的代码集合，不再对「查询自身的并列判定」再叠加一层断言
- [x] 12.4 复跑：`uv run pytest` 596 passed；`test_services_backtest_query.py` 连跑 5 次全绿

**教训**：第一次看到这个间歇性失败时归因为「ruff format 与 pytest 同命令的竞态」就放过了，没有追究。实际排序并列是真实缺陷，且只在数据恰好并列时暴露。
