## MODIFIED Requirements

### Requirement: 滚动重训练 walk-forward

系统 SHALL 支持滚动重训练: 将时间线按日期切分为训练窗口与预测窗口, 训练窗口内再切出尾部验证集; 预测窗口使用训练窗口末尾时间点之前的所有数据训练出的模型, 严格禁止预测窗口数据进入训练。

训练 SHALL 由训练服务函数执行, 接受参数对象（信号区间、因子子集、股票池、输出路径、walk-forward 窗口配置）与 `RunContext`。窗口配置 SHALL 可由参数对象覆盖, 未覆盖的项 SHALL 使用默认值。

#### Scenario: 无前视切分

- **WHEN** 训练配置指定训练窗口 8 年、验证 1 年、预测 3 个月
- **THEN** 模型 SHALL 仅在训练窗口数据上拟合
- **AND** 验证集 SHALL 为训练窗口最后 1 年, 预测窗口 SHALL 紧随其后
- **AND** 每个预测窗口开始时的模型 SHALL 使用该窗口起点之前全部可用数据训练

#### Scenario: 窗口配置可覆盖

- **WHEN** 参数对象显式给出训练年数、验证年数或预测月数
- **THEN** 该次训练使用的窗口配置等于给出的值, 其他运行与全局默认值不受影响

#### Scenario: 窗口配置不覆盖时使用默认值

- **WHEN** 参数对象未给出窗口配置
- **THEN** 训练使用既有的默认窗口配置, 行为与本变更前一致

#### Scenario: 训练过程上报进度

- **WHEN** 训练服务在窗口循环中每完成一个窗口
- **THEN** 通过 `ctx.progress()` 上报已训练窗口数与总窗口数

#### Scenario: 训练过程可取消

- **WHEN** 训练服务在窗口循环头部检测到 `ctx.cancelled()` 返回 `True`
- **THEN** 停止后续窗口的训练与预测, 返回截至当前窗口已产出的预测与特征重要性

### Requirement: IC 验证

系统 SHALL 在验证集与滚动预测段上计算模型预测分的 RankIC (spearman) 与 IC 均值, 输出 IC 序列、ICIR、正 IC 占比, 复用现有 IC 分析函数。

逐日 RankIC 序列 SHALL 与汇总指标一并返回给调用方, SHALL NOT 只保留汇总标量而丢弃序列。训练服务 SHALL 在 `ctx.run_id` 非空时把逐日 RankIC 序列与特征重要性持久化（详见 `model-evaluation-persistence`）, 使评估页面无需重跑训练即可读取。

既有的 IC 计算函数签名与其返回结构 SHALL 保持不变, 持久化 SHALL 建立在其返回值之上。

#### Scenario: 验证集 IC 报告

- **WHEN** 一轮滚动训练完成后调用评估接口
- **THEN** 返回各预测段的 RankIC 序列及汇总指标, 用于模型质量判断

#### Scenario: 逐日序列随汇总一并返回

- **WHEN** 训练服务完成一次训练
- **THEN** 其返回结果中同时含汇总指标（IC 均值、IC_IR、正 IC 占比）与逐日 RankIC 序列, 调用方无需重新计算

#### Scenario: 训练完成时持久化

- **WHEN** 一次训练在后台任务的上下文（`ctx.run_id` 非空）中完成
- **THEN** 该运行的逐日 RankIC 序列与特征重要性已写入模型评估结果表, 可在不重跑训练的前提下读回

#### Scenario: 无有效预测日

- **WHEN** 训练的预测无一满足 IC 计算的最小样本数要求
- **THEN** 逐日序列为空且汇总指标为「无值」, SHALL NOT 抛出异常, SHALL NOT 写入 NaN 行

## ADDED Requirements

### Requirement: 模型训练任务类型注册

系统 SHALL 将模型训练注册为统一运行 API 的任务类型：`kind` 为 `model_train`，参数模型为训练的参数对象，服务函数为训练服务。注册后提交训练 SHALL 经由统一运行接口，SHALL NOT 为此新增专用的写接口。

训练参数对象 SHALL 覆盖 walk-forward 窗口配置（训练年数、验证年数、预测月数、早停轮数、最大提升轮数）与 LightGBM 超参数，使一次训练可由 `params_json` 单独复现。LightGBM 超参数 SHALL 以显式字段与取值边界约束，SHALL NOT 接受任意关键字透传；未暴露的超参数（目标函数、评估指标、线程数等链路契约与环境项）SHALL 保持默认值。

运行完成时系统 SHALL 登记产物记录，指向逐日 IC 表、特征重要性表与预测 parquet 文件，各自记录写入行数。未被产出的产物 SHALL NOT 登记。

#### Scenario: 提交训练任务

- **WHEN** 客户端以 `kind: model_train` 与合法的参数提交到统一运行接口
- **THEN** 立即返回 202 与 `run_id`，任务进入串行队列

#### Scenario: 参数可复现

- **WHEN** 读取该运行的 `params_json`
- **THEN** 其可被反序列化为训练参数对象，用于重跑同一次训练

#### Scenario: 超参数越界被拒绝

- **WHEN** 提交的训练参数中 LightGBM 超参数超出其边界
- **THEN** 返回 422，且不创建运行记录

#### Scenario: 未暴露的超参数不受影响

- **WHEN** 提交的参数只覆盖部分 LightGBM 超参数
- **THEN** 未覆盖的项使用默认值，其他运行与全局默认值不受影响

#### Scenario: 进度与日志可见

- **WHEN** 训练任务在窗口循环中运行
- **THEN** 进度按已训练窗口数推进，日志可通过既有日志流实时读取

#### Scenario: 产物登记

- **WHEN** 训练任务产出了逐日 IC、特征重要性与预测分
- **THEN** 该运行登记三条产物记录，分别指向逐日 IC 表、特征重要性表与预测 parquet 文件，并记录各自的行数

#### Scenario: 无产出的运行不登记产物

- **WHEN** 训练任务未产出任何预测
- **THEN** 该运行不登记任何产物记录
