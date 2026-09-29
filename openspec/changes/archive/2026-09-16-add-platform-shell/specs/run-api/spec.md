## ADDED Requirements

### Requirement: 统一运行接口

系统 SHALL 提供 `POST /api/runs` 作为所有长任务的唯一入口，请求体为 `{kind, params}`，响应为 `202` 与 `run_id`。

#### Scenario: 提交成功立即返回

- **WHEN** 客户端提交一个合法的 `{kind, params}`
- **THEN** 立即返回 HTTP 202 与 `run_id`，不等待任务完成

#### Scenario: 参数非法被拒绝

- **WHEN** 客户端提交的 `params` 未通过参数模型校验
- **THEN** 返回 422，响应包含校验错误详情，且不创建运行记录

### Requirement: 运行查询接口

系统 SHALL 提供运行列表、运行详情与产物查询接口。

#### Scenario: 分页列出运行

- **WHEN** 客户端请求 `GET /api/runs` 且不指定分页参数
- **THEN** 返回最近的运行记录（默认页大小），按开始时间倒序

#### Scenario: 查询单个运行

- **WHEN** 客户端请求 `GET /api/runs/{run_id}`
- **THEN** 返回该运行的 `status` / `progress` / `message` / 时间戳 / `error`

#### Scenario: 运行不存在

- **WHEN** 客户端请求一个不存在的 `run_id`
- **THEN** 返回 404

### Requirement: SSE 日志流

系统 SHALL 提供 `GET /api/runs/{run_id}/logs` 作为 Server-Sent Events 流，先补发已有日志，再持续推送新增日志，直至运行结束。

#### Scenario: 补发历史日志

- **WHEN** 客户端连接一个已产生 10 条日志的运行
- **THEN** 连接建立后立即收到这 10 条，按 `seq` 升序

#### Scenario: 推送新增日志

- **WHEN** 运行期间产生新日志
- **THEN** 订阅该运行的连接在 500 毫秒内收到

#### Scenario: 运行结束关闭流

- **WHEN** 运行进入终态（`ok` / `failed` / `cancelled` / `interrupted`）
- **THEN** 服务端发送结束事件并关闭连接

#### Scenario: 日志流为只读单向

- **WHEN** 客户端向日志流端点发送数据
- **THEN** 不影响运行，流仍为服务端到客户端的单向推送

### Requirement: 取消接口

系统 SHALL 提供 `POST /api/runs/{run_id}/cancel`。

#### Scenario: 取消运行中任务

- **WHEN** 客户端请求取消一个 `running` 的任务
- **THEN** 返回 202，任务在下一个检查点停止，`status` 最终为 `cancelled`
