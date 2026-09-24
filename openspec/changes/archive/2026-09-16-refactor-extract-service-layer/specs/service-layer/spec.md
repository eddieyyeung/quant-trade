## ADDED Requirements

### Requirement: 服务层作为唯一领域入口

系统 SHALL 在 `quant_trade.services` 包中提供所有研究领域操作的函数入口，覆盖数据同步、因子计算、策略信号、模型训练、回测、报告生成六类。任何调用方（HTTP API、脚本、REPL）SHALL 通过该包调用领域逻辑，SHALL NOT 自行实现参数解析与领域计算的组合。

#### Scenario: 服务模块覆盖全部研究领域

- **WHEN** 检查 `quant_trade.services` 包
- **THEN** 存在 `data` / `factors` / `strategies` / `models` / `backtest` / `report` / `queries` 模块，每个模块导出该领域的公开服务函数

#### Scenario: 适配器不含领域逻辑

- **WHEN** 检查任一调用方（HTTP 路由、脚本）
- **THEN** 该调用方只做参数构造与结果序列化，不包含因子计算、回测循环、数据清洗等逻辑

#### Scenario: 服务层不依赖进程级全局状态

- **WHEN** 服务函数被调用
- **THEN** 其输入全部来自 `params` 与 `ctx` 参数，不读取 `sys.argv`，不向 stdout 打印

### Requirement: RunContext 契约

系统 SHALL 提供 `RunContext` 数据类，至少包含 `run_id` / `config` / `store` 三个字段与 `progress()` / `log()` / `cancelled()` 三个方法。服务函数 SHALL 通过该对象上报进度、写入日志、查询取消状态。

#### Scenario: 进度上报

- **WHEN** 服务函数调用 `ctx.progress(0.5, "已完成 500/1000 只股票")`
- **THEN** 进度值与消息被传递给调用方注册的接收器，且服务函数继续执行

#### Scenario: 日志上报

- **WHEN** 服务函数调用 `ctx.log("同步失败，回退到备用源", level="warning")`
- **THEN** 该消息以指定级别传递给调用方注册的接收器

#### Scenario: 无接收器的默认上下文

- **WHEN** 服务函数在未提供 `ctx` 的情况下被调用（使用模块级 `NULL_CONTEXT` 默认值）
- **THEN** 调用正常完成，`progress()` 与 `log()` 为空操作，`cancelled()` 恒返回 `False`

#### Scenario: 函数签名默认值

- **WHEN** 检查任一长任务服务函数的签名
- **THEN** `ctx` 参数具备默认值，使测试与脚本可无 ctx 调用

### Requirement: 协作式取消

系统 SHALL 支持协作式取消：服务函数 SHALL 在约定的循环边界检查 `ctx.cancelled()`，检测到取消后停止后续拉取或计算，并在返回前完成资源清理。系统 SHALL NOT 使用抢占式中断。

#### Scenario: 数据同步中取消

- **WHEN** 股票同步循环执行期间 `ctx.cancelled()` 返回 `True`
- **THEN** 服务函数在当次迭代结束后停止循环，关闭数据源适配器连接，返回已完成部分的结果

#### Scenario: 回测中取消

- **WHEN** 周度回测循环执行期间 `ctx.cancelled()` 返回 `True`
- **THEN** 服务函数停止后续周的信号生成与交易执行，返回截至当前周的净值序列

#### Scenario: 模型训练中取消

- **WHEN** walk-forward 窗口循环执行期间 `ctx.cancelled()` 返回 `True`
- **THEN** 服务函数停止后续窗口的训练，返回已完成窗口的预测结果

#### Scenario: 取消检查点位置

- **WHEN** 检查各长任务服务函数的循环结构
- **THEN** 取消检查位于循环体开头：数据同步的股票循环、因子计算的因子循环、回测的周循环、模型训练的窗口循环

### Requirement: 运行参数对象

每个服务函数 SHALL 接受一个 pydantic `BaseModel` 子类作为参数对象，该对象 SHALL 可序列化为 JSON 并可从 JSON 反序列化，且 SHALL 在领域计算开始前完成校验。

#### Scenario: 参数可序列化往返

- **WHEN** 构造一个参数对象并调用 `model_dump_json()`，随后用 `model_validate_json()` 反序列化
- **THEN** 得到等价的参数对象，可用于重跑同一任务

#### Scenario: 非法参数提前拦截

- **WHEN** 构造参数对象时传入非法值（如 `start` 晚于 `end`）
- **THEN** 在服务函数执行任何领域计算之前抛出校验错误

#### Scenario: 参数默认值来源于配置

- **WHEN** 参数对象的某字段未显式提供
- **THEN** 使用 `AppConfig` 中对应配置节的默认值

### Requirement: 表元数据查询接口

系统 SHALL 提供查询数据表元信息的接口，返回表行数、日期范围与领域实体列表。调用方 SHALL NOT 直接书写裸 SQL 获取此类信息。

#### Scenario: 通用表统计

- **WHEN** 调用 `DataStore.table_stats(table)` 且传入 `daily_kline`
- **THEN** 返回该表行数、最早交易日、最晚交易日

#### Scenario: 非法表名被拒绝

- **WHEN** 调用 `table_stats()` 传入不在允许列表中的表名
- **THEN** 抛出参数错误，不执行任何 SQL

#### Scenario: 因子名列表

- **WHEN** 调用因子名列表查询接口
- **THEN** 返回 `factor_values` 表中实际存在的去重因子名

#### Scenario: 股票池覆盖率

- **WHEN** 调用覆盖率查询接口并指定股票池与日期
- **THEN** 返回该股票池中已同步日线数据的股票数与占比

### Requirement: 模型训练输出特征重要性

`walk_forward_train` SHALL 返回包含 `predictions` / `feature_matrix` / `feature_importance` 的结果对象。特征重要性 SHALL 按 walk-forward 窗口聚合（均值与标准差）。

#### Scenario: 训练返回特征重要性

- **WHEN** `walk_forward_train` 完成全部窗口训练
- **THEN** 返回对象包含特征重要性表，字段为因子名、窗口均值、窗口标准差，按均值降序排列

#### Scenario: 特征矩阵不再被丢弃

- **WHEN** 调用方接收训练结果
- **THEN** 特征矩阵与特征重要性均通过具名字段可访问，不依赖位置解包

#### Scenario: 单窗口训练

- **WHEN** 回测区间仅容纳一个 walk-forward 窗口
- **THEN** 特征重要性表仍正常返回，标准差为 0
