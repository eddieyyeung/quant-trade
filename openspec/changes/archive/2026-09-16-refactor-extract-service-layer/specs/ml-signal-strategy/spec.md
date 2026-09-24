## MODIFIED Requirements

### Requirement: 策略注册与配置

系统 SHALL 通过现有 `@register_strategy` 装饰器注册 `model_ranking`, 支持在 `StrategyConfig` 中通过 `name: model_ranking` 选择, 参数含 top_n、max_industry_weight、模型输出位置。

#### Scenario: 配置驱动选策略

- **WHEN** 配置 `strategy.name = "model_ranking"`
- **THEN** 回测服务与策略信号服务 SHALL 使用 ModelStrategy 生成信号

#### Scenario: 训练产物被下游消费

- **WHEN** 模型训练服务产出预测分并落盘为 parquet
- **THEN** 策略信号服务与回测服务 SHALL 直接读取该 parquet 生成信号，无需手工搬运数据

## REMOVED Requirements

### Requirement: CLI 集成

**Reason**: 本平台不再提供命令行入口。模型训练与预测改由 `quant_trade.services.models` 提供，通过 HTTP API 或脚本调用；预测分落盘位置由训练参数对象指定。

**Migration**: 原 `quant-trade model train` 对应调用 `services.models.train_model(TrainParams(...), ctx)`；原 `quant-trade model predict` 对应调用 `services.models.predict_for_date(PredictParams(...), ctx)`。训练产物仍是 parquet，路径由参数对象中的 `output_path` 字段指定（默认 `data/predictions/model_ranking.parquet`）。
