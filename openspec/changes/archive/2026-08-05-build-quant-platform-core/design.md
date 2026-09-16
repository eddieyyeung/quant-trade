## Context

项目当前为 Python 包骨架阶段（CLAUDE.md、pyproject.toml、空模块目录）。需要从零搭建 A 股低频量化研究平台的完整工具链。平台定位：个人使用、本地运行、日线级别、周度调仓、手动执行信号。

核心约束：
- 本地单机运行，不需要服务端/云基础设施
- 个人维护，模块需简单可调试，不引入微服务架构
- 初始资金 10 万，股票池沪深300+中证500（约800只）
- 模拟盘 = 回测引擎跑到 today，输出下周调仓信号

## Goals / Non-Goals

**Goals:**
- 可复用的因子计算框架，支持多源数据统一查询
- A 股规则感知的回测引擎（T+1、涨跌停、手续费、停牌）
- 每周一键产出：数据更新 → 因子计算 → 策略信号 → HTML 周报
- 同一份策略代码同时驱动历史回测和当前信号生成
- Jupyter Notebook 友好的探索接口，所有核心功能可从 notebook 调用

**Non-Goals:**
- 不接券商 API，不自动下单
- 不做实时行情推送或分钟级/Tick 级数据
- 不支持多用户、权限管理、Web 界面
- 不做因子挖掘/机器学习的自动 pipeline（但保留扩展点）
- 不追求回测性能极致优化（日线+800只+10年数据，向量化足够）

## Decisions

### 1. DuckDB 作为唯一存储引擎

**选择**: DuckDB（单文件数据库），不用 SQLite 或纯 Parquet。

**理由**:
- DuckDB 的列式存储 + SQL 查询 + DataFrame 直接读写，三种模式无缝切换
- 全 A 股 10 年日线数据约 3-5GB，DuckDB 轻松处理，无需 PostgreSQL
- 支持 `read_parquet()` 直接查询 Parquet 文件，akshare 下载后不需额外 ETL
- 单文件部署，零运维，备份就是复制一个文件
- `duckdb.connect()` 内存模式快速回测，文件模式持久化存储

**替代方案**:
- SQLite: 行式存储，OLAP 查询慢，不适合因子计算的大批量截面查询
- 纯 Parquet: 跨股票过滤和 SQL 分析不便，需要额外索引层
- PostgreSQL: 个人项目运维过重

### 2. 模块重组：analysis → factors

**选择**: 新建 `factors` 模块承载因子计算+分析，现有 `analysis/` 目录合并入 `factors/`。

**理由**: 因子计算和分析是同一上下文的两面——计算因子值后立即分析 IC，放在同一模块避免循环导入。`analysis/` 中的统计工具函数移入 `utils/`。

**最终模块结构**:
```
quant_trade/
├── data/          # 数据获取、清洗、DuckDB 读写
├── factors/       # 因子计算 + IC/RankIC/分层回测
├── strategies/    # 策略基类 + 具体策略实现
├── backtest/      # 回测引擎 + A股规则 + 绩效指标
├── signals/       # HTML 报告生成 + 通知推送
├── utils/         # 交易日历、统计工具、去极值/中性化
├── config.py      # Pydantic 配置模型
└── cli.py         # CLI 入口
```

### 3. 因子框架：注册表模式

**选择**: 基类 `Factor` + 装饰器注册表，不引入抽象工厂。

```python
from quant_trade.factors.base import Factor
from quant_trade.factors.registry import register

@register("momentum_20d")
class Momentum20d(Factor):
    category = "momentum"
    def compute(self, date, universe) -> pd.Series:
        ...
```

**理由**:
- 策略配置只需写因子名称字符串，registry 按名查找
- 新增因子只需一个文件 + `@register` 装饰器，零修改已有代码
- 比 YAML/JSON 配置驱动更灵活（因子可以有复杂构造参数）

### 4. 回测引擎：自研而非 vectorbt 封装

**选择**: 自行实现事件驱动式逐周回测循环，不依赖 vectorbt。

**理由**:
- vectorbt 是向量化回测，一次性计算所有信号再回测。A 股 T+1 + 涨跌停限价 + 停牌冻结，条件分支多，不适合纯向量化
- 逐周循环更容易理解、调试、和加入 A 股特殊规则
- 日线级别数据量小，周度循环 10 年仅约 500 次迭代，性能不是瓶颈
- 自研引擎可以直接读取 DuckDB，不需额外的数据格式转换

**回测循环伪代码**:
```python
for week in weeks(start, end):
    friday = last_trade_day_of_week(week)
    monday = next_trade_day(friday)

    signals = strategy.generate_signals(friday, universe, data)

    # 周一开盘执行，处理 A 股约束
    for order in signals:
        if order.is_buy and monday_open(order.code) >= limit_up(order.code, friday):
            continue  # 涨停买不到
        if order.is_sell and monday_open(order.code) <= limit_down(order.code, friday):
            continue  # 跌停卖不掉
        if is_suspended(order.code, monday):
            continue  # 停牌跳过

        execute(order, price=monday_open(order.code))

    portfolio.update()
    portfolio.deduct_fees()  # 佣金万2.5 + 印花税0.05%卖
```

### 5. 数据 pipeline：懒加载 + 热缓存

**选择**: 核心数据（日线行情、财务、成分股）通过 `DataStore` 类按需从 DuckDB 读取，因子计算结果缓存在内存 DataFrame 中供回测重用。

```
                    ┌─────────────┐
  akshare ─────────▶│  Raw Data   │──────────▶ DuckDB (持久)
  tushare ─────────▶│  Adapters   │
                    └─────────────┘
                           │
                    ┌──────▼──────┐
                    │  DataStore  │  ← 统一查询接口
                    │  (DuckDB)   │
                    └──────┬──────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
         daily_kline  financials  index_weights
```

`DataStore` 提供方法：
- `get_daily(ts_codes, start, end)` → DataFrame
- `get_financials(ts_codes, report_date)` → DataFrame
- `get_universe(index_codes, date)` → list[str]

### 6. HTML 周报：Jinja2 模板 + matplotlib 内嵌

**选择**: Jinja2 渲染 HTML 模板，matplotlib 图表保存为 base64 图片嵌入。

**理由**:
- 单体 HTML 文件，双击打开即可看，不需要本地 server
- matplotlib 比 plotly 生成的 HTML 更小（纯图片+HTML 文本 vs plotly 的 JavaScript bundle）
- Jinja2 模板可维护，图表和数据分离
- 后续可扩展为邮件/Markdown 等输出格式

### 7. Config 模型分层

```python
class DataConfig(BaseModel):
    db_path: str = "data/quant.db"
    cache_dir: str = "data/cache"
    primary_source: str = "akshare"
    backup_sources: list[str] = ["tushare"]

class FactorConfig(BaseModel):
    enabled: list[str] = ["momentum_20d", "roe_ttm", "pb_ratio", ...]
    params: dict[str, dict] = {}  # 因子级参数覆盖

class StrategyConfig(BaseModel):
    name: str = "factor_ranking"
    top_n: int = 15
    rebalance_freq: str = "weekly"
    params: dict = {}

class BacktestConfig(BaseModel):
    initial_capital: float = 100_000
    commission_rate: float = 0.00025  # 万2.5
    stamp_duty_rate: float = 0.0005   # 卖出0.05%
    start_date: date = date(2015, 1, 1)

class ReportConfig(BaseModel):
    output_dir: str = "reports"
    template: str = "weekly_report.html.j2"

class AppConfig(BaseModel):
    data: DataConfig
    factor: FactorConfig
    strategy: StrategyConfig
    backtest: BacktestConfig
    report: ReportConfig
```

配置来源优先级：环境变量 > YAML 文件 > Pydantic 默认值。

## Risks / Trade-offs

- **akshare 接口不稳定**: 东方财富等上游网页改版时接口可能失效 → 多源 fallback（tushare/baostock），数据拉取加上错误重试和日志
- **回测未考虑冲击成本**: 10 万资金量小，实际冲击成本可忽略，暂不建模
- **DuckDB 并发写**: 个人使用单进程顺序写入，不存在并发问题
- **因子失效风险**: 历史 IC 高的因子实盘可能衰减 → 周报持续监控 IC 走势，策略定期评估
- **模拟盘与实盘偏差**: 开盘价成交假设在大盘股合理，小盘股可能有偏差 → 优先沪深300+中证500，避开小票
