## Why

当前 CLI（`cli.py`，657 行手写 `sys.argv` 分发）是本项目唯一的编排入口。参数活在 shell history 里，结果活在 `print()` 和散落的 HTML 文件里，没有任何执行记录。平台化要求同一份领域逻辑被 HTTP API 复用，但领域逻辑现在和参数解析、控制台输出耦合在 `_cmd_*` 函数体内，无法直接复用。

本次变更把领域逻辑从 CLI 中剥离为可复用服务层，并彻底移除 CLI。这是后续所有平台化变更（C2–C6）的前置条件。

## What Changes

- 新增 `quant_trade.services` 包，每个领域一个模块（data / factors / strategies / models / backtest / report / queries），函数签名为 `fn(params, ctx) -> Result`
- 新增 `RunContext` 数据类（`run_id` / `config` / `store` + `progress()` / `log()` / `cancelled()`），注入领域函数以支持进度上报与取消
- 领域层关键函数增加 `ctx` 参数：`sync_all` / `sync_daily_kline` / `run_backtest` / `generate_signals` / `walk_forward_train`
- `walk_forward_train` 变更返回结构，额外暴露特征重要性（当前 `LGBMRegressor` 是循环内局部变量，`feature_importances_` 从未被读取，模型文件也不保存）
- 新增 `services/queries.py`：表元数据读接口（表行数、日期范围、因子名列表、覆盖率），消除散落在 `cli.py` 三处的裸 SQL
- 新增 `__main__.py`：`python -m quant_trade` 启动研究平台（FastAPI，挂载现有 simulator API 与 `/api/health`，生产模式托管 `web/dist`）
- **BREAKING** 删除 `src/quant_trade/cli.py`
- **BREAKING** 移除 `pyproject.toml` 中 `[project.scripts]` 的 `quant-trade` 入口
- **BREAKING** 不再提供任何子命令分发（`data` / `factor` / `strategy` / `model` / `backtest` / `weekly` / `sim` 全部移除）
- **BREAKING** 默认监听地址由 `0.0.0.0` 改为 `127.0.0.1`：平台不提供任何身份认证，默认暴露到局域网不可接受。需要远程访问须显式传入 `--host 0.0.0.0`

## Capabilities

### New Capabilities

- `service-layer`: 领域服务层——统一函数入口、`RunContext` 契约、pydantic 参数对象、表元数据查询接口、模型特征重要性输出
- `platform-runtime`: 平台运行时——单一启动入口 `python -m quant_trade`、端口约定、前端静态托管与 SPA 回退、健康检查

### Modified Capabilities

- `simulator-web`: 「Web 服务启动与端口约定」的启动命令由 `quant-trade sim web` 改为 `python -m quant_trade`；端口 9555 / Vite 代理 9333 约定保留
- `data-ingestion`: 「DuckDB 存储日线行情」的三个场景由「用户执行 `quant-trade data sync`」改为「数据同步服务被调用」，行为不变
- `backtest-engine`: 「逐周回测循环」与「回测=模拟盘统一接口」的场景由 CLI 命令调用改为服务层调用，行为不变
- `weekly-reporting`: 「周报页面结构」的场景由「用户执行 `quant-trade weekly`」改为「周报服务被调用」；移除「用默认浏览器打开」这一 CLI 专属行为
- `ml-signal-strategy`: 移除「CLI 集成」需求；「策略注册与配置」场景中的 `quant-trade strategy run` 改为服务层调用

## Impact

**受影响代码**
- 删除：`src/quant_trade/cli.py`（657 行）
- 新增：`src/quant_trade/services/`（8 个模块）、`src/quant_trade/__main__.py`、`src/quant_trade/runtime/`（FastAPI app 组装）
- 修改签名：`data/sync.py`、`backtest/engine.py`、`strategies/base.py`、`models/train.py`
- 保留：`simulator/api.py` 在过渡期继续挂载，其会话读写路径不改

**受影响配置**
- `pyproject.toml`：移除 `[project.scripts]`

**测试**
- 现有测试中调用 CLI 的用例需改为调用服务层
- 新增服务层单测（含 `RunContext` 取消与进度语义）

**无新增依赖**（FastAPI / uvicorn 已在 `[project.optional-dependencies].web` 中）

**过渡期说明**：本变更完成后平台仅具备健康检查与现有 simulator API，研究域页面由 C2 引入。
