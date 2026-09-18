## Purpose

因子层是选股的计算单元：把行情与财务数据折算成每只股票一个可排序的分数。

本能力规定因子的写法（`Factor` 基类的 `compute` 契约）、因子的登记与取用方式（`@register` 装饰器与按名称查找的注册表）、系统内置的三类因子（动量、价值、质量）及其参数，以及因子值进入策略之前的两道加工——预处理管线与分析工具。策略与因子分析只通过名称引用因子，不感知其实现，因此新增因子无需改动框架代码。

因子本身不含选股决策：如何排序、取前几名、如何约束行业权重，属于 `strategy-engine`。因子如何拿到数据库、未拿到时的兜底行为，属于 `factor-store-injection`。

## Requirements

### Requirement: 因子基类定义

系统 SHALL 提供 `Factor` 抽象基类，所有因子继承此基类并实现 `compute(date, universe)` 方法，返回以 ts_code 为 index、因子值为 value 的 pandas Series。

因子 SHALL 从构造方接收数据访问对象，SHALL NOT 在自身内部构造一个默认的数据库连接。构造参数 `store` SHALL 为可选，以便不接触数据的因子无需传入。

当 `store` 未给出时，因子 SHALL 按 `factor-store-injection` 的兜底要求解析配置指定的数据库并留下警告，SHALL NOT 落到一个与配置无关的固定路径。

#### Scenario: 实现新因子

- **WHEN** 开发者创建一个继承 `Factor` 的类并实现 `compute` 方法
- **THEN** 该因子立即可被策略和因子分析工具调用，无需修改框架代码

#### Scenario: 因子使用收到的数据访问对象

- **WHEN** 因子被以某个数据访问对象构造
- **THEN** 该因子的 `compute` 读取的是这个对象，SHALL NOT 是另一个自行构造的

#### Scenario: 未收到数据访问对象时告警

- **WHEN** 因子在未给出 `store` 的情况下被构造
- **THEN** 它仍然可用，但日志中出现一条含解析路径的警告（详见 `factor-store-injection`）
### Requirement: 因子注册表

系统 SHALL 提供装饰器 `@register(name)` 将因子类注册到全局注册表，策略配置中通过因子名称字符串引用。

注册表的取用接口 SHALL 接受一个可选的数据访问对象并透传给被实例化的因子类，使调用方无须先行构造因子再自行注入。该接口 SHALL 与策略注册表保持同一形状。

#### Scenario: 按名称查找因子

- **WHEN** 策略配置 `enabled: ["momentum_20d", "roe_ttm"]`
- **THEN** 系统通过 `registry.get("momentum_20d")` 获取对应因子实例，按配置顺序计算

#### Scenario: 取用时注入数据访问对象

- **WHEN** 调用方以 `registry.get("momentum_20d", store=some_store)` 取用因子
- **THEN** 返回的因子持有 `some_store`，SHALL NOT 另行构造一个默认连接

#### Scenario: 未指定数据访问对象时仍可取出

- **WHEN** 调用方只给出名称
- **THEN** 因子仍被返回，其数据访问对象按 `factor-store-injection` 的兜底规则解析并留下警告
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
