## Context

当前回测引擎 (`backtest/engine.py`) 是**全自动**的周度循环：每周五 `strategy.generate_signals()` → 周一执行 → 更新 NAV。整个流程无人工干预点，无分支时间线，无决策记录。

模拟器要支持**交互式**的历史复盘：用户在任意历史周五看到时点数据快照，做出自主决策，系统执行并追踪 P&L，随时输出"手动 vs 策略 vs 基准"三方对比。

现有可复用资产：
- `backtest/portfolio.py` — 虚拟账户（买卖、T+1、费用）
- `backtest/rules.py` — A 股涨跌停价格、停牌检测
- `data/store.py` — 时点数据查询（`get_daily`、`get_financials` 均支持 `as_of`）
- `strategies/` + `factors/` — 因子计算和策略信号生成

约束条件：
- Python 3.13+ / DuckDB 单文件存储 / ruff + mypy strict
- 财务数据表 (`financials`) 为空，指数权重仅 2026 年快照，因子信号在早期年份偏差较大
- 模拟范围限制在近 3 年（~2023 至今），以规避数据质量问题

## Goals / Non-Goals

**Goals:**
- 用户在终端或 Web UI 中与历史时点交互，做出周度调仓决策
- 每次决策前展示完整的时点数据快照（持仓盈亏、市场概况、因子排名、策略建议）
- 支持会话的暂停、恢复、列表
- 随时输出三方对比报告（手动 vs 策略 vs 基准）
- 引擎与 UI 完全解耦，CLI 和 Web 调用同一 `Simulator` 类

**Non-Goals:**
- 日度调仓（本变更保持周度）
- 多用户/并发模拟
- 日内分时级别模拟（当前数据仅 OHLCV）
- 移动端适配
- 实时行情接入（纯历史回放）
- 策略参数优化/网格搜索

## Decisions

### 1. 架构：包裹而非替换

模拟器不修改 `backtest/engine.py`，而是在其上层新建 `simulator/engine.py`，复用底层组件。

```
                    ┌──────────────────────┐
                    │   Simulator (新增)    │
                    │  create/step/skip/   │
                    │  compare/snapshot    │
                    └──────────┬───────────┘
                               │ 调用
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
        Portfolio         A股 rules        DataStore
        (backtest/)       (backtest/)      (data/)
```

理由：
- 回测引擎是紧耦合的自动循环，强行注入交互会破坏其简洁性
- Portfolio 和 rules 是纯净的状态/计算单元，接口清晰
- 模拟器的循环节奏不同：回测是一次批量跑完，模拟器是逐步推进（每个周五 pause → wait for input → execute → advance）

### 2. 时间线模型：光标 + 周步进

```python
class Simulator:
    session: SimSession          # 包含 cursor_date: date
    portfolio: Portfolio         # 当前持仓状态
    
    def step(self, session, orders, notes) -> StepResult
    def skip(self, session) -> StepResult
    def compare(self, session) -> ComparisonResult
    def snapshot(self, session) -> Snapshot
```

每步流程：
```
  光标所在周五 (cursor_date)
       │
       ▼
  generate_snapshot(cursor_date)   ← 时点数据聚合
       │
       ▼
  用户下 orders
       │
       ▼
  执行 (下一个周一 open price)
  → Portfolio.buy/sell
  → 记录 Decision {orders, notes, snapshot}
       │
       ▼
  cursor 前进到下一个周五
  → 标记期间 NAV
```

参考策略 (`reference_strategy`) 以 "影子模式" 运行：每次 `step()` 时，同时计算策略在本次会怎么做，记录到 Decision 中。对比报告时回溯策略的完整决策链。

### 3. 快照数据结构

```python
@dataclass
class Snapshot:
    signal_date: date           # 当前周五
    exec_date: date             # 下周一
    week_number: int            # 第几周
    total_weeks: int | None     # 总周数 (None if end_date not set)
    
    # 市场概况
    benchmark_close: float      # 沪深300 收盘价
    benchmark_weekly_return: float
    
    # 用户持仓
    portfolio: dict             # 总额 + 逐项 {code, shares, avg_cost, price, pnl_pct, weight_pct}
    
    # 参考策略信号
    strategy_signals: dict | None  # SignalResult (从 reference_strategy 生成)
    # 包含: recommended_buys[{code, weight, reason}], recommended_sells[{code, reason}]
    
    # 全市场因子排名 (Top-N)
    factor_ranking: list[dict]  # [{rank, code, composite_score, factor_scores: {...}}]
```

所有查询使用 `as_of = cursor_date`，确保无未来信息。

### 4. 会话持久化：DuckDB + JSON

```sql
CREATE TABLE IF NOT EXISTS simulator_session (
    id VARCHAR PRIMARY KEY,
    name VARCHAR,
    start_date DATE,
    end_date DATE,              -- NULL = 跑到最新数据
    initial_capital DOUBLE,
    cursor_date DATE,
    portfolio_json TEXT,        -- Portfolio.to_dict() 
    reference_strategy VARCHAR,  -- "factor_ranking" or NULL
    status VARCHAR,             -- "active" / "paused" / "completed"
    created_at TIMESTAMP,
    updated_at TIMESTAMP
);
```

决策和快照用 JSON 文件存储（`data/simulator/<session_id>/decisions.json`），避免 DuckDB 存大 JSON 列性能差。

理由：
- 会话元数据用 DuckDB → 可用 SQL 查询、列表、筛选
- 决策/快照用 JSON 文件 → 序列化简单，逐步追加，人可读
- 不用 CSV → Order/Snapshot 嵌套结构
- 不用内存 → 需要暂停/恢复

### 5. 多线对比模型

对比引擎在 compare() 时：

1. 用用户决策链跑完整周期 → `nav_manual` (已有)
2. 用参考策略重跑同一周期（批量回测）→ `nav_strategy` (调用现有 `run_backtest`)
3. 用基准同期表现 → `nav_benchmark` (已有)

逐周 diff 核心价值：展示分歧周。哪些周用户和策略选了不同的票？谁对谁错？

```
输出结构:
{
  nav_curves: {manual: Series, strategy: Series, benchmark: Series},
  metrics: {manual: {...}, strategy: {...}, benchmark: {...}},
  weekly_diff: [
    {week, date, user_buys: [...], strategy_buys: [...], 
     overlap: 3/15, user_only: [...], strategy_only: [...]}
  ]
}
```

### 6. Phase 1 (CLI) vs Phase 2 (FastAPI)

```
Phase 1: Simulator 类 + CLI (核心可用)
  quant-trade sim start --name "2024复盘" --start 2023-06-01 --ref factor_ranking
  quant-trade sim resume <id>
  quant-trade sim step <id> --buy "600519:10%" --buy "002594:8%" --sell "300750" --note "看好消费"
  quant-trade sim skip <id>
  quant-trade sim status <id>
  quant-trade sim compare <id>

Phase 2: FastAPI + HTML 前端
  GET  /api/simulator/sessions       → 会话列表
  POST /api/simulator/sessions       → 创建会话
  GET  /api/simulator/sessions/{id}  → 当前状态 + 快照
  POST /api/simulator/sessions/{id}/step → 执行决策 + 前进
  POST /api/simulator/sessions/{id}/skip → 跳过本周
  GET  /api/simulator/sessions/{id}/compare → 对比报告
```

引擎层 (`Simulator`) 不感知是 CLI 还是 HTTP 在调用——产出相同的数据结构，CLI 用 text/table 渲染，Web 用 JSON 返回。

### 7. 参考策略集成方式

用户在创建会话时选择一个参考策略（如 `factor_ranking`），也可选 `None`（无参考）。

策略以**旁观模式**运行：每次 `step()` 时，引擎传入当前 `cursor_date` 和 universe 给 `strategy.generate_signals()`，产出 `SignalResult`。这个结果仅用于快照展示和对比报告，不影响用户决策也**不实际执行**。

对比时的 `nav_strategy` 通过 `run_backtest()` 批量重算（非逐步模拟），因为策略是确定性的——不需要逐步回放。

## Risks / Trade-offs

**R1: 数据质量影响早期模拟**
`financials` 表为空，指数权重仅 2026 年 7 月快照。模拟范围限制近 3 年（~2023 至今），但即使近期，价值/质量因子也因缺少财务数据而退化。
→ 缓解：快照中子项标注数据覆盖状态；因子排名仅展示有数据的因子

**R2: CLI 交互体验受限**
终端中等长表格、中文字段在 CLI 中排版困难。历史 K 线在纯文本中无法渲染。
→ 缓解：CLI 输出聚焦关键数字；用 rich 库美化表格；K 线留到 Phase 2 Web UI（ECharts 渲染）

**R3: 单股价格回退期权影响**
后复权价格在不同时间段的相对比较有扭曲。
→ 不缓解：这是所有 A 股回测系统的共识问题，非本变更范围

**R4: 用户决策链条依赖**
如果用户在早期周做了非理性决策（比如全仓 1 只 ST），后续对比报告的意义打折。
→ 缓解：不限制用户操作，但 compare 输出中标注极端集中度/回撤等风险指标

**R5: FastAPI 增加运行时依赖**
当前项目是纯 CLI 工具，引入 Web 框架增加复杂度。
→ 缓解：FastAPI 放在可选 extra（`pip install -e ".[web]"`），CLI 核心功能不依赖它

## Open Questions

- **Q1**: 模拟是否要在 user 完全不操作时自动按策略执行？当前设计：`skip` 意味着本周不调仓（保持现状），而非自动跟策略。需要用户确认这个语义。
- **Q2**: Phase 2 Web UI 是否需要实时 WebSocket 推送（如价格动画），还是纯请求-响应？倾向请求-响应（简单，够用）。
- **Q3**: 对比报告是否需要持久化（保存/分享），还是每次 compare() 动态生成？倾向动态生成 + 可选导出 HTML。
