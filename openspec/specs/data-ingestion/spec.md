## Purpose

TBD — see design.md for architecture context.

## Requirements

### Requirement: DuckDB 存储日线行情

系统 SHALL 使用 DuckDB 持久化存储沪深 A 股日线行情数据，包含开高低收、成交量、成交额、涨跌幅、换手率等字段，以 `(ts_code, trade_date)` 作为联合主键。

#### Scenario: 首次数据拉取

- **WHEN** 用户执行 `quant-trade data sync` 且数据库为空
- **THEN** 系统从 akshare 拉取全 A 股日线数据（2015-01-01 至今），写入 DuckDB `daily_kline` 表

#### Scenario: 增量更新

- **WHEN** 用户执行 `quant-trade data sync` 且数据库已有数据
- **THEN** 系统仅拉取最近 5 个交易日的增量数据，以 upsert 方式写入，不重复拉取历史

#### Scenario: 数据源故障回退

- **WHEN** akshare 接口返回错误或超时
- **THEN** 系统自动尝试 tushare 作为备用源拉取数据，并记录 warning 日志

### Requirement: 财务数据存储

系统 SHALL 按季度拉取并存储 A 股财务数据（PE、PB、ROE、营收同比、净利润同比等），以 `(ts_code, end_date)` 为联合主键，并记录公告日期 `ann_date` 用于回测时避免未来信息泄露。

#### Scenario: 季度财务数据更新

- **WHEN** 用户执行 `quant-trade data sync --include-financials`
- **THEN** 系统拉取最近 8 个季度的财务数据，按公告日标记，写入 DuckDB `financials` 表

### Requirement: 交易日历

系统 SHALL 维护 A 股交易日历表，区分交易日和非交易日，并自动识别调休导致的周末交易日。

#### Scenario: 获取下一交易日

- **WHEN** 用户调用 `trade_calendar.next_trade_date("2026-07-24")`（周五）
- **THEN** 系统返回下周一日期 `2026-07-27`

#### Scenario: 判断是否为交易日

- **WHEN** 用户调用 `trade_calendar.is_trade_date("2026-07-25")`（周六）
- **THEN** 系统返回 `False`

### Requirement: 指数成分股股票池

系统 SHALL 支持按指数代码（沪深300、中证500 等）查询历史成分股列表，包括调入调出日期。

#### Scenario: 获取指定日期的股票池

- **WHEN** 用户调用 `get_universe(["000300.SH", "000905.SH"], "2026-07-24")`
- **THEN** 系统返回当天沪深300和中证500的全部成分股代码列表（去重后约 800 只）

#### Scenario: 过滤 ST 和上市不足一年的股票

- **WHEN** 用户调用 `get_universe(["000300.SH"], "2026-07-24", filter_st=True, min_list_days=250)`
- **THEN** 系统自动剔除 ST/*ST 股票和上市未满 250 个交易日的次新股

### Requirement: 多源数据适配器

系统 SHALL 为每个数据源提供统一适配器接口，屏蔽底层 API 差异，上层代码不感知数据来源。

#### Scenario: 统一接口替换数据源

- **WHEN** 策略代码调用 `DataStore.get_daily(codes, start, end)` 获取行情
- **THEN** 策略代码不需要知道数据来自 akshare 还是 tushare，统一返回 pandas DataFrame
