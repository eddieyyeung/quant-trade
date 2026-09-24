## Context

C1（已归档）把领域逻辑从 CLI 剥离成 `quant_trade.services`，函数签名为 `fn(params, ctx) -> Result`，`RunContext` 提供 `progress()` / `log()` / `cancelled()`。平台入口 `python -m quant_trade` 已可启动，托管 simulator API 与 `web/dist`。

现在缺的是**执行基础设施**：没有任务队列，没有执行记录，没有结果持久化。`data sync` 全 A 股是小时级任务，同步 HTTP 请求扛不住；项目里也从来没有 job / run / task 概念。

C1 留下的三个预留设计点本变更必须落实：

1. `run.trigger`（`manual` / `scheduled` / `api`）
2. `run` 幂等键（`UNIQUE(kind, params_hash, trade_date)`）
3. intent / fill 分离（研究中合一，模拟盘/实盘必须分开）

## Goals / Non-Goals

**Goals**

- 建立 run / run_log / artifact 持久化模型与单 worker 串行队列
- 统一运行 API：一个 `POST /api/runs` 承接所有长任务，加新任务类型不改前端路由
- SSE 日志流与协作式取消的端到端打通
- 数据域页面可用（数据总览 / 同步任务 / 股票池 / 交易日历）
- Ant Design 平台外壳就位，为 C3–C5 提供可复用的页面容器

**Non-Goals**

- 不做因子 / 回测 / 模型 / 报告域页面（C3–C6）
- 不引入 Redis / Celery 等外部队列，不引入进程外 worker
- 不做鉴权与多用户
- 不迁移 simulator 前端（C6）
- 不接调度器（`trigger` 字段写入但暂不产生 `scheduled` 值）

## Decisions

### 1. 单 worker 串行队列，并发度固定为 1

不是简化妥协，是两条硬约束的交集：DuckDB 单文件只有一个写者；研究流程本质串行（不会一边同步数据一边跑回测）。串行同时消灭了锁竞争与"两个任务同时写 daily_kline"这类竞态。

代价：一次只能跑一个任务。对单用户研究平台这是正确的取舍——排队等 5 分钟，好过数据损坏。

**替代方案（未采纳）**：`ProcessPoolExecutor` 多 worker。否决原因——每个进程要自己开 DuckDB 连接，而单文件只允许一个写进程，等于把串行约束藏进运行时错误里。

### 2. DuckDB 连接：同进程多连接、同配置，靠 MVCC 并发

**这是 C1 期间被实测纠正的结论，务必按此实现。**

```
同进程内，read_only + read_write 指向同一文件
→ ConnectionException: Can't open a connection to same database file
   with a different configuration than existing connections
```

所以**不能用只读连接做读路径**。正确做法是所有连接使用默认（读写）配置，并发由 DuckDB 的 MVCC 提供——实测写入期间另一个连接完成 912 次读取、0.8 秒、零阻塞。

worker 持有一个写连接；HTTP 请求处理各开各的默认配置连接。

### 3. 三张表：run / run_log / artifact

```sql
run        (run_id PK, kind, params_json, status, progress, message,
            trigger, idempotency_key, started_at, finished_at, error)
run_log    (run_id, seq, ts, level, message)   PK (run_id, seq)
artifact   (artifact_id PK, run_id, kind, storage, ref, row_count, meta_json)
```

- `status` 取值：`pending` / `running` / `ok` / `failed` / `cancelled` / `interrupted`
- `trigger` 本期恒为 `manual`，字段先落库——C1 已论证过"现在加成本 1 个字段，以后加要改所有写入路径"
- `idempotency_key` 本期允许为空，唯一索引建好
- `artifact.storage` 区分三类后端：`table`（结构化专用表）/ `parquet` / `html`，`ref` 指向具体位置。C4 的回测结果表、C6 的报告文件都走这个索引

### 4. 重启恢复：非终态一律 `interrupted`

worker 启动时把所有非终态记录改写为 `interrupted`，**`running` 与 `pending` 都算**。进程被杀时任务不可能"还在跑"，留着 `running` 会让前端永远转圈。

`pending` 同样要处理，这一点在实现期的端到端验证中被补上：排队中的运行只存在于内存 `queue.Queue` 里，重启后队列是空的，没有任何东西会推进它。原设计只写了 `running`，但"前端永远转圈"的理由对 `pending` 一样成立——表是唯一真相源，就不该声称还有活要来。两者的 `error` 文案不同，便于区分。

替代方案（未采纳）是把遗留 `pending` 重新入队，即把表当作持久化队列。否决原因——重启平台会静默启动一个可能几小时的任务，与"不续跑"的取向不一致；而 `params_json` 可复现，重跑的成本是时间不是正确性。

不尝试续跑——C1 的取消是协作式的，进程被硬杀时领域函数没有机会做清理，续跑的前提不成立。前端把 `interrupted` 呈现为"已中断，可重跑"。

### 5. 统一运行 API，`kind` 走注册表

```
POST   /api/runs              {kind, params}  → 202 + run_id
GET    /api/runs              历史列表（分页）
GET    /api/runs/{id}         状态 + 进度
GET    /api/runs/{id}/logs    SSE 日志流
POST   /api/runs/{id}/cancel
GET    /api/runs/{id}/artifacts
```

注册表把 `kind` 映射到 `(params_model, service_fn)`。加新任务类型 = 注册表加一行。

**替代方案（未采纳）**：每个域一组 REST 路由。否决原因——8 个域各写各的分发，就是 C1 刚删掉的 CLI 的结构换层皮。

### 6. 日志流用 SSE，不用 WebSocket

日志是单向的（服务端 → 浏览器），SSE 原生支持断线重连，且不需要额外协议库。WebSocket 的双向能力这里用不上。

SSE 端点从 `run_log` 表读并持续轮询新增行——不共享内存队列。原因：任务可能在本进程之外的时间段运行（重启后查看历史日志），表是唯一真相源。轮询间隔 500ms，对研究场景足够。

### 7. `RunContext` 的 sink 接到 `run_log` + SSE

C1 已定义 `ProgressSink` / `LogSink` 协议。C2 提供实现：写入 `run_log` 表，并推进内存中的订阅者集合。`progress` 更新 `run.progress` 列。

### 8. 前端：手工 antd 壳，不引 Ant Design Pro

`Layout` + `Sider` + `Menu` + 路由自己搭，约 200 行。Ant Design Pro 默认带 umi/max 全家桶，与现有 Vite + React 19 工程冲突，等于重搭。

依赖落进 `web/package.json`：`antd`、`@ant-design/icons`、`echarts`、`echarts-for-react`、`react-router-dom`。同时清理误挂在**根** `package.json` 的 `react-router-dom` 与 `recharts`。

图表用 ECharts 而非 `@ant-design/plots`：量化平台的净值曲线需要 brush 缩放，K 线/蜡烛图是刚需，ECharts 在这两点上明显更强。代价是视觉风格需手动对齐 antd 主题。

### 9. antd `Table` 一律开虚拟滚动 + 服务端分页

5000 只股票 × 158 因子的表格不可能全量返回。接口层强制分页，前端 `Table` 开 `virtual`。现有 `/api/sessions` 的全量返回写法不得复制到新页面。

### 10. 前端开发代理沿用 9555 / 9333

`web/vite.config.ts` 已有 `port: 9333` + `target: http://localhost:9555`，不改。

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| 单 worker 一次只跑一个任务，长任务阻塞后续 | 有意为之；前端明确展示排队状态。真需要并发再引进程外 worker |
| SSE 轮询 `run_log` 带来额外查询 | 500ms 间隔、按 `(run_id, seq)` 走主键索引；单用户场景压力可忽略 |
| 重启恢复不续跑，长任务被杀要重来 | run 记录可复现（`params_json` + 幂等键），重跑成本是时间不是正确性 |
| `trigger` / `idempotency_key` 本期不使用，可能被当成死字段删掉 | spec 中显式声明为预留，并说明理由 |
| 手工 antd 壳比 Ant Design Pro 多写约 200 行 | 换掉 umi 全家桶依赖，且导航结构自己最清楚 |
| 新增前端依赖体积（antd + echarts） | 按需引入；echarts 用 `echarts-for-react` 的按需注册，不引全量包 |
| 移植既有 simulator 页面时误用全量返回接口 | 见决策 9；C6 迁移时一并改造 |

## Migration Plan

1. 后端先行：三张表 + worker + run API + SSE，用服务层现有函数验证端到端（不起前端也能用 HTTP 测）
2. 前端之一：antd 壳 + 依赖落地 + 任务中心页（验证 SSE 打通）
3. 前端之二：数据域页面
4. 回滚：新表与新路由是纯增量，删掉不影响 C1 的服务层与 simulator

## Open Questions

- 任务中心是否需要"重跑"按钮直接复用历史 run 的 `params_json`？倾向前端做（读 params 填表单再提交），后端不提供隐式重跑接口
- `artifact` 的 `row_count` 对 parquet / html 是否有意义？倾向 parquet 记行数，html 留空
