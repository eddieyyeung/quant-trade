## MODIFIED Requirements

### Requirement: 表元数据查询接口

系统 SHALL 提供查询数据表元信息的接口，返回表行数、日期范围与领域实体列表。调用方 SHALL NOT 直接书写裸 SQL 获取此类信息。

#### Scenario: 通用表统计

- **WHEN** 调用 `DataStore.table_stats(table)` 且传入 `daily_kline`
- **THEN** 返回该表行数、最早交易日、最晚交易日

#### Scenario: 非法表名被拒绝

- **WHEN** 调用 `table_stats()` 传入不在允许列表中的表名
- **THEN** 抛出参数错误，不执行任何 SQL

#### Scenario: 因子名列表

- **WHEN** 调用因子名列表查询接口
- **THEN** 返回 `factor_values` 表中实际存在的去重因子名

#### Scenario: 股票池覆盖率

- **WHEN** 调用覆盖率查询接口并指定股票池与日期
- **THEN** 返回该股票池中已同步日线数据的股票数与占比

#### Scenario: 因子持久化覆盖度

- **WHEN** 调用因子覆盖度查询接口
- **THEN** 返回每个因子的记录数、最早交易日与最晚交易日，供因子库页面判定该因子是否已落库及覆盖区间

#### Scenario: 未落库因子

- **WHEN** 查询覆盖度时某因子在 `factor_values` 中无任何记录
- **THEN** 该因子不出现在覆盖度结果中，调用方据此判定其未落库，SHALL NOT 返回零值行冒充已有覆盖
