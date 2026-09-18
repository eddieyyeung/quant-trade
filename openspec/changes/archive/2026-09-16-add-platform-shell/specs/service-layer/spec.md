## MODIFIED Requirements

### Requirement: RunContext 契约

系统 SHALL 提供 `RunContext` 数据类，至少包含 `run_id` / `config` / `store` 三个字段与 `progress()` / `log()` / `cancelled()` 三个方法。服务函数 SHALL 通过该对象上报进度、写入日志、查询取消状态。平台运行时 SHALL 提供将 sink 接入运行记录持久化与 SSE 推送的实现。

#### Scenario: 进度上报

- **WHEN** 服务函数调用 `ctx.progress(0.5, "已完成 500/1000 只股票")`
- **THEN** 进度值与消息被传递给调用方注册的接收器，且服务函数继续执行

#### Scenario: 日志上报

- **WHEN** 服务函数调用 `ctx.log("同步失败，回退到备用源", level="warning")`
- **THEN** 该消息以指定级别传递给调用方注册的接收器

#### Scenario: 无接收器的默认上下文

- **WHEN** 服务函数在未提供 `ctx` 的情况下被调用（使用模块级 `NULL_CONTEXT` 默认值）
- **THEN** 调用正常完成，`progress()` 与 `log()` 为空操作，`cancelled()` 恒返回 `False`

#### Scenario: 函数签名默认值

- **WHEN** 检查任一长任务服务函数的签名
- **THEN** `ctx` 参数具备默认值，使测试与脚本可无 ctx 调用

#### Scenario: 平台运行时接入 sink

- **WHEN** 服务函数由后台 worker 调用
- **THEN** `progress()` 写入运行记录的进度列，`log()` 写入运行日志表并推送给 SSE 订阅者
