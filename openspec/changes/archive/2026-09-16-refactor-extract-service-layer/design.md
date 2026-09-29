## Context

当前编排路径只有一条：

```
终端 → cli.py:main() → sys.argv 手写分发 → _cmd_*(sub, config, extra)
                                              │
                                              ├─ 参数解析（_extract_arg 手写）
                                              ├─ 领域调用（sync_all / run_backtest / ...）
                                              └─ print() / Jinja2 渲染
```

领域函数本身已经是结构化的（`SignalResult` dataclass、`run_backtest` 返回 dict、`get_factor_values` 返回 DataFrame、`compute_ic_series` 返回 dict），**问题不在领域层，在于"调用领域层"这件事只发生在 CLI 内部**，且伴随三件事：

1. **参数解析与领域调用混在同一个函数体** —— `_cmd_backtest` 既解析 `--start`，又跑回测，又 print 指标
2. **进度无法上报** —— 长任务（全 A 股同步，小时级）只有 `logger.info`，没有结构化的百分比，也没有取消点
3. **读路径不存在** —— 结果要么被 `print()`，要么被丢进 Jinja2 模板。没有返回结构化对象的查询函数

平台化要解决的是 (1)(2)(3)，不是重写领域计算。

## Goals / Non-Goals

**Goals**

- 领域逻辑与调用方式解耦：同一函数被 HTTP API 调用与被脚本调用行为一致
- 长任务具备进度上报与协作式取消能力
- 运行参数成为可序列化、可校验、可 diff 的对象
- 消除 CLI 专属的裸 SQL 与打印逻辑
- 提供单一启动入口，为 C2 的平台骨架铺路

**Non-Goals**

- 不引入任务队列、持久化 run 记录、SSE —— 属于 C2
- 不改动任何因子计算、回测规则、策略逻辑的数值行为
- 不改动 `simulator/` 内部逻辑（`api.py` 仅被挂载方式改变）
- 不做前端改造
- 不保存 LightGBM 模型文件（`model predict` 走 parquet 预测分，无需重新推理）

## Decisions

### 1. 服务函数签名统一为 `fn(params, ctx) -> Result`

```python
# services/backtest.py
class BacktestParams(BaseModel):
    start: date
    end: date
    strategy: str = "factor_ranking"
    initial_capital: float = 100_000
    benchmark: str = "000300.SH"

def run_backtest_service(params: BacktestParams, ctx: RunContext) -> BacktestResult: ...
```

`params` 用 pydantic `BaseModel`（项目已依赖 pydantic v2，`AppConfig` 就是 BaseModel）。收益直接：`model_dump_json()` 存入 run 记录即可获得可复现性，`model_validate()` 在领域计算开始前拦截非法参数。

**替代方案（未采纳）**：继续用 `**kwargs` 或位置参数。否决原因——无法序列化，无法校验，平台层要重写一遍解析逻辑。

**跨字段校验**：基类提供 `model_validator(mode="after")` 拒绝 `start > end`。子类的边界字段命名不统一（`start`/`end` 与 `start_date`/`end_date`），校验器同时识别两种拼写。没有它，倒置区间会被静默接受，直到很晚才表现为空结果。

**惰性导出**：`services/__init__.py` 用 PEP 562 `__getattr__` 延迟解析，不做急切导入。急切导入会形成循环——`data/sync.py` 需要 `RunContext`，于是导入 `services` 包，而 `services.data` 又要导入 `data.sync`。惰性化同时避免只想拿一个上下文对象的调用方被迫拉进 pandas 与 LightGBM。`__all__` 与 `_MODULE_BY_NAME` 必须保持一致，已有测试保证每个导出名可解析。

### 2. `RunContext` 用显式注入，不用回调注册或事件总线

```python
@dataclass
class RunContext:
    run_id: str
    config: AppConfig
    store: DataStore
    def progress(self, pct: float, message: str) -> None: ...
    def log(self, message: str, level: str = "info") -> None: ...
    def cancelled(self) -> bool: ...
```

进度和取消必须从函数**内部**冒出来——在股票循环的每一次迭代里。回调注册或事件总线会让"谁在什么时候被通知"变成隐式契约，调试成本高。显式传参把契约写在签名上。

提供模块级单例 `NULL_CONTEXT`（无副作用实现）作为默认值，使测试与脚本可无 ctx 调用：

```python
def sync_all(store, sources, ctx: RunContext = NULL_CONTEXT) -> SyncResult: ...
```

**替代方案（未采纳）**：`ctx: RunContext | None = None` + 到处判空。否决原因——每个循环边界都要写 `if ctx:` ，噪声大且容易漏。

### 3. 取消采用协作式，检查点固定在循环边界

`ctx.cancelled()` 只在循环的每次迭代开头检查，不设抢占式中断。领域函数在检测到取消后必须：停止拉取/计算，返回已完成部分的结果，并由调用方（C2 的 worker）将 run 标记为 `cancelled`。

**检查点约定位置**：
- 数据同步：股票循环每次迭代
- 因子计算：因子循环每次迭代
- 回测：每周循环每次迭代
- 模型训练：每个 walk-forward 窗口

不做抢占式，原因是用户数据下载到一半被硬中断会留下不完整状态，协作式允许函数在返回前完成清理（关闭适配器连接、提交事务）。

### 4. 表元数据查询分两层

- `DataStore.table_stats(table) -> TableStats`：通用存储自省（行数、最早/最晚日期）。需要连接对象，属于存储层能力。
- `services/queries.py`：领域组合查询（因子名列表、股票池覆盖率、数据缺失检测），由 `DataStore` 原语组合而成。

**理由**：`DataStore` 是通用存储抽象，不应知道"因子名"这种领域概念；但"某张表有多少行、日期跨度多少"是纯存储事实。当前这三处是散落的裸 SQL（`cli.py:103` / `cli.py:119` / `cli.py:401`），且 `list_factor_names()` 完全不存在。

### 5. `walk_forward_train` 返回 dataclass 而非 tuple

```python
@dataclass
class WalkForwardResult:
    predictions: pd.DataFrame
    feature_matrix: pd.DataFrame
    feature_importance: pd.DataFrame   # factor, importance(窗口均值), std
```

当前是 `tuple[pd.DataFrame, pd.DataFrame]`，调用方 `predictions, _ = walk_forward_train(...)` 把特征矩阵直接丢弃（`cli.py:264`），且 `LGBMRegressor` 是训练循环内的局部变量，窗口结束即释放 —— `feature_importances_` 从未被读取。

改成 dataclass 的收益：后续 C5 需要补充分窗口指标（每窗口 IC、训练样本数）时加字段不破坏调用方。

### 6. 直接删除 CLI，不保留过渡适配器

删除 `cli.py` 与 `[project.scripts]`，不做"薄适配器保留一个版本"。

**理由**：保留适配器意味着 C2–C6 全周期维护两套入口，而适配器本身没有任何使用者——simulator 的 API 继续存活，研究域在 C2 前通过 Python REPL/脚本调用服务层即可。

**代价**：C1 完成到 C2 完成之间存在窗口期，研究域操作无 UI。这是有意识的取舍，换取不写一次性代码。

### 7. FastAPI app 组装与 simulator app 分离

`runtime/app.py` 负责组装平台 app（挂 `/api/health`、静态文件、C2 之后的各域路由），`simulator/api.py` 的 `create_app()` 保留但不再独立启动，由平台 app 通过 `include_router` 或挂载方式纳入。

过渡期 `simulator/api.py` 的会话读写逻辑零改动，只改挂载方式。

### 8. 信号日期锚定「最近一个周五」，并删除死代码

`generate_strategy_signals` 在未显式指定日期时取**最近一个周五**，而不是「最新交易日」。周度调仓的信号语义就是周五收盘，周二跑出个周二的信号是错的。

原先实现这件事的 `backtest/engine.py:generate_signals_for_today()` 是死代码（仅被 `backtest/__init__.py` 导出，无任何调用者），且逻辑本身也不对——`last_trade_date_of_week(today)` 返回的是**本周**的周五，可能是未来。直接删除，由服务层用一个语义正确的 `most_recent_friday()` 承接。

注意：`get_calendar()` 返回的是 pandas `Timestamp` 而非 `date`。服务层在返回前统一归一化；mypy 抓不到这类问题，因为 DataFrame 元素类型是 `Any`。

### 9. 取消时截断净值序列

回测的周循环检测到取消后 `break`，但循环之后的 `_record_nav(..., all_trade_dates[recorded_idx:], ...)` 会把净值一路补到区间末尾，产出一条平坦的尾巴——这不满足「返回截至当前周的净值序列」。取消时跳过这段收尾。

配套修正：`nav_records` 为空时 `pd.DataFrame([])` 没有 `trade_date` 列，`drop_duplicates(subset=...)` 会抛 `KeyError`，因此空结果需要单独分支。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| C1 后 C2 前无 UI，研究域只能脚本调用 | 过渡期短且有意识接受；simulator API 全程存活可验证平台能起来 |
| `ctx` 注入是跨文件机械改动，可能漏掉某个循环导致取消不及时 | 在 tasks 中列出全部检查点位置；测试用 `NULL_CONTEXT` 之外的 fake ctx 断言 `cancelled()` 被调用 |
| `walk_forward_train` 返回值改动破坏调用方 | 唯一调用方是 `cli.py`，同变更中删除；测试改造一并完成 |
| 现有测试大量依赖 CLI 调用路径 | 改造为服务层调用。这是本变更的主要测试工作量 |
| 服务层与 `simulator/engine.py` 职责重叠 | 明确边界：`services/` 面向研究批处理（同步/因子/回测/训练），`simulator/` 面向交互式逐周决策，二者共享 `DataStore` 与策略注册表，不互相调用 |
| `DataStore.table_stats` 需要表名白名单以防 SQL 注入 | 用固定的 `TABLE_NAMES` 常量校验入参，不做字符串拼接 |
