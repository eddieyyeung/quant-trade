## ADDED Requirements

### Requirement: 因子相关性矩阵

系统 SHALL 提供因子两两之间的相关性矩阵计算，矩阵元素 SHALL 为区间内逐交易日截面 Pearson 相关的均值，SHALL NOT 将不同日期的观测汇成单一序列后直接相关。

计算 SHALL 由 `quant_trade.factors.correlation` 中的纯函数实现。

#### Scenario: 计算相关矩阵

- **WHEN** 用户对因子列表 `["MA20", "STD20", "KMID"]` 在给定区间请求相关性
- **THEN** 系统返回 3×3 对称矩阵，对角线元素为 1，并返回矩阵所用的因子名顺序

#### Scenario: 单日期区间

- **WHEN** 请求的起始日期与结束日期为同一交易日
- **THEN** 矩阵元素为该日截面的相关系数，计算正常完成

#### Scenario: 逐日截面相关再取均值

- **WHEN** 区间内有 D 个有效交易日
- **THEN** 每个矩阵元素由 D 个截面相关系数取均值得到，且参与计算的有效日期数随结果返回

#### Scenario: 稀疏日期被跳过

- **WHEN** 某交易日的有效截面对数少于计算 IC 所需的最小样本数（10）
- **THEN** 该日 SHALL 被跳过，不计入均值

#### Scenario: 无有效日期

- **WHEN** 区间内没有任何交易日满足最小样本数要求
- **THEN** 服务以 `warning` 级别日志说明原因并返回空矩阵，SHALL NOT 抛出异常

### Requirement: 相关矩阵要素数量上限

请求相关性矩阵的因子数量 SHALL 有上限，超限 SHALL 在领域计算开始之前以参数校验错误拒绝。

#### Scenario: 超过上限被拒绝

- **WHEN** 请求包含 51 个因子的相关性矩阵（上限为 50）
- **THEN** 系统返回参数校验错误（HTTP 422），不执行任何因子值读取

#### Scenario: 恰好等于上限

- **WHEN** 请求包含 50 个因子
- **THEN** 计算正常执行

### Requirement: 相关性取数批量执行

相关性计算 SHALL 以常数次数据库查询完成，SHALL NOT 按交易日或按因子逐次查询。

#### Scenario: 查询次数与区间长度及因子数无关

- **WHEN** 请求 F 个因子、跨越 D 个交易日的相关矩阵
- **THEN** 数据库往返次数不随 F 或 D 增长
