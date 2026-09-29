## ADDED Requirements

### Requirement: 因子分层回测

系统 SHALL 提供因子分层（quantile）回测，按因子值在截面上分组，计算各组的下期收益与净值曲线，以及顶组减底组的多空组合净值。分组计算 SHALL 由 `quant_trade.factors.quantile` 中的纯函数实现，SHALL NOT 经过 `quant_trade.backtest` 的 A 股撮合引擎。

分层回测 SHALL 作为因子区分度的统计视图呈现，其净值 SHALL NOT 被表述为可交易组合的收益。

#### Scenario: 分组净值

- **WHEN** 用户对因子 `MA20` 在给定区间请求 5 组分层回测
- **THEN** 系统返回 5 条净值曲线，每条含日期与累计净值，且各组期初净值均为 1

#### Scenario: 多空组合

- **WHEN** 分层回测完成
- **THEN** 系统额外返回多空组合净值（顶组收益减底组收益后累乘），与分组净值同时可得

#### Scenario: 分组数可配置

- **WHEN** 用户请求 10 组而非默认的 5 组
- **THEN** 系统返回 10 条分组净值，分组数不改变接口形状

#### Scenario: 截面样本不足

- **WHEN** 某交易日的有效因子值少于分组数
- **THEN** 该日 SHALL 被跳过，不产生分组记录，且跳过的日期数随结果返回

#### Scenario: 不使用回测引擎

- **WHEN** 检查分层回测的实现
- **THEN** 其计算路径不引入交易成本、涨跌停、停牌与 T+1 约束，也不调用 `quant_trade.backtest` 的任何函数

### Requirement: 分层回测取数批量执行

分层回测 SHALL 以常数次数据库查询完成：因子值与日线数据各一次批量读取，SHALL NOT 按交易日逐次查询。

#### Scenario: 查询次数与区间长度无关

- **WHEN** 对同一因子请求跨越 N 个交易日的分层回测
- **THEN** 数据库往返次数不随 N 增长

#### Scenario: 区间内无因子数据

- **WHEN** 请求区间的因子在 `factor_values` 中无任何记录
- **THEN** 服务以 `warning` 级别日志说明原因并返回空结果，SHALL NOT 抛出异常
