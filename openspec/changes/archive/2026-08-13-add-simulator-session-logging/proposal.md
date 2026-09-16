## Why

模拟器后端 `POST /api/sessions` 创建会话接口在整条调用链上只有 1 条日志（`session.py:71` 写入 DB 后），调用方无法感知创建进度。当步骤耗时较长（如 snapshot 构建中的因子计算、行情查询）或中途失败时，排查完全依赖 traceback，缺少结构化上下文。需要在创建链路的关键步骤补充日志，提供可观测的进度信息。

## What Changes

- `api.py` `create_session`: 添加请求入口日志（参数摘要）、成功/失败日志（含耗时）
- `engine.py` `Simulator.create()`: 在各关键步骤（日历构建、策略加载、snapshot 构建）添加 INFO/DEBUG 级别日志
- `session.py` `SessionStore.create()`: 在子步骤（目录创建、DB 写入）补充 DEBUG 日志
- `snapshot.py` `SnapshotBuilder.build_snapshot()`: 在四个子模块（市场概览、持仓快照、因子排名、策略信号）添加进度日志
- 所有日志使用现有 `loguru` logger，不引入新依赖
- 不改变 API 接口、返回值结构、异常语义

## Capabilities

### New Capabilities
- `simulator-session-logging`: 模拟器会话创建全链路的可观测日志，覆盖 API 层、引擎层、持久层、快照构建层

### Modified Capabilities
<!-- None — existing capabilities unchanged -->

## Impact

- 修改文件：`src/quant_trade/simulator/api.py`、`engine.py`、`session.py`、`snapshot.py`
- 无 API 变更、无数据模型变更、无新依赖
- 日志级别：关键步骤用 INFO，细节步骤用 DEBUG，异常用 ERROR/WARNING
