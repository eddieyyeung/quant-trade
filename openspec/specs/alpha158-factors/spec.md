## Purpose

向量化 Alpha158 因子库: 158 个截面行情因子 (移植自 qlib Alpha158 表达式定义), polars 滚动算子批量计算, 结果落盘 DuckDB `factor_values` 表供训练与 IC 分析复用。

## Requirements

### Requirement: Alpha158 因子全集

系统 SHALL 提供 158 个因子, 按 qlib Alpha158 定义分组:

| 组 | 数量 | 示例 | 表达式形式 |
|----|------|------|-----------|
| KBAR | 9 | KMID, KLEN, KMID2, KUP, KUP2, KLOW, KLOW2, KSFT, KSFT2 | 单期 OHLC 组合 |
| PRICE | 4 | OPEN0, HIGH0, LOW0, VWAP0 | `$field/$close` |
| ROLLING | 145 | MA5-60, STD5-60, BETA, RSQR, RESI, MAX, MIN, QTLU, QTLD, RANK, RSV, IMAX, IMIN, IMXD, CORR, CORD, CNTP, CNTN, CNTD, SUMP, SUMN, SUMD, VMA, VSTD, WVMA, VSUMP, VSUMN, VSUMD | 29 模板 × 窗口 [5, 10, 20, 30, 60] |

注: qlib 默认 Alpha158 配置 (kbar/price/rolling) 不含 VOLUME 组 (`Ref($volume, d)/$volume`), 故总数 9 + 4 + 145 = 158。

#### Scenario: 因子数量校验

- **WHEN** 系统初始化 Alpha158 因子库
- **THEN** 注册的因子名称总数 SHALL 为 158, 且与 qlib 官方 Alpha158 名称集合一致

### Requirement: 向量化批量计算

系统 SHALL 提供批量计算接口, 对给定日期范围和股票池, 一次性计算全部 158 个因子, 内部使用 polars `group_by` + `rolling_*` 算子向量化实现, 不逐股票循环。

#### Scenario: 批量计算全因子

- **WHEN** 调用批量计算接口, 传入日期范围 [start, end] 和股票池
- **THEN** 返回 DataFrame 含 factor_name、ts_code、trade_date、value 四列, 覆盖全部 158 因子
- **AND** 结果与 qlib 官方 Alpha158 在同数据上的计算结果在数值上一致 (允许浮点容差)

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

### Requirement: vwap 派生列

系统 SHALL 以 `vwap = amount / volume` 派生 vwap 列用于 PRICE 组 VWAP0 因子与 VOLUME/ROLLING 相关因子, 不要求新数据源。

#### Scenario: vwap 派生计算

- **WHEN** amount 或 volume 任一为 0、缺失
- **THEN** 对应 vwap 值 SHALL 为 NaN, 且不参与后续因子计算

### Requirement: 因子值持久化

系统 SHALL 将计算结果落盘 DuckDB `factor_values(factor_name VARCHAR, ts_code VARCHAR, trade_date DATE, value DOUBLE, PRIMARY KEY (factor_name, ts_code, trade_date))` 表, 支持按日期增量更新 (已存在数据覆盖或跳过)。

#### Scenario: 增量更新

- **WHEN** 对已计算过的日期再次执行批量计算
- **THEN** 系统跳过已落盘部分, 仅计算并写入缺失日期的因子值

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

### Requirement: 兼容现有因子框架

系统 SHALL 提供桥接, 允许单个 Alpha158 因子通过现有 `Factor.compute(date, universe)` 接口按需计算 (从 `factor_values` 读取或即时计算), 以便现有 `factor ic` 分析工具直接使用。

#### Scenario: IC 分析桥接

- **WHEN** 通过桥接因子调用 `compute_ic_series`, 传入 Alpha158 因子名
- **THEN** 返回该因子的 IC 均值、ICIR、正 IC 占比, 与手工因子同格式
