# Design: 模型研究页面（C5）

## Context

C1 抽出服务层，C2 建起运行基础设施（`run` / `run_log` / `artifact` 三表、单 worker 串行队列、统一 `POST /api/runs`、SSE 日志流）与 antd 外壳，C3 补齐因子域四页，C4 补齐回测域四页。`/models` 分区已在 `web/src/shell/navigation.tsx:34` 声明但 `implemented: false`，路由落在 `Placeholder`。

模型域的现状是「链路齐备、通路只开一半」：

| 能力 | 现状 |
|---|---|
| 训练 | `walk_forward_train`（`models/train.py:58`）返回 `WalkForwardResult`（`predictions` / `feature_matrix` / `feature_importance` / `windows_trained` / `cancelled`，`train.py:44-55`）。**C1 已让特征重要性真正被读取**：`model.feature_importances_` 在 `train.py:129` 逐窗口收集，`_aggregate_importance`（`train.py:157`）聚成 `factor / importance / std` 并按均值降序。逐窗口 `ctx.cancelled()`（`train.py:92`）与 `ctx.progress`（`train.py:95`）已在 |
| 服务 | `services/models.py`：`TrainParams`（`:25`）/ `train_model`（`:91`）/ `PredictParams`（`:38`）/ `predict_for_date`（`:137`）齐备，但**没有任何调用方** —— CLI 已在 C1 删除，API 层无模型路由，`JOBS` 无模型任务 |
| RankIC | `rank_ic_series`（`models/evaluate.py:12`）**逐日算出了** `ic_series`（`list[tuple[date, float]]`，`:55` 追加、`:62` 返回），但 `train_model` 只取 `ic_mean` / `ic_ir` / `ic_positive_ratio` 三个标量（`services/models.py:117,130-132`），**序列当场丢弃**，`TrainResult`（`:57-70`）上也没有承载它的字段 |
| 特征重要性 | `TrainResult.feature_importance` 已带出来（`services/models.py:68`），但同样只在内存里活到调用返回；`LGBMRegressor` 是窗口循环内的局部变量（`train.py:121`），窗口一过即释放，booster 本身从不落盘 |
| 持久化 | **只有一份预测分 parquet**（`save_predictions` → `params.output_path`，默认 `data/predictions/model_ranking.parquet`，`services/models.py:22`）。`SCHEMA_SQL` 十五张表（`data/schema.py:10-172`）无一张与模型相关。仓库里 `data/predictions/` 目前都不存在 |
| 任务类型 | `JOBS`（`jobs/registry.py:84-107`）四项：`data_sync` / `factor_compute` / `factor_ic` /（C4 的）`backtest`，无模型训练 |
| 前端 | `web/src/pages/models/` 不存在，`web/src/api/models.ts` 不存在，全仓前端无任何模型命中 |

因此本变更与 C4 同构：**先把模型评估结果落库，再建页面**。不落库的话，评估页每次打开都得重跑一次 walk-forward（8 年窗口 + 全池取数，分钟级），预测页也没有任何东西可读——`train_model` 今天连日志都只在 `ctx.log` 里，脚本调用者是唯一的受众。

两个约束先摆明：

1. **特征重要性的窗口聚合已在 C1 完成**（`service-layer` spec 的「模型训练输出特征重要性」已是既有需求）。本变更只做持久化与展示，不重新实现聚合。
2. **落库的主键只能来自 `ctx.run_id`** —— 服务函数不接收 run 标识，`NULL_CONTEXT.run_id` 是空串（`services/context.py:97`），脚本与测试默认走这条路。这条约束的后果与 C4 完全相同。

预测分这条通路上还有第三个约束：`ModelStrategy`（`strategies/model_strategy.py:26-43`）**只从文件路径读预测**，`strategies/factory.py:26-28` 把 `config.strategy.params["predictions_path"]` 写进策略实例。任何让预测分改走表的设计，都要同时改策略的读路径——那是策略域的事，不在本变更范围内。

## Goals / Non-Goals

**Goals**

- 模型评估结果持久化：逐日 RankIC 序列、特征重要性（因子 / 均值 / 标准差）、训练汇总指标
- 训练任务接入统一运行 API（`kind: model_train`），复用进度上报、实时日志、取消、产物登记
- 训练参数对象补齐 walk-forward 窗口与 LightGBM 超参数，使一次训练可由 `params_json` 完整复现
- 三个页面：提交与历史列表、评估详情（RankIC 曲线 / 汇总指标 / 分年度表现 / 特征重要性 top-N）、预测（指定日期的 top picks 与得分分布）
- 评估页与预测页**不重跑训练**：所有数字来自结果表与已落盘的预测文件

**Non-Goals**

- 不保存 LightGBM booster 本体（见 D1）
- 不做模型组合的收益回测（分年度表现的口径见 D11）
- 不做多 run 对比页（评估页是单 run 的；对比的通用形态已在 C4 由回测对比页承担）
- 不做超参数搜索 / 自动调参 / 实验追踪
- 不改动 `rank_ic_series` 的签名与返回结构（见 D7）
- 不改动 `ModelStrategy` 的预测读路径，不把预测分搬进表（见 D3）
- 不做历史 run 的预测分回溯查看（见 D3）

## Decisions

### D1: 三张结果表，booster 不落盘

```sql
CREATE TABLE IF NOT EXISTS model_ic_series (
    run_id     VARCHAR,
    trade_date DATE,
    rank_ic    DOUBLE,
    PRIMARY KEY (run_id, trade_date)
);

CREATE TABLE IF NOT EXISTS model_feature_importance (
    run_id     VARCHAR,
    factor     VARCHAR,
    importance DOUBLE,
    std        DOUBLE,
    PRIMARY KEY (run_id, factor)
);

CREATE TABLE IF NOT EXISTS model_metric (
    run_id       VARCHAR,
    metric_name  VARCHAR,
    metric_value DOUBLE,
    PRIMARY KEY (run_id, metric_name)
);
```

`model_ic_series` 与因子域的 `ic_series`（`factor_values` 旁的 `(factor_name, trade_date, forward_period)`）**必须是两张表**。把模型的逐日 IC 塞进 `ic_series` 只能靠 `factor_name = "model:<run_id>"` 这类把实体编进键名的写法——那正是 C4 决策 D1 拒绝过的形状，页面上要反解字符串才能按 run 过滤。

`model_metric` 沿用 C4 的键值对形状（`run_id, metric_name, metric_value`）：本期写入 `ic_mean` / `ic_ir` / `ic_positive_ratio` / `ic_days`（序列长度）/ `prediction_rows` / `windows_trained` 六项，加一项就只改服务函数，不改表结构。

**booster 不落盘。** `LGBMRegressor` 是窗口循环里的局部变量，一个 8 年区间会训练约 32 个窗口——「模型」在这条链路里从来不是单个对象，而是一串模型加一串预测。落盘 32 个 booster 需要一张新的模型文件表 + 加载路径 + 版本语义，而本变更的三个页面**没有一个需要它**：评估页要的是 IC 与重要性（已落库），预测页要的是预测分（已落 parquet），训练的下游消费者是 `ModelStrategy`，它读的也是预测分而不是模型。要支持「加载某个历史模型再预测」，那是另一个变更，届时表结构新增加即可。

### D2: 训练元信息不单独成表，实际区间从 IC 序列推导

训练区间、窗口数、股票池、超参数都不再落一张 `model_run` 表：

| 展示项 | 来源 |
|---|---|
| 请求参数 / LightGBM 超参数 | `run.params_json`（列表接口返回时解析） |
| 实际覆盖的预测区间 | `model_ic_series` 的 `MIN(trade_date)` / `MAX(trade_date)` |
| 完成窗口数 / 预测行数 | `model_metric` 两行 |
| 是否取消 | `run.status == 'cancelled'` |
| 提交时间 | `run.created_at` |
| 运行中的进度 | `run.progress` / `run.message` |

**运行中的行只有进度是活的**（实现期补定）：其余各项——完成窗口数、预测行数、IC 指标、覆盖区间——都在训练结束时才写入，所以一条 `running` 的行在结果列上必然全空。列表因此把 `progress` 与 `message` 一并带出，页面在未终态的行上用进度条替代那几个空列；不这么做，`model-research-ui` 的「进行中的运行 → 该行展示进度」就只能靠状态标签暗示。

**理由**：与 C4 决策 D2 同——请求区间与实际覆盖区间不是一回事。`train_model` 把 `start=None` 解析成 `DEFAULT_HISTORY_START`（`services/data.py:27`），把 `end=None` 解析成最新交易日（`services/models.py:94-97`），实际发出预测的日期又是 `_signal_dates` 按 `predict_months` 抽样后的结果（`train.py:203`）。把请求参数抄进结果表就是第二份可能过期的真相；真正的事实是逐日 IC 覆盖了哪些日期。

**代价**：一次「训练完成但预测为空」的运行（`train_model` 在 `services/models.py:103-112` 提前返回）在 `model_ic_series` 里没有行，列表页的实际区间显示为空。这是诚实的——那次运行确实没有产出可评估的预测。

### D3: 预测分继续走 parquet，不落表，也不做按 run 回溯

`save_predictions`（`train.py:171`）继续写 `params.output_path`（默认 `data/predictions/model_ranking.parquet`），预测页继续按**路径**读（`predict_for_date`，`services/models.py:137`），不新增 `model_prediction` 表。

**理由（规模）**：一次 8 年、3 个月窗口的训练覆盖约 32 个窗口 × 约 63 个预测日 × 约 500 只股票 ≈ **百万行**，是 C4 净值表（每 run 约 2500 行）的数百倍。为一页「看某天得分分布」的展示付这个存储与写入成本，收益不成比例。

**理由（消费者）**：预测分的下游只有两个——`ModelStrategy`（读文件路径，`strategies/model_strategy.py:39-43`）与预测页。两者要的都是「最近一次训练写下的那一份」，没有任何消费者要「run A 当初的预测」。

**代价（明写在页面上）**：再次训练会覆盖同一路径，历史 run 的预测不复存在——而历史 run 的 IC 与重要性还在。预测页因此 SHALL 标注它展示的是**最近一次成功训练**的预测，并给出该次训练的 `run_id` 与完成时间；SHALL NOT 提供「按 run 查看预测」的入口。宁可页面说清它给的是哪一份，也不要让一个 run 选择器把别的 run 的预测冒充成本 run 的产物。

**替代方案（未采纳）**：预测分按 run 追加副本（`{output_dir}/{run_id}.parquet`）。否决原因：那是同一份百万行数据的第二份拷贝，而它唯一的用途是回看历史预测——这个用途没有任何消费者提出。

### D4: 落库以 `ctx.run_id` 为键，`run_id` 为空时不落库

`train_model` 在 `ctx.run_id` 非空时写入三张表，为空时按原样返回内存结果、不写库、不报错，并在 docstring 中写明该行为。

理由与代价同 C4 决策 D3：`NULL_CONTEXT` 是脚本与单测的默认上下文（`service-layer` spec 的「无接收器的默认上下文」要求这条路径必须正常完成），而 `run_id = ''` 的行会被第二次脚本调用以相同主键静默覆盖，看起来「成功」而数据已经串了。写不进去也比写错好。

**附带**：C1 之后 `scripts/smoke_ml_pipeline.py:66` 仍在按旧签名解包 `preds, _ = walk_forward_train(...)`，该脚本已经跑不通。本变更顺手把它修到 `WalkForwardResult` 用法上——不是因为它阻塞了什么，而是它就在这条改动路径上，留着会误导下一个照着它调用服务的人。

### D5: 注册 `kind: model_train`，零新增写路由，产物登记三条

`JOBS` 增加一项：

| kind | params_model | service_fn | artifacts |
|---|---|---|---|
| `model_train` | `TrainParams` | `train_model` | `model_ic_series`（行数）+ `model_feature_importance`（行数）+ 预测 parquet（行数） |

`POST /api/runs` 的通路、参数校验、SSE 日志、取消、产物登记全部复用（`api/runs.py`）。前端提交走 `runsApi.submit('model_train', {...})`。

**产物三条**：逐日 IC、因子重要性、预测分回答的是三个不同的问题（「模型什么时候准」「模型靠什么」「模型今天选了什么」），互不为对方的函数。**汇总标量（`model_metric`）不单独登记**：它是前三者的派生摘要，登记它只是让产物列表多一行重复信息。

`ArtifactDraft` 的 `meta` 记 `windows_trained` / `start` / `end`，使产物列表不必反解 `params_json` 就能看出这是哪次训练。预测 parquet 的 `storage` 用 `ArtifactStorage.PARQUET`（`runs/models.py:54-62` 已有该取值，C6 的报告文件是它的另一个使用者），`ref` 为输出路径。

### D6: `TrainParams` 扩展为可完整复现的训练配置，但不做任意 kwargs 透传

`TrainParams` 新增（全部可选，`None` 表示用默认值）：

| 字段 | 约束 | 落到 |
|---|---|---|
| `train_years` | `gt=0` | `TrainConfig.train_years` |
| `valid_years` | `gt=0` | `TrainConfig.valid_years` |
| `predict_months` | `ge=1` | `TrainConfig.predict_months` |
| `early_stopping` | `ge=1` | `TrainConfig.early_stopping` |
| `num_boost_round` | `ge=1` | `TrainConfig.num_boost_round` |
| `lgb_params` | 嵌套模型 `LightGBMParams \| None` | 合并进 `TrainConfig.params` |

`LightGBMParams` 是显式字段的 pydantic 模型，覆盖 `DEFAULT_PARAMS`（`train.py:18-29`）里对模型质量真正敏感的几项：`num_leaves` / `min_data_in_leaf` / `learning_rate` / `feature_fraction` / `bagging_fraction` / `bagging_freq`，各自带上界。`train_model` 用 `lgb_params.model_dump(exclude_none=True)` 覆盖默认字典。

**为什么不透传 `dict[str, Any]`**：那是第 3 方（前端）到 `lgb.LGBMRegressor(**params)` 的任意关键字写路径，绕开 `ServiceParams` 的 `extra="forbid"` 校验，可以覆盖 `objective` / `metric` / `num_threads` 这类改变语义或影响并发行为的项，且 `params_json` 里躺着什么完全不受约束。C4 决策 D10 拒绝 `strategy_params: dict[str, Any]` 是同一条理由：要支持任意覆盖，正确做法是为每类参数定义模型，而不是开一个口子。未暴露的项一律留在默认值上。

**替代方案（未采纳）**：把所有 `DEFAULT_PARAMS` 键都做成字段。否决原因：`objective` / `metric` / `verbosity` / `num_threads` 不是研究变量（前两个是链路契约——label 是回归目标、评估口径是 RankIC；后两个是运行环境），暴露它们等于允许用户把评估页的口径调歪。

### D7: 读取路径独立成 `services/model_query.py`，`rank_ic_series` 签名不动

新增只读服务（均 `fn(params, ctx=RunContext)`）：

| 函数 | 说明 |
|---|---|
| `model_run_list` | 训练运行列表：run 状态 + 请求参数摘要 + 实际覆盖区间 + 关键指标，服务端分页 |
| `model_evaluation` | 单个 run 的汇总指标 + 逐日 RankIC 序列 + 分年度聚合表现 + 特征重要性（含 top-N 截断参数） |

**预测的读取路径不新增函数**：`predict_for_date`（`services/models.py:137`）已经在做「按日期取预测分并给出 top-N」，预测页要的只是同一份数据里更多的行（D7 下文的 `scores`）。再写一个 `prediction_detail` 就是同一逻辑的第二份实现，两份口径迟早分叉。

SQL 放在 `models/persistence.py`（对应 C3 的 `factors/ic_store.py`），服务层只做编排；理由同 C4 决策 D6——聚合写在 `api/models.py` 里就是「适配器含领域逻辑」，违反 `service-layer` spec。

**`rank_ic_series` 不改**：它的返回是 `dict[str, object]`，其中 `ic_series` 是 `list[tuple[date, float]]`（`evaluate.py:44,55,62`），已被 `tests/test_ml_training.py` 的 `TestRankIC` 锁定形状。本变更消费它、不重塑它：`train_model` 把 `ic_series` 转成 DataFrame 直接落库。逐日样本数（每次 spearman 用了多少只股票）不在返回值里——为了一个展示上非必需的列去改一个已锁定的契约不划算，真要时再加列（additive，不影响既有行）。

**`PredictResult` 扩展**：新增 `scores: list[PredictionRow]`（该日全部得分，按分数降序）与 `top_n: int`，`picks` 改为从 `scores` 派生的属性（`scores[:top_n]`）。预测页要用全部得分布直方图，而 `picks` 今天只是它的前 N 条——同一份数据的两个字段不如一个有语义的派生。

### D8: `api/models.py` 只读路由，路径顺序约束前置

`create_models_router(config) -> APIRouter`，前缀 `/api/models`：

```
GET /api/models/runs                    训练运行列表（分页）
GET /api/models/runs/{run_id}           单 run 评估：指标 / IC 序列 / 分年表现 / 重要性
GET /api/models/predictions             指定日期的预测（?as_of=&top_n=）
```

写路径（发起训练）一律走 `POST /api/runs`，本路由不含 POST。三段的静态段都在参数段之前，**没有 C4 那种 `/compare` 被 `/{run_id}` 吃掉的陷阱**；但仍要在测试里断言 `/api/models/predictions` 不被 `/api/models/runs/{run_id}` 捕获（它们是不同的第二段，本就不可能撞，断言的作用是把这条性质固定下来，防止将来有人加一个 `/runs/{run_id}/{...}` 通配路由）。

请求上下文用与 `api/factors.py:249-261` 同款的 `_context`（每请求自开 `DataStore`，**默认配置而非只读**——DuckDB 同进程内只读与读写混用会抛 `ConnectionException`）。路由在 `runtime/app.py` 的 `include_router` 段注册（`app.py:84-88`），位置在 SPA 回退之前。

因子选择下拉复用既有 `GET /api/factors`（`api/factors.py:46`），不新增「模型可用因子」路由——特征就是 `factor_values` 里已落库的那批，没有第二套真相。

**`GET /api/models/predictions` 不接受路径参数**（实现期补定）：预测文件由服务端从 `artifact` 表反查——`get_latest_prediction_source`（`models/persistence.py:302`）取「最近一次成功训练的 parquet 产物」的 `ref`，再交给 `predict_for_date`。若照 D3 的字面把 `predictions_path` 开成查询参数，这个端点就成了一条任意本地 parquet 读取路径；而「该展示哪一份预测」本来就有唯一答案，`run` / `artifact` 里已经记着。同时 `PredictResult` 新增 `available_dates`（该文件覆盖的全部预测日），预测页的日期选择器由它限定，而不是让前端猜哪些日期有数据——这回答了 Open Questions 里那条倾向「从预测文件读出可用日期集合」的问题。

### D9: 取消的训练保留已算出的结果；修正取消时仍上报 100% 进度

`walk_forward_train` 在窗口循环头部检查取消（`train.py:92`），此时**已训练窗口的预测已经累积在 `all_preds` 里**（`train.py:141`），返回的 `WalkForwardResult.cancelled` 为 `True` 而 `predictions` 非空。这份数据是真实跑出来的，落库；IC 序列与重要性同样落库。

同时修正 `services/models.py:118`：`ctx.progress(1.0, ...)` 当前在持久化之后**无条件**执行，取消时也把进度推满。这与 C3 在 `compute_alpha158`（任务 11.9）、C4 在 `run_backtest_service` 上发现并修掉的是同一类缺陷，后果一样（取消的任务显示为已完成），修法也一样：仅在未取消时执行。

**为什么在本变更内做**：注册成任务类型之前，进度只有脚本在读；注册之后它就是界面上的承诺。

### D10: 前端三页 + 分区内 `Tabs`，零新增图表注册

`web/src/pages/models/` 下三个页面 + `ModelNav.tsx`（照 `pages/factors/FactorNav.tsx` 的 `Tabs` + `activeKey={location.pathname}`），`navigation.tsx:34` 的 `implemented` 置 `true`，`routes.tsx` 补三条显式路由：

| 路径 | 页面 |
|---|---|
| `/models` | 训练提交表单 + 历史运行列表 |
| `/models/evaluate/:runId` | 评估：RankIC 时序折线、汇总指标卡、分年度表现柱状图、特征重要性 top-N 横向条形图 |
| `/models/predict` | 预测：日期选择 + top picks 表 + 得分分布直方图 |

第二条路由用 `/evaluate/:runId` 而非 `/:runId`：与 `/predict` 的静态第二段并列，不产生「静态段 vs 参数段同层」的歧义——C4 的 `/backtest/compare` 与 `/backtest/:runId` 必须靠声明顺序才不撞，这里不需要那条隐式约束。

**图表零新增注册**：`charts/echarts.ts` 已注册 Line / Bar / Heatmap 与 `DataZoomComponent` 等（`echarts.ts:7-30`）。RankIC 折线用 Line，分年度与得分分布用 Bar，特征重要性横向条形用 Bar（`yAxis` 为类目轴），缩放已具备。本变更不引入新的图表类型，也不引入新的图表库。

客户端封装新增 `web/src/api/models.ts`，走 `http.ts` 的 `request<T>`（`http.ts:41`）；数据获取沿用既有手写模式（`useState` + `useEffect` + `inFlight` ref），不引入数据请求库。样式全部来自 antd 组件与 `shell/theme.ts` token，不新增任何 `.css` / `.less` / `.scss`。

**实现期注意（C3 实测踩到，务必照做）**：图表的共享封装必须从 `echarts-for-react/esm/core` 引入（`charts/EChart.tsx:5` 已就位并有注释说明）。`lib/` 是 CJS，深层路径导入会跳过打包器的 interop，React 收到模块命名空间对象而非组件，页面直接抛 React error #130 且 TypeScript 与构建都不报错。沿用既有 `EChart.tsx`，不重复这个坑。

### D11: 「分年度表现」= 分年度的 IC 汇总，不是组合收益

评估页的分年度表现定义为：把已持久化的逐日 RankIC 序列按自然年分组，每年给出「IC 均值 / IC_IR / 正 IC 占比 / 有效天数」。这些全部可从 `model_ic_series` 一次查询算出，服务端返回，页面只画。

**为什么不做成组合收益**：模型选股的年化收益属于**策略与回测域**——`ModelStrategy` 已经存在（`strategies/model_strategy.py`），回测引擎已经能对它跑净值（C4 的 `backtest_*` 表）。在模型评估页再实现一遍「按预测分选前 N 只、持有 T+2、算收益」，就是同一件事的第二份实现，两份口径迟早会分叉，而页面上会同时出现两个「年化」数字。

**代价**：评估页回答的是「模型准不准」，不是「按它做能赚多少」。这个边界写进页面文案，用户要收益就去回测域跑一次 `model_ranking` 策略——那条通路本变更是通的（`kind: model_train` 跑完，回测页用它写下的预测文件）。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| `model_ic_series` 行数随预测日线性增长（8 年约 2000 行/run） | 与 C4 净值表同阶；DuckDB 列存点查单 run 是毫秒级 |
| `model_feature_importance` 每 run 158 行（Alpha158 全量） | 页面上要的是 top-N，服务端按 `importance DESC` 截断；全量行留给「导出/更多」 |
| 统计量里 `nan` 落库后读回是 `NULL`，页面要处理空洞 | `rank_ic_series` 在无有效日期时返回 `float("nan")`（`evaluate.py:58`），服务层沿用既有 `_as_float`（`services/models.py:172`）把 NaN 归为「无值」，不写 NaN 行 |
| 逐日 IC 的样本数未落库，图上无法标注「这天只有 12 只股票参与」 | D7：不改已锁定的 `rank_ic_series` 契约；该展示需求出现时加列（additive） |
| 两次训练覆盖同一份预测文件，历史 run 的预测不可回看 | D3：预测页明写来源 run 与完成时间，不提供按 run 查看预测的入口 |
| `ctx.run_id` 为空时静默不落库，脚本调用者可能误判 | D4：docstring 明写；任务中要求测试覆盖「空 run_id 不写库且正常返回」 |
| 百万行级别的预测分若将来要落表，迁移成本高 | D1/D3 的选择把它挡在表外；真要落表时那是一个独立变更，届时按 run 分片与保留策略一并设计，而不是现在顺手塞进 `SCHEMA_SQL` |
| `model_metric` 读 `run` 表跨越了 runs 域 | 与 C4 决策 D6 同款约束：只读、且仅用于列表视图；写入 `run` 仍只属 `RunStore`。任务中要求测试断言不产生 `run` 行 |
| 三张新表让「数据总览」页多出三行空表 | 三张表都注册进 `TABLE_NAMES`；`model_ic_series` 注册 `trade_date` 进 `TABLE_DATE_COLUMNS`，`model_feature_importance` / `model_metric` 无日期列则不注册（缺省即无边界）。无数据时 `table_stats` 自然返回 0 行 |
| 训练超参数被调成极端值（如 `train_years` 大于可用历史）导致窗口全空 | `walk_forward_train` 对空窗口已有警告与跳过（`train.py:105-113`）；服务层返回的 `windows_trained = 0` 与空预测在页面上呈现为「无产出」，不伪装成成功 |

## Migration Plan

全量 additive，无破坏性变更：

1. `data/schema.py` 的 `SCHEMA_SQL` 追加三张 `CREATE TABLE IF NOT EXISTS`（仓库无 schema 版本机制，也无 `ALTER TABLE` 用法；新表只能追加）。同时把三张表加入 `data/store.py` 的 `TABLE_NAMES`，`model_ic_series` 加入 `TABLE_DATE_COLUMNS`，使 `/api/data/status` 与数据总览页自动纳入。
2. 新增 `models/persistence.py`、`services/model_query.py`、`api/models.py` 三个模块，均为新增。
3. `services/models.py`：`TrainParams` / `TrainResult` / `PredictResult` **新增**字段，既有字段语义不变；`train_model` 增加落库分支与进度修正。`TrainResult` 新增 `ic_series`（逐日）与 `ic_days` 供调用方直读，既有 `ic_mean` / `ic_ir` / `ic_positive_ratio` 保持不变。
4. `JOBS` 新增一项，既有四项不动；`POST /api/runs` 路由零改动。
5. 前端新增 `pages/models/` 与 `api/models.ts`，`navigation.tsx` / `routes.tsx` 各改一处（图表注册不需要改）。
6. `scripts/smoke_ml_pipeline.py` 修到当前签名（D4）。
7. 回滚：删除新模块与 `JOBS` 一行即回到当前状态；三张表留在库中不影响其他表（可手工 `DROP`）。

## Open Questions

- 列表页默认展示多少条？倾向 20 条一页，与 `RunStore.DEFAULT_PAGE_SIZE` 对齐，待实现时确认。
- 特征重要性默认 top-N 取多少？倾向 20（横向条形图超过 20 行就不可读了）；全量行与 top-N 的切换是否要做成页面上的「显示更多」，倾向不做，导出留给将来的产物下载。
- 得分分布直方图的分箱数固定还是自适应？倾向按当日得分分位数固定 20 箱，避免不同日期/不同 run 的直方图不可比。
- 预测页的日期选择器是否要限定在「有预测的日期」范围内？倾向限定（从预测文件读出可用日期集合），把「这天没有预测」变成不可选项，而不是提交后弹一个空状态。
- **本变更范围外但已发现**：`strategies/model_strategy.py:26` 把预测路径默认值又硬编码了一遍（`"data/predictions/model_ranking.parquet"`），与 `services/models.py:22` 的 `DEFAULT_PREDICTIONS_PATH` 是两份字面量。改任一处都会让策略与预测页读到不同文件，但收敛它要动策略构造签名，留待策略域的下一次变更。
