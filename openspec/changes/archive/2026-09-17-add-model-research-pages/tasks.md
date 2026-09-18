## 1. 模型评估结果数据模型

- [x] 1.1 在 `data/schema.py` 的 `SCHEMA_SQL` 追加三张表：`model_ic_series(run_id, trade_date, rank_ic)`、`model_feature_importance(run_id, factor, importance, std)`、`model_metric(run_id, metric_name, metric_value)`，主键分别为 `(run_id, trade_date)` / `(run_id, factor)` / `(run_id, metric_name)`；注释说明模型 IC 与因子 `ic_series` 分开的理由
- [x] 1.2 将三张表加入 `data/store.py` 的 `TABLE_NAMES`；`model_ic_series` 加入 `TABLE_DATE_COLUMNS`（日期列为 `trade_date`），`model_feature_importance` / `model_metric` 不注册（无日期列）
- [x] 1.3 新建 `tests/test_model_persistence.py` 的表结构部分：`init_db` 后三张表存在、重复初始化幂等、`table_stats` 对空表返回 0 行且两张无日期列的表无日期边界、未知表名仍被拒绝

## 2. 结果存取模块

- [x] 2.1 新建 `src/quant_trade/models/persistence.py`：定义 `IC_COLUMNS` / `IMPORTANCE_COLUMNS` / `METRIC_COLUMNS` 三个列序常量（照 `factors/ic_store.py` 的 `IC_COLUMNS` 写法）
- [x] 2.2 实现 `save_model_evaluation(store, run_id, ic_frame, importance_frame, metric_frame) -> ModelRows`，写入走 `store.conn.register` + `INSERT OR REPLACE` 批量插入；空 DataFrame 跳过并计 0 行；列缺失时抛 `ValueError` 并指出缺失列名
- [x] 2.3 实现读取函数 `get_model_ic_series(store, run_id)` / `get_model_importance(store, run_id, limit=None)` / `get_model_metrics(store, run_id)`，均为参数化 SQL；重要性的 `limit` 按 `importance DESC` 截断；查询失败返回空 DataFrame 并 `logger.warning`（照 `get_ic_series`）
- [x] 2.4 实现 `list_model_runs(store, limit, offset) -> tuple[list[ModelRunRow], int]`，JOIN `run`（**只读**）与 `model_metric`（透视 `ic_mean` / `ic_ir` / `ic_positive_ratio` / `windows_trained` / `prediction_rows`），实际区间取自 `model_ic_series` 的 `MIN/MAX(trade_date)`，按 `COALESCE(started_at, created_at) DESC` 排序并返回总数
- [x] 2.5 在 `tests/test_model_persistence.py` 补：写入后读回逐值相等、同 `run_id` 重写幂等、空输入不写行、缺列被拒绝、`limit` 截断按 importance 降序、模型 IC 写入后 `ic_series` 表行数不变、`list_model_runs` 不产生 `run` 行（只读断言）

## 3. 训练服务改造

- [x] 3.1 `services/models.py` 新增 `LightGBMParams`（显式字段 `num_leaves` / `min_data_in_leaf` / `learning_rate` / `feature_fraction` / `bagging_fraction` / `bagging_freq`，各带取值边界，字段可空）
- [x] 3.2 `TrainParams` 新增 `train_years` / `valid_years` / `predict_months` / `early_stopping` / `num_boost_round`（均可空，带边界）与 `lgb_params: LightGBMParams | None`
- [x] 3.3 `train_model` 用参数对象组装 `TrainConfig`：非 `None` 的窗口字段覆盖默认值，`lgb_params.model_dump(exclude_none=True)` 合并进 `DEFAULT_PARAMS` 的副本；未覆盖项保持默认
- [x] 3.4 `TrainResult` 新增 `ic_series`（逐日 `(date, rank_ic)` 列表）与 `ic_days`（有效天数）；既有 `ic_mean` / `ic_ir` / `ic_positive_ratio` 字段语义不变
- [x] 3.5 `train_model` 在 `ctx.run_id` 非空时把逐日 IC、特征重要性与汇总指标（`ic_mean` / `ic_ir` / `ic_positive_ratio` / `ic_days` / `prediction_rows` / `windows_trained`）写入三张表；为空时不写库、正常返回，并在 docstring 中写明该行为
- [x] 3.6 修正取消路径的进度上报：`services/models.py:118` 的 `ctx.progress(1.0, ...)` 改为仅在未取消时执行
- [x] 3.7 `PredictResult` 新增 `scores: list[PredictionRow]`（该日全部得分，按分数降序）与 `top_n: int`，`picks` 改为 `scores[:top_n]` 的派生属性；`predict_for_date` 填充 `scores` 并保持既有 `picks` 行为不变
- [x] 3.8 修正 `scripts/smoke_ml_pipeline.py:66` 的过时用法：`walk_forward_train` 现在返回 `WalkForwardResult`，不再按二元组解包
- [x] 3.9 在 `tests/test_services_pipeline.py` 补：带 `run_id` 的上下文落库行数与内容、`NULL_CONTEXT` 不落库、两次 `NULL_CONTEXT` 调用互不覆盖、窗口与超参数覆盖生效且不改全局默认、取消后进度小于 1.0、取消后已训练窗口的 IC 仍落库、`picks` 与 `scores[:top_n]` 一致

## 4. 模型评估读取服务

- [x] 4.1 新建 `src/quant_trade/services/model_query.py`：`ModelRunListParams`（`limit` / `offset`）、`ModelEvaluationParams`（`run_id` / `importance_top_n`，带边界），均继承 `ServiceParams`
- [x] 4.2 定义结果 dataclass：`ModelRunSummary` / `ModelRunListResult`（含 `total`）、`ModelICPoint` / `ModelYearSummary` / `ModelEvaluationResult`（汇总指标 + 逐日 IC + 分年度表现 + 特征重要性 + 覆盖区间）；`run_id` 不存在时以未找到标记表达（由 API 层转 404）
- [x] 4.3 实现 `model_run_list(params, ctx)`：读 `list_model_runs`，组装请求参数摘要与实际覆盖区间
- [x] 4.4 实现 `model_evaluation(params, ctx)`：读指标 / IC 序列 / 重要性，并按自然年聚合出 IC 均值、IC_IR、正 IC 占比与有效天数
- [x] 4.5 在 `services/__init__.py` 的 `TYPE_CHECKING` 块、`__all__`、`_MODULE_BY_NAME` 三处登记新增的公开名称
- [x] 4.6 新建 `tests/test_services_model_query.py`：列表分页与倒序、评估组装、分年度聚合值与逐日 IC 分组复算一致、重要性 top-N 截断、不存在的 `run_id` 返回未找到、参数 `model_dump_json` 往返

## 5. 任务类型注册

- [x] 5.1 在 `jobs/registry.py` 的 `JOBS` 注册 `model_train` → `TrainParams` / `train_model`
- [x] 5.2 新增产物映射 `_model_train_artifacts(result)`：逐日 IC 行数、特征重要性行数各登记一条 `table` 产物，预测行数登记一条 `parquet` 产物（`ref` 为输出路径），`meta` 记 `windows_trained` / `start` / `end`；任一为 0 时不登记该条
- [x] 5.3 在 `tests/test_jobs_runner.py` 补：提交 `kind: model_train` 后 `run` 状态流转到 `ok`、三张表出现该 `run_id` 的行、产物按条登记；无产出的运行不登记产物；既有四个 kind 不受影响

## 6. 模型域 HTTP 路由

- [x] 6.1 新建 `src/quant_trade/api/models.py`：`create_models_router(config) -> APIRouter`，前缀 `/api/models`，只读、不含 POST
- [x] 6.2 `GET /api/models/runs` 返回训练运行列表（服务端分页 + 总数）
- [x] 6.3 `GET /api/models/runs/{run_id}` 返回指标 / 逐日 IC / 分年度表现 / 特征重要性，未找到返回 404
- [x] 6.4 `GET /api/models/predictions` 返回指定日期的全部得分与 top-N（`as_of` 可空、`top_n` 带边界）
- [x] 6.5 请求上下文用与 `api/factors.py` 同款的 `_context`（每请求自开 `DataStore`，默认配置而非只读）
- [x] 6.6 在 `runtime/app.py` 的 `include_router` 段挂载模型路由，位置在 SPA 回退之前
- [x] 6.7 新建 `tests/test_api_models.py`：列表分页、评估详情、404、预测查询、`importance_top_n` 越界返回 422、`/api/models/predictions` 不被 `/api/models/runs/{run_id}` 捕获、`/api/models/*` 不打到 SPA 回退

## 7. 前端接口封装

- [x] 7.1 新建 `web/src/api/models.ts`：基于 `http.ts` 的 `request<T>`，封装列表、评估、预测三个接口，类型与后端返回字段逐一对齐（snake_case）
- [x] 7.2 确认本分区不需要新增图表注册：RankIC 折线与分年度/分布柱状用已注册的 Line / Bar，缩放用已注册的 `DataZoomComponent`；若实现期发现缺项，只在 `charts/echarts.ts` 的 `echarts.use` 追加，不在页面内直接引入 `echarts`
- [x] 7.3 在 `tests/test_ui_shell_contract.py` 补模型分区的契约断言：`/models` 的 `implemented` 为 `true`、`routes.tsx` 含三条显式模型路由、模型页面未引入全量 ECharts、未新增样式表文件

## 8. 模型分区页面

- [x] 8.1 新建 `web/src/pages/models/ModelNav.tsx`：照 `pages/factors/FactorNav.tsx` 的 `Tabs` + `activeKey={location.pathname}` 写法，三个页面标签
- [x] 8.2 新建 `web/src/pages/models/Train.tsx`：提交表单（区间 `RangePicker`、因子多选、股票池、输出路径、窗口配置、LightGBM 超参数）+ 历史运行表（状态、完成窗口数、实际区间、关键指标），服务端分页、超 100 行开虚拟滚动
- [x] 8.3 提交走 `runsApi.submit('model_train', {...})`，成功后提示并 `navigate('/jobs/' + run.run_id)`；未填写的字段不出现在请求中（默认值由服务端决定）；因子选项来自 `GET /api/factors`
- [x] 8.4 新建 `web/src/pages/models/Evaluate.tsx`：RankIC 折线图（带 `dataZoom`）、汇总指标卡（`Statistic`）、分年度表现柱状图、特征重要性 top-N 横向条形图（标注窗口数）；顶部标注运行状态、数据生成时间与覆盖区间
- [x] 8.5 评估页空态与异常态：无有效 IC、无特征重要性、已取消（曲线停在最后一个预测日）、运行不存在（未找到说明 + 返回入口）
- [x] 8.6 新建 `web/src/pages/models/Predict.tsx`：日期选择（限定在预测文件覆盖的日期内）+ top-N 选股表 + 得分分布直方图；顶部标注该份预测来源的运行标识与完成时间；无预测文件时提示先训练并给出跳转
- [x] 8.7 `web/src/shell/navigation.tsx` 把 `/models` 的 `implemented` 置 `true`；`web/src/shell/routes.tsx` 补 `/models`、`/models/evaluate/:runId`、`/models/predict` 三条显式路由
- [x] 8.8 确认未新增任何 `.css` / `.less` / `.scss` 文件，样式全部来自 antd 组件与 `shell/theme.ts` token

## 9. 验证

- [x] 9.1 `uv run pytest` 全绿
- [x] 9.2 `uv run ruff check` / `uv run ruff format --check` / `uv run mypy src` 通过
- [x] 9.3 `cd web && npm run build` 通过，`npx oxlint` 无错误
- [x] 9.4 端到端：提交一次训练 → 任务详情进度按窗口推进、日志实时追加 → 完成后数据总览页出现三张模型结果表的行数与日期范围
- [x] 9.5 端到端：打开评估页 → RankIC 曲线、指标卡、分年度表现、特征重要性均有数据；缩放生效
- [x] 9.6 端到端：打开预测页 → 选择日期后 top picks 与得分分布渲染，来源运行标识正确
- [x] 9.7 端到端：提交后取消 → `run.status` 为 `cancelled`、进度未被推到 100%、已完成窗口的 IC 可在评估页看到
- [x] 9.8 端到端：以 `NULL_CONTEXT` 脚本调用训练两次 → 三张表无 `run_id` 为空的行，且两次调用互不覆盖
- [x] 9.9 `openspec validate add-model-research-pages --strict` 通过

## 备注

- 模型分区的页面渲染（曲线、指标卡、分布图、空态与错误态）在实现期用无头浏览器逐页核对过一次。仓库没有前端组件测试框架，`tests/test_ui_shell_contract.py` 的 `TestModelSection` 只能对源码文本断言这些状态存在；要把渲染本身变成回归测试，需要为前端引入测试框架，那是独立变更。
- 运行中的训练行只有 `progress` / `message` 是活的（其余各项都在训练结束时写入）。该字段由 `list_model_runs` 一并带出，页面在未终态的行上用进度条替代空的结果列。
