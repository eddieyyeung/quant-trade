## MODIFIED Requirements

### Requirement: 会话 CRUD API

后端 SHALL 提供模拟盘会话的创建、列表、详情、状态查询与删除接口。

会话详情返回的快照 SHALL 携带推荐订单与来源策略名，供决策台直接预览采纳，SHALL NOT 要求客户端另行发起一次重算请求。

#### Scenario: 列出会话
- **WHEN** 客户端 `GET /api/sessions`
- **THEN** 返回会话数组，日期字段序列化为 ISO 字符串，且不包含 `portfolio_json` 字段

#### Scenario: 创建会话
- **WHEN** 客户端 `POST /api/sessions`，带 `name`、`start_date`、可选 `end_date`、`capital`、`ref` 查询参数
- **THEN** 返回 `session_id`、`cursor_date`、`total_weeks` 及序列化后的初始 `snapshot`

#### Scenario: 会话详情
- **WHEN** 客户端 `GET /api/sessions/{session_id}`，会话存在
- **THEN** 返回 `cursor_date`、`week_number`、`portfolio_value`、`previous_decisions` 与 `snapshot`，且 `snapshot` 含推荐订单与来源策略名

#### Scenario: 初始快照不计算推荐

- **WHEN** 客户端创建一个会话并拿到初始 `snapshot`
- **THEN** 该快照的推荐订单与策略信号均为无值，与既有「因子和策略信号将在首次调仓时计算」的延迟计算约定一致

#### Scenario: 会话不存在
- **WHEN** 客户端请求不存在的 `session_id` 的详情或状态
- **THEN** 返回 HTTP 404，detail 为错误描述

#### Scenario: 删除会话
- **WHEN** 客户端 `DELETE /api/sessions/{session_id}`
- **THEN** 会话被删除，返回 `{"deleted": "<session_id>"}`

### Requirement: 周度决策 API

后端 SHALL 提供 `step`（执行调仓）与 `skip`（跳过本周）接口推进模拟盘。

`step` 的订单条目 SHALL 接受可选的 `reason`，并在成交明细中原样带回，使按推荐方案执行的订单在回执里保留来源理由。

#### Scenario: 执行调仓
- **WHEN** 客户端 `POST /api/sessions/{session_id}/step`，body 含 `orders`（`ts_code`、`target_pct`、`direction`）与可选 `notes`
- **THEN** 返回 `cursor_advanced`、`next_cursor_date`、组合市值/现金、`executed_orders` 明细与 `warnings`

#### Scenario: 订单带理由
- **WHEN** `step` 的订单条目含 `reason`
- **THEN** 对应成交明细的理由为该值

#### Scenario: 订单不带理由
- **WHEN** `step` 的订单条目不含 `reason`
- **THEN** 请求照常处理，对应成交明细的理由回落为「用户主动建仓」

#### Scenario: 无效调仓
- **WHEN** `step` 请求的订单非法（如持仓不足）
- **THEN** 返回 HTTP 400，detail 为错误描述，会话状态不变

#### Scenario: 跳过本周
- **WHEN** 客户端 `POST /api/sessions/{session_id}/skip`
- **THEN** 游标推进一周，返回 `cursor_advanced`、`next_cursor_date`、组合市值
