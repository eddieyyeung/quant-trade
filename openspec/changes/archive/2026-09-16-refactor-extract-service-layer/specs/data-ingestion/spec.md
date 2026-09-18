## MODIFIED Requirements

### Requirement: DuckDB 存储日线行情

系统 SHALL 使用 DuckDB 持久化存储沪深 A 股日线行情数据，包含开高低收、成交量、成交额、涨跌幅、换手率等字段，以 `(ts_code, trade_date)` 作为联合主键。数据写入 SHALL 由数据同步服务函数执行，调用方 SHALL NOT 自行组合数据源适配器与写入逻辑。

#### Scenario: 首次数据拉取

- **WHEN** 数据同步服务被调用且数据库为空
- **THEN** 系统从 2015-01-01 起拉取**股票池成分股**（默认沪深300 + 中证500，约 800 只）的日线数据，写入 DuckDB `daily_kline` 表
- **AND** 若股票池为空，则退化为取上市股票列表的前 200 只作为样本
- **AND** 系统 SHALL NOT 声称拉取了「全 A 股」—— 非成分股不在同步范围内

#### Scenario: 增量更新

- **WHEN** 数据同步服务被调用且数据库已有数据
- **THEN** 系统仅拉取最近 5 个交易日的增量数据，以 upsert 方式写入，不重复拉取历史

#### Scenario: 数据源故障回退

- **WHEN** akshare 接口返回错误或超时
- **THEN** 系统自动尝试 tushare 作为备用源拉取数据，并通过 `ctx.log(level="warning")` 上报

#### Scenario: 同步过程上报进度

- **WHEN** 数据同步服务在股票循环中每完成一批股票
- **THEN** 通过 `ctx.progress()` 上报已完成数量与总数

#### Scenario: 同步过程可取消

- **WHEN** 数据同步服务在股票循环中检测到 `ctx.cancelled()` 返回 `True`
- **THEN** 停止后续拉取，关闭数据源适配器连接，返回已完成部分的结果
