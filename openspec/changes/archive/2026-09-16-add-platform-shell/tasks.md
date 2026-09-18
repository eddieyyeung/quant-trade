## 1. 运行记录数据模型

- [x] 1.1 在 `data/schema.py` 新增 `run` / `run_log` / `artifact` 三张表
- [x] 1.2 `run` 表建唯一索引 `(kind, idempotency_key)`，允许空值重复
- [x] 1.3 新建 `src/quant_trade/runs/models.py`：`RunRecord` / `RunLogLine` / `ArtifactRecord` dataclass
- [x] 1.4 新建 `src/quant_trade/runs/store.py`：`RunStore` 提供创建 / 更新状态 / 追加日志 / 登记产物 / 分页查询
- [x] 1.5 `RunStatus` 枚举仅含 `pending` / `running` / `ok` / `failed` / `cancelled` / `interrupted`
- [x] 1.6 新建 `tests/test_runs_store.py`：状态机转换、日志序号连续、产物登记、分页与倒序

## 2. 任务 worker

- [x] 2.1 新建 `src/quant_trade/jobs/registry.py`：`kind` → `(params_model, service_fn)` 注册表，先注册 `data_sync` → `DataSyncParams` / `sync_market_data`
- [x] 2.2 新建 `src/quant_trade/jobs/runner.py`：单 worker 串行队列（`queue.Queue` + 单线程），并发度固定为 1
- [x] 2.3 实现 `RunContext` 的 sink：`progress` 写 `run.progress` / `run.message`，`log` 写 `run_log` 并推送给订阅者
- [x] 2.4 实现 `CancelToken` 与取消接口的对接：取消请求只对 `running` 任务生效
- [x] 2.5 实现启动恢复：将所有 `running` 记录改写为 `interrupted` 并写入 `error`
- [x] 2.6 异常路径：服务函数抛异常时置 `failed` 并记录异常类型与消息
- [x] 2.7 新建 `tests/test_jobs_runner.py`：串行排队、进度落库、日志落库、取消、异常置 failed、重启恢复

## 3. 运行 API

- [x] 3.1 新建 `src/quant_trade/api/runs.py`：`POST /api/runs` 校验参数后入队，返回 202 + `run_id`
- [x] 3.2 `POST /api/runs` 对未注册 `kind` 返回 400，参数非法返回 422 且不建记录
- [x] 3.3 `GET /api/runs` 分页列表，按开始时间倒序
- [x] 3.4 `GET /api/runs/{run_id}` 详情，不存在返回 404
- [x] 3.5 `GET /api/runs/{run_id}/artifacts` 产物列表
- [x] 3.6 `POST /api/runs/{run_id}/cancel`，非运行中返回错误
- [x] 3.7 在 `runtime/app.py` 挂载 runs 路由（位于 SPA 回退之前）
- [x] 3.8 新建 `tests/test_api_runs.py`：提交 / 校验失败 / 404 / 取消 / 分页

## 4. SSE 日志流

- [x] 4.1 新建 `src/quant_trade/api/log_stream.py`：`GET /api/runs/{run_id}/logs` 返回 `text/event-stream`
- [x] 4.2 连接建立时按 `seq` 升序补发已有日志
- [x] 4.3 轮询 `run_log` 新增行（间隔 500ms），推送给订阅者
- [x] 4.4 运行进入终态时发送结束事件并关闭连接
- [x] 4.5 客户端断开时清理订阅，不泄漏
- [x] 4.6 新建 `tests/test_api_log_stream.py`：补发历史、增量推送、终态关闭

## 5. 前端依赖与工程清理

- [x] 5.1 向 `web/package.json` 添加 `antd` / `@ant-design/icons` / `echarts` / `echarts-for-react` / `react-router-dom`
- [x] 5.2 从**根** `package.json` 移除 `react-router-dom` 与 `recharts`
- [x] 5.3 执行安装并确认 `npm run build` 通过

## 6. antd 平台外壳

- [x] 6.1 新建 `web/src/shell/AppShell.tsx`：`Layout` + `Sider` + `Menu`，八个分区入口
- [x] 6.2 新建 `web/src/shell/routes.tsx`：路由表，未实现分区指向占位页
- [x] 6.3 新建 `web/src/shell/Placeholder.tsx`：说明该分区由后续变更提供
- [x] 6.4 主题 token 对齐（暗色/亮色至少一种可用），移除对 `web/src/index.css` 手写样式的依赖
- [x] 6.5 保留 `/api/health` 连通状态展示

## 7. 任务中心页面

- [x] 7.1 新建 `web/src/api/runs.ts`：运行相关接口封装
- [x] 7.2 新建 `web/src/pages/jobs/RunList.tsx`：antd `Table`，状态用 `Tag`，进度用 `Progress`，按时间倒序
- [x] 7.3 新建 `web/src/pages/jobs/RunDetail.tsx`：`Descriptions` 展示元信息 + SSE 日志面板
- [x] 7.4 日志面板用 `EventSource` 订阅，逐行追加，终态后停止
- [x] 7.5 取消按钮：仅 `running` 可用，调用取消接口后刷新状态

## 8. 数据域页面

- [x] 8.1 后端：新增 `GET /api/data/status` 与 `GET /api/data/coverage`，复用 `data_status` / `universe_coverage`
- [x] 8.2 新建 `web/src/pages/data/Overview.tsx`：`Statistic` 卡片 + 表统计表格，行数为 0 的表标注无数据
- [x] 8.3 新建 `web/src/pages/data/SyncForm.tsx`：`Form` + `DatePicker.RangePicker` + `Switch`，提交后跳转任务中心
- [x] 8.4 前端校验：起始日期晚于结束日期时阻止提交
- [x] 8.5 新建 `web/src/pages/data/Universe.tsx`：股票池规模与覆盖率
- [x] 8.6 新建 `web/src/pages/data/Calendar.tsx`：交易日历视图

## 9. 验证

- [x] 9.1 `uv run pytest` 全绿
- [x] 9.2 `uv run ruff check` / `uv run ruff format --check` / `uv run mypy src` 通过
- [x] 9.3 `cd web && npm run build` 通过，`npx oxlint` 无错误
- [x] 9.4 端到端：界面点「同步」→ 任务中心可见新运行 → 进度推进 → 日志实时追加 → 可取消
- [x] 9.5 端到端：同步运行期间刷新页面，运行仍在跑且日志继续追加
- [x] 9.6 重启验证：运行中杀掉进程再启动，该运行显示为已中断
- [x] 9.7 并发验证：连续提交两个任务，第二个排队等待而非并发执行
- [x] 9.8 `openspec validate add-platform-shell --strict` 通过
