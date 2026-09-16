## Why

模拟盘已有完整 Web 前后端（FastAPI 后端 + React/Vite 前端）和本地运行方式，但现有 specs 只覆盖引擎、会话持久化等核心逻辑，没有任何 spec 描述 Web 服务的 API 契约与本地运行约定。导致端口配置散落各处（CLI 默认 8000、Vite 代理指向 9555，注释过期），新接手者无法从 spec 得知前后端如何协作。此变更补齐该空白。

## What Changes

- 新增 `simulator-web` 能力 spec，固化以下行为：
  - 后端：`quant-trade sim web` 启动 FastAPI 服务，默认端口 9555
  - 后端 API：会话 CRUD、step、skip、status、compare 接口的请求/响应契约
  - 前端：React/Vite 应用，dev server 端口 9333，`/api` 代理转发到 `http://localhost:9555`
  - 端口约定：后端默认端口与 Vite 代理目标一致；改端口须同步 `web/vite.config.ts`
- 修复 `sim web` CLI 默认端口 8000 → 9555，与前端代理对齐（已实现于会话中）
- 修复 `web/src/api/client.ts` 过时注释（代理目标 8000 → 9555）（已实现于会话中）
- README 补充「Web 服务（前后端）」本地运行文档（已实现于会话中）

## Capabilities

### New Capabilities
- `simulator-web`: 模拟盘 Web 服务 — FastAPI 后端 API 契约（会话 CRUD、决策 step、状态、对比）、前后端本地开发端口约定（后端 9555，前端 9333 + `/api` 代理）

### Modified Capabilities
<!-- 无。现有能力（simulator-engine、session-persistence 等）行为不变，端口默认值仅属新能力覆盖范围。 -->

## Impact

- **代码**: `src/quant_trade/cli.py`（`sim web` 默认端口）、`src/quant_trade/simulator/api.py`（API 实现，行为不变）
- **前端**: `web/`（vite.config.ts 代理配置，行为不变）
- **文档**: `README.md`（Web 服务章节已加）
- **依赖**: `pyproject.toml` `web` extra（fastapi、uvicorn，已存在，无变更）
- **API 兼容性**: 无破坏性变更。端口默认值 8000 → 9555 属行为修正，仅影响本地开发工作流。
