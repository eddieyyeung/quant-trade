## ADDED Requirements

### Requirement: 模型评估结果表结构

系统 SHALL 在 DuckDB 中持久化模型训练产出的评估结果，使用三张表：`model_ic_series`（逐日 RankIC 序列）、`model_feature_importance`（特征重要性）、`model_metric`（训练汇总指标键值对）。三张表 SHALL 以 `run_id` 作为主键的第一列，使同一次训练的结果整体可寻址。

模型逐日 IC SHALL 存放在 `model_ic_series` 独立表中，SHALL NOT 写入因子域既有的 `ic_series` 表（该表以 `factor_name` 为键的第一列，模型不是因子）。特征重要性 SHALL 保留每个因子的窗口均值与窗口标准差，两者由训练窗口聚合而来。

表 SHALL 由 `data/schema.py` 的 `SCHEMA_SQL` 以 `CREATE TABLE IF NOT EXISTS` 建立，SHALL NOT 依赖 `ALTER TABLE`。

#### Scenario: 逐日 IC 按运行存储

- **WHEN** 一次训练产出了 N 个有效预测日的 RankIC
- **THEN** `model_ic_series` 中存在 N 行，主键为 `(run_id, trade_date)`，每个预测日一行

#### Scenario: 模型 IC 不与因子 IC 混表

- **WHEN** 一次模型训练写入逐日 IC
- **THEN** `ic_series` 表的行数不变，模型 IC 只出现在 `model_ic_series`

#### Scenario: 特征重要性保留窗口离散度

- **WHEN** 一次训练跨多个 walk-forward 窗口
- **THEN** 每个参与训练的因子在 `model_feature_importance` 中占一行，含因子名、窗口均值与窗口标准差

#### Scenario: 重写同一运行幂等

- **WHEN** 同一个 `run_id` 的评估结果被写入两次
- **THEN** 每张表的该 `run_id` 下行数不变，值为后写入者，不产生重复行

#### Scenario: 汇总指标以键值对存储

- **WHEN** 训练计算出一个新的汇总指标
- **THEN** 该指标以一行 `(run_id, metric_name, metric_value)` 写入，无需修改表结构

#### Scenario: 空结果不写行

- **WHEN** 一次训练未产出任何预测（区间内无可训练窗口或股票池为空）
- **THEN** 三张表均不写入该 `run_id` 的行

### Requirement: 落库以运行标识为键

训练服务 SHALL 在 `RunContext.run_id` 非空时将评估结果写入三张表。当 `run_id` 为空（`NULL_CONTEXT`，即脚本与测试的默认上下文）时，服务 SHALL 正常返回内存结果且 SHALL NOT 写入任何行。

预测分 SHALL 继续以 parquet 文件形式写入参数对象给出的输出路径，SHALL NOT 单独建表存储。

#### Scenario: 后台任务执行时落库

- **WHEN** 训练由后台 worker 执行，`ctx.run_id` 为一个运行标识
- **THEN** 逐日 IC、特征重要性与汇总指标均写入对应表，且 `run_id` 列等于该标识

#### Scenario: 脚本调用不写库

- **WHEN** 以 `NULL_CONTEXT` 调用训练服务（`run_id` 为空串）
- **THEN** 服务返回完整的训练结果，且三张表的行数不变

#### Scenario: 两次脚本调用不互相覆盖

- **WHEN** 以 `NULL_CONTEXT` 连续调用训练服务两次，参数不同
- **THEN** 第二次调用的结果不覆盖第一次的任何已存数据

#### Scenario: 预测分仍写入输出路径

- **WHEN** 训练产出预测分且参数对象给出了输出路径
- **THEN** 该路径下存在包含 `ts_code` / `trade_date` / `score` 三列的 parquet 文件

#### Scenario: 无值指标不写成 NaN 行

- **WHEN** 某次训练的有效预测日不足，RankIC 汇总量无定义
- **THEN** 对应指标的值为 NULL 或不写入，SHALL NOT 写入 NaN 作为指标值

### Requirement: 取消的训练保留已算出的结果

被取消的训练 SHALL 持久化截至取消点已训练窗口产出的预测所对应的逐日 IC 与特征重要性，SHALL NOT 因为未跑完而丢弃全部结果。

训练服务在取消路径 SHALL NOT 把进度上报为 100%。

#### Scenario: 取消后仍可评估

- **WHEN** 一次训练在第 10 个窗口被取消并落库
- **THEN** `model_ic_series` 中存在该运行截至取消点的逐日 IC 行，`run.status` 为 `cancelled`

#### Scenario: 取消的运行不显示为已完成

- **WHEN** 训练在窗口循环中途被取消
- **THEN** 上报的进度停留在取消时的完成比例，SHALL NOT 被推进到 100%

#### Scenario: 取消不写入未执行区间

- **WHEN** 训练在第 10 个窗口被取消
- **THEN** `model_ic_series` 中不存在第 11 个窗口及其后的预测日行

### Requirement: 模型评估查询接口

系统 SHALL 在服务层提供模型评估结果的读取入口，返回结构化结果而非要求调用方书写 SQL：训练运行列表（含状态、请求参数摘要、实际覆盖区间与关键指标，服务端分页）、单个运行的评估（汇总指标、逐日 RankIC 序列、分年度表现、特征重要性）、指定日期的预测（全部得分与 top-N 选股）。

读取路径 SHALL NOT 重新执行训练，SHALL NOT 重新计算 RankIC。

特征重要性查询 SHALL 支持按窗口均值降序返回前 N 项，SHALL NOT 要求调用方一次取回全部因子。

分年度表现 SHALL 为逐日 RankIC 序列按自然年分组的汇总（IC 均值、IC_IR、正 IC 占比、有效天数），SHALL NOT 以组合收益为口径。

#### Scenario: 列表包含运行状态

- **WHEN** 查询训练运行列表
- **THEN** 每行包含 `run_id`、运行状态、完成窗口数、预测行数与关键评估指标

#### Scenario: 列按时间倒序并分页

- **WHEN** 训练运行数超过一页
- **THEN** 返回该页数据与总数，按提交时间倒序，SHALL NOT 一次返回全部行

#### Scenario: 评估不重跑训练

- **WHEN** 打开一个已完成运行的评估
- **THEN** 指标、IC 序列与特征重要性全部来自结果表，不触发任何窗口训练

#### Scenario: 特征重要性截断

- **WHEN** 请求特征重要性的前 N 项且 N 小于参与训练的因子数
- **THEN** 返回按窗口均值降序的前 N 项，响应体积不随因子总数增长

#### Scenario: 分年度表现来自已持久化的 IC

- **WHEN** 一次训练的 IC 序列跨越 3 个自然年
- **THEN** 分年度表现返回 3 组汇总，每组的值可由该年的逐日 IC 复算得到

#### Scenario: 查询不存在的运行

- **WHEN** 请求一个未在结果表中出现的 `run_id`
- **THEN** 返回未找到，SHALL NOT 返回空序列冒充存在

#### Scenario: 实际区间取自 IC 覆盖范围

- **WHEN** 请求区间被解析为默认起止日（`start` / `end` 未指定）
- **THEN** 列表与详情展示的区间为逐日 IC 序列覆盖的首末日期，而非请求参数中的默认值

#### Scenario: 预测查询返回全部得分

- **WHEN** 查询某日的预测
- **THEN** 返回该日全部股票的得分（按分数降序）与其中前 `top_n` 只作为选股，供表格与得分分布图共用同一份数据

### Requirement: 模型评估结果只增不改

系统 SHALL 以一次训练对应一份评估结果的语义存储模型评估结果。重跑同一参数 SHALL 产生新的 `run_id` 与新的一份结果，SHALL NOT 覆盖既有运行的结果。

#### Scenario: 同参数重跑

- **WHEN** 用完全相同的参数提交两次训练
- **THEN** 结果表中存在两组以不同 `run_id` 为键的行，两组数据都可通过各自的 `run_id` 读回

#### Scenario: 结果表纳入数据总览

- **WHEN** 打开数据总览页
- **THEN** 三张模型评估结果表出现在表清单中，展示行数与日期范围（`model_ic_series` 展示日期跨度，无日期列的两张表展示 0 边界）；无数据时展示为 0 行
