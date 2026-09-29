## MODIFIED Requirements

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
