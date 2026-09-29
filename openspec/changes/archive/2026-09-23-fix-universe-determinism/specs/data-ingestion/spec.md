## MODIFIED Requirements

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
