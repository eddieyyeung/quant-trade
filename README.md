# quant-trade — A 股量化研究平台

个人 A 股低频量化研究平台。日线级别，周度调仓，回测即模拟盘，手动下单。

## 快速开始

```bash
# 1. 安装
uv sync --extra dev

# 2. 拉取数据（首次全量，约需 5-10 分钟）
uv run quant-trade data sync

# 3. 跑一遍完整周度流程（数据→因子→策略→报告）
uv run quant-trade weekly
```

第 3 步会在 `reports/` 目录生成 HTML 周报，自动用浏览器打开。

## 设计理念

```
数据 → 因子 → 策略 → 回测/信号 → HTML 周报

每周五收盘后跑一遍，看周报，手动在券商 APP 下单。
不需要接券商 API，不需要实时行情，不需要服务器。
```

核心约束：
- 日线数据，每周调仓
- 10 万初始资金模拟盘
- 沪深 300 + 中证 500 股票池（约 800 只）
- 多因子打分排名选股，等权 Top-15
- DuckDB 单文件数据库，零运维

## 命令参考

```bash
# ===== 数据 =====
uv run quant-trade data sync                      # 拉取行情+基本信息，增量更新
uv run quant-trade data sync --include-financials  # 同时拉取财务数据（慢）
uv run quant-trade data status                    # 查看数据库状态

# ===== 因子 =====
uv run quant-trade factor update                  # 计算全部启用因子
uv run quant-trade factor list                    # 列出已注册因子
uv run quant-trade factor ic                      # 查看因子 IC 摘要

# ===== 策略 =====
uv run quant-trade strategy list                  # 列出已注册策略
uv run quant-trade strategy run                   # 生成当前调仓信号（终端输出）

# ===== 回测 =====
uv run quant-trade backtest run                   # 默认 2015-01-01 至今
uv run quant-trade backtest run --start 2020-01-01 --end 2025-12-31

# ===== 一键报告 =====
uv run quant-trade weekly                         # 数据→因子→策略→HTML 周报
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

```bash
# 用 strategy run 看当前信号
uv run quant-trade strategy run

# 输出示例：
# Signal date: 2026-07-18 (周五)
#   🟢 BUY  600519.SH  6.7%  (综合得分 92.3)
#   🔴 SELL 000858.SZ   →0   (得分跌出 Top-15)
#   ...
```

维护 `portfolio.json` 记录虚拟持仓。每周按信号手动操作后更新文件。

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
│   ├── config.py            # Pydantic 配置
│   └── cli.py               # CLI 入口
└── tests/                   # 测试
```

## 依赖

- **数据**: akshare, duckdb, pandas, polars
- **计算**: numpy, scipy
- **配置**: pydantic, pyyaml, python-dotenv
- **报告**: matplotlib, jinja2
- **日志**: loguru
