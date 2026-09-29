## 1. IC 序列数据模型

- [x] 1.1 在 `data/schema.py` 的 `SCHEMA_SQL` 新增 `ic_series(factor_name, trade_date, forward_period, ic, rank_ic, sample_size)` 表，主键 `(factor_name, trade_date, forward_period)`
- [x] 1.2 将 `ic_series` 加入 `data/store.py` 的 `TABLE_NAMES` 与 `TABLE_DATE_COLUMNS`（日期列为 `trade_date`），使数据总览与 `/api/data/status` 自动纳入
- [x] 1.3 在 `factors/ic_store.py`（原计划 `factors/storage.py` 不存在，落库代码在 `alpha158/storage.py`）新增 `save_ic_series(store, frame) -> int` 与 `get_ic_series(store, factors, start, end, forward_period=None) -> DataFrame`，写入走 `INSERT OR REPLACE` 以复用主键幂等
- [x] 1.4 新建 `tests/test_factors_ic_store.py`：写入、多持有期共存、同主键重算覆盖、按因子与区间读取、无记录返回空

## 2. 批量 IC 计算

- [x] 2.1 在 `factors/analysis.py` 新增 `compute_ic_frame(store, factors, universe, dates, forward_periods) -> pd.DataFrame`，一次批量读取因子值与日线，内存逐日计算，返回长表 `factor_name, trade_date, forward_period, ic, rank_ic, sample_size`
- [x] 2.2 批量取数：因子值一次 `get_factor_values`，日线一次查询并覆盖区间末尾最大持有期，复刻逐日路径「基准取 base_date 当日或之后首个交易日」的取值语义
- [x] 2.3 样本不足（有效截面对数 < 10）与数据缺失的日期跳过，不产出 NaN 行
- [x] 2.4 新建 `tests/test_factors_ic_frame.py`：断言查询次数与区间长度无关（用计数包装的 store）
- [x] 2.5 同文件新增一致性测试：同一 fixture 下 `compute_ic_frame` 与 `compute_ic_series` 的逐日 IC 结果逐一相等
- [x] 2.6 同文件覆盖：多因子多持有期、空因子值、单日期区间、`compute_ic_series` 签名未变

## 3. 分层回测

- [x] 3.1 新建 `src/quant_trade/factors/quantile.py`，纯函数实现截面 `qcut` 分组、组内等权下期收益、累乘净值
- [x] 3.2 返回分组净值、多空组合净值（顶组减底组收益后累乘）与跳过的日期数；分组数默认为 5
- [x] 3.3 有效因子值少于分组数的交易日跳过，不产出分组记录
- [x] 3.4 新建 `tests/test_factors_quantile.py`：分组净值期初为 1、多空净值符号、分组数可配置、样本不足跳过、区间无因子值返回空而非抛异常
- [x] 3.5 断言实现路径不引用 `quant_trade.backtest`（用构造数据验证无费用与交易约束介入）

## 4. 因子相关性

- [x] 4.1 新建 `src/quant_trade/factors/correlation.py`，纯函数实现逐交易日截面 Pearson 相关，再对日期取均值
- [x] 4.2 返回对称矩阵、因子名顺序与参与计算的有效日期数；对角线为 1
- [x] 4.3 单日期区间正常返回；有效截面对数 < 10 的日期跳过；无有效日期返回空矩阵
- [x] 4.4 新建 `tests/test_factors_correlation.py`：对称性与对角线、构造的强相关因子对得出高相关、稀疏日期被跳过、单日期区间、无有效日期返回空
- [x] 4.5 批量取数要求由服务层保证（`correlation_matrix` 是纯函数，本身不查库）：查询次数断言并入 5.9 的服务层测试

## 5. 因子分析服务层

- [x] 5.1 新建 `src/quant_trade/services/factor_analysis.py`：`FactorICComputeParams`（`factors` / `start_date` / `end_date` / `forward_periods` / `universe`）、`FactorQuantileParams`、`FactorCorrelationParams`、`FactorCoverageParams`，均继承 `ServiceParams`
- [x] 5.2 `FactorCorrelationParams.factors` 施加 `max_length=50` 上限，超限在校验阶段报错
- [x] 5.3 实现 `compute_factor_ic(params, ctx=NULL_CONTEXT)`：算 IC 后写 `ic_series`，取消检查置于因子循环头部，按因子上报进度，每因子 `ctx.log` 一条；返回结果含写入行数与完成因子数
- [x] 5.4 实现 `factor_quantile_backtest(params, ctx=NULL_CONTEXT)`：批量取数后调用 `factors/quantile.py`，返回分组净值与多空净值
- [x] 5.5 实现 `factor_correlation(params, ctx=NULL_CONTEXT)`：批量取数后调用 `factors/correlation.py`
- [x] 5.6 实现 `factor_coverage(params, ctx=NULL_CONTEXT)`：逐因子记录数、最早与最晚交易日；未落库因子不出现在结果中
- [x] 5.7 缺交易日历的守卫与既有服务一致：`ValueError("Database has no trade dates; run a data sync first")`；空结果以 `warning` 日志返回而非抛异常
- [x] 5.8 在 `services/__init__.py` 的 `TYPE_CHECKING` 块、`__all__`、`_MODULE_BY_NAME` 三处登记新增的公开名称
- [x] 5.9 新建 `tests/test_services_factor_analysis.py`：IC 落库行数与内容、取消后已完成因子保留、相关因子数超限报校验错、分层结果形状、覆盖度对未落库因子的处理
- [x] 5.10 参数序列化往返：各 Params 经 `model_dump_json` / `model_validate_json` 后等价

## 6. 任务类型注册

- [x] 6.1 在 `jobs/registry.py` 的 `JOBS` 注册 `factor_compute` → `Alpha158Params` / `compute_alpha158`，产物映射登记 `factor_values` 表行数
- [x] 6.2 同处注册 `factor_ic` → `FactorICComputeParams` / `compute_factor_ic`，产物映射登记 `ic_series` 表行数
- [x] 6.3 在 `tests/test_jobs_runner.py` 补充两个 kind 的端到端：提交后 `run` 状态流转到 `ok`、产物登记行存在；验证 `data_sync` 不受影响

## 7. 因子域 HTTP 路由

- [x] 7.1 新建 `src/quant_trade/api/factors.py`：`create_factors_router(config) -> APIRouter`，前缀 `/api/factors`
- [x] 7.2 `GET /api/factors` 返回因子列表（名称、分类、覆盖状态），服务端分页并返回总数
- [x] 7.3 `GET /api/factors/ic` 读 `ic_series`，返回按日期升序的序列与汇总指标（IC 均值、标准差、IC_IR、正值占比）
- [x] 7.4 `GET /api/factors/ic/decay` 返回同一因子各持有期的 IC 均值
- [x] 7.5 `GET /api/factors/quantile` 与 `GET /api/factors/correlation` 现算并返回，本路由不含任何 POST
- [x] 7.6 请求上下文用与 `api/data.py` 同款的 `_context` 写法（每请求自开 `DataStore`，用默认配置而非只读）
- [x] 7.7 在 `runtime/app.py` 挂载因子路由，位置在 SPA 回退之前
- [x] 7.8 新建 `tests/test_api_factors.py`：列表分页、IC 序列与汇总、无 IC 记录返回空、相关因子数超限返回 422、分层与相关端点正常返回、`/api/factors` 不打到 SPA 回退

## 8. 前端图表与接口封装

- [x] 8.1 新建 `web/src/charts/echarts.ts`：从 `echarts/core` 按需注册 Line / Bar / Heatmap 与 Grid / Tooltip / Legend / MarkLine / VisualMap / DataZoom / CanvasRenderer
- [x] 8.2 新建 `web/src/charts/EChart.tsx`：薄封装 `echarts-for-react`，统一高度、`notMerge` 与空数据不渲染
- [x] 8.3 新建 `web/src/api/factors.ts`：基于 `http.ts` 的 `request<T>`，封装因子列表、IC 序列、衰减、分层、相关性五个接口，类型与后端返回对齐
- [x] 8.4 因子勾选状态用一个 `localStorage` 读写的小模块承载（`pages/factors/selection.ts`），读取失败时回退为空选择（不抛异常）

## 9. 因子分区页面

- [x] 9.1 新建 `web/src/pages/factors/FactorNav.tsx`：照 `pages/data/DataNav.tsx` 的 `Tabs` + `activeKey={location.pathname}` 写法，四个页面标签
- [x] 9.2 新建 `web/src/pages/factors/Library.tsx`：因子表（名称、分类、覆盖区间、记录数），服务端分页，超过 100 行开虚拟滚动，含分类筛选与勾选列，未落库因子标注
- [x] 9.3 因子库页面提供 Alpha158 计算提交：`runsApi.submit('factor_compute', {...})` 后 `navigate('/jobs/' + run.run_id)`
- [x] 9.4 新建 `web/src/pages/factors/IcAnalysis.tsx`：RankIC 折线（含零轴参考线）、汇总指标、衰减柱状图、无数据时的空状态与前往计算任务的入口
- [x] 9.5 IC 页面提供 `factor_ic` 任务提交入口
- [x] 9.6 新建 `web/src/pages/factors/Quantile.tsx`：因子 / 区间 / 分组数 / 调仓频率表单，分组净值多线图与多空净值图，页面标注「统计视图，非可交易收益」
- [x] 9.7 新建 `web/src/pages/factors/Correlation.tsx`：因子多选 + 显式计算按钮 + 热力图，展示参与计算的有效日期数，超过因子上限时提交前提示
- [x] 9.8 `web/src/shell/navigation.tsx` 把 `/factors` 的 `implemented` 置 `true`；`web/src/shell/routes.tsx` 补四条显式路由
- [x] 9.9 确认未新增任何 `.css` / `.less` / `.scss` 文件，样式全部来自 antd 组件与 `shell/theme.ts` token

## 10. 验证

- [x] 10.1 `uv run pytest` 全绿
- [x] 10.2 `uv run ruff check` / `uv run ruff format --check` / `uv run mypy src` 通过
- [x] 10.3 `cd web && npm run build` 通过，`npx oxlint` 无错误
- [x] 10.4 端到端：因子库页发起 Alpha158 计算 → 提交后跳转任务中心（`/jobs/<run_id>`）；完成后因子库覆盖度出现 158 个因子（行数 4,673,369，区间 2026-06-15..2026-08-07）
  - 附注：`compute_alpha158` 无取消检查点（单一阻塞调用），提交后取消不生效——既有行为，本变更未改动
- [x] 10.5 端到端：发起 `factor_ic` 计算 → IC 分析页选中该因子后曲线、IC_IR、胜率、衰减图均有数据
- [x] 10.6 端到端：分层回测页提交一次请求 → 分组净值与多空净值渲染，且可见统计视图标注
  - 附注：日期区间选择器由 antd `RangePicker` 归一化，无法产生倒置区间；页面的区间校验与 `SyncForm` 同款，用于「参数从别处恢复」的场景，未在浏览器中触发
- [x] 10.7 端到端：相关性页选择 5 个因子 → 显式点击后渲染热力图，且页面加载时未发起计算请求
- [x] 10.8 端到端：勾选两个因子后进入 IC 页，选择器已默认选中；刷新 `/factors/quantile` 不 404
  - 验证方式：`playwright-core` 驱动本地缓存的 Chromium 无头浏览器，22 项断言全通过
- [x] 10.9 数据总览页出现 `ic_series` 表行数与日期范围，无数据时标注为无数据
- [x] 10.10 `openspec validate add-factor-research-pages --strict` 通过

## 11. 验证阶段补齐（verify 后新增）

- [x] 11.1 `compute_alpha158` 改为按 90 天分块执行，每块开头检查取消；`Alpha158Result` 增加 `cancelled`，行数与因子数跨块累加（design D13）
- [x] 11.2 新建 `tests/test_services_alpha158_chunking.py`：分块结果与单次整体计算逐值相等、取消保留已完成块、单块区间不拆分、进度按块上报、空区间不落库
- [x] 11.3 补 `tests/test_services_factor_analysis.py`：分层服务的查询次数与区间长度无关（`_CountingStore` 提到模块级，并覆写 `get_daily` 以计入查询）
- [x] 11.4 补同文件：相关性服务「无有效日期」路径的 warning 日志与空矩阵断言
- [x] 11.5 补 `specs/alpha158-factors/spec.md` 的 ADDED 需求「Alpha158 计算分块执行并可取消」（5 个 scenario）
- [x] 11.6 design D4 函数表补 `factor_ic_series` / `factor_ic_decay` 两行读取路径并说明理由
- [x] 11.7 design D11 补记封装必须用 `echarts-for-react/esm/core` 的实测原因；proposal Impact 补 `services/factors.py`
- [x] 11.9 真实运行验证：150 只股票 × 3 块区间提交后取消 → `status=cancelled`、已完成块 1,347,456 行保留、产物按部分行数登记
  - 顺带发现并修复：取消时进度被写成 1.0（分块数算成了总块数而非已完成块数），现为「已完成块/总块数」，并补 `test_cancelled_run_does_not_report_full_progress`
- [x] 11.8 无头浏览器补验：倒置区间无法提交（RangePicker 归一化，服务端另有 422）、相关性页超过因子上限提示、分层页区间无数据空态、IC 页尚无 IC 数据空态
