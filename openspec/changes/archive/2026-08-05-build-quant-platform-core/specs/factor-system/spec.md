## ADDED Requirements

### Requirement: 因子基类定义

系统 SHALL 提供 `Factor` 抽象基类，所有因子继承此基类并实现 `compute(date, universe)` 方法，返回以 ts_code 为 index、因子值为 value 的 pandas Series。

#### Scenario: 实现新因子

- **WHEN** 开发者创建一个继承 `Factor` 的类并实现 `compute` 方法
- **THEN** 该因子立即可被策略和因子分析工具调用，无需修改框架代码

### Requirement: 因子注册表

系统 SHALL 提供装饰器 `@register(name)` 将因子类注册到全局注册表，策略配置中通过因子名称字符串引用。

#### Scenario: 按名称查找因子

- **WHEN** 策略配置 `enabled: ["momentum_20d", "roe_ttm"]`
- **THEN** 系统通过 `registry.get("momentum_20d")` 获取对应因子实例，按配置顺序计算

### Requirement: 内置动量因子

系统 SHALL 内置以下动量类因子：

| 因子名 | 描述 | 参数 |
|--------|------|------|
| `momentum_20d` | 20日收益率 | 自然对数收益率 |
| `momentum_60d` | 60日收益率 | 自然对数收益率 |
| `ma_deviation` | 收盘价相对60日均线偏离 | 偏离百分比 |

#### Scenario: 计算动量因子

- **WHEN** 对全市场 `universe` 在某交易日调用 `momentum_20d.compute(date, universe)`
- **THEN** 系统返回每只股票的近 20 个交易日对数收益率 Series，缺失值跳过

### Requirement: 内置价值因子

系统 SHALL 内置以下价值类因子：

| 因子名 | 描述 | 参数 |
|--------|------|------|
| `pb_ratio` | 市净率倒数（1/PB） | 财报数据最近一期 |
| `pe_ratio` | 市盈率倒数（1/PE） | 剔除负值 |
| `dividend_yield` | 股息率 | 近12个月分红/市值 |

#### Scenario: PB因子计算

- **WHEN** 对全市场 `universe` 调用 `pb_ratio.compute(date, universe)`
- **THEN** 系统从 `financials` 表读取最近一期 PB，返回 `1/PB` 作为因子值，PB 越高因子值越低

### Requirement: 内置质量因子

系统 SHALL 内置以下质量类因子：

| 因子名 | 描述 | 参数 |
|--------|------|------|
| `roe_ttm` | 净资产收益率 TTM | 财务数据最近一期 |
| `revenue_yoy` | 营业收入同比增长率 | 财务数据最近一期 |

#### Scenario: ROE因子计算

- **WHEN** 调用 `roe_ttm.compute(date, universe)`
- **THEN** 系统从 `financials` 表读取最近一期 ROE 值，以 `ann_date <= date` 过滤避免未来信息

### Requirement: 因子预处理管线

系统 SHALL 提供可配置的因子预处理管线，包括去极值（MAD/Z-Score）、缺失值填充（行业中位数）、标准化（Z-Score）、行业中性化。

#### Scenario: 标准预处理流程

- **WHEN** 用户调用 `preprocess(factor_values, steps=["winsorize", "standardize"])`
- **THEN** 系统依次执行：3倍MAD去极值 → Z-Score标准化 → 返回处理后 Series

#### Scenario: 行业中性化

- **WHEN** 用户调用 `preprocess(factor_values, steps=["neutralize"])`
- **THEN** 系统对因子值做行业虚拟变量回归，取残差作为行业中性化后的因子值

### Requirement: 因子有效性分析

系统 SHALL 提供 IC（Information Coefficient）和 RankIC 分析工具，计算因子值与下期收益的相关性。

#### Scenario: 计算单期IC

- **WHEN** 用户调用 `analyze_ic(factor_values, forward_returns, method="pearson")`
- **THEN** 系统返回该期 Pearson IC 值，以及 RankIC（Spearman 秩相关系数）

#### Scenario: IC序列统计

- **WHEN** 用户调用 `analyze_ic_series(factor_name, start_date, end_date)`
- **THEN** 系统返回：IC均值、IC标准差、ICIR（IC均值/IC标准差）、IC>0的比例、累计IC曲线
