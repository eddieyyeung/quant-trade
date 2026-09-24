# 策略研究页面

## Why

平台八个分区已建七个，`/strategies` 是最后一个占位页。策略域的后端其实早就齐了——`factor_ranking` 与 `model_ranking` 已注册，`list_strategies` 与 `generate_strategy_signals` 都在服务层——但它**没有任何读取端口**。

于是「某个策略在某天给出什么信号」这个问题，今天只有两条路能回答：生成一整份周报再打开 HTML，或者建一个仿真会话走一周。两条都不是为了看信号而存在的。

信号本身也不落库：周报把它烘进 HTML，仿真把它写进会话的决策 JSON。两者都是**别的用途的副产物**，不是可查询的记录。而 `factor_values` 会被重算覆盖，所以「回看上周三的策略信号」在不落库的前提下根本不是一句能兑现的话。

## What Changes

**信号落库**
- 新增 `strategy_signal` 表，一条信号一行，按 `(run_id, seq)` 主键，字段含日期、策略名、代码、方向、目标仓位与理由
- 新增服务函数 `run_strategy_signals`：在 `generate_strategy_signals` 之上按 `ctx.run_id` 落库
- `generate_strategy_signals` 的**签名与行为保持不变**——周报管线继续直接调用它，不产生 `strategy_signal` 行（周报要的是渲染，不是一条可查记录）

**接入统一运行接口**
- 注册任务类型 `kind: strategy_signals`，参数模型复用 `SignalParams`
- 产物登记一条指向 `strategy_signal` 的 table 产物，`meta` 带策略名、信号日期与股票池规模

**读取端口**
- 新增读取服务：信号运行列表（服务端分页）、单次运行的信号与摘要
- 新增只读路由 `GET /api/strategies`（已注册策略清单）、`GET /api/strategies/runs`（历史）、`GET /api/strategies/runs/{run_id}`（单次信号）

**策略分区前端**
- 两页：策略清单（含发起信号生成的表单与历史运行）、单次信号详情（调仓信号表 + 摘要）
- `/strategies` 侧边栏入口不再指向占位页
- 样式一律走 antd 与 `shell/theme.ts`，不新增手写样式表；图表走共享 ECharts 封装

## Capabilities

### New Capabilities

- `strategy-signal-persistence`：策略信号的落库、任务类型注册与读取接口。含表结构、按运行标识落库、空集不写行、只增不改、运行列表与单次信号的读取契约
- `strategy-research-ui`：策略分区的两个页面——策略清单（含提交入口与历史）与单次信号详情，以及分区导航、图表与实现约定

### Modified Capabilities

无。

这个变更是纯增量的：`strategy-engine` 描述的是引擎如何算信号，落库是叠在其上的一层关注点，由 `strategy-signal-persistence` 单独承载——与 `factor-ic-persistence` 独立于 `factor-system`、`model-evaluation-persistence` 独立于 `ml-model-training` 是同一种切法。任务类型注册本身不改变 `job-runner` 或 `run-api` 的任何需求文本，它们描述的是通用机制。

## Impact

**新增**
- `src/quant_trade/strategies/signal_store.py`：表列常量、写入与读取函数
- `src/quant_trade/services/strategy_query.py`：运行列表与单次信号的读取服务
- `src/quant_trade/api/strategies.py`：只读路由
- `web/src/api/strategies.ts`、`web/src/pages/strategies/`
- `tests/test_strategy_signal_store.py`、`tests/test_services_strategy_query.py`、`tests/test_api_strategies.py`

**修改**
- `src/quant_trade/data/schema.py`：新增 `strategy_signal` 表
- `src/quant_trade/data/store.py`：把新表加入 `TABLE_NAMES` 与 `TABLE_DATE_COLUMNS`，使数据总览页显示它
- `src/quant_trade/services/strategies.py`：新增 `run_strategy_signals`
- `src/quant_trade/services/__init__.py`：登记新增公开名称
- `src/quant_trade/jobs/registry.py`：注册 `strategy_signals` 与产物映射
- `src/quant_trade/runtime/app.py`：挂载策略路由
- `web/src/shell/navigation.tsx` 与 `routes.tsx`：分区置为已实现并补显式路由
- `tests/test_ui_shell_contract.py`：新增 `TestStrategiesSection`

**新增依赖**：无。

**验证锚点**
- 提交一次 `kind: strategy_signals` → 任务详情出现一条指向 `strategy_signal` 的产物，表中有该 run_id 的行
- 策略分区能列出已注册策略、能发起信号生成、能回看历史运行与其信号表
- 周报生成仍然不写 `strategy_signal`（既有行为不变）
- `/strategies` 不再是占位页，且不新增任何手写 CSS
