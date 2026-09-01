# Tasks: spec-simulator-web

## 1. 端口对齐（已完成）

- [x] 1.1 `sim web` CLI 默认端口 8000 → 9555（`src/quant_trade/cli.py`）
- [x] 1.2 同步 CLI help 文本端口示例
- [x] 1.3 修复 `web/src/api/client.ts` 过时代理注释

## 2. 文档（已完成）

- [x] 2.1 README 新增「Web 服务（前后端）」章节：后端 `sim web`、前端 `npm run dev`、端口约定
- [x] 2.2 README 目录结构补充 `simulator/` 与 `web/`
- [x] 2.3 README 依赖清单补充 web 前后端依赖

## 3. API 测试

- [x] 3.1 新建 `tests/test_simulator_api.py`：用 `fastapi.testclient.TestClient` 覆盖 spec 场景
- [x] 3.2 会话 CRUD：list（无 `portfolio_json`）、create、get、404、delete
- [x] 3.3 决策推进：step 成功、step 非法订单 400、skip
- [x] 3.4 compare：正常返回结构、不存在会话 404
- [x] 3.5 CLI：`sim web --port` 参数解析（含新默认值 9555）

## 4. 验证

- [x] 4.1 `uv run pytest tests/test_simulator_api.py` 全绿
- [x] 4.2 `uv run ruff check` + `uv run mypy src` 通过
- [x] 4.3 `openspec validate spec-simulator-web` 通过
