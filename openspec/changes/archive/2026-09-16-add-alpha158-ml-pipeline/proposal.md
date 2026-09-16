# Proposal: Alpha158 Factor Library + ML Training Pipeline

## Why

当前项目只有线性因子打分策略 (`factor_ranking`: 8 个手工因子等权打分), 无机器学习能力。微软开源量化平台 qlib 的 Alpha158 因子集 (158 个截面因子) + GBDT 排名模型是公开验证过的 alpha 挖掘范式, 是项目最直接的升级路径。直接引入 qlib 不可行 (pyqlib 不支持 Python 3.13、数据格式绑定自身 .bin 存储、回测模型与 A 股规则冲突), 故借鉴其方法论: 移植 Alpha158 因子定义, 用项目已有的 polars/DuckDB/自研回测引擎重建 ML 研究链路。

## What Changes

- 新增 **Alpha158 因子库**: 158 个截面因子 (9 KBAR + 4 PRICE + 5 VOLUME + 140 ROLLING), polars 向量化算子实现, 批量计算后落盘 DuckDB 新表 `factor_values` 供训练复用
- 新增 **ML 训练链路**: 特征宽表构造、T+2 收益 label、LightGBM 滚动重训练 (walk-forward), 输出截面排名预测分, 复用现有 IC 分析验证
- 新增 **ModelStrategy**: 模型预测驱动选股的策略类, 走现有 `Strategy.generate_signals` 接口, 复用自研 A 股回测引擎
- 新增依赖 `lightgbm` (加入 analysis 可选依赖组)
- 保留现有 `factor_ranking` 作为线性 baseline, 与 ML 策略对比

## Capabilities

### New Capabilities
- `alpha158-factors`: 向量化因子计算引擎 (polars 滚动算子库 + 158 个因子模板定义) 与因子值持久化 (DuckDB `factor_values` 表)
- `ml-model-training`: 训练数据构造 (特征 + label)、LightGBM 滚动重训练 walk-forward 流程、预测结果输出
- `ml-signal-strategy`: 基于模型预测排名的选股策略, 实现现有 Strategy 接口, CLI 集成

### Modified Capabilities
<!-- 无。现有 factor-system / strategy-engine / backtest-engine 需求不变, 新因子与新策略为增量内容 -->

## Impact

- **代码**: 新包 `quant_trade.factors.alpha158` (算子 + 模板)、新包 `quant_trade.models` (训练链路)、`quant_trade.strategies.model_strategy` 新策略类、CLI 新命令 (`factor alpha158`, `model train`, 策略注册)
- **数据**: DuckDB 新增 `factor_values` 表 (additive, 不破坏现有表); vwap 列由 `amount/volume` 派生, 无需新数据源
- **依赖**: 新增 `lightgbm` (analysis extra) 与 `pyarrow` (核心依赖, polars→pandas 桥接必需); 不引入 pyqlib
- **不动的部分**: DataStore 查询接口、回测引擎、simulator、现有 8 个因子、现有策略
