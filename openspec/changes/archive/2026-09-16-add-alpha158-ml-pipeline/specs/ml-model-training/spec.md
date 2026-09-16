# Spec: ML Model Training

## Purpose

基于 Alpha158 因子库的机器学习训练链路: 特征矩阵与 label 构造、LightGBM 滚动重训练 (walk-forward)、截面排名预测分输出与 IC 验证, 全程无前视偏差。

## ADDED Requirements

### Requirement: 特征矩阵构造

系统 SHALL 从 `factor_values` 表构造训练特征矩阵: 每个训练样本为 (ts_code, trade_date), 特征为当日全部或指定子集的 Alpha158 因子值, 应用 `winsorize_mad → fill_na_median → standardize` 预处理 (按日截面执行)。

#### Scenario: 截面预处理

- **WHEN** 构造特征矩阵时对每个交易日截面执行去极值与标准化
- **THEN** 每个截面特征列 SHALL 在该截面内完成 winsorize (3 MAD) 与 z-score, 不跨日期使用统计量

### Requirement: Label 构造

系统 SHALL 以 `close[t+2] / close[t+1] - 1` 构造 label (T+2 收盘对收盘收益, 与 qlib Alpha158 默认 label 一致), 使用交易日历对齐, 不足两个交易日的前向数据时该样本 label 为 NaN 并剔除。

#### Scenario: T+2 对齐

- **WHEN** 信号日为周五 (周内最后交易日)
- **THEN** label SHALL 使用下周二收盘价相对下周一收盘价的收益
- **AND** 若 t+1 或 t+2 不存在 (停牌/期末), 该样本 SHALL 被剔除而非填充

### Requirement: 滚动重训练 walk-forward

系统 SHALL 支持滚动重训练: 将时间线按日期切分为训练窗口与预测窗口, 训练窗口内再切出尾部验证集; 预测窗口使用训练窗口末尾时间点之前的所有数据训练出的模型, 严格禁止预测窗口数据进入训练。

#### Scenario: 无前视切分

- **WHEN** 训练配置指定训练窗口 8 年、验证 1 年、预测 3 个月
- **THEN** 模型 SHALL 仅在训练窗口数据上拟合
- **AND** 验证集 SHALL 为训练窗口最后 1 年, 预测窗口 SHALL 紧随其后
- **AND** 每个预测窗口开始时的模型 SHALL 使用该窗口起点之前全部可用数据训练

### Requirement: LightGBM 训练与预测分

系统 SHALL 使用 LightGBM 训练截面排名模型, 输出股票池内每只股票在预测日的预测分 (截面内可排序), 训练目标为 label 回归或 lambdarank, 超参数支持配置 (num_leaves、min_data_in_leaf、learning_rate 等)。

#### Scenario: 预测分输出

- **WHEN** 对预测窗口内每个信号日调用预测接口
- **THEN** 返回该日股票池各股票的预测分 Series (index: ts_code), 排序即可得到选股顺序
- **AND** 预测分 SHALL 仅依赖该信号日及之前可用数据

### Requirement: IC 验证

系统 SHALL 在验证集与滚动预测段上计算模型预测分的 RankIC (spearman) 与 IC 均值, 输出 IC 序列、ICIR、正 IC 占比, 复用现有 IC 分析函数。

#### Scenario: 验证集 IC 报告

- **WHEN** 一轮滚动训练完成后调用评估接口
- **THEN** 返回各预测段的 RankIC 序列及汇总指标, 用于模型质量判断
