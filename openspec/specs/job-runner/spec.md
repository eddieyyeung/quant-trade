# job-runner Specification

## Purpose
后台任务的执行基础设施：单 worker、并发度为 1 的串行队列负责入队与执行，向服务函数注入 `RunContext` 以上报进度与日志，通过 `CancelToken` 协作式取消，并在进程重启时把遗留的非终态记录改写为 `interrupted`。任务类型由注册表映射到参数模型与服务函数。
## Requirements
### Requirement: 串行任务队列

系统 SHALL 以单个 worker、并发度为 1 的方式串行执行任务。同时提交多个任务时，其余任务 SHALL 排队等待而非并发执行。

#### Scenario: 任务排队

- **WHEN** 在第一个任务运行期间提交第二个任务
- **THEN** 第二个任务 `status` 保持 `pending`，直到第一个任务结束才开始

#### Scenario: 队首任务开始执行

- **WHEN** worker 取到一个 `pending` 任务
- **THEN** `status` 变为 `running`，`started_at` 被写入

### Requirement: 进度与日志上报

worker SHALL 向服务函数注入一个 `RunContext`，其 `progress_sink` 写入 `run.progress` 与 `run.message`，`log_sink` 写入 `run_log`。

#### Scenario: 进度落库

- **WHEN** 服务函数调用 `ctx.progress(0.35, "已完成 350/1000")`
- **THEN** `run.progress` 为 `0.35`，`run.message` 为 `已完成 350/1000`

#### Scenario: 日志同时落库并推送给订阅者

- **WHEN** 服务函数调用 `ctx.log("回退到备用源", level="warning")`
- **THEN** `run_log` 新增一行，且当前订阅该运行的 SSE 连接收到该条

### Requirement: 协作式取消

系统 SHALL 通过 `CancelToken` 向服务函数的 `RunContext` 传递取消请求，服务函数 SHALL 在下一个检查点停止并返回已完成部分。

#### Scenario: 取消请求被传递

- **WHEN** 客户端请求取消一个 `running` 的任务
- **THEN** 该任务的 `ctx.cancelled()` 在下一次被调用时返回 `True`

#### Scenario: 对非运行中任务取消被拒绝

- **WHEN** 客户端请求取消一个已 `ok` 或 `failed` 的任务
- **THEN** 返回错误，不改变该任务的 `status`

### Requirement: 重启恢复

worker SHALL 在启动时将所有遗留的非终态记录改写为 `interrupted`，既包括 `running` 也包括 `pending` —— 后者只存在于进程内的队列中，重启后无人再会推进它。系统 SHALL NOT 尝试续跑未完成的任务。

#### Scenario: 进程被杀后重启

- **WHEN** 服务重启且存在 `status` 为 `running` 的记录
- **THEN** 这些记录被改写为 `interrupted`，`error` 说明进程中断

#### Scenario: 排队任务随进程消失

- **WHEN** 服务重启且存在 `status` 为 `pending` 的记录（进程退出时仍在内存队列中）
- **THEN** 这些记录被改写为 `interrupted`，`error` 说明其未开始执行
- **AND** 系统 SHALL NOT 在重启后自动重新执行它们

#### Scenario: 中断任务可重跑

- **WHEN** 用户对一个 `interrupted` 任务重新提交相同的 `params_json`
- **THEN** 系统接受并创建一个新的运行

### Requirement: 任务类型注册表

系统 SHALL 通过注册表将 `kind` 映射到 `(参数模型, 服务函数)`。新增任务类型 SHALL 只需在注册表添加一项，无需改动 API 路由或前端路由。

#### Scenario: 已注册类型可提交

- **WHEN** 提交 `kind` 为 `data_sync` 的运行
- **THEN** 系统用 `DataSyncParams` 校验参数并调用 `sync_market_data`

#### Scenario: 未注册类型被拒绝

- **WHEN** 提交一个不在注册表中的 `kind`
- **THEN** 返回 400，指出该 `kind` 不可识别
