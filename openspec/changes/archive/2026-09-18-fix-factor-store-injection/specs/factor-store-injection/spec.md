## ADDED Requirements

### Requirement: 需要数据的组件从调用方接收数据访问对象

需要读写市场数据的组件（因子、策略、回测引擎）SHALL 从调用方接收数据访问对象，SHALL NOT 自行构造一个默认的数据库连接。

注册表工厂 SHALL 支持把数据访问对象透传给被实例化的组件：`get(name, store=...)` 是一个统一形状，因子注册表与策略注册表 SHALL 一致。

工厂 SHALL 以关键字传递数据访问对象，SHALL NOT 依赖其构造参数的位置。

#### Scenario: 因子从调用方接收数据访问对象

- **WHEN** 策略通过注册表构造一个因子
- **THEN** 该因子持有的数据访问对象是策略收到的那个，SHALL NOT 是另一个自行构造的

#### Scenario: 策略从调用方接收数据访问对象

- **WHEN** 服务层通过策略工厂构造一个策略
- **THEN** 该策略持有的数据访问对象是服务层上下文里的那个

#### Scenario: 两个注册表形状一致

- **WHEN** 检查因子注册表与策略注册表的取用接口
- **THEN** 两者都接受一个可选的数据访问对象参数并透传给被实例化的类

#### Scenario: 不接收数据访问对象的组件实例化即失败

- **WHEN** 一个未声明接收数据访问对象的策略类被注册表以该参数实例化
- **THEN** 实例化抛出错误，SHALL NOT 静默地让该策略去用别的数据库

### Requirement: 未注入时的兜底解析配置而非硬编码路径

当组件未收到数据访问对象时，兜底构造 SHALL 解析**配置指定的**数据库路径，SHALL NOT 使用硬编码的字面量路径。路径解析 SHALL 复用应用既有的规则（`QUANT_CONFIG` 环境变量，否则默认配置文件），SHALL NOT 另立一套。

#### Scenario: 兜底读到配置指定的库

- **WHEN** 配置指定的数据库不是仓库自带的那个，而某处走到了兜底构造
- **THEN** 该处读到的是配置指定的库，SHALL NOT 是仓库自带的库

#### Scenario: 解析规则与应用一致

- **WHEN** 设置 `QUANT_CONFIG` 指向另一份配置
- **THEN** 兜底构造解析出的路径与进程启动时使用的数据库路径相同

#### Scenario: 显式给出路径时不走配置

- **WHEN** 调用方显式给出数据库路径
- **THEN** 使用该路径，SHALL NOT 读取配置

### Requirement: 隐式兜底留下可观测的痕迹

走到兜底分支（调用方未注入数据访问对象）时，系统 SHALL 记录一条警告，SHALL NOT 静默处理。

警告 SHALL 指出解析出的路径，SHALL NOT 只说明「使用了默认值」而不说默认值是什么。

本要求的目的 SHALL 是让遗漏注入在日志中自行现身：该遗漏的后果是数据来源错误，而不是异常，因此没有痕迹就无从发现。

#### Scenario: 兜底时告警

- **WHEN** 某组件未收到数据访问对象而走到兜底分支
- **THEN** 日志中出现一条警告，且其中含解析出的数据库路径

#### Scenario: 正常注入时不告警

- **WHEN** 组件正常收到调用方传入的数据访问对象
- **THEN** 日志中不出现该警告

#### Scenario: 告警不阻断执行

- **WHEN** 兜底分支被走到，且配置可解析出一份数据库路径
- **THEN** 组件继续以该数据库正常工作，SHALL NOT 因告警而抛出异常中断调用

#### Scenario: 配置不可解析时失败而非另选一个库

- **WHEN** 兜底分支被走到，而配置既不存在也无法解析（例如进程的工作目录下没有配置文件）
- **THEN** 构造失败并抛出，SHALL NOT 转而使用某个与配置无关的固定路径

这一条是**刻意**的例外，也是上面「告警不阻断执行」的边界：兜底之所以不抛异常，是为了在**能确定**用哪个库时不打断调用方；而当连该用哪个库都无从确定时，静默挑一个正是本变更要根除的行为。<br>
代价是 `DataStore()` 从此依赖进程环境：它要求配置文件可解析，否则抛 `FileNotFoundError`。这是把一个隐式的错换成一次显式的失败。

## MODIFIED Requirements

### Requirement: FactorRegistry.get accepts optional store parameter

`FactorRegistry.get(name, store=None)` SHALL accept an optional `store` parameter of type `DataStore | None`. When a `store` is provided, the returned Factor instance MUST use that DataStore instead of creating a new one.

When no `store` is provided, the returned instance SHALL resolve the **configured** database and log a warning naming the resolved path — see 未注入时的兜底解析配置而非硬编码路径 and 隐式兜底留下可观测的痕迹 above. It SHALL NOT fall back to a hardcoded literal path, and SHALL NOT do so silently.

#### Scenario: get with store injected

- **WHEN** caller invokes `factor_registry.get("momentum_20d", store=shared_store)`
- **THEN** the returned Factor instance's internal `store` attribute SHALL reference `shared_store`
- **AND** no new DuckDB connection SHALL be created by that Factor instance

#### Scenario: get without a store resolves the configured database

- **WHEN** caller invokes `factor_registry.get("momentum_20d")` without a store argument
- **THEN** the returned Factor instance SHALL construct its own DataStore by resolving the configured database path
- **AND** a warning naming the resolved path SHALL be logged

### Requirement: Factor compute uses injected store

When a Factor instance is created with an injected DataStore, all `compute()` method calls SHALL use that store for data queries, producing results identical to those from a store opened on the same database file.

#### Scenario: compute result equivalence

- **WHEN** `Factor.compute(date, universe)` is called on an instance with injected store `S1`
- **THEN** the returned pd.Series SHALL be equal to the result from an instance constructed with `DataStore(<the same database file>)`

#### Scenario: 因子基类的兜底同样生效

- **WHEN** 一个只实现 `compute`、未自行声明构造参数的 `Factor` 子类被构造
- **THEN** 它持有的数据访问对象按上面的兜底规则解析，且该因子可正常参与计算，SHALL NOT 在首次查询时因数据访问对象为空而失败
