## MODIFIED Requirements

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
