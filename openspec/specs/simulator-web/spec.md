## Purpose

Simulator web: FastAPI web service for the interactive simulator — session CRUD, weekly step/skip decisions, comparison reports, development CORS, and frontend API conventions (backend on port 9555, Vite dev proxy on 9333, `/api` relative base).
## Requirements
### Requirement: Web 服务启动与端口约定

模拟盘 API SHALL 由研究平台进程托管，通过 `python -m quant_trade` 启动，默认监听端口 9555，主机默认 `127.0.0.1`（仅回环）。前端 Vite dev server SHALL 监听 9333，并将 `/api` 请求代理转发至 `http://localhost:9555`。系统 SHALL NOT 提供独立的模拟盘启动命令。

#### Scenario: 默认端口启动后端

- **WHEN** 用户执行 `uv run python -m quant_trade`
- **THEN** 研究平台在 `http://localhost:9555` 启动，模拟盘 API 随之可用，无需显式指定端口

#### Scenario: 自定义端口

- **WHEN** 用户执行 `uv run python -m quant_trade --port 9000`
- **THEN** 服务监听 9000，且用户须同步修改 `web/vite.config.ts` 代理目标才能与前端联调

#### Scenario: 前端开发代理

- **WHEN** 前端 dev server（9333）收到 `/api` 开头的请求
- **THEN** 请求被转发到 `http://localhost:9555`，`changeOrigin` 生效

#### Scenario: 独立启动命令已移除

- **WHEN** 用户执行 `uv run quant-trade sim web`
- **THEN** 命令不存在，进程以非零状态码退出

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

基址解析 SHALL 由平台共享的请求模块承担，仿真客户端 SHALL 建立在其之上，SHALL NOT 自带第二套基址解析与错误归一化。仿真客户端 SHALL NOT 依赖已被删除的独立模拟盘前端模块。

#### Scenario: 默认基址
- **WHEN** 未设置 `VITE_API_BASE`
- **THEN** 所有 API 请求发往 `/api` 相对路径，经 Vite 代理到达后端

#### Scenario: 自定义基址
- **WHEN** 构建时设置 `VITE_API_BASE=https://api.example.com`
- **THEN** 所有 API 请求发往该绝对地址

#### Scenario: 仿真客户端复用平台请求模块
- **WHEN** 检查仿真前端发起请求的路径
- **THEN** 基址解析与错误归一化来自平台共享的请求模块，仿真客户端只声明路径与响应类型

### Requirement: 仿真分区并入平台外壳

仿真前端 SHALL 作为研究平台的一个分区存在，通过侧边栏「仿真」进入 `/simulator`，SHALL NOT 再作为独立应用运行。`/simulator` 入口 SHALL 不再指向占位页。

分区内 SHALL 由列表行进入单个会话的决策台，SHALL NOT 为详情页声明顶部标签——详情页的 `activeKey` 匹配不到任何标签，标签栏会空白。

页面样式 SHALL 使用 antd 组件与主题 token，SHALL NOT 新增手写样式表。图表 SHALL 使用按需注册的共享 ECharts 封装，SHALL NOT 引入其他图表库。

#### Scenario: 分区入口不再是占位页

- **WHEN** 用户点击侧边栏「仿真」
- **THEN** 进入会话创建与列表页面，而非「该分区由后续变更提供」的占位内容

#### Scenario: 刷新不 404

- **WHEN** 用户直接刷新 `/simulator` 或处于某个会话的决策台时刷新
- **THEN** 后端 SPA 回退返回页面外壳，路由正常渲染，请求不打到回退处理之外

#### Scenario: 无手写样式表

- **WHEN** 检查仿真分区新增的文件
- **THEN** 不存在新增的 `.css` / `.less` / `.scss` 文件，页面样式来源于 antd 组件与主题 token

#### Scenario: 图表按需注册

- **WHEN** 检查仿真分区的图表引入路径
- **THEN** 图表均经由共享封装组件使用，未出现直接的全量 ECharts 引入，也未引入其他图表库

#### Scenario: 后端错误可见

- **WHEN** 任一仿真页面的请求返回错误
- **THEN** 页面展示该错误的信息，SHALL NOT 静默吞掉

