## Context

当前 `POST /api/sessions` 创建会话的调用链：

```
api.py:create_session (无日志)
  → engine.py:Simulator.create (无步骤日志)
    → 解析日历、验证日期、初始化 Portfolio (无日志)
    → SessionStore.create (仅1条日志: "Session {id} created")
    → 获取 universe (无日志)
    → SnapshotBuilder.build_snapshot (无日志, 最耗时)
      → 市场概览、持仓快照、因子排名、策略信号 (全部无日志)
```

整条链路仅 `session.py:71` 一条 INFO 日志。如果 snapshot 构建阶段的因子计算耗时 5-10 秒，调用方无法感知进度。

项目已使用 `loguru` 作为日志库，所有目标文件均已 import `logger`（除 `api.py`）。

## Goals / Non-Goals

**Goals:**
- 在会话创建链路的每个关键步骤添加 `logger.info()` 日志，使调用方能看到进度
- 在子步骤（目录创建、DB 写入、快照子模块）添加 `logger.debug()` 日志，便于排查慢查询
- 在 API 层添加入口参数日志和耗时日志
- 异常路径记录错误上下文（参数值、失败步骤名）

**Non-Goals:**
- 不引入结构化日志/分布式追踪 (OpenTelemetry 等)
- 不改变 API 接口签名或返回值
- 不修改日志格式或 sink 配置
- 不在 `Simulator.step()` / `skip()` 等运行时操作中添加日志（本次仅限创建流程）

## Decisions

### 1. 日志级别选择

| 步骤 | 级别 | 理由 |
|------|------|------|
| API 入口参数/成功/失败 | INFO | 每次请求都要可见 |
| 引擎层关键步骤（日历就绪、策略加载、快照构建开始） | INFO | 用户关心的进度节点 |
| 持久层子步骤（目录创建、DB INSERT） | DEBUG | 常态下不需要，排查存储问题时开启 |
| 快照子模块（市场、持仓、因子、策略） | INFO | 交互式工具需可见进度（后续 speed-up-simulator-snapshot 从 DEBUG 升级） |
| 异常 | ERROR/WARNING | 已有异常处理处补充上下文 |

### 2. 不在 api.py 引入新的日志依赖

`api.py` 当前无 `logger` import。直接 `from loguru import logger`，与项目其他模块一致。不引入中间件方案（太重型，与需求不匹配）。

### 3. 日志内容包含关键上下文

每条 INFO 日志包含：session 相关参数（name, date range, capital）或步骤耗时。格式示例：

```
"Creating session: name=测试会话, start=2023-06-01, end=2025-12-31, capital=100000"
"Session calendar ready: 130 weeks, first_friday=2023-06-02"
"Reference strategy loaded: momentum_rotation"
"Building initial snapshot..."
"Session abc123 created in 2.3s: 130 weeks, strategy=momentum_rotation"
```

### 4. 使用 `perf_counter` 而非装饰器

不引入计时装饰器。在 `create_session` 入口记录 `time.perf_counter()`，出口计算耗时，简单直接。

## Risks / Trade-offs

- **日志量增加**: 每次创建会话从 1 条日志增加到 5-8 条（INFO）+ 若干 DEBUG。INFO 级别下增量可忽略（创会话是低频操作）。
- **日志格式不一致风险**: 手动编写日志字符串可能不够统一。缓解：本次仅涉及 4 个文件，review 时检查一致性。
- **无结构化输出**: 日志仍为纯文本，无法被日志系统自动解析。这是 Non-Goal，未来可演进。
