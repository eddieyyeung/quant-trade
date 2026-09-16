# Tasks: Alpha158 Factor Library + ML Training Pipeline

## 1. 基础与依赖

- [x] 1.1 添加 `lightgbm` 到 `pyproject.toml` analysis 可选依赖组, `uv sync --extra analysis` 验证安装
- [x] 1.2 `schema.py` 新增 `factor_values` 表 (factor_name, ts_code, trade_date, value, 主键三元组)
- [x] 1.3 创建包骨架 `quant_trade/factors/alpha158/` (operators.py, templates.py, compute.py, storage.py, bridge.py) 与 `quant_trade/models/` (features.py, train.py, evaluate.py)

## 2. Alpha158 因子计算

- [x] 2.1 实现 polars 滚动算子库: ts_mean, ts_std, ts_max, ts_min, ts_rank, ts_quantile, ts_corr, ts_cov, ts_sum, ts_resi, ts_beta, ts_idxmax, ts_idxmin (对应 qlib Mean/Std/Max/Min/Rank/Quantile/Corr/Sum/Resi/Slope/IdxMax/IdxMin)
- [x] 2.2 实现 KBAR 组 9 因子 (KMID/KLEN/KMID2/KUP/KUP2/KLOW/KLOW2/KSFT/KSFT2) 与 PRICE 组 4 因子 (OPEN0/HIGH0/LOW0/VWAP0), vwap 由 amount/volume 派生
- [x] 2.3 实现 ROLLING 组 29 模板 × 窗口 [5,10,20,30,60] = 145 因子 (qlib 默认配置不含 VOLUME 组, 9+4+145=158)
- [x] 2.4 实现批量计算入口: 日期范围 + 股票池 → 长表 DataFrame, 覆盖 158 因子; 单测校验因子名集合与数量
- [x] 2.5 实现 `factor_values` 落盘与增量更新 (DuckDB DataFrame 批量 INSERT OR REPLACE), 宽表/长表查询接口
- [x] 2.6 数值一致性验证: pyqlib 不支持 Python 3.13, 改为对照 qlib 表达式语义的 pandas/scipy 参照实现逐算子验证 (MA/BETA/RSQR/RESI/CORR/RANK/RSV/IMAX/WVMA/VSUMP/KMID 全部一致)
- [x] 2.7 桥接因子: Alpha158 单因子经现有 `Factor.compute` 接口可用 (bridge.py), 兼容 `factor ic` 分析

## 3. ML 训练链路

- [x] 3.1 特征矩阵构造: (ts_code, trade_date) 样本 + 当日因子值宽表, 按日截面 winsorize(3 MAD) + z-score
- [x] 3.2 Label 构造: `close[t+2]/close[t+1]-1`, 交易日历对齐, 缺失剔除
- [x] 3.3 walk-forward 切分: 训练/验证/预测窗口配置, 训练样本要求 label 完全落在训练窗内 (无前视), 测试覆盖
- [x] 3.4 LightGBM 训练器: 超参配置 (num_leaves/min_data_in_leaf/learning_rate), 训练窗口尾部验证集早停
- [x] 3.5 预测接口: 对预测窗口各信号日输出预测分 DataFrame, 落盘 parquet 供策略加载
- [x] 3.6 IC 验证: 验证集与滚动预测段 RankIC 序列 + ICIR + 正 IC 占比报告

## 4. ModelStrategy 与 CLI

- [x] 4.1 `ModelStrategy(Strategy)` 类: 预测分加载 → 排序 → top-N 等权 → 行业约束, 注册名 `model_ranking`
- [x] 4.2 预测分加载接口: 从训练产出 parquet 按信号日读取, 无预测分日期返回空信号
- [x] 4.3 CLI 命令: `factor alpha158` (计算落盘), `model train` (滚动训练 + 预测分产出), `model predict` (指定日期预测)
- [x] 4.4 `StrategyConfig` 支持 `model_ranking` 参数 (top_n, max_industry_weight 经 CLI 注入)

## 5. 端到端验证

- [x] 5.1 单元测试: 算子库, 因子值 (对照手算样本), 增量落盘, walk-forward 无前视断言, ModelStrategy 空信号处理, IC 桥接 (compute_ic_series), CLI 全链 (factor alpha158 → model train → model predict → backtest) — 22 个新测试全部通过
- [x] 5.2 端到端: `scripts/smoke_ml_pipeline.py` 全链路 (alpha158 → train → predict → strategy), 合成 20 股 × 800 日数据: 12 训练窗口, 8500 预测行, RankIC 0.03
- [x] 5.3 ruff check / format / mypy 全绿 (新模块), 全量测试 73 过; `tests/test_integration.py` 2 个失败为既有问题 (干净树复现, 与本 change 无关)
- [x] 5.4 README 与 CLAUDE.md 更新 CLI 命令与模块说明
