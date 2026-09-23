## Purpose

把外部行情与财务数据搬进本地 DuckDB，是整条研究链路的输入口。

它规定五件事：日线行情与财务数据如何落库、交易日历如何维护、指数成分股股票池如何取得（含成分股为空时按行情推导的兜底）、以及多家数据源（akshare / tushare）如何以统一适配器接入。

数据只在这里写入：因子、策略、回测、报告都只读。因此表结构与写入语义（覆盖方式、缺失值、指数行的特殊列）由本能力负责，下游按 `daily_kline` / `financials` / `stock_basic` / `trade_calendar` / `index_weights` 的既定形状取用。
## Requirements
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

返回的代码列表 SHALL 有稳定顺序：相同参数、相同数据下，重复调用 SHALL 返回完全相同的顺序，SHALL NOT 依赖数据库的行序。指数成分股路径与 kline 回退路径均 SHALL 满足这一条。

顺序 SHALL 按股票代码升序。并列分数在下游如何被截断由此确定，但按代码排序本身只是一个确定的约定，不代表优先级。

#### Scenario: 获取指定日期的股票池

- **WHEN** 用户调用 `get_universe(["000300.SH", "000905.SH"], "2026-07-24")`
- **THEN** 系统返回当天沪深300和中证500的全部成分股代码列表（去重后约 800 只）

#### Scenario: 过滤 ST 和上市不足一年的股票

- **WHEN** 用户调用 `get_universe(["000300.SH"], "2026-07-24", filter_st=True, min_list_days=250)`
- **THEN** 系统自动剔除 ST/*ST 股票和上市未满 250 个交易日的次新股

#### Scenario: 重复调用顺序一致

- **WHEN** 用同一组参数对同一个数据连接重复调用 `get_universe` 两次
- **THEN** 两次返回的列表长度与顺序完全相同

#### Scenario: 回退路径同样有序

- **WHEN** 指定日期没有指数成分股数据，`get_universe` 回退到由 kline 推导股票池
- **THEN** 返回的列表按股票代码升序

#### Scenario: 剔除 ST 不打乱顺序

- **WHEN** `filter_st=True` 且股票池中存在 ST 股票
- **THEN** 被剔除的代码消失，其余代码保持原有的相对顺序

### Requirement: 多源数据适配器

系统 SHALL 为每个数据源提供统一适配器接口，屏蔽底层 API 差异，上层代码不感知数据来源。

#### Scenario: 统一接口替换数据源

- **WHEN** 策略代码调用 `DataStore.get_daily(codes, start, end)` 获取行情
- **THEN** 策略代码不需要知道数据来自 akshare 还是 tushare，统一返回 pandas DataFrame

