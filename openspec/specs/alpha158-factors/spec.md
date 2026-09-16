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

#### Scenario: 宽表查询

- **WHEN** 调用查询接口传入因子名列表 ["KMID", "MA5", "STD20"]、股票池和单个日期
- **THEN** 返回该日期所有股票的三列因子宽表, 缺值股票保留 NaN 行

### Requirement: 兼容现有因子框架

系统 SHALL 提供桥接, 允许单个 Alpha158 因子通过现有 `Factor.compute(date, universe)` 接口按需计算 (从 `factor_values` 读取或即时计算), 以便现有 `factor ic` 分析工具直接使用。

#### Scenario: IC 分析桥接

- **WHEN** 通过桥接因子调用 `compute_ic_series`, 传入 Alpha158 因子名
- **THEN** 返回该因子的 IC 均值、ICIR、正 IC 占比, 与手工因子同格式
