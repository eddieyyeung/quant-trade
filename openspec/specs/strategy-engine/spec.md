## Purpose

因子给出分数，本能力把分数变成持仓。

它规定策略的写法（`Strategy` 基类的 `generate_signals` 契约与 `SignalResult` 的返回结构）、策略的登记与取用（`@register_strategy` 与按名称查找的注册表）、系统内置的多因子打分排名策略及其参数（因子权重、Top-N、行业集中度上限），以及策略参数如何从配置读取。

策略只拿到「哪些股票、各占多少」这一层结果，不含撮合、费用与涨跌停处理——那些是 `backtest-engine`。策略从哪个数据库取数由 `factor-store-injection` 规定，策略内部构造的因子必须持有与策略同一个数据访问对象。

## Requirements

### Requirement: 策略基类接口

系统 SHALL 定义 `Strategy` 抽象基类，所有策略实现 `generate_signals(date, universe, data)` 方法，接收调仓日期、候选股票池、数据访问对象，返回 `SignalResult`。

基类 SHALL 提供以数据访问对象为可选参数的默认构造，使「策略持有哪个数据库」成为基类契约，SHALL NOT 要求每个子类各自记得声明。

策略在 `generate_signals` 内部构造的因子参与计算时，SHALL 把**它自己收到的**数据访问对象交给这些因子，SHALL NOT 让因子各自去开默认库。策略自身亦 SHALL NOT 在内部构造默认连接。

#### Scenario: 策略返回交易信号

- **WHEN** 策略的 `generate_signals("2026-07-24", universe, data_store)` 被调用
- **THEN** 系统返回 `SignalResult` 对象，包含 `orders` 列表和 `weights` 字典

#### Scenario: 策略内部的因子用同一个库

- **WHEN** 一个策略在 `generate_signals` 中通过注册表构造因子
- **THEN** 这些因子持有的数据访问对象与传给该策略的是同一个
- **AND** 选股所使用的价格与财务数据与股票池来自同一个数据库

#### Scenario: 策略从构造方接收数据访问对象

- **WHEN** 策略被以某个数据访问对象构造
- **THEN** 该策略持有它，SHALL NOT 另行构造一个默认连接
### Requirement: SignalResult 数据结构

系统 SHALL 定义 `SignalResult` 包含目标持仓权重映射和逐笔交易信号，以及每笔交易的理由说明。`Order` 数据类包含 ts_code、目标仓位占比、操作方向和信号理由。

#### Scenario: 生成调仓订单

- **WHEN** 策略输出 `SignalResult(orders=[Order("600519.SH", 0.067, "BUY", "综合得分92"), ...])`
- **THEN** 回测引擎可据此执行调仓，周报可展示每笔信号的理由

### Requirement: 多因子打分排名策略

系统 SHALL 提供 `FactorRankingStrategy`，对候选股票池计算多个因子的综合得分，取得分最高的 Top-N 只股票等权配置。

#### Scenario: 等权 Top-N 选股

- **WHEN** 配置 `top_n=15`，`enabled=["momentum_20d", "pb_ratio", "roe_ttm"]`
- **THEN** 系统对 universe 中每只股票计算三个因子值，标准化后等权加总得综合分，取前15只，每只分配 1/15 权重

#### Scenario: 因子权重配置

- **WHEN** 配置 `factor_weights: {momentum_20d: 0.4, pb_ratio: 0.3, roe_ttm: 0.3}`
- **THEN** 系统按指定权重加权合成综合得分

#### Scenario: 行业集中度约束

- **WHEN** 配置 `max_industry_weight: 0.30` 且 Top-15 中有 6 只同属食品饮料行业
- **THEN** 系统保留得分最高的 4 只（15 × 30% ≈ 4），其余食品饮料股剔除，递补其他行业高得分股票

### Requirement: 策略注册表

系统 SHALL 提供与因子一致的策略注册表机制，`@register_strategy(name)` 装饰器注册策略类，服务层与配置中按名称引用。

取用接口 SHALL 接受一个可选的数据访问对象并透传给被实例化的策略类，形状 SHALL 与因子注册表一致。

注册表 SHALL 以关键字传递该参数；当被实例化的策略类不接受它时，注册表 SHALL 让实例化失败，SHALL NOT 退化为静默构造一个没有数据访问对象的策略。

#### Scenario: 按配置名加载策略

- **WHEN** 配置 `strategy.name: "factor_ranking"`
- **THEN** 系统通过 `strategy_registry.get("factor_ranking")` 获取策略类实例

#### Scenario: 加载时注入数据访问对象

- **WHEN** 调用方以 `strategy_registry.get("factor_ranking", store=some_store)` 取用策略
- **THEN** 返回的策略持有 `some_store`

#### Scenario: 工厂透传数据访问对象

- **WHEN** 服务层以数据访问对象调用策略工厂
- **THEN** 工厂把该对象交给注册表取用接口，最终抵达策略实例

#### Scenario: 不接受数据访问对象的策略不会被静默降级

- **WHEN** 注册表中某个策略类未声明接收数据访问对象
- **THEN** 以该参数实例化时失败，SHALL NOT 静默构造一个无数据访问对象的实例并继续
### Requirement: 策略参数化配置

系统 SHALL 支持通过 YAML 配置文件或 Pydantic model 传递策略参数，运行时无需修改代码。

#### Scenario: YAML 配置驱动策略

- **WHEN** `config/strategy.yaml` 定义 `name: factor_ranking`, `top_n: 20`, `enabled: [momentum_20d, roe_ttm]`
- **THEN** 策略按配置运行，Top-20 选股，仅使用两个因子
