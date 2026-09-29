# backtest-result-store Specification

## Purpose
回测结果持久化：以 `run_id` 为键把净值/基准/回撤序列、交易明细、绩效指标与期末持仓写入 DuckDB 四张结果表，取消的运行保留已完成部分、基准缺失不丢净值行，并提供运行列表、详情、交易明细分页与多运行对比的服务层读取入口，结果只增不改。
## Requirements
### Requirement: 回测结果表结构

系统 SHALL 在 DuckDB 中持久化回测结果，使用四张表：`backtest_nav`（净值 / 基准 / 回撤序列）、`backtest_trade`（交易明细）、`backtest_metric`（绩效指标键值对）、`backtest_position`（期末持仓）。四张表 SHALL 以 `run_id` 作为主键的第一列，使同一次运行的结果整体可寻址。

表 SHALL 由 `data/schema.py` 的 `SCHEMA_SQL` 以 `CREATE TABLE IF NOT EXISTS` 建立，SHALL NOT 依赖 `ALTER TABLE`。

#### Scenario: 净值表主键

- **WHEN** 同一个 `run_id` 的净值序列被写入两次
- **THEN** `backtest_nav` 中该 `(run_id, trade_date)` 只有一行，值为后写入者，不产生重复行

#### Scenario: 交易明细按序号定位

- **WHEN** 一次运行产生了 N 笔交易
- **THEN** `backtest_trade` 中存在 N 行，`seq` 从 1 连续递增，可按键 `(run_id, seq)` 有序读回

#### Scenario: 指标以键值对存储

- **WHEN** 回测计算出一个新的绩效指标
- **THEN** 该指标以一行 `(run_id, metric_name, metric_value)` 写入，无需修改表结构

#### Scenario: 期末持仓独立成表

- **WHEN** 回测结束时组合仍持有股票
- **THEN** 每只持仓在 `backtest_position` 中占一行，含代码、股数、成本价、现价与市值

#### Scenario: 空结果不写行

- **WHEN** 回测因区间内无交易日或股票池为空而返回空结果
- **THEN** 四张表均不写入该 `run_id` 的行，且该运行不登记产物

### Requirement: 落库以运行标识为键

回测服务 SHALL 在 `RunContext.run_id` 非空时将结果写入四张表。当 `run_id` 为空（`NULL_CONTEXT`，即脚本与测试的默认上下文）时，服务 SHALL 正常返回内存结果且 SHALL NOT 写入任何行。

#### Scenario: 后台任务执行时落库

- **WHEN** 回测由后台 worker 执行，`ctx.run_id` 为一个运行标识
- **THEN** 该次运行的净值、交易、指标、持仓均写入对应表，且表的 `run_id` 列等于该标识

#### Scenario: 脚本调用不写库

- **WHEN** 以 `NULL_CONTEXT` 调用回测服务（`run_id` 为空串）
- **THEN** 服务返回完整的 `BacktestResult`，且四张表的行数不变

#### Scenario: 两次脚本调用不互相覆盖

- **WHEN** 以 `NULL_CONTEXT` 连续调用回测服务两次，参数不同
- **THEN** 第二次调用的结果不覆盖第一次的任何已存数据

### Requirement: 取消的回测保留已完成部分

被取消的回测 SHALL 持久化截至取消点已产生的净值与交易，SHALL NOT 因为未跑完而丢弃全部结果。

#### Scenario: 取消后仍可查询

- **WHEN** 一次回测在第 30 周被取消并落库
- **THEN** `backtest_nav` 中存在截至取消日的净值行，`run.status` 为 `cancelled`

#### Scenario: 取消不写入未执行区间

- **WHEN** 回测在第 30 周被取消
- **THEN** `backtest_nav` 中不存在第 31 周及之后的日期行，净值曲线不在中途截断后延展为平线

### Requirement: 基准缺失不影响净值行

净值序列 SHALL 逐日写入。基准序列 SHALL 按交易日与净值对齐后写入；某日基准缺失时该行 SHALL 仍被写入，`benchmark` 列 SHALL 为 NULL，SHALL NOT 丢弃该净值行。

#### Scenario: 基准数据短于净值

- **WHEN** 基准指数在区间末尾缺少若干交易日的数据
- **THEN** `backtest_nav` 仍包含这些日期的行，其 `benchmark` 为 NULL，`nav` 与 `drawdown` 正常

#### Scenario: 回撤随净值一并落库

- **WHEN** 净值序列被写入
- **THEN** 每一行的 `drawdown` 为该日相对历史峰值的回撤比例，与净值序列同源

### Requirement: 回测结果查询接口

系统 SHALL 在服务层提供回测结果的读取入口，返回结构化结果而非要求调用方书写 SQL：运行列表（含状态、策略、实际区间与关键指标，服务端分页）、单个运行的详情（元信息、指标、净值序列、期末持仓）、交易明细（服务端分页）、多个运行的对比（净值叠加与指标对照）。

读取路径 SHALL NOT 重新执行回测。

#### Scenario: 列表包含运行状态

- **WHEN** 查询回测运行列表
- **THEN** 每行包含 `run_id`、运行状态、策略名、净值覆盖的实际起止日与关键绩效指标

#### Scenario: 列表按时间倒序并分页

- **WHEN** 回测运行数超过一页
- **THEN** 返回该页数据与总数，按提交时间倒序，SHALL NOT 一次返回全部行

#### Scenario: 详情不重跑回测

- **WHEN** 打开一个已完成运行的回测详情
- **THEN** 指标、净值与持仓全部来自结果表，不触发周循环

#### Scenario: 交易明细分页

- **WHEN** 一次运行有上万笔交易
- **THEN** 交易明细接口按页返回，响应体积不随总笔数增长

#### Scenario: 对比读取多条净值

- **WHEN** 请求对比 3 个运行
- **THEN** 返回 3 条净值序列与各自指标，一次请求完成，不产生按运行的逐次请求

#### Scenario: 查询不存在的运行

- **WHEN** 请求一个不属于回测任务的 `run_id`（无该运行记录，或该记录是其他任务类型）
- **THEN** 返回未找到，SHALL NOT 返回空曲线冒充存在

#### Scenario: 有运行记录但没有结果

- **WHEN** 请求一个存在但未产出结果的回测运行（例如空区间而失败）
- **THEN** 返回该运行的状态与空的净值序列，SHALL NOT 因无结果而报未找到——那次运行确实被提交过

#### Scenario: 实际区间取自净值覆盖范围

- **WHEN** 请求的起始日被归一化到其后的第一个交易日
- **THEN** 列表与详情展示的起始日为净值序列的首日，而非请求参数中的日期

### Requirement: 结果只增不改

系统 SHALL 以一次运行对应一份结果的语义存储回测结果。重跑同一参数 SHALL 产生新的 `run_id` 与新的一份结果，SHALL NOT 覆盖既有运行的结果。

#### Scenario: 同参数重跑

- **WHEN** 用完全相同的参数提交两次回测
- **THEN** 结果表中存在两组以不同 `run_id` 为键的行，两组数据都可通过各自的 `run_id` 读回

#### Scenario: 结果表纳入数据总览

- **WHEN** 打开数据总览页
- **THEN** 回测结果表出现在表清单中，展示行数与日期范围；无数据时展示为 0 行
