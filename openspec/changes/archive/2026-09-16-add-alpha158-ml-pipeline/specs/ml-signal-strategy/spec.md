# Spec: ML Signal Strategy

## Purpose

基于模型预测分排名的选股策略 `ModelStrategy`, 实现现有 `Strategy` 接口, 供自研回测引擎与 simulator 直接使用, 与 `factor_ranking` 线性 baseline 可对比。

## ADDED Requirements

### Requirement: ModelStrategy 类

系统 SHALL 提供 `ModelStrategy(Strategy)` 策略类, 注册名 `model_ranking`, 通过模型预测分选股: 取信号日股票池预测分 → 排序 → top-N 等权 → 行业权重约束, 返回 `SignalResult` (Order 列表与目标权重)。

#### Scenario: 生成信号

- **WHEN** 调用 `generate_signals(date, universe, data)` 且模型已训练并产出该日预测分
- **THEN** 返回 top-N 股票等权目标权重, 行业权重不超过配置上限
- **AND** 返回结果结构与 `factor_ranking` 相同, 回测引擎无需感知差异

### Requirement: 预测分加载接口

系统 SHALL 支持 `ModelStrategy` 从训练链路产出的预测结果 (落盘文件或表) 加载各信号日预测分, 构造参数含模型输出位置与预测分来源配置。

#### Scenario: 加载已产出预测分

- **WHEN** 配置预测结果存储位置并调用策略
- **THEN** 策略按信号日读取该日预测分, 无预测分的日期返回空信号并跳过调仓

### Requirement: 策略注册与配置

系统 SHALL 通过现有 `@register_strategy` 装饰器注册 `model_ranking`, 支持在 `StrategyConfig` 中通过 `name: model_ranking` 选择, 参数含 top_n、max_industry_weight、模型输出位置。

#### Scenario: 配置驱动选策略

- **WHEN** 配置 `strategy.name = "model_ranking"`
- **THEN** 回测与 `quant-trade strategy run` SHALL 使用 ModelStrategy 生成信号

### Requirement: CLI 集成

系统 SHALL 扩展 CLI: `quant-trade model train` (滚动训练并输出预测分) 与 `quant-trade model predict` (对指定日期生成预测分), 训练输出可直接被 `strategy run` / 回测消费。

#### Scenario: 训练到回测闭环

- **WHEN** 依次执行 `factor alpha158` (计算落盘)、`model train` (滚动训练)、`backtest run` (配置 model_ranking)
- **THEN** 全流程无需手工搬运数据, 回测结果包含 ML 策略绩效
