## MODIFIED Requirements

### Requirement: 对比报告 API

后端 SHALL 提供手动盘与参考策略/基准的对比接口。

`weekly_diffs` 的每一条 SHALL 描述该周「用户的目标组合」与「当周推荐的目标组合」之间的偏离，SHALL NOT 依赖影子回测的逐周持仓。目标组合 SHALL 由订单中方向为买入且目标仓位大于 0 的代码集合构成。

每条差异 SHALL 携带一个可空的偏离对象：未配置参考策略（或无策略信号可依据）时该对象为无值，此时 SHALL NOT 报告任何偏离。偏离对象 SHALL 含是否完全跟随、被剔除的代码、被额外加入的代码。

策略影子回测失败时，接口 SHALL 照常返回手动盘与基准序列，并 SHALL 在结果中给出失败原因，SHALL NOT 静默省略策略序列。

#### Scenario: 获取对比报告

- **WHEN** 客户端 `GET /api/sessions/{session_id}/compare`
- **THEN** 返回 `weeks_completed`、手动/策略/基准净值序列、`metrics`、`weekly_diffs` 与 HTML 报告路径

#### Scenario: 完全跟随推荐

- **WHEN** 某周用户提交的买入目标组合与当周推荐的目标组合一致
- **THEN** 该周的偏离对象标记为完全跟随，被剔除与被额外加入均为空

#### Scenario: 部分采纳

- **WHEN** 当周推荐 4 只，用户只提交其中 3 只并自行加入 1 只
- **THEN** 该周被剔除列含未采纳的那 1 只，被额外加入列含自行加入的那 1 只，不完全跟随

#### Scenario: 未配置参考策略

- **WHEN** 某周没有可依据的策略信号
- **THEN** 该周的偏离对象为无值，SHALL NOT 报告为完全跟随

#### Scenario: 策略回测失败

- **WHEN** 参考策略的影子回测抛出异常
- **THEN** 接口返回 200，手动盘与基准序列照常存在，结果中含失败原因，策略序列为无值

#### Scenario: 会话不存在

- **WHEN** 对比不存在的会话
- **THEN** 返回 HTTP 404
