## ADDED Requirements

### Requirement: 策略基类接口

系统 SHALL 定义 `Strategy` 抽象基类，所有策略实现 `generate_signals(date, universe, data)` 方法，接收调仓日期、候选股票池、数据访问对象，返回 `SignalResult`。

#### Scenario: 策略返回交易信号

- **WHEN** 策略的 `generate_signals("2026-07-24", universe, data_store)` 被调用
- **THEN** 系统返回 `SignalResult` 对象，包含 `orders` 列表和 `weights` 字典

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

系统 SHALL 提供与因子一致的策略注册表机制，`@register_strategy(name)` 装饰器注册策略类，CLI 和配置中按名称引用。

#### Scenario: 按配置名加载策略

- **WHEN** 配置 `strategy.name: "factor_ranking"`
- **THEN** 系统通过 `strategy_registry.get("factor_ranking")` 获取策略类实例

### Requirement: 策略参数化配置

系统 SHALL 支持通过 YAML 配置文件或 Pydantic model 传递策略参数，运行时无需修改代码。

#### Scenario: YAML 配置驱动策略

- **WHEN** `config/strategy.yaml` 定义 `name: factor_ranking`, `top_n: 20`, `enabled: [momentum_20d, roe_ttm]`
- **THEN** 策略按配置运行，Top-20 选股，仅使用两个因子
