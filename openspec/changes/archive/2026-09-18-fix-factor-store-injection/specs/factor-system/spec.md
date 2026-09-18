## MODIFIED Requirements

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
