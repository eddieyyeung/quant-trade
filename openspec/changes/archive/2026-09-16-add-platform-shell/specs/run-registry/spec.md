## ADDED Requirements

### Requirement: 运行记录持久化

系统 SHALL 在 DuckDB 中持久化每次运行的记录，包含 `run_id` / `kind` / `params_json` / `status` / `progress` / `message` / `trigger` / `idempotency_key` / `started_at` / `finished_at` / `error` 字段。

#### Scenario: 入队即产生记录

- **WHEN** 一个运行被提交
- **THEN** `run` 表中出现对应行，`status` 为 `pending`，`params_json` 为该次运行的参数对象序列化结果

#### Scenario: 参数可从记录复现

- **WHEN** 读取任意一条 `run` 记录的 `params_json`
- **THEN** 其可被反序列化为对应的参数对象，用于重跑同一次运行

### Requirement: 运行状态机

`run.status` SHALL 仅取以下值：`pending` / `running` / `ok` / `failed` / `cancelled` / `interrupted`，且转换遵循单向路径。

#### Scenario: 正常完成

- **WHEN** 服务函数正常返回
- **THEN** `status` 由 `running` 变为 `ok`，`finished_at` 被写入

#### Scenario: 服务函数抛异常

- **WHEN** 服务函数抛出异常
- **THEN** `status` 变为 `failed`，`error` 记录异常类型与消息，`finished_at` 被写入

#### Scenario: 用户取消

- **WHEN** 用户在运行期间请求取消，服务函数检测到并返回
- **THEN** `status` 变为 `cancelled`，已完成部分的产物保留

### Requirement: 运行日志持久化

系统 SHALL 将运行期间通过 `RunContext.log()` 上报的每条日志写入 `run_log` 表，按 `(run_id, seq)` 递增编号。

#### Scenario: 日志按序可读

- **WHEN** 一次运行产生了 N 条日志
- **THEN** `run_log` 中存在 N 行，`seq` 从 1 连续递增，可按键 `(run_id, seq)` 有序读回

#### Scenario: 日志含级别

- **WHEN** 服务函数以 `level="warning"` 上报
- **THEN** 该行的 `level` 列为 `warning`

### Requirement: 产物索引

系统 SHALL 通过 `artifact` 表索引运行产物，`storage` 字段区分 `table`（结构化专用表）/ `parquet` / `html` 三类后端，`ref` 指向具体位置。

#### Scenario: 登记结构化产物

- **WHEN** 一次运行产出结构化结果并写入专用表
- **THEN** `artifact` 中登记一行，`storage` 为 `table`，`ref` 为该表名，`row_count` 为写入行数

#### Scenario: 按运行查询产物

- **WHEN** 查询某 `run_id` 的产物
- **THEN** 返回该次运行登记的全部 artifact 行

### Requirement: 预留字段

`run.trigger` 与 `run.idempotency_key` SHALL 在本期即写入（`trigger` 恒为 `manual`，幂等键可为空），以便后续引入调度器与自动模拟盘时无需迁移历史数据。

#### Scenario: 手动触发的运行

- **WHEN** 用户从界面提交一个运行
- **THEN** `trigger` 为 `manual`

#### Scenario: 幂等键唯一约束存在

- **WHEN** 检查 `run` 表结构
- **THEN** 存在 `(kind, idempotency_key)` 的唯一约束，为空值时不阻止多行插入
