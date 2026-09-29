## ADDED Requirements

### Requirement: 策略信号表结构

系统 SHALL 在 DuckDB 中持久化策略信号生成产出的订单，使用 `strategy_signal` 表，一行一笔订单，含运行标识、序号、交易日、策略名、股票代码、方向、目标仓位与理由。表 SHALL 以 `(run_id, seq)` 为主键，使一次运行的信号整体可寻址、且同一运行重写幂等。

`seq` SHALL 由写入端按订单顺序连续编号，SHALL NOT 由数据库生成。信号顺序 SHALL 被保留——引擎给出的订单次序承载语义，重排会毁掉它。

策略名 SHALL 冗余在每一行上，SHALL NOT 要求读取方回 `run.params_json` 解析。

表 SHALL 由 `data/schema.py` 的 `SCHEMA_SQL` 以 `CREATE TABLE IF NOT EXISTS` 建立，SHALL NOT 依赖 `ALTER TABLE`。表 SHALL 纳入表元数据白名单，并在数据总览中展示行数与日期跨度。

#### Scenario: 信号按运行存储

- **WHEN** 一次信号生成产出了 N 笔订单
- **THEN** `strategy_signal` 中存在 N 行，主键为 `(run_id, seq)`，`seq` 从 1 连续到 N

#### Scenario: 订单顺序被保留

- **WHEN** 读回一次运行的信号
- **THEN** 顺序与引擎产出的一致，SHALL NOT 被按代码或方向重排

#### Scenario: 重复写入同一运行幂等

- **WHEN** 同一个 `run_id` 的信号被写入两次
- **THEN** 该 `run_id` 下的行数不变，值为后写入者，不产生重复行

#### Scenario: 表纳入数据总览

- **WHEN** 打开数据总览页
- **THEN** `strategy_signal` 出现在表清单中，展示行数与日期跨度（交易日跨度）；无数据时展示为 0 行

### Requirement: 落库以运行标识为键

系统 SHALL 在 `RunContext.run_id` 非空时把信号写入 `strategy_signal`。当 `run_id` 为空（`NULL_CONTEXT`，即脚本与测试的默认上下文）时，服务 SHALL 正常返回内存结果且 SHALL NOT 写入任何行。

信号生成 SHALL 有两条入口：一条只计算并返回结果、不落库，另一条在其之上落库。既有入口 SHALL 保持签名与行为不变，使依赖它的周报管线 SHALL NOT 因为本变更而产生任何 `strategy_signal` 行。

订单为空时 SHALL NOT 写入任何行。

#### Scenario: 后台任务执行时落库

- **WHEN** 信号生成由后台 worker 执行，`ctx.run_id` 为一个运行标识
- **THEN** 全部订单写入 `strategy_signal`，且 `run_id` 列等于该标识

#### Scenario: 脚本调用不写库

- **WHEN** 以 `NULL_CONTEXT` 调用信号生成服务
- **THEN** 服务返回完整的信号结果，且 `strategy_signal` 的行数不变

#### Scenario: 空信号不写行

- **WHEN** 一次信号生成没有产出任何订单（股票池为空或区间内无交易日）
- **THEN** `strategy_signal` 不写入该 `run_id` 的行

#### Scenario: 只计算的那条入口仍然不落库

- **WHEN** 既有信号生成入口在一个非空的 `ctx.run_id` 下被调用
- **THEN** 它返回完整结果且不写入任何行——落库是另一条入口的职责

#### Scenario: 周报管线不受影响

- **WHEN** 生成一次周报（其管线内部调用既有信号生成入口）
- **THEN** `strategy_signal` 的行数不变

### Requirement: 策略信号任务类型注册

系统 SHALL 将信号生成注册为统一运行 API 的任务类型：`kind` 为 `strategy_signals`，参数模型为信号生成的参数对象，服务函数为落库版信号生成。注册后发起信号生成 SHALL 经由统一运行接口，SHALL NOT 为此新增专用的写接口。

运行完成时系统 SHALL 登记一条指向 `strategy_signal` 表的产物记录，记录写入行数，并在元信息中携带策略名、信号日期与股票池规模。股票池规模 SHALL 只在元信息中出现——它不在表里，行上无从推导。

未产出订单的运行 SHALL NOT 登记产物。

#### Scenario: 提交信号生成任务

- **WHEN** 客户端以 `kind: strategy_signals` 与合法的参数提交到统一运行接口
- **THEN** 立即返回 202 与 `run_id`，任务进入串行队列

#### Scenario: 产物登记

- **WHEN** 信号生成产出了订单
- **THEN** 该运行登记一条指向 `strategy_signal` 的产物记录，行数等于订单数

#### Scenario: 产物携带元信息

- **WHEN** 读取该产物记录
- **THEN** 其元信息含策略名、信号日期与股票池规模

#### Scenario: 空信号不登记产物

- **WHEN** 信号生成没有产出任何订单
- **THEN** 该运行不登记任何产物记录

#### Scenario: 参数可复现

- **WHEN** 读取该运行的 `params_json`
- **THEN** 其可被反序列化为信号生成的参数对象，用于重跑同一次信号生成

#### Scenario: 进度与日志可见

- **WHEN** 信号生成任务运行中
- **THEN** 进度被上报，日志可通过既有日志流实时读取

### Requirement: 策略信号查询接口

系统 SHALL 在服务层提供策略信号的读取入口，返回结构化结果而非要求调用方书写 SQL：信号运行列表（含状态、策略名、信号日期、订单数与股票池规模，服务端分页）与单次运行的信号（分页的订单行与总数）。

读取路径 SHALL NOT 重新执行信号生成。列表 SHALL 以运行记录为主表，产物元信息 SHALL 只用于补齐股票池规模——运行记录是主体，产物是可选补充，使尚未产出结果的运行同样出现在列表中。

信号日期、策略名与订单数 SHALL 从信号表推导，SHALL NOT 从产物元信息抄写：那三项在表里就有，抄一份会造出第二份可能过期的真相。

查询不存在的运行时 SHALL 以未找到表达，SHALL NOT 返回空列表冒充存在。

#### Scenario: 列表包含运行状态

- **WHEN** 查询信号运行列表
- **THEN** 每行含 `run_id`、运行状态、策略名、信号日期与订单数

#### Scenario: 尚未产出结果的运行也在列表里

- **WHEN** 某个信号生成任务已入队或正在运行，还没有登记产物
- **THEN** 该行出现在列表中，状态与进度可见，信号日期、订单数与股票池规模留空
- **AND** 其信号详情的查询以未找到表达，SHALL NOT 返回空列表冒充存在

#### Scenario: 列按时间倒序并分页

- **WHEN** 信号运行数超过一页
- **THEN** 返回该页数据与总数，按提交时间倒序，SHALL NOT 一次返回全部行

#### Scenario: 单次信号分页返回

- **WHEN** 查询某次运行的信号且订单数超过一页
- **THEN** 返回该页订单与订单总数

#### Scenario: 查询不重跑

- **WHEN** 打开一次已完成的信号运行
- **THEN** 全部订单来自 `strategy_signal` 表，不触发任何策略执行

#### Scenario: 查询不存在的运行

- **WHEN** 请求一个未在信号表中出现的 `run_id`
- **THEN** 返回未找到，SHALL NOT 返回空序列冒充存在

### Requirement: 信号结果只增不改

系统 SHALL 以一次信号生成对应一份结果的语义存储策略信号。重跑同一参数 SHALL 产生新的 `run_id` 与新的一份记录，SHALL NOT 覆盖既有运行的结果。

#### Scenario: 同参数重跑

- **WHEN** 用完全相同的参数提交两次信号生成
- **THEN** 表中存在两组以不同 `run_id` 为键的行，两组都可通过各自的 `run_id` 读回

#### Scenario: 重跑不改变历史

- **WHEN** 在 `factor_values` 被重算之后再次生成同一日期的信号
- **THEN** 先前那次运行的信号仍可按其 `run_id` 原样读回，取值不受本次重算影响
