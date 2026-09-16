## Purpose

Simulator web: FastAPI web service for the interactive simulator — session CRUD, weekly step/skip decisions, comparison reports, development CORS, and frontend API conventions (backend on port 9555, Vite dev proxy on 9333, `/api` relative base).

## Requirements

### Requirement: Web 服务启动与端口约定

模拟盘 Web 服务 SHALL 通过 `quant-trade sim web` 命令启动，默认监听端口 9555，主机默认 `0.0.0.0`。前端 Vite dev server SHALL 监听 9333，并将 `/api` 请求代理转发至 `http://localhost:9555`。

#### Scenario: 默认端口启动后端
- **WHEN** 用户执行 `uv run quant-trade sim web`
- **THEN** FastAPI 服务在 `http://localhost:9555` 启动，无需显式指定端口

#### Scenario: 自定义端口
- **WHEN** 用户执行 `uv run quant-trade sim web --port 9000`
- **THEN** 服务监听 9000，且用户须同步修改 `web/vite.config.ts` 代理目标才能与前端联调

#### Scenario: 前端开发代理
- **WHEN** 前端 dev server（9333）收到 `/api` 开头的请求
- **THEN** 请求被转发到 `http://localhost:9555`，`changeOrigin` 生效

### Requirement: 会话 CRUD API

后端 SHALL 提供模拟盘会话的创建、列表、详情、状态查询与删除接口。

#### Scenario: 列出会话
- **WHEN** 客户端 `GET /api/sessions`
- **THEN** 返回会话数组，日期字段序列化为 ISO 字符串，且不包含 `portfolio_json` 字段

#### Scenario: 创建会话
- **WHEN** 客户端 `POST /api/sessions`，带 `name`、`start_date`、可选 `end_date`、`capital`、`ref` 查询参数
- **THEN** 返回 `session_id`、`cursor_date`、`total_weeks` 及序列化后的初始 `snapshot`

#### Scenario: 会话详情
- **WHEN** 客户端 `GET /api/sessions/{session_id}`，会话存在
- **THEN** 返回 `cursor_date`、`week_number`、`portfolio_value`、`previous_decisions` 与 `snapshot`

#### Scenario: 会话不存在
- **WHEN** 客户端请求不存在的 `session_id` 的详情或状态
- **THEN** 返回 HTTP 404，detail 为错误描述

#### Scenario: 删除会话
- **WHEN** 客户端 `DELETE /api/sessions/{session_id}`
- **THEN** 会话被删除，返回 `{"deleted": "<session_id>"}`

### Requirement: 周度决策 API

后端 SHALL 提供 `step`（执行调仓）与 `skip`（跳过本周）接口推进模拟盘。

#### Scenario: 执行调仓
- **WHEN** 客户端 `POST /api/sessions/{session_id}/step`，body 含 `orders`（`ts_code`、`target_pct`、`direction`）与可选 `notes`
- **THEN** 返回 `cursor_advanced`、`next_cursor_date`、组合市值/现金、`executed_orders` 明细与 `warnings`

#### Scenario: 无效调仓
- **WHEN** `step` 请求的订单非法（如持仓不足）
- **THEN** 返回 HTTP 400，detail 为错误描述，会话状态不变

#### Scenario: 跳过本周
- **WHEN** 客户端 `POST /api/sessions/{session_id}/skip`
- **THEN** 游标推进一周，返回 `cursor_advanced`、`next_cursor_date`、组合市值

### Requirement: 对比报告 API

后端 SHALL 提供手动盘与参考策略/基准的对比接口。

#### Scenario: 获取对比报告
- **WHEN** 客户端 `GET /api/sessions/{session_id}/compare`
- **THEN** 返回 `weeks_completed`、手动/策略/基准净值序列、`metrics`、`weekly_diffs` 与 HTML 报告路径

#### Scenario: 会话不存在
- **WHEN** 对比不存在的会话
- **THEN** 返回 HTTP 404

### Requirement: 开发环境 CORS

后端 SHALL 允许开发阶段任意来源跨域访问（`allow_origins=["*"]`），方法与请求头不限。

#### Scenario: 跨域请求
- **WHEN** 浏览器从 9333 端口前端发起 `/api` 请求（不经 Vite 代理时）
- **THEN** 响应携带 CORS 头，请求成功

### Requirement: 前端 API 基址约定

前端 SHALL 默认使用相对路径 `/api` 作为 API 基址，并可通过 `VITE_API_BASE` 环境变量覆盖（用于连接部署后的后端）。

#### Scenario: 默认基址
- **WHEN** 未设置 `VITE_API_BASE`
- **THEN** 所有 API 请求发往 `/api` 相对路径，经 Vite 代理到达后端

#### Scenario: 自定义基址
- **WHEN** 构建时设置 `VITE_API_BASE=https://api.example.com`
- **THEN** 所有 API 请求发往该绝对地址
