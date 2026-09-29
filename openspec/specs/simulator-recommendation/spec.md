# simulator-recommendation Specification

## Purpose
TBD - created by archiving change add-simulator-one-click-follow. Update Purpose after archive.
## Requirements
### Requirement: 推荐订单的生成

快照 SHALL 携带可直接提交给周度决策接口的推荐订单，来源为同一快照中参考策略信号。

推荐订单 SHALL 与决策订单同构：`ts_code`、`target_pct`、`direction`、`reason`。映射 SHALL NOT 重新计算策略信号，SHALL NOT 改变信号中的目标权重。

订单顺序 SHALL 是买入在前、卖出在后，与决策表单的提交顺序约定一致。

信号中为「跌出策略目标组合」的持仓补出的卖出条目 SHALL 予以保留，SHALL NOT 只取买入条目交给引擎自动补卖。

#### Scenario: 有参考策略且有信号

- **WHEN** 会话配置了参考策略且本周产生了信号
- **THEN** 快照的推荐订单逐条对应信号条目，`ts_code`、`target_pct`、`direction`、`reason` 与信号一致，且买入条目排在卖出条目之前

#### Scenario: 目标权重原样传递

- **WHEN** 信号给出某只股票的目标权重 0.0667
- **THEN** 推荐订单中该股的目标权重为 0.0667，SHALL NOT 被归一化或重新分配

#### Scenario: 补出的卖出被保留

- **WHEN** 参考策略本周未把某只持仓列入目标组合
- **THEN** 推荐订单包含该股目标仓位为 0 的卖出条目，理由标注为跌出目标组合

### Requirement: 推荐订单的可用性区分

推荐订单 SHALL 区分「未配置参考策略」与「配置了策略但本周无信号」两种空值：前者为无值，后者为空列表。

#### Scenario: 未配置参考策略

- **WHEN** 会话创建时未指定参考策略
- **THEN** 推荐订单为无值，来源策略名为无值

#### Scenario: 配置了策略但本周无信号

- **WHEN** 会话配置了参考策略，且该策略本周未给出任何买卖信号，且组合无持仓
- **THEN** 推荐订单为空列表，来源策略名为该策略名

### Requirement: 推荐来源标注

快照 SHALL 标注推荐订单的来源策略名，使采纳记录能说明这批订单出自哪个策略。

#### Scenario: 采纳后回看

- **WHEN** 会话的参考策略为 `factor_ranking`
- **THEN** 快照的来源策略名为 `factor_ranking`

### Requirement: 决策订单的理由透传

决策订单 SHALL 支持可选的 `reason` 字段。

执行成交时，成交明细中的理由 SHALL 取该订单的 `reason`；该字段缺失或为空时，理由 SHALL 回落为「用户主动建仓」。

#### Scenario: 带理由的订单

- **WHEN** 提交一条 `reason` 为「综合得分 2.31」的买入订单
- **THEN** 该笔成交明细的理由为「综合得分 2.31」，SHALL NOT 为「用户主动建仓」

#### Scenario: 不带理由的订单

- **WHEN** 提交一条未提供 `reason` 的买入订单
- **THEN** 该笔成交明细的理由为「用户主动建仓」

