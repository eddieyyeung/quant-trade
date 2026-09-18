## 1. 服务层骨架

- [x] 1.1 新建 `src/quant_trade/services/__init__.py`，导出各领域公开服务函数
- [x] 1.2 新建 `src/quant_trade/services/context.py`，实现 `RunContext` dataclass（`run_id` / `config` / `store` / `progress()` / `log()` / `cancelled()`）
- [x] 1.3 在 `context.py` 实现 `NULL_CONTEXT` 模块级单例（`progress`/`log` 空操作，`cancelled()` 恒 `False`）
- [x] 1.4 定义进度与日志接收器协议（`ProgressSink` / `LogSink`），使 C2 可注入 SSE 推送实现
- [x] 1.5 新建 `src/quant_trade/services/params.py`，定义各领域参数对象的公共基类约定（pydantic `BaseModel`，从 `AppConfig` 取默认值）

## 2. 数据域服务

- [x] 2.1 新建 `src/quant_trade/services/data.py`，定义 `DataSyncParams`（`include_financials` / `start_date` / `sources`）
- [x] 2.2 实现 `sync_market_data(params, ctx)`，迁入 `cli.py:_cmd_data("sync")` 的四步流程（stock_basic + calendar → index weights → index daily → 全量 kline）
- [x] 2.3 在股票循环开头加入 `ctx.cancelled()` 检查，每批完成后调用 `ctx.progress()`
- [x] 2.4 实现 `data_status(params, ctx)`，迁入 `cli.py:_cmd_data("status")` 的输出逻辑但返回结构化对象
- [x] 2.5 为 `sync_all` / `sync_daily_kline` 增加可选 `ctx` 参数，在适配器调用处上报进度

## 3. 因子域服务

- [x] 3.1 新建 `src/quant_trade/services/factors.py`，定义 `FactorComputeParams`（`factors` / `start_date` / `end_date` / `universe`）与 `Alpha158Params`
- [x] 3.2 实现 `compute_factors(params, ctx)`，迁入 `cli.py:_cmd_factor("update")`
- [x] 3.3 实现 `compute_alpha158(params, ctx)`，迁入 `cli.py:_cmd_factor("alpha158")`
- [x] 3.4 实现 `factor_ic_summary(params, ctx)`，迁入 `cli.py:_cmd_factor("ic")` 并返回 `compute_ic_series` 的结构化 dict（当前 CLI 仅打印）
- [x] 3.5 实现 `list_factors(params, ctx)`，返回注册表 + 持久化状态
- [x] 3.6 在因子循环开头加入 `ctx.cancelled()` 检查与 `ctx.progress()` 上报

## 4. 策略域服务

- [x] 4.1 新建 `src/quant_trade/services/strategies.py`，定义 `SignalParams`（`date` / `strategy` / `top_n` / `universe`）
- [x] 4.2 实现 `generate_strategy_signals(params, ctx)`，迁入 `cli.py:_cmd_strategy("run")`，返回 `SignalResult` 序列化结构
- [x] 4.3 实现 `list_strategies(params, ctx)`，返回策略注册表

## 5. 模型域服务

- [x] 5.1 新建 `src/quant_trade/services/models.py`，定义 `TrainParams`（`start` / `end` / `factors` / `output_path`）与 `PredictParams`（`date` / `model_path`）
- [x] 5.2 在 `models/train.py` 定义 `WalkForwardResult` dataclass（`predictions` / `feature_matrix` / `feature_importance`）
- [x] 5.3 修改 `walk_forward_train` 返回 `WalkForwardResult`，在每个窗口训练后读取 `feature_importances_` 并按窗口聚合（均值 / 标准差）
- [x] 5.4 实现 `train_model(params, ctx)`，迁入 `cli.py:_cmd_model("train")`
- [x] 5.5 实现 `predict_for_date(params, ctx)`，迁入 `cli.py:_cmd_model("predict")`
- [x] 5.6 在 walk-forward 窗口循环开头加入 `ctx.cancelled()` 检查与 `ctx.progress()` 上报（按窗口序号）

## 6. 回测域服务

- [x] 6.1 新建 `src/quant_trade/services/backtest.py`，定义 `BacktestParams`（`start` / `end` / `strategy` / `initial_capital` / `benchmark`）
- [x] 6.2 实现 `run_backtest_service(params, ctx)`，迁入 `cli.py:_cmd_backtest("run")`，返回含 `nav_series` / `benchmark_series` / `trade_log` / `metrics` / `portfolio` 的结构化结果
- [x] 6.3 在周循环开头加入 `ctx.cancelled()` 检查与 `ctx.progress()` 上报（按周序号）
- [x] 6.4 确认结果对象不使用 `date` 索引的 `pd.Series` 裸对象对外暴露，提供转为 `[{date, value}]` 的辅助方法（为 C4 落库做准备）

## 7. 报告域服务

- [x] 7.1 新建 `src/quant_trade/services/report.py`，定义 `WeeklyReportParams`
- [x] 7.2 实现 `generate_weekly(params, ctx)`，迁入 `cli.py:_cmd_weekly`，返回报告文件路径
- [x] 7.3 移除服务函数中的 `webbrowser.open()` 调用（改由调用方决定呈现方式）
- [x] 7.4 保留 `factor_ic_data` 与 `trade_log` 的传参通道（修复现有缺口留给 C6，本任务仅确保通道存在）

## 8. 查询服务

- [x] 8.1 在 `data/store.py` 新增 `TABLE_NAMES` 常量白名单
- [x] 8.2 在 `data/store.py` 实现 `table_stats(table) -> TableStats`（行数 / 最早日期 / 最晚日期），非法表名抛参数错误
- [x] 8.3 新建 `src/quant_trade/services/queries.py`，实现 `list_factor_names()`（查 `factor_values` 去重因子名）
- [x] 8.4 在 `services/queries.py` 实现 `universe_coverage(universe, date)`（已同步日线的股票数与占比）
- [x] 8.5 消除 `cli.py:103` / `cli.py:119` / `cli.py:401` 三处裸 SQL 的对应逻辑（迁入上述接口）

## 9. 平台运行时

- [x] 9.1 新建 `src/quant_trade/runtime/app.py`，实现 `create_platform_app(config)` 组装 FastAPI 实例
- [x] 9.2 实现 `GET /api/health`，返回服务状态、数据库路径、数据库可达标志
- [x] 9.3 挂载现有 `simulator/api.py` 的 app（`include_router` 或子应用挂载），确认 `/api/sessions` 全系列接口可用
- [x] 9.4 实现 `web/dist` 静态托管，并对非 `/api/` 前缀的未匹配路径回退 `index.html`
- [x] 9.5 实现 `web/dist` 缺失时的降级响应（`/` 返回构建提示，`/api/health` 仍正常）
- [x] 9.6 新建 `src/quant_trade/__main__.py`，解析 `--port` / `--host` / `--reload` 并启动 uvicorn
- [x] 9.7 在 `__main__.py` 中检测并拒绝任何子命令参数，以非零状态码退出并输出提示

## 10. 移除 CLI

- [x] 10.1 删除 `src/quant_trade/cli.py`
- [x] 10.2 从 `pyproject.toml` 移除 `[project.scripts]` 的 `quant-trade` 入口
- [x] 10.3 全仓库搜索残留引用（`cli.py` / `quant-trade ` 命令字符串 / `_cmd_` 前缀）并清理
- [x] 10.4 更新 `CLAUDE.md` 的 Quick Start 与 CLI Commands 章节，改为 `python -m quant_trade` 启动描述
- [x] 10.5 更新 `README.md` 中涉及命令行用法的章节

## 11. 测试改造

- [x] 11.1 盘点现有测试中对 CLI 的调用（`tests/` 全量搜索），列出需改造的用例
- [x] 11.2 将 CLI 调用改造为服务层调用，断言从「捕获 stdout」改为「检查返回对象」
- [x] 11.3 新增 `tests/test_services_context.py`：`NULL_CONTEXT` 语义、进度与日志接收、取消传播
- [x] 11.4 新增 `tests/test_services_queries.py`：`table_stats` 正常与非法表名、`list_factor_names`、`universe_coverage`
- [x] 11.5 新增 `tests/test_models_walkforward.py`：`WalkForwardResult` 字段完整性、特征重要性按窗口聚合、单窗口场景
- [x] 11.6 新增 `tests/test_runtime_app.py`：健康检查、静态托管、SPA 回退、`/api/` 404 不回退、`web/dist` 缺失降级、子命令拒绝

## 12. 验证

- [x] 12.1 `uv run pytest` 全绿
- [x] 12.2 `uv run ruff check` 与 `uv run ruff format --check` 通过
- [x] 12.3 `uv run mypy src` strict 模式通过
- [x] 12.4 手动验证：`uv run python -m quant_trade` 启动，`/api/health` 返回正常，`/api/sessions` 可列出既有会话
- [x] 12.5 手动验证：`uv run python -m quant_trade data sync` 以非零状态码退出并输出子命令拒绝提示
- [x] 12.6 手动验证：在 REPL 中调用 `services.backtest.run_backtest_service(BacktestParams(...), NULL_CONTEXT)` 能跑出与改造前一致的绩效指标
- [x] 12.7 `openspec validate refactor-extract-service-layer --strict` 通过
