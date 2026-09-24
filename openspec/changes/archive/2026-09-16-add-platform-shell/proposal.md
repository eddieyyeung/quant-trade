> **状态：排期中。** 依赖 `refactor-extract-service-layer`（C1）完成后再补全 design / specs / tasks。

## Why

C1 剥离出服务层后，领域函数已可被 HTTP 调用，但**没有执行记录，也没有异步执行能力**。`data sync` 全 A 股是小时级任务，同步 HTTP 请求扛不住；且当前全项目没有任何 job / run / task 概念（grep 零命中），参数与结果都不可追溯。

本变更建立平台的执行基础设施与第一个完整垂直切片（数据域），使「点按钮 → 后台跑 → 看进度 → 查结果」这条链路端到端可用。后续 C3–C6 复用同一套基础设施，只填页面。

## What Changes

- 新增三张 DuckDB 表：`run` / `run_log` / `artifact`
  - `run` 含 `run_id` / `kind` / `params_json` / `status` / `progress` / `started_at` / `finished_at` / `error` / `artifact_dir`
  - **预留字段（本期写入，暂不使用）**：`trigger`（`manual` / `scheduled` / `api`）、`idempotency_key`
  - `artifact` 为索引表，`storage` 字段区分三类后端：结构化专用表 / Parquet 文件 / HTML 文件
- 新增单 worker 串行任务队列
  - 并发度固定为 1 —— DuckDB 单文件只有一个写者，且研究流程本质串行，串行同时消灭锁竞争
  - 启动时将遗留的 `running` 状态修正为 `interrupted`
  - 取消通过 `RunContext.cancelled()` 协作式传递
- 新增统一运行 API：`POST /api/runs` / `GET /api/runs` / `GET /api/runs/{id}` / `GET /api/runs/{id}/logs`（SSE）/ `POST /api/runs/{id}/cancel` / `GET /api/runs/{id}/artifacts`
  - 加新任务类型 = 注册表加一行，前端不加路由
- 新增任务中心页与数据域页（Ant Design）
- 新增前端工程基础：`antd` + `@ant-design/icons` + `echarts` + `echarts-for-react` + `react-router-dom` 落入 `web/package.json`
  - 清理误挂在根 `package.json` 的 `react-router-dom` 与 `recharts`
- 实现 `run` 与 `RunContext` 的对接：进度与日志写入 `run_log` 并推送到 SSE 订阅者

## Capabilities

### New Capabilities

- `run-registry`: 运行记录、日志、产物的持久化模型与生命周期（pending → running → ok / failed / cancelled / interrupted）
- `job-runner`: 单 worker 串行队列——入队、执行、进度上报、协作式取消、重启恢复
- `run-api`: 统一运行 API 与 SSE 日志流
- `research-ui-shell`: Ant Design 平台外壳（布局、导航、路由、主题）
- `data-console`: 数据域页面——数据总览、同步任务触发、股票池、交易日历

### Modified Capabilities

- `service-layer`: 服务函数的 `progress()` / `log()` 接收器接入 `run_log` 持久化与 SSE 推送
- `platform-runtime`: 挂载 run API 路由

## Impact

**新增**
- `src/quant_trade/runs/`（模型、存储、生命周期）
- `src/quant_trade/jobs/`（队列、worker、sink 实现）
- `src/quant_trade/api/runs.py`（路由）
- `web/src/shell/`（Layout / Menu / 路由）、`web/src/pages/jobs/`、`web/src/pages/data/`

**修改**
- `src/quant_trade/data/schema.py`：新增三张表
- `src/quant_trade/runtime/app.py`：挂载 run 路由
- `web/package.json`、`web/vite.config.ts`

**新增依赖**
- 后端：无（FastAPI + uvicorn 已有）
- 前端：`antd`、`@ant-design/icons`、`echarts`、`echarts-for-react`、`react-router-dom`

**验证锚点**：点「同步」按钮 → run 记录产生 → 进度条动 → 日志实时追加 → 可取消 → 完成后 run 状态为 `ok`

## 开工说明

前置依赖 `refactor-extract-service-layer` 已归档。本变更 artifacts 齐全，可直接 `/opsx:apply add-platform-shell`。

**从第 1 节开始**（运行记录数据模型）。tasks 的 1–4 节是纯后端，全程可用 HTTP 验证，不依赖前端；到第 4 节结束平台就已具备「提交任务 → 后台执行 → 实时日志 → 取消」的完整链路。

### 已归档变更留下的、本变更必须知道的事实

1. **DuckDB 连接必须同配置**。C1 期间实测：同进程内 `read_only` + `read_write` 指向同一文件会抛
   `ConnectionException: Can't open a connection to same database file with a different configuration`。
   读路径**不能**用只读连接，一律使用默认配置，并发靠 MVCC（实测写入期间另一连接完成 912 次读取、零阻塞）。详见 design 决策 2。

2. **服务层已就位，直接调，不要重新实现**。`quant_trade.services` 提供 `sync_market_data` / `data_status` /
   `universe_coverage` 等，签名为 `fn(params, ctx) -> Result`。`RunContext` 的 `ProgressSink` / `LogSink`
   协议已定义，C2 只需提供实现（design 决策 7）。

3. **`AppConfig.backtest.start_date` 的语义已修正**。它现在是「回测起始日」，落在非交易日会被归一化到其后第一个交易日
   （`backtest/engine.py:57`）。不要假设它必须恰好是交易日。

### 前序变更遗留的已知债（不阻塞本变更）

- `openspec/specs/kline-universe-fallback/spec.md` 中有一条 scenario 名为「CLI data sync includes indices」，
  CLI 已不存在，名字过时（delta 格式无 scenario 级 rename，改名需独立变更）。
- `signals/reporter.py` 的非周五判据只检查「今天」，未处理「最近交易日非周五」；`date.today()` 不可注入。
  已记入 C6。

### 验证口径

第 9 节含 8 项验证，其中 4 项是端到端（界面点同步 → 看进度 → 看日志 → 取消 / 刷新保活 / 重启恢复 / 排队）。
后端部分建议先写 `tests/test_jobs_runner.py` 覆盖串行与取消，再做前端。

