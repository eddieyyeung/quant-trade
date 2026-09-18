> **状态：待实施。** `add-platform-shell`（C2）已归档，design / specs / tasks 已补全。

## Why

因子研究是量化研究的核心循环，也是当前缺口最集中的一块：

- **IC 分析每次现算且只跑单日** —— `compute_ic_series` 内部对每个日期各查一次日线，N 个日期 = N 次查询；CLI 只跑最新一天，多日期能力从未被使用
- **分层回测完全不存在** —— 全仓库 grep `quantile|分层|layered` 零命中
- **因子相关性完全不存在** —— 无 factor-factor 相关性函数
- **因子名列表不存在** —— 需从 `ALPHA158_NAMES` 或裸表 dump

也就是说本变更里「迁移到页面」与「从零实现」各占一半，必须分开估。

## What Changes

- 新增分层回测：按因子值分组（5/10 组），计算各组净值曲线与多空组合
- 新增因子相关性：因子间相关矩阵（支持按日期截面计算）
- IC 序列落库：新增 `ic_series` 表，避免页面每次现算
- 新增因子库页面：因子列表、分类、启用开关、持久化覆盖度
- 新增 IC 分析页面：RankIC 序列曲线、IC_IR、IC 胜率、因子衰减
- 新增分层回测页面：分组净值曲线、多空组合净值
- 新增相关性页面：相关矩阵热力图
- 因子计算任务接入统一运行 API（`kind: factor_compute`）

## Capabilities

### New Capabilities

- `factor-quantile-backtest`: 因子分层回测——分组净值、多空组合
- `factor-correlation`: 因子相关性矩阵计算与展示
- `factor-ic-persistence`: IC / RankIC 序列的持久化与查询
- `factor-research-ui`: 因子库、IC 分析、分层回测、相关性四个页面

### Modified Capabilities

- `service-layer`: 表元数据查询接口新增「因子持久化覆盖度」与「未落库因子」两个场景（该需求定义在 service-layer，不在 factor-system）
- `alpha158-factors`: 因子名列表来源统一到 `services.queries.list_factor_names()`

## Impact

**新增**
- `src/quant_trade/factors/quantile.py`、`src/quant_trade/factors/correlation.py`
- `src/quant_trade/services/factor_analysis.py`
- `web/src/pages/factors/`（库 / IC / 分层 / 相关性）

**修改**
- `src/quant_trade/factors/analysis.py`：新增批量 IC 计算 `compute_ic_frame`（`compute_ic_series` 签名与语义不变）
- `src/quant_trade/services/factors.py`：`compute_alpha158` 改为分块执行以支持取消，`Alpha158Result` 增加 `cancelled`
- `src/quant_trade/data/schema.py`：新增 `ic_series` 表
- `src/quant_trade/api/`：挂载因子域路由

**新增依赖**：无

**ECharts 用途**：IC 序列折线图（含零轴参考线）、分层净值多线图、相关矩阵热力图、因子衰减柱状图
