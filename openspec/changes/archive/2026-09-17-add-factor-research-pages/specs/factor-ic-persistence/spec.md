## ADDED Requirements

### Requirement: IC 序列持久化

系统 SHALL 在 DuckDB 中持久化因子的 IC / RankIC 序列，表结构为 `ic_series(factor_name, trade_date, forward_period, ic, rank_ic, sample_size)`，主键 `(factor_name, trade_date, forward_period)`。

`forward_period` SHALL 是主键的一部分，使同一因子在不同持有期下的 IC 并存；`sample_size` SHALL 记录该日参与计算的有效股票数。

#### Scenario: 写入 IC 序列

- **WHEN** IC 计算任务对因子 `MA20` 完成区间 `[start, end]`、forward period 为 5 的计算
- **THEN** `ic_series` 中每个有效交易日存在一行，`factor_name` 为 `MA20`，`forward_period` 为 5，`ic` 与 `rank_ic` 均已写入

#### Scenario: 同一因子多持有期共存

- **WHEN** 同一因子在 forward period 为 1、5、10、20 下分别计算完成
- **THEN** 四个持有期的记录同时存在，互不覆盖

#### Scenario: 重算幂等

- **WHEN** 对已计算过的 `(factor_name, trade_date, forward_period)` 再次执行计算
- **THEN** 记录被覆盖而非重复插入，表中该主键下仍只有一行

#### Scenario: 无效日期不写入

- **WHEN** 某交易日因样本不足或数据缺失无法计算 IC
- **THEN** 该日不产生记录，SHALL NOT 写入 NaN 行

### Requirement: IC 序列查询

系统 SHALL 提供按因子名、日期范围与 forward period 读取 IC 序列的查询接口，并返回汇总指标：IC 均值、IC 标准差、IC_IR、IC 正值占比。

查询 SHALL 只读 `ic_series` 表，SHALL NOT 在读取路径上重新计算 IC。

#### Scenario: 读取 IC 序列

- **WHEN** 客户端按因子 `MA20`、日期范围与 forward period 5 读取 IC 序列
- **THEN** 返回按日期升序的 `(trade_date, ic, rank_ic, sample_size)` 序列

#### Scenario: 汇总指标随序列返回

- **WHEN** 读取到 N 条 IC 记录
- **THEN** 同一响应内包含 IC 均值、IC 标准差、IC_IR 与 IC 正值占比

#### Scenario: 因子无持久化记录

- **WHEN** 查询的因子在 `ic_series` 中无任何记录
- **THEN** 返回空序列与空的汇总指标，且不报错

#### Scenario: 因子衰减数据

- **WHEN** 客户端请求同一因子在多个 forward period 下的 IC 均值
- **THEN** 每个持有期各返回一个汇总值，供衰减对比使用

### Requirement: 批量 IC 计算

系统 SHALL 提供批量 IC 计算函数，一次读取区间内的因子值与日线数据后在内存中逐日计算，SHALL NOT 按交易日逐次查询数据库。

批量计算与既有的逐日 `compute_ic_series` SHALL 共用同一套相关系数实现，且在相同输入上 SHALL 给出相同的逐日结果。

#### Scenario: 查询次数与区间长度无关

- **WHEN** 对 F 个因子、跨越 D 个交易日的区间执行批量 IC 计算
- **THEN** 数据库往返次数不随 D 增长（每个因子的整个区间一次批量取数），但在 F 维度上按因子逐次取数

#### Scenario: 按因子取数以保留取消边界

- **WHEN** IC 计算任务逐因子执行
- **THEN** 每个因子的取数是该因子的完整区间，取消检查落在因子边界上，使已完成因子的记录得以保留

#### Scenario: 两条路径结果一致

- **WHEN** 对同一 fixture 数据分别用批量函数与逐日 `compute_ic_series` 计算同一因子同一 forward period
- **THEN** 两份逐日 IC 结果逐一相等

#### Scenario: 逐日契约不变

- **WHEN** 检查 `compute_ic_series` 的签名
- **THEN** 其参数与返回值结构与本变更前一致，既有调用方无需修改

### Requirement: IC 计算任务

系统 SHALL 通过任务类型 `factor_ic` 执行 IC 计算，参数包含因子列表、日期范围、forward period 列表与股票池。任务 SHALL 在因子循环头部检查取消请求并上报进度，完成后 SHALL 在 `artifact` 表中登记 `ic_series` 表产物。

#### Scenario: 提交 IC 计算任务

- **WHEN** 客户端以 `kind: factor_ic` 提交计算请求
- **THEN** 系统返回 202 与 `run_id`，任务执行完毕后 `ic_series` 中可见对应记录

#### Scenario: 进度与取消

- **WHEN** IC 计算任务运行期间用户请求取消
- **THEN** 服务在下一个因子的循环头部停止，已完成的因子记录保留，任务状态为 `cancelled`

#### Scenario: 产物登记

- **WHEN** IC 计算任务正常完成
- **THEN** `artifact` 中登记一行，`storage` 为 `table`，`ref` 为 `ic_series`，`row_count` 为写入行数

#### Scenario: 参数可复现

- **WHEN** 读取该运行的 `params_json`
- **THEN** 其可被反序列化为 IC 计算参数对象，用于重跑同一次计算
