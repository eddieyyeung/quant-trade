## 1. 信号表与持久化模块

- [x] 1.1 `data/schema.py` 的 `SCHEMA_SQL` 追加 `strategy_signal(run_id, seq, trade_date, strategy, ts_code, direction, target_pct, reason)`，主键 `(run_id, seq)`；注释说明它与 `backtest_trade` 同构的理由（都是某次运行的一串有序交易意图）
- [x] 1.2 把 `strategy_signal` 加入 `data/store.py` 的 `TABLE_NAMES`，并加入 `TABLE_DATE_COLUMNS`（日期列为 `trade_date`），使数据总览显示行数与日期跨度
- [x] 1.3 新建 `src/quant_trade/strategies/signal_store.py`（照 `backtest/result_store.py` 的写法）：模块 docstring 写明「策略包不得 import services」，定义 `SIGNAL_COLUMNS` 列序常量与 `STRATEGY_SIGNAL_KIND = "strategy_signals"`
- [x] 1.4 实现 `build_signal_frame(orders) -> pd.DataFrame`：把订单 dict 列表转成 `SIGNAL_COLUMNS` 列序的帧，`seq` 从 1 连续编号；空输入返回空帧
- [x] 1.5 实现 `save_strategy_signals(store, run_id, frame) -> int`：走 `store.conn.register` 批量插入；空帧跳过并返回 0

> **实现期偏离**：改用「先 `DELETE FROM strategy_signal WHERE run_id = ?`、再 `INSERT`」，而非计划里的 `INSERT OR REPLACE`。测试当场抓到：`OR REPLACE` 只替换主键 `(run_id, seq)` 已存在的行，用**更少**的订单重写同一运行会留下上一次的尾巴——写 3 笔再写 2 笔，表里仍是 3 行，与 spec 的「重复写入同一运行幂等」直接矛盾。`backtest/result_store.py` 的 `_upsert` 有同样的形状，但那里每次运行只写一次，所以没暴露。
- [x] 1.6 实现读取函数 `get_strategy_signals(store, run_id, limit, offset)`（按 `seq` 升序，服务端分页）、`count_strategy_signals(store, run_id)`、`list_strategy_runs(store, limit, offset)`（JOIN `run` 只读，聚合 `MIN(trade_date)` / `MAX(strategy)` / `COUNT(*)`，**左外连接**产物 meta 补齐 `universe_size`，按 `COALESCE(finished_at, created_at) DESC` 排序并返回总数）
- [x] 1.7 新建 `tests/test_strategy_signal_store.py`：`init_db` 后表存在、重复初始化幂等、写入后读回逐值相等、`seq` 连续、同 `run_id` 重写幂等、空输入不写行、顺序按 `seq` 保留、`table_stats` 对空表返回 0 行且日期边界为无、`list_strategy_runs` 不产生 `run` 行（只读断言）

## 2. 落库入口与任务类型

- [x] 2.1 `services/strategies.py` 新增 `run_strategy_signals(params, ctx) -> SignalSummary`：调 `generate_strategy_signals` 取结果，`ctx.run_id` 非空且订单非空时用 `build_signal_frame` + `save_strategy_signals` 落库；为空时不写库、正常返回，docstring 写明该行为
- [x] 2.2 **确认 `generate_strategy_signals` 的签名与函数体一行未动**——周报管线依赖它，落库不得混进去（design D3）。用 `git diff` 核对该函数无改动
- [x] 2.3 `jobs/registry.py` 注册 `strategy_signals` → `SignalParams` / `run_strategy_signals`；新增产物映射 `_strategy_signal_artifacts(result)`：订单为空返回 `[]`，否则一条 `ArtifactDraft(kind="table", storage=TABLE, ref="strategy_signal", row_count=len(orders), meta={"strategy", "signal_date", "universe_size"})`
- [x] 2.4 在 `services/__init__.py` 的 `TYPE_CHECKING` 块、`__all__`、`_MODULE_BY_NAME` 三处登记 `run_strategy_signals`
- [x] 2.5 在 `tests/test_jobs_runner.py` 补 `TestStrategySignalJob`：kind 已注册且 `service_fn is run_strategy_signals`、产物 `ref` 为 `strategy_signal` 且 `meta` 三键齐备、无订单时产物为 `[]`、既有六个 kind 不受影响
- [x] 2.6 在 `tests/test_services_strategies.py` 补：`run_strategy_signals` 在非空 `run_id` 下落库且行数等于订单数；`NULL_CONTEXT` 下不写库；空订单不写行
- [x] 2.7 在 `tests/test_services_report.py` 或 `tests/test_services_strategies.py` 补一条回归断言：**生成一次周报后 `strategy_signal` 行数不变**（design D3 的核心风险）

## 3. 读取服务

- [x] 3.1 新建 `src/quant_trade/services/strategy_query.py`：`StrategyRunListParams`（`limit` / `offset`，带边界，照 `model_query.py`）与 `StrategySignalQueryParams`（`run_id`、`limit`、`offset`）
- [x] 3.2 定义结果 dataclass：`StrategyRunSummary`（`run_id` / `status` / `strategy` / `signal_date` / `order_count` / `universe_size` / `progress` / `created_at` / `finished_at`）、`StrategyRunListResult`（含 `total`）、`StrategySignalRow`（`seq` / `ts_code` / `direction` / `target_pct` / `reason`）、`StrategySignalResult`（`run_id` / `found` / `strategy` / `signal_date` / `universe_size` / `orders` / `total`）
- [x] 3.3 实现 `strategy_run_list(params, ctx)`：读 `list_strategy_runs`；尚未产出结果的运行同样在列，其 `signal_date` / `order_count` / `universe_size` 为无（**不是 0**）
- [x] 3.4 实现 `strategy_signals(params, ctx)`：读 `get_strategy_signals` + `count_strategy_signals`；该 `run_id` 在表中无行时以 `found=False` 表达（由 API 层转 404），**不得**返回空列表冒充存在
- [x] 3.5 在 `services/__init__.py` 的三处登记新增公开名称
- [x] 3.6 新建 `tests/test_services_strategy_query.py`：列表分页与倒序、未产出结果的运行在列且三字段为 None、单次信号分页与总数、不存在的 `run_id` 返回未找到、订单顺序与 `seq` 一致、参数 `model_dump_json` 往返

## 4. HTTP 路由

- [x] 4.1 新建 `src/quant_trade/api/strategies.py`：`create_strategies_router(config) -> APIRouter`，前缀 `/api/strategies`，只读不含 POST
- [x] 4.2 `GET /api/strategies` 返回 `{items: [...], default: "..."}`（照 `api/backtests.py` 的 strategies 端点形状，`default` 取 `config.strategy.name`）
- [x] 4.3 `GET /api/strategies/runs` 返回信号运行列表（服务端分页 + 总数）
- [x] 4.4 `GET /api/strategies/runs/{run_id}` 返回单次信号，未登记返回 404
- [x] 4.5 请求上下文用与 `api/models.py` / `api/reports.py` 同款的 `_context`（每请求自开 `DataStore`）
- [x] 4.6 在 `runtime/app.py` 的 `include_router` 段挂载策略路由，位置在 SPA 回退之前
- [x] 4.7 新建 `tests/test_api_strategies.py`：策略清单含 `factor_ranking` 与 `model_ranking`、运行列表分页、单次信号、404、`limit` 越界返回 422、`/api/strategies/*` 不打到 SPA 回退

## 5. 前端接口封装

- [x] 5.1 新建 `web/src/api/strategies.ts`：基于 `http.ts` 的 `request<T>`，导出 `strategiesApi`（`list` / `runs` / `signals`），类型与后端返回字段逐一对齐（snake_case）；`default` 与 `items` 逐字对应后端
- [x] 5.2 提交信号生成走既有的 `runsApi.submit('strategy_signals', {...})`，**不在本文件里另造一个提交函数**
- [x] 5.3 确认未新增任何 `.css` / `.less` / `.scss` 文件

## 6. 策略分区页面

- [x] 6.1 `web/src/shell/navigation.tsx` 把 `/strategies` 的 `implemented` 置 `true`；`web/src/shell/routes.tsx` 在 SECTIONS 占位循环之前补 `/strategies` 与 `/strategies/signals/:runId` 两条显式路由
- [x] 6.2 新建 `web/src/pages/strategies/StrategyNav.tsx`：照 `pages/backtest/BacktestNav.tsx` 的 `Tabs` + `activeKey={location.pathname}` 写法，两个标签（策略 / 信号）；**信号详情不作标签**（design D6）
- [x] 6.3 新建 `web/src/pages/strategies/List.tsx`：提交表单（策略 `Select` 选项来自后端、日期 `DatePicker`、股票池多选、持仓数 `InputNumber`）+ 历史运行表（状态、策略、信号日期、订单数、进度、操作）
- [x] 6.4 `List.tsx` 提交走 `runsApi.submit('strategy_signals', {...})`，成功后 `navigate('/jobs/' + run.run_id)`；**未填写的字段不出现在请求中**；股票池为空时**不发空数组**（发送前拦截）
- [x] 6.5 `List.tsx` 的列表按既有约定分三支且**失败分支在前**：失败 → `Alert type="error"` + 重试；`!loading && total === 0` → `Alert type="info"`「尚无信号生成」；否则渲染表格。非终态行用 `Progress`，终态展示占位符；非终态行存在时轮询并用 `inFlight` ref 防重入
- [x] 6.6 `List.tsx` 的是否提供详情入口以「该行有订单数」为准，**不以 `status === 'ok'` 为准**（上个变更验证期修过的同一个坑）
- [x] 6.7 新建 `web/src/pages/strategies/Signals.tsx`：摘要（策略名、信号日期、订单数）+ 调仓信号表（方向 `Tag`、代码、目标仓位百分比、理由），订单**按后端顺序展示不重排**
- [x] 6.8 `Signals.tsx` 的顶部标注运行状态与时间；取消的运行标注为「取消于」而非「完成于」；覆盖「无信号」、未找到、加载失败三个分支，各带返回策略页面的入口
- [x] 6.9 `Signals.tsx` 用 `useEffect(..., [runId])` 重置状态，避免 React Router 复用组件实例时上一个运行的信号留在新 URL 下
- [x] 6.10 确认信号详情页**不发起任何信号生成请求**（`runsApi` 不出现在该文件里）
- [x] 6.11 确认未新增任何 `.css` / `.less` / `.scss`，且本分区未引入 `echarts`

## 7. 前端契约测试

- [x] 7.1 在 `tests/test_ui_shell_contract.py` 补 `TestStrategiesSection`：`navigation.tsx` 的 `/strategies` 为 `implemented: true`、`routes.tsx` 含两条显式路由、`pages/strategies` 含 `StrategyNav` / `List` / `Signals`、无新增样式表、未引入 `echarts`
- [x] 7.2 补：`List.tsx` 以 `runsApi.submit('strategy_signals'` 提交、股票池为空时不发空数组（断言存在拦截分支）、详情入口不以 `status === 'ok'` 为准、错误分支在空态之前（`listFailure !== null` 先出现）
- [x] 7.3 补：`Signals.tsx` 不重排订单（不含 `.sort(`）、不重跑（`runsApi` 缺席）、含「取消于」条件分支与「无信号」空态
- [x] 7.4 补：`api/strategies.ts` 复用共享路径（含 `runsApi.submit` 或 `from './http'`），未自带二分基址

## 8. 验证

- [x] 8.1 `uv run pytest` 全绿
- [x] 8.2 `uv run ruff check` / `uv run ruff format --check` / `uv run mypy src` 通过
- [x] 8.3 `cd web && npm run build` 通过，`npm run lint`（oxlint）无错误
- [x] 8.4 端到端：在策略分区提交一次信号生成 → 跳转任务中心 → 进度与日志可见 → 完成后数据总览出现 `strategy_signal` 的行数与日期跨度
- [x] 8.5 端到端：任务详情出现一条指向 `strategy_signal` 的产物，`meta` 含策略名、信号日期与股票池规模
- [x] 8.6 端到端：回到策略分区 → 历史列表出现该行 → 进入详情 → 调仓信号表按引擎顺序展示，策略名与信号日期正确
- [x] 8.7 端到端：**日期留空**提交 → 详情页展示的是引擎实际使用的交易日（回退到最近开市日），不是提交时的空值
- [x] 8.8 端到端回归：生成一次周报 → `strategy_signal` 行数不变（design D3 的核心风险）
- [x] 8.9 端到端：`/strategies` 不再是占位页；直接刷新 `/strategies/signals/{run_id}` 不 404
- [x] 8.10 确认全仓无新增 `.css` / `.less` / `.scss`
- [x] 8.11 `openspec validate add-strategy-research-pages --strict` 通过

## 备注

- **本变更的核心风险是碰坏周报。** `generate_strategy_signals` 被周报管线调用，落库若混进它，每次生成周报都会往 `strategy_signal` 写一批以周报 run_id 为键、不对应任何信号生成任务的行。design D3 用独立入口隔离，任务 2.2 与 8.8 分别从代码与行为两侧锁住。
- **D1（落库）未经用户确认**：开工前问过「同步读还是走运行 / 落不落库」两个岔口，用户直接要求快进。design D1 记录了理由与被否的替代方案。要改回同步读，删掉 `strategy-signal-persistence` spec 与第 1–2 组任务即可，第 3–7 组的形状基本不变。
- **左外连接**：第 1.6 组的运行列表以 `run` 为主表、产物为可选补充。上个变更在这个形状上踩过一次坑（内连接让 `pending`/`running` 的运行整个消失），此处直接沿用修正后的形状。
- **「详情入口以订单数为准」**（6.6）是同一个坑的第二处：以 `status === 'ok'` 为准会让「产出结果后被取消」的运行没有入口。
- `SignalSummary.weights` 不落库（design D3）：没有任何读取路径需要它，落两份会让「组合应该长什么样」有两个可能不一致的答案。

## 实现期发现（不在本变更范围）

- **因子层无视注入的 store。** `FactorRegistry.get(name, store=None)` 把 store 默认成 `None`，于是 `cls(store=None)`，于是每个因子类走 `self.store = DataStore()` —— 而 `DataStore.__init__` 的默认值是**硬编码的** `"data/quant.db"`（`data/store.py:87`）。`FactorRankingStrategy.generate_signals` 调的是 `factor_registry.get(fname)`，**没有把它拿到的 store 传下去**（`strategies/factor_ranking.py:64`）。结果是策略用配置指定的库解析股票池、用仓库自带的库算价格因子——同一个进程里读两个库。生产环境里两者恰好是同一个文件，所以看不出来；把 `db_path` 指到别处就会出现「信号为空且没有任何解释」。
  这是既有缺陷，属于因子层，不在本变更范围内。本变更的 e2e 因此**不对真实运行的产出做断言**（它的数据来源不确定），只断言「产物登记与是否有订单一致」；读取链路用种子数据验证。
- `backtest/result_store.py` 的 `_upsert` 用 `INSERT OR REPLACE`，形状与 1.5 修掉的同一个坑。那里每次运行只写一次，所以尚未暴露；一旦出现「重写同一运行且行数变少」的路径就会留下尾巴。

## 验证期修正（`/opsx:verify` 发现并已修）

对抗式审计逐场景核对了 49 个场景，报出 1 个未覆盖、7 处「代码与 THEN 相悖」。以下已修，并各自补了或改了测试。

1. **「无信号」是死代码** —— `strategy_query.strategy_signals` 只要行数为 0 就返回 `found=False`，路由转 404，详情页因此永远走「不存在」分支，`Signals.tsx` 里的「无信号」渲染不出来。而 spec 的持久化侧要求「尚未产出结果的运行 → 未找到」，UI 侧要求「没有订单的运行 → 展示无信号」——两句都对，是我把两种情况塌缩成了一种。改为按运行是否**已终结**三分：未知 id → 未找到；已知但未终结且无行 → 未找到；已知且已终结且无行 → **找到**、订单为空。测试相应拆成两条。

2. **无订单的已终结运行没有入口** —— 详情入口原挂 `row.order_count`，与上一条叠加后「跑完但一只没选」的结论既到不了也看不到。改为挂 `isTerminal(row.status)`。

3. **空状态没有发起入口** —— spec 要求「空状态与提交入口」，实现只有一句说明文字。补了跳转链接。

4. **一条恒真的 e2e 断言** —— `check('rows still waiting have null counts, not zeros', X === false || true)` 永远为真，从未可能失败。已删除；该断言所在的场景另由 service/store 层覆盖。

5. **一条空转的 API 测试** —— `test_run_that_produced_nothing_is_404` 请求的 `run-pending` 是 fixture 从未插入的 id（重命名时漏改），实际在重复「未知运行」那条用例。改为断言已终结且无订单的运行返回 200 + 空列表，并在 fixture 里真的种一个 `run-empty`。

6. **任务 7.4 是假勾** —— 任务写的是「补 `api/strategies.ts` 的复用与字段对齐测试」，我打了勾却没写。已补 `TestFrontendAlignment` 四条（逐字段比对 API 响应键与 TS 类型），以及 `api/strategies.ts` 复用共享请求模块、且不自带写接口两条契约。

7. **spec 自身的三处问题** —— ①「策略清单页面」声称历史列表与之同屏，而「策略分区导航」说两者是两个页面；实现按后者，已把历史拆成独立的「信号历史页面」需求并补齐场景。②「区间倒置被拦截」是从回测 spec 抄来的场景名，本表单没有日期区间，已改名为「显式给出的空股票池被拦截」。③「请求中不包含该字段」与实现（发送显式 `null`）相悖，且服务端对 `null` 与缺字段一视同仁；已把措辞改为「以缺省值提交」。④「分区内切换」的理由「其路径与任何标签都不相等」是错的——详情路径确实以标签路径开头，代码正是因此用前缀匹配；已改写。

**未修**：审计另指出若干场景只有代码没有测试（`提交失败可见`、`分页与虚拟滚动`、`日期留空时展示的是实际信号日` 的端到端、`重跑不改变历史` 的经验性验证），以及 e2e 从不访问 `/data`（「表纳入数据总览」只测到 service 层）。这些是覆盖广度问题，不是行为错误。
