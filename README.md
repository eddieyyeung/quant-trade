# quant-trade — A 股量化研究平台

个人 A 股低频量化研究平台。日线级别，周度调仓，回测即模拟盘，手动下单。

## 快速开始

```bash
# 1. 安装
uv sync --extra dev

# 2. 构建前端（首次或前端有改动时）
cd web && npm install && npm run build && cd ..

# 3. 启动平台
uv run python -m quant_trade
```

打开 <http://127.0.0.1:9555>。数据同步、因子计算、模型训练、回测都在界面里发起，
不再需要记忆命令行参数。

平台默认只监听本机回环地址。服务没有任何鉴权，若要暴露到局域网需显式指定
`--host 0.0.0.0`。

### 开发模式

前后端分离，前端热更新：

```bash
uv run python -m quant_trade --reload   # 终端 A：API，端口 9555
cd web && npm run dev                   # 终端 B：Vite，端口 9333，代理 /api
```

### 其他启动参数

```bash
uv run python -m quant_trade --port 9000    # 换端口
uv run python -m quant_trade --host 0.0.0.0 # 允许外部访问（无鉴权，谨慎）
```

启动入口不接受任何子命令 —— 研究操作通过界面发起。

## 研究操作

每个研究操作都是 `quant_trade.services` 里的一个函数，接受「参数对象 + `RunContext`」，
返回结构化结果。界面按钮调用的就是这些函数，脚本和 notebook 也可以直接调：

```python
from quant_trade.config import AppConfig, DEFAULT_CONFIG_PATH
from quant_trade.data.store import DataStore
from quant_trade.services import RunContext
from quant_trade.services.data import DataSyncParams, sync_market_data
from quant_trade.services.backtest import BacktestParams, run_backtest_service

config = AppConfig.from_yaml(DEFAULT_CONFIG_PATH)
store = DataStore(config.data.db_path)
ctx = RunContext(run_id="manual", config=config, store=store)

sync_market_data(DataSyncParams(include_financials=True), ctx)

result = run_backtest_service(BacktestParams(), ctx)
print(result.metrics["total_return"], len(result.nav), "nav points")
```

可用的服务函数：

| 领域 | 函数 | 说明 |
|------|------|------|
| 数据 | `sync_market_data` | 同步行情、指数权重、日线 |
| 数据 | `data_status` | 各表行数与日期跨度、股票池规模 |
| 因子 | `compute_factors` / `compute_alpha158` | 计算注册因子 / 全部 158 个 Alpha158 因子 |
| 因子 | `factor_ic_summary` | IC / RankIC 统计 |
| 因子 | `list_factors` | 因子注册表与落盘状态 |
| 策略 | `generate_strategy_signals` / `list_strategies` | 生成调仓信号 / 列出策略 |
| 模型 | `train_model` / `predict_for_date` | walk-forward 训练 / 某日 Top 选股 |
| 模型 | `model_run_list` / `model_evaluation` | 训练历史 / 单次训练的 IC 序列、分年度表现与特征重要性 |
| 回测 | `run_backtest_service` | 净值、回撤、交易明细、绩效指标 |
| 报告 | `generate_weekly` | 完整周度流程 → HTML 报告 |
| 查询 | `list_factor_names` / `universe_coverage` | 已落盘因子名 / 股票池覆盖率 |

长任务通过 `RunContext` 上报进度并响应取消：

```python
from quant_trade.services import CancelToken, RunContext

token = CancelToken()
ctx = RunContext(
    run_id="r1", config=config, store=store,
    cancel_token=token,
    progress_sink=lambda pct, msg: print(f"{pct:.0%} {msg}"),
    log_sink=lambda msg, level: print(f"[{level}] {msg}"),
)
# 在另一个线程里调用 token.cancel() 即可中断
```

## 配置

所有配置在 `config/default.yaml`，首次使用无需修改。

```yaml
data:
  db_path: data/quant.db        # DuckDB 文件路径
  primary_source: akshare       # 主数据源
  backup_sources: [tushare]     # 备用数据源

factor:
  enabled:                      # 启用的因子列表
    - momentum_20d
    - momentum_60d
    - ma_deviation
    - pb_ratio
    - pe_ratio
    - dividend_yield
    - roe_ttm
    - revenue_yoy

strategy:
  name: factor_ranking          # 策略名称
  top_n: 15                     # 持仓数量
  max_industry_weight: 0.30     # 单行业最大权重

backtest:
  initial_capital: 100000       # 初始资金
  commission_rate: 0.00025      # 佣金万 2.5
  stamp_duty_rate: 0.0005       # 印花税 0.05%（卖出）
  start_date: 2015-01-01        # 回测起始日

report:
  output_dir: reports           # 报告输出目录
```

环境变量 `QUANT_CONFIG` 可指定自定义配置文件路径。

## 数据源

| 来源 | 日线 | 财务 | 免费 | 说明 |
|------|------|------|------|------|
| akshare | ✓ | 部分 | ✓ | 主数据源，东方财富上游 |
| tushare | ✓ | ✓ | 部分 | 备用，需设置环境变量 `TUSHARE_TOKEN` |

首次使用只需 akshare，无需额外配置。如需财务数据，建议注册 tushare 并设置 token：

```bash
export TUSHARE_TOKEN=your_token_here
```

## 因子体系

8 个内置因子：

| 因子 | 类型 | 说明 |
|------|------|------|
| momentum_20d | 动量 | 20 日对数收益率 |
| momentum_60d | 动量 | 60 日对数收益率 |
| ma_deviation | 动量 | 收盘价偏离 60 日均线幅度 |
| pb_ratio | 价值 | 市净率倒数（1/PB）|
| pe_ratio | 价值 | 市盈率倒数（1/PE，剔除负值）|
| dividend_yield | 价值 | 股息率 |
| roe_ttm | 质量 | 净资产收益率 TTM |
| revenue_yoy | 质量 | 营业收入同比增长率 |

添加新因子：继承 `Factor` 基类，加 `@register("name")` 装饰器即可。

## 策略

`FactorRankingStrategy`：多因子等权合成综合得分，取 Top-N 等权配置。

```
1. 对全市场计算所有启用因子
2. MAD 去极值 + Z-Score 标准化
3. 等权加总得综合得分
4. 取 Top-15，行业集中度不超过 30%
5. 输出等权目标仓位
```

## 回测规则

回测引擎内置 A 股特有规则：

- **T+1**: 当日买入次日才可卖出
- **涨跌停**: 涨停不买，跌停不卖。主板 10%、创业板/科创板 20%、ST 5%
- **交易成本**: 佣金万 2.5（最低 5 元）、卖出印花税 0.05%、过户费 0.001%
- **停牌**: 成交量=0 时冻结持仓
- **最小单位**: 100 股（1 手）

## 模拟盘

模拟盘 = 回测引擎跑到今天。

```python
from quant_trade.services.strategies import SignalParams, generate_strategy_signals

signals = generate_strategy_signals(SignalParams(), ctx)
for o in signals.orders:
    print(o.direction, o.ts_code, f"{o.target_pct:.1%}", o.reason)
```

界面上则以表格展示信号，并可逐周记录你的实际决策、与策略决策做对比。

## Web 界面

平台前端为 React + Vite，后端为 FastAPI，二者由同一个进程提供服务。开发时前端跑在
Vite dev server（9333），`/api` 请求代理到后端 9555。

```bash
# 生产：先构建前端，再由后端一起托管
cd web && npm install && npm run build && cd ..
uv run python -m quant_trade            # http://127.0.0.1:9555

# 开发：两个终端
uv run python -m quant_trade --reload   # 终端 A：API
cd web && npm run dev                   # 终端 B：Vite，http://localhost:9333
```

若改了后端端口，需同步修改 `web/vite.config.ts` 的 proxy 目标。

## 周报

自动生成的 HTML 报告包含 5 个区域：

1. **概览指标卡** — 当前市值、累计收益、夏普、最大回撤、超额收益
2. **净值曲线图** — 策略 vs 沪深 300 对比
3. **调仓信号表** — 下周买卖清单，买入绿/卖出红
4. **持仓明细** — 当前持仓、成本、浮动盈亏
5. **因子表现** — 因子 IC 跟踪，识别因子衰减

## 从 Jupyter 使用

所有模块可直接在 Notebook 中调用：

```python
from quant_trade.data import DataStore
from quant_trade.factors import registry
from quant_trade.strategies import strategy_registry
from quant_trade.backtest import run_backtest
from datetime import date

store = DataStore("data/quant.db")

# 查看股票池
universe = store.get_universe(["000300.SH", "000905.SH"], date.today())

# 计算单个因子
f = registry.get("momentum_20d")
vals = f.compute(date.today(), universe)

# 跑策略
strategy = strategy_registry.get("factor_ranking")
signals = strategy.generate_signals(date.today(), universe, store)

# 跑回测
result = run_backtest(strategy, date(2020, 1, 1), date.today(), store=store)
print(result["metrics"])
```

## 目录结构

```
quant-trade/
├── config/default.yaml      # 默认配置
├── data/quant.db            # DuckDB 数据库（运行后生成）
├── reports/                  # 周报输出（运行后生成）
├── src/quant_trade/
│   ├── data/                # 数据层
│   │   ├── sources/         # akshare/tushare 适配器
│   │   ├── store.py         # 统一查询接口
│   │   ├── sync.py          # 数据同步
│   │   └── calendar.py      # 交易日历
│   ├── factors/             # 因子计算
│   │   ├── momentum.py      # 动量因子
│   │   ├── value.py         # 价值因子
│   │   ├── quality.py       # 质量因子
│   │   ├── preprocess.py    # 预处理管线
│   │   └── analysis.py      # IC 分析
│   ├── strategies/          # 策略引擎
│   │   └── factor_ranking.py
│   ├── backtest/            # 回测引擎
│   │   ├── engine.py        # 主循环
│   │   ├── portfolio.py     # 持仓管理
│   │   └── rules.py         # A 股规则
│   ├── signals/reporter.py  # HTML 报告生成
│   ├── simulator/           # 模拟盘（引擎、会话存储、FastAPI 后端）
│   ├── config.py            # Pydantic 配置
│   └── cli.py               # CLI 入口
├── web/                     # React + Vite 前端（模拟盘 Web UI）
└── tests/                   # 测试
```

## 依赖

- **数据**: akshare, duckdb, pandas, polars
- **计算**: numpy, scipy
- **配置**: pydantic, pyyaml, python-dotenv
- **报告**: matplotlib, jinja2
- **日志**: loguru
- **Web 后端**: fastapi, uvicorn（`uv sync --extra web` 安装）
- **Web 前端**: React 19 + Vite（`web/` 目录，独立 `npm install`）
