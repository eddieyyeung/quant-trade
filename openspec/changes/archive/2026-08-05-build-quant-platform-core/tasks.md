## 1. Project Setup & Dependencies

- [x] 1.1 添加新依赖到 pyproject.toml：`duckdb`、`jinja2`、`matplotlib`
- [x] 1.2 创建新模块目录结构：`factors/`、`strategies/`、`signals/`
- [x] 1.3 扩展 Config 模型：`FactorConfig`、`StrategyConfig`、`BacktestConfig`、`ReportConfig`，父级 `AppConfig` 聚合
- [x] 1.4 创建 `config/default.yaml` 完整默认配置（覆盖全部 5 个 Config 模型）
- [x] 1.5 将 `analysis/` 中通用统计函数迁移至 `utils/`，`analysis/` 目录删除

## 2. Data Ingestion

- [x] 2.1 实现 DuckDB schema 初始化：`daily_kline`、`stock_basic`、`index_weights`、`financials`、`trade_calendar` 五张表
- [x] 2.2 实现 akshare 数据源适配器：日线行情（复权）+ 股票基础信息列表拉取
- [x] 2.3 实现 tushare 备用数据源适配器（相同接口，自动 fallback）
- [x] 2.4 实现 `DataStore` 统一查询类：`get_daily()`、`get_financials()`、`get_universe()`、`get_calendar()`
- [x] 2.5 实现交易日历工具：`is_trade_date()`、`next_trade_date()`、`last_trade_date()`、`trade_dates_between()`
- [x] 2.6 实现指数成分股查询：沪深300、中证500 历史成分股拉取与查询，ST/次新股过滤
- [x] 2.7 实现 `quant-trade data sync` CLI 子命令：首次全量拉取 + 增量更新 + 财务数据拉取

## 3. Factor System

- [x] 3.1 实现 `Factor` 抽象基类：定义 `category`、`name`、`compute(date, universe) -> Series` 接口
- [x] 3.2 实现 `@register` 因子注册表装饰器和 `FactorRegistry` 类
- [x] 3.3 实现动量因子：`momentum_20d`、`momentum_60d`、`ma_deviation`
- [x] 3.4 实现价值因子：`pb_ratio`（1/PB）、`pe_ratio`（1/PE剔除负值）、`dividend_yield`
- [x] 3.5 实现质量因子：`roe_ttm`、`revenue_yoy`（注意 ann_date 过滤未来信息）
- [x] 3.6 实现因子预处理管线：MAD 去极值、行业中位数填充缺失值、Z-Score 标准化、行业中性化
- [x] 3.7 实现因子 IC/RankIC 分析工具：单期 IC、IC 序列统计（均值/标准差/ICIR/胜率/累计曲线）
- [x] 3.8 实现 `quant-trade factor update` CLI 子命令：计算全部启用因子，结果缓存

## 4. Strategy Engine

- [x] 4.1 实现 `Strategy` 抽象基类：定义 `generate_signals(date, universe, data) -> SignalResult` 接口
- [x] 4.2 实现 `Order` 和 `SignalResult` 数据类：ts_code、target_pct、direction、reason
- [x] 4.3 实现 `@register_strategy` 策略注册表和 `StrategyRegistry` 类
- [x] 4.4 实现 `FactorRankingStrategy`：多因子等权合成综合得分，Top-N 选股
- [x] 4.5 实现因子加权配置支持（`factor_weights` 字典）
- [x] 4.6 实现行业集中度约束：单行业最大占比限制，超限自动递补
- [x] 4.7 实现 `quant-trade strategy run` CLI 子命令：加载策略配置，输出调仓信号

## 5. Backtest Engine

- [x] 5.1 实现 `Portfolio` 虚拟持仓管理类：现金、持仓、成本记录、市值计算
- [x] 5.2 实现回测主循环：逐周遍历、周五收盘生成信号、下周一开盘执行
- [x] 5.3 实现 T+1 制度检查：当日买入次日才可卖出，同周内买入不可卖出
- [x] 5.4 实现涨跌停限制：主板 10%、创业板/科创板 20%、ST 5%，触及涨停不买、跌停不卖
- [x] 5.5 实现交易成本扣减：佣金（万2.5 最低5元）、印花税（卖出0.05%）、过户费（0.001%）
- [x] 5.6 实现停牌检测与处理：成交量=0 或价格不变时冻结持仓，跳过交易
- [x] 5.7 实现绩效指标计算：年化收益、夏普比率、最大回撤、Calmar、周胜率、超额收益、换手率
- [x] 5.8 实现沪深300基准对比：自动拉取基准数据并计算相对表现
- [x] 5.9 实现 `quant-trade backtest run` CLI 子命令：指定起止日期，输出绩效报告
- [x] 5.10 实现模拟盘模式：`portfolio.json` 读写，信号生成时读现有持仓，输出调仓差异

## 6. Weekly Reporting

- [x] 6.1 实现 Jinja2 HTML 模板：概览卡片 + 净值曲线图区 + 调仓信号表 + 持仓明细表 + 因子IC表 + 底部元信息
- [x] 6.2 实现净值曲线 matplotlib 图表生成，base64 PNG 嵌入 HTML
- [x] 6.3 实现调仓信号表渲染：操作方向（买入绿/卖出红）、代码、名称、目标仓位、信号理由
- [x] 6.4 实现持仓明细表渲染：代码、名称、持仓成本、当前价格、浮动盈亏（正绿负红）
- [x] 6.5 实现因子 IC 跟踪表：本周 IC、累计 IC 均值、ICIR，正值绿负值红
- [x] 6.6 实现非周五运行提示：顶部黄色警告条
- [x] 6.7 实现 `quant-trade weekly` CLI 一键命令：数据更新 → 因子计算 → 策略运行 → 生成周报

## 7. Integration & Polish

- [x] 7.1 端到端集成测试：用 mock 数据验证 data → factor → strategy → backtest → report 全链路
- [x] 7.2 添加每个模块的 `__init__.py` 公开 API 导出
- [x] 7.3 更新 CLAUDE.md 补充新模块说明和使用示例
- [x] 7.4 添加 `ERROR_CONFIG` 环境变量支持自定义 YAML 配置路径
