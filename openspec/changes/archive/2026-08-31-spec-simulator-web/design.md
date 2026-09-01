# Design: spec-simulator-web

## Context

模拟盘 Web 服务已完整实现：`src/quant_trade/simulator/api.py`（FastAPI，JSON-only，无前端渲染）+ `web/`（React 19 + Vite）。现有 specs 覆盖引擎与持久化，但 Web 层契约空白，且曾存在端口漂移：CLI 默认 8000，Vite 代理固定 9555，注释过时。

本 change 大部分为实现完成的补 spec 工作：固化行为契约 + 修正端口默认值。

## Goals / Non-Goals

**Goals:**
- 固化 Web 服务 API 契约与端口约定，作为后续修改的回归基准
- 消除端口不一致（CLI 默认 = 代理目标 = 9555）
- README 可复现的本地运行步骤

**Non-Goals:**
- 不改动任何 API 端点路径、请求/响应结构（spec 描述现状，非重设计）
- 不引入前端构建产物托管到后端（FastAPI 保持 JSON-only）
- 不做鉴权、多用户、生产部署配置

## Decisions

1. **端口统一为 9555**
   - 选择：改 CLI 默认值 8000 → 9555，与 Vite 代理对齐
   - 备选：改 vite.config.ts 代理到 8000。弃因：CLI 默认值面向更多使用者（含无前端场景），而代理配置只有一处且 9555 已在 web 侧多处固化（README、注释）
   - 备选：引入统一配置项读取端口。弃因：过度设计，本地开发工具两处常量足够

2. **spec 能力边界：单个 `simulator-web` 能力覆盖后端 API + 前端代理约定**
   - 备选：拆 `simulator-web-api` / `simulator-web-frontend` 两个能力。弃因：前端无独立业务逻辑，其行为（端口、代理、API 基址）只有与后端配对才有意义；拆分徒增维护成本

3. **spec 描述现状而非新增行为**
   - 实现先于 spec 存在，spec 作为事实契约记录。发现实现与 spec 冲突时（本次的端口默认值），以 spec 为准修正实现

4. **前端 API 基址：相对路径 `/api` + `VITE_API_BASE` 覆盖**
   - 选择：dev 走 Vite 代理免 CORS；部署时环境变量注入绝对地址，无需改代码
   - 后端保留 CORS `*` 兜底，覆盖不经代理的直连场景

## Risks / Trade-offs

- [Risk] 端口 9555 被其他服务占用 → Mitigation: `--port` 参数可改，README 注明需同步 vite.config.ts
- [Risk] spec 与实现未来漂移 → Mitigation: archive 时 validate，spec 场景可直接映射为 API 测试
- [Risk] CORS `*` 在生产有暴露风险 → Mitigation: 当前仅本地个人使用（README 明示），部署时收紧为显式 origin 列表（超出本 change 范围）
