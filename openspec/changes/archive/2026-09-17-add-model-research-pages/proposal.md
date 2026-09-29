> **状态：已完成（C5）。** 立项时依赖 C2，design / specs / tasks 在 C2 之后补全。
>
> **为何此时写下的两条"缺失"已经不再准确**：下面「Why」里的「特征重要性根本不存在」与「CLI 丢弃第二个返回值」在 C1 就已修掉——`walk_forward_train` 现在返回 `WalkForwardResult`，`feature_importances_` 逐窗口读取并按窗口聚合成 `factor / importance / std`，CLI 本身也已被删除。这一节保留立项时的判断，作为该变更要解决的问题的记录；实际范围以 `design.md` 的 Context 为准（只做持久化与展示，不重新实现聚合）。

## Why

模型域的产物持久化只剩一半，且两个关键能力完全缺失：

- **RankIC 曲线不落盘** —— 每次 `model train` 重算，只打印 3 个汇总数，`ic_series` 列表被丢弃
- **特征重要性根本不存在** —— `LGBMRegressor` 是训练循环内的局部变量，窗口结束即释放；代码从未读取 `feature_importances_`，模型文件也不保存。CLI 还把 `walk_forward_train` 的第二个返回值（特征矩阵）直接丢弃：`predictions, _ = walk_forward_train(...)`
- 唯一落库的是预测分 parquet（`data/predictions/model_ranking.parquet`）

C1 已让 `walk_forward_train` 返回 `WalkForwardResult`（含特征重要性）。本变更把这份数据接上页面，并补齐 RankIC 曲线的持久化。

## What Changes

- 新增训练任务页：walk-forward 区间、因子选择、LightGBM 参数、输出路径
- 新增模型评估页：RankIC 序列曲线、IC_IR、IC 胜率、分年度表现、特征重要性 top-N 条形图
- 新增预测页：指定日期的 top picks、得分分布
- RankIC 曲线落库（新增 `model_metric` 表或在 `artifact` 中以 Parquet 存放）
- 训练任务接入统一运行 API（`kind: model_train`），按窗口上报进度

## Capabilities

### New Capabilities

- `model-evaluation-persistence`: RankIC 序列与特征重要性的持久化与查询
- `model-research-ui`: 训练任务页、评估页、预测页

### Modified Capabilities

- `ml-model-training`: 训练服务在完成后持久化 RankIC 曲线与特征重要性

## Impact

**新增**
- `src/quant_trade/models/persistence.py`
- `src/quant_trade/services/model_query.py`
- `web/src/pages/models/`（训练 / 评估 / 预测）

**修改**
- `src/quant_trade/models/train.py`：`WalkForwardResult` 落库
- `src/quant_trade/models/evaluate.py`：`rank_ic_series` 结果落库
- `src/quant_trade/data/schema.py`：新增模型指标表
- `src/quant_trade/api/`：挂载模型域路由

**新增依赖**：无

**注意**：特征重要性的窗口聚合逻辑在 C1 已实现，本变更只负责持久化与展示，不重复实现。

**ECharts 用途**：RankIC 时序折线图、特征重要性横向条形图、预测得分分布直方图
