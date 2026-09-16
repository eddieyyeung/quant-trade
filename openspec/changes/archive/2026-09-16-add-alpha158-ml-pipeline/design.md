# Design: Alpha158 Factor Library + ML Training Pipeline

## Context

项目现状: DuckDB 单文件存储 (`daily_kline` 含 OHLCV/amount/pct_change/turn_rate), 因子框架为注册表模式 (8 个手工因子, 逐股票 pandas loop 计算), 策略仅有线性打分 `factor_ranking`, 回测为自研周频 A 股引擎。

借鉴对象 qlib: 其 Alpha158 因子集为公开表达式定义, 数据需求 (OHLCV + vwap) 项目 schema 全覆盖 (vwap = amount/volume 派生)。pyqlib 不支持 Python 3.13, 数据格式绑定自身 .bin, 回测与 A 股规则冲突 — 故移植方法论而非引入依赖。

## Goals / Non-Goals

**Goals:**
- 158 个 Alpha158 因子在项目内可批量计算、持久化、复用
- LightGBM 截面排名模型, 滚动重训练 walk-forward, 无前视偏差
- ModelStrategy 复用自研回测引擎, 与 factor_ranking 可对比
- 全链路 CLI 化: 因子计算 → 训练 → 预测 → 回测

**Non-Goals:**
- 不引入 pyqlib, 不复制其表达式 DSL 字符串引擎
- 不改动 DataStore 查询接口、回测引擎、现有因子与策略
- 不做 RL、在线 serving、高频执行
- 不做自动因子挖掘

## Decisions

### D1: 因子定义用函数组合, 不用字符串 DSL

**选择**: 算子库 (python 函数) + 模板组合。`ts_mean(close, 20) / close` 形式写为 `ts_mean(col("close"), 20) / col("close")`。

**备选**: 完整 DSL (qlib 式 `Mean($close, 20)/$close`) — 需要解析器 + 求值器 + 测试, 工作量翻倍, 对单一因子集无增益。

**理由**: 函数组合同样声明式、类型安全、IDE 可跳转; DSL 留作后续扩展点。

### D2: polars 向量化计算, 替代逐股票 loop

**选择**: 单次 `get_daily` 拉全市场数据, polars `group_by("ts_code")` + `rolling_*` 算子批量出全部因子。

**理由**: 现有 `Momentum20d` 逐股票 pandas loop, 几百股 × 多窗口时秒级变分钟级。polars 已是项目依赖, rolling 算子 (mean/std/max/min/rank/quantile/corr) 原生支持; BETA/RESI 用 `rolling_map` + polyfit 或 cov/var 比值。

### D3: 因子落盘 DuckDB `factor_values` 表

**选择**: `factor_values(factor_name, ts_code, trade_date, value)`, 主键三元组, 按日期增量更新。

**备选**: 纯内存算完即弃 — 训练需跨多年反复取, 重复计算不可接受; qlib 式独立缓存文件 — 违背 DuckDB 单存储哲学。

### D4: label 用 T+2 close-to-close 收益

**选择**: `close[t+2] / close[t+1] - 1` (qlib 默认 `Ref($close,-2)/Ref($close,-1)-1`)。

**理由**: A 股 T+1 下, 信号日收盘不可交易, 次日开盘买入, T+2 收盘卖出无障碍。项目周频调仓, label 与调仓周期对齐即可。

### D5: LightGBM 滚动重训练, 训练/预测窗口分离

**选择**: walk-forward — 训练窗口 N 年 → 预测后续 M 月 → 滚动。截面按日期切分, 日期边界保证无前视。验证集取训练窗口尾部一段。

**理由**: LightGBM 轻量、GBDT 在截面因子模型上性能与解释性平衡好 (qlib 模型 zoo 首选)。sklearn API 现成, 无 torch 负担。

### D6: ModelStrategy 走现有 Strategy 接口

**选择**: 新 `ModelStrategy(Strategy)` 类, `generate_signals` 内: 取模型预测分 → 截面预处理 → top-N 等权 → 行业约束, 复用 `Order/SignalResult` 结构。

**理由**: 回测/模拟器/报告全链路不感知策略内部实现, 与 `factor_ranking` 天然可对比 (呼应 simulator 策略对比功能)。

### D7: 预处理复用现有管线

**选择**: 特征用现有 `winsorize_mad → fill_na_median → standardize`; 行业中性化按需开关。

**理由**: 该管线等价 qlib CSZScoreNorm。LightGBM 树模型对单调变换不敏感, 但去极值对 A 股涨跌停极端值必要。

### D8: lightgbm 进 analysis extra, 不进核心依赖

**理由**: 与 scikit-learn/statsmodels 同级定位; 核心安装保持轻量。

**补充 (实现期)**: polars→pandas 桥接需要 `pyarrow`, 加入核心依赖 (pyproject)。Mac 需 `brew install libomp` 才能加载 lightgbm 动态库。

## Risks / Trade-offs

- **[R1] 幸存者偏差** — 现有数据若只含在册股票, 退市股缺失 → 回测用 `stock_basic.list_date` 过滤 (已有 `_min_list_date` 逻辑), 训练时同样按当日可交易股票过滤
- **[R2] 前视偏差** — label 错位、财务数据公告日滞后 → label 严格 T+2 close 对齐; Alpha158 全为行情因子, 无财务因子, 天然规避
- **[R3] `rolling_map` 性能** — polyfit 逐窗口 python 回调慢 → BETA/RESI 优先用向量化形式 (slope = cov/var), `rolling_map` 作后备
- **[R4] 因子表体积** — 158 因子 × 全市场 × 多年, 千万行级 → DuckDB 压缩存储 + 只算近 N 年 + 按需增量
- **[R5] 过拟合** — 158 特征对截面样本易过拟合 → LightGBM 正则 (num_leaves/min_data_in_leaf), 验证集 IC 监控, 训练集外滚动验证
- **[R6] A 股小市值效应** — 等权 top-N 天然偏向小市值 → 保留行业约束, 后续可加市值中性化 (非本期目标)

## Migration Plan

全量 additive, 无破坏性变更:
1. `factor_values` 表通过 schema 初始化 `CREATE TABLE IF NOT EXISTS` 加入, 不影响现有库
2. 新代码全在新模块, 现有 CLI 命令不变; 新命令追加
3. 回滚: 删除新表与新模块即恢复原状; lightgbm 从 extra 移除

## Open Questions

- 训练窗口默认值 (建议: 8 年训练 / 1 年验证 / 3 个月滚动预测, 待数据量实测后定)
- 是否把现有 8 个手工因子 (基本面类) 并入特征矩阵 (倾向: 是, 第二期)
- 预测分输出是否需要模型概率标定 (倾向: 不需要, 只做排序)
