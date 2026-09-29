## MODIFIED Requirements

### Requirement: 因子查询接口

系统 SHALL 提供按因子名、股票池、日期范围读取因子值的查询接口, 返回宽表 (index: ts_code, columns: factor_name) 或长表, 供训练链路直接消费。

因子名列表 SHALL 由 `quant_trade.services.queries.list_factor_names()` 统一提供——其来源为 `factor_values` 表中实际存在的去重因子名。调用方 SHALL NOT 绕过服务层直接从 `ALPHA158_NAMES` 常量或裸表 dump 取因子名。

#### Scenario: 宽表查询

- **WHEN** 调用查询接口传入因子名列表 ["KMID", "MA5", "STD20"]、股票池和单个日期
- **THEN** 返回该日期所有股票的三列因子宽表, 缺值股票保留 NaN 行

#### Scenario: 因子名列表来源统一

- **WHEN** 因子库页面需要展示可选因子
- **THEN** 因子名经 `services.queries.list_factor_names()` 取得, 与 `factor_values` 表内容一致, 而非来自 `ALPHA158_NAMES` 静态常量

#### Scenario: 因子名列表区分已落库与未落库

- **WHEN** 158 个 Alpha158 因子中只有部分已写入 `factor_values`
- **THEN** 因子名列表只返回已落库的部分, 调用方 SHALL NOT 假设列表长度恒为 158

#### Scenario: Alpha158 因子持久化覆盖度

- **WHEN** 查询 Alpha158 因子的持久化覆盖度
- **THEN** 返回各因子的记录数与日期范围, 供因子库页面展示覆盖区间

## ADDED Requirements

### Requirement: Alpha158 计算分块执行并可取消

系统 SHALL 将 Alpha158 计算按日历区间分块执行, 每块开始前检查取消请求, 逐块落库并上报进度. 取消时 SHALL 保留已完成块已写入的因子值. 分块计算的结果 SHALL 与单次整体计算逐值相等.

#### Scenario: 分块结果与单次计算一致

- **WHEN** 对同一区间分别执行分块计算与单次整体计算
- **THEN** 两者写入 `factor_values` 的值逐一相等, 行数一致

#### Scenario: 取消保留已完成块

- **WHEN** 计算进行到第二块时收到取消请求
- **THEN** 第一块已写入的因子值保留, 后续块不再计算, 结果对象标记为已取消

#### Scenario: 单块区间不拆分

- **WHEN** 请求区间不超过一个块的跨度
- **THEN** 计算只执行一块, 行为与分块前一致

#### Scenario: 进度按块上报

- **WHEN** 区间跨越 N 个块
- **THEN** 每完成一块进度推进至 `已完成块数 / N`, 全部完成时为 1.0

#### Scenario: 已保存行数跨块累加

- **WHEN** 计算跨越多个块
- **THEN** 结果中的已保存行数为各块之和, 因子数为各块出现过的因子名并集
