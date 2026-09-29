## Context

`ComparisonEngine` 拼三条线：手动盘（从决策记录回放）、参考策略（`run_backtest` 跑一遍影子回测）、基准（沪深300）。真机验证时策略线不出现，追下去是三个层次的问题。

已实测确认的因果链：

```
DuckDB .df() → row["cursor_date"] 是 pd.Timestamp
   └─ comparison._to_date 原样放行（isinstance(d, date) 把 Timestamp 也收了）
        └─ run_backtest(end=Timestamp)
             └─ 周循环 exec_date > end  →  TypeError
                  "Cannot compare Timestamp with datetime.date"
                  └─ except Exception → warning → return None, {}
```

对照实测：

```
comparison._to_date(Timestamp('2023-06-01')) -> Timestamp('2023-06-01 00:00:00')
engine._to_date(...)                          -> datetime.date(2023, 6, 1)
calendar._to_date(...)                        -> datetime.date(2023, 6, 1)
```

`engine.py` 那份的 docstring 就是在讲这个坑，comparison 这份顺序反了 —— 仓库里三份同名函数，坏的是唯一没被判过的那份。

## Goals / Non-Goals

**Goals:**

- 策略净值线恢复绘制，关键指标重新有「策略」一组。
- 策略回测失败时页面看得见原因，不再静默少一条线。
- 逐周差异表回答「你采纳了多少、剔掉了什么、额外加了什么」——即一键采纳与回填改单的反馈闭环。
- 差异表在两个会话形态下都说得通：有参考策略、无参考策略。

**Non-Goals:**

- 不实现「策略持仓重叠」这条口径（即从影子回测的 `trade_log` 回放每周持仓）。它回答的是另一个问题，且要顺带把两侧口径都改成持仓集合，成本与收益不成比例。本变更把这条口径的残骸清掉，不留半截。
- 不动 `run_backtest`、`Portfolio`、回测引擎的任何行为。
- 不改净值曲线的后端归一约定。
- 不引入「推荐方案仓位系数」之类的调仓工具。

## Decisions

### 决策一：删除 comparison 的 `_to_date`，改用 `quant_trade.data.calendar._to_date`

**选择**：删掉 `comparison.py:401` 的定义，把它加进已有的 `from quant_trade.data.calendar import ...`（那里已经在导 `TradeCalendar`）。7 个调用点名字不变。

**排除的方案**：就地重排 `isinstance` 顺序。改动同样小，但仓库里继续留着三份同名实现，下一份新代码还会挑错一份抄。

**排除的方案**：把 `_to_date` 提升成公开工具。三份变一份是应该的，但那是独立的重构，不该混进一个 bug 修复里；本变更只做「坏的那份不再被用」。

### 决策二：策略回测失败返回原因，而不是抛异常

`ComparisonResult` 增加一个可选字段承载失败原因，UI 用 Alert 呈现。

**理由**：手动盘与基准两条线失败时依然有效，为一个策略跑不起来而让整个对比接口 500，是把小故障放大。但「静默少一条线」正是这个 bug 藏了这么久的原因——降级可以，必须说出来。

### 决策三：差异表换成「决策 vs 当周推荐」

**选择**：按周比较 `Decision.user_orders` 与 `Decision.strategy_orders` 推导出的**目标组合**，输出「完全跟随 / 你剔除 / 你额外加」。

```
推荐的 BUY 目标组合  R = {o.ts_code | o ∈ strategy_orders, o.direction == "BUY", o.target_pct > 0}
你的 BUY 目标组合    U = {o.ts_code | o ∈ user_orders,     o.direction == "BUY", o.target_pct > 0}

你剔除  = R − U
你额外加 = U − R
完全跟随 = R == U
```

**为何按组合而不是按订单**：同一目标组合可以有多种下单方式（分两笔买、先买后调）。订单级比较会把「分两笔」判成偏离，把「权重改了」判成没偏离。目标组合是决策的意图，也是「一键采纳」真正复制的东西。

**为何不用持仓重叠**：那需要跑一遍影子回测再回放 `trade_log` 得到每周持仓（今天的 `strategy_holds_by_week` 从没填过），而且两侧口径要重新统一成持仓集合才可比。这条路成本高，回答的也是另一个问题——「我和一个全程跟策略的账户差多少」，而不是「我这次听劝了没有」。

**为何卖出不单列**：卖出动作由目标组合的差集隐含。额外列一列「推荐的卖出你没执行」，和「你剔除」是同一件事的两种说法。

### 决策四：删掉恒空的列，不保留

「策略独有 / 共同」两列目前恒为 `—`。保留它们等于在页面上写「本周没有差异」——和事实相反，且无法分辨「没有差异」与「算不出来」。直接删。

### 决策五：无参考策略的周次显示「无推荐」，不是「完全跟随」

`Decision.strategy_orders` 为 `None`（会话未配置参考策略，或策略信号生成失败）与 `[]`（策略配置了但本周没信号）是两回事，沿用仓库既有的 `null` / `[]` 约定：

- `None` → 该周显示「无参考策略」，不参与采纳率统计
- `[]` → 该周推荐的组合为空，若用户也空仓则「完全跟随」，否则全部算「你额外加」

## Risks / Trade-offs

- **差异表不再反映持仓结果** → 它只讲意图：你剔掉 601138.SH，不等于你当时手里没有它。页面文案要写清是「目标组合」的比较，否则会被读成持仓对照。
- **老会话的 `strategy_orders` 可能为 `None`**（早期创建、或 `ref_strategy` 加载失败时 `step` 存的是 `None`）→ 按决策五显示「无参考策略」，SHALL NOT 误报成完全跟随。
- **采纳率是个容易自我欺骗的指标** → 全量跟随每周都是 100%，看起来漂亮但恰恰是让手动线贴合策略线的那种做法。本变更只呈现事实，不加「高采纳率=好」的暗示。
- **删列是接口破坏** → 前后端同仓，同时改；`weekly_diffs` 没有别的消费方（已确认无外部调用）。

## Open Questions

- **`decision-comparison` 的 Purpose 已就地改掉，不在本变更的 delta 里。** 实测（OpenSpec 1.6.0）delta 只支持 `ADDED/MODIFIED/REMOVED/RENAMED Requirements` 四种操作，写进 delta 的 `## Purpose` 段在 archive 时被直接忽略——主 spec 的 Purpose 原样保留。而本变更把差异表从「持仓重叠」改成了「目标组合偏离」，Purpose 里那句 "weekly position diffs" 会永久留下来说错话。故直接改了主 spec 那一行；Requirements 照常由 delta 在归档时套用。

- 影子回测的每周持仓（原 `strategy_holds_by_week`）是否值得单独做一个变更。如果要做，得先决定两侧口径都取「周末持仓」还是都取「目标组合」。本变更不做。
- 采纳率是否需要按「权重是否也一致」细分（跟了名字但改了权重算不算跟随）。当前按名字集合算，权重偏离不计入。
- **真机验证发现：推荐不可复现，偏离表因此不可信 —— 这是本变更范围外的根因，挡住任务 5.4。** 实测链路：

  ```
  get_universe(2023-06-02) 两次调用
    → 同一个 772 只代码集合，顺序不同（SELECT DISTINCT 无 ORDER BY）
    → 综合得分大量并列（当周 8 个因子里 5 个返回空，剩下一只股票的分数彼此相同）
    → sort_values 在并列下的顺序决定谁进 top_n / 谁被行业上限砍掉
    → 同一日期两次调用选出不同的组合
  ```

  实测（同一份 DB）：

  ```
  universe size: 772 772 | same order: False | same set: True
  call A picks: ('002230.SZ', '002602.SZ', '300475.SZ', '688322.SH')
  call B picks: ('002230.SZ', '002602.SZ', '300475.SZ', '688322.SH')   ← 传同一份有序 universe 时稳定
  call C picks: ('002558.SZ', '002602.SZ', '300476.SZ', '688475.SH')   ← 换成 reversed(universe) 就全变了
  ```

  加上 `ORDER BY ts_code` 后两次查询一致，推荐随之可复现。

  后果：用户点了「一键执行」的那一周，`Decision.strategy_orders` 记的是 `step` 内部**重算**出来的另一套组合，于是 `followed` 报 `false`、`dropped` / `added` 各两只 —— 明明完全照做。**一键跟随与偏离表在当前数据层上是互斥的。**

  影响面超出仿真：整个因子/回测平台的选股结果都取决于一次 SQL 的行序，任何一次重跑都可能选出不同的股票。修法只有一行（`get_universe` 与 `_universe_from_kline` 各加 `ORDER BY ts_code`），但会改变已有回测结果，必须是独立变更。

  两条互补的路，本变更都不做：

  - **R1 修根因**：让 universe 有序 → 策略可复现。改的是数据层，动的是全平台选股。
  - **R2 偏离以「当时展示的推荐」为基准**：把用户看到并采纳的那份推荐随决策落盘（`snapshot_before` 目前不入库），偏离就变成「你有没有照做给你的方案」，不受重算漂移影响。但影子净值线仍不可复现，`对比` 页的「策略」线与「推荐」仍不是同一套组合。
