# platform-runtime Specification

## Purpose
研究平台的进程外壳：`python -m quant_trade` 单入口同时托管 HTTP API 与前端构建产物，只绑定回环地址；启动时初始化任务 worker 并恢复遗留的非终态运行记录，同时保留既有模拟盘 API 的可用性。
## Requirements
### Requirement: 单一启动入口

系统 SHALL 通过 `python -m quant_trade` 启动研究平台，该入口 SHALL NOT 接受任何子命令。平台 SHALL 在本机可访问的地址上启动一个同时提供 HTTP API 与前端页面的服务进程，并 SHALL 在启动时初始化任务 worker 并恢复中断的运行记录。

#### Scenario: 默认启动

- **WHEN** 用户执行 `uv run python -m quant_trade`（不带任何参数）
- **THEN** 服务在 `http://127.0.0.1:9555` 启动，无需显式指定端口或主机

#### Scenario: 默认不暴露到网络

- **WHEN** 用户以默认参数启动平台，且平台不提供任何身份认证
- **THEN** 服务仅绑定回环地址 `127.0.0.1`，局域网内其他主机无法访问
- **AND** 需要对外暴露时必须显式传入 `--host 0.0.0.0`

#### Scenario: 自定义端口

- **WHEN** 用户执行 `uv run python -m quant_trade --port 9000`
- **THEN** 服务监听 9000 端口

#### Scenario: 开发模式热重载

- **WHEN** 用户执行 `uv run python -m quant_trade --reload`
- **THEN** 服务以热重载模式启动，代码变更后自动重启

#### Scenario: 传入子命令被拒绝

- **WHEN** 用户执行 `uv run python -m quant_trade data sync`
- **THEN** 进程以非零状态码退出，并输出提示：本平台不接受子命令，研究操作通过 Web 界面发起

#### Scenario: 控制台脚本已移除

- **WHEN** 检查 `pyproject.toml` 的 `[project.scripts]`
- **THEN** 不存在 `quant-trade` 入口点

#### Scenario: 启动时恢复中断运行

- **WHEN** 平台启动且数据库中残留 `running` 或 `pending` 状态的运行记录
- **THEN** 这些记录被改写为 `interrupted`

### Requirement: 前端静态托管

平台 SHALL 在检测到前端构建产物（`web/dist`）时托管该目录，并对未匹配到静态文件的非 API 路径回退返回 `index.html` 以支持前端路由。

#### Scenario: 托管构建产物

- **WHEN** `web/dist` 存在且客户端请求 `/`
- **THEN** 返回 `web/dist/index.html`

#### Scenario: 静态资源

- **WHEN** 客户端请求 `/assets/<file>`
- **THEN** 返回 `web/dist/assets/` 下对应的资源文件

#### Scenario: 前端路由回退

- **WHEN** 客户端请求 `/factors/ic` 且 `web/dist` 中不存在该路径的实体文件
- **THEN** 返回 `web/dist/index.html`，由前端路由接管

#### Scenario: API 路径不参与回退

- **WHEN** 客户端请求以 `/api/` 开头且不存在的路径
- **THEN** 返回 404 JSON 响应，不回退到 `index.html`

#### Scenario: 构建产物缺失

- **WHEN** `web/dist` 不存在且客户端请求 `/`
- **THEN** 返回 HTTP 200 与提示信息，说明需先执行前端构建（`cd web && npm run build`）；`/api/health` 仍可正常访问

### Requirement: 健康检查接口

平台 SHALL 提供 `GET /api/health` 接口，返回服务状态与数据库可达性。

#### Scenario: 健康检查成功

- **WHEN** 客户端请求 `GET /api/health`
- **THEN** 返回 HTTP 200 与 JSON，包含服务状态、数据库路径、数据库可达标志

#### Scenario: 数据库在运行期不可达

- **WHEN** 服务已启动，其后新建数据库连接失败（路径不可读、被其他进程独占、文件损坏），客户端请求 `GET /api/health`
- **THEN** 返回 HTTP 200，响应中数据库可达标志为 `false`，服务状态标明降级，并附带错误详情

#### Scenario: 已建立的连接不受文件删除影响

- **WHEN** 数据库文件在服务运行期间被删除，而进程内已持有打开的连接
- **THEN** 健康检查仍报告数据库可达
- **AND** 系统 SHALL NOT 将其误报为降级 —— DuckDB 在同一进程内共享已打开的数据库实例，删除文件不影响已建立的连接，探测到的是内存中的实例而非磁盘文件

#### Scenario: 数据库在启动期不可打开

- **WHEN** 数据库路径不可用（如指向目录或无权限），服务启动
- **THEN** 启动失败并抛出可读错误，SHALL NOT 启动一个半可用进程

### Requirement: 现有模拟盘 API 保持可用

平台启动时 SHALL 挂载现有模拟盘 API，使会话 CRUD、逐周决策与对比接口在迁移期间继续可用，行为与端口约定不变。

#### Scenario: 模拟盘接口可访问

- **WHEN** 平台已启动，客户端请求 `GET /api/sessions`
- **THEN** 返回会话数组，行为与既有 `simulator-web` 规格一致

#### Scenario: 模拟盘前端开发代理

- **WHEN** Vite dev server（9333）收到 `/api` 开头的请求
- **THEN** 请求被转发到 `http://localhost:9555`

