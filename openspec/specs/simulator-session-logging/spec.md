## Purpose

TBD — see design.md for architecture context.

## Requirements

### Requirement: API 层记录会话创建请求
系统 SHALL 在接收到 `POST /api/sessions` 请求时，使用 INFO 级别记录请求参数（name, start_date, end_date, capital, reference_strategy）。

#### Scenario: 完整参数请求
- **WHEN** 客户端发送 `POST /api/sessions?name=测试&start_date=2023-06-01&end_date=2025-12-31&capital=200000&ref=momentum_rotation`
- **THEN** 日志输出包含 `name=测试, start=2023-06-01, end=2025-12-31, capital=200000, ref=momentum_rotation`

#### Scenario: 最小参数请求
- **WHEN** 客户端发送 `POST /api/sessions` 不带任何查询参数
- **THEN** 日志输出包含默认参数值 `name=未命名会话, capital=100000`

### Requirement: API 层记录会话创建耗时与结果
系统 SHALL 在会话创建成功时使用 INFO 级别记录 session_id、总周数和耗时（毫秒）；在创建失败时使用 ERROR 级别记录异常信息和请求参数。

#### Scenario: 创建成功
- **WHEN** `sim.create()` 返回结果
- **THEN** 日志输出包含 `session_id=<uuid>, total_weeks=<N>, elapsed=<X>ms`

#### Scenario: 创建失败
- **WHEN** `sim.create()` 抛出异常
- **THEN** 日志输出包含异常类型、异常消息和请求参数

### Requirement: 引擎层记录关键步骤进度
系统 SHALL 在 `Simulator.create()` 执行的每个关键步骤使用 INFO 级别记录进度：入口参数、日期范围解析、日历构建、策略加载、数据准备、快照构建。

#### Scenario: 创建入口
- **WHEN** `Simulator.create()` 被调用
- **THEN** 日志输出包含 `name`, `start`, `end`, `capital`

#### Scenario: 日期范围解析
- **WHEN** end_date 为 None 需要自动解析
- **THEN** 日志输出 `Resolving latest trade date...` 和解析结果

#### Scenario: 日历数据获取
- **WHEN** 开始查询 trade_calendar 表
- **THEN** 日志输出 `Fetching calendar data: <start> → <end>`，查询完成后输出 `Calendar data fetched: N trade dates`

#### Scenario: 周调度计算
- **WHEN** 开始执行 `weeks_between` 循环
- **THEN** 日志输出 `Computing week schedule...`

#### Scenario: 日历就绪
- **WHEN** 日历查询和调度计算完成
- **THEN** 日志输出包含周数 `N weeks` 和首个周五日期 `first_friday=YYYY-MM-DD`

#### Scenario: 参考策略加载
- **WHEN** 指定了 reference_strategy 且加载成功
- **THEN** 日志输出 `Reference strategy loaded: <name>`

#### Scenario: 参考策略未找到
- **WHEN** 指定了 reference_strategy 但 registry 中不存在
- **THEN** 日志输出 WARNING 级别消息（已有行为保持）

#### Scenario: 股票池就绪
- **WHEN** `get_universe` 返回结果
- **THEN** 日志输出 `Universe ready: N stocks`

#### Scenario: 开始构建快照
- **WHEN** 进入 snapshot 构建阶段
- **THEN** 日志输出 `Building initial snapshot...`

### Requirement: 持久层记录子步骤细节
系统 SHALL 在 `SessionStore.create()` 的子步骤使用 DEBUG 级别记录：目录创建、decisions.json 写入、数据库行插入。

#### Scenario: 目录创建
- **WHEN** session 目录创建成功
- **THEN** DEBUG 日志输出目录路径

#### Scenario: decisions 文件初始化
- **WHEN** decisions.json 写入空数组完成
- **THEN** DEBUG 日志输出文件路径

#### Scenario: DB 行插入
- **WHEN** INSERT 语句执行成功
- **THEN** DEBUG 日志输出 session_id

### Requirement: 快照构建记录子模块进度
系统 SHALL 在 `SnapshotBuilder.build_snapshot()` 的四个子模块使用 INFO 级别记录开始执行：市场概览构建、持仓快照构建、因子排名构建、策略信号构建。在入口处使用 INFO 记录 cursor_date 和 universe 大小。

#### Scenario: 快照构建入口
- **WHEN** `build_snapshot` 被调用
- **THEN** INFO 日志输出 `cursor_date` 和 `universe_size`

#### Scenario: 子模块开始执行
- **WHEN** 进入 `_build_market_overview`、`_build_portfolio_snapshot`、`_build_factor_ranking`、`_build_strategy_signals` 的任一步骤
- **THEN** INFO 日志输出子模块名称

#### Scenario: 因子排名完成
- **WHEN** 因子计算和排名完成
- **THEN** DEBUG 日志输出因子数量和排名条目数

### Requirement: 快照构建逐因子进度
系统 SHALL 在 `_build_factor_ranking` 计算每个因子时使用 INFO 级别记录进度，使调用方能感知长时间计算未卡死。

#### Scenario: 因子计算开始
- **WHEN** 进入因子计算循环
- **THEN** INFO 日志输出 `Computing N factors for M stocks...`

#### Scenario: 逐个因子进度
- **WHEN** 开始计算第 i 个因子
- **THEN** INFO 日志输出 `Factor i/N: <factor_name>...`
