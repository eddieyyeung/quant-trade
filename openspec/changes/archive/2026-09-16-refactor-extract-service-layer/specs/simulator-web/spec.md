## MODIFIED Requirements

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
